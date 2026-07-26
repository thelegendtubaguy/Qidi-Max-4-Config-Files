from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .fs_atomic import atomic_write_bytes
from .manifest import select_source_patch_variant
from .models import InstalledState, RuntimePaths, SourcePatchSpec, SourcePatchState, UpgradeSources
from .path_safety import ensure_external_path_has_no_symlink_components


class SourcePatchError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePatchResult:
    id: str
    destination: str
    classification: str
    original: SourcePatchState | None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def destination_path(paths: RuntimePaths, destination: str) -> Path:
    return paths.managed_klipper_root / PurePosixPath(destination)


def validate_payload(paths: RuntimePaths, patch: SourcePatchSpec, firmware: str) -> bytes:
    source = paths.installer_root / patch.source
    if source.is_symlink() or not source.is_file():
        raise SourcePatchError(f"Source patch payload is not a regular file: {source}")
    value = source.read_bytes()
    variant = select_source_patch_variant(patch, firmware)
    if sha256_bytes(value) != variant.desired_sha256:
        raise SourcePatchError(f"Source patch payload hash does not match manifest: {patch.id}")
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
        raise SourcePatchError("Source ledger firmware does not match the current printer firmware.")
    stored_source = upgrade_sources.versions.get(state.package_version)
    if stored_source is None:
        raise SourcePatchError("Source ledger package version is not an approved upgrade source.")
    stored_entries = {
        (entry.id, entry.destination, entry.firmware): entry
        for entry in stored_source.source_patches
    }
    specs = {patch.id: patch for patch in patches}
    seen_destinations: set[str] = set()
    for entry in state.source_patches:
        spec = specs.get(entry.id)
        key = (entry.id, entry.destination, entry.firmware)
        baseline = stored_entries.get(key)
        if (
            spec is None
            or entry.firmware != state.runtime_firmware
            or entry.destination != spec.destination
            or entry.destination in seen_destinations
            or baseline is None
            or entry.original_sha256 != baseline.original_sha256
            or entry.desired_sha256 != baseline.desired_sha256
        ):
            raise SourcePatchError("Source ledger does not match its stored firmware baseline.")
        seen_destinations.add(entry.destination)


def classify_install_source_patch(*, paths: RuntimePaths, patch: SourcePatchSpec, firmware: str, prior_state: InstalledState | None) -> SourcePatchResult:
    payload = validate_payload(paths, patch, firmware)
    target = destination_path(paths, patch.destination)
    ensure_external_path_has_no_symlink_components(root=paths.managed_klipper_root, target=target)
    if target.is_symlink() or not target.is_file():
        raise SourcePatchError(f"Source patch destination is not a regular file: {target}")
    live = target.read_bytes(); live_hash = sha256_bytes(live)
    variant = select_source_patch_variant(patch, firmware)
    if live_hash == variant.desired_sha256:
        prior = _prior_entry(prior_state, patch.id)
        if prior is not None:
            prior = replace(
                prior,
                firmware=firmware,
                destination=patch.destination,
                desired_sha256=variant.desired_sha256,
                install_result="noop_desired",
            )
        return SourcePatchResult(patch.id, patch.destination, "noop_desired", prior)
    if live_hash == variant.expected_sha256:
        original = SourcePatchState(patch.id, patch.destination, firmware, live_hash, variant.desired_sha256,
                                    stat.S_IMODE(target.stat().st_mode), live, "applied")
        return SourcePatchResult(patch.id, patch.destination, "applied", original)
    prior = _prior_entry(prior_state, patch.id)
    if (
        prior is not None
        and prior.firmware == firmware
        and prior.destination == patch.destination
        and prior.desired_sha256 == live_hash
    ):
        original = SourcePatchState(patch.id, patch.destination, firmware, prior.original_sha256, variant.desired_sha256,
                                    prior.original_mode, prior.original_bytes, "prior_managed")
        return SourcePatchResult(patch.id, patch.destination, "prior_managed", original)
    raise SourcePatchError(f"Unsupported drift at managed source destination: {patch.destination}")


def deploy_source_patch(*, paths: RuntimePaths, patch: SourcePatchSpec, firmware: str, result: SourcePatchResult) -> SourcePatchState | None:
    if result.classification == "noop_desired":
        return result.original
    payload = validate_payload(paths, patch, firmware)
    target = destination_path(paths, patch.destination)
    assert result.original is not None
    atomic_write_bytes(target, payload, mode=result.original.original_mode, force_mode=True)
    if sha256_bytes(target.read_bytes()) != select_source_patch_variant(patch, firmware).desired_sha256:
        raise SourcePatchError(f"Source patch post-write hash verification failed: {patch.destination}")
    return result.original


def restore_source_patch(*, paths: RuntimePaths, entry: SourcePatchState) -> bool:
    target = destination_path(paths, entry.destination)
    ensure_external_path_has_no_symlink_components(root=paths.managed_klipper_root, target=target)
    if target.is_symlink() or not target.is_file():
        raise SourcePatchError(f"Source patch destination is not a regular file: {target}")
    current = sha256_bytes(target.read_bytes())
    if current == entry.original_sha256:
        return False
    if current != entry.desired_sha256:
        raise SourcePatchError(f"Managed source has drifted: {entry.destination}")
    atomic_write_bytes(target, entry.original_bytes, mode=entry.original_mode, force_mode=True)
    if sha256_bytes(target.read_bytes()) != entry.original_sha256:
        raise SourcePatchError(f"Source restore hash verification failed: {entry.destination}")
    return True


def _prior_entry(state: InstalledState | None, patch_id: str) -> SourcePatchState | None:
    if state is None: return None
    return next((entry for entry in state.source_patches if entry.id == patch_id), None)
