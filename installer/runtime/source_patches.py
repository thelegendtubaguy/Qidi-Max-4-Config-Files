from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .fs_atomic import atomic_write_bytes
from .manifest import source_patch_variants_for_firmware
from .models import (
    InstalledState,
    RuntimePaths,
    SourcePatchSpec,
    SourcePatchState,
    SourcePatchVariantSpec,
    UpgradeSources,
)
from .path_safety import ensure_external_path_has_no_symlink_components


class SourcePatchError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePatchResult:
    id: str
    destination: str
    classification: str
    variant: SourcePatchVariantSpec
    original: SourcePatchState | None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def destination_path(paths: RuntimePaths, destination: str) -> Path:
    return paths.managed_klipper_root / PurePosixPath(destination)


def validate_payload(
    paths: RuntimePaths,
    patch: SourcePatchSpec,
    variant: SourcePatchVariantSpec,
) -> bytes:
    source = paths.installer_root / variant.source
    if source.is_symlink() or not source.is_file():
        raise SourcePatchError(f"Source patch payload is not a regular file: {source}")
    value = source.read_bytes()
    if sha256_bytes(value) != variant.desired_sha256:
        raise SourcePatchError(
            f"Source patch payload hash does not match manifest: {patch.id}"
        )
    compile(value, str(source), "exec")
    return value


def validate_source_state(
    state: InstalledState,
    patches: tuple[SourcePatchSpec, ...],
    *,
    upgrade_sources: UpgradeSources,
    expected_firmware: str | None,
) -> None:
    if expected_firmware is None or state.runtime_firmware != expected_firmware:
        raise SourcePatchError(
            "Source ledger firmware does not match the current printer firmware."
        )
    stored_source = upgrade_sources.versions.get(state.package_version)
    if stored_source is None:
        raise SourcePatchError(
            "Source ledger package version is not an approved upgrade source."
        )
    stored_entries = {
        (
            entry.id,
            entry.destination,
            entry.firmware,
            entry.original_sha256,
            entry.desired_sha256,
        )
        for entry in stored_source.source_patches
    }
    specs = {patch.id: patch for patch in patches}
    seen_destinations: set[str] = set()
    for entry in state.source_patches:
        spec = specs.get(entry.id)
        key = (
            entry.id,
            entry.destination,
            entry.firmware,
            entry.original_sha256,
            entry.desired_sha256,
        )
        if (
            spec is None
            or entry.firmware != state.runtime_firmware
            or entry.destination != spec.destination
            or entry.destination in seen_destinations
            or key not in stored_entries
        ):
            raise SourcePatchError(
                "Source ledger does not match its stored firmware baseline."
            )
        seen_destinations.add(entry.destination)


def classify_install_source_patch(
    *,
    paths: RuntimePaths,
    patch: SourcePatchSpec,
    firmware: str,
    prior_state: InstalledState | None,
) -> SourcePatchResult:
    variants = source_patch_variants_for_firmware(patch, firmware)
    for variant in variants:
        validate_payload(paths, patch, variant)

    target = destination_path(paths, patch.destination)
    ensure_external_path_has_no_symlink_components(
        root=paths.managed_klipper_root, target=target
    )
    if target.is_symlink() or not target.is_file():
        raise SourcePatchError(
            f"Source patch destination is not a regular file: {target}"
        )

    live = target.read_bytes()
    live_hash = sha256_bytes(live)
    prior = _prior_entry(prior_state, patch.id)
    prior_variant = _variant_for_original(variants, prior)

    desired_matches = tuple(
        variant for variant in variants if live_hash == variant.desired_sha256
    )
    if desired_matches:
        variant = (
            prior_variant
            if prior_variant is not None
            and prior_variant.desired_sha256 == live_hash
            else desired_matches[0]
        )
        retained = None
        if prior is not None and prior_variant == variant:
            retained = replace(
                prior,
                firmware=firmware,
                destination=patch.destination,
                desired_sha256=variant.desired_sha256,
                install_result="noop_desired",
            )
        return SourcePatchResult(
            patch.id, patch.destination, "noop_desired", variant, retained
        )

    expected_matches = tuple(
        variant for variant in variants if live_hash == variant.expected_sha256
    )
    if expected_matches:
        variant = expected_matches[0]
        original = SourcePatchState(
            patch.id,
            patch.destination,
            firmware,
            live_hash,
            variant.desired_sha256,
            stat.S_IMODE(target.stat().st_mode),
            live,
            "applied",
        )
        return SourcePatchResult(
            patch.id, patch.destination, "applied", variant, original
        )

    if (
        prior is not None
        and prior_variant is not None
        and prior.firmware == firmware
        and prior.destination == patch.destination
        and prior.desired_sha256 == live_hash
    ):
        original = SourcePatchState(
            patch.id,
            patch.destination,
            firmware,
            prior.original_sha256,
            prior_variant.desired_sha256,
            prior.original_mode,
            prior.original_bytes,
            "prior_managed",
        )
        return SourcePatchResult(
            patch.id,
            patch.destination,
            "prior_managed",
            prior_variant,
            original,
        )

    accepted = sorted(
        {
            value
            for variant in variants
            for value in (variant.expected_sha256, variant.desired_sha256)
        }
    )
    raise SourcePatchError(
        "Unsupported drift at managed source destination: "
        f"{patch.destination} (firmware {firmware}, live SHA-256 {live_hash}; "
        f"accepted SHA-256: {', '.join(accepted)})"
    )


def deploy_source_patch(
    *,
    paths: RuntimePaths,
    patch: SourcePatchSpec,
    firmware: str,
    result: SourcePatchResult,
) -> SourcePatchState | None:
    if result.classification == "noop_desired":
        return result.original
    if result.variant.firmware != firmware:
        raise SourcePatchError(
            f"Source patch variant firmware does not match runtime: {patch.id}"
        )
    payload = validate_payload(paths, patch, result.variant)
    target = destination_path(paths, patch.destination)
    assert result.original is not None
    atomic_write_bytes(
        target, payload, mode=result.original.original_mode, force_mode=True
    )
    if sha256_bytes(target.read_bytes()) != result.variant.desired_sha256:
        raise SourcePatchError(
            f"Source patch post-write hash verification failed: {patch.destination}"
        )
    return result.original


def restore_source_patch(*, paths: RuntimePaths, entry: SourcePatchState) -> bool:
    target = destination_path(paths, entry.destination)
    ensure_external_path_has_no_symlink_components(
        root=paths.managed_klipper_root, target=target
    )
    if target.is_symlink() or not target.is_file():
        raise SourcePatchError(
            f"Source patch destination is not a regular file: {target}"
        )
    current = sha256_bytes(target.read_bytes())
    if current == entry.original_sha256:
        return False
    if current != entry.desired_sha256:
        raise SourcePatchError(f"Managed source has drifted: {entry.destination}")
    atomic_write_bytes(
        target, entry.original_bytes, mode=entry.original_mode, force_mode=True
    )
    if sha256_bytes(target.read_bytes()) != entry.original_sha256:
        raise SourcePatchError(
            f"Source restore hash verification failed: {entry.destination}"
        )
    return True


def _variant_for_original(
    variants: tuple[SourcePatchVariantSpec, ...],
    prior: SourcePatchState | None,
) -> SourcePatchVariantSpec | None:
    if prior is None:
        return None
    return next(
        (
            variant
            for variant in variants
            if variant.expected_sha256 == prior.original_sha256
        ),
        None,
    )


def _prior_entry(
    state: InstalledState | None, patch_id: str
) -> SourcePatchState | None:
    if state is None:
        return None
    return next(
        (entry for entry in state.source_patches if entry.id == patch_id), None
    )
