from __future__ import annotations

import io
import shutil
import unittest

from installer.runtime import klipper_cfg
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.errors import InstalledPackageValidationError
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import (
    MOONRAKER_QUERY_URL,
    REPO_ROOT,
    build_env,
    copy_base_runtime,
    homing_fixture_bytes,
    moonraker_urlopen,
)


class UninstallFlowTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )

    def _homing_override(self, printer_root):
        return klipper_cfg.resolve_unique_section(
            (printer_root / "config/klipper-macros-qd/kinematics.cfg").read_text(
                encoding="utf-8"
            ),
            "homing_override",
        ).text

    def _set_firmware_version(self, printer_root, version):
        (printer_root / "firmware_manifest.json").write_text(
            f'{"{"}"SOC": {{"version": "{version}"}}{"}"}\n',
            encoding="utf-8",
        )

    def _overlay_stock_snapshot(self, printer_root, version):
        source = REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares" / version / "config"
        shutil.copytree(source, printer_root / "config", dirs_exist_ok=True)
        if version in {"01.01.06.04", "01.01.06.05"}:
            (printer_root / "klipper/klippy/extras/homing.py").write_bytes(
                homing_fixture_bytes(version)
            )

    def _install_first(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        return printer_root

    def test_happy_path_uninstall(self):
        printer_root = copy_base_runtime()
        original_homing_override = self._homing_override(printer_root)
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())
        stream = io.StringIO()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        result = run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(stream),
            urlopen=moonraker_urlopen(),
        )
        self.assertFalse((paths.config_root / "tltg_optimized_state.yaml").exists())
        self.assertFalse((paths.config_root / "tltg-optimized-macros").exists())
        printer_cfg = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        self.assertNotIn("[include tltg-optimized-macros/*.cfg]", printer_cfg)
        self.assertIn("homing_speed: 50", printer_cfg)
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "z_tilt", "speed").value,
            "150",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "bed_mesh", "speed").value,
            "150",
        )
        self.assertIn("on_error_gcode: CANCEL_PRINT", printer_cfg)
        self.assertEqual(self._homing_override(printer_root), original_homing_override)
        output = stream.getvalue()
        self.assertIn("stage 1/5", output)
        self.assertIn("Uninstalled.", output)
        self.assertIn("Klipper service process restarted and verified.", output)
        self.assertTrue(result.backup_zip_path.exists())

    def test_uninstall_restores_hotend_fan_interval_for_each_firmware(self):
        stock_by_firmware = {
            "01.01.06.03": "0.0015",
            "01.01.06.04": "0.0015",
            "01.01.06.05": "0.0005",
        }
        for firmware, stock_value in stock_by_firmware.items():
            with self.subTest(firmware=firmware):
                printer_root = copy_base_runtime()
                if firmware != "01.01.06.03":
                    self._set_firmware_version(printer_root, firmware)
                    self._overlay_stock_snapshot(printer_root, firmware)
                paths = resolve_runtime_paths(
                    bundle_root=REPO_ROOT,
                    environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
                )
                run_install(
                    paths,
                    self.manifest,
                    PlainReporter(io.StringIO()),
                    urlopen=moonraker_urlopen(),
                )
                printer_cfg_path = paths.config_root / "printer.cfg"
                self.assertEqual(
                    klipper_cfg.resolve_unique_option(
                        printer_cfg_path.read_text(encoding="utf-8"),
                        "heater_fan hotend_fan",
                        "tachometer_poll_interval",
                    ).value,
                    "0.00075",
                )

                run_uninstall(
                    paths,
                    self.manifest,
                    self.compatibility,
                    PlainReporter(io.StringIO()),
                    urlopen=moonraker_urlopen(),
                )
                self.assertEqual(
                    klipper_cfg.resolve_unique_option(
                        printer_cfg_path.read_text(encoding="utf-8"),
                        "heater_fan hotend_fan",
                        "tachometer_poll_interval",
                    ).value,
                    stock_value,
                )

    def test_uninstall_preserves_user_modified_hotend_fan_interval(self):
        firmware = "01.01.06.05"
        printer_root = copy_base_runtime()
        self._set_firmware_version(printer_root, firmware)
        self._overlay_stock_snapshot(printer_root, firmware)
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
        )
        printer_cfg_path = paths.config_root / "printer.cfg"
        printer_cfg_path.write_text(
            klipper_cfg.set_option_value(
                printer_cfg_path.read_text(encoding="utf-8"),
                "heater_fan hotend_fan",
                "tachometer_poll_interval",
                "0.0008",
            ),
            encoding="utf-8",
        )

        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                printer_cfg_path.read_text(encoding="utf-8"),
                "heater_fan hotend_fan",
                "tachometer_poll_interval",
            ).value,
            "0.0008",
        )

    def test_uninstall_preserves_user_modified_homing_override(self):
        printer_root = self._install_first()
        kinematics = printer_root / "config/klipper-macros-qd/kinematics.cfg"
        modified_section = "[homing_override]\ngcode:\n  M118 user modified homing override\n"
        kinematics.write_text(
            klipper_cfg.append_section(
                kinematics.read_text(encoding="utf-8"),
                modified_section,
            ),
            encoding="utf-8",
        )
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(),
        )
        self.assertEqual(self._homing_override(printer_root), modified_section)

    def test_uninstall_fails_closed_when_markers_exist_without_valid_state(self):
        printer_root = copy_base_runtime()
        (printer_root / "config/tltg-optimized-macros").mkdir(parents=True)
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        with self.assertRaises(InstalledPackageValidationError):
            run_uninstall(
                paths,
                self.manifest,
                self.compatibility,
                PlainReporter(io.StringIO()),
                urlopen=moonraker_urlopen(),
            )
