from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_text
from .source_patches import destination_path, sha256_bytes
from .path_safety import ensure_external_path_has_no_symlink_components


class ProcessRestartError(InstallerError):
    pass


class ProcessRestartTransientError(ProcessRestartError):
    pass


_VALID_OPERATIONS = frozenset({"install", "uninstall", "restore", "rollback"})
RESTART_READY_TIMEOUT_SECONDS = 60
DEFAULT_RESTART_ATTEMPTS = RESTART_READY_TIMEOUT_SECONDS + 1


def printer_info_url(moonraker_url: str) -> str:
    parts = urllib.parse.urlsplit(moonraker_url)
    prefix = parts.path.removesuffix("/printer/objects/query")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{prefix}/printer/info", "", ""))


def service_restart_url(moonraker_url: str) -> str:
    parts = urllib.parse.urlsplit(moonraker_url)
    prefix = parts.path.removesuffix("/printer/objects/query")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{prefix}/machine/services/restart", "", ""))


def read_printer_info(moonraker_url: str, *, urlopen=urllib.request.urlopen) -> tuple[int, str]:
    try:
        with urlopen(printer_info_url(moonraker_url), timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProcessRestartTransientError("Could not read Klipper process information.") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    pid = result.get("process_id") if isinstance(result, dict) else None
    state = result.get("state") if isinstance(result, dict) else None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not isinstance(state, str):
        raise ProcessRestartError("Moonraker returned an invalid Klipper process identity.")
    return pid, state


def write_restart_marker(
    paths,
    targets: Sequence[tuple[str, str, str | None]],
    *,
    operation: str,
    process_id: int,
) -> None:
    if operation not in _VALID_OPERATIONS:
        raise ProcessRestartError("Klipper restart marker operation is invalid.")
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise ProcessRestartError("Klipper restart marker process identity is invalid.")
    marker_targets = []
    seen_ids: set[str] = set()
    seen_destinations: set[str] = set()
    for patch_id, destination, sha256 in targets:
        if (
            not patch_id
            or patch_id in seen_ids
            or not _valid_destination(destination)
            or destination in seen_destinations
            or (sha256 is not None and not _sha256(sha256))
        ):
            raise ProcessRestartError("Klipper restart marker targets are invalid.")
        seen_ids.add(patch_id)
        seen_destinations.add(destination)
        marker_targets.append({"id": patch_id, "destination": destination, "sha256": sha256})
    if not marker_targets:
        raise ProcessRestartError("Klipper restart marker has no targets.")
    payload = {
        "schema_version": 1,
        "operation": operation,
        "pre_restart_process_id": process_id,
        "targets": marker_targets,
    }
    atomic_write_text(paths.restart_marker_path, json.dumps(payload, sort_keys=True) + "\n", mode=0o600, force_mode=True)


def load_restart_marker(paths, *, allowed_entries: Mapping[str, str]) -> dict:
    try:
        raw = json.loads(paths.restart_marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProcessRestartError("Klipper restart marker is missing or malformed.") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "operation", "pre_restart_process_id", "targets"}:
        raise ProcessRestartError("Klipper restart marker is malformed.")
    process_id = raw.get("pre_restart_process_id")
    if (
        raw.get("schema_version") != 1
        or raw.get("operation") not in _VALID_OPERATIONS
        or isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id <= 0
    ):
        raise ProcessRestartError("Klipper restart marker is malformed.")
    targets = raw.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ProcessRestartError("Klipper restart marker has no targets.")
    seen_ids: set[str] = set()
    seen_destinations: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"id", "destination", "sha256"}:
            raise ProcessRestartError("Klipper restart marker target is malformed.")
        patch_id = target.get("id")
        destination = target.get("destination")
        if (
            not isinstance(patch_id, str)
            or not patch_id
            or patch_id in seen_ids
            or not _valid_destination(destination)
            or destination in seen_destinations
            or allowed_entries.get(patch_id) != destination
            or (
                target.get("sha256") is not None
                and not _sha256(target.get("sha256"))
            )
        ):
            raise ProcessRestartError("Klipper restart marker target is malformed.")
        seen_ids.add(patch_id)
        seen_destinations.add(destination)
    return raw


def restart_pending(
    paths,
    *,
    allowed_entries: Mapping[str, str],
    urlopen=urllib.request.urlopen,
    sleep=time.sleep,
    attempts: int = DEFAULT_RESTART_ATTEMPTS,
) -> bool:
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    marker = load_restart_marker(paths, allowed_entries=allowed_entries)
    for target in marker["targets"]:
        path = destination_path(paths, target["destination"])
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root, target=path
        )
        expected_sha256 = target["sha256"]
        if expected_sha256 is None:
            if path.exists() or path.is_symlink():
                raise ProcessRestartError(
                    f"Pending Klipper source has drifted: {target['destination']}"
                )
            continue
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_bytes(path.read_bytes()) != expected_sha256
        ):
            raise ProcessRestartError(
                f"Pending Klipper source has drifted: {target['destination']}"
            )
    pre_restart_pid = marker["pre_restart_process_id"]
    current = _read_printer_info_with_retry(
        paths.moonraker_url, urlopen=urlopen, sleep=sleep, attempts=attempts
    )
    if current[1] == "ready" and current[0] != pre_restart_pid:
        atomic_delete(paths.restart_marker_path)
        return True
    request = urllib.request.Request(
        service_restart_url(paths.moonraker_url),
        data=b'{"service":"klipper"}',
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ProcessRestartError("Could not request Klipper service restart.") from exc
    for attempt in range(attempts):
        try:
            pid, state = read_printer_info(paths.moonraker_url, urlopen=urlopen)
        except ProcessRestartTransientError:
            pass
        else:
            if state == "ready" and pid != pre_restart_pid:
                atomic_delete(paths.restart_marker_path)
                return True
        if attempt + 1 < attempts:
            sleep(1)
    raise ProcessRestartError("Klipper did not return ready under a new process identity. Restart Klipper service manually, then retry the installer.")


def _read_printer_info_with_retry(moonraker_url, *, urlopen, sleep, attempts: int) -> tuple[int, str]:
    for attempt in range(attempts):
        try:
            return read_printer_info(moonraker_url, urlopen=urlopen)
        except ProcessRestartTransientError:
            if attempt + 1 == attempts:
                raise
            sleep(1)
    raise AssertionError("unreachable")


def _valid_destination(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and ".." not in pure.parts and len(pure.parts) >= 3 and pure.parts[:2] == ("klippy", "extras") and pure.as_posix() == value


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and value == value.lower() and all(c in "0123456789abcdef" for c in value)
