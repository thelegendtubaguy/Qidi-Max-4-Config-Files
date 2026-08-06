from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import unittest
from pathlib import Path
from unittest import mock

from installer.runtime import klipper_cfg
from installer.runtime.auto_update import AutoUpdateError, LOCK_HELD_ENV, run_auto_update_check, state_path
from installer.runtime.backup import load_backup_snapshot, snapshot_runtime_tree
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.manifest import load_manifest
from installer.runtime.models import (
    InstalledState,
    ManagedTreeState,
    PatchLedgerEntry,
)
from installer.runtime.reporter import PlainReporter
from installer.runtime.restore_helper import run_restore_helper
from installer.runtime.runner import run_install
from installer.runtime.state_file import load_installed_state, write_installed_state
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import (
    REPO_ROOT,
    _JsonResponse,
    build_env,
    copy_base_runtime,
    homing_fixture_bytes,
    homing_sync_reset_fixture_bytes,
    moonraker_urlopen,
    temp_path,
)


DESIRED_HOMING_SHA256 = "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3"
SYNC_RESET_DESIRED_HOMING_SHA256 = "09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a"
SOURCE_CASES = (
    ("01.01.06.03", "standard", DESIRED_HOMING_SHA256),
    ("01.01.06.04", "standard", DESIRED_HOMING_SHA256),
    ("01.01.06.04", "sync-reset", SYNC_RESET_DESIRED_HOMING_SHA256),
    ("01.01.06.05", "sync-reset", SYNC_RESET_DESIRED_HOMING_SHA256),
)


class SourcePatchLifecycleMatrixTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )

    def _fixture(self, firmware: str, *, source_variant: str = "standard"):
        printer_root = copy_base_runtime()
        if firmware != "01.01.06.03":
            shutil.copytree(
                REPO_ROOT
                / "installer/stock/qidi-max4-defaults/firmwares"
                / firmware
                / "config",
                printer_root / "config",
                dirs_exist_ok=True,
            )
        (printer_root / "firmware_manifest.json").write_text(
            json.dumps({"SOC": {"version": firmware}}), encoding="utf-8"
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        stock_source = (
            homing_sync_reset_fixture_bytes()
            if source_variant == "sync-reset"
            else homing_fixture_bytes(firmware)
        )
        source = paths.managed_klipper_root / "klippy/extras/homing.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(stock_source)
        return printer_root, paths, stock_source

    def _run_install(self, paths, *, environ=None):
        return run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
            environ=environ,
        )

    def _assert_homing_speed(self, printer_root: Path, value: str) -> None:
        text = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        for stepper in ("stepper_x", "stepper_y"):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(text, stepper, "homing_speed").value,
                value,
            )

    def test_fresh_stock_install_applies_source_and_records_preimage_for_all_variants(self):
        for firmware, source_variant, desired_sha256 in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                self._run_install(paths)

                self._assert_homing_speed(printer_root, "100")
                self.assertEqual(
                    hashlib.sha256(
                        (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()
                    ).hexdigest(),
                    desired_sha256,
                )
                state = load_installed_state(
                    printer_root / "config/tltg_optimized_state.yaml"
                )
                self.assertEqual(len(state.source_patches), 1)
                entry = state.source_patches[0]
                self.assertEqual(entry.firmware, firmware)
                self.assertEqual(entry.original_bytes, stock_source)
                self.assertEqual(entry.original_sha256, hashlib.sha256(stock_source).hexdigest())

    def test_noninteractive_box_reconciliation_adds_missing_mappings_without_replacing_existing_ones(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\n"
            "box_count = 2\n"
            "enable_box = 1\n"
            "value_t0 = 'slot0'\n"
            "value_t1 = 'slot3'\n"
            "value_t2 = 'slot2'\n"
            "value_t3 = 'slot3'\n",
            encoding="utf-8",
        )

        self._run_install(paths)

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables, "Variables", "value_t1"
            ).value,
            "'slot3'",
        )
        for tool in range(4, 8):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(
                    saved_variables, "Variables", f"value_t{tool}"
                ).value,
                f"'slot{tool}'",
            )
        self.assertNotIn(
            "value_t",
            (paths.config_root / "tltg_optimized_state.yaml").read_text(
                encoding="utf-8"
            ),
        )

    def test_2606151_upgrade_migrates_65_and_adds_source_ledger_for_all_variants(self):
        for firmware, source_variant, _ in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                cfg = printer_root / "config/printer.cfg"
                cfg.write_text(
                    cfg.read_text(encoding="utf-8").replace(
                        "homing_speed: 50", "homing_speed: 65"
                    ),
                    encoding="utf-8",
                )
                write_installed_state(
                    printer_root / "config/tltg_optimized_state.yaml",
                    InstalledState(
                        schema_version=1,
                        package_id="qidi-max4-optimized",
                        package_version="26.06.15.1",
                        runtime_firmware=firmware,
                        backup_label="legacy-26.06.15.1",
                        installed_at="2026-06-15T00:00:00Z",
                        managed_tree=ManagedTreeState(
                            "config/tltg-optimized-macros", ()
                        ),
                        patch_ledger=(
                            PatchLedgerEntry(
                                "stepper_x_homing_speed",
                                "config/printer.cfg",
                                "stepper_x",
                                "homing_speed",
                                "50",
                                "65",
                                "applied",
                            ),
                            PatchLedgerEntry(
                                "stepper_y_homing_speed",
                                "config/printer.cfg",
                                "stepper_y",
                                "homing_speed",
                                "50",
                                "65",
                                "applied",
                            ),
                        ),
                    ),
                )

                self._run_install(paths)

                self._assert_homing_speed(printer_root, "100")
                state = load_installed_state(
                    printer_root / "config/tltg_optimized_state.yaml"
                )
                speeds = {
                    entry.id: entry
                    for entry in state.patch_ledger
                    if entry.id
                    in {"stepper_x_homing_speed", "stepper_y_homing_speed"}
                }
                self.assertEqual({entry.expected for entry in speeds.values()}, {"50"})
                self.assertEqual({entry.desired for entry in speeds.values()}, {"100"})
                self.assertEqual(state.source_patches[0].original_bytes, stock_source)

    def test_auto_update_child_source_activation_advances_checksum_for_all_variants(self):
        for firmware, source_variant, desired_sha256 in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, _, _ = self._fixture(
                    firmware, source_variant=source_variant
                )
                bundle_root = temp_path("auto-update-lifecycle-") / "tltg-optimized-macros"
                bundle_root.mkdir()
                (bundle_root / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                paths = resolve_runtime_paths(
                    bundle_root=bundle_root,
                    environ=build_env(
                        printer_root,
                        moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
                    ),
                )
                archive = _release_archive()
                checksum = hashlib.sha256(archive).hexdigest()
                state_path(paths).write_text(
                    json.dumps({"latest_checksum": "0" * 64}), encoding="utf-8"
                )
                pids = iter((100, 101))
                child_calls = []

                def urlopen(request, timeout=0):
                    url = getattr(request, "full_url", str(request))
                    if url.endswith(".sha256"):
                        return _BytesResponse(f"{checksum} bundle\n".encode())
                    if url.endswith(".tar.gz"):
                        return _BytesResponse(archive)
                    if url.endswith("/printer/info"):
                        return _JsonResponse(
                            {"result": {"state": "ready", "process_id": next(pids)}}
                        )
                    if url.endswith("/machine/services/restart"):
                        return _JsonResponse({"result": "ok"})
                    if "printer/objects/query" in url:
                        return _JsonResponse(
                            {
                                "result": {
                                    "status": {"print_stats": {"state": "standby"}}
                                }
                            }
                        )
                    self.fail(f"Unexpected URL: {url}")

                def child_run(command, **kwargs):
                    child_calls.append((command, kwargs))
                    self.assertEqual(kwargs["env"][LOCK_HELD_ENV], "1")
                    child_paths = resolve_runtime_paths(
                        bundle_root=REPO_ROOT,
                        environ=build_env(
                            printer_root,
                            moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
                        ),
                    )
                    run_install(
                        child_paths,
                        self.manifest,
                        PlainReporter(io.StringIO()),
                        urlopen=urlopen,
                        environ=kwargs["env"],
                    )
                    return subprocess.CompletedProcess(command, 0)

                result = run_auto_update_check(
                    paths=paths,
                    reporter=PlainReporter(io.StringIO()),
                    environ={
                        "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                        "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
                    },
                    urlopen=urlopen,
                    run=child_run,
                )

                self.assertEqual(result.action, "updated")
                self.assertEqual(len(child_calls), 1)
                self.assertEqual(
                    json.loads(state_path(paths).read_text(encoding="utf-8"))["latest_checksum"],
                    checksum,
                )
                self.assertEqual(
                    hashlib.sha256(
                        (
                            printer_root
                            / "klipper/klippy/extras/homing.py"
                        ).read_bytes()
                    ).hexdigest(),
                    desired_sha256,
                )
                self.assertFalse(paths.restart_marker_path.exists())

    def test_auto_update_checksum_mismatch_preserves_bundle_and_release_state(self):
        printer_root, _, _ = self._fixture("01.01.06.03")
        bundle_root = temp_path("auto-update-mismatch-") / "tltg-optimized-macros"
        bundle_root.mkdir()
        (bundle_root / "old.txt").write_text("old bundle", encoding="utf-8")
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        old_checksum = "1" * 64
        advertised_checksum = "2" * 64
        state_path(paths).write_text(
            json.dumps({"latest_checksum": old_checksum}), encoding="utf-8"
        )
        child_calls = []

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _BytesResponse(f"{advertised_checksum} bundle\n".encode())
            if url.endswith(".tar.gz"):
                return _BytesResponse(b"not the advertised archive")
            if "printer/objects/query" in url:
                return _JsonResponse(
                    {"result": {"status": {"print_stats": {"state": "standby"}}}}
                )
            self.fail(f"Unexpected URL: {url}")

        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(
                paths=paths,
                reporter=PlainReporter(io.StringIO()),
                environ={
                    "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                    "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
                },
                urlopen=urlopen,
                run=lambda command, **kwargs: (
                    child_calls.append(command)
                    or subprocess.CompletedProcess(command, 0)
                ),
            )

        self.assertEqual(child_calls, [])
        self.assertEqual(
            json.loads(state_path(paths).read_text(encoding="utf-8"))[
                "latest_checksum"
            ],
            old_checksum,
        )
        self.assertEqual(
            (bundle_root / "old.txt").read_text(encoding="utf-8"), "old bundle"
        )

    def test_source_write_failure_rolls_back_source_and_marker_for_all_variants(self):
        for firmware, source_variant, _ in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                with mock.patch(
                    "installer.runtime.runner.mirror_tree",
                    side_effect=RuntimeError("force failure after source deployment"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "force failure"):
                        self._run_install(paths)

                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(paths.restart_marker_path.exists())
                self.assertFalse(
                    (printer_root / "config/tltg_optimized_state.yaml").exists()
                )

    def test_source_inclusive_restore_restores_stock_source_for_all_variants(self):
        for firmware, source_variant, _ in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                install = self._run_install(paths)
                assert install.backup_zip_path is not None
                expected_config = load_backup_snapshot(
                    backup_zip_path=install.backup_zip_path, source_directory="config"
                )
                (printer_root / "config/printer.cfg").write_text(
                    "[printer]\nmodified: yes\n", encoding="utf-8"
                )

                result = run_restore_helper(
                    paths,
                    self.manifest,
                    stream=io.StringIO(),
                    input_stream=io.StringIO("RESTORE\nY\n"),
                    backup_path=str(install.backup_zip_path),
                    urlopen=moonraker_urlopen(),
                )

                self.assertEqual(result, 0)
                self.assertEqual(
                    snapshot_runtime_tree(
                        printer_data_root=printer_root, source_directory="config"
                    ),
                    expected_config,
                )
                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(paths.restart_marker_path.exists())

    def test_uninstall_restores_stock_config_and_source_for_all_variants(self):
        for firmware, source_variant, _ in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                self._run_install(paths)

                run_uninstall(
                    paths,
                    self.manifest,
                    self.compatibility,
                    PlainReporter(io.StringIO()),
                    urlopen=moonraker_urlopen(),
                )

                self._assert_homing_speed(printer_root, "50")
                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(
                    (printer_root / "config/tltg_optimized_state.yaml").exists()
                )


class _BytesResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def _release_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"#!/bin/sh\nexit 0\n"
        member = tarfile.TarInfo("tltg-optimized-macros/install.sh")
        member.mode = 0o755
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()
