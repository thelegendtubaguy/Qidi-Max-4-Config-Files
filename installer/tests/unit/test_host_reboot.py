from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from installer.runtime import messages
from installer.runtime.host_reboot import (
    HostRebootError,
    arm_auto_update_reboot_followup,
    maybe_schedule_host_reboot,
    perform_scheduled_host_reboot,
    read_host_reboot_marker,
    verify_completed_host_reboot,
    write_host_reboot_marker,
)
from installer.runtime.models import RuntimePaths
from installer.runtime.reporter import PlainReporter
from installer.tests.helpers import REPO_ROOT, moonraker_urlopen


class HostRebootTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="host-reboot-test-"))
        self.boot_id = self.root / "boot-id"
        self.boot_id.write_text("boot-one\n", encoding="utf-8")
        self.paths = RuntimePaths(
            bundle_root=self.root / "bundle",
            installer_root=self.root / "bundle/installer",
            printer_data_root=self.root / "printer_data",
            config_root=self.root / "printer_data/config",
            firmware_manifest_path=self.root / "firmware.json",
            moonraker_url="http://moonraker.invalid/query",
            lock_path=self.root / "printer_data/lock",
            recovery_sentinel_path=self.root / "printer_data/recovery",
            backup_root=self.root / "printer_data",
            boot_id_path_override=self.boot_id,
        )

    def _write_marker(self, **kwargs):
        return write_host_reboot_marker(
            self.paths,
            package_version="1.2.3",
            source=kwargs.pop("source", "interactive_install"),
            now=lambda: datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_marker_is_strict_versioned_json_with_mode_0600(self):
        self._write_marker()

        marker = read_host_reboot_marker(self.paths)

        self.assertEqual(marker.boot_id, "boot-one")
        self.assertEqual(marker.operation_id, "rockchip_root_sync")
        self.assertEqual(self.paths.host_reboot_marker_path.stat().st_mode & 0o777, 0o600)

    def test_malformed_unknown_schema_and_wrong_mode_are_rejected(self):
        path = self.paths.host_reboot_marker_path
        path.parent.mkdir(parents=True)
        cases = (
            ("not-json\n", 0o600),
            (json.dumps({"schema_version": 99}) + "\n", 0o600),
            ("{}\n", 0o644),
        )
        for content, mode in cases:
            with self.subTest(content=content, mode=mode):
                path.write_text(content, encoding="utf-8")
                path.chmod(mode)
                with self.assertRaises(HostRebootError):
                    read_host_reboot_marker(self.paths)

    def test_changed_boot_id_clears_marker_only_after_postflight(self):
        self._write_marker()
        self.boot_id.write_text("boot-two\n", encoding="utf-8")
        calls = []

        completed = verify_completed_host_reboot(
            self.paths,
            verify_operation=lambda: calls.append("verified"),
            reporter=PlainReporter(io.StringIO()),
        )

        self.assertTrue(completed)
        self.assertEqual(calls, ["verified"])
        self.assertFalse(self.paths.host_reboot_marker_path.exists())

    def test_failed_postflight_retains_changed_boot_marker(self):
        self._write_marker()
        self.boot_id.write_text("boot-two\n", encoding="utf-8")

        with self.assertRaises(HostRebootError):
            verify_completed_host_reboot(
                self.paths,
                verify_operation=lambda: (_ for _ in ()).throw(RuntimeError("bad postflight")),
                reporter=PlainReporter(io.StringIO()),
            )

        self.assertTrue(self.paths.host_reboot_marker_path.exists())

    def test_idle_automatic_request_schedules_delayed_systemd_reboot(self):
        self._write_marker()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        scheduled = maybe_schedule_host_reboot(
            self.paths,
            reporter=PlainReporter(io.StringIO()),
            input_stream=None,
            environ={},
            automatic=True,
            urlopen=moonraker_urlopen("standby"),
            run=run,
        )

        self.assertTrue(scheduled)
        self.assertEqual(calls[0][-1], "-v")
        self.assertIn("systemd-run", calls[1])
        self.assertIn("--on-active=10s", calls[1])
        self.assertIn("complete-host-reboot", calls[1])
        self.assertNotIn("reboot", calls[1])
        self.assertTrue(self.paths.host_reboot_marker_path.exists())

    def test_delayed_reboot_rechecks_idle_state_before_systemctl_reboot(self):
        self._write_marker()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        performed = perform_scheduled_host_reboot(
            self.paths,
            reporter=PlainReporter(io.StringIO()),
            environ={},
            urlopen=moonraker_urlopen("standby"),
            run=run,
        )

        self.assertTrue(performed)
        self.assertEqual(calls[0][-1], "-v")
        self.assertEqual(calls[1][-2:], ["systemctl", "reboot"])

        calls.clear()
        performed = perform_scheduled_host_reboot(
            self.paths,
            reporter=PlainReporter(io.StringIO()),
            environ={},
            urlopen=moonraker_urlopen("printing"),
            run=run,
        )
        self.assertFalse(performed)
        self.assertEqual(calls, [])

    def test_active_and_unknown_state_defer_without_sudo(self):
        for urlopen, expected in (
            (moonraker_urlopen("printing"), messages.HOST_REBOOT_DEFERRED_ACTIVE_PRINT),
            (moonraker_urlopen(raw_payload={}), messages.HOST_REBOOT_DEFERRED_UNKNOWN_STATE),
        ):
            with self.subTest(expected=expected):
                self._write_marker()
                output = io.StringIO()
                calls = []
                scheduled = maybe_schedule_host_reboot(
                    self.paths,
                    reporter=PlainReporter(output),
                    input_stream=None,
                    environ={},
                    automatic=True,
                    urlopen=urlopen,
                    run=lambda command, **kwargs: calls.append(command),
                )
                self.assertFalse(scheduled)
                self.assertEqual(calls, [])
                self.assertIn(expected, output.getvalue())
                self.assertTrue(self.paths.host_reboot_marker_path.exists())

    def test_interactive_decline_and_noninteractive_default_leave_marker_pending(self):
        for input_stream in (io.StringIO("no\n"), None):
            with self.subTest(interactive=input_stream is not None):
                self._write_marker()
                calls = []
                scheduled = maybe_schedule_host_reboot(
                    self.paths,
                    reporter=PlainReporter(io.StringIO()),
                    input_stream=input_stream,
                    environ={},
                    urlopen=moonraker_urlopen("standby"),
                    run=lambda command, **kwargs: calls.append(command),
                )
                self.assertFalse(scheduled)
                self.assertEqual(calls, [])

    def test_scheduler_failure_retains_marker(self):
        self._write_marker()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0 if len(calls) == 1 else 1)

        scheduled = maybe_schedule_host_reboot(
            self.paths,
            reporter=PlainReporter(io.StringIO()),
            input_stream=None,
            environ={},
            automatic=True,
            urlopen=moonraker_urlopen("standby"),
            run=run,
        )

        self.assertFalse(scheduled)
        self.assertTrue(self.paths.host_reboot_marker_path.exists())

    def test_release_wrapper_exposes_and_forwards_reboot_host(self):
        text = (REPO_ROOT / "installer/release/install.sh").read_text(encoding="utf-8")

        self.assertIn("--reboot-host                 Authorize", text)
        self.assertIn('reboot_host_arg="--reboot-host"', text)
        self.assertIn('set -- "$@" "$reboot_host_arg"', text)

    def test_auto_update_child_arms_followup_not_reboot(self):
        self.paths.bundle_root.mkdir(parents=True)
        script = self.paths.bundle_root / "auto-update.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        self._write_marker(source="auto_update_child", auto_update_checksum_before="a" * 64)
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        armed = arm_auto_update_reboot_followup(
            self.paths,
            reporter=PlainReporter(io.StringIO()),
            environ={},
            run=run,
        )

        self.assertTrue(armed)
        self.assertIn("systemd-run", calls[1])
        self.assertIn("--on-active=30s", calls[1])
        self.assertNotIn("reboot", calls[1])
        self.assertEqual(calls[1][-1], "--run")


if __name__ == "__main__":
    unittest.main()
