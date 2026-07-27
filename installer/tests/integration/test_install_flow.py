from __future__ import annotations

import io
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.runtime import klipper_cfg
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.errors import ActivePrintError, OperationCancelled, PreflightTargetsError
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.tests.helpers import (
    MOONRAKER_QUERY_URL,
    REPO_ROOT,
    build_env,
    copy_base_runtime,
    homing_fixture_bytes,
    moonraker_urlopen,
    temp_path,
)


class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

    def _homing_override(self, printer_root):
        return klipper_cfg.resolve_unique_section(
            (printer_root / "config/klipper-macros-qd/kinematics.cfg").read_text(
                encoding="utf-8"
            ),
            "homing_override",
        ).text

    def _homing_override_exists(self, printer_root):
        try:
            self._homing_override(printer_root)
        except klipper_cfg.TargetResolutionError as exc:
            if exc.reason == "missing":
                return False
            raise
        return True

    def _set_firmware_version(self, printer_root, version):
        (printer_root / "firmware_manifest.json").write_text(
            f'{"{"}"SOC": {{"version": "{version}"}}{"}"}\n',
            encoding="utf-8",
        )

    def _overlay_stock_snapshot(self, printer_root, version):
        source = REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares" / version / "config"
        shutil.copytree(source, printer_root / "config", dirs_exist_ok=True)
        if version == "01.01.06.04":
            (printer_root / "klipper/klippy/extras/homing.py").write_bytes(
                homing_fixture_bytes(version)
            )

    def test_happy_path_install(self):
        printer_root = copy_base_runtime()
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_install(paths, self.manifest, reporter=PlainReporter(stream), urlopen=moonraker_urlopen())

        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())
        printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        self.assertIn("[include tltg-optimized-macros/*.cfg]", printer_cfg)
        self.assertIn("homing_speed: 100", printer_cfg)
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "z_tilt", "speed").value,
            "600",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "bed_mesh", "speed").value,
            "600",
        )
        self.assertIn("on_error_gcode: OPTIMIZED_CANCEL_PRINT_ON_ERROR", printer_cfg)
        self.assertFalse(self._homing_override_exists(printer_root))
        optimized_kinematics = paths.config_root / "tltg-optimized-macros/kinematics.cfg"
        self.assertIn("[homing_override]", optimized_kinematics.read_text(encoding="utf-8"))
        self.assertTrue((paths.config_root / "tltg-optimized-macros").exists())
        output = stream.getvalue()
        self.assertIn("stage 1/5", output)
        self.assertIn("Installed.", output)
        self.assertIn("Klipper service process restarted and verified.", output)
        self.assertTrue(result.backup_zip_path.exists())

    def test_happy_path_install_on_firmware_04_stock_config(self):
        printer_root = copy_base_runtime()
        self._set_firmware_version(printer_root, "01.01.06.04")
        self._overlay_stock_snapshot(printer_root, "01.01.06.04")
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, reporter=PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

        printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        official_filaments = (paths.config_root / "officiall_filas_list.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn("[include tltg-optimized-macros/*.cfg]", printer_cfg)
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "stepper_x", "homing_speed").value,
            "100",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "closed_loop x", "query_cycle").value,
            "10",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "closed_loop x", "trigger_current").value,
            "400",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                printer_cfg, "temperature_sensor Chamber_Thermal_Protection_Sensor", "max_temp"
            ).value,
            "170",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(official_filaments, "fila25", "filament").value,
            "PA6-CF",
        )
        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())

    def test_install_replaces_filament_sensor_and_resume_for_both_firmware_baselines(self):
        for firmware in ("01.01.06.03", "01.01.06.04"):
            with self.subTest(firmware=firmware):
                printer_root = copy_base_runtime()
                if firmware == "01.01.06.04":
                    self._set_firmware_version(printer_root, firmware)
                    self._overlay_stock_snapshot(printer_root, firmware)
                paths = resolve_runtime_paths(
                    bundle_root=REPO_ROOT,
                    environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
                )
                run_install(paths, self.manifest, reporter=PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

                printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
                with self.assertRaises(klipper_cfg.TargetResolutionError) as sensor_error:
                    klipper_cfg.resolve_unique_section(
                        printer_cfg, "filament_switch_sensor filament_switch_sensor"
                    )
                self.assertEqual(sensor_error.exception.reason, "missing")

                pause_resume = (
                    paths.config_root / "klipper-macros-qd/pause_resume_cancel.cfg"
                ).read_text(encoding="utf-8")
                with self.assertRaises(klipper_cfg.TargetResolutionError) as resume_error:
                    klipper_cfg.resolve_unique_section(pause_resume, "gcode_macro RESUME")
                self.assertEqual(resume_error.exception.reason, "missing")

                optimized_filament = (
                    paths.config_root / "tltg-optimized-macros/filament.cfg"
                ).read_text(encoding="utf-8")
                self.assertIn("[filament_switch_sensor filament_switch_sensor]", optimized_filament)
                self.assertIn("[gcode_macro TLTG_FILAMENT_SENSOR]", optimized_filament)
                self.assertIn("[gcode_macro RESUME]", optimized_filament)

    def test_install_patches_firmware_04_stock_drift_when_targets_exist(self):
        printer_root = copy_base_runtime()
        self._set_firmware_version(printer_root, "01.01.06.04")
        self._overlay_stock_snapshot(printer_root, "01.01.06.04")
        printer_cfg_path = printer_root / "config/printer.cfg"
        printer_cfg_path.write_text(
            printer_cfg_path.read_text(encoding="utf-8")
            .replace("query_cycle:10", "query_cycle:5", 1)
            .replace("max_temp:170", "max_temp:150", 1),
            encoding="utf-8",
        )
        official_path = printer_root / "config/officiall_filas_list.cfg"
        official_path.write_text(
            official_path.read_text(encoding="utf-8").replace("PA6-CF", "PA-CF", 2),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, reporter=PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

        printer_cfg = printer_cfg_path.read_text(encoding="utf-8")
        official_filaments = official_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "closed_loop x", "query_cycle").value,
            "10",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                printer_cfg, "temperature_sensor Chamber_Thermal_Protection_Sensor", "max_temp"
            ).value,
            "170",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(official_filaments, "fila25", "filament").value,
            "PA6-CF",
        )

    def test_install_patches_virtual_sdcard_on_error_with_inline_comment(self):
        printer_root = copy_base_runtime()
        printer_cfg_path = printer_root / "config/printer.cfg"
        printer_cfg_path.write_text(
            printer_cfg_path.read_text(encoding="utf-8").replace(
                "on_error_gcode: CANCEL_PRINT\n",
                "on_error_gcode: CANCEL_PRINT # 出错时取消打印\n",
                1,
            ),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, reporter=PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

        printer_cfg = printer_cfg_path.read_text(encoding="utf-8")
        resolved = klipper_cfg.resolve_unique_option(
            printer_cfg, "virtual_sdcard", "on_error_gcode"
        )
        self.assertEqual(resolved.value, "OPTIMIZED_CANCEL_PRINT_ON_ERROR")
        self.assertIn(
            "on_error_gcode: OPTIMIZED_CANCEL_PRINT_ON_ERROR # 出错时取消打印",
            printer_cfg,
        )

    def test_box_extras_is_not_required_for_install(self):
        printer_root = copy_base_runtime()
        (printer_root / "config/box.cfg").unlink()
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_install(paths, self.manifest, reporter=PlainReporter(stream), urlopen=moonraker_urlopen())

        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())
        self.assertTrue(result.backup_zip_path.exists())
        self.assertIn("Installed.", stream.getvalue())

    def test_install_prompts_to_enable_detected_qidi_box(self):
        printer_root = copy_base_runtime()
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\nbox_count = 1\nenable_box = 0\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            reporter=PlainReporter(stream),
            input_stream=io.StringIO("y\ny\nn\nn\n"),
            urlopen=moonraker_urlopen(),
        )

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        enabled = klipper_cfg.resolve_unique_option(saved_variables, "Variables", "enable_box")
        self.assertEqual(enabled.value, "1")
        state_text = (paths.config_root / "tltg_optimized_state.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("\n  enable_box:", state_text)
        output = stream.getvalue()
        self.assertIn("Would you like to enable QIDI Box support now?", output)
        self.assertIn("QIDI Box support enabled in saved_variables.cfg.", output)

    def test_install_preserves_disabled_detected_qidi_box_when_declined(self):
        printer_root = copy_base_runtime()
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\nbox_count = 1\nenable_box = 0\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            reporter=PlainReporter(stream),
            input_stream=io.StringIO("y\nn\nn\n"),
            urlopen=moonraker_urlopen(),
        )

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        enabled = klipper_cfg.resolve_unique_option(saved_variables, "Variables", "enable_box")
        self.assertEqual(enabled.value, "0")
        output = stream.getvalue()
        self.assertIn("Would you like to enable QIDI Box support now?", output)
        self.assertIn("QIDI Box support left disabled.", output)

    def test_install_prompts_to_correct_value_t_slot_mismatches(self):
        printer_root = copy_base_runtime()
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\n"
            "box_count = 1\n"
            "enable_box = 1\n"
            "value_t0 = 'slot0'\n"
            "value_t1 = 'slot3'\n"
            "value_t2 = 'slot2'\n"
            "value_t3 = 'slot0'\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            reporter=PlainReporter(stream),
            input_stream=io.StringIO("y\ny\nn\nn\n"),
            urlopen=moonraker_urlopen(),
        )

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t0").value,
            "'slot0'",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t1").value,
            "'slot1'",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t2").value,
            "'slot2'",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t3").value,
            "'slot3'",
        )
        state_text = (paths.config_root / "tltg_optimized_state.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("value_t", state_text)
        output = stream.getvalue()
        self.assertIn("Tool numbers do not line up with slot numbers", output)
        self.assertIn("value_t1: slot3 -> slot1", output)
        self.assertIn("value_t3: slot0 -> slot3", output)
        self.assertIn("Tool-slot mappings corrected in saved_variables.cfg.", output)

    def test_install_adds_missing_value_t_slots_for_second_box(self):
        printer_root = copy_base_runtime()
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\n"
            "box_count = 2\n"
            "enable_box = 1\n"
            "value_t0 = 'slot0'\n"
            "value_t1 = 'slot1'\n"
            "value_t2 = 'slot2'\n"
            "value_t3 = 'slot3'\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            reporter=PlainReporter(stream),
            input_stream=io.StringIO("y\nn\nn\n"),
            urlopen=moonraker_urlopen(),
        )

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        for tool in range(8):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(
                    saved_variables, "Variables", f"value_t{tool}"
                ).value,
                f"'slot{tool}'",
            )
        state_text = (paths.config_root / "tltg_optimized_state.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("value_t", state_text)
        output = stream.getvalue()
        self.assertIn("Added 4 missing QIDI Box tool-slot mapping", output)
        self.assertNotIn("Tool numbers do not line up with slot numbers", output)

    def test_install_preserves_value_t_slot_mismatches_when_declined(self):
        printer_root = copy_base_runtime()
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\n"
            "box_count = 1\n"
            "enable_box = 1\n"
            "value_t0 = 'slot0'\n"
            "value_t1 = 'slot3'\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            reporter=PlainReporter(stream),
            input_stream=io.StringIO("y\nn\nn\n"),
            urlopen=moonraker_urlopen(),
        )

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t1").value,
            "'slot3'",
        )
        output = stream.getvalue()
        self.assertIn("Tool numbers do not line up with slot numbers", output)
        self.assertIn("Tool-slot mappings left unchanged.", output)

    def test_legacy_manual_configs_reset_to_stock_before_install(self):
        printer_root = copy_base_runtime()
        (printer_root / "config/klipper-macros-qd/filament.cfg").write_text(
            "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n",
            encoding="utf-8",
        )
        (printer_root / "config/klipper-macros-qd/start_end.cfg").write_text(
            "[gcode_macro OPTIMIZED_PRINT_START_HOME]\ngcode:\n  M118 legacy\n",
            encoding="utf-8",
        )
        legacy_tree = printer_root / "config/tltg-optimized-macros"
        legacy_tree.mkdir()
        (legacy_tree / "legacy.cfg").write_text("[gcode_macro LEGACY]\ngcode:\n", encoding="utf-8")
        preserved_box = (printer_root / "config/box.cfg").read_text(encoding="utf-8")
        stream = io.StringIO()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.legacy_manual_install.shutil.which", return_value="/usr/bin/tool"):
            run_install(
                paths,
                self.manifest,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("y\ny\nn\nn\nn\n"),
                urlopen=moonraker_urlopen(),
                run=run,
            )

        output = stream.getvalue()
        self.assertIn("Legacy manually-copied optimized configs were detected.", output)
        self.assertIn("Legacy manual config backup:", output)
        self.assertIn("qidi-client.service restarted after stock config reset.", output)
        self.assertFalse((legacy_tree / "legacy.cfg").exists())
        self.assertEqual((printer_root / "config/box.cfg").read_text(encoding="utf-8"), preserved_box)
        self.assertNotIn(
            "OPTIMIZED_CUT_FILAMENT",
            (printer_root / "config/klipper-macros-qd/filament.cfg").read_text(encoding="utf-8"),
        )
        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())
        printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        self.assertIn("[include tltg-optimized-macros/*.cfg]", printer_cfg)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "restart", "qidi-client.service"], calls)
        backups = sorted(printer_root.glob("tltg-optimized-macros-before-optimize-legacy-manual-reset-*.zip"))
        self.assertEqual(len(backups), 1)

    def test_legacy_manual_configs_reset_to_firmware_04_stock_before_install(self):
        printer_root = copy_base_runtime()
        self._set_firmware_version(printer_root, "01.01.06.04")
        (printer_root / "klipper/klippy/extras/homing.py").write_bytes(
            homing_fixture_bytes("01.01.06.04")
        )
        (printer_root / "config/klipper-macros-qd/filament.cfg").write_text(
            "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n",
            encoding="utf-8",
        )
        stream = io.StringIO()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.legacy_manual_install.shutil.which", return_value="/usr/bin/tool"):
            run_install(
                paths,
                self.manifest,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("y\ny\nn\nn\nn\n"),
                urlopen=moonraker_urlopen(),
                run=run,
            )

        printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        official_filaments = (paths.config_root / "officiall_filas_list.cfg").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "closed_loop x", "query_cycle").value,
            "10",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "closed_loop x", "trigger_speed").value,
            "50",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(official_filaments, "fila25", "filament").value,
            "PA6-CF",
        )
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "restart", "qidi-client.service"], calls)
        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())

    def test_legacy_manual_config_reset_missing_firmware_snapshot_fails_before_overwrite(self):
        printer_root = copy_base_runtime()
        filament = printer_root / "config/klipper-macros-qd/filament.cfg"
        legacy_text = "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n"
        filament.write_text(legacy_text, encoding="utf-8")
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.legacy_manual_install.shutil.which", return_value="/usr/bin/tool"):
            with patch(
                "installer.runtime.legacy_manual_install.STOCK_CONFIG_ROOT",
                Path("stock/qidi-max4-defaults/missing-firmwares"),
            ):
                with self.assertRaises(Exception):
                    run_install(
                        paths,
                        self.manifest,
                        reporter=PlainReporter(io.StringIO()),
                        input_stream=io.StringIO("y\n"),
                        urlopen=moonraker_urlopen(),
                        run=lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
                    )
        self.assertEqual(filament.read_text(encoding="utf-8"), legacy_text)
        self.assertFalse((paths.config_root / "tltg_optimized_state.yaml").exists())

    def test_legacy_manual_reset_preserves_stock_kamp_symlink(self):
        printer_root = copy_base_runtime()
        (printer_root / "config/klipper-macros-qd/filament.cfg").write_text(
            "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n",
            encoding="utf-8",
        )
        kamp_target = temp_path("legacy-kamp-symlink-target-")
        kamp_settings = kamp_target / "KAMP_Settings.cfg"
        kamp_settings.write_text(
            "[gcode_macro _KAMP_Settings]\ngcode:\n  M118 preserved\n",
            encoding="utf-8",
        )
        (printer_root / "config/KAMP").symlink_to(kamp_target, target_is_directory=True)
        stream = io.StringIO()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with patch("installer.runtime.legacy_manual_install.shutil.which", return_value="/usr/bin/tool"):
            run_install(
                paths,
                self.manifest,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("y\ny\nn\nn\nn\n"),
                urlopen=moonraker_urlopen(),
                run=run,
            )

        self.assertTrue((printer_root / "config/KAMP").is_symlink())
        self.assertEqual((printer_root / "config/KAMP").readlink(), kamp_target)
        self.assertIn("M118 preserved", kamp_settings.read_text(encoding="utf-8"))
        self.assertTrue((paths.config_root / "tltg_optimized_state.yaml").exists())
        backups = sorted(printer_root.glob("tltg-optimized-macros-before-optimize-legacy-manual-reset-*.zip"))
        self.assertEqual(len(backups), 1)
        self.assertIn(["sudo", "-S", "-p", "", "systemctl", "restart", "qidi-client.service"], calls)

    def test_legacy_manual_config_prompt_decline_cancels_without_reset(self):
        printer_root = copy_base_runtime()
        filament = printer_root / "config/klipper-macros-qd/filament.cfg"
        legacy_text = "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n"
        filament.write_text(legacy_text, encoding="utf-8")
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with self.assertRaises(OperationCancelled):
            run_install(
                paths,
                self.manifest,
                reporter=PlainReporter(io.StringIO()),
                input_stream=io.StringIO("n\n"),
                urlopen=moonraker_urlopen(),
            )
        self.assertEqual(filament.read_text(encoding="utf-8"), legacy_text)
        self.assertFalse((paths.config_root / "tltg_optimized_state.yaml").exists())

    def test_active_print_stops_before_legacy_manual_config_prompt(self):
        printer_root = copy_base_runtime()
        filament = printer_root / "config/klipper-macros-qd/filament.cfg"
        legacy_text = "[gcode_macro OPTIMIZED_CUT_FILAMENT]\ngcode:\n  M118 legacy\n"
        filament.write_text(legacy_text, encoding="utf-8")
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with self.assertRaises(ActivePrintError):
            run_install(
                paths,
                self.manifest,
                reporter=PlainReporter(stream),
                input_stream=io.StringIO("y\n"),
                urlopen=moonraker_urlopen("printing"),
            )
        self.assertEqual(filament.read_text(encoding="utf-8"), legacy_text)
        self.assertNotIn("Legacy manually-copied optimized configs were detected.", stream.getvalue())

    def test_reinstall_preserves_section_restore_ledger(self):
        printer_root = copy_base_runtime()
        original_section = self._homing_override(printer_root)
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

        state_text = (paths.config_root / "tltg_optimized_state.yaml").read_text(encoding="utf-8")
        self.assertIn(original_section.splitlines()[0], state_text)
        self.assertFalse(self._homing_override_exists(printer_root))

    def test_homing_override_whitespace_and_comment_changes_are_allowed(self):
        printer_root = copy_base_runtime()
        kinematics = printer_root / "config/klipper-macros-qd/kinematics.cfg"
        kinematics.write_text(
            kinematics.read_text(encoding="utf-8")
            .replace("#axes:xy", "# local comment-only change", 1)
            .replace("G4 P400", "      G4      P400", 1),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertFalse(self._homing_override_exists(printer_root))

    def test_modified_homing_override_fails_closed(self):
        printer_root = copy_base_runtime()
        kinematics = printer_root / "config/klipper-macros-qd/kinematics.cfg"
        kinematics.write_text(
            kinematics.read_text(encoding="utf-8").replace(
                "SET_VELOCITY_LIMIT ACCEL=500",
                "SET_VELOCITY_LIMIT ACCEL=501",
                1,
            ),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with self.assertRaises(PreflightTargetsError) as exc:
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertEqual(exc.exception.report.patch_target_issues[0].reason, "user_modified")
        self.assertFalse((paths.config_root / "tltg_optimized_state.yaml").exists())

    def test_missing_patch_target_fails_closed(self):
        printer_root = copy_base_runtime()
        printer_cfg = printer_root / "config/printer.cfg"
        printer_cfg.write_text(
            printer_cfg.read_text(encoding="utf-8").replace("homing_speed: 50\n", "", 1),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with self.assertRaises(PreflightTargetsError) as exc:
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        self.assertEqual(exc.exception.report.patch_target_issues[0].reason, "missing")
