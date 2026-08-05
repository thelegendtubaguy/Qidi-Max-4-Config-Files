from __future__ import annotations

import hashlib
import io
import json
import shutil
import unittest
import zipfile

from installer.runtime import patches
from installer.runtime.backup import BackupArchiveError, load_external_backup_entries
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.manifest import load_manifest
from installer.runtime.models import (
    InstalledState,
    ManagedTreeState,
    PatchLedgerEntry,
    SourcePatchState,
    UpgradeSource,
    UpgradeSourcePatch,
    UpgradeSources,
)
from installer.runtime.state_file import StateValidationError, load_installed_state, write_installed_state
from installer.runtime.uninstall import run_uninstall
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.source_patches import (
    SourcePatchError,
    classify_install_source_patch,
    validate_source_state,
)
from installer.runtime.process_restart import ProcessRestartError
from installer.tests.helpers import (
    REPO_ROOT,
    build_env,
    copy_base_runtime,
    homing_fixture_bytes,
    homing_sync_reset_fixture_bytes,
    moonraker_urlopen,
    temp_path,
)


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

    def _paths(self, root):
        return resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats"))

    def test_manifest_exposes_both_01010604_stock_variants(self):
        patch = self.manifest.install.source_patches[0]
        variants = tuple(
            variant
            for variant in patch.variants
            if variant.firmware == "01.01.06.04"
        )
        self.assertEqual(len(variants), 2)
        self.assertEqual(
            {variant.expected_sha256 for variant in variants},
            {
                "ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0",
                "0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761",
            },
        )
        self.assertEqual(
            {variant.source for variant in variants},
            {
                "klipper/qidi/homing.py",
                "klipper/qidi/homing-sync-reset.py",
            },
        )

    def test_manifest_exposes_only_sync_reset_01010605_stock_variant(self):
        patch = self.manifest.install.source_patches[0]
        variants = tuple(
            variant
            for variant in patch.variants
            if variant.firmware == "01.01.06.05"
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(
            variants[0].expected_sha256,
            "0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761",
        )
        self.assertEqual(variants[0].source, "klipper/qidi/homing-sync-reset.py")
        self.assertEqual(
            variants[0].desired_sha256,
            "09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a",
        )

    def test_payload_compiles_hashes_and_has_required_timing_without_diagnostics(self):
        payload = REPO_ROOT / "installer/klipper/qidi/homing.py"
        value = payload.read_bytes()
        self.assertEqual(hashlib.sha256(value).hexdigest(), "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3")
        compile(value, str(payload), "exec")
        text = value.decode("utf-8")
        self.assertIn("G4 P100", text); self.assertIn("G4 P50", text)
        self.assertIn(".25 if rails[0].get_name()", text)
        self.assertIn("retry_count >= hi.retries", text)
        self.assertIn("self.toolhead.manual_move(lift, hi.retract_speed)", text)
        self.assertIn("SET_HOMING_MODE STEPPER=y VALUE=2", text)
        self.assertNotIn("TLTG_HOME_TIME_MARK", text)
        self.assertNotIn("TLTG_HOME_TIMING", text)
        self.assertNotIn("TLTG_HOME_MACRO_TIMING", text)

    def test_sync_reset_payload_compiles_preserves_vendor_behavior_and_has_optimized_timing(self):
        payload = REPO_ROOT / "installer/klipper/qidi/homing-sync-reset.py"
        value = payload.read_bytes()
        self.assertEqual(
            hashlib.sha256(value).hexdigest(),
            "09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a",
        )
        compile(value, str(payload), "exec")
        regular = (REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes()
        block_start = value.index(b"        # self.endstops[0][0].endstop_sync_reset()")
        block_end = value.index(
            b"        for mcu_endstop, name in self.endstops:", block_start
        )
        block = value[block_start:block_end]
        stock_variant = homing_sync_reset_fixture_bytes()
        stock_block_start = stock_variant.index(
            b"        # self.endstops[0][0].endstop_sync_reset()"
        )
        stock_block_end = stock_variant.index(
            b"        for mcu_endstop, name in self.endstops:", stock_block_start
        )
        self.assertEqual(block, stock_variant[stock_block_start:stock_block_end])
        self.assertEqual(value.replace(block, b"", 1), regular)

        text = value.decode("utf-8")
        self.assertIn("target_obj.endstop_sync_reset()", text)
        self.assertIn("Sync Reset executing via class", text)
        self.assertIn("G4 P100", text)
        self.assertIn("G4 P50", text)
        self.assertIn('.25 if rails[0].get_name() in ("stepper_x", "stepper_y")', text)
        self.assertNotIn("TLTG_HOME_TIME_MARK", text)
        self.assertNotIn("TLTG_HOME_TIMING", text)
        self.assertNotIn("TLTG_HOME_MACRO_TIMING", text)

    def test_stock_fixtures_match_supported_baselines(self):
        fixtures = (
            (
                "01.01.06.03",
                homing_fixture_bytes("01.01.06.03"),
                "89428b465b7f3d62bd8b65b3155b8aa8e93cd917f59779e40a246b5d89ff8d71",
            ),
            (
                "01.01.06.04",
                homing_fixture_bytes("01.01.06.04"),
                "ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0",
            ),
            (
                "01.01.06.04-sync-reset",
                homing_sync_reset_fixture_bytes(),
                "0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761",
            ),
            (
                "01.01.06.05",
                homing_fixture_bytes("01.01.06.05"),
                "0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761",
            ),
        )
        for name, value, expected in fixtures:
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256(value).hexdigest(), expected)
                compile(value, name, "exec")

    def test_install_applies_stock_source_and_records_first_preimage(self):
        root = copy_base_runtime(); paths = self._paths(root)
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3")
        state = (root / "config/tltg_optimized_state.yaml").read_text()
        self.assertIn("source_patches:", state)
        self.assertIn("89428b465b7f3d62bd8b65b3155b8aa8e93cd917f59779e40a246b5d89ff8d71", state)

    def test_source_install_on_all_supported_firmware_fixtures(self):
        for firmware in ("01.01.06.03", "01.01.06.04", "01.01.06.05"):
            with self.subTest(firmware=firmware):
                root = copy_base_runtime(); paths = self._paths(root)
                (root / "firmware_manifest.json").write_text(json.dumps({"SOC": {"version": firmware}}), encoding="utf-8")
                if firmware != "01.01.06.03":
                    shutil.copytree(
                        REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares" / firmware / "config",
                        root / "config",
                        dirs_exist_ok=True,
                    )
                (paths.managed_klipper_root / "klippy/extras/homing.py").write_bytes(
                    homing_fixture_bytes(firmware)
                )
                run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
                expected = (
                    "09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a"
                    if firmware == "01.01.06.05"
                    else "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3"
                )
                self.assertEqual(
                    hashlib.sha256((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()).hexdigest(),
                    expected,
                )

    def test_install_preserves_sync_reset_variant_behavior_and_provenance(self):
        root = copy_base_runtime(); paths = self._paths(root)
        (root / "firmware_manifest.json").write_text(
            json.dumps({"SOC": {"version": "01.01.06.04"}}), encoding="utf-8"
        )
        shutil.copytree(
            REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares/01.01.06.04/config",
            root / "config",
            dirs_exist_ok=True,
        )
        stock = homing_sync_reset_fixture_bytes()
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        target.write_bytes(stock)

        run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
        )

        desired = (REPO_ROOT / "installer/klipper/qidi/homing-sync-reset.py").read_bytes()
        self.assertEqual(target.read_bytes(), desired)
        state = load_installed_state(root / "config/tltg_optimized_state.yaml")
        self.assertEqual(state.source_patches[0].original_bytes, stock)
        self.assertEqual(
            state.source_patches[0].desired_sha256,
            "09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a",
        )

        compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )
        run_uninstall(
            paths,
            self.manifest,
            compatibility,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
        )
        self.assertEqual(target.read_bytes(), stock)

    def test_noninteractive_restart_failure_preserves_installed_state_and_marker(self):
        root = copy_base_runtime(); paths = self._paths(root)

        def failing_restart(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                from installer.tests.helpers import _JsonResponse
                return _JsonResponse({"result": {"state": "ready", "process_id": 100}})
            if "/machine/services/restart" in url:
                raise OSError("restart unavailable")
            return moonraker_urlopen()(request, timeout)

        with self.assertRaises(ProcessRestartError):
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=failing_restart)
        self.assertTrue((root / "config/tltg_optimized_state.yaml").exists())
        self.assertTrue(paths.restart_marker_path.exists())

    def test_unknown_source_drift_fails_before_config_backup(self):
        root = copy_base_runtime(); paths = self._paths(root)
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        target.write_bytes(b"unknown source")
        with self.assertRaises(SourcePatchError) as raised:
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        message = str(raised.exception)
        self.assertIn("firmware 01.01.06.03", message)
        self.assertIn(
            hashlib.sha256(b"unknown source").hexdigest(), message
        )
        self.assertIn("accepted SHA-256", message)
        self.assertFalse(list(root.glob("tltg-optimized-macros-before-optimize-*.zip")))

    def test_firmware_05_rejects_firmware_04_standard_homing_before_backup(self):
        root = copy_base_runtime(); paths = self._paths(root)
        (root / "firmware_manifest.json").write_text(
            json.dumps({"SOC": {"version": "01.01.06.05"}}), encoding="utf-8"
        )
        shutil.copytree(
            REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares/01.01.06.05/config",
            root / "config",
            dirs_exist_ok=True,
        )
        standard_04 = homing_fixture_bytes("01.01.06.04")
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        target.write_bytes(standard_04)

        with self.assertRaises(SourcePatchError) as raised:
            run_install(
                paths,
                self.manifest,
                PlainReporter(io.StringIO()),
                urlopen=moonraker_urlopen(),
            )

        message = str(raised.exception)
        self.assertIn("firmware 01.01.06.05", message)
        self.assertIn(hashlib.sha256(standard_04).hexdigest(), message)
        self.assertFalse(list(root.glob("tltg-optimized-macros-before-optimize-*.zip")))

    def test_already_desired_source_is_a_noop_without_ledger(self):
        root = copy_base_runtime(); paths = self._paths(root)
        desired = (REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes()
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        target.write_bytes(desired)
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertEqual(target.read_bytes(), desired)
        self.assertNotIn("source_patches:", (root / "config/tltg_optimized_state.yaml").read_text())

    def test_source_state_rejects_self_consistent_unapproved_original_before_backup(self):
        root = copy_base_runtime(); paths = self._paths(root)
        value = b"locally consistent but unsupported original\n"
        state = _minimal_state(source_patches=[{
            "id": "qidi_homing", "destination": "klippy/extras/homing.py", "firmware": "01.01.06.03",
            "original_sha256": hashlib.sha256(value).hexdigest(),
            "desired_sha256": "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3",
            "original_mode": 0o644, "original_bytes": value, "install_result": "applied",
        }])
        write_installed_state(root / "config/tltg_optimized_state.yaml", state)
        with self.assertRaises(SourcePatchError):
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertFalse(list(root.glob("tltg-optimized-macros-before-optimize-*.zip")))

    def test_source_state_parser_rejects_malformed_encoded_bytes_hashes_modes_paths_and_duplicates(self):
        root = copy_base_runtime()
        stock = homing_fixture_bytes("01.01.06.03")
        state = _minimal_state(source_patches=[{
            "id": "qidi_homing", "destination": "klippy/extras/homing.py", "firmware": "01.01.06.03",
            "original_sha256": hashlib.sha256(stock).hexdigest(),
            "desired_sha256": "32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3",
            "original_mode": 0o644, "original_bytes": stock, "install_result": "applied",
        }])
        path = root / "config/tltg_optimized_state.yaml"
        write_installed_state(path, state)
        valid = path.read_text(encoding="utf-8")
        mutations = (
            ("bad-base64", lambda text: text.replace("original_bytes: ", "original_bytes: '@@@' # ", 1)),
            ("bad-hash", lambda text: text.replace(hashlib.sha256(stock).hexdigest(), "0" * 64, 1)),
            ("bad-mode", lambda text: text.replace("original_mode: 420", "original_mode: 512", 1)),
            ("bad-path", lambda text: text.replace("klippy/extras/homing.py", "../homing.py", 1)),
            ("bad-result", lambda text: text.replace("install_result: applied", "install_result: drift", 1)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                path.write_text(mutate(valid), encoding="utf-8")
                with self.assertRaises(StateValidationError):
                    load_installed_state(path)
        duplicate = valid + valid.split("source_patches:\n", 1)[1]
        path.write_text(duplicate, encoding="utf-8")
        with self.assertRaises(StateValidationError):
            load_installed_state(path)

    def test_migration_of_65_requires_matching_prior_managed_target_and_preserves_stock(self):
        root = copy_base_runtime(); paths = self._paths(root)
        cfg = root / "config/printer.cfg"
        text = cfg.read_text(encoding="utf-8").replace("homing_speed: 50", "homing_speed: 65")
        cfg.write_text(text, encoding="utf-8")
        prior = _minimal_state(patch_ledger=[
            PatchLedgerEntry("stepper_x_homing_speed", "config/printer.cfg", "stepper_x", "homing_speed", "50", "65", "applied"),
            PatchLedgerEntry("stepper_y_homing_speed", "config/printer.cfg", "stepper_y", "homing_speed", "50", "65", "noop_desired"),
        ])
        write_installed_state(root / "config/tltg_optimized_state.yaml", prior)
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        state_text = (root / "config/tltg_optimized_state.yaml").read_text(encoding="utf-8")
        self.assertIn("desired: '100'", state_text)
        self.assertIn("expected: '50'", state_text)
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_uninstall(paths, self.manifest, compatibility, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        restored = cfg.read_text(encoding="utf-8")
        self.assertEqual(restored.count("\nhoming_speed: 50\n"), 2)

    def test_65_migration_classification_requires_matching_owned_x_or_y_target_on_all_firmwares(self):
        prior = _minimal_state(patch_ledger=[
            PatchLedgerEntry("stepper_x_homing_speed", "config/printer.cfg", "stepper_x", "homing_speed", "50", "65", "applied"),
            PatchLedgerEntry("stepper_y_homing_speed", "config/printer.cfg", "stepper_y", "homing_speed", "50", "65", "noop_desired"),
        ])
        for firmware in ("01.01.06.03", "01.01.06.04", "01.01.06.05"):
            for patch in self.manifest.patches.set_options:
                if patch.id not in {"stepper_x_homing_speed", "stepper_y_homing_speed"}:
                    continue
                with self.subTest(firmware=firmware, patch=patch.id):
                    self.assertEqual(
                        patches.classify_install_patch("65", patch, firmware, prior).classification,
                        patches.INSTALL_APPLIED,
                    )
                    self.assertEqual(
                        patches.classify_install_patch("65", patch, firmware, None).classification,
                        patches.USER_MODIFIED,
                    )

    def test_65_migration_rejects_fabricated_wrong_target_ledger(self):
        patch = next(item for item in self.manifest.patches.set_options if item.id == "stepper_x_homing_speed")
        prior = _minimal_state(patch_ledger=[
            PatchLedgerEntry(
                "stepper_x_homing_speed", "config/other.cfg", "stepper_x", "homing_speed",
                "50", "65", "applied",
            ),
        ])
        self.assertEqual(
            patches.classify_install_patch("65", patch, "01.01.06.03", prior).classification,
            patches.USER_MODIFIED,
        )

    def test_future_allowlisted_prior_desired_payload_is_migrated_with_first_preimage(self):
        root = copy_base_runtime(); paths = self._paths(root)
        stock = homing_fixture_bytes("01.01.06.03")
        prior_payload = b"prior managed homing payload\n"
        prior_hash = hashlib.sha256(prior_payload).hexdigest()
        state = _minimal_state(source_patches=[SourcePatchState(
            id="qidi_homing", destination="klippy/extras/homing.py", firmware="01.01.06.03",
            original_sha256=hashlib.sha256(stock).hexdigest(), desired_sha256=prior_hash,
            original_mode=0o644, original_bytes=stock, install_result="prior_managed",
        )])
        state = InstalledState(**{**state.__dict__, "package_version": "99.99.99.1"})
        compatibility = UpgradeSources(1, {
            "99.99.99.1": UpgradeSource(
                "99.99.99.1", (), (
                    UpgradeSourcePatch(
                        "qidi_homing", "klippy/extras/homing.py", "01.01.06.03",
                        hashlib.sha256(stock).hexdigest(), prior_hash,
                    ),
                ),
            ),
        })
        validate_source_state(
            state,
            self.manifest.install.source_patches,
            upgrade_sources=compatibility,
            expected_firmware="01.01.06.03",
        )
        target = paths.managed_klipper_root / "klippy/extras/homing.py"
        target.write_bytes(prior_payload)
        result = classify_install_source_patch(
            paths=paths,
            patch=self.manifest.install.source_patches[0],
            firmware="01.01.06.03",
            prior_state=state,
        )
        self.assertEqual(result.classification, "prior_managed")
        self.assertEqual(result.original.original_bytes, stock)

        target.write_bytes((REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes())
        result = classify_install_source_patch(
            paths=paths,
            patch=self.manifest.install.source_patches[0],
            firmware="01.01.06.03",
            prior_state=state,
        )
        self.assertEqual(result.original.desired_sha256, self.manifest.install.source_patches[0].variants[0].desired_sha256)

    def test_source_ledger_rejects_cross_firmware_install_and_uninstall(self):
        root = copy_base_runtime(); paths = self._paths(root)
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        (root / "firmware_manifest.json").write_text(
            json.dumps({"SOC": {"version": "01.01.06.04"}}), encoding="utf-8"
        )
        compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )
        state = load_installed_state(root / "config/tltg_optimized_state.yaml")
        with self.assertRaises(SourcePatchError):
            validate_source_state(
                state,
                self.manifest.install.source_patches,
                upgrade_sources=compatibility,
                expected_firmware="01.01.06.04",
            )
        with self.assertRaises(SourcePatchError):
            run_uninstall(
                paths, self.manifest, compatibility, PlainReporter(io.StringIO()),
                urlopen=moonraker_urlopen(),
            )

    def test_unowned_65_is_not_migrated(self):
        root = copy_base_runtime(); paths = self._paths(root)
        cfg = root / "config/printer.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("homing_speed: 50", "homing_speed: 65"), encoding="utf-8")
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertEqual(cfg.read_text(encoding="utf-8").count("\nhoming_speed: 65\n"), 2)

    def test_external_archive_validation_rejects_tampering_and_requires_exact_allowlist(self):
        root = temp_path("source-archive-")
        allowed = {"qidi_homing": "klippy/extras/homing.py"}
        data = b"source bytes"
        digest = hashlib.sha256(data).hexdigest()
        valid = {
            "schema_version": 1,
            "files": [{"id": "qidi_homing", "destination": "klippy/extras/homing.py", "member": "external/qidi_homing", "sha256": digest, "mode": 0o644}],
        }
        for name, metadata, members in (
            ("valid", valid, {"external/qidi_homing": data}),
            ("bad-hash", {**valid, "files": [{**valid["files"][0], "sha256": "0" * 64}]}, {"external/qidi_homing": data}),
            ("wrong-destination", {**valid, "files": [{**valid["files"][0], "destination": "../../escape"}]}, {"external/qidi_homing": data}),
            ("extra-member", valid, {"external/qidi_homing": data, "external/extra": b"x"}),
        ):
            path = root / f"{name}.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(".tltg-external-files.json", json.dumps(metadata))
                for member, value in members.items(): archive.writestr(member, value)
            if name == "valid":
                _, entries = load_external_backup_entries(
                    backup_zip_path=path,
                    allowed_entries=allowed,
                    require_manifest=True,
                )
                self.assertEqual(entries[0][2], data)
            else:
                with self.assertRaises(BackupArchiveError):
                    load_external_backup_entries(backup_zip_path=path, allowed_entries=allowed, require_manifest=True)


def _minimal_state(*, patch_ledger=(), source_patches=()):
    from installer.runtime.models import SourcePatchState
    return InstalledState(
        schema_version=1, package_id="qidi-max4-optimized", package_version="26.07.13.1",
        runtime_firmware="01.01.06.03", backup_label="old", installed_at="2026-01-01T00:00:00Z",
        managed_tree=ManagedTreeState("config/tltg-optimized-macros", ()), patch_ledger=tuple(patch_ledger),
        source_patches=tuple(SourcePatchState(**entry) if isinstance(entry, dict) else entry for entry in source_patches),
    )
