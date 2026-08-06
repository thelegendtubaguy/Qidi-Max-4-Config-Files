#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    path_check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check_gcode_paths.py")],
        cwd=REPO_ROOT,
        check=False,
    )
    if path_check.returncode != 0:
        return path_check.returncode

    suite = unittest.defaultTestLoader.discover(
        str(REPO_ROOT / "installer/tests"),
        top_level_dir=str(REPO_ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
