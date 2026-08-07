from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.manifest import load_manifest
from installer.runtime.models import SystemOptimizationCliOptions
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.state_file import load_installed_state
from installer.runtime.system_optimizations import SYSTEM_ROOT_ENV
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import REPO_ROOT, build_env, copy_base_runtime, moonraker_urlopen, temp_path


class SystemOptimizationFlowTests(unittest.TestCase):
    def test_system_optimization_dry_run_install_and_uninstall_lifecycle(self):
        printer_root = copy_base_runtime()
        system_root = _fake_system_root()
        before = _snapshot_tree(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot_id = printer_root / "boot-id"
        boot_id.write_text("boot-one\n", encoding="utf-8")
        env["TLTG_OPTIMIZED_BOOT_ID_PATH"] = str(boot_id)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )
        stream = io.StringIO()

        result = run_install(
            paths,
            manifest,
            PlainReporter(stream),
            dry_run=True,
            urlopen=moonraker_urlopen(),
            environ=env,
            system_options=SystemOptimizationCliOptions(disable_ai_detection=True),
        )

        self.assertTrue(result.dry_run)
        self.assertEqual(_snapshot_tree(system_root), before)
        self.assertFalse((printer_root / manifest.state_file).exists())
        self.assertIn("System optimizations dry-run:", stream.getvalue())

        install_responses = io.StringIO("yes\nyes\nno\nunused\n")
        base_urlopen = moonraker_urlopen()
        install_idle_checks = 0

        def install_urlopen(request, timeout=0):
            nonlocal install_idle_checks
            url = getattr(request, "full_url", str(request))
            if url == paths.moonraker_url:
                install_idle_checks += 1
            return base_urlopen(request, timeout=timeout)

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=install_responses,
            urlopen=install_urlopen,
            environ=env,
        )
        self.assertEqual(install_idle_checks, 2)
        self.assertEqual(install_responses.readline(), "unused\n")
        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
        self.assertIn(
            "deb http://deb.debian.org/debian bullseye",
            (system_root / "etc/apt/sources.list").read_text(encoding="utf-8"),
        )
        rockchip = manifest.system_optimizations.rockchip_root_sync
        dropin = system_root / rockchip.dropin.lstrip("/")
        self.assertEqual(dropin.read_text(encoding="utf-8"), rockchip.dropin_content)
        self.assertTrue(paths.host_reboot_marker_path.exists())
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertIn("rockchip_root_sync", state.system_ledger["restore_preimages"])

        run_uninstall(
            paths,
            manifest,
            compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        self.assertFalse((printer_root / manifest.state_file).exists())
        self.assertFalse((system_root / "etc/resolv.conf").is_symlink())
        self.assertEqual(
            (system_root / "etc/resolv.conf").read_text(encoding="utf-8"),
            "nameserver 114.114.114.114\n",
        )
        self.assertEqual(
            (system_root / "etc/apt/sources.list").read_text(encoding="utf-8"),
            "old apt\n",
        )
        self.assertFalse(dropin.exists())


def _fake_system_root() -> Path:
    system_root = temp_path("system-optimization-flow-")
    (system_root / "etc/resolvconf/resolv.conf.d").mkdir(parents=True)
    (system_root / "etc/resolv.conf").write_text("nameserver 114.114.114.114\n", encoding="utf-8")
    (system_root / "etc/resolvconf/resolv.conf.d/head").write_text("nameserver 8.8.8.8\n", encoding="utf-8")
    (system_root / "etc/resolvconf/resolv.conf.d/tail").write_text("", encoding="utf-8")
    (system_root / "etc/apt").mkdir(parents=True)
    (system_root / "etc/apt/sources.list").write_text("old apt\n", encoding="utf-8")
    rockchip_unit = system_root / "lib/systemd/system/rockchip.service"
    rockchip_unit.parent.mkdir(parents=True)
    rockchip_unit.write_text("[Service]\nExecStart=/etc/init.d/rockchip.sh\n", encoding="utf-8")
    rockchip_script = system_root / "etc/init.d/rockchip.sh"
    rockchip_script.parent.mkdir(parents=True)
    rockchip_script.write_text(
        "#!/bin/bash -e\n"
        "rk3308\n"
        "CHIPNAME=\"rk3208\"\n"
        "mount -o remount,sync /\n"
        "install_packages\n"
        "touch /usr/local/first_boot_flag\n",
        encoding="utf-8",
    )
    gif = system_root / "home/qidi/QIDI_Client/access/account/process.gif"
    gif.parent.mkdir(parents=True)
    gif.write_bytes(b"old")
    (system_root / "systemd").mkdir()
    for service in ("xl2tpd", "bluetooth", "algo_app.service"):
        (system_root / "systemd" / f"{service}.json").write_text(
            json.dumps({"exists": True, "service": service, "enabled": "enabled", "active": "active"}, sort_keys=True),
            encoding="utf-8",
        )
    return system_root


def _snapshot_tree(root: Path) -> dict[str, tuple[str, str | bytes, int | None]]:
    snapshot: dict[str, tuple[str, str | bytes, int | None]] = {}
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            snapshot[relative] = ("symlink", item.readlink().as_posix(), None)
        elif item.is_file():
            snapshot[relative] = ("file", item.read_bytes(), item.stat().st_mode & 0o777)
        elif item.is_dir():
            snapshot[relative] = ("dir", b"", item.stat().st_mode & 0o777)
    return snapshot
