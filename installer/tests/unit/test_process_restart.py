from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.process_restart import (
    ProcessRestartError,
    load_restart_marker,
    read_printer_info,
    restart_pending,
    service_restart_url,
    write_restart_marker,
)
from installer.tests.helpers import build_env, copy_base_runtime


class _Response:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class ProcessRestartTests(unittest.TestCase):
    def setUp(self):
        self.root = copy_base_runtime()
        self.paths = resolve_runtime_paths(
            bundle_root=Path(__file__).resolve().parents[2],
            environ=build_env(self.root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats"),
        )
        self.destination = "klippy/extras/homing.py"
        self.target = self.paths.managed_klipper_root / self.destination
        self.digest = hashlib.sha256(self.target.read_bytes()).hexdigest()
        self.marker_target = ("qidi_homing", self.destination, self.digest)
        self.allowed = {"qidi_homing": self.destination}

    def _marker(self):
        write_restart_marker(
            self.paths, (self.marker_target,), operation="install", process_id=100
        )

    def test_marker_is_strict_mode_0600_and_binds_allowlisted_target(self):
        self._marker()
        self.assertEqual(os.stat(self.paths.restart_marker_path).st_mode & 0o777, 0o600)
        marker = load_restart_marker(self.paths, allowed_entries=self.allowed)
        self.assertEqual(marker["operation"], "install")
        self.assertEqual(marker["pre_restart_process_id"], 100)
        self.assertEqual(marker["targets"], [{"id": "qidi_homing", "destination": self.destination, "sha256": self.digest}])

    def test_marker_rejects_malformed_pid_operation_duplicate_or_unallowlisted_target(self):
        cases = [
            {"schema_version": 1, "operation": "install", "pre_restart_process_id": True, "targets": []},
            {"schema_version": 1, "operation": "bad", "pre_restart_process_id": 1, "targets": []},
            {"schema_version": 1, "operation": "install", "pre_restart_process_id": 1, "targets": [{"id": "x", "destination": "../x", "sha256": "a" * 64}]},
            {"schema_version": 1, "operation": "install", "pre_restart_process_id": 1, "targets": [{"id": "x", "destination": self.destination, "sha256": "a" * 64}]},
            {"schema_version": 1, "operation": "install", "pre_restart_process_id": 1, "targets": [{"id": "qidi_homing", "destination": self.destination, "sha256": "A" * 64}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.paths.restart_marker_path.write_text(json.dumps(payload))
                with self.assertRaises(ProcessRestartError):
                    load_restart_marker(self.paths, allowed_entries=self.allowed)

    def test_printer_info_requires_positive_non_boolean_pid_and_string_state(self):
        invalid_results = [None, {}, {"process_id": True, "state": "ready"}, {"process_id": 0, "state": "ready"}, {"process_id": -1, "state": "ready"}, {"process_id": "1", "state": "ready"}, {"process_id": 1, "state": 1}]
        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaises(ProcessRestartError):
                    read_printer_info(self.paths.moonraker_url, urlopen=lambda *_args, **_kwargs: _Response({"result": result}))

    def test_verified_service_restart_posts_json_and_clears_marker_only_after_new_ready_pid(self):
        self._marker()
        requests = []
        pids = iter([100, 100, 101])

        def urlopen(request, timeout=0):
            requests.append(request)
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response({"result": {"process_id": next(pids), "state": "ready"}})
            return _Response({"result": "ok"})

        self.assertTrue(restart_pending(self.paths, allowed_entries=self.allowed, urlopen=urlopen, sleep=lambda _: None, attempts=2))
        service_request = next(item for item in requests if getattr(item, "full_url", "").endswith("/machine/services/restart"))
        self.assertEqual(service_request.get_method(), "POST")
        self.assertEqual(service_request.data, b'{"service":"klipper"}')
        self.assertEqual(service_request.get_header("Content-type"), "application/json")
        self.assertFalse(self.paths.restart_marker_path.exists())

    def test_ready_pid_different_from_marker_clears_without_second_restart(self):
        self._marker()
        requests = []

        def already_restarted(request, timeout=0):
            requests.append(getattr(request, "full_url", str(request)))
            return _Response({"result": {"process_id": 101, "state": "ready"}})

        self.assertTrue(restart_pending(self.paths, allowed_entries=self.allowed, urlopen=already_restarted, sleep=lambda _: None))
        self.assertFalse(self.paths.restart_marker_path.exists())
        self.assertFalse(any("/machine/services/restart" in item for item in requests))

    def test_transient_initial_printer_info_failures_are_retried(self):
        self._marker()
        info_calls = 0

        def transient_initial(request, timeout=0):
            nonlocal info_calls
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                info_calls += 1
                if info_calls < 3:
                    raise OSError("Moonraker starting")
                return _Response({"result": {"process_id": 101, "state": "ready"}})
            self.fail("Restart must not be requested after ready PID replacement")

        self.assertTrue(restart_pending(self.paths, allowed_entries=self.allowed, urlopen=transient_initial, sleep=lambda _: None, attempts=3))
        self.assertEqual(info_calls, 3)
        self.assertFalse(self.paths.restart_marker_path.exists())

    def test_transient_poll_failure_is_tolerated_but_unchanged_pid_times_out_and_retains_marker(self):
        self._marker()
        calls = {"info": 0}

        def unchanged(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                calls["info"] += 1
                if calls["info"] == 2:
                    raise OSError("temporarily unavailable")
                return _Response({"result": {"process_id": 100, "state": "ready"}})
            return _Response({"result": "ok"})

        with self.assertRaises(ProcessRestartError):
            restart_pending(self.paths, allowed_entries=self.allowed, urlopen=unchanged, sleep=lambda _: None, attempts=2)
        self.assertTrue(self.paths.restart_marker_path.exists())

    def test_malformed_post_restart_identity_fails_without_consuming_marker(self):
        self._marker()
        replies = iter([
            {"result": {"process_id": 100, "state": "ready"}},
            {"result": {"process_id": True, "state": "ready"}},
        ])

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response(next(replies))
            return _Response({"result": "ok"})

        with self.assertRaises(ProcessRestartError):
            restart_pending(self.paths, allowed_entries=self.allowed, urlopen=urlopen, sleep=lambda _: None, attempts=2)
        self.assertTrue(self.paths.restart_marker_path.exists())

    def test_later_retry_clears_retained_marker_after_a_failed_restart(self):
        self._marker()

        def unchanged(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response({"result": {"process_id": 100, "state": "ready"}})
            return _Response({"result": "ok"})

        with self.assertRaises(ProcessRestartError):
            restart_pending(self.paths, allowed_entries=self.allowed, urlopen=unchanged, sleep=lambda _: None, attempts=1)
        pids = iter([100, 101])

        def recovered(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _Response({"result": {"process_id": next(pids), "state": "ready"}})
            return _Response({"result": "ok"})

        self.assertTrue(restart_pending(self.paths, allowed_entries=self.allowed, urlopen=recovered, sleep=lambda _: None, attempts=1))
        self.assertFalse(self.paths.restart_marker_path.exists())

    def test_pending_absence_is_verified_before_restart(self):
        destination = "klippy/extras/tltg_pa_calibration.py"
        target = self.paths.managed_klipper_root / destination
        self.assertFalse(target.exists())
        write_restart_marker(
            self.paths,
            (("tltg_pa_calibration_extra", destination, None),),
            operation="uninstall",
            process_id=100,
        )
        allowed = {"tltg_pa_calibration_extra": destination}

        def already_restarted(request, timeout=0):
            return _Response(
                {"result": {"process_id": 101, "state": "ready"}}
            )

        self.assertTrue(
            restart_pending(
                self.paths,
                allowed_entries=allowed,
                urlopen=already_restarted,
                sleep=lambda _: None,
            )
        )
        self.assertFalse(self.paths.restart_marker_path.exists())

        write_restart_marker(
            self.paths,
            (("tltg_pa_calibration_extra", destination, None),),
            operation="uninstall",
            process_id=100,
        )
        target.write_bytes(b"unexpected")
        with self.assertRaises(ProcessRestartError):
            restart_pending(
                self.paths,
                allowed_entries=allowed,
                urlopen=lambda *_args, **_kwargs: self.fail("must not restart"),
            )

    def test_pending_target_drift_or_symlink_blocks_restart_and_retains_marker(self):
        self._marker()
        self.target.write_bytes(b"drift")
        with self.assertRaises(ProcessRestartError):
            restart_pending(self.paths, allowed_entries=self.allowed, urlopen=lambda *_args, **_kwargs: self.fail("must not restart"))
        self.assertTrue(self.paths.restart_marker_path.exists())

    def test_service_restart_url_keeps_moonraker_prefix(self):
        self.assertEqual(service_restart_url("http://host/moonraker/printer/objects/query?print_stats"), "http://host/moonraker/machine/services/restart")
