from __future__ import annotations

from . import klipper_cfg
from .ensure_lines import has_active_line
from .errors import PreflightTargetsError
from .manifest import select_source_patch_variant
from .mirror import collect_source_hashes, sha256_file
from .models import (
    EnsureLineSpec,
    InstalledState,
    LineSpec,
    Manifest,
    PatchResult,
    PatchTargetIssue,
    PreflightReport,
    RuntimePaths,
)
from .patches import SECTION_DELETED, USER_MODIFIED



def verify_install_postflight(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    patch_results: tuple[PatchResult, ...] = (),
    detected_firmware: str | None = None,
) -> None:
    expected_files = collect_source_hashes(
        paths.installer_root / manifest.managed_tree.source,
        destination_root=paths.printer_data_root / manifest.managed_tree.destination,
        relative_to=paths.printer_data_root,
        required_files=manifest.managed_tree.required_files,
    )
    missing_files = []
    for rel_path, expected_sha256 in expected_files.items():
        path = paths.printer_data_root / rel_path
        if not path.exists() or not path.is_file():
            missing_files.append(rel_path)
            continue
        if sha256_file(path) != expected_sha256:
            missing_files.append(rel_path)
    if detected_firmware is not None:
        for patch in manifest.install.source_patches:
            path = paths.managed_klipper_root / patch.destination
            desired = select_source_patch_variant(patch, detected_firmware).desired_sha256
            if path.is_symlink() or not path.is_file() or sha256_file(path) != desired:
                missing_files.append(patch.destination)
    missing_lines = []
    for spec in manifest.postflight.verify_lines:
        path = paths.printer_data_root / spec.file
        if not path.exists() or not has_active_line(klipper_cfg.read_text(path), spec.line):
            missing_lines.append(spec)
    patch_target_issues = []
    for result in patch_results:
        if result.classification == USER_MODIFIED:
            continue
        path = paths.printer_data_root / result.file
        if not path.exists():
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason="missing",
                )
            )
            continue
        try:
            text = klipper_cfg.read_text(path)
            if result.option == "__section__":
                try:
                    current = klipper_cfg.resolve_unique_section(text, result.section).text
                except klipper_cfg.TargetResolutionError as exc:
                    if exc.reason == "missing" and result.desired == SECTION_DELETED:
                        current = SECTION_DELETED
                    else:
                        raise
            else:
                current = klipper_cfg.resolve_unique_option(
                    text, result.section, result.option
                ).value
        except klipper_cfg.TargetResolutionError as exc:
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason=exc.reason,
                )
            )
            continue
        if current != result.desired:
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason="missing",
                )
            )
    report = PreflightReport(
        missing_files=tuple(missing_files),
        missing_sections=(),
        missing_lines=tuple(missing_lines),
        patch_target_issues=tuple(patch_target_issues),
    )
    if not report.is_empty():
        raise PreflightTargetsError(report)



def verify_uninstall_postflight(
    *,
    paths: RuntimePaths,
    state: InstalledState,
    patch_results: tuple[PatchResult, ...],
    include_line: EnsureLineSpec,
) -> None:
    missing_files = []
    missing_lines = []
    patch_target_issues = []

    include_line_path = paths.printer_data_root / include_line.file
    if include_line_path.exists() and has_active_line(
        klipper_cfg.read_text(include_line_path), include_line.line
    ):
        missing_lines.append(LineSpec(file=include_line.file, line=include_line.line))

    managed_tree_root = paths.printer_data_root / state.managed_tree.root
    if managed_tree_root.exists():
        missing_files.append(state.managed_tree.root)
    for entry in state.source_patches:
        path = paths.managed_klipper_root / entry.destination
        if path.is_symlink() or not path.is_file() or sha256_file(path) != entry.original_sha256:
            missing_files.append(entry.destination)

    for result in patch_results:
        if result.classification == USER_MODIFIED:
            continue
        path = paths.printer_data_root / result.file
        if not path.exists():
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason="missing",
                )
            )
            continue
        try:
            text = klipper_cfg.read_text(path)
            if result.option == "__section__":
                current = klipper_cfg.resolve_unique_section(text, result.section).text
            else:
                current = klipper_cfg.resolve_unique_option(
                    text, result.section, result.option
                ).value
        except klipper_cfg.TargetResolutionError as exc:
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason=exc.reason,
                )
            )
            continue
        if current != result.expected:
            patch_target_issues.append(
                PatchTargetIssue(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    reason="missing",
                )
            )

    report = PreflightReport(
        missing_files=tuple(missing_files),
        missing_sections=(),
        missing_lines=tuple(missing_lines),
        patch_target_issues=tuple(patch_target_issues),
    )
    if not report.is_empty():
        raise PreflightTargetsError(report)
