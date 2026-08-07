from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .manifest import validate_relative_path
from .models import (
    AllowedPatchTarget,
    Manifest,
    UpgradeSource,
    UpgradeSourceExternalFile,
    UpgradeSourcePatch,
    UpgradeSources,
)


class CompatibilityValidationError(ValueError):
    pass


def load_supported_upgrade_sources(path: Path) -> UpgradeSources:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise CompatibilityValidationError(
            f"Could not read supported upgrade sources: {path}"
        ) from exc
    except yaml.YAMLError as exc:
        raise CompatibilityValidationError(
            f"Could not parse supported upgrade sources: {path}"
        ) from exc
    return parse_supported_upgrade_sources(raw)


def parse_supported_upgrade_sources(raw: Any) -> UpgradeSources:
    if not isinstance(raw, dict):
        raise CompatibilityValidationError(
            "Supported upgrade sources root must be a mapping."
        )
    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise CompatibilityValidationError(
            "Supported upgrade sources schema_version must be 1."
        )
    versions_raw = raw.get("versions")
    if not isinstance(versions_raw, dict):
        raise CompatibilityValidationError("versions must be a mapping.")

    versions: dict[str, UpgradeSource] = {}
    for version, entry in versions_raw.items():
        if not isinstance(version, str) or not version:
            raise CompatibilityValidationError("Version keys must be non-empty strings.")
        if not isinstance(entry, dict):
            raise CompatibilityValidationError(
                f"Version entry for {version} must be a mapping."
            )
        allowed_raw = entry.get("allowed_patch_targets")
        inherited_version = entry.get("inherits")
        inherited = None
        if inherited_version is not None:
            inherited = versions.get(inherited_version) if isinstance(inherited_version, str) else None
            if inherited is None or allowed_raw is not None:
                raise CompatibilityValidationError(
                    f"inherits for {version} must name an earlier version and replace allowed_patch_targets."
                )
            allowed_raw = [target.__dict__ for target in inherited.allowed_patch_targets]
        if not isinstance(allowed_raw, list):
            raise CompatibilityValidationError(
                f"allowed_patch_targets for {version} must be a list."
            )
        allowed_targets: list[AllowedPatchTarget] = []
        seen: set[tuple[str, str, str]] = set()
        for target in allowed_raw:
            if not isinstance(target, dict):
                raise CompatibilityValidationError(
                    f"allowed_patch_targets entries for {version} must be mappings."
                )
            item = AllowedPatchTarget(
                file=validate_relative_path(
                    _require_str(target, "file"), allowed_roots=("config",)
                ),
                section=_require_str(target, "section"),
                option=_require_str(target, "option"),
            )
            if item.target_tuple in seen:
                raise CompatibilityValidationError(
                    f"Duplicate uninstall patch target for {version}: {item.target_tuple}"
                )
            seen.add(item.target_tuple)
            allowed_targets.append(item)
        source_patches = _parse_source_patches(entry, version)
        external_files = _parse_external_files(entry, version, inherited)
        versions[version] = UpgradeSource(
            version=version,
            allowed_patch_targets=tuple(allowed_targets),
            source_patches=source_patches,
            external_files=external_files,
        )

    return UpgradeSources(schema_version=1, versions=versions)


def validate_manifest_compatibility(
    manifest: Manifest, upgrade_sources: UpgradeSources
) -> None:
    manifest_versions = set(manifest.package.known_versions)
    supported_versions = set(upgrade_sources.versions.keys())
    if manifest_versions != supported_versions:
        raise CompatibilityValidationError(
            "package.known_versions must exactly match supported upgrade-source versions."
        )

    current_entry = upgrade_sources.versions.get(manifest.package.version)
    if current_entry is None:
        raise CompatibilityValidationError(
            "Current package.version must exist in supported upgrade sources."
        )

    manifest_targets = {
        patch.target_tuple
        for patch in (*manifest.patches.set_options, *manifest.patches.delete_sections)
    }
    current_targets = {target.target_tuple for target in current_entry.allowed_patch_targets}
    if manifest_targets != current_targets:
        raise CompatibilityValidationError(
            "Current package.version uninstall targets must exactly match manifest patches."
        )

    manifest_sources = {
        (patch.id, patch.destination, variant.firmware, variant.expected_sha256, variant.desired_sha256)
        for patch in manifest.install.source_patches
        for variant in patch.variants
    }
    current_sources = {
        (item.id, item.destination, item.firmware, item.original_sha256, item.desired_sha256)
        for item in current_entry.source_patches
    }
    if manifest_sources != current_sources:
        raise CompatibilityValidationError(
            "Current package.version source-patch baselines must exactly match the manifest."
        )

    manifest_external = {
        (item.id, item.destination, item.sha256)
        for item in manifest.install.external_files
    }
    current_external = {
        (item.id, item.destination, item.sha256)
        for item in current_entry.external_files
    }
    if manifest_external != current_external:
        raise CompatibilityValidationError(
            "Current package.version external-file baselines must exactly match the manifest."
        )


def allowed_target_tuples_for_version(
    upgrade_sources: UpgradeSources, package_version: str
) -> set[tuple[str, str, str]]:
    source = upgrade_sources.versions.get(package_version)
    if source is None:
        raise CompatibilityValidationError(
            f"Unsupported installed package version: {package_version}"
        )
    return {target.target_tuple for target in source.allowed_patch_targets}


def _parse_source_patches(entry: dict[str, Any], version: str) -> tuple[UpgradeSourcePatch, ...]:
    raw = entry.get("source_patches", [])
    if not isinstance(raw, list):
        raise CompatibilityValidationError(f"source_patches for {version} must be a list.")
    result: list[UpgradeSourcePatch] = []
    seen_ids: set[str] = set()
    seen_destinations: set[str] = set()
    seen_variants: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise CompatibilityValidationError(f"source_patches entries for {version} must be mappings.")
        patch_id = _require_str(item, "id")
        destination = validate_relative_path(
            _require_str(item, "destination"), allowed_roots=("klippy",)
        )
        if not destination.startswith("klippy/extras/"):
            raise CompatibilityValidationError("Source-patch destinations must stay under klippy/extras/.")
        firmware = _require_str(item, "firmware")
        original_sha = _require_sha256(item, "original_sha256")
        desired_sha = _require_sha256(item, "desired_sha256")
        key = (patch_id, firmware, original_sha)
        if patch_id in seen_ids and destination not in seen_destinations:
            raise CompatibilityValidationError("Source-patch IDs must use one destination.")
        if destination in seen_destinations and patch_id not in seen_ids:
            raise CompatibilityValidationError("Source-patch destinations must use one ID.")
        if key in seen_variants:
            raise CompatibilityValidationError(
                "Duplicate source-patch firmware stock baseline."
            )
        seen_ids.add(patch_id)
        seen_destinations.add(destination)
        seen_variants.add(key)
        result.append(UpgradeSourcePatch(patch_id, destination, firmware, original_sha, desired_sha))
    return tuple(result)


def _parse_external_files(
    entry: dict[str, Any],
    version: str,
    inherited: UpgradeSource | None,
) -> tuple[UpgradeSourceExternalFile, ...]:
    raw = entry.get("external_files")
    if raw is None:
        return inherited.external_files if inherited is not None else ()
    if not isinstance(raw, list):
        raise CompatibilityValidationError(
            f"external_files for {version} must be a list."
        )
    result: list[UpgradeSourceExternalFile] = []
    destinations_by_id: dict[str, str] = {}
    ids_by_destination: dict[str, str] = {}
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise CompatibilityValidationError(
                f"external_files entries for {version} must be mappings."
            )
        file_id = _require_str(item, "id")
        destination = validate_relative_path(
            _require_str(item, "destination"), allowed_roots=("klippy",)
        )
        if not destination.startswith("klippy/extras/"):
            raise CompatibilityValidationError(
                "External-file destinations must stay under klippy/extras/."
            )
        sha256 = _require_sha256(item, "sha256")
        key = (file_id, destination, sha256)
        if key in seen:
            raise CompatibilityValidationError(
                f"Duplicate external-file baseline for {version}: {file_id}"
            )
        if file_id in destinations_by_id and destinations_by_id[file_id] != destination:
            raise CompatibilityValidationError(
                "External-file IDs must use one destination."
            )
        if destination in ids_by_destination and ids_by_destination[destination] != file_id:
            raise CompatibilityValidationError(
                "External-file destinations must use one ID."
            )
        destinations_by_id[file_id] = destination
        ids_by_destination[destination] = file_id
        seen.add(key)
        result.append(UpgradeSourceExternalFile(file_id, destination, sha256))
    return tuple(result)


def _require_sha256(mapping: dict[str, Any], key: str) -> str:
    value = _require_str(mapping, key).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CompatibilityValidationError(f"{key} must be SHA-256 hex.")
    return value


def _require_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise CompatibilityValidationError(f"Expected non-empty string at {key}.")
    return value
