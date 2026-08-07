from __future__ import annotations

import argparse
import os
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path

from . import messages, safety
from .auto_update import (
    LOCK_HELD_ENV,
    AutoUpdateError,
    current_auto_update_checksum,
    disable_auto_updates,
    enable_auto_updates,
    run_auto_update_check,
)
from .box_enablement import maybe_reconcile_tool_slots_after_box_count_change
from .backup import BackupArchiveError
from .compatibility import CompatibilityValidationError, load_supported_upgrade_sources, validate_manifest_compatibility
from .demo import resolve_demo_tui_delay_seconds, run_demo
from .errors import ActivePrintError, InstallerError, OperationCancelled, PrinterStateError
from .host_reboot import (
    HostRebootError,
    maybe_schedule_host_reboot,
    perform_scheduled_host_reboot,
    read_host_reboot_marker,
    verify_completed_host_reboot,
)
from .locking import acquire
from .manifest import ManifestValidationError, load_manifest
from .models import RuntimePaths, SystemOptimizationCliOptions
from .reporter import create_reporter
from .restore_helper import RestoreHelperError, run_restore_helper
from .rollback import clear_recovery_sentinel
from .runner import run_install
from .safety import ensure_no_recovery_sentinel
from .state_file import StateValidationError
from .system_optimizations import (
    maybe_reconcile_system_optimizations,
    recover_pending_system_optimization,
    verify_rockchip_postflight,
)
from .uninstall import run_uninstall

DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")
DEFAULT_FIRMWARE_MANIFEST = Path("/home/qidi/update/firmware_manifest.json")
DEFAULT_MOONRAKER_URL = "http://127.0.0.1:7125/printer/objects/query?print_stats"



def main(
    argv: list[str] | None = None,
    *,
    stream=None,
    input_stream=None,
    bundle_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    args = parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    bundle_root = bundle_root or Path(__file__).resolve().parents[2]
    reporter = create_reporter(
        stream,
        prefer_plain=args.plain,
        bundle_root=bundle_root,
        environ=env,
        debug=args.debug,
    )
    input_stream = None if args.yes else (input_stream or sys.stdin)
    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=env)
    reporter.debug(
        event="cli.start",
        mode=args.mode,
        dry_run=args.dry_run,
        demo_tui=args.demo_tui,
        printer_data_root=paths.printer_data_root,
        bundle_root=bundle_root,
    )

    try:
        if args.demo_tui:
            delay_seconds = resolve_demo_tui_delay_seconds(environ=env)
            reporter.debug(
                event="cli.demo_tui.start",
                mode=args.mode,
                delay_seconds=delay_seconds,
            )
            run_demo(args.mode, reporter, delay_seconds=delay_seconds)
            reporter.debug(
                event="cli.complete",
                mode=args.mode,
                return_code=0,
                action="demo-tui",
            )
            return 0

        if args.mode == "complete-host-reboot":
            with acquire(paths.lock_path):
                ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    input_stream=None,
                    environ=env,
                )
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                if read_host_reboot_marker(paths) is not None and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    perform_scheduled_host_reboot(
                        paths,
                        reporter=reporter,
                        environ=env,
                    )
            return 0

        if args.mode == "auto-update-check":
            with acquire(paths.lock_path):
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
                reporter.debug(
                    event="cli.recovery_sentinel.clear",
                    sentinel_path=paths.recovery_sentinel_path,
                )
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    input_stream=None,
                    environ=env,
                )
                maybe_reconcile_tool_slots_after_box_count_change(paths=paths, reporter=reporter)
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                pending_reboot = read_host_reboot_marker(paths)
                release_state_committed = _auto_reboot_release_committed(
                    paths=paths,
                    marker=pending_reboot,
                )
                if release_state_committed and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    maybe_schedule_host_reboot(
                        paths,
                        reporter=reporter,
                        input_stream=None,
                        environ=env,
                        automatic=True,
                    )
                    reporter.debug(
                        event="cli.complete",
                        mode=args.mode,
                        return_code=0,
                        action="pending-host-reboot",
                    )
                    return 0
                result = run_auto_update_check(paths=paths, reporter=reporter, environ=env)
                if result.action == "already-current":
                    try:
                        safety.ensure_printer_idle(paths.moonraker_url)
                    except ActivePrintError:
                        reporter.line(messages.AUTO_UPDATE_SKIPPED_ACTIVE_PRINT)
                    except PrinterStateError:
                        reporter.line(messages.AUTO_UPDATE_SKIPPED_UNKNOWN_STATE)
                    else:
                        current_manifest = load_manifest(paths.installer_root / "package.yaml")
                        maybe_reconcile_system_optimizations(
                            paths=paths,
                            manifest=current_manifest,
                            reporter=reporter,
                            environ=env,
                        )
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                pending_reboot = read_host_reboot_marker(paths)
                if _auto_reboot_release_committed(
                    paths=paths,
                    marker=pending_reboot,
                ) and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    maybe_schedule_host_reboot(
                        paths,
                        reporter=reporter,
                        input_stream=None,
                        environ=env,
                        automatic=True,
                    )
                reporter.debug(
                    event="cli.complete",
                    mode=args.mode,
                    return_code=0,
                    action=result.action,
                )
                return 0

        if args.mode == "enable-auto-updates":
            enable_auto_updates(paths=paths, reporter=reporter, input_stream=input_stream, environ=env)
            reporter.debug(event="cli.complete", mode=args.mode, return_code=0)
            return 0

        if args.mode == "disable-auto-updates":
            disable_auto_updates(paths=paths, reporter=reporter, input_stream=input_stream)
            reporter.debug(event="cli.complete", mode=args.mode, return_code=0)
            return 0

        if args.mode == "clear-recovery-sentinel":
            with acquire(paths.lock_path):
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                manifest = load_manifest(paths.installer_root / "package.yaml")
                removed = clear_recovery_sentinel(
                    paths.recovery_sentinel_path,
                    printer_data_root=paths.printer_data_root,
                    external_root=paths.managed_klipper_root,
                    allowed_external_entries={
                        **{
                            patch.id: patch.destination
                            for patch in manifest.install.source_patches
                        },
                        **{
                            spec.id: spec.destination
                            for spec in manifest.install.external_files
                        },
                    },
                )
                reporter.emit_clear_recovery_sentinel(removed)
                reporter.debug(
                    event="cli.complete",
                    mode=args.mode,
                    removed=removed,
                )
                return 0

        manifest = load_manifest(paths.installer_root / "package.yaml")
        reporter.debug(
            event="cli.manifest.loaded",
            package_version=manifest.package.version,
            install_label_prefix=manifest.backup.label_prefix,
        )

        if args.mode == "restore-backup":
            with acquire(paths.lock_path):
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                rc = run_restore_helper(
                    paths,
                    manifest,
                    reporter=reporter,
                    input_stream=input_stream,
                    backup_path=args.backup,
                    debug=reporter.debug,
                )
                reporter.debug(event="cli.complete", mode=args.mode, return_code=rc)
                return rc

        compatibility = load_supported_upgrade_sources(
            paths.installer_root / "supported_upgrade_sources.yaml"
        )
        validate_manifest_compatibility(manifest, compatibility)
        reporter.debug(
            event="cli.compatibility.validated",
            known_versions=len(manifest.package.known_versions),
        )

        lock_inherited = _lock_inherited(args.mode, env)
        with nullcontext() if lock_inherited else acquire(paths.lock_path):
            reporter.debug(
                event="cli.lock.inherited" if lock_inherited else "cli.lock.acquired",
                mode=args.mode,
                lock_path=paths.lock_path,
            )
            ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
            reporter.debug(
                event="cli.recovery_sentinel.clear",
                sentinel_path=paths.recovery_sentinel_path,
            )
            if not args.dry_run:
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=manifest,
                    reporter=reporter,
                    input_stream=input_stream,
                    environ=env,
                )
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=manifest,
                    reporter=reporter,
                    environ=env,
                )
            if args.mode == "install":
                run_install(
                    paths,
                    manifest,
                    reporter,
                    dry_run=args.dry_run,
                    input_stream=input_stream,
                    environ=env,
                    system_options=_system_options_from_args(args),
                )
            else:
                run_uninstall(
                    paths,
                    manifest,
                    compatibility,
                    reporter,
                    dry_run=args.dry_run,
                    input_stream=input_stream,
                    environ=env,
                    system_options=_system_options_from_args(args),
                )
        if args.dry_run:
            if args.reboot_host:
                reporter.line("Host reboot dry-run: would schedule a delayed reboot after successful completion.")
        elif (
            not lock_inherited
            and read_host_reboot_marker(paths) is not None
            and _host_reboot_operation_current(
                manifest=manifest,
                reporter=reporter,
                environ=env,
            )
        ):
            maybe_schedule_host_reboot(
                paths,
                reporter=reporter,
                input_stream=input_stream,
                environ=env,
                explicit=args.reboot_host,
            )
        reporter.debug(event="cli.complete", mode=args.mode, return_code=0)
        return 0
    except KeyboardInterrupt:
        reporter.debug(
            event="cli.interrupted",
            mode=args.mode,
            return_code=130,
        )
        reporter.line(messages.INTERRUPTED)
        return 130
    except OperationCancelled as exc:
        reporter.debug(
            event="cli.complete",
            mode=args.mode,
            return_code=0,
            action="cancelled",
        )
        return 0
    except (
        ManifestValidationError,
        CompatibilityValidationError,
        StateValidationError,
        BackupArchiveError,
        RestoreHelperError,
        AutoUpdateError,
    ) as exc:
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
        )
        reporter.emit_error(exc)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1
    except InstallerError as exc:
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
        )
        reporter.emit_error(exc)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        reporter.emit_error(exc)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1



def _verify_completed_host_reboot(
    *,
    paths: RuntimePaths,
    manifest,
    reporter,
    environ: dict[str, str],
) -> bool:
    if read_host_reboot_marker(paths) is None:
        return False
    spec = manifest.system_optimizations
    if spec is None:
        raise HostRebootError(messages.HOST_REBOOT_POSTFLIGHT_FAILED)
    return verify_completed_host_reboot(
        paths,
        reporter=reporter,
        verify_operation=lambda: verify_rockchip_postflight(
            spec=spec.rockchip_root_sync,
            environ=environ,
        ),
    )


def _auto_reboot_release_committed(*, paths: RuntimePaths, marker) -> bool:
    if marker is None:
        return False
    if marker.source != "auto_update_child":
        return True
    before = marker.auto_update_checksum_before
    return before is not None and current_auto_update_checksum(paths) not in {None, before}


def _host_reboot_operation_current(*, manifest, reporter, environ: dict[str, str]) -> bool:
    spec = manifest.system_optimizations
    if spec is None:
        reporter.line(messages.HOST_REBOOT_CURRENT_STATE_FAILED)
        return False
    try:
        verify_rockchip_postflight(
            spec=spec.rockchip_root_sync,
            environ=environ,
        )
    except InstallerError:
        reporter.line(messages.HOST_REBOOT_CURRENT_STATE_FAILED)
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QIDI Max 4 Optimized installer runtime")
    parser.add_argument(
        "mode",
        choices=[
            "install",
            "uninstall",
            "clear-recovery-sentinel",
            "restore-backup",
            "auto-update-check",
            "enable-auto-updates",
            "disable-auto-updates",
            "complete-host-reboot",
        ],
    )
    parser.add_argument("--plain", action="store_true", help="Use plain text output instead of the rich terminal UI.")
    parser.add_argument("--debug", action="store_true", help="Print debug events and tracebacks on failure.")
    parser.add_argument("--dry-run", action="store_true", help="Preview install or uninstall actions without writing changes.")
    parser.add_argument("--demo-tui", action="store_true", help="Render a demo of the install/uninstall TUI without writing changes.")
    parser.add_argument("--backup", help="Backup label or path to restore with restore-backup mode.")
    parser.add_argument("--yes", action="store_true", help="Run non-interactively using default yes-mode choices.")
    parser.add_argument("--skip-system-optimizations", action="store_true", help="Skip DNS, APT, qidiclient GIF, Rockchip root-mount, VPN, Bluetooth, and AI service changes.")
    parser.add_argument("--disable-ai-detection", action="store_true", help="Disable the QIDI AI detection backend service when system optimizations run.")
    parser.add_argument("--keep-ai-detection", action="store_true", help="Keep the QIDI AI detection backend service enabled when system optimizations run.")
    parser.add_argument("--keep-system-optimizations", action="store_true", help="During uninstall, leave installer-managed system settings in place.")
    parser.add_argument("--reboot-host", action="store_true", help="Authorize a pending managed host OS reboot after successful install or uninstall.")
    args = parser.parse_args(argv)
    if args.dry_run and args.mode not in {"install", "uninstall"}:
        parser.error("--dry-run is only supported with install and uninstall.")
    if args.demo_tui and args.mode not in {"install", "uninstall"}:
        parser.error("--demo-tui is only supported with install and uninstall.")
    if args.demo_tui and args.dry_run:
        parser.error("--demo-tui cannot be combined with --dry-run.")
    if args.backup and args.mode != "restore-backup":
        parser.error("--backup is only supported with restore-backup.")
    if args.yes and args.mode not in {"install", "uninstall", "auto-update-check"}:
        parser.error("--yes is only supported with install, uninstall, and auto-update-check.")
    if args.disable_ai_detection and args.keep_ai_detection:
        parser.error("--disable-ai-detection and --keep-ai-detection cannot be combined.")
    if args.skip_system_optimizations and (args.disable_ai_detection or args.keep_ai_detection):
        parser.error("AI detection flags cannot be combined with --skip-system-optimizations.")
    if (args.skip_system_optimizations or args.disable_ai_detection or args.keep_ai_detection) and args.mode not in {"install", "uninstall"}:
        parser.error("system optimization flags are only supported with install and uninstall.")
    if args.keep_system_optimizations and args.mode != "uninstall":
        parser.error("--keep-system-optimizations is only supported with uninstall.")
    if args.reboot_host and args.mode not in {"install", "uninstall"}:
        parser.error("--reboot-host is only supported with install and uninstall.")
    return args



def _system_options_from_args(args: argparse.Namespace) -> SystemOptimizationCliOptions:
    return SystemOptimizationCliOptions(
        skip_system_optimizations=args.skip_system_optimizations,
        disable_ai_detection=args.disable_ai_detection,
        keep_ai_detection=args.keep_ai_detection,
        keep_system_optimizations=args.keep_system_optimizations,
        reboot_host=args.reboot_host,
    )



def _lock_inherited(mode: str, environ: dict[str, str]) -> bool:
    return mode == "install" and environ.get(LOCK_HELD_ENV) == "1"



def resolve_runtime_paths(*, bundle_root: Path, environ: dict[str, str]) -> RuntimePaths:
    printer_data_root = Path(
        environ.get("TLTG_OPTIMIZED_PRINTER_DATA_ROOT", DEFAULT_PRINTER_DATA_ROOT)
    )
    firmware_manifest_path = Path(
        environ.get("TLTG_OPTIMIZED_FIRMWARE_MANIFEST", DEFAULT_FIRMWARE_MANIFEST)
    )
    moonraker_url = environ.get("TLTG_OPTIMIZED_MOONRAKER_URL", DEFAULT_MOONRAKER_URL)
    return RuntimePaths(
        bundle_root=bundle_root,
        installer_root=bundle_root / "installer",
        printer_data_root=printer_data_root,
        config_root=printer_data_root / "config",
        firmware_manifest_path=firmware_manifest_path,
        moonraker_url=moonraker_url,
        lock_path=printer_data_root / ".tltg_optimized_installer.lock",
        recovery_sentinel_path=printer_data_root / ".tltg_optimized_recovery_required",
        backup_root=printer_data_root,
        klipper_root=(
            Path(environ["TLTG_OPTIMIZED_KLIPPER_ROOT"])
            if "TLTG_OPTIMIZED_KLIPPER_ROOT" in environ
            else None
        ),
        host_reboot_marker_override=(
            Path(environ["TLTG_OPTIMIZED_HOST_REBOOT_MARKER"])
            if "TLTG_OPTIMIZED_HOST_REBOOT_MARKER" in environ
            else None
        ),
        boot_id_path_override=(
            Path(environ["TLTG_OPTIMIZED_BOOT_ID_PATH"])
            if "TLTG_OPTIMIZED_BOOT_ID_PATH" in environ
            else None
        ),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
