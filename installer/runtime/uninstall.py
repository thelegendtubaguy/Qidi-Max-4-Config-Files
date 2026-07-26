from __future__ import annotations

import shutil
import urllib.request

from . import klipper_cfg, messages, patches
from .auto_update import AutoUpdateError, auto_updates_configured, disable_auto_updates
from .backup import (
    build_uninstall_backup_label,
    create_config_backup,
    list_installer_backups,
    prune_installer_backups,
    utc_now,
)
from .compatibility import CompatibilityValidationError, allowed_target_tuples_for_version
from .ensure_lines import has_active_line, remove_active_line
from .errors import InstalledPackageValidationError, OperationCancelled
from .firmware import detect_firmware_version_best_effort
from .interaction import confirm_yes, maybe_restart_klipper, maybe_restart_pending_service
from .fs_atomic import atomic_write_text
from .mirror import detect_uninstall_managed_tree_drift, remove_tree
from .path_safety import ensure_uninstall_paths_safe
from .source_patches import restore_source_patch, validate_source_state
from .process_restart import read_printer_info, write_restart_marker
from .models import (
    DriftRecord,
    EnsureLineSpec,
    IncludeLineIntent,
    InstalledState,
    ManagedTreeIntent,
    Manifest,
    RuntimePaths,
    StateFileIntent,
    SystemOptimizationCliOptions,
    UninstallPlan,
    UninstallResult,
)
from .postflight import verify_uninstall_postflight
from .preflight import run_uninstall_preflight
from .rollback import RollbackJournal
from .state_file import StateValidationError, delete_state_file, load_installed_state
from .system_optimizations import maybe_prompt_restore_system_optimizations, restore_system_optimizations



def run_uninstall(
    paths: RuntimePaths,
    manifest: Manifest,
    compatibility,
    reporter,
    *,
    dry_run: bool = False,
    input_stream=None,
    urlopen=urllib.request.urlopen,
    disk_usage=shutil.disk_usage,
    environ: dict[str, str] | None = None,
    system_options: SystemOptimizationCliOptions | None = None,
) -> UninstallResult:
    env = {} if environ is None else environ
    system_options = system_options or SystemOptimizationCliOptions()
    reporter.debug(
        event="uninstall.start",
        dry_run=dry_run,
        package_version=manifest.package.version,
    )

    reporter.status(messages.CHECKING_FIRMWARE_VERSION)
    current_firmware = detect_firmware_version_best_effort(paths.firmware_manifest_path)
    reporter.debug(
        event="uninstall.firmware.detected",
        firmware_version=current_firmware or "unknown-firmware",
        manifest_path=paths.firmware_manifest_path,
    )

    reporter.status(messages.CHECKING_INSTALLED_PACKAGE)
    ensure_uninstall_paths_safe(paths=paths, manifest=manifest)
    state_path = paths.printer_data_root / manifest.state_file
    include_line = manifest.include_line
    managed_tree_root = paths.printer_data_root / manifest.managed_tree.destination
    include_line_path = paths.printer_data_root / include_line.file

    non_patch_markers = {
        "state_file": state_path.exists(),
        "managed_tree": managed_tree_root.exists(),
        "include_line": include_line_path.exists()
        and has_active_line(klipper_cfg.read_text(include_line_path), include_line.line),
    }
    reporter.debug(
        event="uninstall.markers.checked",
        state_file=non_patch_markers["state_file"],
        managed_tree=non_patch_markers["managed_tree"],
        include_line=non_patch_markers["include_line"],
    )

    state = None
    state_invalid = False
    if state_path.exists():
        try:
            state = load_installed_state(state_path)
            _validate_uninstall_ledger(state, compatibility)
        except (StateValidationError, CompatibilityValidationError):
            state_invalid = True
            state = None
    patch_markers = {}
    if state is not None:
        patch_markers = detect_patch_markers(paths=paths, state=state)
        reporter.debug(
            event="uninstall.ledger.loaded",
            package_version=state.package_version,
            patch_markers=sum(1 for value in patch_markers.values() if value),
        )
    elif state_invalid:
        reporter.debug(event="uninstall.ledger.invalid", state_path=state_path)
    else:
        reporter.debug(event="uninstall.ledger.missing", state_path=state_path)

    if not any(non_patch_markers.values()) and not any(patch_markers.values()):
        reporter.emit_nothing_to_uninstall()
        reporter.debug(event="uninstall.complete", action="nothing-to-uninstall")
        return UninstallResult(
            patch_results=(),
            managed_tree_drift=(),
            backup_label=None,
            backup_zip_path=None,
        )

    if any(non_patch_markers.values()) and (state is None or state_invalid):
        raise InstalledPackageValidationError()

    assert state is not None
    validate_source_state(
        state,
        manifest.install.source_patches,
        upgrade_sources=compatibility,
        expected_firmware=current_firmware,
    )

    reporter.status(messages.PERFORMING_UNINSTALL_PREFLIGHT_CHECKS)
    run_uninstall_preflight(
        paths=paths,
        state=state,
        include_line_file=include_line.file,
        state_file_path=manifest.state_file,
        reporter=reporter,
        urlopen=urlopen,
        disk_usage=disk_usage,
    )
    managed_tree_drift = detect_uninstall_managed_tree_drift(
        destination_root=paths.printer_data_root / state.managed_tree.root,
        printer_data_root=paths.printer_data_root,
        state=state,
    )
    reporter.debug(
        event="uninstall.managed_tree_drift.detected",
        drift_records=len(managed_tree_drift),
    )

    restore_system = False
    if not dry_run and not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.UNINSTALL_CONFIRMATION_PROMPT,
        instruction=messages.UNINSTALL_CONFIRMATION_INSTRUCTION,
        cancel_message=messages.UNINSTALL_CANCELLED,
    ):
        reporter.debug(event="uninstall.cancelled", dry_run=False)
        raise OperationCancelled(messages.UNINSTALL_CANCELLED)
    if not dry_run:
        restore_system = maybe_prompt_restore_system_optimizations(
            state=state,
            reporter=reporter,
            input_stream=input_stream,
            cli_options=system_options,
        )

    reporter.status(messages.CREATING_BACKUP)
    started_at = utc_now()
    backup_label = build_uninstall_backup_label(
        current_firmware=current_firmware,
        package_version=state.package_version,
        moment=started_at,
    )
    backup_zip_path = None
    if not dry_run:
        existing_backups = list_installer_backups(
            paths.printer_data_root,
            install_label_prefix=manifest.backup.label_prefix,
        )
        reporter.debug(
            event="uninstall.backups.detected",
            existing_backups=len(existing_backups),
            backup_root=paths.backup_root,
        )
        backup_zip_path = create_config_backup(
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
            backup_label=backup_label,
            external_files=tuple(
                (
                    entry.id,
                    entry.destination,
                    paths.managed_klipper_root / entry.destination,
                )
                for entry in state.source_patches
            ),
            external_firmware=state.runtime_firmware,
        )
        pruned_backups = prune_installer_backups(
            paths.printer_data_root,
            install_label_prefix=manifest.backup.label_prefix,
            keep_path=backup_zip_path,
        )
        reporter.debug(
            event="uninstall.backup.created",
            backup_label=backup_label,
            backup_zip_path=backup_zip_path,
            pruned_backups=len(pruned_backups),
        )
        if pruned_backups:
            reporter.debug(
                event="uninstall.backup.retention.pruned",
                removed=", ".join(str(path) for path in pruned_backups),
            )

    reporter.status(messages.UNINSTALLING)
    plan = build_uninstall_plan(
        paths=paths,
        manifest=manifest,
        state=state,
        include_line=include_line,
        backup_label=backup_label,
        managed_tree_drift=managed_tree_drift,
    )
    reporter.debug(
        event="uninstall.plan.built",
        patches=len(plan.patch_results),
        managed_tree_drift=len(plan.managed_tree_drift),
    )
    if dry_run:
        _emit_uninstall_dry_run_counters(reporter, state)
        result = UninstallResult(
            patch_results=plan.patch_results,
            managed_tree_drift=plan.managed_tree_drift,
            backup_label=plan.backup_label,
            backup_zip_path=None,
            dry_run=True,
        )
        reporter.emit_uninstall_dry_run(plan=plan)
        reporter.debug(event="uninstall.complete", dry_run=True, backup_label=plan.backup_label)
        return result

    return _execute_uninstall(
        paths=paths,
        manifest=manifest,
        reporter=reporter,
        state=state,
        include_line=include_line,
        plan=plan,
        backup_zip_path=backup_zip_path,
        input_stream=input_stream,
        urlopen=urlopen,
        restore_system=restore_system,
        environ=env,
    )



def build_uninstall_plan(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    state: InstalledState,
    include_line: EnsureLineSpec,
    backup_label: str,
    managed_tree_drift: tuple[DriftRecord, ...],
) -> UninstallPlan:
    patch_results = _collect_uninstall_patch_results(paths=paths, state=state)
    include_line_intents = _collect_uninstall_include_line_intents(
        paths=paths,
        include_line=include_line,
    )
    managed_tree_intent = ManagedTreeIntent(
        id=manifest.managed_tree.id,
        source=None,
        destination=state.managed_tree.root,
        action="remove",
    )
    return UninstallPlan(
        backup_label=backup_label,
        managed_tree_intent=managed_tree_intent,
        include_line_intents=include_line_intents,
        patch_results=patch_results,
        managed_tree_drift=managed_tree_drift,
        state_file_intent=StateFileIntent(path=manifest.state_file, action="delete"),
    )



def _execute_uninstall(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    state: InstalledState,
    include_line: EnsureLineSpec,
    plan: UninstallPlan,
    backup_zip_path,
    input_stream,
    urlopen,
    restore_system: bool,
    environ: dict[str, str],
) -> UninstallResult:
    managed_tree_root = paths.printer_data_root / state.managed_tree.root
    include_line_path = paths.printer_data_root / include_line.file
    state_path = paths.printer_data_root / manifest.state_file
    journal = RollbackJournal(
        paths.recovery_sentinel_path,
        printer_data_root=paths.printer_data_root,
        source_directory=manifest.backup.source_directory,
    )
    patch_results = []
    uninstall_counters = _uninstall_counters_template(state)
    uninstall_counters["managed_tree_drift"][0] = 1
    try:
        touched_files = {state_path, include_line_path}
        touched_files.update(paths.printer_data_root / entry.file for entry in state.patch_ledger)
        for path in touched_files:
            journal.track_file(path)
        for entry in state.source_patches:
            journal.track_file(paths.managed_klipper_root / entry.destination)
        journal.track_tree(managed_tree_root)
        reporter.debug(
            event="uninstall.rollback.tracking",
            tracked_files=len(touched_files),
            tracked_trees=1,
        )

        source_restores = tuple(entry for entry in state.source_patches if (paths.managed_klipper_root / entry.destination).is_file() and (paths.managed_klipper_root / entry.destination).read_bytes() != entry.original_bytes)
        if source_restores:
            pid, _ = read_printer_info(paths.moonraker_url, urlopen=urlopen)
            journal.track_file(paths.restart_marker_path)
            journal.note_write()
            write_restart_marker(
                paths,
                tuple(
                    (entry.id, entry.destination, entry.original_sha256)
                    for entry in source_restores
                ),
                operation="uninstall",
                process_id=pid,
            )
            for entry in source_restores:
                journal.note_write()
                restore_source_patch(paths=paths, entry=entry)

        for entry in state.patch_ledger:
            path = paths.printer_data_root / entry.file
            text = klipper_cfg.read_text(path)
            if entry.option == "__section__":
                current = _section_text_or_deleted(text, entry.section)
                result = patches.classify_uninstall_patch(current, entry)
                patch_results.append(result)
                if result.classification == patches.UNINSTALL_REVERTED:
                    new_text = klipper_cfg.append_section(text, result.expected)
                    journal.note_write()
                    atomic_write_text(path, new_text)
            else:
                resolved = klipper_cfg.resolve_unique_option(text, entry.section, entry.option)
                result = patches.classify_uninstall_patch(resolved.value, entry)
                patch_results.append(result)
                if result.classification == patches.UNINSTALL_REVERTED:
                    new_text = klipper_cfg.set_option_value(
                        text, entry.section, entry.option, result.expected
                    )
                    journal.note_write()
                    atomic_write_text(path, new_text)
            uninstall_counters["patches"][0] += 1
        reporter.emit_uninstall_counters(**_freeze_counters(uninstall_counters))

        if include_line_path.exists():
            text = klipper_cfg.read_text(include_line_path)
            new_text = remove_active_line(text, include_line.line)
            if new_text != text:
                journal.note_write()
                atomic_write_text(include_line_path, new_text)
        uninstall_counters["include_removal"][0] = 1
        reporter.emit_uninstall_counters(**_freeze_counters(uninstall_counters))

        remove_tree(managed_tree_root, journal)
        uninstall_counters["managed_tree_removal"][0] = 1
        reporter.emit_uninstall_counters(**_freeze_counters(uninstall_counters))

        verify_uninstall_postflight(
            paths=paths,
            state=state,
            patch_results=tuple(patch_results),
            include_line=include_line,
        )
        uninstall_counters["postflight"][0] = 1
        reporter.emit_uninstall_counters(**_freeze_counters(uninstall_counters))

        if restore_system:
            restore_system_optimizations(
                paths=paths,
                state=state,
                reporter=reporter,
                input_stream=input_stream,
                environ=environ,
            )

        if state_path.exists():
            journal.note_write()
            delete_state_file(state_path)
        uninstall_counters["state_remove"][0] = 1
        reporter.emit_uninstall_counters(**_freeze_counters(uninstall_counters))
    except Exception as exc:
        reporter.debug(
            event="uninstall.failure",
            backup_label=plan.backup_label,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
            rollback_started=journal.write_started,
        )
        if journal.write_started:
            journal.rollback_or_raise(
                exc, backup_label=plan.backup_label, backup_zip_path=backup_zip_path
            )
        raise

    result = UninstallResult(
        patch_results=tuple(patch_results),
        managed_tree_drift=plan.managed_tree_drift,
        backup_label=plan.backup_label,
        backup_zip_path=backup_zip_path,
    )
    reporter.emit_uninstall_success(
        patch_results=result.patch_results,
        managed_tree_drift=result.managed_tree_drift,
    )
    if auto_updates_configured():
        try:
            disable_auto_updates(
                paths=paths,
                reporter=reporter,
                input_stream=input_stream,
                require_sudo=True,
            )
        except AutoUpdateError as exc:
            reporter.line(f"{messages.AUTO_UPDATE_DISABLE_FAILED} {exc.message}")
    if paths.restart_marker_path.exists():
        maybe_restart_pending_service(
            paths=paths,
            allowed_entries={patch.id: patch.destination for patch in manifest.install.source_patches},
            reporter=reporter,
            input_stream=input_stream,
            urlopen=urlopen,
        )
    else:
        maybe_restart_klipper(
            reporter=reporter,
            input_stream=input_stream,
            moonraker_query_url=paths.moonraker_url,
            urlopen=urlopen,
        )
    reporter.debug(
        event="uninstall.complete",
        dry_run=False,
        backup_label=plan.backup_label,
        backup_zip_path=backup_zip_path,
        managed_tree_drift=len(result.managed_tree_drift),
        user_modified_patches=len(
            [item for item in result.patch_results if item.classification == patches.USER_MODIFIED]
        ),
    )
    return result



def detect_patch_markers(*, paths: RuntimePaths, state: InstalledState) -> dict[str, bool]:
    markers = {}
    for entry in state.patch_ledger:
        path = paths.printer_data_root / entry.file
        if not path.exists():
            markers[entry.id] = False
            continue
        try:
            text = klipper_cfg.read_text(path)
            if entry.option == "__section__":
                current = _section_text_or_deleted(text, entry.section)
            else:
                current = klipper_cfg.resolve_unique_option(text, entry.section, entry.option).value
        except klipper_cfg.TargetResolutionError:
            markers[entry.id] = False
            continue
        markers[entry.id] = current == entry.desired
    return markers



def _collect_uninstall_patch_results(
    *,
    paths: RuntimePaths,
    state: InstalledState,
) -> tuple:
    results = []
    for entry in state.patch_ledger:
        path = paths.printer_data_root / entry.file
        text = klipper_cfg.read_text(path)
        if entry.option == "__section__":
            current = _section_text_or_deleted(text, entry.section)
            results.append(patches.classify_uninstall_patch(current, entry))
        else:
            resolved = klipper_cfg.resolve_unique_option(text, entry.section, entry.option)
            results.append(patches.classify_uninstall_patch(resolved.value, entry))
    return tuple(results)



def _section_text_or_deleted(text: str, section: str) -> str:
    try:
        return klipper_cfg.resolve_unique_section(text, section).text
    except klipper_cfg.TargetResolutionError as exc:
        if exc.reason == "missing":
            return patches.SECTION_DELETED
        raise



def _collect_uninstall_include_line_intents(
    *,
    paths: RuntimePaths,
    include_line: EnsureLineSpec,
) -> tuple[IncludeLineIntent, ...]:
    include_line_path = paths.printer_data_root / include_line.file
    text = klipper_cfg.read_text(include_line_path)
    return (
        IncludeLineIntent(
            id=include_line.id,
            file=include_line.file,
            line=include_line.line,
            after=None,
            action="remove"
            if has_active_line(text, include_line.line)
            else "already_absent",
        ),
    )



def _validate_uninstall_ledger(state: InstalledState, compatibility) -> None:
    allowed_targets = allowed_target_tuples_for_version(
        compatibility, state.package_version
    )
    for entry in state.patch_ledger:
        if entry.target_tuple not in allowed_targets:
            raise CompatibilityValidationError(
                "Ledger patch target is not allowed for stored installed package version."
            )



def _uninstall_counters_template(state: InstalledState) -> dict[str, list[int]]:
    return {
        "patches": [0, len(state.patch_ledger)],
        "include_removal": [0, 1],
        "managed_tree_drift": [0, 1],
        "managed_tree_removal": [0, 1],
        "postflight": [0, 1],
        "state_remove": [0, 1],
    }



def _emit_uninstall_dry_run_counters(reporter, state: InstalledState) -> None:
    counters = _uninstall_counters_template(state)
    counters["managed_tree_drift"][0] = counters["managed_tree_drift"][1]
    for name in ("patches", "include_removal", "managed_tree_removal", "postflight", "state_remove"):
        counters[name][0] = counters[name][1]
        reporter.emit_uninstall_counters(**_freeze_counters(counters))



def _freeze_counters(counters: dict[str, list[int]]) -> dict[str, tuple[int, int]]:
    return {name: (value[0], value[1]) for name, value in counters.items()}
