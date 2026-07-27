from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
import zipfile
from pathlib import Path
from unittest.mock import patch

from installer.runtime import messages
from installer.runtime.auto_update import LOCK_HELD_ENV
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.state_file import load_installed_state, write_installed_state
from installer.runtime.models import SystemOptimizationCliOptions
from installer.runtime.system_optimizations import (
    SYSTEM_ROOT_ENV,
    MOONRAKER_METADATA_PATCH_MARKER,
    MOONRAKER_METADATA_OPERATION,
    SystemOptimizationError,
    SystemOptimizationRecoveryError,
    _apply_rockchip,
    _apply_service,
    _patched_moonraker_metadata_text,
    _recover_pending_rockchip_journal,
    _root_mount_options,
    _restore_file_preimage,
    recover_pending_system_optimization,
    _restore_service,
    _service_state,
    _validate_archive_members,
    resolve_policy,
)
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import REPO_ROOT, build_env, copy_base_runtime, moonraker_urlopen


class SystemOptimizationTests(unittest.TestCase):
    def test_auto_update_without_prior_policy_skips_system_optimizations(self):
        policy = resolve_policy(
            prior_ledger=None,
            reporter=PlainReporter(io.StringIO()),
            input_stream=None,
            cli_options=SystemOptimizationCliOptions(),
            auto_update_child=True,
        )

        self.assertIsNone(policy)

    def test_manifest_parses_system_optimizations(self):
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.assertIsNotNone(manifest.system_optimizations)
        system = manifest.system_optimizations
        assert system is not None
        self.assertEqual(system.dns.fallback_nameservers, ("1.1.1.1", "8.8.8.8"))
        self.assertEqual(system.qidiclient_static_gifs.archive, "system/qidiclient-static-gifs.tar.gz")
        self.assertEqual(system.moonraker_metadata_3mf.file, "/home/qidi/moonraker/moonraker/components/file_manager/metadata.py")
        self.assertEqual(system.services.disable, ("xl2tpd", "bluetooth"))

    def test_defective_rockchip_shape_is_repaired_and_owned(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        spec = manifest.system_optimizations.rockchip_root_sync
        self.assertEqual((system_root / spec.dropin.lstrip("/")).read_text(encoding="utf-8"), spec.dropin_content)
        self.assertNotIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))
        self.assertTrue(paths.host_reboot_marker_path.exists())
        state = load_installed_state(printer_root / manifest.state_file)
        preimage = state.system_ledger["restore_preimages"]["rockchip_root_sync"]
        self.assertFalse(preimage["dropin"]["exists"])
        self.assertIn("sync", preimage["mount_options"])

    def test_exact_desired_unowned_rockchip_dropin_remains_unowned(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync
        dropin = system_root / spec.dropin.lstrip("/")
        dropin.parent.mkdir(parents=True)
        dropin.write_text(spec.dropin_content, encoding="utf-8")

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        state = load_installed_state(printer_root / manifest.state_file)
        self.assertNotIn("rockchip_root_sync", state.system_ledger["restore_preimages"])
        self.assertFalse(paths.host_reboot_marker_path.exists())
        self.assertNotIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))

    def test_conflicting_symlink_and_corrected_rockchip_shapes_are_preserved(self):
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync
        for shape in ("conflict", "symlink", "corrected", "commented"):
            with self.subTest(shape=shape):
                printer_root, system_root = _runtime_with_fake_system()
                env = _rockchip_env(printer_root, system_root)
                paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
                _write_defective_rockchip(system_root)
                dropin = system_root / spec.dropin.lstrip("/")
                if shape == "conflict":
                    dropin.parent.mkdir(parents=True)
                    dropin.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
                elif shape == "symlink":
                    dropin.parent.mkdir(parents=True)
                    target = system_root / "operator-override.conf"
                    target.write_text("operator\n", encoding="utf-8")
                    dropin.symlink_to(target)
                elif shape == "corrected":
                    (system_root / spec.script.lstrip("/")).write_text("#!/bin/bash -e\ncorrected\n", encoding="utf-8")
                else:
                    (system_root / spec.script.lstrip("/")).write_text(
                        "#!/bin/bash -e\n"
                        "# rk3308\n"
                        '# CHIPNAME="rk3208"\n'
                        "# mount -o remount,sync /\n"
                        "# install_packages\n"
                        "# touch /usr/local/first_boot_flag\n",
                        encoding="utf-8",
                    )

                before = dropin.readlink() if dropin.is_symlink() else dropin.read_bytes() if dropin.exists() else None
                run_install(
                    paths,
                    manifest,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                    urlopen=moonraker_urlopen(),
                    environ=env,
                )

                after = dropin.readlink() if dropin.is_symlink() else dropin.read_bytes() if dropin.exists() else None
                self.assertEqual(after, before)
                self.assertIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))
                self.assertFalse(paths.host_reboot_marker_path.exists())

    def test_owned_rockchip_drift_reconciles_without_replacing_first_preimage(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync
        prompts = "yes\nyes\nno\nno\nno\n"
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO(prompts), urlopen=moonraker_urlopen(), environ=env)
        first = load_installed_state(printer_root / manifest.state_file).system_ledger["restore_preimages"]["rockchip_root_sync"]
        (system_root / spec.dropin.lstrip("/")).unlink()
        (system_root / "mounts/root.options").write_text("rw,relatime,sync\n", encoding="utf-8")

        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO(prompts), urlopen=moonraker_urlopen(), environ=env)

        state = load_installed_state(printer_root / manifest.state_file)
        self.assertEqual(state.system_ledger["restore_preimages"]["rockchip_root_sync"], first)
        self.assertTrue((system_root / spec.dropin.lstrip("/")).exists())
        self.assertNotIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))

    def test_rockchip_live_command_failures_are_propagated_at_each_mutation_stage(self):
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = replace(
            manifest.system_optimizations.rockchip_root_sync,
            dropin=str(REPO_ROOT / ".tmp-rockchip-command-test" / "override.conf"),
        )
        for stage, needle in (
            ("atomic-install", "install"),
            ("daemon-reload", "daemon-reload"),
            ("unit-start", "start"),
            ("remount", "remount,rw,async"),
        ):
            with self.subTest(stage=stage):
                commands = []

                def run(command, **kwargs):
                    commands.append(command)
                    return subprocess.CompletedProcess(
                        command,
                        1 if any(needle in str(part) for part in command) else 0,
                    )

                with self.assertRaises(Exception):
                    _apply_rockchip(
                        spec=spec,
                        root=Path("/"),
                        sudo_password="password",
                        run=run,
                        classification="defective_stock",
                    )
                self.assertTrue(any(any(needle in str(part) for part in command) for command in commands))

    def test_rockchip_postflight_failure_rolls_back_dropin_mount_and_marker(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync

        with patch("installer.runtime.system_optimizations._postflight_rockchip", side_effect=SystemOptimizationError("injected")):
            run_install(
                paths,
                manifest,
                PlainReporter(io.StringIO()),
                input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                urlopen=moonraker_urlopen(),
                environ=env,
            )

        self.assertFalse((system_root / spec.dropin.lstrip("/")).exists())
        self.assertIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))
        self.assertFalse(paths.host_reboot_marker_path.exists())
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertNotIn("rockchip_root_sync", state.system_ledger["restore_preimages"])

    def test_uninstall_restores_owned_rockchip_state_and_preserves_operator_drift(self):
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync
        for drift in (False, True):
            with self.subTest(drift=drift):
                printer_root, system_root = _runtime_with_fake_system()
                _write_defective_rockchip(system_root)
                env = _rockchip_env(printer_root, system_root)
                paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
                run_install(
                    paths,
                    manifest,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                    urlopen=moonraker_urlopen(),
                    environ=env,
                )
                dropin = system_root / spec.dropin.lstrip("/")
                if drift:
                    dropin.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")

                run_uninstall(
                    paths,
                    manifest,
                    compatibility,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nyes\nno\n"),
                    urlopen=moonraker_urlopen(),
                    environ=env,
                )

                if drift:
                    self.assertEqual(dropin.read_text(encoding="utf-8"), "[Service]\nExecStart=/bin/false\n")
                    self.assertTrue(paths.host_reboot_marker_path.exists())
                else:
                    self.assertFalse(dropin.exists())
                    self.assertIn("sync", _root_mount_options(spec=spec, root=system_root, run=subprocess.run))
                    self.assertFalse(paths.host_reboot_marker_path.exists())

    def test_rockchip_journal_persists_full_preimage_until_state_write(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

        with patch("installer.runtime.system_optimizations.write_installed_state", side_effect=OSError("state write failed")):
            with self.assertRaises(OSError):
                run_install(
                    paths,
                    manifest,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                    urlopen=moonraker_urlopen(),
                    environ=env,
                )

        journal_path = printer_root / ".tltg_optimized_system_journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["operation"], "rockchip_root_sync")
        self.assertIn("dropin", journal["preimage"])
        self.assertIn("mount_options", journal["preimage"])
        self.assertEqual(journal_path.stat().st_mode & 0o777, 0o600)

        tampered = json.loads(json.dumps(journal))
        tampered["preimage"]["dropin"]["path"] = "/etc/passwd"
        journal_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(SystemOptimizationRecoveryError):
            recover_pending_system_optimization(
                paths=paths,
                manifest=manifest,
                reporter=PlainReporter(io.StringIO()),
                input_stream=None,
                environ=env,
            )
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        journal_path.chmod(0o600)

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        self.assertFalse(journal_path.exists())
        self.assertNotIn(
            "sync",
            _root_mount_options(
                spec=manifest.system_optimizations.rockchip_root_sync,
                root=system_root,
                run=subprocess.run,
            ),
        )

    def test_committed_rockchip_journal_is_cleared_without_rollback(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        with patch("installer.runtime.system_optimizations._clear_committed_rockchip_journal"):
            run_install(
                paths,
                manifest,
                PlainReporter(io.StringIO()),
                input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                urlopen=moonraker_urlopen(),
                environ=env,
            )
        state = load_installed_state(printer_root / manifest.state_file)
        journal_path = printer_root / ".tltg_optimized_system_journal.json"
        dropin = system_root / manifest.system_optimizations.rockchip_root_sync.dropin.lstrip("/")

        _recover_pending_rockchip_journal(
            paths=paths,
            root=system_root,
            sudo_password=None,
            run=subprocess.run,
            ledger=state.system_ledger,
            spec=manifest.system_optimizations.rockchip_root_sync,
        )

        self.assertFalse(journal_path.exists())
        self.assertTrue(dropin.exists())
        self.assertNotIn(
            "sync",
            _root_mount_options(
                spec=manifest.system_optimizations.rockchip_root_sync,
                root=system_root,
                run=subprocess.run,
            ),
        )

    def test_rockchip_rollback_failure_is_hard_and_retains_journal(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

        with patch(
            "installer.runtime.system_optimizations._postflight_rockchip",
            side_effect=SystemOptimizationError("injected postflight"),
        ), patch(
            "installer.runtime.system_optimizations._restore_preimage_map",
            side_effect=OSError("injected rollback"),
        ):
            with self.assertRaises(SystemOptimizationRecoveryError):
                run_install(
                    paths,
                    manifest,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
                    urlopen=moonraker_urlopen(),
                    environ=env,
                )

        self.assertTrue((printer_root / ".tltg_optimized_system_journal.json").exists())

    def test_root_mount_sync_detection_uses_exact_tokens(self):
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        spec = manifest.system_optimizations.rockchip_root_sync
        root = Path(tempfile.mkdtemp(prefix="mount-token-root-"))
        state = root / "mounts/root.options"
        state.parent.mkdir(parents=True)
        for text, expected in (("rw,async,relatime\n", False), ("rw,nosync,relatime\n", False), ("rw,sync,relatime\n", True)):
            with self.subTest(text=text):
                state.write_text(text, encoding="utf-8")
                self.assertEqual("sync" in _root_mount_options(spec=spec, root=root, run=subprocess.run), expected)

    def test_moonraker_3mf_patch_preserves_plate_1_behavior(self):
        metadata = _run_patched_moonraker_extract(1)

        self.assertTrue(metadata["gcode_path"].endswith("Metadata/plate_1.gcode"))
        self.assertEqual(metadata["used_extruders"], [1])
        self.assertEqual(metadata["thumbnails"][0]["relative_path"], ".thumbs/job/plate_1.png")

    def test_moonraker_3mf_patch_selects_plate_4_files_and_thumbnail(self):
        metadata = _run_patched_moonraker_extract(4)

        self.assertTrue(metadata["gcode_path"].endswith("Metadata/plate_4.gcode"))
        self.assertEqual(metadata["used_extruders"], [4])
        self.assertEqual(metadata["thumbnails"][0]["relative_path"], ".thumbs/job/plate_4.png")

    def test_moonraker_3mf_patch_falls_back_to_plate_1_for_missing_or_invalid_slice_info(self):
        for slice_info in (None, "not xml"):
            with self.subTest(slice_info=slice_info):
                metadata = _run_patched_moonraker_extract(1, slice_info=slice_info)

                self.assertTrue(metadata["gcode_path"].endswith("Metadata/plate_1.gcode"))
                self.assertEqual(metadata["used_extruders"], [1])
                self.assertEqual(metadata["thumbnails"][0]["relative_path"], ".thumbs/job/plate_1.png")

    def test_system_optimization_prompt_reprompts_after_stray_input(self):
        output = io.StringIO()

        policy = resolve_policy(
            prior_ledger=None,
            reporter=PlainReporter(output),
            input_stream=io.StringIO("\\\n YES \n no \n"),
            cli_options=SystemOptimizationCliOptions(),
            auto_update_child=False,
        )

        self.assertEqual(policy, {"system_optimizations": "enabled", "ai_detection": "keep_enabled"})
        self.assertEqual(output.getvalue().count(messages.SYSTEM_OPTIMIZATIONS_PROMPT), 2)
        self.assertNotIn(messages.SYSTEM_OPTIMIZATIONS_SKIPPED, output.getvalue())

    def test_interactive_reinstall_prompts_when_prior_system_policy_was_disabled(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertEqual(state.system_ledger["policy"], {"system_optimizations": "disabled", "ai_detection": "unset"})

        output = io.StringIO()
        run_install(
            paths,
            manifest,
            PlainReporter(output),
            input_stream=io.StringIO("yes\nYES\nNO\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertIn(messages.SYSTEM_OPTIMIZATIONS_PROMPT, output.getvalue())
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertEqual(state.system_ledger["policy"], {"system_optimizations": "enabled", "ai_detection": "keep_enabled"})
        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())

    def test_state_file_round_trips_system_ledger(self):
        printer_root = copy_base_runtime()
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        state_path = printer_root / manifest.state_file
        state = load_installed_state(state_path)
        ledger = {"policy": {"system_optimizations": "enabled", "ai_detection": "disable"}}
        write_installed_state(state_path, type(state)(**{**state.__dict__, "system_ledger": ledger}))
        self.assertEqual(load_installed_state(state_path).system_ledger, ledger)

    def test_install_applies_system_optimizations_to_fake_root(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        output = io.StringIO()
        run_install(
            paths,
            manifest,
            PlainReporter(output),
            input_stream=io.StringIO("yes\nyes\nyes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
        self.assertEqual((system_root / "etc/resolv.conf").readlink(), Path("/run/resolvconf/resolv.conf"))
        self.assertEqual(
            (system_root / "etc/resolvconf/resolv.conf.d/tail").read_text(encoding="utf-8"),
            "nameserver 1.1.1.1\nnameserver 8.8.8.8\n",
        )
        self.assertIn("deb http://deb.debian.org/debian bullseye", (system_root / "etc/apt/sources.list").read_text(encoding="utf-8"))
        installed_gif = system_root / "home/qidi/QIDI_Client/access/account/process.gif"
        self.assertTrue(installed_gif.stat().st_size > 10)
        self.assertEqual(installed_gif.stat().st_mode & 0o777, 0o600)
        backup_dirs = sorted((system_root / "home/qidi/QIDI_Client/access").glob(".gif-backup-*"))
        self.assertEqual(len(backup_dirs), 1)
        self.assertEqual((backup_dirs[0] / "account/process.gif").read_bytes(), b"old")
        self.assertIn("Spaghetti Detection", output.getvalue())
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertEqual(state.system_ledger["policy"], {"system_optimizations": "enabled", "ai_detection": "disable"})
        self.assertIn("service_algo_app.service", state.system_ledger["restore_preimages"])
        gif_preimage = state.system_ledger["restore_preimages"]["qidiclient_static_gifs"]
        self.assertTrue(gif_preimage["backup_dir"].startswith(str(system_root / "home/qidi/QIDI_Client/access/.gif-backup-")))
        self.assertEqual(gif_preimage["files"]["account/process.gif"]["mode"], "0600")
        gif_actions = [action for action in state.system_ledger["actions"] if action["id"] == "qidiclient_static_gifs"]
        self.assertIn("installed_sha256", gif_actions[-1]["postflight"])

    def test_install_applies_moonraker_metadata_patch_when_system_hardening_declined_and_uninstall_restores(self):
        printer_root, system_root = _runtime_with_fake_system()
        metadata_path = system_root / "home/qidi/moonraker/moonraker/components/file_manager/metadata.py"
        metadata_path.parent.mkdir(parents=True)
        metadata_path.write_text(_moonraker_metadata_fixture(), encoding="utf-8")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        patched = metadata_path.read_text(encoding="utf-8")
        self.assertIn(MOONRAKER_METADATA_PATCH_MARKER, patched)
        state = load_installed_state(printer_root / manifest.state_file)
        self.assertIn(MOONRAKER_METADATA_OPERATION, state.system_ledger["restore_preimages"])

        run_uninstall(
            paths,
            manifest,
            compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertNotIn(MOONRAKER_METADATA_PATCH_MARKER, metadata_path.read_text(encoding="utf-8"))

    def test_keep_ai_detection_records_service_state_without_disabling(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_fake_service(system_root, "algo_app.service", enabled="enabled", active="active")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        state = load_installed_state(printer_root / manifest.state_file)
        ai_actions = [action for action in state.system_ledger["actions"] if action["id"] == "service_algo_app.service"]
        self.assertEqual(ai_actions[-1]["status"], "skipped_by_policy")
        self.assertEqual(ai_actions[-1]["preimage"]["enabled"], "enabled")
        self.assertEqual(ai_actions[-1]["preimage"]["active"], "active")
        self.assertNotIn("service_algo_app.service", state.system_ledger["restore_preimages"])
        self.assertEqual(json.loads((system_root / "systemd/algo_app.service.json").read_text(encoding="utf-8"))["enabled"], "enabled")

    def test_service_state_normalizes_sysv_is_enabled_notice(self):
        def run(command, **kwargs):
            if command == ["systemctl", "is-enabled", "xl2tpd"]:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout=(
                        "xl2tpd.service is not a native service, redirecting to systemd-sysv-install.\n"
                        "Executing: /lib/systemd/systemd-sysv-install is-enabled xl2tpd\n"
                        "disabled\n"
                    ),
                )
            if command == ["systemctl", "is-active", "xl2tpd"]:
                return subprocess.CompletedProcess(command, 3, stdout="inactive\n")
            raise AssertionError(command)

        state = _service_state("xl2tpd", root=Path("/"), run=run)

        self.assertTrue(state["exists"])
        self.assertEqual(state["enabled"], "disabled")
        self.assertEqual(state["active"], "inactive")

    def test_apply_service_runs_explicit_stop_fallbacks_for_sysv_service(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

        _apply_service(
            "xl2tpd",
            root=Path("/"),
            sudo_password="qiditech",
            run=run,
            preimage={"exists": True},
        )

        self.assertEqual(
            commands,
            [
                ["sudo", "-S", "-p", "", "systemctl", "disable", "--now", "xl2tpd"],
                ["sudo", "-S", "-p", "", "systemctl", "stop", "xl2tpd"],
                ["sudo", "-S", "-p", "", "/etc/init.d/xl2tpd", "stop"],
            ],
        )

    def test_apply_service_skips_init_script_fallback_for_dotted_service(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

        _apply_service(
            "algo_app.service",
            root=Path("/"),
            sudo_password="qiditech",
            run=run,
            preimage={"exists": True},
        )

        self.assertEqual(
            commands,
            [
                ["sudo", "-S", "-p", "", "systemctl", "disable", "--now", "algo_app.service"],
                ["sudo", "-S", "-p", "", "systemctl", "stop", "algo_app.service"],
            ],
        )

    def test_missing_default_service_is_recorded_without_restore_preimage(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_fake_service(system_root, "xl2tpd", exists=False, enabled="not-found", active="not-found")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        state = load_installed_state(printer_root / manifest.state_file)
        xl2tpd_actions = [action for action in state.system_ledger["actions"] if action["id"] == "service_xl2tpd"]
        self.assertEqual(xl2tpd_actions[-1]["status"], "missing")
        self.assertFalse(xl2tpd_actions[-1]["preimage"]["exists"])
        self.assertNotIn("service_xl2tpd", state.system_ledger["restore_preimages"])

    def test_auto_update_reconciles_prior_system_policy(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nyes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        (system_root / "etc/resolv.conf").unlink()
        (system_root / "etc/resolv.conf").write_text("nameserver 114.114.114.114\n", encoding="utf-8")
        (system_root / "etc/apt/sources.list").write_text("deb http://mirrors.ustc.edu.cn/debian bullseye main\n", encoding="utf-8")
        (system_root / "systemd/algo_app.service.json").write_text('{"exists": true, "service": "algo_app.service", "enabled": "enabled", "active": "active"}', encoding="utf-8")
        env[LOCK_HELD_ENV] = "1"
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
        self.assertIn("deb http://deb.debian.org/debian", (system_root / "etc/apt/sources.list").read_text(encoding="utf-8"))
        self.assertIn('"enabled": "disabled"', (system_root / "systemd/algo_app.service.json").read_text(encoding="utf-8"))

    def test_auto_update_disabled_system_policy_does_not_apply_new_operations(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_fake_service(system_root, "bluetooth", enabled="enabled", active="active")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
            system_options=SystemOptimizationCliOptions(skip_system_optimizations=True),
        )

        env[LOCK_HELD_ENV] = "1"
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertFalse((system_root / "etc/resolv.conf").is_symlink())
        self.assertEqual(json.loads((system_root / "systemd/bluetooth.json").read_text(encoding="utf-8"))["enabled"], "enabled")

    def test_auto_update_keep_ai_detection_reconciles_other_operations_only(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_fake_service(system_root, "algo_app.service", enabled="enabled", active="active")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        (system_root / "etc/resolv.conf").unlink()
        (system_root / "etc/resolv.conf").write_text("nameserver 114.114.114.114\n", encoding="utf-8")
        _write_fake_service(system_root, "algo_app.service", enabled="enabled", active="active")
        env[LOCK_HELD_ENV] = "1"
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
        self.assertEqual(json.loads((system_root / "systemd/algo_app.service.json").read_text(encoding="utf-8"))["enabled"], "enabled")

    def test_auto_update_enabled_policy_applies_operation_missing_from_prior_ledger(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_fake_service(system_root, "bluetooth", enabled="enabled", active="active")
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        state_path = printer_root / manifest.state_file
        state = load_installed_state(state_path)
        ledger = dict(state.system_ledger)
        ledger["restore_preimages"] = dict(ledger["restore_preimages"])
        ledger["restore_preimages"].pop("service_bluetooth", None)
        ledger["actions"] = [action for action in ledger["actions"] if action["id"] != "service_bluetooth"]
        write_installed_state(state_path, type(state)(**{**state.__dict__, "system_ledger": ledger}))
        _write_fake_service(system_root, "bluetooth", enabled="enabled", active="active")

        env[LOCK_HELD_ENV] = "1"
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        self.assertEqual(json.loads((system_root / "systemd/bluetooth.json").read_text(encoding="utf-8"))["enabled"], "disabled")
        updated = load_installed_state(state_path)
        self.assertIn("service_bluetooth", updated.system_ledger["restore_preimages"])

    def test_auto_update_enabled_policy_applies_rockchip_and_records_checksum_bound_marker(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nno\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
        _write_defective_rockchip(system_root)
        auto_state = printer_root / "config/tltg_optimized_auto_update_state.json"
        auto_state.write_text(json.dumps({"latest_checksum": "a" * 64}), encoding="utf-8")
        env[LOCK_HELD_ENV] = "1"

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        marker = json.loads(paths.host_reboot_marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["source"], "auto_update_child")
        self.assertEqual(marker["auto_update_checksum_before"], "a" * 64)
        self.assertTrue((system_root / manifest.system_optimizations.rockchip_root_sync.dropin.lstrip("/")).exists())

    def test_auto_update_disabled_policy_preserves_defective_rockchip_state(self):
        printer_root, system_root = _runtime_with_fake_system()
        _write_defective_rockchip(system_root)
        env = _rockchip_env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
            system_options=SystemOptimizationCliOptions(skip_system_optimizations=True),
        )
        env[LOCK_HELD_ENV] = "1"

        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=None,
            urlopen=moonraker_urlopen(),
            environ=env,
        )

        spec = manifest.system_optimizations.rockchip_root_sync
        self.assertFalse((system_root / spec.dropin.lstrip("/")).exists())
        self.assertTrue("sync" in _root_mount_options(spec=spec, root=system_root, run=subprocess.run))
        self.assertFalse(paths.host_reboot_marker_path.exists())

    def test_real_root_file_restore_replaces_current_target_symlink(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(printer_root, moonraker_url="http://moonraker.invalid"))
        with tempfile.TemporaryDirectory(prefix="system-preimage-", dir=REPO_ROOT) as tmp:
            tmp_path = Path(tmp)
            backup = tmp_path / "resolv.conf.backup"
            backup.write_text("nameserver 114.114.114.114\n", encoding="utf-8")
            restore_path = tmp_path / "resolv.conf"
            restore_path.symlink_to("current-resolv.conf")
            commands = []

            def run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)

            _restore_file_preimage(
                {
                    "path": str(restore_path),
                    "exists": True,
                    "type": "file",
                    "mode": "0644",
                    "backup_path": str(backup),
                },
                paths=paths,
                root=Path("/"),
                sudo_password="qiditech",
                run=run,
            )

            install_commands = [command for command in commands if command[:7] == ["sudo", "-S", "-p", "", "install", "-D", "-m"]]
            mv_commands = [command for command in commands if command[:6] == ["sudo", "-S", "-p", "", "mv", "-f"]]
            rm_commands = [command for command in commands if command[:6] == ["sudo", "-S", "-p", "", "rm", "-f"]]
            self.assertEqual(len(install_commands), 1)
            self.assertEqual(install_commands[0][7], "0644")
            self.assertEqual(install_commands[0][8], str(backup))
            self.assertTrue(install_commands[0][9].startswith(f"{restore_path}.tltg-restore-"))
            self.assertEqual(len(mv_commands), 1)
            self.assertEqual(mv_commands[0][7], str(restore_path))
            self.assertTrue(mv_commands[0][6].startswith(f"{restore_path}.tltg-restore-"))
            self.assertNotIn(["sudo", "-S", "-p", "", "rm", "-f", str(restore_path)], rm_commands)

    def test_restore_service_skips_units_missing_at_restore_time(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(command, 1, stdout="not-found\n")
            if command[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(command, 3, stdout="unknown\n")
            return subprocess.CompletedProcess(command, 0)

        _restore_service(
            {"service": "xl2tpd", "exists": True, "enabled": "enabled", "active": "active"},
            root=Path("/"),
            sudo_password="qiditech",
            run=run,
        )

        self.assertEqual(commands, [["systemctl", "is-enabled", "xl2tpd"], ["systemctl", "is-active", "xl2tpd"]])

    def test_qidiclient_archive_validation_rejects_unsafe_members(self):
        cases = [
            _tar_member("/account/process.gif"),
            _tar_member("account/../evil.gif"),
            _tar_member("account/link.gif", tarfile.SYMTYPE),
            _tar_member("account/device.gif", tarfile.CHRTYPE),
            _tar_member("unexpected/process.gif"),
        ]
        for member in cases:
            with self.subTest(member=member.name, type=member.type):
                with self.assertRaises(SystemOptimizationError):
                    _validate_archive_members([member])

    def test_uninstall_can_restore_system_preimages(self):
        printer_root, system_root = _runtime_with_fake_system()
        env = _env(printer_root, system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_install(
            paths,
            manifest,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("yes\nyes\nyes\nno\nno\n"),
            urlopen=moonraker_urlopen(),
            environ=env,
        )
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
        self.assertEqual((system_root / "etc/resolv.conf").read_text(encoding="utf-8"), "nameserver 114.114.114.114\n")
        self.assertEqual((system_root / "etc/apt/sources.list").read_text(encoding="utf-8"), "old apt\n")


def _run_patched_moonraker_extract(plate_index: int, *, slice_info: str | None = "valid") -> dict:
    namespace: dict = {}
    exec(_patched_moonraker_metadata_text(_moonraker_metadata_fixture()), namespace)
    with tempfile.TemporaryDirectory(prefix="moonraker-3mf-") as tmp:
        archive_path = Path(tmp) / "job.gcode.3mf"
        with zipfile.ZipFile(archive_path, "w") as archive:
            if slice_info == "valid":
                archive.writestr(
                    "Metadata/slice_info.config",
                    f'<config><plate><metadata key="index" value="{plate_index}"/><metadata key="prediction" value="1"/><metadata key="weight" value="2"/><metadata key="nozzle_diameters" value="0.4"/></plate></config>',
                )
            elif slice_info is not None:
                archive.writestr("Metadata/slice_info.config", slice_info)
            archive.writestr(f"Metadata/plate_{plate_index}.json", json.dumps({"filament_ids": [plate_index]}))
            archive.writestr(f"Metadata/plate_{plate_index}.gcode", "; generated by OrcaSlicer 2.0 on\nG1 X1\n")
        return namespace["extract_3mf"](str(archive_path), "/home/qidi/printer_data/gcodes/job.gcode")


def _moonraker_metadata_fixture() -> str:
    return '''
import json
import os
import sys
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET

class Logger:
    def info(self, message):
        pass

logger = Logger()
_3MF_MODEL_PATH = "Metadata/plate_1.gcode"
_3MF_THUMB_PATH = "Metadata/plate_1_big.png"
_3MF_THUMB_SMALL_PATH = "Metadata/plate_1_small.png"
_3MF_THUMB_PATH_ALL = "Metadata/plate_1.png"
_3MF_SLICE_INFO_PATH = "Metadata/slice_info.config"
_3MF_PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"
_3MF_PLATE_1_PATH = "Metadata/plate_1.json"
_3MF_PLATE_1_GCODE_PATH = "Metadata/plate_1.gcode"

def generate_thumb_path(input_filepath: str, root_path: str, plant_index: int) -> str:
    root_path = os.path.join(root_path, '')
    if not input_filepath.startswith(root_path):
        return ""
    relative_path = input_filepath[len(root_path):]
    filename_with_ext = os.path.basename(relative_path)
    base_name = os.path.splitext(filename_with_ext)[0]
    sub_dir = os.path.dirname(relative_path)
    thumb_path = os.path.join(".thumbs", sub_dir, base_name, f"plate_{plant_index}.png")
    return os.path.normpath(thumb_path)

class Slicer:
    def parse_gcode_end_byte(self):
        return 2
    def parse_gcode_start_byte(self):
        return 1

def get_slicer(path):
    return Slicer(), {"gcode_path": path}

def extract_3mf(_3mf_path: str, dest_path: str) -> None:
    metadata = {}
    if not os.path.isfile(_3mf_path):
        logger.info(f"3MF file Not Found: {_3mf_path}")
        sys.exit(-1)
    plate_num = 1
    try:
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            tmp_xml_path = ""
            tmp_project_settings_path = ""
            tmp_plate_1_path = ""
            tmp_plate_1_gcode_path = ""
            with zipfile.ZipFile(_3mf_path) as zf:
                if _3MF_THUMB_PATH_ALL in zf.namelist():
                    tmp_thumb_all_path = zf.extract(
                        _3MF_THUMB_PATH_ALL, path=tmp_dir_name
                    )
                if _3MF_SLICE_INFO_PATH in zf.namelist():
                    tmp_xml_path = zf.extract(
                        _3MF_SLICE_INFO_PATH, path=tmp_dir_name
                    )
                if _3MF_PROJECT_SETTINGS_PATH in zf.namelist():
                    tmp_project_settings_path = zf.extract(
                        _3MF_PROJECT_SETTINGS_PATH, path=tmp_dir_name
                    )
                if _3MF_PLATE_1_PATH in zf.namelist():
                    tmp_plate_1_path = zf.extract(
                        _3MF_PLATE_1_PATH, path=tmp_dir_name
                    )
                if _3MF_PLATE_1_GCODE_PATH in zf.namelist():
                    tmp_plate_1_gcode_path = zf.extract(
                        _3MF_PLATE_1_GCODE_PATH, path=tmp_dir_name
                    )
            if os.path.exists(tmp_xml_path):
                with open(tmp_xml_path, "r", encoding="utf-8") as file:
                    xml_data = file.read()
                plate = ET.fromstring(xml_data).find("plate")
                for metadata_plate in plate.findall('metadata'):
                    if metadata_plate.get('key') == 'prediction':
                        prediction = metadata_plate.get('value')
                    elif metadata_plate.get('key') == 'weight':
                        weight = metadata_plate.get('value')
                    elif metadata_plate.get('key') == 'nozzle_diameters':
                        nozzle_diameters = metadata_plate.get('value')
                metadata["estimated_time"] = int(prediction)__SP__
                metadata["filament_weight_total"] = float(weight)__SP3__
                metadata["nozzle_diameter"] = float(nozzle_diameters)__SP__
                metadata["filament_total"] = sum(
                    float(filament.get("used_m", 0.0))
                    for filament in plate.findall("filament")
                ) * 1000
                plate_num = len(ET.fromstring(xml_data).findall("plate"))
            if os.path.exists(tmp_project_settings_path):
                pass
            if os.path.exists(tmp_plate_1_path):
                with open(tmp_plate_1_path, "r", encoding="utf-8") as file:
                    plate_1_data = file.read()
                plate_json = json.loads(plate_1_data)
                metadata['used_extruders'] = plate_json.get('filament_ids')
            if os.path.exists(tmp_plate_1_gcode_path):
                slicer, ident = get_slicer(tmp_plate_1_gcode_path)
                metadata.update(ident)
                for method_name in ["gcode_end_byte", "gcode_start_byte"]:
                    func = getattr(slicer, "parse_" + method_name, None)
                    if callable(func):
                        result = func()
                        if result is not None:
                            metadata[method_name] = result
    except Exception:
        raise
    data = []
    data.append({
        'width': 512, 'height': 512,
        'size': 0,
        'relative_path': generate_thumb_path(dest_path, "/home/qidi/printer_data/gcodes/", 1)
    })
    metadata['thumbnails'] = data
    metadata['size'] = os.path.getsize(_3mf_path)
    metadata['modified'] = os.path.getmtime(_3mf_path)
    metadata['uuid'] = str(uuid.uuid4())
    return metadata
'''.replace("__SP3__", "   ").replace("__SP__", " ")


def _runtime_with_fake_system() -> tuple[Path, Path]:
    printer_root = copy_base_runtime()
    system_root = Path(tempfile.mkdtemp(prefix="system-root-"))
    (system_root / "etc/resolvconf/resolv.conf.d").mkdir(parents=True)
    (system_root / "etc/resolv.conf").write_text("nameserver 114.114.114.114\n", encoding="utf-8")
    (system_root / "etc/resolvconf/resolv.conf.d/head").write_text("nameserver 8.8.8.8\n", encoding="utf-8")
    (system_root / "etc/resolvconf/resolv.conf.d/tail").write_text("", encoding="utf-8")
    (system_root / "etc/apt").mkdir(parents=True)
    (system_root / "etc/apt/sources.list").write_text("old apt\n", encoding="utf-8")
    gif = system_root / "home/qidi/QIDI_Client/access/account/process.gif"
    gif.parent.mkdir(parents=True)
    gif.write_bytes(b"old")
    gif.chmod(0o600)
    return printer_root, system_root


def _env(printer_root: Path, system_root: Path) -> dict[str, str]:
    env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
    env[SYSTEM_ROOT_ENV] = str(system_root)
    return env


def _rockchip_env(printer_root: Path, system_root: Path) -> dict[str, str]:
    env = _env(printer_root, system_root)
    boot_id = printer_root / "boot-id"
    boot_id.write_text("boot-one\n", encoding="utf-8")
    env["TLTG_OPTIMIZED_BOOT_ID_PATH"] = str(boot_id)
    return env


def _write_defective_rockchip(system_root: Path) -> None:
    unit = system_root / "lib/systemd/system/rockchip.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\nExecStart=/etc/init.d/rockchip.sh\n", encoding="utf-8")
    script = system_root / "etc/init.d/rockchip.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/bin/bash -e\n"
        "rk3308\n"
        'CHIPNAME="rk3208"\n'
        "mount -o remount,sync /\n"
        "install_packages\n"
        "touch /usr/local/first_boot_flag\n",
        encoding="utf-8",
    )


def _write_fake_service(
    system_root: Path,
    service: str,
    *,
    exists: bool = True,
    enabled: str = "enabled",
    active: str = "active",
) -> None:
    path = system_root / "systemd" / f"{service}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"exists": exists, "service": service, "enabled": enabled, "active": active}, sort_keys=True),
        encoding="utf-8",
    )


def _tar_member(name: str, member_type: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = member_type
    if member_type == tarfile.REGTYPE:
        member.size = 1
    return member


if __name__ == "__main__":
    unittest.main()
