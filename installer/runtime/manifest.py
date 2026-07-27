from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from .models import (
    BackupSpec,
    EnsureLineSpec,
    FirmwareSpec,
    InstallSpec,
    LineSpec,
    ManagedTreeSpec,
    Manifest,
    PackageMeta,
    PatchSetSpec,
    PatchSpec,
    PatchVariantSpec,
    SectionPatchSpec,
    SectionPatchVariantSpec,
    PostflightSpec,
    PreflightSpec,
    SectionSpec,
    SourcePatchSpec,
    SourcePatchVariantSpec,
    StateSpec,
    SystemAptSourcesSpec,
    SystemDnsSpec,
    SystemOptionalServiceSpec,
    SystemOptimizationsSpec,
    SystemQidiClientStaticGifsSpec,
    SystemMoonrakerMetadata3mfSpec,
    SystemRockchipRootSyncSpec,
    SystemServicesSpec,
)


class ManifestValidationError(ValueError):
    pass


def load_manifest(path: Path) -> Manifest:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise ManifestValidationError(f"Could not read manifest: {path}") from exc
    except yaml.YAMLError as exc:
        raise ManifestValidationError(f"Could not parse manifest: {path}") from exc
    return parse_manifest(raw)


def parse_manifest(raw: Any) -> Manifest:
    if not isinstance(raw, dict):
        raise ManifestValidationError("Manifest root must be a mapping.")
    schema_version = _require_int(raw, "schema_version")
    if schema_version != 1:
        raise ManifestValidationError("Manifest schema_version must be 1.")

    package_raw = _require_mapping(raw, "package")
    package = PackageMeta(
        id=_require_str(package_raw, "id"),
        display_name=_require_str(package_raw, "display_name"),
        printer_model=_require_str(package_raw, "printer_model"),
        version=_require_str(package_raw, "version"),
        known_versions=_require_unique_str_list(package_raw, "known_versions", non_empty=True),
    )

    firmware_raw = _require_mapping(raw, "firmware")
    firmware = FirmwareSpec(
        supported=_require_unique_str_list(firmware_raw, "supported", non_empty=True)
    )

    backup_raw = _require_mapping(raw, "backup")
    backup = BackupSpec(
        source_directory=_validate_relative_path(
            _require_str(backup_raw, "source_directory"), allowed_roots=("config",)
        ),
        label_prefix=_require_str(backup_raw, "label_prefix"),
    )

    state_raw = _require_mapping(raw, "state")
    state = StateSpec(
        record_file=_validate_relative_path(
            _require_str(state_raw, "record_file"), allowed_roots=("config",)
        )
    )

    preflight_raw = _require_mapping(raw, "preflight")
    preflight = PreflightSpec(
        required_files=tuple(
            _validate_relative_path(item, allowed_roots=("config",))
            for item in _require_list_of_str(preflight_raw, "required_files")
        ),
        required_sections=tuple(
            SectionSpec(
                file=_validate_relative_path(
                    _require_str(item, "file"), allowed_roots=("config",)
                ),
                section=_require_str(item, "section"),
            )
            for item in _require_list_of_mapping(preflight_raw, "required_sections")
        ),
        required_lines=tuple(
            LineSpec(
                file=_validate_relative_path(
                    _require_str(item, "file"), allowed_roots=("config",)
                ),
                line=_require_str(item, "line"),
            )
            for item in _require_list_of_mapping(preflight_raw, "required_lines")
        ),
    )

    install_raw = _require_mapping(raw, "install")
    if "managed_trees" in install_raw:
        raise ManifestValidationError(
            "install.managed_trees is not supported; use install.managed_tree."
        )
    if "managed_files" in install_raw:
        raise ManifestValidationError("install.managed_files is not supported.")
    managed_tree_raw = _require_mapping(install_raw, "managed_tree")
    ensure_lines = tuple(
        EnsureLineSpec(
            id=_require_str(item, "id"),
            file=_validate_relative_path(
                _require_str(item, "file"), allowed_roots=("config",)
            ),
            line=_require_str(item, "line"),
            after=_require_str(item, "after"),
        )
        for item in _require_list_of_mapping(install_raw, "ensure_lines")
    )
    if len(ensure_lines) != 1:
        raise ManifestValidationError(
            "Manifest must define exactly one install.ensure_lines entry."
        )
    source_patches = _parse_source_patches(install_raw, firmware)
    install = InstallSpec(
        ensure_directories=tuple(
            _validate_relative_path(item, allowed_roots=("config",))
            for item in _require_list_of_str(install_raw, "ensure_directories")
        ),
        managed_tree=ManagedTreeSpec(
            id=_require_str(managed_tree_raw, "id"),
            source=_validate_relative_path(
                _require_str(managed_tree_raw, "source"), allowed_roots=("klipper",)
            ),
            destination=_validate_relative_path(
                _require_str(managed_tree_raw, "destination"), allowed_roots=("config",)
            ),
            mode=_validate_managed_tree_mode(_require_str(managed_tree_raw, "mode")),
            required_files=tuple(
                _validate_tree_relative_path(item)
                for item in _optional_list_of_str(managed_tree_raw, "required_files")
            ),
        ),
        ensure_lines=ensure_lines,
        source_patches=source_patches,
    )

    patches_raw = _require_mapping(raw, "patches")
    patch_specs: list[PatchSpec] = []
    seen_patch_ids: set[str] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    for item in _require_list_of_mapping(patches_raw, "set_options"):
        patch_id = _require_str(item, "id")
        if patch_id in seen_patch_ids:
            raise ManifestValidationError(f"Duplicate patch id: {patch_id}")
        seen_patch_ids.add(patch_id)
        patch = PatchSpec(
            id=patch_id,
            file=_validate_relative_path(
                _require_str(item, "file"), allowed_roots=("config",)
            ),
            section=_require_str(item, "section"),
            option=_require_str(item, "option"),
            variants=tuple(
                PatchVariantSpec(
                    firmwares=_require_unique_str_list(variant, "firmwares", non_empty=True),
                    expected=_require_str(variant, "expected"),
                    desired=_require_str(variant, "desired"),
                )
                for variant in _require_list_of_mapping(item, "variants")
            ),
        )
        if patch.target_tuple in seen_targets:
            raise ManifestValidationError(
                "Duplicate patch target: " + " / ".join(patch.target_tuple)
            )
        seen_targets.add(patch.target_tuple)
        _validate_patch_variant_coverage(patch, firmware.supported)
        patch_specs.append(patch)

    section_patch_specs: list[SectionPatchSpec] = []
    for item in _require_list_of_mapping(patches_raw, "delete_sections"):
        patch_id = _require_str(item, "id")
        if patch_id in seen_patch_ids:
            raise ManifestValidationError(f"Duplicate patch id: {patch_id}")
        seen_patch_ids.add(patch_id)
        patch = SectionPatchSpec(
            id=patch_id,
            file=_validate_relative_path(
                _require_str(item, "file"), allowed_roots=("config",)
            ),
            section=_require_str(item, "section"),
            variants=tuple(
                SectionPatchVariantSpec(
                    firmwares=_require_unique_str_list(variant, "firmwares", non_empty=True),
                    expected_normalized_sha256=_require_sha256(
                        variant, "expected_normalized_sha256"
                    ),
                )
                for variant in _require_list_of_mapping(item, "variants")
            ),
        )
        if patch.target_tuple in seen_targets:
            raise ManifestValidationError(
                "Duplicate patch target: " + " / ".join(patch.target_tuple)
            )
        seen_targets.add(patch.target_tuple)
        _validate_patch_variant_coverage(patch, firmware.supported)
        section_patch_specs.append(patch)
    patches = PatchSetSpec(
        set_options=tuple(patch_specs), delete_sections=tuple(section_patch_specs)
    )

    postflight_raw = _require_mapping(raw, "postflight")
    if "verify_files" in postflight_raw:
        raise ManifestValidationError(
            "postflight.verify_files is not supported; install.managed_tree defines "
            "postflight file verification."
        )
    postflight = PostflightSpec(
        verify_lines=tuple(
            LineSpec(
                file=_validate_relative_path(
                    _require_str(item, "file"), allowed_roots=("config",)
                ),
                line=_require_str(item, "line"),
            )
            for item in _require_list_of_mapping(postflight_raw, "verify_lines")
        ),
    )

    return Manifest(
        schema_version=schema_version,
        package=package,
        firmware=firmware,
        backup=backup,
        state=state,
        preflight=preflight,
        install=install,
        patches=patches,
        postflight=postflight,
        system_optimizations=_parse_system_optimizations(raw),
    )


def _parse_source_patches(install_raw: dict[str, Any], firmware: FirmwareSpec) -> tuple[SourcePatchSpec, ...]:
    entries = install_raw.get("source_patches", [])
    if not isinstance(entries, list):
        raise ManifestValidationError("Expected list at install.source_patches.")
    patches: list[SourcePatchSpec] = []
    ids: set[str] = set()
    destinations: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ManifestValidationError("Source patch entries must be mappings.")
        patch_id = _require_str(item, "id")
        source = _validate_relative_path(_require_str(item, "source"), allowed_roots=("klipper",))
        destination = _validate_relative_path(_require_str(item, "destination"), allowed_roots=("klippy",))
        if not destination.startswith("klippy/extras/"):
            raise ManifestValidationError("Source patch destinations must stay under klippy/extras/.")
        if patch_id in ids or destination in destinations:
            raise ManifestValidationError("Source patch IDs and destinations must be unique.")
        ids.add(patch_id)
        destinations.add(destination)
        variants = tuple(
            SourcePatchVariantSpec(
                firmware=_require_str(variant, "firmware"),
                expected_sha256=_require_sha256(variant, "expected_sha256"),
                desired_sha256=_require_sha256(variant, "desired_sha256"),
            )
            for variant in _require_list_of_mapping(item, "variants")
        )
        if {variant.firmware for variant in variants} != set(firmware.supported) or len(variants) != len(firmware.supported):
            raise ManifestValidationError(f"Source patch {patch_id} must define exactly one variant per supported firmware.")
        patches.append(SourcePatchSpec(id=patch_id, source=source, destination=destination, variants=variants))
    return tuple(patches)


def select_source_patch_variant(patch: SourcePatchSpec, firmware_version: str) -> SourcePatchVariantSpec:
    matches = [variant for variant in patch.variants if variant.firmware == firmware_version]
    if len(matches) != 1:
        raise ManifestValidationError(f"Source patch {patch.id} does not support firmware {firmware_version}.")
    return matches[0]


def _parse_system_optimizations(raw: dict[str, Any]) -> SystemOptimizationsSpec | None:
    system_raw = raw.get("system_optimizations")
    if system_raw is None:
        return None
    if not isinstance(system_raw, dict):
        raise ManifestValidationError("Expected mapping at system_optimizations.")

    dns_raw = _require_mapping(system_raw, "dns")
    apt_raw = _require_mapping(system_raw, "apt_sources")
    services_raw = _require_mapping(system_raw, "services")
    gifs_raw = _require_mapping(system_raw, "qidiclient_static_gifs")
    moonraker_metadata_raw = _require_mapping(system_raw, "moonraker_metadata_3mf")
    rockchip_raw = _require_mapping(system_raw, "rockchip_root_sync")
    optional_raw = services_raw.get("optional_disable", [])
    if not isinstance(optional_raw, list):
        raise ManifestValidationError("Expected list at optional_disable.")

    return SystemOptimizationsSpec(
        enabled_by_default=_require_bool(system_raw, "enabled_by_default"),
        dns=SystemDnsSpec(
            resolv_conf=_validate_absolute_path(_require_str(dns_raw, "resolv_conf")),
            resolvconf_head=_validate_absolute_path(_require_str(dns_raw, "resolvconf_head")),
            resolvconf_tail=_validate_absolute_path(_require_str(dns_raw, "resolvconf_tail")),
            target_symlink=_validate_absolute_path(_require_str(dns_raw, "target_symlink")),
            fallback_nameservers=_require_unique_str_list(
                dns_raw, "fallback_nameservers", non_empty=True
            ),
        ),
        apt_sources=SystemAptSourcesSpec(
            file=_validate_absolute_path(_require_str(apt_raw, "file")),
            content=_require_str(apt_raw, "content"),
        ),
        services=SystemServicesSpec(
            disable=_require_unique_str_list(services_raw, "disable"),
            optional_disable=tuple(
                SystemOptionalServiceSpec(
                    id=_require_str(item, "id"),
                    service=_require_str(item, "service"),
                    prompt=_require_str(item, "prompt"),
                )
                for item in optional_raw
                if _ensure_mapping_entry(item, "optional_disable")
            ),
        ),
        qidiclient_static_gifs=SystemQidiClientStaticGifsSpec(
            archive=_validate_relative_path(
                _require_str(gifs_raw, "archive"), allowed_roots=("system",)
            ),
            sha256=_require_sha256(gifs_raw, "sha256"),
            destination=_validate_absolute_path(_require_str(gifs_raw, "destination")),
            restart_service=_require_str(gifs_raw, "restart_service"),
        ),
        moonraker_metadata_3mf=SystemMoonrakerMetadata3mfSpec(
            file=_validate_absolute_path(_require_str(moonraker_metadata_raw, "file")),
            restart_service=_require_str(moonraker_metadata_raw, "restart_service"),
        ),
        rockchip_root_sync=_parse_rockchip_root_sync(rockchip_raw),
    )


def _parse_rockchip_root_sync(raw: dict[str, Any]) -> SystemRockchipRootSyncSpec:
    operation_id = _require_str(raw, "id")
    if operation_id != "rockchip_root_sync":
        raise ManifestValidationError("system_optimizations.rockchip_root_sync.id must be rockchip_root_sync.")
    unit = _require_str(raw, "unit")
    if unit != "rockchip.service":
        raise ManifestValidationError("Rockchip operation unit must be rockchip.service.")
    mount_target = _validate_absolute_path(_require_str(raw, "mount_target"))
    if mount_target != "/":
        raise ManifestValidationError("Rockchip operation mount_target must be /.")
    unit_markers = _require_unique_str_list(raw, "defective_unit_markers", non_empty=True)
    script_markers = _require_unique_str_list(raw, "defective_script_markers", non_empty=True)
    ordered_markers = _require_unique_str_list(raw, "ordered_script_markers", non_empty=True)
    desired_exec_start = _validate_absolute_path(_require_str(raw, "desired_exec_start"))
    vendor_exec_start = _validate_absolute_path(_require_str(raw, "vendor_exec_start"))
    dropin_content = _require_str(raw, "dropin_content")
    expected_dropin = f"[Service]\nExecStart=\nExecStart={desired_exec_start}\n"
    if dropin_content != expected_dropin:
        raise ManifestValidationError("Rockchip drop-in content must contain only the declared ExecStart reset and desired command.")
    if any(marker not in script_markers for marker in ordered_markers):
        raise ManifestValidationError("Rockchip ordered_script_markers must also appear in defective_script_markers.")
    return SystemRockchipRootSyncSpec(
        id=operation_id,
        unit=unit,
        unit_file=_validate_absolute_path(_require_str(raw, "unit_file")),
        script=_validate_absolute_path(_require_str(raw, "script")),
        dropin=_validate_absolute_path(_require_str(raw, "dropin")),
        dropin_content=dropin_content,
        mount_target=mount_target,
        defective_unit_markers=unit_markers,
        defective_script_markers=script_markers,
        ordered_script_markers=ordered_markers,
        vendor_exec_start=vendor_exec_start,
        desired_exec_start=desired_exec_start,
    )



def patch_applies_to_firmware(patch: PatchSpec | SectionPatchSpec, firmware_version: str) -> bool:
    return any(firmware_version in variant.firmwares for variant in patch.variants)


def active_patch_entries(manifest: Manifest, firmware_version: str):
    return tuple(
        patch
        for patch in (*manifest.patches.set_options, *manifest.patches.delete_sections)
        if patch_applies_to_firmware(patch, firmware_version)
    )


def select_patch_variant(patch: PatchSpec | SectionPatchSpec, firmware_version: str):
    matches = [
        variant
        for variant in patch.variants
        if firmware_version in variant.firmwares
    ]
    if len(matches) != 1:
        raise ManifestValidationError(
            f"Patch {patch.id} does not have exactly one variant for firmware {firmware_version}."
        )
    return matches[0]


def validate_relative_path(path: str, *, allowed_roots: Iterable[str]) -> str:
    return _validate_relative_path(path, allowed_roots=tuple(allowed_roots))


def _validate_patch_variant_coverage(
    patch: PatchSpec | SectionPatchSpec, supported_firmwares: tuple[str, ...]
) -> None:
    covered: set[str] = set()
    for variant in patch.variants:
        for firmware_version in variant.firmwares:
            if firmware_version not in supported_firmwares:
                raise ManifestValidationError(
                    f"Patch {patch.id} references unsupported firmware {firmware_version}."
                )
            if firmware_version in covered:
                raise ManifestValidationError(
                    f"Patch {patch.id} defines multiple variants for firmware {firmware_version}."
                )
            covered.add(firmware_version)
    if not covered:
        raise ManifestValidationError(f"Patch {patch.id} must define at least one firmware variant.")


def _validate_relative_path(path: str, *, allowed_roots: tuple[str, ...]) -> str:
    if not isinstance(path, str) or not path:
        raise ManifestValidationError("Path values must be non-empty strings.")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ManifestValidationError(f"Path is not allowed: {path}")
    if not pure.parts or pure.parts[0] not in allowed_roots:
        allowed = ", ".join(allowed_roots)
        raise ManifestValidationError(f"Path must start with one of {allowed}: {path}")
    return pure.as_posix()


def _validate_managed_tree_mode(mode: str) -> str:
    if mode != "mirror":
        raise ManifestValidationError(f"Unsupported managed tree mode: {mode}")
    return mode


def _validate_absolute_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ManifestValidationError("Path values must be non-empty strings.")
    pure = PurePosixPath(path)
    if not pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ManifestValidationError(f"Absolute path is not allowed: {path}")
    return pure.as_posix()



def _validate_tree_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ManifestValidationError("Path values must be non-empty strings.")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ManifestValidationError(f"Path is not allowed: {path}")
    if not pure.parts:
        raise ManifestValidationError(f"Path is not allowed: {path}")
    return pure.as_posix()



def _require_mapping(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ManifestValidationError(f"Expected mapping at {key}.")
    return value


def _require_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"Expected non-empty string at {key}.")
    return value


def _require_int(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int):
        raise ManifestValidationError(f"Expected integer at {key}.")
    return value



def _require_bool(mapping: dict[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ManifestValidationError(f"Expected boolean at {key}.")
    return value



def _ensure_mapping_entry(item: Any, key: str) -> bool:
    if not isinstance(item, dict):
        raise ManifestValidationError(f"Expected mapping entries at {key}.")
    return True



def _require_sha256(mapping: dict[str, Any], key: str) -> str:
    value = _require_str(mapping, key).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ManifestValidationError(f"Expected SHA-256 hex string at {key}.")
    return value


def _require_list_of_mapping(mapping: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ManifestValidationError(f"Expected list at {key}.")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ManifestValidationError(f"Expected mapping entries at {key}.")
        result.append(item)
    return result


def _require_list_of_str(mapping: dict[str, Any], key: str) -> list[str]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ManifestValidationError(f"Expected list at {key}.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ManifestValidationError(f"Expected string entries at {key}.")
        result.append(item)
    return result


def _optional_list_of_str(mapping: dict[str, Any], key: str) -> list[str]:
    value = mapping.get(key, [])
    if not isinstance(value, list):
        raise ManifestValidationError(f"Expected list at {key}.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ManifestValidationError(f"Expected string entries at {key}.")
        result.append(item)
    return result



def _require_unique_str_list(
    mapping: dict[str, Any], key: str, *, non_empty: bool = False
) -> tuple[str, ...]:
    values = _require_list_of_str(mapping, key)
    if non_empty and not values:
        raise ManifestValidationError(f"Expected non-empty list at {key}.")
    if len(values) != len(set(values)):
        raise ManifestValidationError(f"Duplicate values are not allowed at {key}.")
    return tuple(values)
