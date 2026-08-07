from __future__ import annotations

from dataclasses import dataclass
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

import yaml

from . import external_files
from .backup import (
    format_backup_display_timestamp,
    list_installer_backups,
    parse_installer_backup_archive,
    load_backup_snapshot,
    restore_backup_snapshot,
    snapshot_runtime_tree,
    describe_snapshot_difference,
    load_external_backup_entries,
    restore_external_backup_entries,
    requires_external_backup_manifest,
    UNKNOWN_FIRMWARE_TOKEN,
)
from .compatibility import (
    CompatibilityValidationError,
    load_supported_upgrade_sources,
)
from .errors import ExternalFileError
from .firmware import detect_firmware_version
from .fs_atomic import atomic_delete, atomic_write_bytes
from .manifest import source_patch_variants_for_firmware
from .models import InstalledState, Manifest, RuntimePaths
from .path_safety import ensure_external_path_has_no_symlink_components
from .process_restart import read_printer_info, write_restart_marker
from .interaction import maybe_restart_pending_service
from .rollback import RollbackJournal
from .state_file import StateValidationError, parse_installed_state


CONFIRMATION_TOKEN = "RESTORE"
DebugFn = Callable[..., None]


@dataclass(frozen=True)
class BackupSelection:
    path: Path
    label: str
    display_created_at: str


class RestoreHelperError(ValueError):
    pass


def run_restore_helper(
    paths: RuntimePaths,
    manifest: Manifest,
    *,
    stream: TextIO | None = None,
    input_stream: TextIO,
    backup_path: str | None = None,
    debug: DebugFn | None = None,
    reporter=None,
    urlopen=None,
) -> int:
    if reporter is None:
        from .reporter import PlainReporter

        reporter = PlainReporter(stream)
    debug = debug or getattr(reporter, "debug", _noop_debug)
    selection = _resolve_backup_selection(
        printer_data_root=paths.printer_data_root,
        install_label_prefix=manifest.backup.label_prefix,
        backup_path=backup_path,
        reporter=reporter,
        input_stream=input_stream,
        debug=debug,
    )
    if selection is None:
        return 0

    reporter.emit_restore_selection(label=selection.label, path=str(selection.path))
    reporter.emit_restore_warning(
        config_path=str(paths.printer_data_root / manifest.backup.source_directory)
    )
    reporter.line(f"Type {CONFIRMATION_TOKEN} to continue:")
    confirmation = input_stream.readline().strip()
    if confirmation != CONFIRMATION_TOKEN:
        reporter.line()
        reporter.line("Restore cancelled.")
        return 0
    reporter.line()

    debug(
        event="restore.selection.confirmed",
        backup_path=selection.path,
        source_directory=manifest.backup.source_directory,
    )
    parsed = parse_installer_backup_archive(selection.path, install_label_prefix=manifest.backup.label_prefix)
    backup_snapshot = load_backup_snapshot(
        backup_zip_path=selection.path,
        source_directory=manifest.backup.source_directory,
    )
    state_declares_sources, state_firmware = _snapshot_source_firmware(backup_snapshot)
    archived_state = _snapshot_installed_state(backup_snapshot)
    require_external = requires_external_backup_manifest(
        package_version=parsed.package_version if parsed is not None else None,
        state_declares_source_patches=state_declares_sources,
    )
    source_entries = {
        patch.id: patch.destination
        for patch in manifest.install.source_patches
    }
    managed_entries = {
        spec.id: spec.destination
        for spec in manifest.install.external_files
    }
    declared_managed_entries = {
        record.id: record.destination
        for record in archived_state.external_files
    } if archived_state is not None else {}
    if any(
        managed_entries.get(file_id) != destination
        for file_id, destination in declared_managed_entries.items()
    ):
        raise RestoreHelperError(
            "Archived external-file state is not supported by this bundle."
        )
    archive_allowed = {**source_entries, **declared_managed_entries}
    restart_allowed = {**source_entries, **managed_entries}
    external_firmware, archived_external = load_external_backup_entries(
        backup_zip_path=selection.path,
        allowed_entries=archive_allowed,
        require_manifest=require_external,
    )
    source_external = tuple(
        entry for entry in archived_external if entry[0] in source_entries
    )
    managed_payloads = tuple(
        entry for entry in archived_external if entry[0] in declared_managed_entries
    )
    managed_external = _plan_managed_external_restore(
        paths=paths,
        manifest=manifest,
        archived_state=archived_state,
        archived_payloads=managed_payloads,
    )
    restore_firmware = None
    if source_external:
        restore_firmware = _validate_external_restore_firmware(
            paths=paths,
            manifest=manifest,
            parsed=parsed,
            state_firmware=state_firmware,
            manifest_firmware=external_firmware,
        )
    restart_targets = tuple(
        (patch_id, destination, hashlib.sha256(value).hexdigest())
        for patch_id, destination, value, _ in source_external
    ) + tuple(
        (spec.id, spec.destination, desired_sha256)
        for spec, _, _, desired_sha256, changed in managed_external
        if changed
    )
    _validate_external_restore_targets(
        paths=paths,
        manifest=manifest,
        external=source_external,
        firmware=restore_firmware,
    )
    process_id = None
    if restart_targets:
        process_id, _ = read_printer_info(
            paths.moonraker_url,
            **({"urlopen": urlopen} if urlopen is not None else {}),
        )

    journal = RollbackJournal(
        paths.recovery_sentinel_path,
        printer_data_root=paths.printer_data_root,
        source_directory=manifest.backup.source_directory,
    )
    journal.track_tree(paths.printer_data_root / manifest.backup.source_directory)
    journal.track_file(paths.restart_marker_path)
    for _, destination, _, _ in source_external:
        journal.track_file(paths.managed_klipper_root / destination)
    for spec, _, _, _, changed in managed_external:
        if changed:
            journal.track_file(
                paths.managed_klipper_root / spec.destination
            )
    try:
        journal.note_write()
        restored_snapshot = restore_backup_snapshot(
            backup_zip_path=selection.path,
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
        )
        runtime_snapshot = snapshot_runtime_tree(
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
        )
        if runtime_snapshot != backup_snapshot or restored_snapshot != backup_snapshot:
            raise RestoreHelperError(
                "Restore verification failed. "
                + describe_snapshot_difference(runtime_snapshot, backup_snapshot)
            )
        if restart_targets:
            assert process_id is not None
            journal.note_write()
            write_restart_marker(
                paths,
                restart_targets,
                operation="restore",
                process_id=process_id,
            )
            journal.note_write()
            restore_external_backup_entries(
                entries=source_external, destination_root=paths.managed_klipper_root
            )
            for spec, desired_bytes, desired_mode, _, changed in managed_external:
                if not changed:
                    continue
                journal.note_write()
                target = paths.managed_klipper_root / spec.destination
                if desired_bytes is None:
                    atomic_delete(target)
                else:
                    assert desired_mode is not None
                    atomic_write_bytes(
                        target,
                        desired_bytes,
                        mode=desired_mode,
                        force_mode=True,
                    )
    except Exception as exc:
        journal.rollback_or_raise(
            exc, backup_label=selection.label, backup_zip_path=selection.path
        )
        raise

    debug(
        event="restore.completed",
        backup_path=selection.path,
        restored_files=len(backup_snapshot),
    )
    if restart_targets:
        activated = maybe_restart_pending_service(
            paths=paths,
            allowed_entries=restart_allowed,
            reporter=reporter,
            input_stream=input_stream,
            **({"urlopen": urlopen} if urlopen is not None else {}),
        )
        if not activated:
            reporter.line("Source restoration completed; Klipper activation remains pending.")
    reporter.emit_restore_complete(
        verified_path=str(paths.printer_data_root / manifest.backup.source_directory)
    )
    return 0


def _plan_managed_external_restore(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    archived_state: InstalledState | None,
    archived_payloads: tuple[tuple[str, str, bytes, int], ...],
):
    records = {
        record.id: record
        for record in archived_state.external_files
    } if archived_state is not None else {}
    if archived_state is not None:
        try:
            compatibility = load_supported_upgrade_sources(
                paths.installer_root / "supported_upgrade_sources.yaml"
            )
            external_files.validate_state_provenance(
                state=archived_state,
                specs=manifest.install.external_files,
                upgrade_sources=compatibility,
            )
        except (CompatibilityValidationError, ExternalFileError) as exc:
            raise RestoreHelperError(
                "Archived external-file state has unsupported provenance."
            ) from exc

    payloads = {
        file_id: (destination, value, mode)
        for file_id, destination, value, mode in archived_payloads
    }
    if set(payloads) != set(records):
        raise RestoreHelperError(
            "Archived external-file payloads do not match installed state."
        )

    result = []
    for spec in manifest.install.external_files:
        source = external_files.source_path(paths, spec)
        if (
            source.is_symlink()
            or not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != spec.sha256
        ):
            raise RestoreHelperError(
                f"Managed external-file source is invalid: {spec.source}"
            )

        record = records.get(spec.id)
        desired_bytes = None
        desired_mode = None
        desired_sha256 = None
        if record is not None:
            destination, desired_bytes, desired_mode = payloads[spec.id]
            desired_sha256 = hashlib.sha256(desired_bytes).hexdigest()
            if (
                destination != record.destination
                or desired_sha256 != record.installed_sha256
            ):
                raise RestoreHelperError(
                    "Archived external-file payload does not match installed state: "
                    f"{record.destination}"
                )

        target = paths.managed_klipper_root / spec.destination
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root, target=target
        )
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RestoreHelperError(
                f"Managed external restore target is unsafe: {spec.destination}"
            )
        if target.exists():
            live_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            accepted_live_hashes = {spec.sha256}
            if desired_sha256 is not None:
                accepted_live_hashes.add(desired_sha256)
            if live_sha256 not in accepted_live_hashes:
                raise RestoreHelperError(
                    f"Managed external restore target has drifted: {spec.destination}"
                )
            changed = live_sha256 != desired_sha256
        else:
            changed = desired_sha256 is not None
        result.append(
            (
                spec,
                desired_bytes,
                desired_mode,
                desired_sha256,
                changed,
            )
        )
    return tuple(result)


def _validate_external_restore_firmware(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    parsed,
    state_firmware: str | None,
    manifest_firmware: str | None,
) -> str:
    current_firmware = detect_firmware_version(paths.firmware_manifest_path)
    if current_firmware not in manifest.firmware.supported:
        raise RestoreHelperError(
            f"External source restore does not support firmware {current_firmware}."
        )

    label_firmware = None
    if parsed is not None and parsed.firmware_token != UNKNOWN_FIRMWARE_TOKEN:
        label_firmware = parsed.firmware_token
    provenance = tuple(
        value
        for value in (manifest_firmware, label_firmware, state_firmware)
        if value is not None
    )
    if not provenance:
        raise RestoreHelperError(
            "External source backup has no firmware provenance."
        )
    if any(value != current_firmware for value in provenance):
        raise RestoreHelperError(
            "External source backup firmware does not match the printer firmware."
        )
    return current_firmware


def _validate_external_restore_targets(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    external: tuple[tuple[str, str, bytes, int], ...],
    firmware: str | None,
) -> None:
    if not external:
        return
    assert firmware is not None
    patches = {patch.id: patch for patch in manifest.install.source_patches}
    for patch_id, destination, archived_bytes, _ in external:
        target = paths.managed_klipper_root / destination
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root, target=target
        )
        if target.is_symlink() or not target.is_file():
            raise RestoreHelperError(
                f"External restore target is not a regular file: {destination}"
            )
        variants = source_patch_variants_for_firmware(
            patches[patch_id], firmware
        )
        archived_hash = hashlib.sha256(archived_bytes).hexdigest()
        accepted_hashes = {
            value
            for variant in variants
            for value in (variant.expected_sha256, variant.desired_sha256)
        }
        if archived_hash not in accepted_hashes:
            raise RestoreHelperError(
                f"External backup source is not valid for firmware {firmware}: {destination}"
            )
        if hashlib.sha256(target.read_bytes()).hexdigest() not in (
            accepted_hashes | {archived_hash}
        ):
            raise RestoreHelperError(
                f"External restore target has drifted: {destination}"
            )


def _snapshot_installed_state(
    snapshot: dict[str, bytes],
) -> InstalledState | None:
    state = snapshot.get("config/tltg_optimized_state.yaml")
    if state is None:
        return None
    try:
        raw = yaml.safe_load(state)
        return parse_installed_state(raw)
    except (yaml.YAMLError, StateValidationError) as exc:
        raise RestoreHelperError("Archived installed state is malformed.") from exc


def _snapshot_source_firmware(snapshot: dict[str, bytes]) -> tuple[bool, str | None]:
    state = snapshot.get("config/tltg_optimized_state.yaml")
    if state is None:
        return False, None
    try:
        raw = yaml.safe_load(state)
    except yaml.YAMLError as exc:
        raise RestoreHelperError("Archived installed state is malformed.") from exc
    if not isinstance(raw, dict):
        raise RestoreHelperError("Archived installed state is malformed.")
    if "source_patches" not in raw:
        return False, None
    runtime = raw.get("runtime")
    firmware = runtime.get("firmware") if isinstance(runtime, dict) else None
    if not isinstance(firmware, str) or not firmware:
        raise RestoreHelperError(
            "Archived source-aware state has no valid firmware provenance."
        )
    return True, firmware


def _resolve_backup_selection(
    *,
    printer_data_root: Path,
    install_label_prefix: str,
    backup_path: str | None,
    reporter,
    input_stream: TextIO,
    debug: DebugFn,
) -> BackupSelection | None:
    if backup_path is not None:
        resolved_path = _resolve_backup_path(backup_path)
        if not resolved_path.exists() or not resolved_path.is_file():
            raise RestoreHelperError(f"Backup zip was not found: {resolved_path}")
        selection = _selection_from_path(
            resolved_path,
            install_label_prefix=install_label_prefix,
        )
        debug(event="restore.selection.direct", backup_path=selection.path)
        return selection

    backups = list_installer_backups(
        printer_data_root,
        install_label_prefix=install_label_prefix,
    )
    if not backups:
        reporter.line(f"No installer backups were found under {printer_data_root}.")
        return None

    ordered_backups = tuple(reversed(backups))
    reporter.emit_backup_choices(
        tuple(
            (index, archive.display_created_at, archive.label, str(archive.path))
            for index, archive in enumerate(ordered_backups, start=1)
        )
    )

    while True:
        reporter.line("Select a backup number to restore, or q to cancel:")
        raw_choice = input_stream.readline().strip()
        if not raw_choice:
            reporter.line()
            continue
        if raw_choice.lower() in {"q", "quit"}:
            reporter.line()
            reporter.line("Restore cancelled.")
            return None
        try:
            selection_index = int(raw_choice)
        except ValueError:
            reporter.line()
            reporter.line("Enter a listed backup number or q.")
            continue
        if 1 <= selection_index <= len(ordered_backups):
            reporter.line()
            archive = ordered_backups[selection_index - 1]
            selection = BackupSelection(
                path=archive.path,
                label=archive.label,
                display_created_at=archive.display_created_at,
            )
            debug(event="restore.selection.menu", backup_path=selection.path)
            return selection
        reporter.line()
        reporter.line("Enter a listed backup number or q.")


def _selection_from_path(path: Path, *, install_label_prefix: str) -> BackupSelection:
    parsed = parse_installer_backup_archive(path, install_label_prefix=install_label_prefix)
    if parsed is not None:
        return BackupSelection(
            path=path,
            label=parsed.label,
            display_created_at=parsed.display_created_at,
        )
    created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return BackupSelection(
        path=path,
        label=path.stem,
        display_created_at=format_backup_display_timestamp(created_at),
    )


def _resolve_backup_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _noop_debug(**kwargs) -> None:
    return
