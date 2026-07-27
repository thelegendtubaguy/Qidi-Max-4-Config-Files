from __future__ import annotations

import copy
import unittest
from pathlib import Path

from installer.runtime.manifest import ManifestValidationError, parse_manifest
import yaml
from installer.tests.helpers import REPO_ROOT


class RockchipManifestTests(unittest.TestCase):
    def setUp(self):
        self.raw = yaml.safe_load((REPO_ROOT / "installer/package.yaml").read_text(encoding="utf-8"))

    def test_complete_rockchip_operation_parses_without_firmware_binding(self):
        manifest = parse_manifest(copy.deepcopy(self.raw))
        spec = manifest.system_optimizations.rockchip_root_sync

        self.assertEqual(spec.id, "rockchip_root_sync")
        self.assertEqual(spec.mount_target, "/")
        self.assertEqual(spec.desired_exec_start, "/bin/true")
        self.assertNotIn("firmware", self.raw["system_optimizations"]["rockchip_root_sync"])

    def test_missing_rockchip_field_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        del raw["system_optimizations"]["rockchip_root_sync"]["script"]

        with self.assertRaises(ManifestValidationError):
            parse_manifest(raw)

    def test_relative_rockchip_path_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["system_optimizations"]["rockchip_root_sync"]["dropin"] = "etc/override.conf"

        with self.assertRaises(ManifestValidationError):
            parse_manifest(raw)

    def test_malformed_dropin_content_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["system_optimizations"]["rockchip_root_sync"]["dropin_content"] = "[Service]\nExecStart=/bin/true\n"

        with self.assertRaises(ManifestValidationError):
            parse_manifest(raw)

    def test_duplicate_structural_marker_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        markers = raw["system_optimizations"]["rockchip_root_sync"]["defective_script_markers"]
        markers.append(markers[-1])

        with self.assertRaises(ManifestValidationError):
            parse_manifest(raw)

    def test_ordered_marker_must_be_structurally_required(self):
        raw = copy.deepcopy(self.raw)
        raw["system_optimizations"]["rockchip_root_sync"]["ordered_script_markers"].append("not-required")

        with self.assertRaises(ManifestValidationError):
            parse_manifest(raw)


if __name__ == "__main__":
    unittest.main()
