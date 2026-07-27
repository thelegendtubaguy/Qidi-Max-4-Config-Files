from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.bump_installer_version import (
    GLOBALS_PATH,
    PACKAGE_PATH,
    UPGRADE_SOURCES_PATH,
    bump_version,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class BumpInstallerVersionTests(unittest.TestCase):
    def test_bump_version_updates_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative_path in (PACKAGE_PATH, UPGRADE_SOURCES_PATH, GLOBALS_PATH):
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / relative_path, destination)

            changed = bump_version(root, "99.01.02.3")

            self.assertEqual(
                changed,
                [
                    str(PACKAGE_PATH),
                    str(UPGRADE_SOURCES_PATH),
                    str(GLOBALS_PATH),
                ],
            )
            package_text = (root / PACKAGE_PATH).read_text(encoding="utf-8")
            self.assertIn('  version: "99.01.02.3"', package_text)
            self.assertIn('    - "99.01.02.3"', package_text)
            upgrade_sources_text = (root / UPGRADE_SOURCES_PATH).read_text(encoding="utf-8")
            self.assertIn(
                '  "99.01.02.3":\n    inherits: "26.07.26.2"',
                upgrade_sources_text,
            )
            globals_text = (root / GLOBALS_PATH).read_text(encoding="utf-8")
            self.assertIn('variable_package_version: "99.01.02.3"', globals_text)

            self.assertEqual(bump_version(root, "99.01.02.3"), [])


if __name__ == "__main__":
    unittest.main()
