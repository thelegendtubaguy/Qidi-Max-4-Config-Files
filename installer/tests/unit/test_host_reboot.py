from __future__ import annotations

import io
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
from installer.tests.helpers import moonraker_urlopen, temp_path


class HostRebootTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_path("host-reboot-test-")
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

    def test_marker_is_cleared_only_after_successful_post_reboot_verification(self):
        self._write_marker()
        marker = read_host_reboot_marker(self.paths)
        self.assertEqual(marker.boot_id, "boot-one")
        self.assertEqual(self.paths.host_reboot_marker_path.stat().st_mode & 0o777, 0o600)

        self.boot_id.write_text("boot-two\n", encoding="utf-8")
        with self.assertRaises(HostRebootError):
            verify_completed_host_reboot(
                self.paths,
                verify_operation=lambda: (_ for _ in ()).throw(RuntimeError("failed")),
                reporter=PlainReporter(io.StringIO()),
            )
        self.assertTrue(self.paths.host_reboot_marker_path.exists())

        self.assertTrue(
            verify_completed_host_reboot(
                self.paths,
                verify_operation=lambda: None,
                reporter=PlainReporter(io.StringIO()),
            )
        )
        self.assertFalse(self.paths.host_reboot_marker_path.exists())

    def test_idle_reboot_is_delayed_and_rechecks_printer_state_before_execution(self):
        self._write_marker()
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        self.assertTrue(
            maybe_schedule_host_reboot(
                self.paths,
                reporter=PlainReporter(io.StringIO()),
                input_stream=None,
                environ={},
                automatic=True,
                urlopen=moonraker_urlopen("standby"),
                run=run,
            )
        )
        self.assertIn("systemd-run", calls[1])
        self.assertIn("--on-active=10s", calls[1])
        self.assertNotIn("reboot", calls[1])

        calls.clear()
        self.assertFalse(
            perform_scheduled_host_reboot(
                self.paths,
                reporter=PlainReporter(io.StringIO()),
                environ={},
                urlopen=moonraker_urlopen("printing"),
                run=run,
            )
        )
        self.assertEqual(calls, [])

        self.assertTrue(
            perform_scheduled_host_reboot(
                self.paths,
                reporter=PlainReporter(io.StringIO()),
                environ={},
                urlopen=moonraker_urlopen("standby"),
                run=run,
            )
        )
        self.assertEqual(calls[-1][-2:], ["systemctl", "reboot"])

    def test_busy_or_unknown_printer_leaves_reboot_pending(self):
        for urlopen in (
            moonraker_urlopen("printing"),
            moonraker_urlopen(raw_payload={}),
        ):
            with self.subTest(urlopen=urlopen):
                self._write_marker()
                calls = []
                self.assertFalse(
                    maybe_schedule_host_reboot(
                        self.paths,
                        reporter=PlainReporter(io.StringIO()),
                        input_stream=None,
                        environ={},
                        automatic=True,
                        urlopen=urlopen,
                        run=lambda command, **kwargs: calls.append(command),
                    )
                )
                self.assertEqual(calls, [])
                self.assertTrue(self.paths.host_reboot_marker_path.exists())

    def test_auto_update_arms_followup_without_rebooting(self):
        self.paths.bundle_root.mkdir(parents=True)
        (self.paths.bundle_root / "auto-update.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self._write_marker(source="auto_update_child", auto_update_checksum_before="a" * 64)
        calls: list[list[str]] = []

        self.assertTrue(
            arm_auto_update_reboot_followup(
                self.paths,
                reporter=PlainReporter(io.StringIO()),
                environ={},
                run=lambda command, **kwargs: (
                    calls.append(command) or subprocess.CompletedProcess(command, 0)
                ),
            )
        )
        self.assertIn("systemd-run", calls[1])
        self.assertIn("--on-active=30s", calls[1])
        self.assertNotIn("reboot", calls[1])


if __name__ == "__main__":
    unittest.main()
