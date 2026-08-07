from __future__ import annotations

import atexit
import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
BASE_RUNTIME_FIXTURE = FIXTURES_ROOT / "runtime" / "base"
REPO_ROOT = Path(__file__).resolve().parents[2]
MOONRAKER_QUERY_URL = "http://moonraker.invalid/printer/objects/query?print_stats"
_TEMP_ROOTS: list[Path] = []
def homing_fixture_bytes(firmware: str) -> bytes:
    if firmware not in {"01.01.06.03", "01.01.06.04", "01.01.06.05"}:
        raise ValueError(f"Unsupported homing fixture firmware: {firmware}")
    if firmware == "01.01.06.05":
        return homing_sync_reset_fixture_bytes()
    value = (REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes()
    value = value.replace(b"G4 P100", b"G4 P200").replace(b"G4 P50", b"G4 P200")
    value = value.replace(
        b'self.toolhead.dwell(\r\n            .25 if rails[0].get_name() in ("stepper_x", "stepper_y")\r\n            else 1)',
        b"self.toolhead.dwell(1)",
    )
    if firmware == "01.01.06.03":
        value = value.replace(
            b"G4 P200\\nSET_HOMING_MODE STEPPER=y VALUE=2",
            b"G4 P200SET_HOMING_MODE STEPPER=y VALUE=2",
        )
    return value


def homing_sync_reset_fixture_bytes() -> bytes:
    return (
        FIXTURES_ROOT
        / "source-patches/01.01.06.04/homing-sync-reset.py"
    ).read_bytes()


def temp_path(prefix: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_ROOTS.append(root)
    return root



def _cleanup_temp_roots() -> None:
    for root in reversed(_TEMP_ROOTS):
        shutil.rmtree(root, ignore_errors=True)



atexit.register(_cleanup_temp_roots)



def copy_base_runtime() -> Path:
    temp_root = temp_path("installer-runtime-")
    shutil.copytree(BASE_RUNTIME_FIXTURE / "config", temp_root / "config")
    shutil.copy2(BASE_RUNTIME_FIXTURE / "firmware_manifest.json", temp_root / "firmware_manifest.json")
    target = temp_root / "klipper/klippy/extras"
    target.mkdir(parents=True, exist_ok=True)
    (target / "homing.py").write_bytes(homing_fixture_bytes("01.01.06.03"))
    return temp_root



def build_env(printer_data_root: Path, *, moonraker_url: str) -> dict[str, str]:
    return {
        "TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(printer_data_root),
        "TLTG_OPTIMIZED_KLIPPER_ROOT": str(printer_data_root / "klipper"),
        "TLTG_OPTIMIZED_FIRMWARE_MANIFEST": str(printer_data_root / "firmware_manifest.json"),
        "TLTG_OPTIMIZED_MOONRAKER_URL": moonraker_url,
    }



def snapshot_tree(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for item in sorted(root.rglob("*")):
        if item.is_file():
            snapshot[item.relative_to(root).as_posix()] = item.read_bytes()
    return snapshot


class _JsonResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def moonraker_urlopen(state: str | None = "standby", *, raw_payload=None):
    payload = raw_payload
    if payload is None:
        payload = {"result": {"status": {"print_stats": {"state": state}}}}
    process = {"pid": 100}

    def urlopen(request, timeout=0):
        url = getattr(request, "full_url", str(request))
        if "/machine/services/restart" in url:
            process["pid"] += 1
            return _JsonResponse({"result": "ok"})
        if "/printer/info" in url:
            return _JsonResponse({"result": {"state": "ready", "process_id": process["pid"]}})
        return _JsonResponse(payload)

    return urlopen


@contextmanager
def moonraker_server(state: str | None = "standby", *, raw_payload=None):
    payload = raw_payload
    if payload is None:
        payload = {"result": {"status": {"print_stats": {"state": state}}}}

    process = {"pid": 100}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.endswith("/printer/info"):
                self._respond({"result": {"state": "ready", "process_id": process["pid"]}})
            else:
                self._respond(payload)

        def do_POST(self):
            if self.path.endswith("/machine/services/restart"):
                process["pid"] += 1
            self._respond({"result": "ok"})

        def log_message(self, fmt, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/printer/objects/query?print_stats"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
