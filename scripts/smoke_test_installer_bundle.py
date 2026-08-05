#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.manifest import load_manifest
from installer.runtime.naming import (
    BUNDLE_ROOT_NAME,
    INSTALL_BACKUP_LABEL_PREFIX,
    UNINSTALL_BACKUP_LABEL_PREFIX,
)

FIXTURE_ROOT = REPO_ROOT / "installer/tests/fixtures/runtime/base"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    args = parser.parse_args(argv)

    archive_path = Path(args.archive).resolve()
    validate_bundle_archive(archive_path)

    workspace = Path(tempfile.mkdtemp(prefix="installer-smoke-"))
    extract_root = workspace / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(extract_root)
    bundle_root = extract_root / BUNDLE_ROOT_NAME
    if not (bundle_root / "restore.sh").exists():
        raise SystemExit("bundle smoke test is missing restore.sh")
    if not (bundle_root / "installer/system/qidiclient-static-gifs.tar.gz").exists():
        raise SystemExit("bundle smoke test is missing qidiclient static GIF archive")
    snapshot_base = bundle_root / "installer/stock/qidi-max4-defaults/firmwares"
    for firmware in ("01.01.06.03", "01.01.06.04", "01.01.06.05"):
        snapshot_root = snapshot_base / firmware / "config"
        printer_config = snapshot_root / "printer.cfg"
        if not printer_config.exists():
            raise SystemExit(f"bundle smoke test is missing {firmware} stock config snapshot")
        for excluded in ("MCU_ID.cfg", "box.cfg", "fluidd.cfg", "saved_variables.cfg"):
            if (snapshot_root / excluded).exists():
                raise SystemExit(
                    f"bundle smoke test found excluded {firmware} stock file: {excluded}"
                )

    firmware_05_root = snapshot_base / "01.01.06.05" / "config"
    firmware_05_printer = (firmware_05_root / "printer.cfg").read_text(encoding="utf-8")
    for expected in (
        "tachometer_poll_interval: 0.0005",
        "# [smart_output_pin polar_cooler]",
        "# [smart_output_pin beeper]",
        "[output_pin polar_cooler]",
    ):
        if expected not in firmware_05_printer:
            raise SystemExit(f"bundle .05 stock printer.cfg is missing {expected}")
    firmware_05_qd_macro = (
        firmware_05_root / "klipper-macros-qd/qd_macro.cfg"
    ).read_text(encoding="utf-8")
    for expected in ("SET_KINEMATIC_POSITION Z=150", "G1 Z1 F600"):
        if expected not in firmware_05_qd_macro:
            raise SystemExit(f"bundle .05 stock qd_macro.cfg is missing {expected}")
    package_manifest = load_manifest(bundle_root / "installer/package.yaml")
    for patch in package_manifest.install.source_patches:
        for variant in patch.variants:
            payload = bundle_root / "installer" / variant.source
            if not payload.is_file() or payload.is_symlink():
                raise SystemExit(
                    f"bundle smoke test is missing source payload: {variant.source}"
                )
            payload_bytes = payload.read_bytes()
            try:
                compile(payload_bytes, str(payload), "exec")
            except SyntaxError as exc:
                raise SystemExit(
                    f"bundle source payload does not compile: {variant.source}: {exc}"
                ) from exc
            if hashlib.sha256(payload_bytes).hexdigest() != variant.desired_sha256:
                raise SystemExit(
                    "bundle source payload hash does not match package manifest: "
                    f"{variant.source}"
                )
    package_version = read_package_version(bundle_root / "installer/package.yaml")
    help_output = run_command([str(bundle_root / "install.sh"), "--help"], cwd=bundle_root, env=os.environ.copy())
    if help_output.returncode != 0:
        raise SystemExit(help_output.stdout + help_output.stderr)
    for expected in (
        f"QIDI Max 4 Optimized installer {package_version}",
        "Usage: ./install.sh [options]",
        "-v, --version",
        "--uninstall",
        "--clear-recovery-sentinel",
        "--plain",
        "--debug",
        "--dry-run",
        "--demo-tui",
        "--yes",
        "--skip-system-optimizations",
        "--disable-ai-detection",
        "--keep-ai-detection",
        "--keep-system-optimizations",
    ):
        if expected not in help_output.stdout:
            raise SystemExit(f"install.sh help output is missing {expected}")
    version_output = run_command([str(bundle_root / "install.sh"), "-v"], cwd=bundle_root, env=os.environ.copy())
    if version_output.returncode != 0:
        raise SystemExit(version_output.stdout + version_output.stderr)
    if version_output.stdout.strip() != f"QIDI Max 4 Optimized installer {package_version}":
        raise SystemExit("install.sh -v output did not match package.version")

    with moonraker_server("standby") as url:
        dry_run_install_printer_root = prepare_printer_root(workspace / "dry-run-install-printer")
        dry_run_install_env = build_env(dry_run_install_printer_root, moonraker_url=url)
        dry_run_install = run_command(
            [str(bundle_root / "install.sh"), "--dry-run", "--plain"],
            cwd=bundle_root,
            env=dry_run_install_env,
        )
        if dry_run_install.returncode != 0:
            raise SystemExit(dry_run_install.stdout + dry_run_install.stderr)
        if (dry_run_install_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("install dry-run smoke test unexpectedly created state file")
        if list(dry_run_install_printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("install dry-run smoke test unexpectedly created a backup zip")
        if "Dry-run summary:" not in dry_run_install.stdout:
            raise SystemExit("install dry-run smoke test did not emit the dry-run summary")

        plain_printer_root = prepare_printer_root(workspace / "plain-printer")
        plain_env = build_env(plain_printer_root, moonraker_url=url)
        install = run_command(
            [str(bundle_root / "install.sh"), "--plain"],
            cwd=bundle_root,
            env=plain_env,
            input_text="yes\nno\nno\n",
        )
        if install.returncode != 0:
            raise SystemExit(install.stdout + install.stderr)
        if not (plain_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("install smoke test did not create state file")

        uninstall_dry_run = run_command(
            [str(bundle_root / "install.sh"), "--uninstall", "--dry-run", "--plain"],
            cwd=bundle_root,
            env=plain_env,
        )
        if uninstall_dry_run.returncode != 0:
            raise SystemExit(uninstall_dry_run.stdout + uninstall_dry_run.stderr)
        if not (plain_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("uninstall dry-run smoke test unexpectedly removed state file")
        if list(plain_printer_root.glob(f"{UNINSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("uninstall dry-run smoke test unexpectedly created a backup zip")
        if "Dry-run summary:" not in uninstall_dry_run.stdout:
            raise SystemExit("uninstall dry-run smoke test did not emit the dry-run summary")

        uninstall = run_command(
            [str(bundle_root / "install.sh"), "--uninstall", "--plain"],
            cwd=bundle_root,
            env=plain_env,
            input_text="yes\nno\nno\n",
        )
        if uninstall.returncode != 0:
            raise SystemExit(uninstall.stdout + uninstall.stderr)
        if (plain_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("uninstall smoke test did not remove state file")

        rich_printer_root = prepare_printer_root(workspace / "rich-printer")
        rich_env = build_env(rich_printer_root, moonraker_url=url)
        rich_env["TERM"] = "xterm-256color"
        rich_install = run_command_with_pty(
            [str(bundle_root / "install.sh")],
            cwd=bundle_root,
            env=rich_env,
            input_text="yes\nno\nno\nno\n",
        )
        if rich_install.returncode != 0:
            raise SystemExit(rich_install.stdout)
        if "QIDI Max 4 Optimized installer" not in rich_install.stdout:
            raise SystemExit("rich launcher smoke test did not render the live TUI")
        if not (rich_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("rich install smoke test did not create state file")

        demo_printer_root = prepare_printer_root(workspace / "demo-printer")
        demo_env = build_env(demo_printer_root, moonraker_url=url)
        demo_env["TERM"] = "xterm-256color"
        demo_env["TLTG_OPTIMIZED_DEMO_TUI_DELAY_SECONDS"] = "0.05"
        rich_demo = run_command_with_pty(
            [str(bundle_root / "install.sh"), "--demo-tui"],
            cwd=bundle_root,
            env=demo_env,
        )
        if rich_demo.returncode != 0:
            raise SystemExit(rich_demo.stdout)
        if "QIDI Max 4 Optimized installer" not in rich_demo.stdout:
            raise SystemExit("demo TUI smoke test did not render the live TUI")
        if "Installed." not in rich_demo.stdout:
            raise SystemExit("demo TUI smoke test did not emit the install success line")
        if (demo_printer_root / "config/tltg_optimized_state.yaml").exists():
            raise SystemExit("demo TUI smoke test unexpectedly created state file")
        if list(demo_printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("demo TUI smoke test unexpectedly created a backup zip")

        restore_printer_root = prepare_printer_root(workspace / "restore-printer")
        restore_env = build_env(restore_printer_root, moonraker_url=url)
        restore_install = run_command(
            [str(bundle_root / "install.sh"), "--plain"],
            cwd=bundle_root,
            env=restore_env,
            input_text="yes\nno\nno\n",
        )
        if restore_install.returncode != 0:
            raise SystemExit(restore_install.stdout + restore_install.stderr)
        restore_backups = sorted(restore_printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip"))
        if len(restore_backups) != 1:
            raise SystemExit("restore helper smoke test did not create the expected install backup zip")
        restore_backup = restore_backups[0]
        invalid_restore_zip = restore_printer_root / "invalid-restore.zip"
        with zipfile.ZipFile(invalid_restore_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("notes.txt", "not a runtime backup\n")
        restore_printer_cfg_before = (restore_printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        invalid_restore_run = run_command(
            [str(bundle_root / "restore.sh"), "--backup", str(invalid_restore_zip)],
            cwd=bundle_root,
            env=restore_env,
            input_text="RESTORE\n",
        )
        if invalid_restore_run.returncode == 0:
            raise SystemExit("restore helper smoke test unexpectedly accepted an invalid backup zip")
        if (restore_printer_root / "config/printer.cfg").read_text(encoding="utf-8") != restore_printer_cfg_before:
            raise SystemExit("restore helper smoke test changed printer.cfg after an invalid backup zip")

        (restore_printer_root / "config/printer.cfg").write_text("[printer]\n", encoding="utf-8")
        state_path = restore_printer_root / "config/tltg_optimized_state.yaml"
        if state_path.exists():
            state_path.unlink()
        managed_tree = restore_printer_root / "config/tltg-optimized-macros"
        if managed_tree.exists():
            shutil.rmtree(managed_tree)
        restore_run = run_command(
            [str(bundle_root / "restore.sh"), "--backup", str(restore_backup)],
            cwd=bundle_root,
            env=restore_env,
            input_text="RESTORE\n",
        )
        if restore_run.returncode != 0:
            raise SystemExit(restore_run.stdout + restore_run.stderr)
        restored_printer_cfg = (restore_printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        if "[include tltg-optimized-macros/*.cfg]" in restored_printer_cfg:
            raise SystemExit("restore helper smoke test did not restore the archived printer.cfg state")
        if state_path.exists():
            raise SystemExit("restore helper smoke test unexpectedly preserved the installed state file")
        if managed_tree.exists():
            raise SystemExit("restore helper smoke test unexpectedly preserved the managed tree")

        rich_vendor = bundle_root / "installer/runtime/vendor/rich"
        disabled_rich_vendor = bundle_root / "installer/runtime/vendor/rich.disabled"
        rich_vendor.rename(disabled_rich_vendor)
        try:
            fallback_printer_root = prepare_printer_root(workspace / "fallback-printer")
            fallback_env = build_env(fallback_printer_root, moonraker_url=url)
            fallback_env["TERM"] = "xterm-256color"
            fallback_install = run_command_with_pty(
                [str(bundle_root / "install.sh")],
                cwd=bundle_root,
                env=fallback_env,
                input_text="yes\nno\nno\nno\n",
            )
            if fallback_install.returncode != 0:
                raise SystemExit(fallback_install.stdout)
            if "stage 1/5" not in fallback_install.stdout:
                raise SystemExit("plain fallback smoke test did not emit contract stage output")
            if "QIDI Max 4 Optimized installer" in fallback_install.stdout:
                raise SystemExit("plain fallback smoke test unexpectedly rendered the rich TUI")
        finally:
            disabled_rich_vendor.rename(rich_vendor)

    print("bundle smoke test passed")
    return 0


@contextmanager
def moonraker_server(state: str):
    payload = {"result": {"status": {"print_stats": {"state": state}}}}
    process = {"pid": 100}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, body):
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

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


def prepare_printer_root(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_ROOT / "config", root / "config")
    shutil.copy2(FIXTURE_ROOT / "firmware_manifest.json", root / "firmware_manifest.json")
    homing = REPO_ROOT / "installer/klipper/qidi/homing.py"
    target = root / "klipper/klippy/extras"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(homing, target / "homing.py")
    return root


def build_env(printer_root: Path, *, moonraker_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(printer_root),
            "TLTG_OPTIMIZED_FIRMWARE_MANIFEST": str(printer_root / "firmware_manifest.json"),
            "TLTG_OPTIMIZED_MOONRAKER_URL": moonraker_url,
            "TLTG_OPTIMIZED_KLIPPER_ROOT": str(printer_root / "klipper"),
        }
    )
    return env


def run_command(args: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
    )


def run_command_with_pty(args: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None):
    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=False,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    if input_text:
        os.write(master_fd, input_text.encode("utf-8"))

    chunks: list[bytes] = []
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                if process.poll() is not None:
                    break
                continue
            chunks.append(chunk)
            if process.poll() is not None and len(chunk) < 4096:
                continue
    finally:
        os.close(master_fd)

    return subprocess.CompletedProcess(
        args=args,
        returncode=process.wait(),
        stdout=b"".join(chunks).decode("utf-8", errors="replace"),
        stderr="",
    )


def read_package_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^package:\n(?:^[ \t]+.*\n)*?^[ \t]+version:\s*["\']?([^"\'\n]+)', text)
    if match is None:
        raise ValueError(f"Could not read package.version from {path}")
    return match.group(1)


def validate_bundle_archive(path: Path) -> None:
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/"):
                raise ValueError(f"Archive entry has absolute path: {member.name}")
            member_path = Path(member.name)
            if member_path.parts[0] != BUNDLE_ROOT_NAME:
                raise ValueError(f"Archive entry escapes bundle root: {member.name}")
            if any(part == ".." for part in member_path.parts):
                raise ValueError(f"Archive entry contains ..: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"Archive entry type is not allowed: {member.name}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
