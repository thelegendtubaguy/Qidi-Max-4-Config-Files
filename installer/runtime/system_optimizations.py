from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import messages
from .auto_update import current_auto_update_checksum
from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_text
from .host_reboot import clear_host_reboot_marker, read_host_reboot_marker, write_host_reboot_marker
from .interaction import confirm_yes, prompt_yes
from .models import (
    InstalledState,
    Manifest,
    RuntimePaths,
    SystemOptimizationCliOptions,
    SystemOptimizationsSpec,
    SystemRockchipRootSyncSpec,
)
from .state_file import StateValidationError, load_installed_state, write_installed_state
from .sudo import authenticate_sudo, run_sudo, run_sudo_ignore_failure, run_sudo_or_raise

SYSTEM_ROOT_ENV = "TLTG_OPTIMIZED_SYSTEM_ROOT"
DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")
SYSTEM_BACKUP_DIR = "tltg_optimized_system_backups"
SYSTEM_JOURNAL = ".tltg_optimized_system_journal.json"
AI_TOUCHSCREEN_WARNING = (
    "Turn off Settings -> Printing Options -> Spaghetti Detection and Foreign Object "
    "Detection on the touchscreen if you want the screen to match the disabled backend state."
)
QIDICLIENT_ARCHIVE_ROOTS = frozenset(
    {
        "account",
        "block_popup",
        "filament",
        "network",
        "offline_update",
        "set_filament",
        "startup",
    }
)
SYSTEMD_ENABLED_STATES = frozenset({"enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "masked", "masked-runtime", "static", "indirect", "disabled", "generated", "transient", "bad"})
SYSTEMD_ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance", "unknown"})
SERVICE_DISABLED_ENABLED_STATES = frozenset({"disabled", "masked", "masked-runtime"})
SERVICE_DISABLED_ACTIVE_STATES = frozenset({"inactive"})
MOONRAKER_METADATA_OPERATION = "moonraker_metadata_3mf_plate_index"
MOONRAKER_METADATA_PATCH_MARKER = "def _3mf_selected_plate_index(xml_data: str) -> int:"
ROCKCHIP_ROOT_SYNC_OPERATION = "rockchip_root_sync"
ROCKCHIP_UNSUPPORTED = frozenset({"unsupported", "conflicting_dropin", "unsafe_path"})
ROCKCHIP_ALREADY_CURRENT = frozenset({"already_current_owned", "already_current_unowned"})


class SystemOptimizationError(InstallerError):
    pass


class SystemOptimizationApplyError(SystemOptimizationError):
    def __init__(self, message: str, ledger: dict[str, Any]):
        super().__init__(message)
        self.ledger = ledger


class SystemOptimizationRecoveryError(SystemOptimizationError):
    pass


def maybe_apply_system_optimizations(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    state: InstalledState,
    state_path: Path,
    input_stream,
    cli_options: SystemOptimizationCliOptions,
    environ: dict[str, str],
    auto_update_child: bool,
    run=subprocess.run,
) -> InstalledState:
    spec = manifest.system_optimizations
    if spec is None:
        return state
    policy = resolve_policy(
        prior_ledger=state.system_ledger,
        reporter=reporter,
        input_stream=input_stream,
        cli_options=cli_options,
        auto_update_child=auto_update_child,
    )
    if policy is None:
        return state
    ledger = _ledger_with_policy(state.system_ledger, policy)
    if not _system_root_allowed(paths=paths, environ=environ):
        reporter.debug(event="system_optimizations.skipped", reason="non-default-system-root")
        updated = _replace_system_ledger(state, ledger)
        write_installed_state(state_path, updated)
        return updated
    try:
        ledger = apply_system_optimizations(
            paths=paths,
            spec=spec,
            ledger=ledger,
            reporter=reporter,
            input_stream=input_stream,
            environ=environ,
            source="auto_update_child" if auto_update_child else "yes_install" if input_stream is None else "interactive_install",
            package_version=manifest.package.version,
            run=run,
        )
    except SystemOptimizationRecoveryError:
        raise
    except SystemOptimizationApplyError as exc:
        ledger = exc.ledger
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {exc.message}")
    except InstallerError as exc:
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {getattr(exc, 'message', str(exc))}")
    updated = _replace_system_ledger(state, ledger)
    write_installed_state(state_path, updated)
    _clear_committed_rockchip_journal(paths, ledger)
    return updated


def recover_pending_system_optimization(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    input_stream,
    environ: dict[str, str],
    run=subprocess.run,
) -> bool:
    if not _journal_path(paths).exists() or not _system_root_allowed(paths=paths, environ=environ):
        return False
    ledger: dict[str, Any] = {}
    state_path = paths.printer_data_root / manifest.state_file
    if state_path.exists():
        try:
            state = load_installed_state(state_path)
        except StateValidationError:
            state = None
        if state is not None and isinstance(state.system_ledger, dict):
            ledger = state.system_ledger
    root = _system_root(environ)
    sudo_password = None if _is_fake_root(root) else _sudo_password(
        reporter=reporter,
        input_stream=input_stream,
        environ=environ,
        run=run,
    )
    before = _journal_path(paths).exists()
    if manifest.system_optimizations is None:
        raise SystemOptimizationRecoveryError("Manifest has no Rockchip recovery specification.")
    _recover_pending_rockchip_journal(
        paths=paths,
        root=root,
        sudo_password=sudo_password,
        run=run,
        ledger=ledger,
        spec=manifest.system_optimizations.rockchip_root_sync,
    )
    recovered = before and not _journal_path(paths).exists()
    if recovered:
        reporter.line("Pending Rockchip system transaction recovered.")
    return recovered


def maybe_reconcile_system_optimizations(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    environ: dict[str, str],
    run=subprocess.run,
) -> None:
    if manifest.system_optimizations is None:
        return
    state_path = paths.printer_data_root / manifest.state_file
    if not state_path.exists():
        return
    try:
        state = load_installed_state(state_path)
    except StateValidationError:
        return
    policy = state.system_ledger.get("policy") if isinstance(state.system_ledger, dict) else None
    if not isinstance(policy, dict):
        return
    ledger = _ledger_with_policy(state.system_ledger, {
        "system_optimizations": str(policy.get("system_optimizations", "disabled")),
        "ai_detection": str(policy.get("ai_detection", "unset")),
    })
    if not _system_root_allowed(paths=paths, environ=environ):
        return
    try:
        ledger = apply_system_optimizations(
            paths=paths,
            spec=manifest.system_optimizations,
            ledger=ledger,
            reporter=reporter,
            input_stream=None,
            environ=environ,
            source="auto_update_reconcile",
            package_version=manifest.package.version,
            run=run,
        )
    except SystemOptimizationRecoveryError:
        raise
    except SystemOptimizationApplyError as exc:
        ledger = exc.ledger
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {exc.message}")
    except InstallerError as exc:
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {getattr(exc, 'message', str(exc))}")
    updated = _replace_system_ledger(state, ledger)
    write_installed_state(state_path, updated)
    _clear_committed_rockchip_journal(paths, ledger)



def maybe_emit_system_dry_run(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    prior_state: InstalledState | None,
    cli_options: SystemOptimizationCliOptions,
    environ: dict[str, str],
) -> None:
    if manifest.system_optimizations is None:
        return
    prior_ledger = prior_state.system_ledger if prior_state is not None else None
    policy = _noninteractive_policy(prior_ledger, cli_options, auto_update_child=False)
    if policy is None:
        policy = {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}
    reporter.line("System optimizations dry-run:")
    if policy.get("system_optimizations") != "enabled":
        reporter.line("  - skipped by policy")
        return
    for operation_id in _selected_operation_ids(manifest.system_optimizations, policy):
        if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
            if not _system_root_allowed(paths=paths, environ=environ):
                reporter.line(f"  - would evaluate {operation_id}")
                continue
            classification = _classify_rockchip_operation(
                paths=paths,
                spec=manifest.system_optimizations.rockchip_root_sync,
                root=_system_root(environ),
                prior_owned=(
                    isinstance(prior_ledger, dict)
                    and isinstance(prior_ledger.get("restore_preimages"), dict)
                    and operation_id in prior_ledger["restore_preimages"]
                ),
                run=subprocess.run,
            )
            if classification in ROCKCHIP_UNSUPPORTED:
                reporter.line(f"  - would preserve rockchip_root_sync ({classification})")
                continue
            if classification in ROCKCHIP_ALREADY_CURRENT:
                reporter.line("  - rockchip_root_sync already current")
                continue
            reporter.line(f"  - would apply {operation_id}")
            reporter.line("  - would install the guarded systemd drop-in, reload/start the unit, and remount / without sync")
            reporter.line("  - would record a pending delayed host reboot after successful postflight")
            continue
        reporter.line(f"  - would apply {operation_id}")
    if not _system_root_allowed(paths=paths, environ=environ):
        reporter.line("  - real system writes skipped outside the printer runtime root")


def maybe_prompt_restore_system_optimizations(
    *,
    state: InstalledState,
    reporter,
    input_stream,
    cli_options: SystemOptimizationCliOptions,
) -> bool:
    if not _has_restore_preimages(state.system_ledger):
        return False
    if cli_options.keep_system_optimizations:
        return False
    if input_stream is None:
        return True
    return confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.SYSTEM_RESTORE_PROMPT,
        instruction=messages.SYSTEM_RESTORE_PROMPT_INSTRUCTION,
        cancel_message=messages.SYSTEM_RESTORE_SKIPPED,
    )


def restore_system_optimizations(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    state: InstalledState,
    reporter,
    input_stream,
    environ: dict[str, str],
    run=subprocess.run,
) -> None:
    ledger = state.system_ledger or {}
    preimages = ledger.get("restore_preimages")
    if not isinstance(preimages, dict) or not preimages:
        return
    if not _system_root_allowed(paths=paths, environ=environ):
        reporter.debug(event="system_optimizations.restore.skipped", reason="non-default-system-root")
        return
    root = _system_root(environ)
    sudo_password = None if _is_fake_root(root) else _sudo_password(
        reporter=reporter, input_stream=input_stream, environ=environ, run=run
    )
    rockchip_restored = False
    for operation_id in reversed(list(preimages.keys())):
        preimage = preimages[operation_id]
        if operation_id == "dns":
            for item in reversed(preimage.get("files", [])):
                _restore_file_preimage(item, paths=paths, root=root, sudo_password=sudo_password, run=run)
            if not _is_fake_root(root):
                run_sudo_ignore_failure(["resolvconf", "-u"], run=run, password=sudo_password or "")
        elif operation_id == "apt_sources":
            _restore_file_preimage(preimage["file"], paths=paths, root=root, sudo_password=sudo_password, run=run)
        elif operation_id.startswith("service_"):
            _restore_service(preimage, root=root, sudo_password=sudo_password, run=run)
        elif operation_id == "qidiclient_static_gifs":
            _restore_gifs(preimage, root=root, sudo_password=sudo_password, run=run)
        elif operation_id == MOONRAKER_METADATA_OPERATION:
            _restore_file_preimage(preimage, paths=paths, root=root, sudo_password=sudo_password, run=run)
            _restart_service("moonraker.service", root=root, sudo_password=sudo_password, run=run)
        elif operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
            if manifest.system_optimizations is None:
                raise SystemOptimizationError("Manifest has no Rockchip restore specification.")
            restored = _restore_rockchip(
                preimage,
                spec=manifest.system_optimizations.rockchip_root_sync,
                paths=paths,
                root=root,
                sudo_password=sudo_password,
                run=run,
                require_desired_live=True,
            )
            if restored:
                rockchip_restored = True
            else:
                reporter.line("Rockchip system state was modified after install and was preserved.")
    if rockchip_restored:
        clear_host_reboot_marker(paths)
    reporter.line(messages.SYSTEM_RESTORE_COMPLETE)


def resolve_policy(
    *,
    prior_ledger: dict[str, Any] | None,
    reporter,
    input_stream,
    cli_options: SystemOptimizationCliOptions,
    auto_update_child: bool,
) -> dict[str, str] | None:
    explicit = _noninteractive_policy(
        prior_ledger,
        cli_options,
        auto_update_child=auto_update_child,
        interactive=input_stream is not None,
    )
    if explicit is not None:
        return explicit
    if auto_update_child:
        return None
    if input_stream is None:
        return {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}
    if not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.SYSTEM_OPTIMIZATIONS_PROMPT,
        instruction=messages.SYSTEM_OPTIMIZATIONS_PROMPT_INSTRUCTION,
        cancel_message=messages.SYSTEM_OPTIMIZATIONS_SKIPPED,
    ):
        return {"system_optimizations": "disabled", "ai_detection": "unset"}
    if prompt_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.AI_DETECTION_PROMPT,
        instruction=messages.AI_DETECTION_PROMPT_INSTRUCTION,
    ):
        reporter.line(AI_TOUCHSCREEN_WARNING)
        return {"system_optimizations": "enabled", "ai_detection": "disable"}
    return {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}


def apply_system_optimizations(
    *,
    paths: RuntimePaths,
    spec: SystemOptimizationsSpec,
    ledger: dict[str, Any],
    reporter,
    input_stream,
    environ: dict[str, str],
    source: str,
    package_version: str = "unknown",
    run=subprocess.run,
) -> dict[str, Any]:
    root = _system_root(environ)
    sudo_password = None if _is_fake_root(root) else _sudo_password(
        reporter=reporter, input_stream=input_stream, environ=environ, run=run
    )
    actions = list(ledger.get("actions", [])) if isinstance(ledger.get("actions"), list) else []
    restore_preimages = dict(ledger.get("restore_preimages", {})) if isinstance(ledger.get("restore_preimages"), dict) else {}
    initial_restore_preimages = dict(restore_preimages)
    policy = ledger.get("policy", {}) if isinstance(ledger.get("policy"), dict) else {}
    _recover_pending_rockchip_journal(
        paths=paths,
        root=root,
        sudo_password=sudo_password,
        run=run,
        ledger=ledger,
        spec=spec.rockchip_root_sync,
    )
    selected_ids = _selected_operation_ids(spec, policy)
    if "qidiclient_static_gifs" in selected_ids:
        _validate_qidiclient_archive(paths.installer_root / spec.qidiclient_static_gifs.archive, spec.qidiclient_static_gifs.sha256)
    applied_any = False
    current_preimages: dict[str, Any] = {}
    try:
        for operation_id in selected_ids:
            started_at = _now()
            if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
                classification = _classify_rockchip_operation(
                    paths=paths,
                    spec=spec.rockchip_root_sync,
                    root=root,
                    prior_owned=operation_id in restore_preimages,
                    run=run,
                )
                if classification in ROCKCHIP_UNSUPPORTED or classification in ROCKCHIP_ALREADY_CURRENT:
                    if classification in ROCKCHIP_UNSUPPORTED:
                        reporter.line(f"Rockchip root optimization preserved current state: {classification}.")
                    actions.append(
                        _action_record(
                            operation_id=operation_id,
                            status=classification,
                            started_at=started_at,
                            preimage=None,
                            desired=_desired(operation_id, spec),
                            postflight={"classification": classification},
                            source=source,
                            reconciled=False,
                        )
                    )
                    continue
                preimage = _capture_rockchip_preimage(
                    paths=paths,
                    spec=spec.rockchip_root_sync,
                    root=root,
                    classification=classification,
                    run=run,
                )
                transaction_id = uuid.uuid4().hex
                journal = {
                    "schema_version": 1,
                    "operation": operation_id,
                    "transaction_id": transaction_id,
                    "started_at": started_at,
                    "phase": "captured",
                    "preimage": preimage,
                }
                atomic_write_text(
                    _journal_path(paths),
                    json.dumps(journal, sort_keys=True, indent=2) + "\n",
                    mode=0o600,
                    force_mode=True,
                )
                current_preimages[operation_id] = preimage
                _apply_rockchip(
                    spec=spec.rockchip_root_sync,
                    root=root,
                    sudo_password=sudo_password,
                    run=run,
                    classification=classification,
                )
                postflight = _postflight_rockchip(spec=spec.rockchip_root_sync, root=root, run=run)
                installer_owned = classification != "desired_unowned_mount_drift"
                if installer_owned:
                    write_host_reboot_marker(
                        paths,
                        package_version=package_version,
                        source=source,
                        auto_update_checksum_before=(
                            current_auto_update_checksum(paths)
                            if source == "auto_update_child"
                            else None
                        ),
                    )
                    restore_preimages.setdefault(operation_id, preimage)
                action = _action_record(
                    operation_id=operation_id,
                    status="applied" if installer_owned else "reconciled_unowned",
                    started_at=started_at,
                    preimage=preimage,
                    desired=_desired(operation_id, spec),
                    postflight=postflight,
                    source=source,
                    reconciled=source in {"auto_update_child", "auto_update_reconcile"},
                )
                action["transaction_id"] = transaction_id
                actions.append(action)
                applied_any = True
                continue
            preimage = _preimage_before_apply_if_required(
                operation_id,
                paths=paths,
                spec=spec,
                root=root,
                run=run,
            )
            if operation_id.startswith("service_") or operation_id == MOONRAKER_METADATA_OPERATION:
                if not preimage.get("exists", True):
                    actions.append(
                        _action_record(
                            operation_id=operation_id,
                            status="missing",
                            started_at=started_at,
                            preimage=preimage,
                            desired=_desired(operation_id, spec),
                            postflight=(
                                {"path": preimage.get("path"), "exists": False}
                                if operation_id == MOONRAKER_METADATA_OPERATION
                                else {"service": preimage.get("service"), "exists": False}
                            ),
                            source=source,
                            reconciled=False,
                        )
                    )
                    continue
                if operation_id == MOONRAKER_METADATA_OPERATION:
                    if not _operation_needs_apply(operation_id, spec=spec, root=root, run=run):
                        actions.append(
                            _action_record(
                                operation_id=operation_id,
                                status="already_current",
                                started_at=started_at,
                                preimage=None,
                                desired=_desired(operation_id, spec),
                                postflight=_postflight_operation(operation_id, paths=paths, spec=spec, root=root, run=run),
                                source=source,
                                reconciled=False,
                            )
                        )
                        continue
                elif _service_state_is_disabled(preimage):
                    actions.append(
                        _action_record(
                            operation_id=operation_id,
                            status="already_current",
                            started_at=started_at,
                            preimage=preimage,
                            desired=_desired(operation_id, spec),
                            postflight=preimage,
                            source=source,
                            reconciled=False,
                        )
                    )
                    continue
            elif not _operation_needs_apply(operation_id, spec=spec, root=root, run=run):
                actions.append(
                    _action_record(
                        operation_id=operation_id,
                        status="already_current",
                        started_at=started_at,
                        preimage=None,
                        desired=_desired(operation_id, spec),
                        postflight="ok",
                        source=source,
                        reconciled=False,
                    )
                )
                continue
            journal = {"operation": operation_id, "started_at": started_at}
            _journal_path(paths).write_text(json.dumps(journal, indent=2), encoding="utf-8")
            if preimage is None:
                preimage = _capture_operation_preimage(
                    operation_id,
                    paths=paths,
                    spec=spec,
                    root=root,
                    run=run,
                )
            current_preimages[operation_id] = preimage
            _apply_operation(
                operation_id,
                paths=paths,
                spec=spec,
                root=root,
                sudo_password=sudo_password,
                run=run,
                preimage=preimage,
            )
            postflight = _postflight_operation(
                operation_id,
                paths=paths,
                spec=spec,
                root=root,
                run=run,
            )
            action = _action_record(
                operation_id=operation_id,
                status="applied",
                started_at=journal["started_at"],
                preimage=preimage,
                desired=_desired(operation_id, spec),
                postflight=postflight,
                source=source,
                reconciled=source in {"auto_update_child", "auto_update_reconcile"},
            )
            actions.append(action)
            restore_preimages.setdefault(operation_id, preimage)
            _journal_path(paths).unlink(missing_ok=True)
            applied_any = True
        for operation_id in _policy_skipped_optional_service_operation_ids(spec, policy, selected_ids):
            started_at = _now()
            preimage = _capture_operation_preimage(
                operation_id,
                paths=paths,
                spec=spec,
                root=root,
                run=run,
            )
            actions.append(
                _action_record(
                    operation_id=operation_id,
                    status="missing" if not preimage.get("exists", True) else "skipped_by_policy",
                    started_at=started_at,
                    preimage=preimage,
                    desired={"enabled": "unchanged", "active": "unchanged"},
                    postflight={"service": preimage.get("service"), "exists": preimage.get("exists", True)},
                    source=source,
                    reconciled=False,
                )
            )
    except BaseException as exc:
        try:
            _restore_preimage_map(
                current_preimages,
                paths=paths,
                root=root,
                sudo_password=sudo_password,
                run=run,
            )
        except Exception as rollback_exc:
            raise SystemOptimizationRecoveryError(
                f"System optimization rollback failed: {getattr(rollback_exc, 'message', str(rollback_exc))}"
            ) from rollback_exc
        atomic_delete(_journal_path(paths))
        if isinstance(exc, KeyboardInterrupt):
            raise
        partial = {**ledger, "actions": actions, "restore_preimages": initial_restore_preimages}
        raise SystemOptimizationApplyError(getattr(exc, "message", str(exc)), partial) from exc
    if source in {"auto_update_child", "auto_update_reconcile"}:
        reporter.line(
            "System optimizations reconciled."
            if applied_any
            else "System optimizations already current."
        )
    return {**ledger, "actions": actions, "restore_preimages": restore_preimages}


def _action_record(
    *,
    operation_id: str,
    status: str,
    started_at: str,
    preimage: dict[str, Any] | None,
    desired: dict[str, Any],
    postflight: Any,
    source: str,
    reconciled: bool,
) -> dict[str, Any]:
    return {
        "id": operation_id,
        "status": status,
        "started_at": started_at,
        "completed_at": _now(),
        "preimage": preimage,
        "desired": desired,
        "postflight": postflight,
        "source": source,
        "reconciled": reconciled,
    }


def _preimage_before_apply_if_required(
    operation_id: str,
    *,
    paths: RuntimePaths,
    spec: SystemOptimizationsSpec,
    root: Path,
    run,
) -> dict[str, Any] | None:
    if operation_id.startswith("service_") or operation_id == MOONRAKER_METADATA_OPERATION:
        return _capture_operation_preimage(operation_id, paths=paths, spec=spec, root=root, run=run)
    return None


def _postflight_operation(
    operation_id: str,
    *,
    paths: RuntimePaths,
    spec: SystemOptimizationsSpec,
    root: Path,
    run,
) -> Any:
    if operation_id.startswith("service_"):
        service = _service_for_operation(operation_id, spec)
        state = {**_service_state(service, root=root, run=run), "service": service}
        if state.get("exists", True) and not _service_state_is_disabled(state):
            raise SystemOptimizationError(f"Service did not reach disabled inactive state: {service}")
        return state
    if operation_id == "qidiclient_static_gifs":
        return _postflight_gifs(paths=paths, spec=spec, root=root)
    if operation_id == MOONRAKER_METADATA_OPERATION:
        target = _map_path(root, spec.moonraker_metadata_3mf.file)
        return {"path": spec.moonraker_metadata_3mf.file, "patched": target.exists() and MOONRAKER_METADATA_PATCH_MARKER in target.read_text(encoding="utf-8")}
    if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
        return _postflight_rockchip(spec=spec.rockchip_root_sync, root=root, run=run)
    return "ok"


def _capture_operation_preimage(operation_id: str, *, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path, run) -> dict[str, Any]:
    if operation_id == "dns":
        return {
            "files": [
                _capture_file(spec.dns.resolv_conf, paths=paths, root=root),
                _capture_file(spec.dns.resolvconf_head, paths=paths, root=root),
                _capture_file(spec.dns.resolvconf_tail, paths=paths, root=root),
            ]
        }
    if operation_id == "apt_sources":
        return {"file": _capture_file(spec.apt_sources.file, paths=paths, root=root)}
    if operation_id == "qidiclient_static_gifs":
        return _capture_gifs_preimage(paths=paths, spec=spec, root=root)
    if operation_id == MOONRAKER_METADATA_OPERATION:
        return _capture_file(spec.moonraker_metadata_3mf.file, paths=paths, root=root)
    if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
        return _capture_rockchip_preimage(
            paths=paths,
            spec=spec.rockchip_root_sync,
            root=root,
            classification="owned_reconcile",
            run=run,
        )
    if operation_id.startswith("service_"):
        service = _service_for_operation(operation_id, spec)
        return {**_service_state(service, root=root, run=run), "service": service}
    raise SystemOptimizationError(f"Unknown system operation: {operation_id}")



def _apply_operation(operation_id: str, *, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path, sudo_password: str | None, run, preimage: dict[str, Any]) -> None:
    if operation_id == "dns":
        _apply_dns(paths=paths, spec=spec, root=root, sudo_password=sudo_password, run=run)
        return
    if operation_id == "apt_sources":
        _apply_apt(spec=spec, root=root, sudo_password=sudo_password, run=run)
        return
    if operation_id == "qidiclient_static_gifs":
        _apply_gifs(paths=paths, spec=spec, root=root, sudo_password=sudo_password, run=run, preimage=preimage)
        return
    if operation_id == MOONRAKER_METADATA_OPERATION:
        _apply_moonraker_metadata_patch(spec=spec, root=root, sudo_password=sudo_password, run=run, preimage=preimage)
        return
    if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
        _apply_rockchip(
            spec=spec.rockchip_root_sync,
            root=root,
            sudo_password=sudo_password,
            run=run,
            classification=str(preimage.get("classification", "owned_reconcile")),
        )
        return
    if operation_id.startswith("service_"):
        service = _service_for_operation(operation_id, spec)
        _apply_service(service, root=root, sudo_password=sudo_password, run=run, preimage=preimage)
        return
    raise SystemOptimizationError(f"Unknown system operation: {operation_id}")



def _apply_dns(*, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path, sudo_password: str | None, run) -> None:
    _write_file(spec.dns.resolvconf_head, "", root=root, sudo_password=sudo_password, run=run)
    tail = "".join(f"nameserver {server}\n" for server in spec.dns.fallback_nameservers)
    _write_file(spec.dns.resolvconf_tail, tail, root=root, sudo_password=sudo_password, run=run)
    if _is_fake_root(root):
        target = _map_path(root, spec.dns.target_symlink)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(target, root=root)
        target.write_text(tail, encoding="utf-8")
        resolv = _map_path(root, spec.dns.resolv_conf)
        resolv.parent.mkdir(parents=True, exist_ok=True)
        if resolv.exists() and not resolv.is_symlink():
            _reject_symlink_path(resolv, root=root)
        resolv.unlink(missing_ok=True)
        resolv.symlink_to(spec.dns.target_symlink)
    else:
        run_sudo_or_raise(["resolvconf", "-u"], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
        run_sudo_or_raise(["ln", "-sfn", spec.dns.target_symlink, spec.dns.resolv_conf], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")



def _apply_apt(*, spec: SystemOptimizationsSpec, root: Path, sudo_password: str | None, run) -> None:
    _write_file(spec.apt_sources.file, spec.apt_sources.content, root=root, sudo_password=sudo_password, run=run)



def _apply_service(service: str, *, root: Path, sudo_password: str | None, run, preimage: dict[str, Any]) -> None:
    if _is_fake_root(root):
        _write_fake_service_state(service, root=root, enabled="disabled", active="inactive")
    else:
        result = run_sudo(["systemctl", "disable", "--now", service], run=run, password=sudo_password or "")
        if result.returncode != 0 and preimage.get("exists", True):
            raise SystemOptimizationError(messages.SYSTEM_OPTIMIZATIONS_FAILED)
        run_sudo_ignore_failure(["systemctl", "stop", service], run=run, password=sudo_password or "")
        if "." not in service:
            run_sudo_ignore_failure([f"/etc/init.d/{service}", "stop"], run=run, password=sudo_password or "")



def _apply_moonraker_metadata_patch(
    *,
    spec: SystemOptimizationsSpec,
    root: Path,
    sudo_password: str | None,
    run,
    preimage: dict[str, Any],
) -> None:
    path = spec.moonraker_metadata_3mf.file
    mapped = _map_path(root, path)
    _reject_symlink_path(mapped, root=root)
    patched = _patched_moonraker_metadata_text(mapped.read_text(encoding="utf-8"))
    _write_file_preserving_preimage(path, patched, preimage=preimage, root=root, sudo_password=sudo_password, run=run)
    _restart_service(spec.moonraker_metadata_3mf.restart_service, root=root, sudo_password=sudo_password, run=run)



def _patched_moonraker_metadata_text(text: str) -> str:
    if MOONRAKER_METADATA_PATCH_MARKER in text:
        return text
    helper = '''\n\ndef _3mf_plate_path(plate_index: int, suffix: str) -> str:\n    return f"Metadata/plate_{plate_index}.{suffix}"\n\n\ndef _3mf_slice_info_root(xml_data: str):\n    try:\n        return ET.fromstring(xml_data)\n    except Exception:\n        return None\n\n\ndef _3mf_selected_plate_index(xml_data: str) -> int:\n    root = _3mf_slice_info_root(xml_data)\n    if root is None:\n        return 1\n    try:\n        for item in root.findall(".//metadata"):\n            if item.get("key") == "index":\n                index = int(item.get("value", "1"))\n                return index if index > 0 else 1\n    except Exception:\n        pass\n    return 1\n'''
    text = text.replace('_3MF_PLATE_1_GCODE_PATH = "Metadata/plate_1.gcode"\n', '_3MF_PLATE_1_GCODE_PATH = "Metadata/plate_1.gcode"\n' + helper, 1)
    replacements = {
        '    plate_num = 1\n    try:\n': '    plate_num = 1\n    plate_index = 1\n    try:\n',
        '            tmp_plate_1_path = ""\n            tmp_plate_1_gcode_path = ""\n            with zipfile.ZipFile(_3mf_path) as zf:\n': '            tmp_plate_path = ""\n            tmp_plate_gcode_path = ""\n            with zipfile.ZipFile(_3mf_path) as zf:\n                names = zf.namelist()\n                if _3MF_SLICE_INFO_PATH in names:\n                    plate_index = _3mf_selected_plate_index(\n                        zf.read(_3MF_SLICE_INFO_PATH).decode("utf-8", "replace")\n                    )\n',
        '                if _3MF_THUMB_PATH_ALL in zf.namelist():': '                if _3MF_THUMB_PATH_ALL in names:',
        '                if _3MF_SLICE_INFO_PATH in zf.namelist():': '                if _3MF_SLICE_INFO_PATH in names:',
        '                if _3MF_PROJECT_SETTINGS_PATH in zf.namelist():': '                if _3MF_PROJECT_SETTINGS_PATH in names:',
        '                if _3MF_PLATE_1_PATH in zf.namelist():\n                    tmp_plate_1_path = zf.extract(\n                        _3MF_PLATE_1_PATH, path=tmp_dir_name\n                    )\n                if _3MF_PLATE_1_GCODE_PATH in zf.namelist():\n                    tmp_plate_1_gcode_path = zf.extract(\n                        _3MF_PLATE_1_GCODE_PATH, path=tmp_dir_name\n                    )\n': '                plate_json_path = _3mf_plate_path(plate_index, "json")\n                plate_gcode_path = _3mf_plate_path(plate_index, "gcode")\n                if plate_json_path in names:\n                    tmp_plate_path = zf.extract(plate_json_path, path=tmp_dir_name)\n                if plate_gcode_path in names:\n                    tmp_plate_gcode_path = zf.extract(plate_gcode_path, path=tmp_dir_name)\n',
        '                plate = ET.fromstring(xml_data).find("plate")\n                for metadata_plate in plate.findall(\'metadata\'):\n': '                xml_root = _3mf_slice_info_root(xml_data)\n                plate = xml_root.find("plate") if xml_root is not None else None\n                if plate is not None:\n                    for metadata_plate in plate.findall(\'metadata\'):\n',
        "                    if metadata_plate.get('key') == 'prediction':\n                        prediction = metadata_plate.get('value')\n                    elif metadata_plate.get('key') == 'weight':\n                        weight = metadata_plate.get('value')\n                    elif metadata_plate.get('key') == 'nozzle_diameters':\n                        nozzle_diameters = metadata_plate.get('value')\n                metadata[\"estimated_time\"] = int(prediction) \n                metadata[\"filament_weight_total\"] = float(weight)   \n                metadata[\"nozzle_diameter\"] = float(nozzle_diameters) \n                metadata[\"filament_total\"] = sum(\n                    float(filament.get(\"used_m\", 0.0))\n                    for filament in plate.findall(\"filament\")\n                ) * 1000\n                plate_num = len(ET.fromstring(xml_data).findall(\"plate\"))\n": "                        if metadata_plate.get('key') == 'prediction':\n                            prediction = metadata_plate.get('value')\n                        elif metadata_plate.get('key') == 'weight':\n                            weight = metadata_plate.get('value')\n                        elif metadata_plate.get('key') == 'nozzle_diameters':\n                            nozzle_diameters = metadata_plate.get('value')\n                    metadata[\"estimated_time\"] = int(prediction) \n                    metadata[\"filament_weight_total\"] = float(weight)   \n                    metadata[\"nozzle_diameter\"] = float(nozzle_diameters) \n                    metadata[\"filament_total\"] = sum(\n                        float(filament.get(\"used_m\", 0.0))\n                        for filament in plate.findall(\"filament\")\n                    ) * 1000\n                    plate_num = len(xml_root.findall(\"plate\"))\n",
        '            if os.path.exists(tmp_plate_1_path):\n                with open(tmp_plate_1_path, "r", encoding="utf-8") as file:\n                    plate_1_data = file.read()\n                plate_json = json.loads(plate_1_data)\n': '            if os.path.exists(tmp_plate_path):\n                with open(tmp_plate_path, "r", encoding="utf-8") as file:\n                    plate_data = file.read()\n                plate_json = json.loads(plate_data)\n',
        '            if os.path.exists(tmp_plate_1_gcode_path):\n                slicer, ident = get_slicer(tmp_plate_1_gcode_path)\n': '            if os.path.exists(tmp_plate_gcode_path):\n                slicer, ident = get_slicer(tmp_plate_gcode_path)\n',
        "        'relative_path': generate_thumb_path(dest_path, \"/home/qidi/printer_data/gcodes/\", 1)": "        'relative_path': generate_thumb_path(dest_path, \"/home/qidi/printer_data/gcodes/\", plate_index)",
    }
    for old, new in replacements.items():
        if old not in text:
            raise SystemOptimizationError("Moonraker metadata.py did not match expected QIDI 3MF extraction shape.")
        text = text.replace(old, new, 1)
    return text



def _capture_gifs_preimage(*, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path) -> dict[str, Any]:
    archive_path = paths.installer_root / spec.qidiclient_static_gifs.archive
    destination = _map_path(root, spec.qidiclient_static_gifs.destination)
    _reject_symlink_path(destination, root=root)
    if not destination.exists() or not destination.is_dir():
        raise SystemOptimizationError(f"qidiclient access directory is missing: {spec.qidiclient_static_gifs.destination}")
    backup_root = destination / f".gif-backup-{_now_for_path()}"
    backup_root.mkdir(parents=True, exist_ok=False)
    replaced: list[str] = []
    created: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _validate_archive_members(members)
        for member in members:
            if not member.isfile():
                continue
            relative = PurePosixPath(member.name).as_posix()
            source_existing = destination / relative
            _reject_symlink_path(source_existing, root=root)
            if source_existing.exists() and not source_existing.is_file():
                raise SystemOptimizationError(f"qidiclient static GIF target is not a file: {relative}")
            if source_existing.exists():
                stat = source_existing.stat()
                files[relative] = {
                    "mode": f"{stat.st_mode & 0o777:04o}",
                    "uid": stat.st_uid,
                    "gid": stat.st_gid,
                }
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_existing, backup)
                replaced.append(relative)
            else:
                created.append(relative)
    return {
        "backup_dir": str(backup_root),
        "destination": spec.qidiclient_static_gifs.destination,
        "replaced": replaced,
        "created": created,
        "files": files,
        "restart_service": spec.qidiclient_static_gifs.restart_service,
    }



def _apply_gifs(*, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path, sudo_password: str | None, run, preimage: dict[str, Any]) -> None:
    archive_path = paths.installer_root / spec.qidiclient_static_gifs.archive
    destination = _map_path(root, spec.qidiclient_static_gifs.destination)
    replaced = list(preimage.get("replaced", []))
    created = list(preimage.get("created", []))
    metadata = preimage.get("files", {}) if isinstance(preimage.get("files"), dict) else {}
    _reject_symlink_path(destination, root=root)
    if not destination.exists() or not destination.is_dir():
        raise SystemOptimizationError(f"qidiclient access directory is missing: {spec.qidiclient_static_gifs.destination}")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _validate_archive_members(members)
        if _is_fake_root(root):
            archive.extractall(destination, members=members)
            _apply_fake_gif_metadata(destination=destination, replaced=replaced, metadata=metadata)
        else:
            tmp = Path(tempfile.mkdtemp(prefix="tltg-static-gifs-"))
            try:
                archive.extractall(tmp, members=members)
                for relative in [*replaced, *created]:
                    source = tmp / relative
                    target = f"{spec.qidiclient_static_gifs.destination.rstrip('/')}/{relative}"
                    _reject_symlink_path(_map_path(root, target), root=root)
                    file_metadata = metadata.get(relative, {}) if isinstance(metadata.get(relative), dict) else {}
                    mode = str(file_metadata.get("mode", "0644"))
                    run_sudo_or_raise(["install", "-D", "-m", mode, str(source), target], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
                    if "uid" in file_metadata and "gid" in file_metadata:
                        run_sudo_or_raise(["chown", f"{file_metadata['uid']}:{file_metadata['gid']}", target], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    _restart_service(spec.qidiclient_static_gifs.restart_service, root=root, sudo_password=sudo_password, run=run)


def _apply_fake_gif_metadata(*, destination: Path, replaced: list[str], metadata: dict[str, Any]) -> None:
    for relative in replaced:
        file_metadata = metadata.get(relative, {}) if isinstance(metadata.get(relative), dict) else {}
        mode = file_metadata.get("mode")
        if mode is not None:
            (destination / relative).chmod(int(str(mode), 8))


def _restore_preimage_map(preimages: dict[str, Any], *, paths: RuntimePaths, root: Path, sudo_password: str | None, run) -> None:
    for operation_id in reversed(list(preimages.keys())):
        preimage = preimages[operation_id]
        if operation_id == "dns":
            for item in reversed(preimage.get("files", [])):
                _restore_file_preimage(item, paths=paths, root=root, sudo_password=sudo_password, run=run)
        elif operation_id == "apt_sources":
            _restore_file_preimage(preimage["file"], paths=paths, root=root, sudo_password=sudo_password, run=run)
        elif operation_id.startswith("service_"):
            _restore_service(preimage, root=root, sudo_password=sudo_password, run=run)
        elif operation_id == "qidiclient_static_gifs":
            _restore_gifs(preimage, root=root, sudo_password=sudo_password, run=run)
        elif operation_id == MOONRAKER_METADATA_OPERATION:
            _restore_file_preimage(preimage, paths=paths, root=root, sudo_password=sudo_password, run=run)
            _restart_service("moonraker.service", root=root, sudo_password=sudo_password, run=run)
        elif operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
            _restore_rockchip(
                preimage,
                spec=None,
                paths=paths,
                root=root,
                sudo_password=sudo_password,
                run=run,
                require_desired_live=False,
            )



def _restore_file_preimage(preimage: dict[str, Any], *, paths: RuntimePaths, root: Path, sudo_password: str | None, run) -> None:
    path = preimage["path"]
    mapped = _map_path(root, path)
    if not preimage.get("exists"):
        if _is_fake_root(root):
            mapped.unlink(missing_ok=True)
        else:
            _reject_parent_symlink_path(mapped, root=root)
            run_sudo_or_raise(["rm", "-f", path], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
        return
    if preimage.get("type") == "symlink":
        target = preimage["target"]
        if _is_fake_root(root):
            mapped.unlink(missing_ok=True)
            mapped.parent.mkdir(parents=True, exist_ok=True)
            mapped.symlink_to(target)
        else:
            _reject_parent_symlink_path(mapped, root=root)
            run_sudo_or_raise(["ln", "-sfn", target, path], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
        return
    backup_path = Path(preimage["backup_path"])
    if _is_fake_root(root):
        mapped.unlink(missing_ok=True)
        mapped.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, mapped)
    else:
        _reject_parent_symlink_path(mapped, root=root)
        tmp_path = f"{path}.tltg-restore-{_now_for_path()}.tmp"
        try:
            run_sudo_or_raise(["install", "-D", "-m", preimage.get("mode", "0644"), str(backup_path), tmp_path], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
            run_sudo_or_raise(["mv", "-f", tmp_path, path], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
            if "uid" in preimage and "gid" in preimage:
                run_sudo_or_raise(["chown", f"{preimage['uid']}:{preimage['gid']}", path], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
        finally:
            run_sudo_ignore_failure(["rm", "-f", tmp_path], run=run, password=sudo_password or "")


def _restore_service(preimage: dict[str, Any], *, root: Path, sudo_password: str | None, run) -> None:
    service = preimage["service"]
    if not preimage.get("exists", True):
        return
    enabled = preimage.get("enabled", "disabled")
    active = preimage.get("active", "inactive")
    if _is_fake_root(root):
        current = _service_state(service, root=root, run=run)
        if not current.get("exists", True):
            return
        _write_fake_service_state(service, root=root, enabled=enabled, active=active)
        return
    current = _service_state(service, root=root, run=run)
    if not current.get("exists", True):
        return
    if enabled == "enabled":
        run_sudo_or_raise(["systemctl", "enable", service], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    else:
        run_sudo_or_raise(["systemctl", "disable", service], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    if active == "active":
        run_sudo_or_raise(["systemctl", "start", service], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    else:
        run_sudo_or_raise(["systemctl", "stop", service], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")


def _restore_gifs(preimage: dict[str, Any], *, root: Path, sudo_password: str | None, run) -> None:
    backup_dir = Path(preimage["backup_dir"])
    destination = _map_path(root, preimage["destination"])
    metadata = preimage.get("files", {}) if isinstance(preimage.get("files"), dict) else {}
    for relative in preimage.get("replaced", []):
        backup = backup_dir / relative
        target = destination / relative
        if not backup.exists():
            continue
        file_metadata = metadata.get(relative, {}) if isinstance(metadata.get(relative), dict) else {}
        mode = str(file_metadata.get("mode", "0644"))
        if _is_fake_root(root):
            target.parent.mkdir(parents=True, exist_ok=True)
            _reject_symlink_path(target, root=root)
            shutil.copy2(backup, target)
            target.chmod(int(mode, 8))
        else:
            _reject_symlink_path(target, root=root)
            restore_target = f"{preimage['destination'].rstrip('/')}/{relative}"
            run_sudo_or_raise(["install", "-D", "-m", mode, str(backup), restore_target], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
            if "uid" in file_metadata and "gid" in file_metadata:
                run_sudo_or_raise(["chown", f"{file_metadata['uid']}:{file_metadata['gid']}", restore_target], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    for relative in preimage.get("created", []):
        target = destination / relative
        if _is_fake_root(root):
            _reject_symlink_path(target, root=root)
            target.unlink(missing_ok=True)
        else:
            _reject_parent_symlink_path(target, root=root)
            run_sudo_or_raise(["rm", "-f", f"{preimage['destination'].rstrip('/')}/{relative}"], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    if preimage.get("restart_service"):
        _restart_service(str(preimage["restart_service"]), root=root, sudo_password=sudo_password, run=run)


def _capture_file(path: str, *, paths: RuntimePaths, root: Path) -> dict[str, Any]:
    mapped = _map_path(root, path)
    preimage: dict[str, Any] = {"path": path, "exists": mapped.exists() or mapped.is_symlink()}
    if not preimage["exists"]:
        return preimage
    if mapped.is_symlink():
        preimage.update({"type": "symlink", "target": os.readlink(mapped)})
        return preimage
    backup_root = paths.printer_data_root / SYSTEM_BACKUP_DIR / _now_for_path() / "files"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / path.lstrip("/")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(mapped, backup_path)
    stat = mapped.stat()
    preimage.update({
        "type": "file",
        "backup_path": str(backup_path),
        "mode": f"{stat.st_mode & 0o777:04o}",
        "uid": stat.st_uid,
        "gid": stat.st_gid,
    })
    return preimage


def _write_file_preserving_preimage(
    path: str,
    content: str,
    *,
    preimage: dict[str, Any],
    root: Path,
    sudo_password: str | None,
    run,
) -> None:
    mapped = _map_path(root, path)
    mode = str(preimage.get("mode", "0644"))
    if _is_fake_root(root):
        mapped.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(mapped, root=root)
        mapped.write_text(content, encoding="utf-8")
        mapped.chmod(int(mode, 8))
        return
    _reject_parent_symlink_path(mapped, root=root)
    if mapped.is_symlink():
        raise SystemOptimizationError(f"Refusing to write through symlink: {path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        tmp = handle.name
    try:
        run_sudo_or_raise(["install", "-D", "-m", mode, tmp, path], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
        if "uid" in preimage and "gid" in preimage:
            run_sudo_or_raise(["chown", f"{preimage['uid']}:{preimage['gid']}", path], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _write_file(path: str, content: str, *, root: Path, sudo_password: str | None, run) -> None:
    mapped = _map_path(root, path)
    if _is_fake_root(root):
        mapped.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(mapped, root=root)
        mapped.write_text(content, encoding="utf-8")
        return
    _reject_parent_symlink_path(mapped, root=root)
    if mapped.is_symlink():
        raise SystemOptimizationError(f"Refusing to write through symlink: {path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        tmp = handle.name
    try:
        run_sudo_or_raise(["install", "-D", "-m", "0644", tmp, path], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _service_state(service: str, *, root: Path, run) -> dict[str, Any]:
    if _is_fake_root(root):
        state_path = _fake_service_state_path(root, service)
        if not state_path.exists():
            return {"exists": True, "enabled": "enabled", "active": "active"}
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.setdefault("service", service)
        return state
    enabled = run(["systemctl", "is-enabled", service], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    active = run(["systemctl", "is-active", service], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    enabled_raw = (enabled.stdout or "").strip()
    active_raw = (active.stdout or "").strip()
    missing_markers = ("not-found", "could not be found", "failed to get unit file state", "no such")
    combined = f"{enabled_raw}\n{active_raw}".lower()
    exists = not any(marker in combined for marker in missing_markers)
    return {
        "exists": exists,
        "enabled": _systemctl_state_text(enabled_raw, SYSTEMD_ENABLED_STATES),
        "active": _systemctl_state_text(active_raw, SYSTEMD_ACTIVE_STATES),
    }


def _systemctl_state_text(output: str, known_states: frozenset[str]) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if lowered in known_states:
            return lowered
    return lines[-1].lower() if lines else "unknown"


def _service_state_is_disabled(state: dict[str, Any]) -> bool:
    return (
        str(state.get("enabled", "")).lower() in SERVICE_DISABLED_ENABLED_STATES
        and str(state.get("active", "")).lower() in SERVICE_DISABLED_ACTIVE_STATES
    )


def _write_fake_service_state(service: str, *, root: Path, enabled: str, active: str) -> None:
    path = _fake_service_state_path(root, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exists": True, "service": service, "enabled": enabled, "active": active}, sort_keys=True), encoding="utf-8")


def _fake_service_state_path(root: Path, service: str) -> Path:
    return root / "systemd" / f"{service}.json"


def _restart_service(service: str, *, root: Path, sudo_password: str | None, run) -> None:
    if _is_fake_root(root):
        return
    run_sudo_ignore_failure(["systemctl", "restart", service], run=run, password=sudo_password or "")


def _reject_operation_unsafe_for_compare(operation_id: str, *, spec: SystemOptimizationsSpec, root: Path) -> None:
    if operation_id == "dns":
        resolv = _map_path(root, spec.dns.resolv_conf)
        head = _map_path(root, spec.dns.resolvconf_head)
        tail = _map_path(root, spec.dns.resolvconf_tail)
        for path in (resolv, head, tail):
            _reject_parent_symlink_path(path, root=root)
        for path in (head, tail):
            if path.exists() and path.is_symlink():
                raise SystemOptimizationError(f"Refusing to read through symlink: {path}")
        return
    if operation_id == "apt_sources":
        _reject_symlink_path(_map_path(root, spec.apt_sources.file), root=root)
        return
    if operation_id == "qidiclient_static_gifs":
        destination = _map_path(root, spec.qidiclient_static_gifs.destination)
        _reject_symlink_path(destination, root=root)
        archive_path = Path(spec.qidiclient_static_gifs.archive)
        if archive_path.exists():
            try:
                with tarfile.open(archive_path, "r:gz") as archive:
                    for member in archive.getmembers():
                        if member.isfile():
                            _reject_symlink_path(destination / member.name, root=root)
            except tarfile.TarError as exc:
                raise SystemOptimizationError("qidiclient static GIF archive could not be read.") from exc
        return
    if operation_id == MOONRAKER_METADATA_OPERATION:
        _reject_symlink_path(_map_path(root, spec.moonraker_metadata_3mf.file), root=root)
        return


def _operation_needs_apply(operation_id: str, *, spec: SystemOptimizationsSpec, root: Path, run) -> bool:
    _reject_operation_unsafe_for_compare(operation_id, spec=spec, root=root)
    if operation_id == "dns":
        resolv = _map_path(root, spec.dns.resolv_conf)
        head = _map_path(root, spec.dns.resolvconf_head)
        tail = _map_path(root, spec.dns.resolvconf_tail)
        desired_tail = "".join(f"nameserver {server}\n" for server in spec.dns.fallback_nameservers)
        return not (
            resolv.is_symlink()
            and os.readlink(resolv) == spec.dns.target_symlink
            and (not head.exists() or head.read_text(encoding="utf-8") == "")
            and tail.exists()
            and tail.read_text(encoding="utf-8") == desired_tail
        )
    if operation_id == "apt_sources":
        path = _map_path(root, spec.apt_sources.file)
        return not path.exists() or path.read_text(encoding="utf-8") != spec.apt_sources.content
    if operation_id == "qidiclient_static_gifs":
        return not _gifs_match_archive(
            _map_path(root, spec.qidiclient_static_gifs.destination),
            Path(spec.qidiclient_static_gifs.archive),
        )
    if operation_id == MOONRAKER_METADATA_OPERATION:
        path = _map_path(root, spec.moonraker_metadata_3mf.file)
        return path.exists() and MOONRAKER_METADATA_PATCH_MARKER not in path.read_text(encoding="utf-8")
    if operation_id.startswith("service_"):
        service = _service_for_operation(operation_id, spec)
        state = _service_state(service, root=root, run=run)
        if not state.get("exists", True):
            return False
        return not _service_state_is_disabled(state)
    return True



def _classify_rockchip_operation(
    *,
    paths: RuntimePaths,
    spec: SystemRockchipRootSyncSpec,
    root: Path,
    prior_owned: bool,
    run,
) -> str:
    dropin = _map_path(root, spec.dropin)
    unit_file = _map_path(root, spec.unit_file)
    script = _map_path(root, spec.script)
    if _path_has_symlink_component(dropin, root=root):
        return "unsafe_path"
    if dropin.exists() or dropin.is_symlink():
        if dropin.is_symlink() or not dropin.is_file():
            return "conflicting_dropin"
        try:
            content = dropin.read_text(encoding="utf-8")
        except OSError:
            return "conflicting_dropin"
        if content != spec.dropin_content:
            return "conflicting_dropin"
        if not _exec_start_matches(_effective_exec_start(spec=spec, root=root, run=run), spec.desired_exec_start):
            return "conflicting_dropin"
        if _root_mount_has_sync(spec=spec, root=root, run=run):
            return "owned_mount_drift" if prior_owned else "desired_unowned_mount_drift"
        return "already_current_owned" if prior_owned else "already_current_unowned"
    if (
        unit_file.is_symlink()
        or script.is_symlink()
        or not unit_file.is_file()
        or not script.is_file()
    ):
        return "unsupported"
    try:
        unit_text = unit_file.read_text(encoding="utf-8")
        script_text = script.read_text(encoding="utf-8")
    except OSError:
        return "unsupported"
    if any(_active_shell_marker_position(unit_text, marker) is None for marker in spec.defective_unit_markers):
        return "unsupported"
    if any(_active_shell_marker_position(script_text, marker) is None for marker in spec.defective_script_markers):
        return "unsupported"
    positions = [_active_shell_marker_position(script_text, marker) for marker in spec.ordered_script_markers]
    if (
        any(position is None for position in positions)
        or positions != sorted(positions)
        or len(set(positions)) != len(positions)
    ):
        return "unsupported"
    if not _exec_start_matches(_effective_exec_start(spec=spec, root=root, run=run), spec.vendor_exec_start):
        return "unsupported"
    return "owned_reconcile" if prior_owned else "defective_stock"


def _active_shell_marker_position(text: str, marker: str) -> int | None:
    if marker.startswith("#!"):
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        return 0 if first == marker else None
    exact_markers = {
        'CHIPNAME="rk3208"',
        "mount -o remount,sync /",
        "install_packages",
        "touch /usr/local/first_boot_flag",
        "ExecStart=/etc/init.d/rockchip.sh",
    }
    matches: list[int] = []
    for index, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        executable = line.split(" #", 1)[0].rstrip()
        if marker in exact_markers:
            if executable == marker or (
                marker == "install_packages" and executable.startswith("install_packages ")
            ):
                matches.append(index)
        elif marker in executable:
            matches.append(index)
    return matches[-1] if matches else None


def _capture_rockchip_preimage(
    *,
    paths: RuntimePaths,
    spec: SystemRockchipRootSyncSpec,
    root: Path,
    classification: str,
    run,
) -> dict[str, Any]:
    marker_path = paths.host_reboot_marker_path
    marker: dict[str, Any] = {"exists": marker_path.exists()}
    if marker["exists"]:
        read_host_reboot_marker(paths)
        if marker_path.is_symlink() or not marker_path.is_file():
            raise SystemOptimizationError("Host reboot marker is not a regular file.")
        marker.update(
            {
                "content": marker_path.read_text(encoding="utf-8"),
                "mode": f"{marker_path.stat().st_mode & 0o777:04o}",
            }
        )
    return {
        "classification": classification,
        "dropin": _capture_file(spec.dropin, paths=paths, root=root),
        "desired_dropin": spec.dropin_content,
        "unit": _rockchip_unit_state(spec=spec, root=root, run=run),
        "mount_options": list(_root_mount_options(spec=spec, root=root, run=run)),
        "marker": marker,
    }


def _apply_rockchip(
    *,
    spec: SystemRockchipRootSyncSpec,
    root: Path,
    sudo_password: str | None,
    run,
    classification: str,
) -> None:
    if classification != "desired_unowned_mount_drift":
        _install_rockchip_dropin(
            spec=spec,
            root=root,
            sudo_password=sudo_password,
            run=run,
        )
    if _is_fake_root(root):
        _write_fake_service_state(spec.unit, root=root, enabled="enabled", active="inactive")
        state_path = _fake_service_state_path(root, spec.unit)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({"result": "success", "exec_main_status": 0, "sub": "dead"})
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        _write_fake_root_mount_options(spec=spec, root=root, options=_async_mount_options(_root_mount_options(spec=spec, root=root, run=run)))
        return
    run_sudo_or_raise(["systemctl", "daemon-reload"], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
    run_sudo_ignore_failure(["systemctl", "reset-failed", spec.unit], run=run, password=sudo_password or "")
    run_sudo_or_raise(["systemctl", "start", spec.unit], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
    run_sudo_or_raise(["mount", "-o", "remount,rw,async", spec.mount_target], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")


def _install_rockchip_dropin(
    *,
    spec: SystemRockchipRootSyncSpec,
    root: Path,
    sudo_password: str | None,
    run,
) -> None:
    mapped = _map_path(root, spec.dropin)
    if _is_fake_root(root):
        mapped.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(mapped, root=root)
        atomic_write_text(mapped, spec.dropin_content, mode=0o644, force_mode=True)
        return
    _reject_parent_symlink_path(mapped, root=root)
    if mapped.is_symlink():
        raise SystemOptimizationError(f"Refusing to replace symlink: {spec.dropin}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(spec.dropin_content)
        source = handle.name
    target_tmp = f"{spec.dropin}.tltg-{_now_for_path()}.tmp"
    try:
        run_sudo_or_raise(["install", "-D", "-m", "0644", source, target_tmp], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
        run_sudo_or_raise(["mv", "-f", target_tmp, spec.dropin], messages.SYSTEM_OPTIMIZATIONS_FAILED, run=run, password=sudo_password or "")
    finally:
        Path(source).unlink(missing_ok=True)
        run_sudo_ignore_failure(["rm", "-f", target_tmp], run=run, password=sudo_password or "")


def verify_rockchip_postflight(
    *,
    spec: SystemRockchipRootSyncSpec,
    environ: dict[str, str],
    run=subprocess.run,
) -> dict[str, Any]:
    return _postflight_rockchip(spec=spec, root=_system_root(environ), run=run)


def _postflight_rockchip(*, spec: SystemRockchipRootSyncSpec, root: Path, run) -> dict[str, Any]:
    dropin = _map_path(root, spec.dropin)
    if dropin.is_symlink() or not dropin.is_file() or dropin.read_text(encoding="utf-8") != spec.dropin_content:
        raise SystemOptimizationError("Rockchip systemd drop-in did not reach desired state.")
    effective = _effective_exec_start(spec=spec, root=root, run=run)
    if not _exec_start_matches(effective, spec.desired_exec_start):
        raise SystemOptimizationError("Rockchip effective ExecStart did not reach the no-op command.")
    state = _rockchip_unit_state(spec=spec, root=root, run=run)
    if state.get("result") != "success" or state.get("exec_main_status") != 0:
        raise SystemOptimizationError("Rockchip service did not complete successfully.")
    if state.get("active") not in {"active", "inactive"}:
        raise SystemOptimizationError("Rockchip service active state is invalid after completion.")
    options = _root_mount_options(spec=spec, root=root, run=run)
    if "sync" in options:
        raise SystemOptimizationError("Root filesystem still has the sync mount option.")
    return {
        "dropin": spec.dropin,
        "exec_start": spec.desired_exec_start,
        "service": state,
        "mount_options": list(options),
    }


def _restore_rockchip(
    preimage: dict[str, Any],
    *,
    spec: SystemRockchipRootSyncSpec | None,
    paths: RuntimePaths,
    root: Path,
    sudo_password: str | None,
    run,
    require_desired_live: bool,
) -> bool:
    if spec is not None:
        _validate_rockchip_restore_preimage(preimage, spec=spec, paths=paths)
    dropin_preimage = preimage.get("dropin", {})
    dropin_path = str(dropin_preimage.get("path", spec.dropin if spec is not None else ""))
    desired = str(preimage.get("desired_dropin", spec.dropin_content if spec is not None else ""))
    if not dropin_path:
        raise SystemOptimizationError("Rockchip restore preimage is missing the drop-in path.")
    mapped = _map_path(root, dropin_path)
    if require_desired_live and (
        mapped.is_symlink() or not mapped.is_file() or mapped.read_text(encoding="utf-8") != desired
    ):
        return False
    _restore_file_preimage(dropin_preimage, paths=paths, root=root, sudo_password=sudo_password, run=run)
    mount_options = tuple(str(item) for item in preimage.get("mount_options", []))
    if _is_fake_root(root):
        rockchip_spec = spec or _rockchip_spec_from_preimage(preimage)
        _write_fake_root_mount_options(spec=rockchip_spec, root=root, options=mount_options)
    else:
        run_sudo_or_raise(["systemctl", "daemon-reload"], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
        mode = "sync" if "sync" in mount_options else "async"
        mount_target = spec.mount_target if spec is not None else "/"
        run_sudo_or_raise(["mount", "-o", f"remount,rw,{mode}", mount_target], messages.SYSTEM_RESTORE_FAILED, run=run, password=sudo_password or "")
    marker = preimage.get("marker", {})
    if marker.get("exists"):
        atomic_write_text(
            paths.host_reboot_marker_path,
            str(marker.get("content", "")),
            mode=int(str(marker.get("mode", "0600")), 8),
            force_mode=True,
        )
    else:
        clear_host_reboot_marker(paths)
    return True


def _rockchip_spec_from_preimage(preimage: dict[str, Any]) -> SystemRockchipRootSyncSpec:
    dropin = str(preimage.get("dropin", {}).get("path", "/etc/systemd/system/rockchip.service.d/override.conf"))
    return SystemRockchipRootSyncSpec(
        id=ROCKCHIP_ROOT_SYNC_OPERATION,
        unit="rockchip.service",
        unit_file="/lib/systemd/system/rockchip.service",
        script="/etc/init.d/rockchip.sh",
        dropin=dropin,
        dropin_content=str(preimage.get("desired_dropin", "")),
        mount_target="/",
        defective_unit_markers=(),
        defective_script_markers=(),
        ordered_script_markers=(),
        vendor_exec_start="/etc/init.d/rockchip.sh",
        desired_exec_start="/bin/true",
    )


def _effective_exec_start(*, spec: SystemRockchipRootSyncSpec, root: Path, run) -> str:
    if _is_fake_root(root):
        dropin = _map_path(root, spec.dropin)
        source = dropin if dropin.is_file() else _map_path(root, spec.unit_file)
        try:
            values = [
                line.split("=", 1)[1].strip()
                for line in source.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("ExecStart=")
            ]
        except OSError:
            return ""
        non_empty = [value for value in values if value]
        return non_empty[-1] if non_empty else ""
    result = run(
        ["systemctl", "show", spec.unit, "--property=ExecStart", "--value"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _exec_start_matches(value: str, expected: str) -> bool:
    stripped = value.strip()
    if stripped == expected or stripped.startswith(f"{expected} "):
        return True
    return f"path={expected} " in stripped or f"argv[]={expected} " in stripped


def _rockchip_unit_state(*, spec: SystemRockchipRootSyncSpec, root: Path, run) -> dict[str, Any]:
    if _is_fake_root(root):
        state_path = _fake_service_state_path(root, spec.unit)
        if not state_path.exists():
            return {"exists": True, "active": "failed", "sub": "failed", "result": "exit-code", "exec_main_status": 100}
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return {
            "exists": bool(state.get("exists", True)),
            "active": str(state.get("active", "unknown")),
            "sub": str(state.get("sub", "unknown")),
            "result": str(state.get("result", "unknown")),
            "exec_main_status": int(state.get("exec_main_status", -1)),
        }
    properties: dict[str, str] = {}
    for prop in ("ActiveState", "SubState", "Result", "ExecMainStatus"):
        result = run(
            ["systemctl", "show", spec.unit, f"--property={prop}", "--value"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise SystemOptimizationError(f"Could not inspect {spec.unit} property {prop}.")
        properties[prop] = (result.stdout or "").strip()
    try:
        status = int(properties["ExecMainStatus"])
    except ValueError as exc:
        raise SystemOptimizationError("Rockchip service exit status is invalid.") from exc
    return {
        "exists": True,
        "active": properties["ActiveState"],
        "sub": properties["SubState"],
        "result": properties["Result"],
        "exec_main_status": status,
    }


def _root_mount_options(*, spec: SystemRockchipRootSyncSpec, root: Path, run) -> tuple[str, ...]:
    if _is_fake_root(root):
        path = _fake_root_mount_state_path(root)
        if not path.exists():
            return ("rw", "relatime", "sync")
        value = path.read_text(encoding="utf-8").strip()
    else:
        result = run(
            ["findmnt", "--noheadings", "--output", "OPTIONS", "--target", spec.mount_target],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise SystemOptimizationError("Could not inspect root mount options.")
        value = (result.stdout or "").strip()
    options = tuple(item.strip() for item in value.split(",") if item.strip())
    if not options:
        raise SystemOptimizationError("Root mount options are empty.")
    return options


def _root_mount_has_sync(*, spec: SystemRockchipRootSyncSpec, root: Path, run) -> bool:
    return "sync" in _root_mount_options(spec=spec, root=root, run=run)


def _async_mount_options(options: tuple[str, ...]) -> tuple[str, ...]:
    result = [item for item in options if item != "sync"]
    if "rw" not in result:
        result.insert(0, "rw")
    return tuple(result)


def _write_fake_root_mount_options(*, spec: SystemRockchipRootSyncSpec, root: Path, options: tuple[str, ...]) -> None:
    path = _fake_root_mount_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(options) + "\n", encoding="utf-8")


def _fake_root_mount_state_path(root: Path) -> Path:
    return root / "mounts" / "root.options"


def _path_has_symlink_component(path: Path, *, root: Path) -> bool:
    try:
        _reject_parent_symlink_path(path, root=root)
    except SystemOptimizationError:
        return True
    return path.is_symlink()


def _selected_operation_ids(spec: SystemOptimizationsSpec, policy: dict[str, Any]) -> tuple[str, ...]:
    ids = [MOONRAKER_METADATA_OPERATION]
    if policy.get("system_optimizations") != "enabled":
        return tuple(ids)
    ids.extend(["dns", "apt_sources", "qidiclient_static_gifs"])
    ids.extend(f"service_{service}" for service in spec.services.disable)
    if policy.get("ai_detection") == "disable":
        ids.extend(f"service_{item.service}" for item in spec.services.optional_disable)
    ids.append(ROCKCHIP_ROOT_SYNC_OPERATION)
    return tuple(ids)


def _policy_skipped_optional_service_operation_ids(
    spec: SystemOptimizationsSpec,
    policy: dict[str, Any],
    selected_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if policy.get("ai_detection") == "disable":
        return ()
    selected = set(selected_ids)
    return tuple(
        operation_id
        for operation_id in (f"service_{item.service}" for item in spec.services.optional_disable)
        if operation_id not in selected
    )


def _service_for_operation(operation_id: str, spec: SystemOptimizationsSpec) -> str:
    service = operation_id.removeprefix("service_")
    services = set(spec.services.disable)
    services.update(item.service for item in spec.services.optional_disable)
    if service not in services:
        raise SystemOptimizationError(f"Unknown service operation: {operation_id}")
    return service


def _desired(operation_id: str, spec: SystemOptimizationsSpec) -> dict[str, Any]:
    if operation_id == "dns":
        return {"resolv_conf": spec.dns.target_symlink, "fallback_nameservers": list(spec.dns.fallback_nameservers)}
    if operation_id == "apt_sources":
        return {"sha256": hashlib.sha256(spec.apt_sources.content.encode("utf-8")).hexdigest()}
    if operation_id == "qidiclient_static_gifs":
        return {"archive_sha256": spec.qidiclient_static_gifs.sha256}
    if operation_id == MOONRAKER_METADATA_OPERATION:
        return {"path": spec.moonraker_metadata_3mf.file, "restart_service": spec.moonraker_metadata_3mf.restart_service}
    if operation_id == ROCKCHIP_ROOT_SYNC_OPERATION:
        rockchip = spec.rockchip_root_sync
        return {
            "dropin": rockchip.dropin,
            "dropin_sha256": hashlib.sha256(rockchip.dropin_content.encode("utf-8")).hexdigest(),
            "exec_start": rockchip.desired_exec_start,
            "mount_target": rockchip.mount_target,
            "sync": False,
        }
    if operation_id.startswith("service_"):
        return {"enabled": "disabled", "active": "inactive"}
    return {}


def _gifs_match_archive(destination: Path, archive_relative_path: Path) -> bool:
    archive_path = archive_relative_path
    if not archive_path.is_absolute():
        archive_path = Path(__file__).resolve().parents[1] / archive_relative_path
    try:
        for relative, expected in _archive_file_hashes(archive_path).items():
            target = destination / relative
            if not target.exists() or not target.is_file():
                return False
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                return False
    except (OSError, tarfile.TarError):
        return False
    return True


def _postflight_gifs(*, paths: RuntimePaths, spec: SystemOptimizationsSpec, root: Path) -> dict[str, Any]:
    archive_hashes = _archive_file_hashes(paths.installer_root / spec.qidiclient_static_gifs.archive)
    destination = _map_path(root, spec.qidiclient_static_gifs.destination)
    for relative, expected in archive_hashes.items():
        target = destination / relative
        if not target.exists() or not target.is_file():
            raise SystemOptimizationError(f"qidiclient static GIF was not installed: {relative}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemOptimizationError(f"qidiclient static GIF hash mismatch: {relative}")
    return {"installed_sha256": archive_hashes}


def _archive_file_hashes(archive_path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _validate_archive_members(members)
        for member in members:
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise SystemOptimizationError("qidiclient static GIF archive member could not be read.")
            hashes[PurePosixPath(member.name).as_posix()] = hashlib.sha256(extracted.read()).hexdigest()
    return hashes



def _validate_qidiclient_archive(path: Path, sha256: str) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SystemOptimizationError("qidiclient static GIF archive is missing.") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemOptimizationError("qidiclient static GIF archive checksum mismatch.")
    try:
        with tarfile.open(path, "r:gz") as archive:
            _validate_archive_members(archive.getmembers())
    except tarfile.TarError as exc:
        raise SystemOptimizationError("qidiclient static GIF archive could not be read.") from exc


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise SystemOptimizationError("qidiclient static GIF archive contains an unsafe path.")
        if path.parts[0] not in QIDICLIENT_ARCHIVE_ROOTS:
            raise SystemOptimizationError("qidiclient static GIF archive contains an unexpected root.")
        if not (member.isfile() or member.isdir()):
            raise SystemOptimizationError("qidiclient static GIF archive contains an unsafe entry type.")


def _noninteractive_policy(
    prior_ledger: dict[str, Any] | None,
    cli_options: SystemOptimizationCliOptions,
    *,
    auto_update_child: bool,
    interactive: bool = False,
) -> dict[str, str] | None:
    if cli_options.skip_system_optimizations:
        return {"system_optimizations": "disabled", "ai_detection": "unset"}
    prior_policy = prior_ledger.get("policy") if isinstance(prior_ledger, dict) else None
    use_prior_policy = (
        isinstance(prior_policy, dict)
        and (auto_update_child or not interactive)
        and not (cli_options.disable_ai_detection or cli_options.keep_ai_detection)
    )
    if use_prior_policy:
        return {
            "system_optimizations": str(prior_policy.get("system_optimizations", "disabled")),
            "ai_detection": str(prior_policy.get("ai_detection", "unset")),
        }
    if auto_update_child:
        return None
    if cli_options.disable_ai_detection:
        return {"system_optimizations": "enabled", "ai_detection": "disable"}
    if cli_options.keep_ai_detection:
        return {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}
    return None


def _ledger_with_policy(ledger: dict[str, Any] | None, policy: dict[str, str]) -> dict[str, Any]:
    existing = dict(ledger or {})
    existing["policy"] = dict(policy)
    existing.setdefault("actions", [])
    existing.setdefault("restore_preimages", {})
    return existing


def _replace_system_ledger(state: InstalledState, ledger: dict[str, Any]) -> InstalledState:
    return InstalledState(
        schema_version=state.schema_version,
        package_id=state.package_id,
        package_version=state.package_version,
        runtime_firmware=state.runtime_firmware,
        backup_label=state.backup_label,
        installed_at=state.installed_at,
        managed_tree=state.managed_tree,
        patch_ledger=state.patch_ledger,
        source_patches=state.source_patches,
        external_files=state.external_files,
        system_ledger=ledger,
    )


def _has_restore_preimages(ledger: dict[str, Any] | None) -> bool:
    return isinstance(ledger, dict) and isinstance(ledger.get("restore_preimages"), dict) and bool(ledger["restore_preimages"])


def _system_root(environ: dict[str, str]) -> Path:
    return Path(environ.get(SYSTEM_ROOT_ENV, "/"))


def _is_fake_root(root: Path) -> bool:
    return root != Path("/")


def _system_root_allowed(*, paths: RuntimePaths, environ: dict[str, str]) -> bool:
    return SYSTEM_ROOT_ENV in environ or paths.printer_data_root == DEFAULT_PRINTER_DATA_ROOT


def _map_path(root: Path, absolute: str) -> Path:
    if not _is_fake_root(root):
        return Path(absolute)
    return root / absolute.lstrip("/")



def _reject_symlink_path(path: Path, *, root: Path) -> None:
    _reject_parent_symlink_path(path, root=root)
    if path.exists() and path.is_symlink():
        raise SystemOptimizationError(f"Refusing to write through symlink: {path}")



def _reject_parent_symlink_path(path: Path, *, root: Path) -> None:
    if not _is_fake_root(root):
        current = Path("/")
        for part in path.parts[1:-1]:
            current = current / part
            if current.is_symlink():
                raise SystemOptimizationError(f"Refusing to write through symlink: {path}")
        return
    root_resolved = root.resolve()
    try:
        path.parent.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise SystemOptimizationError(f"Path escapes fake system root: {path}") from exc
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise SystemOptimizationError(f"Refusing to write through symlink: {path}")


def _validate_rockchip_restore_preimage(
    preimage: dict[str, Any],
    *,
    spec: SystemRockchipRootSyncSpec,
    paths: RuntimePaths,
) -> None:
    required = {"classification", "dropin", "desired_dropin", "unit", "mount_options", "marker"}
    if set(preimage) != required or preimage.get("desired_dropin") != spec.dropin_content:
        raise SystemOptimizationRecoveryError("Rockchip restore preimage has an invalid schema.")
    if preimage.get("classification") not in {
        "defective_stock",
        "owned_reconcile",
        "owned_mount_drift",
        "desired_unowned_mount_drift",
    }:
        raise SystemOptimizationRecoveryError("Rockchip restore classification is invalid.")
    dropin = preimage.get("dropin")
    if not isinstance(dropin, dict) or dropin.get("path") != spec.dropin or not isinstance(dropin.get("exists"), bool):
        raise SystemOptimizationRecoveryError("Rockchip restore drop-in preimage is invalid.")
    if dropin["exists"]:
        if dropin.get("type") != "file":
            raise SystemOptimizationRecoveryError("Rockchip restore drop-in type is invalid.")
        backup_value = dropin.get("backup_path")
        if not isinstance(backup_value, str):
            raise SystemOptimizationRecoveryError("Rockchip restore backup path is invalid.")
        backup = Path(backup_value)
        backup_root = (paths.printer_data_root / SYSTEM_BACKUP_DIR).resolve()
        try:
            backup.resolve().relative_to(backup_root)
        except (OSError, ValueError) as exc:
            raise SystemOptimizationRecoveryError("Rockchip restore backup escapes the managed backup root.") from exc
        if backup.is_symlink() or not backup.is_file():
            raise SystemOptimizationRecoveryError("Rockchip restore backup is missing or unsafe.")
        try:
            if backup.read_text(encoding="utf-8") != spec.dropin_content:
                raise SystemOptimizationRecoveryError("Rockchip restore backup content is not recognized.")
        except OSError as exc:
            raise SystemOptimizationRecoveryError("Rockchip restore backup could not be read.") from exc
        mode = dropin.get("mode")
        try:
            parsed_mode = int(str(mode), 8)
        except (TypeError, ValueError) as exc:
            raise SystemOptimizationRecoveryError("Rockchip restore mode is invalid.") from exc
        if not 0 <= parsed_mode <= 0o777:
            raise SystemOptimizationRecoveryError("Rockchip restore mode is invalid.")
        if any(not isinstance(dropin.get(key), int) or isinstance(dropin.get(key), bool) or dropin[key] < 0 for key in ("uid", "gid")):
            raise SystemOptimizationRecoveryError("Rockchip restore ownership is invalid.")
    elif set(dropin) != {"path", "exists"}:
        raise SystemOptimizationRecoveryError("Absent Rockchip drop-in preimage contains unexpected fields.")
    options = preimage.get("mount_options")
    if not isinstance(options, list) or not options or any(
        not isinstance(option, str)
        or not option
        or "," in option
        or any(char.isspace() for char in option)
        for option in options
    ):
        raise SystemOptimizationRecoveryError("Rockchip restore mount options are invalid.")
    marker = preimage.get("marker")
    if not isinstance(marker, dict) or not isinstance(marker.get("exists"), bool):
        raise SystemOptimizationRecoveryError("Rockchip restore marker preimage is invalid.")
    if marker["exists"]:
        if set(marker) != {"exists", "content", "mode"} or not isinstance(marker.get("content"), str):
            raise SystemOptimizationRecoveryError("Rockchip restore marker content is invalid.")
        try:
            marker_mode = int(str(marker.get("mode")), 8)
        except (TypeError, ValueError) as exc:
            raise SystemOptimizationRecoveryError("Rockchip restore marker mode is invalid.") from exc
        if marker_mode != 0o600:
            raise SystemOptimizationRecoveryError("Rockchip restore marker mode is invalid.")
    elif set(marker) != {"exists"}:
        raise SystemOptimizationRecoveryError("Absent Rockchip marker preimage contains unexpected fields.")


def _recover_pending_rockchip_journal(
    *,
    paths: RuntimePaths,
    root: Path,
    sudo_password: str | None,
    run,
    ledger: dict[str, Any],
    spec: SystemRockchipRootSyncSpec,
) -> None:
    path = _journal_path(paths)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemOptimizationRecoveryError("System optimization journal is invalid.") from exc
    if not isinstance(payload, dict) or payload.get("operation") != ROCKCHIP_ROOT_SYNC_OPERATION:
        return
    preimage = payload.get("preimage")
    transaction_id = payload.get("transaction_id")
    if (
        payload.get("schema_version") != 1
        or not isinstance(preimage, dict)
        or not isinstance(transaction_id, str)
        or not transaction_id
    ):
        raise SystemOptimizationRecoveryError("Rockchip system optimization journal is incomplete.")
    _validate_rockchip_restore_preimage(preimage, spec=spec, paths=paths)
    actions = ledger.get("actions") if isinstance(ledger, dict) else None
    if isinstance(actions, list) and any(
        isinstance(action, dict)
        and action.get("id") == ROCKCHIP_ROOT_SYNC_OPERATION
        and action.get("transaction_id") == transaction_id
        and action.get("status") in {"applied", "reconciled_unowned"}
        for action in actions
    ):
        atomic_delete(path)
        return
    try:
        _restore_rockchip(
            preimage,
            spec=spec,
            paths=paths,
            root=root,
            sudo_password=sudo_password,
            run=run,
            require_desired_live=False,
        )
    except Exception as exc:
        raise SystemOptimizationRecoveryError(
            f"Pending Rockchip rollback failed: {getattr(exc, 'message', str(exc))}"
        ) from exc
    atomic_delete(path)


def _clear_committed_rockchip_journal(paths: RuntimePaths, ledger: dict[str, Any]) -> None:
    path = _journal_path(paths)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemOptimizationRecoveryError("System optimization journal is invalid after state persistence.") from exc
    if isinstance(payload, dict) and payload.get("operation") == ROCKCHIP_ROOT_SYNC_OPERATION:
        transaction_id = payload.get("transaction_id")
        actions = ledger.get("actions") if isinstance(ledger, dict) else None
        if not isinstance(transaction_id, str) or not isinstance(actions, list) or not any(
            isinstance(action, dict)
            and action.get("id") == ROCKCHIP_ROOT_SYNC_OPERATION
            and action.get("transaction_id") == transaction_id
            and action.get("status") in {"applied", "reconciled_unowned"}
            for action in actions
        ):
            raise SystemOptimizationRecoveryError("Rockchip journal has no matching committed ledger action.")
        atomic_delete(path)


def _journal_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / SYSTEM_JOURNAL


def _sudo_password(*, reporter, input_stream, environ: dict[str, str], run) -> str:
    return authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
