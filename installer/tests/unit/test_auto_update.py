from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import unittest
from unittest.mock import patch

from installer.runtime.auto_update import (
    DEFAULT_CHECKSUM_URL,
    LOCK_HELD_ENV,
    SERVICE_NAME,
    TIMER_NAME,
    AutoUpdateError,
    disable_auto_updates,
    enable_auto_updates,
    maybe_prompt_enable_auto_updates,
    maybe_repair_configured_auto_updates,
    run_auto_update_check,
    service_text,
    state_path,
    timer_text,
)
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.naming import BUNDLE_ROOT_NAME
from installer.runtime.reporter import PlainReporter
from installer.runtime.process_restart import write_restart_marker
from installer.tests.helpers import REPO_ROOT, build_env, copy_base_runtime, MOONRAKER_QUERY_URL, temp_path


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def _release_archive(payloads: dict[str, bytes]) -> bytes:
    archive_path = temp_path("auto-update-archive-") / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        root = tarfile.TarInfo(BUNDLE_ROOT_NAME)
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        for relative_path, payload in payloads.items():
            info = tarfile.TarInfo(f"{BUNDLE_ROOT_NAME}/{relative_path}")
            info.size = len(payload)
            info.mode = 0o755 if relative_path.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(payload))
    return archive_path.read_bytes()


class AutoUpdateTests(unittest.TestCase):
    def test_active_print_skips_update_before_running_installer(self):
        printer_root = copy_base_runtime()
        checksum = "a" * 64
        stream = io.StringIO()
        calls = []

        def urlopen(url, timeout=0):
            if str(url).endswith(".sha256"):
                return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())
            if "printer/objects/query" in str(url):
                payload = {"result": {"status": {"print_stats": {"state": "printing"}}}}
                return _Response(json.dumps(payload).encode())
            raise AssertionError(f"unexpected url: {url}")

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(stream),
            environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
            urlopen=urlopen,
            run=run,
        )

        self.assertEqual(result.action, "skipped-active-print")
        self.assertEqual(calls, [])
        self.assertIn("Auto-update skipped because a print is active or paused.", stream.getvalue())

    def test_checksum_fetch_failure_skips_without_running_installer(self):
        printer_root = copy_base_runtime()
        stream = io.StringIO()
        calls = []

        def urlopen(url, timeout=0):
            raise AutoUpdateError("fetch failed")

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(stream),
            environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
            urlopen=urlopen,
            run=run,
        )

        self.assertEqual(result.action, "skipped-checksum-unavailable")
        self.assertEqual(calls, [])
        self.assertFalse(state_path(paths).exists())
        self.assertIn(
            "Auto-update skipped because the latest release checksum could not be fetched.",
            stream.getvalue(),
        )

    def test_first_successful_check_initializes_state_without_running_installer(self):
        printer_root = copy_base_runtime()
        checksum = "c" * 64
        stream = io.StringIO()
        calls = []

        def urlopen(url, timeout=0):
            if str(url).endswith(".sha256"):
                return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())
            if "printer/objects/query" in str(url):
                payload = {"result": {"status": {"print_stats": {"state": "standby"}}}}
                return _Response(json.dumps(payload).encode())
            raise AssertionError(f"unexpected url: {url}")

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(stream),
            environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
            urlopen=urlopen,
            run=run,
        )

        self.assertEqual(result.action, "initialized")
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)
        self.assertIn("Auto-update check: initialized latest release state.", stream.getvalue())

    def test_changed_checksum_downloads_verified_archive_and_runs_extracted_installer(self):
        printer_root = copy_base_runtime()
        bundle_root = temp_path("auto-update-bundle-") / BUNDLE_ROOT_NAME
        bundle_root.mkdir()
        (bundle_root / "old.txt").write_text("old bundle", encoding="utf-8")
        archive = _release_archive({"install.sh": b"#!/bin/sh\nexit 0\n", "payload.txt": b"new bundle\n"})
        checksum = hashlib.sha256(archive).hexdigest()
        old_checksum = "1" * 64
        stream = io.StringIO()
        calls = []
        opened_urls = []

        def urlopen(url, timeout=0):
            opened_urls.append(str(url))
            if str(url).endswith(".sha256"):
                return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())
            if str(url).endswith(".tar.gz"):
                return _Response(archive)
            if "printer/objects/query" in str(url):
                payload = {"result": {"status": {"print_stats": {"state": "standby"}}}}
                return _Response(json.dumps(payload).encode())
            raise AssertionError(f"unexpected url: {url}")

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        moonraker_url = MOONRAKER_QUERY_URL
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(printer_root, moonraker_url=moonraker_url),
        )
        state_path(paths).parent.mkdir(parents=True, exist_ok=True)
        state_path(paths).write_text(json.dumps({"latest_checksum": old_checksum}), encoding="utf-8")
        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(stream),
            environ={
                **build_env(printer_root, moonraker_url=moonraker_url),
                "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/tltg-optimized-macros.tar.gz",
            },
            urlopen=urlopen,
            run=run,
        )

        self.assertEqual(result.action, "updated")
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)
        self.assertFalse(any("install-latest.sh" in url for url in opened_urls))
        self.assertEqual(calls[0][0], ["/bin/sh", str(bundle_root / "install.sh"), "--yes", "--plain"])
        self.assertEqual(calls[0][1]["cwd"], str(bundle_root))
        self.assertEqual(calls[0][1]["env"][LOCK_HELD_ENV], "1")
        self.assertFalse((bundle_root / "old.txt").exists())
        self.assertEqual((bundle_root / "payload.txt").read_text(encoding="utf-8"), "new bundle\n")
        self.assertIn("Auto-update complete.", stream.getvalue())

    def test_changed_checksum_mismatch_does_not_replace_bundle_or_state(self):
        printer_root = copy_base_runtime()
        bundle_root = temp_path("auto-update-bundle-") / BUNDLE_ROOT_NAME
        bundle_root.mkdir()
        (bundle_root / "old.txt").write_text("old bundle", encoding="utf-8")
        checksum = "2" * 64
        old_checksum = "1" * 64
        stream = io.StringIO()
        calls = []

        def urlopen(url, timeout=0):
            if str(url).endswith(".sha256"):
                return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())
            if str(url).endswith(".tar.gz"):
                return _Response(b"not the expected archive bytes")
            if "printer/objects/query" in str(url):
                payload = {"result": {"status": {"print_stats": {"state": "standby"}}}}
                return _Response(json.dumps(payload).encode())
            raise AssertionError(f"unexpected url: {url}")

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        moonraker_url = MOONRAKER_QUERY_URL
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(printer_root, moonraker_url=moonraker_url),
        )
        state_path(paths).parent.mkdir(parents=True, exist_ok=True)
        state_path(paths).write_text(json.dumps({"latest_checksum": old_checksum}), encoding="utf-8")
        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(
                paths=paths,
                reporter=PlainReporter(stream),
                environ={
                    **build_env(printer_root, moonraker_url=moonraker_url),
                    "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                    "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/tltg-optimized-macros.tar.gz",
                },
                urlopen=urlopen,
                run=run,
            )

        self.assertEqual(calls, [])
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], old_checksum)
        self.assertEqual((bundle_root / "old.txt").read_text(encoding="utf-8"), "old bundle")

    def test_failed_auto_update_child_does_not_advance_checksum(self):
        printer_root = copy_base_runtime()
        bundle_root = temp_path("auto-update-bundle-") / BUNDLE_ROOT_NAME
        bundle_root.mkdir()
        archive = _release_archive({"install.sh": b"#!/bin/sh\\nexit 1\\n"})
        checksum = hashlib.sha256(archive).hexdigest()
        old_checksum = "1" * 64
        paths = resolve_runtime_paths(bundle_root=bundle_root, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        state_path(paths).write_text(json.dumps({"latest_checksum": old_checksum}), encoding="utf-8")

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _Response(f"{checksum} bundle\\n".encode())
            if url.endswith(".tar.gz"):
                return _Response(archive)
            if "printer/objects/query" in url:
                return _Response(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}}}}).encode())
            self.fail(url)

        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(
                paths=paths,
                reporter=PlainReporter(io.StringIO()),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256", "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/tltg-optimized-macros.tar.gz"},
                urlopen=urlopen,
                run=lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
            )
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], old_checksum)

    def test_enable_auto_updates_installs_systemd_units_through_sudo(self):
        printer_root = copy_base_runtime()
        checksum = "b" * 64
        stream = io.StringIO()
        commands = []
        run_kwargs = []

        def urlopen(url, timeout=0):
            return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())

        def run(command, **kwargs):
            commands.append(command)
            run_kwargs.append(kwargs)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"):
            enable_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=urlopen,
                run=run,
            )

        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)
        self.assertIn(["sudo", "-S", "-p", "", "-v"], commands)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "daemon-reload"], commands)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "enable", TIMER_NAME], commands)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "restart", TIMER_NAME], commands)
        sudo_kwargs = [kwargs for command, kwargs in zip(commands, run_kwargs) if command[:4] == ["sudo", "-S", "-p", ""]]
        self.assertTrue(sudo_kwargs)
        self.assertTrue(all(kwargs.get("input") == "qiditech\n" for kwargs in sudo_kwargs))
        self.assertTrue(all(kwargs.get("text") is True for kwargs in sudo_kwargs))
        install_targets = [command[-1] for command in commands if command[:6] == ["sudo", "-S", "-p", "", "install", "-m"]]
        self.assertIn(f"/etc/systemd/system/{SERVICE_NAME}", install_targets)
        self.assertIn(f"/etc/systemd/system/{TIMER_NAME}", install_targets)
        self.assertIn("Auto-updates enabled.", stream.getvalue())

    def test_repair_configured_auto_updates_refreshes_systemd_units(self):
        printer_root = copy_base_runtime()
        checksum = "f" * 64
        stream = io.StringIO()
        commands = []
        opened_urls = []

        def urlopen(url, timeout=0):
            opened_urls.append(str(url))
            return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())

        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.auto_updates_configured", return_value=True), patch(
            "installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"
        ):
            handled = maybe_repair_configured_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=urlopen,
                run=run,
            )

        self.assertTrue(handled)
        self.assertEqual(opened_urls, [DEFAULT_CHECKSUM_URL])
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "daemon-reload"], commands)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "enable", TIMER_NAME], commands)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "restart", TIMER_NAME], commands)
        self.assertIn("Auto-updates repaired.", stream.getvalue())

    def test_repair_configured_auto_updates_failure_is_nonfatal(self):
        printer_root = copy_base_runtime()
        stream = io.StringIO()

        def run(command, **kwargs):
            if command == ["sudo", "-S", "-p", "", "-v"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.auto_updates_configured", return_value=True), patch(
            "installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"
        ):
            handled = maybe_repair_configured_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=lambda url, timeout=0: _Response(("e" * 64 + "  tltg-optimized-macros.tar.gz\n").encode()),
                run=run,
            )

        self.assertTrue(handled)
        self.assertIn("Could not repair auto-updates. sudo authentication failed.", stream.getvalue())

    def test_timer_runs_five_minutes_after_boot(self):
        text = timer_text()

        self.assertIn("OnBootSec=5min\n", text)
        self.assertIn("OnUnitActiveSec=1h\n", text)

    def test_service_text_clears_url_override_environment(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )

        text = service_text(paths)

        self.assertIn(
            "UnsetEnvironment=TLTG_AUTO_UPDATE_ARCHIVE_URL TLTG_AUTO_UPDATE_CHECKSUM_URL TLTG_INSTALLER_ARCHIVE_URL TLTG_INSTALLER_CHECKSUM_URL\n",
            text,
        )
        self.assertIn(f"ExecStart={REPO_ROOT / 'auto-update.sh'} --run\n", text)

    def test_enable_auto_updates_prompts_when_default_sudo_password_fails(self):
        printer_root = copy_base_runtime()
        checksum = "d" * 64
        stream = io.StringIO()
        commands = []
        run_kwargs = []

        def urlopen(url, timeout=0):
            return _Response(f"{checksum}  tltg-optimized-macros.tar.gz\n".encode())

        def run(command, **kwargs):
            commands.append(command)
            run_kwargs.append(kwargs)
            if command == ["sudo", "-S", "-p", "", "-v"] and kwargs.get("input") == "qiditech\n":
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"):
            enable_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("correct-password\n"),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=urlopen,
                run=run,
            )

        sudo_inputs = [
            kwargs.get("input")
            for command, kwargs in zip(commands, run_kwargs)
            if command[:4] == ["sudo", "-S", "-p", ""]
        ]
        self.assertGreaterEqual(len(sudo_inputs), 2)
        self.assertEqual(sudo_inputs[0], "qiditech\n")
        self.assertEqual(sudo_inputs[1], "correct-password\n")
        self.assertTrue(all(item == "correct-password\n" for item in sudo_inputs[1:]))
        output = stream.getvalue()
        self.assertIn("Initial sudo authentication failed.", output)
        self.assertIn("Enter sudo password to continue:", output)
        self.assertIn("Auto-updates enabled.", output)

    def test_maybe_prompt_enable_auto_updates_handles_sudo_failure_nonfatally(self):
        printer_root = copy_base_runtime()
        stream = io.StringIO()

        def run(command, **kwargs):
            if command == ["sudo", "-S", "-p", "", "-v"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"), patch(
            "installer.runtime.auto_update.subprocess.run",
            side_effect=run,
        ):
            enabled = maybe_prompt_enable_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("yes\n"),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=lambda url, timeout=0: _Response(("e" * 64 + "  tltg-optimized-macros.tar.gz\n").encode()),
            )

        self.assertFalse(enabled)
        self.assertIn("Could not enable auto-updates. sudo authentication failed.", stream.getvalue())

    def test_disable_auto_updates_wraps_sudo_failure_as_auto_update_error(self):
        printer_root = copy_base_runtime()

        def run(command, **kwargs):
            if command == ["sudo", "-S", "-p", "", "-v"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"):
            with self.assertRaises(AutoUpdateError) as caught:
                disable_auto_updates(
                    paths=paths,
                    reporter=PlainReporter(io.StringIO()),
                    input_stream=None,
                    run=run,
                    require_sudo=True,
                )

        self.assertEqual(caught.exception.message, "sudo authentication failed.")

    def test_enable_auto_updates_continues_when_checksum_seed_fails(self):
        printer_root = copy_base_runtime()
        stream = io.StringIO()
        commands = []
        run_kwargs = []

        def urlopen(url, timeout=0):
            raise AutoUpdateError("seed failed")

        def run(command, **kwargs):
            commands.append(command)
            run_kwargs.append(kwargs)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.auto_update.shutil.which", return_value="/usr/bin/tool"):
            enable_auto_updates(
                paths=paths,
                reporter=PlainReporter(stream),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=urlopen,
                run=run,
            )

        self.assertFalse(state_path(paths).exists())
        self.assertIn(["sudo", "-S", "-p", "", "-v"], commands)
        sudo_kwargs = [kwargs for command, kwargs in zip(commands, run_kwargs) if command[:4] == ["sudo", "-S", "-p", ""]]
        self.assertTrue(sudo_kwargs)
        self.assertTrue(all(kwargs.get("input") == "qiditech\n" for kwargs in sudo_kwargs))
        self.assertEqual("Auto-updates enabled.\n", stream.getvalue())

    def _pending_restart_marker(self, paths):
        destination = "klippy/extras/homing.py"
        target = paths.managed_klipper_root / destination
        value = (REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes()
        target.write_bytes(value)
        digest = hashlib.sha256(value).hexdigest()
        write_restart_marker(
            paths,
            (("qidi_homing", destination, digest),),
            operation="install",
            process_id=100,
        )

    def test_pending_activation_runs_before_checksum_fetch_and_survives_checksum_failure(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        self._pending_restart_marker(paths)
        requests = []
        pids = iter([100, 101])

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request)); requests.append(url)
            if url.endswith(".sha256"):
                raise OSError("offline")
            if url.endswith("/printer/info"):
                return _Response(json.dumps({"result": {"state": "ready", "process_id": next(pids)}}).encode())
            if url.endswith("/machine/services/restart"):
                return _Response(b'{"result":"ok"}')
            if "printer/objects/query" in url:
                return _Response(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}}}}).encode())
            self.fail(url)

        result = run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"}, urlopen=urlopen)
        self.assertEqual(result.action, "skipped-checksum-unavailable")
        self.assertFalse(paths.restart_marker_path.exists())
        self.assertLess(requests.index("http://moonraker.invalid/machine/services/restart"), requests.index("https://example.invalid/latest.sha256"))

    def test_pending_activation_precedes_missing_checksum_state_initialization(self):
        printer_root = copy_base_runtime()
        checksum = "d" * 64
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        self._pending_restart_marker(paths)
        pids = iter([100, 101])

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response(json.dumps({"result": {"state": "ready", "process_id": next(pids)}}).encode())
            if url.endswith("/machine/services/restart"):
                return _Response(b'{"result":"ok"}')
            if "printer/objects/query" in url:
                return _Response(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}}}}).encode())
            if url.endswith(".sha256"):
                self.assertFalse(paths.restart_marker_path.exists())
                return _Response(f"{checksum} bundle\\n".encode())
            self.fail(url)

        result = run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"}, urlopen=urlopen)
        self.assertEqual(result.action, "initialized")
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)

    def test_pending_activation_failure_does_not_advance_matching_checksum_or_remove_marker(self):
        printer_root = copy_base_runtime()
        checksum = "f" * 64
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        self._pending_restart_marker(paths)
        state_path(paths).write_text(json.dumps({"latest_checksum": checksum}), encoding="utf-8")

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response(json.dumps({"result": {"state": "ready", "process_id": 100}}).encode())
            if "printer/objects/query" in url:
                return _Response(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}}}}).encode())
            if url.endswith("/machine/services/restart"):
                return _Response(b'{"result":"ok"}')
            self.fail("checksum must not be fetched before activation")

        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"}, urlopen=urlopen)
        self.assertTrue(paths.restart_marker_path.exists())
        self.assertEqual(json.loads(state_path(paths).read_text())["latest_checksum"], checksum)

    def test_drifted_pending_target_blocks_auto_update_before_checksum_fetch(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        self._pending_restart_marker(paths)
        (paths.managed_klipper_root / "klippy/extras/homing.py").write_bytes(b"drift")

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if "printer/objects/query" in url:
                return _Response(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}}}}).encode())
            self.fail("checksum or restart must not run: " + url)

        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"}, urlopen=urlopen)
        self.assertTrue(paths.restart_marker_path.exists())

    def test_config_only_auto_update_does_not_call_service_restart(self):
        printer_root = copy_base_runtime()
        checksum = "e" * 64
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL))
        state_path(paths).write_text(json.dumps({"latest_checksum": checksum}), encoding="utf-8")
        seen = []

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request)); seen.append(url)
            if url.endswith(".sha256"):
                return _Response(f"{checksum} bundle\\n".encode())
            self.fail(url)

        result = run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"}, urlopen=urlopen)
        self.assertEqual(result.action, "already-current")
        self.assertFalse(any("machine/services/restart" in item for item in seen))
