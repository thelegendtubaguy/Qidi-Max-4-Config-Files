from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import messages, safety
from .errors import ActivePrintError, InstallerError, PrinterStateError
from .fs_atomic import atomic_delete, atomic_write_text
from .interaction import prompt_yes
from .models import RuntimePaths
from .sudo import SudoError, authenticate_sudo, run_sudo_or_raise

HOST_REBOOT_SCHEMA_VERSION = 1
HOST_REBOOT_REASON = "rockchip_root_sync"
HOST_REBOOT_OPERATION = "rockchip_root_sync"
HOST_REBOOT_UNIT = "tltg-optimized-host-reboot"
HOST_REBOOT_FOLLOWUP_UNIT = "tltg-optimized-host-reboot-followup"


class HostRebootError(InstallerError):
    pass


@dataclass(frozen=True)
class HostRebootMarker:
    schema_version: int
    reason: str
    operation_id: str
    package_version: str
    created_at: str
    boot_id: str
    source: str
    auto_update_checksum_before: str | None = None


def current_boot_id(paths: RuntimePaths) -> str:
    try:
        value = paths.boot_id_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HostRebootError(messages.HOST_REBOOT_BOOT_ID_FAILED) from exc
    if not value or len(value) > 128 or any(char.isspace() for char in value):
        raise HostRebootError(messages.HOST_REBOOT_BOOT_ID_FAILED)
    return value


def read_host_reboot_marker(paths: RuntimePaths) -> HostRebootMarker | None:
    path = paths.host_reboot_marker_path
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if path.stat().st_mode & 0o777 != 0o600:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID) from exc
    if not isinstance(payload, dict):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    required = {
        "schema_version",
        "reason",
        "operation_id",
        "package_version",
        "created_at",
        "boot_id",
        "source",
    }
    allowed = required | {"auto_update_checksum_before"}
    if set(payload) - allowed or not required.issubset(payload):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload.get("schema_version") != HOST_REBOOT_SCHEMA_VERSION:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload.get("reason") != HOST_REBOOT_REASON or payload.get("operation_id") != HOST_REBOOT_OPERATION:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    for key in ("package_version", "created_at", "boot_id", "source"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload["source"] not in {
        "interactive_install",
        "yes_install",
        "auto_update_child",
        "auto_update_reconcile",
    }:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    checksum = payload.get("auto_update_checksum_before")
    if checksum is not None and (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(char not in "0123456789abcdef" for char in checksum)
    ):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    try:
        datetime.strptime(payload["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID) from exc
    return HostRebootMarker(
        schema_version=payload["schema_version"],
        reason=payload["reason"],
        operation_id=payload["operation_id"],
        package_version=payload["package_version"],
        created_at=payload["created_at"],
        boot_id=payload["boot_id"],
        source=payload["source"],
        auto_update_checksum_before=checksum,
    )


def write_host_reboot_marker(
    paths: RuntimePaths,
    *,
    package_version: str,
    source: str,
    auto_update_checksum_before: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> HostRebootMarker:
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    marker = HostRebootMarker(
        schema_version=HOST_REBOOT_SCHEMA_VERSION,
        reason=HOST_REBOOT_REASON,
        operation_id=HOST_REBOOT_OPERATION,
        package_version=package_version,
        created_at=moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        boot_id=current_boot_id(paths),
        source=source,
        auto_update_checksum_before=auto_update_checksum_before,
    )
    payload = {
        "schema_version": marker.schema_version,
        "reason": marker.reason,
        "operation_id": marker.operation_id,
        "package_version": marker.package_version,
        "created_at": marker.created_at,
        "boot_id": marker.boot_id,
        "source": marker.source,
    }
    if marker.auto_update_checksum_before is not None:
        payload["auto_update_checksum_before"] = marker.auto_update_checksum_before
    atomic_write_text(
        paths.host_reboot_marker_path,
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        mode=0o600,
        force_mode=True,
    )
    return marker


def clear_host_reboot_marker(paths: RuntimePaths) -> None:
    atomic_delete(paths.host_reboot_marker_path)


def verify_completed_host_reboot(
    paths: RuntimePaths,
    *,
    verify_operation: Callable[[], object],
    reporter,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.boot_id == current_boot_id(paths):
        return False
    try:
        verify_operation()
    except Exception as exc:
        reporter.line(messages.HOST_REBOOT_POSTFLIGHT_FAILED)
        raise HostRebootError(messages.HOST_REBOOT_POSTFLIGHT_FAILED) from exc
    clear_host_reboot_marker(paths)
    reporter.line(messages.HOST_REBOOT_VERIFIED)
    return True


def arm_auto_update_reboot_followup(
    paths: RuntimePaths,
    *,
    reporter,
    environ: dict[str, str],
    input_stream=None,
    run=subprocess.run,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.source != "auto_update_child":
        return False
    script = paths.bundle_root / "auto-update.sh"
    if not script.is_file():
        reporter.line(messages.HOST_REBOOT_FOLLOWUP_FAILED)
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=input_stream,
        )
        run_sudo_or_raise(
            [
                "systemd-run",
                f"--unit={HOST_REBOOT_FOLLOWUP_UNIT}",
                "--on-active=30s",
                "--collect",
                f"--uid={os.getuid()}",
                "/bin/sh",
                str(script),
                "--run",
            ],
            messages.HOST_REBOOT_FOLLOWUP_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_FOLLOWUP_FAILED)
        return False
    reporter.line(messages.HOST_REBOOT_FOLLOWUP_ARMED)
    return True


def maybe_schedule_host_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    input_stream,
    environ: dict[str, str],
    explicit: bool = False,
    automatic: bool = False,
    urlopen=urllib.request.urlopen,
    run=subprocess.run,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.boot_id != current_boot_id(paths):
        return False
    if not explicit and not automatic:
        if input_stream is None:
            reporter.line(messages.HOST_REBOOT_PENDING)
            return False
        if not prompt_yes(
            reporter=reporter,
            input_stream=input_stream,
            question=messages.HOST_REBOOT_PROMPT,
            instruction=messages.HOST_REBOOT_PROMPT_INSTRUCTION,
        ):
            reporter.line(messages.HOST_REBOOT_PENDING)
            return False
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    except ActivePrintError:
        reporter.line(messages.HOST_REBOOT_DEFERRED_ACTIVE_PRINT)
        return False
    except PrinterStateError:
        reporter.line(messages.HOST_REBOOT_DEFERRED_UNKNOWN_STATE)
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=input_stream,
        )
        run_sudo_or_raise(
            [
                "systemd-run",
                f"--unit={HOST_REBOOT_UNIT}",
                "--on-active=10s",
                "--collect",
                f"--uid={os.getuid()}",
                sys.executable,
                "-I",
                "-S",
                str(paths.bundle_root / "installer/runtime/bootstrap.py"),
                "complete-host-reboot",
                "--plain",
            ],
            messages.HOST_REBOOT_SCHEDULE_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_SCHEDULE_FAILED)
        return False
    reporter.line(messages.HOST_REBOOT_SCHEDULED)
    return True


def perform_scheduled_host_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    environ: dict[str, str],
    urlopen=urllib.request.urlopen,
    run=subprocess.run,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.boot_id != current_boot_id(paths):
        return False
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    except ActivePrintError:
        reporter.line(messages.HOST_REBOOT_DEFERRED_ACTIVE_PRINT)
        return False
    except PrinterStateError:
        reporter.line(messages.HOST_REBOOT_DEFERRED_UNKNOWN_STATE)
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=None,
        )
        run_sudo_or_raise(
            ["systemctl", "reboot"],
            messages.HOST_REBOOT_SCHEDULE_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_SCHEDULE_FAILED)
        return False
    return True
