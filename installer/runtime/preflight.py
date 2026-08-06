from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

from . import klipper_cfg, patches, safety
from .ensure_lines import has_active_line
from .manifest import active_patch_entries, select_patch_variant
from .errors import PathSafetyError, PreflightTargetsError
from .mirror import validate_managed_tree_source
from .path_safety import (
    ensure_external_path_has_no_symlink_components,
    ensure_install_paths_safe,
    ensure_runtime_path_has_no_symlink_components,
)
from .models import (
    InstalledState,
    Manifest,
    PatchTargetIssue,
    PreflightReport,
    RuntimePaths,
)

DiskUsageFn = Callable[[Path], object]
UrlOpenFn = Callable[..., object]




def run_install_environment_preflight(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter=None,
    disk_usage: DiskUsageFn = shutil.disk_usage,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> None:
    ensure_install_paths_safe(paths=paths, manifest=manifest)
    printer_state = safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    validate_managed_tree_source(
        paths.installer_root / manifest.managed_tree.source,
        required_files=manifest.managed_tree.required_files,
    )
    required_bytes = estimate_install_free_bytes(paths=paths, manifest=manifest)
    free_bytes = safety.ensure_sufficient_free_space(
        paths.printer_data_root,
        required_bytes,
        disk_usage=disk_usage,
    )
    if reporter is not None:
        reporter.debug(
            event="install.preflight.environment",
            printer_state=printer_state,
            required_free_bytes=required_bytes,
            free_bytes=free_bytes,
        )



def run_install_config_preflight(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter=None,
    detected_firmware: str | None = None,
    prior_state: InstalledState | None = None,
) -> None:
    report = build_install_preflight_report(
        paths=paths,
        manifest=manifest,
        detected_firmware=detected_firmware,
        prior_state=prior_state,
    )
    patch_entries = _install_patch_entries(manifest, detected_firmware)
    unique_patch_targets = _count_unique_patch_targets(patch_entries)
    if reporter is not None:
        reporter.emit_install_preflight_counters(
            files=(len(manifest.preflight.required_files), len(manifest.preflight.required_files)),
            sections=(len(manifest.preflight.required_sections), len(manifest.preflight.required_sections)),
            lines=(len(manifest.preflight.required_lines), len(manifest.preflight.required_lines)),
            patch_targets=(unique_patch_targets, unique_patch_targets),
        )
        reporter.debug(
            event="install.preflight.report",
            missing_files=len(report.missing_files),
            missing_sections=len(report.missing_sections),
            missing_lines=len(report.missing_lines),
            patch_target_issues=len(report.patch_target_issues),
        )
    if not report.is_empty():
        raise PreflightTargetsError(report)



def build_install_preflight_report(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    detected_firmware: str | None = None,
    prior_state: InstalledState | None = None,
) -> PreflightReport:
    missing_files = [
        rel_path
        for rel_path in manifest.preflight.required_files
        if not (paths.printer_data_root / rel_path).exists()
    ]
    missing_sections = []
    for spec in manifest.preflight.required_sections:
        path = paths.printer_data_root / spec.file
        if not path.exists() or not klipper_cfg.has_section(klipper_cfg.read_text(path), spec.section):
            missing_sections.append(spec)
    missing_lines = []
    for spec in manifest.preflight.required_lines:
        path = paths.printer_data_root / spec.file
        if not path.exists() or not has_active_line(klipper_cfg.read_text(path), spec.line):
            missing_lines.append(spec)
    patch_target_issues = _patch_target_issues(
        paths=paths,
        patch_entries=_install_patch_entries(manifest, detected_firmware),
        detected_firmware=detected_firmware,
        prior_state=prior_state,
    )
    return PreflightReport(
        missing_files=tuple(missing_files),
        missing_sections=tuple(missing_sections),
        missing_lines=tuple(missing_lines),
        patch_target_issues=tuple(patch_target_issues),
    )



def run_uninstall_preflight(
    *,
    paths: RuntimePaths,
    state: InstalledState,
    include_line_file: str,
    state_file_path: str,
    reporter=None,
    disk_usage: DiskUsageFn = shutil.disk_usage,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> None:
    for path in (state.managed_tree.root, include_line_file, state_file_path):
        ensure_runtime_path_has_no_symlink_components(
            printer_data_root=paths.printer_data_root,
            target=paths.printer_data_root / path,
        )
    for entry in state.source_patches:
        target = paths.managed_klipper_root / entry.destination
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root,
            target=target,
        )
        if target.is_symlink() or not target.is_file():
            raise PathSafetyError(f"Managed source is not a regular file: {target}")
    report = build_uninstall_preflight_report(
        paths=paths,
        state=state,
        include_line_file=include_line_file,
    )
    if reporter is not None:
        if hasattr(reporter, "emit_uninstall_preflight_counters"):
            reporter.emit_uninstall_preflight_counters(
                files=(2, 2),
                sections=(0, 0),
                lines=(0, 0),
                patch_targets=(len(state.patch_ledger), len(state.patch_ledger)),
            )
        reporter.debug(
            event="uninstall.preflight.report",
            missing_files=len(report.missing_files),
            missing_sections=len(report.missing_sections),
            missing_lines=len(report.missing_lines),
            patch_target_issues=len(report.patch_target_issues),
        )
    if not report.is_empty():
        raise PreflightTargetsError(report)
    printer_state = safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    required_bytes = estimate_uninstall_free_bytes(
        paths=paths,
        state=state,
        include_line_file=include_line_file,
        state_file_path=state_file_path,
    )
    free_bytes = safety.ensure_sufficient_free_space(
        paths.printer_data_root,
        required_bytes,
        disk_usage=disk_usage,
    )
    if reporter is not None:
        reporter.debug(
            event="uninstall.preflight.environment",
            printer_state=printer_state,
            required_free_bytes=required_bytes,
            free_bytes=free_bytes,
        )



def build_uninstall_preflight_report(
    *,
    paths: RuntimePaths,
    state: InstalledState,
    include_line_file: str,
) -> PreflightReport:
    missing_files = []
    include_line_path = paths.printer_data_root / include_line_file
    if not include_line_path.exists():
        missing_files.append(include_line_file)
    managed_tree_root = paths.printer_data_root / state.managed_tree.root
    if not managed_tree_root.exists():
        missing_files.append(state.managed_tree.root)
    patch_target_issues = _patch_target_issues(paths=paths, patch_entries=state.patch_ledger)
    return PreflightReport(
        missing_files=tuple(missing_files),
        missing_sections=(),
        missing_lines=(),
        patch_target_issues=tuple(patch_target_issues),
    )



def estimate_install_free_bytes(*, paths: RuntimePaths, manifest: Manifest) -> int:
    external_paths = tuple(
        paths.managed_klipper_root / patch.destination
        for patch in manifest.install.source_patches
    )
    backup_reserve = safety.total_tree_size(paths.config_root) + sum(
        safety.file_size(path) for path in external_paths
    )
    rollback_paths = _install_rollup_paths(paths=paths, manifest=manifest)
    rollback_reserve = sum(safety.file_size(path) for path in rollback_paths)
    managed_tree_write = safety.total_tree_size(
        paths.installer_root / manifest.managed_tree.source
    )
    config_writes = sum(
        safety.file_size(paths.printer_data_root / spec.file)
        for spec in manifest.install.ensure_lines
    )
    patch_writes = sum(
        safety.file_size(paths.printer_data_root / patch.file)
        for patch in (*manifest.patches.set_options, *manifest.patches.delete_sections)
    )
    source_writes = sum(
        max(
            safety.file_size(paths.installer_root / variant.source)
            for variant in patch.variants
        )
        for patch in manifest.install.source_patches
    )
    state_write = 8 * 1024 + sum(safety.file_size(path) for path in external_paths)
    write_reserve = managed_tree_write + config_writes + patch_writes + source_writes + state_write
    # Same-directory atomic replacement needs room for the new file and rollback copy.
    write_reserve += managed_tree_write + config_writes + patch_writes + source_writes + state_write
    return safety.required_free_bytes(
        backup_reserve=backup_reserve,
        rollback_reserve=rollback_reserve,
        write_reserve=write_reserve,
    )



def estimate_uninstall_free_bytes(
    *,
    paths: RuntimePaths,
    state: InstalledState,
    include_line_file: str,
    state_file_path: str,
) -> int:
    source_paths = tuple(paths.managed_klipper_root / entry.destination for entry in state.source_patches)
    backup_reserve = safety.total_tree_size(paths.config_root) + sum(
        safety.file_size(path) for path in source_paths
    )
    rollback_paths = _uninstall_rollup_paths(
        paths=paths,
        state=state,
        include_line_file=include_line_file,
        state_file_path=state_file_path,
    )
    rollback_reserve = sum(safety.file_size(path) for path in rollback_paths)
    config_write_paths = {paths.printer_data_root / include_line_file}
    config_write_paths.update(paths.printer_data_root / entry.file for entry in state.patch_ledger)
    write_reserve = sum(safety.file_size(path) for path in config_write_paths)
    source_writes = sum(
        max(safety.file_size(path), len(entry.original_bytes))
        for path, entry in zip(source_paths, state.source_patches)
    )
    write_reserve += source_writes
    write_reserve += write_reserve
    return safety.required_free_bytes(
        backup_reserve=backup_reserve,
        rollback_reserve=rollback_reserve,
        write_reserve=write_reserve,
    )



def _install_rollup_paths(*, paths: RuntimePaths, manifest: Manifest) -> set[Path]:
    rollup = {paths.printer_data_root / manifest.state_file, paths.restart_marker_path}
    rollup.update(paths.printer_data_root / spec.file for spec in manifest.install.ensure_lines)
    rollup.update(paths.managed_klipper_root / patch.destination for patch in manifest.install.source_patches)
    rollup.update(
        paths.printer_data_root / patch.file
        for patch in (*manifest.patches.set_options, *manifest.patches.delete_sections)
    )
    managed_tree_root = paths.printer_data_root / manifest.managed_tree.destination
    if managed_tree_root.exists():
        rollup.update(item for item in managed_tree_root.rglob("*") if item.is_file())
    return rollup



def _uninstall_rollup_paths(
    *,
    paths: RuntimePaths,
    state: InstalledState,
    include_line_file: str,
    state_file_path: str,
) -> set[Path]:
    rollup = {
        paths.printer_data_root / include_line_file,
        paths.printer_data_root / state_file_path,
    }
    rollup.update(paths.printer_data_root / entry.file for entry in state.patch_ledger)
    rollup.update(paths.managed_klipper_root / entry.destination for entry in state.source_patches)
    rollup.add(paths.restart_marker_path)
    managed_tree_root = paths.printer_data_root / state.managed_tree.root
    if managed_tree_root.exists():
        rollup.update(item for item in managed_tree_root.rglob("*") if item.is_file())
    return rollup



def _install_patch_entries(manifest: Manifest, detected_firmware: str | None) -> tuple[object, ...]:
    if detected_firmware is None:
        return (*manifest.patches.set_options, *manifest.patches.delete_sections)
    return active_patch_entries(manifest, detected_firmware)



def _count_unique_patch_targets(patch_entries: Iterable[object]) -> int:
    return len({(entry.file, entry.section, entry.option) for entry in patch_entries})



def _patch_target_issues(
    *,
    paths: RuntimePaths,
    patch_entries: Iterable[object],
    detected_firmware: str | None = None,
    prior_state: InstalledState | None = None,
) -> list[PatchTargetIssue]:
    issues: list[PatchTargetIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in patch_entries:
        target = (entry.file, entry.section, entry.option)
        if target in seen:
            continue
        seen.add(target)
        path = paths.printer_data_root / entry.file
        if not path.exists():
            issues.append(
                PatchTargetIssue(
                    id=entry.id,
                    file=entry.file,
                    section=entry.section,
                    option=entry.option,
                    reason="missing",
                )
            )
            continue
        try:
            text = klipper_cfg.read_text(path)
            if entry.option == "__section__":
                section = klipper_cfg.resolve_unique_section(text, entry.section)
                if detected_firmware is not None:
                    variant = select_patch_variant(entry, detected_firmware)
                    current_hash = klipper_cfg.normalized_section_sha256(section.text)
                    if current_hash != variant.expected_normalized_sha256:
                        issues.append(
                            PatchTargetIssue(
                                id=entry.id,
                                file=entry.file,
                                section=entry.section,
                                option=entry.option,
                                reason="user_modified",
                            )
                        )
            else:
                klipper_cfg.resolve_unique_option(text, entry.section, entry.option)
        except klipper_cfg.TargetResolutionError as exc:
            if (
                entry.option == "__section__"
                and exc.reason == "missing"
                and (
                    _prior_state_has_patch(prior_state, entry)
                    or getattr(entry, "desired", None) == patches.SECTION_DELETED
                )
            ):
                continue
            issues.append(
                PatchTargetIssue(
                    id=entry.id,
                    file=entry.file,
                    section=entry.section,
                    option=entry.option,
                    reason=exc.reason,
                )
            )
    return issues



def _prior_state_has_patch(prior_state: InstalledState | None, entry: object) -> bool:
    if prior_state is None:
        return False
    target = (entry.file, entry.section, entry.option)
    return any(item.target_tuple == target for item in prior_state.patch_ledger)
