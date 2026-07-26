from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import messages
from .errors import InstallerError
from .interaction import confirm_yes, prompt_yes
from .models import (
    InstalledState,
    Manifest,
    RuntimePaths,
    SystemOptimizationCliOptions,
    SystemOptimizationsSpec,
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


class SystemOptimizationError(InstallerError):
    pass


class SystemOptimizationApplyError(SystemOptimizationError):
    def __init__(self, message: str, ledger: dict[str, Any]):
        super().__init__(message)
        self.ledger = ledger


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
            source="auto_update_reconcile" if auto_update_child else "yes_install" if input_stream is None else "interactive_install",
            run=run,
        )
    except SystemOptimizationApplyError as exc:
        ledger = exc.ledger
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {exc.message}")
    except InstallerError as exc:
        reporter.line(f"{messages.SYSTEM_OPTIMIZATIONS_FAILED} {getattr(exc, 'message', str(exc))}")
    updated = _replace_system_ledger(state, ledger)
    write_installed_state(state_path, updated)
    return updated


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
    maybe_apply_system_optimizations(
        paths=paths,
        manifest=manifest,
        reporter=reporter,
        state=state,
        state_path=state_path,
        input_stream=None,
        cli_options=SystemOptimizationCliOptions(),
        environ=environ,
        auto_update_child=True,
        run=run,
    )



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
    run=subprocess.run,
) -> dict[str, Any]:
    root = _system_root(environ)
    sudo_password = None if _is_fake_root(root) else _sudo_password(
        reporter=reporter, input_stream=input_stream, environ=environ, run=run
    )
    actions = list(ledger.get("actions", [])) if isinstance(ledger.get("actions"), list) else []
    restore_preimages = dict(ledger.get("restore_preimages", {})) if isinstance(ledger.get("restore_preimages"), dict) else {}
    policy = ledger.get("policy", {}) if isinstance(ledger.get("policy"), dict) else {}
    selected_ids = _selected_operation_ids(spec, policy)
    if "qidiclient_static_gifs" in selected_ids:
        _validate_qidiclient_archive(paths.installer_root / spec.qidiclient_static_gifs.archive, spec.qidiclient_static_gifs.sha256)
    applied_any = False
    current_preimages: dict[str, Any] = {}
    try:
        for operation_id in selected_ids:
            started_at = _now()
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
                reconciled=source == "auto_update_reconcile",
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
    except Exception as exc:
        _journal_path(paths).unlink(missing_ok=True)
        try:
            _restore_preimage_map(
                current_preimages,
                paths=paths,
                root=root,
                sudo_password=sudo_password,
                run=run,
            )
        except Exception:
            pass
        partial = {**ledger, "actions": actions, "restore_preimages": {**restore_preimages, **current_preimages}}
        raise SystemOptimizationApplyError(getattr(exc, "message", str(exc)), partial) from exc
    if source == "auto_update_reconcile":
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



def _selected_operation_ids(spec: SystemOptimizationsSpec, policy: dict[str, Any]) -> tuple[str, ...]:
    ids = [MOONRAKER_METADATA_OPERATION]
    if policy.get("system_optimizations") != "enabled":
        return tuple(ids)
    ids.extend(["dns", "apt_sources", "qidiclient_static_gifs"])
    ids.extend(f"service_{service}" for service in spec.services.disable)
    if policy.get("ai_detection") == "disable":
        ids.extend(f"service_{item.service}" for item in spec.services.optional_disable)
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


def _journal_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / SYSTEM_JOURNAL


def _sudo_password(*, reporter, input_stream, environ: dict[str, str], run) -> str:
    return authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
