from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class RuntimePaths:
    bundle_root: Path
    installer_root: Path
    printer_data_root: Path
    config_root: Path
    firmware_manifest_path: Path
    moonraker_url: str
    lock_path: Path
    recovery_sentinel_path: Path
    backup_root: Path
    klipper_root: Path | None = None
    process_restart_marker_path: Path | None = None
    host_reboot_marker_override: Path | None = None
    boot_id_path_override: Path | None = None

    @property
    def managed_klipper_root(self) -> Path:
        return self.klipper_root or self.printer_data_root.parent / "klipper"

    @property
    def restart_marker_path(self) -> Path:
        return self.process_restart_marker_path or self.printer_data_root / ".tltg_optimized_klipper_restart_required"

    @property
    def host_reboot_marker_path(self) -> Path:
        return self.host_reboot_marker_override or self.printer_data_root / ".tltg_optimized_host_reboot_required"

    @property
    def boot_id_path(self) -> Path:
        return self.boot_id_path_override or Path("/proc/sys/kernel/random/boot_id")


@dataclass(frozen=True)
class SectionSpec:
    file: str
    section: str


@dataclass(frozen=True)
class LineSpec:
    file: str
    line: str


@dataclass(frozen=True)
class ManagedTreeSpec:
    id: str
    source: str
    destination: str
    mode: str
    required_files: tuple[str, ...]


@dataclass(frozen=True)
class EnsureLineSpec:
    id: str
    file: str
    line: str
    after: str


@dataclass(frozen=True)
class PatchVariantSpec:
    firmwares: tuple[str, ...]
    expected: str
    desired: str


@dataclass(frozen=True)
class SectionPatchVariantSpec:
    firmwares: tuple[str, ...]
    expected_normalized_sha256: str


@dataclass(frozen=True)
class PatchSpec:
    id: str
    file: str
    section: str
    option: str
    variants: tuple[PatchVariantSpec, ...]

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class SectionPatchSpec:
    id: str
    file: str
    section: str
    variants: tuple[SectionPatchVariantSpec, ...]

    @property
    def option(self) -> str:
        return "__section__"

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class PackageMeta:
    id: str
    display_name: str
    printer_model: str
    version: str
    known_versions: tuple[str, ...]


@dataclass(frozen=True)
class FirmwareSpec:
    supported: tuple[str, ...]


@dataclass(frozen=True)
class BackupSpec:
    source_directory: str
    label_prefix: str


@dataclass(frozen=True)
class StateSpec:
    record_file: str


@dataclass(frozen=True)
class PreflightSpec:
    required_files: tuple[str, ...]
    required_sections: tuple[SectionSpec, ...]
    required_lines: tuple[LineSpec, ...]


@dataclass(frozen=True)
class SourcePatchVariantSpec:
    firmware: str
    source: str
    expected_sha256: str
    desired_sha256: str


@dataclass(frozen=True)
class SourcePatchSpec:
    id: str
    source: str
    destination: str
    variants: tuple[SourcePatchVariantSpec, ...]


@dataclass(frozen=True)
class InstallSpec:
    ensure_directories: tuple[str, ...]
    managed_tree: ManagedTreeSpec
    ensure_lines: tuple[EnsureLineSpec, ...]
    source_patches: tuple[SourcePatchSpec, ...] = ()


@dataclass(frozen=True)
class PatchSetSpec:
    set_options: tuple[PatchSpec, ...]
    delete_sections: tuple[SectionPatchSpec, ...]


@dataclass(frozen=True)
class PostflightSpec:
    verify_lines: tuple[LineSpec, ...]


@dataclass(frozen=True)
class SystemDnsSpec:
    resolv_conf: str
    resolvconf_head: str
    resolvconf_tail: str
    target_symlink: str
    fallback_nameservers: tuple[str, ...]


@dataclass(frozen=True)
class SystemAptSourcesSpec:
    file: str
    content: str


@dataclass(frozen=True)
class SystemOptionalServiceSpec:
    id: str
    service: str
    prompt: str


@dataclass(frozen=True)
class SystemServicesSpec:
    disable: tuple[str, ...]
    optional_disable: tuple[SystemOptionalServiceSpec, ...]


@dataclass(frozen=True)
class SystemQidiClientStaticGifsSpec:
    archive: str
    sha256: str
    destination: str
    restart_service: str


@dataclass(frozen=True)
class SystemMoonrakerMetadata3mfSpec:
    file: str
    restart_service: str


@dataclass(frozen=True)
class SystemRockchipRootSyncSpec:
    id: str
    unit: str
    unit_file: str
    script: str
    dropin: str
    dropin_content: str
    mount_target: str
    defective_unit_markers: tuple[str, ...]
    defective_script_markers: tuple[str, ...]
    ordered_script_markers: tuple[str, ...]
    vendor_exec_start: str
    desired_exec_start: str


@dataclass(frozen=True)
class SystemOptimizationsSpec:
    enabled_by_default: bool
    dns: SystemDnsSpec
    apt_sources: SystemAptSourcesSpec
    services: SystemServicesSpec
    qidiclient_static_gifs: SystemQidiClientStaticGifsSpec
    moonraker_metadata_3mf: SystemMoonrakerMetadata3mfSpec
    rockchip_root_sync: SystemRockchipRootSyncSpec


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    package: PackageMeta
    firmware: FirmwareSpec
    backup: BackupSpec
    state: StateSpec
    preflight: PreflightSpec
    install: InstallSpec
    patches: PatchSetSpec
    postflight: PostflightSpec
    system_optimizations: Optional[SystemOptimizationsSpec] = None

    @property
    def state_file(self) -> str:
        return self.state.record_file

    @property
    def managed_tree(self) -> ManagedTreeSpec:
        return self.install.managed_tree

    @property
    def include_line(self) -> EnsureLineSpec:
        if len(self.install.ensure_lines) != 1:
            raise ValueError("Manifest must define exactly one install.ensure_lines entry.")
        return self.install.ensure_lines[0]


@dataclass(frozen=True)
class AllowedPatchTarget:
    file: str
    section: str
    option: str

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class UpgradeSourcePatch:
    id: str
    destination: str
    firmware: str
    original_sha256: str
    desired_sha256: str


@dataclass(frozen=True)
class UpgradeSource:
    version: str
    allowed_patch_targets: tuple[AllowedPatchTarget, ...]
    source_patches: tuple[UpgradeSourcePatch, ...] = ()


@dataclass(frozen=True)
class UpgradeSources:
    schema_version: int
    versions: dict[str, UpgradeSource]


@dataclass(frozen=True)
class ManagedTreeFileRecord:
    path: str
    sha256: str


@dataclass(frozen=True)
class ManagedTreeState:
    root: str
    files: tuple[ManagedTreeFileRecord, ...]


@dataclass(frozen=True)
class PatchLedgerEntry:
    id: str
    file: str
    section: str
    option: str
    expected: str
    desired: str
    install_result: str

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class SourcePatchState:
    id: str
    destination: str
    firmware: str
    original_sha256: str
    desired_sha256: str
    original_mode: int
    original_bytes: bytes
    install_result: str


@dataclass(frozen=True)
class InstalledState:
    schema_version: int
    package_id: str
    package_version: str
    runtime_firmware: str
    backup_label: str
    installed_at: str
    managed_tree: ManagedTreeState
    patch_ledger: tuple[PatchLedgerEntry, ...]
    source_patches: tuple[SourcePatchState, ...] = ()
    system_ledger: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class SystemOptimizationCliOptions:
    skip_system_optimizations: bool = False
    disable_ai_detection: bool = False
    keep_ai_detection: bool = False
    keep_system_optimizations: bool = False
    reboot_host: bool = False


@dataclass(frozen=True)
class PatchTargetIssue:
    id: str
    file: str
    section: str
    option: str
    reason: str


@dataclass(frozen=True)
class PreflightReport:
    missing_files: tuple[str, ...]
    missing_sections: tuple[SectionSpec, ...]
    missing_lines: tuple[LineSpec, ...]
    patch_target_issues: tuple[PatchTargetIssue, ...]

    def is_empty(self) -> bool:
        return not (
            self.missing_files
            or self.missing_sections
            or self.missing_lines
            or self.patch_target_issues
        )


@dataclass(frozen=True)
class PatchResult:
    id: str
    file: str
    section: str
    option: str
    current: str
    expected: str
    desired: str
    classification: str


@dataclass(frozen=True)
class DriftRecord:
    path: str
    sha256_before_remove: str


@dataclass(frozen=True)
class ManagedTreeIntent:
    id: str
    source: Optional[str]
    destination: str
    action: str


@dataclass(frozen=True)
class IncludeLineIntent:
    id: str
    file: str
    line: str
    after: Optional[str]
    action: str


@dataclass(frozen=True)
class StateFileIntent:
    path: str
    action: str


@dataclass(frozen=True)
class InstallPlan:
    backup_label: str
    managed_tree_intent: ManagedTreeIntent
    include_line_intents: tuple[IncludeLineIntent, ...]
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    managed_tree_files: tuple[ManagedTreeFileRecord, ...]
    state_file_intent: StateFileIntent


@dataclass(frozen=True)
class UninstallPlan:
    backup_label: str
    managed_tree_intent: ManagedTreeIntent
    include_line_intents: tuple[IncludeLineIntent, ...]
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    state_file_intent: StateFileIntent


@dataclass(frozen=True)
class InstallResult:
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    backup_label: str
    backup_zip_path: Optional[Path]
    dry_run: bool = False


@dataclass(frozen=True)
class UninstallResult:
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    backup_label: Optional[str]
    backup_zip_path: Optional[Path]
    dry_run: bool = False
