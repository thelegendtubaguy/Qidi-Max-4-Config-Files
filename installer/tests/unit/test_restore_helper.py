from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from installer.runtime import backup as backup_runtime
from installer.runtime import restore_helper as restore_helper_runtime
from installer.runtime.backup import (
    BackupArchiveError,
    create_config_backup,
    load_backup_snapshot,
    load_external_backup_entries,
    snapshot_runtime_tree,
)
from installer.runtime.errors import RollbackFailedError
from installer.runtime.path_safety import PathSafetyError
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.restore_helper import RestoreHelperError, run_restore_helper
from installer.runtime.runner import run_install
from installer.tests.helpers import (
    REPO_ROOT,
    build_env,
    copy_base_runtime,
    homing_fixture_bytes,
    MOONRAKER_QUERY_URL,
    moonraker_urlopen,
    temp_path,
)


class RestoreHelperTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")

    def _paths(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        return printer_root, paths

    def _source_backup(self):
        printer_root, paths = self._paths()
        result = run_install(
            paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen()
        )
        assert result.backup_zip_path is not None
        return printer_root, paths, result.backup_zip_path

    def _external_entries(self):
        return {patch.id: patch.destination for patch in self.manifest.install.source_patches}

    def _rewrite_archive(self, source: Path, mutate):
        members: list[tuple[zipfile.ZipInfo, bytes]] = []
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                clone.compress_type = zipfile.ZIP_DEFLATED
                clone.external_attr = info.external_attr
                members.append((clone, archive.read(info)))
        mutate(members)
        target = temp_path("restore-archive-") / source.name
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for info, value in members:
                archive.writestr(info, value)
        return target

    def _external_manifest(self, members):
        for info, value in members:
            if info.filename == ".tltg-external-files.json":
                return json.loads(value.decode("utf-8"))
        self.fail("external manifest missing from source-inclusive fixture")

    def _replace_external_manifest(self, members, document):
        for index, (info, _) in enumerate(members):
            if info.filename == ".tltg-external-files.json":
                members[index] = (info, json.dumps(document, sort_keys=True).encode("utf-8"))
                return
        self.fail("external manifest missing from source-inclusive fixture")

    def _assert_restore_rejected_before_write(self, paths, archive, expected):
        before_config = snapshot_runtime_tree(
            printer_data_root=paths.printer_data_root, source_directory="config"
        )
        source = paths.managed_klipper_root / "klippy/extras/homing.py"
        before_source = source.read_bytes() if not source.is_symlink() else None
        before_source_link = source.readlink() if source.is_symlink() else None
        before_marker = (
            paths.restart_marker_path.read_bytes()
            if paths.restart_marker_path.exists()
            else None
        )
        with self.assertRaises(expected):
            run_restore_helper(
                paths,
                self.manifest,
                stream=io.StringIO(),
                input_stream=io.StringIO("RESTORE\n"),
                backup_path=str(archive),
                urlopen=moonraker_urlopen(),
            )
        self.assertEqual(
            snapshot_runtime_tree(printer_data_root=paths.printer_data_root, source_directory="config"),
            before_config,
        )
        if before_source_link is None:
            self.assertFalse(source.is_symlink())
            self.assertEqual(source.read_bytes(), before_source)
        else:
            self.assertTrue(source.is_symlink())
            self.assertEqual(source.readlink(), before_source_link)
        self.assertEqual(
            paths.restart_marker_path.read_bytes()
            if paths.restart_marker_path.exists()
            else None,
            before_marker,
        )

    def test_source_inclusive_archive_restores_config_and_external_source(self):
        printer_root, paths, backup_zip = self._source_backup()
        expected_config = load_backup_snapshot(
            backup_zip_path=backup_zip, source_directory="config"
        )
        expected_source = homing_fixture_bytes("01.01.06.03")
        (printer_root / "config/printer.cfg").write_text("[printer]\nmodified: yes\n", encoding="utf-8")
        source = paths.managed_klipper_root / "klippy/extras/homing.py"

        rc = run_restore_helper(
            paths,
            self.manifest,
            stream=io.StringIO(),
            input_stream=io.StringIO("RESTORE\nY\n"),
            backup_path=str(backup_zip),
            urlopen=moonraker_urlopen(),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(
            snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config"),
            expected_config,
        )
        self.assertEqual(source.read_bytes(), expected_source)
        self.assertFalse(paths.restart_marker_path.exists())

    def test_cross_firmware_external_archives_are_rejected_before_write(self):
        patch = self.manifest.install.source_patches[0]
        for archive_firmware, live_firmware in (
            ("01.01.06.03", "01.01.06.04"),
            ("01.01.06.04", "01.01.06.03"),
        ):
            with self.subTest(
                archive_firmware=archive_firmware,
                live_firmware=live_firmware,
            ):
                printer_root, paths = self._paths()
                source = paths.managed_klipper_root / patch.destination
                source.write_bytes(homing_fixture_bytes(archive_firmware))
                archive = create_config_backup(
                    printer_data_root=printer_root,
                    source_directory="config",
                    backup_label=(
                        "tltg-optimized-macros-before-optimize-"
                        f"{archive_firmware}-26.07.26.1-20260726T000000Z"
                    ),
                    external_files=((patch.id, patch.destination, source),),
                    external_firmware=archive_firmware,
                )

                (printer_root / "firmware_manifest.json").write_text(
                    json.dumps({"SOC": {"version": live_firmware}}),
                    encoding="utf-8",
                )
                source.write_bytes(homing_fixture_bytes(live_firmware))

                self._assert_restore_rejected_before_write(
                    paths, archive, RestoreHelperError
                )

    def test_explicit_legacy_config_only_archive_restores_without_external_manifest(self):
        printer_root, paths = self._paths()
        expected_config = snapshot_runtime_tree(
            printer_data_root=printer_root, source_directory="config"
        )
        archive = create_config_backup(
            printer_data_root=printer_root,
            source_directory="config",
            backup_label="tltg-optimized-macros-before-optimize-01.01.06.03-26.07.13.2-20260726T000000Z",
        )
        source = paths.managed_klipper_root / "klippy/extras/homing.py"
        before_source = source.read_bytes()
        (printer_root / "config/printer.cfg").write_text("[printer]\nmodified: yes\n", encoding="utf-8")

        rc = run_restore_helper(
            paths,
            self.manifest,
            stream=io.StringIO(),
            input_stream=io.StringIO("RESTORE\n"),
            backup_path=str(archive),
            urlopen=moonraker_urlopen(),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(
            snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config"),
            expected_config,
        )
        self.assertEqual(source.read_bytes(), before_source)
        self.assertFalse(paths.restart_marker_path.exists())

    def test_current_format_archive_without_external_manifest_is_rejected_before_write(self):
        _, paths, backup_zip = self._source_backup()

        def strip_manifest(members):
            members[:] = [
                (info, value)
                for info, value in members
                if info.filename != ".tltg-external-files.json"
            ]

        self._assert_restore_rejected_before_write(
            paths, self._rewrite_archive(backup_zip, strip_manifest), BackupArchiveError
        )

    def test_external_manifest_rejects_duplicate_identity_destination_and_member(self):
        _, _, backup_zip = self._source_backup()
        allowed = self._external_entries()
        _, source_entries = load_external_backup_entries(
            backup_zip_path=backup_zip, allowed_entries=allowed, require_manifest=True
        )
        self.assertEqual(len(source_entries), 1)
        original_id, original_destination, original_value, original_mode = source_entries[0]
        other = "other"
        two_allowed = {
            original_id: original_destination,
            other: "klippy/extras/other.py",
        }

        for name, second in (
            ("duplicate id", {"id": original_id, "destination": "klippy/extras/other.py", "member": f"external/{original_id}"}),
            ("duplicate destination", {"id": other, "destination": original_destination, "member": f"external/{other}"}),
            ("duplicate member", {"id": other, "destination": "klippy/extras/other.py", "member": f"external/{original_id}"}),
        ):
            with self.subTest(name=name):
                def mutate(members, second=second):
                    document = self._external_manifest(members)
                    entry = dict(document["files"][0])
                    entry.update(second)
                    document["files"].append(entry)
                    self._replace_external_manifest(members, document)
                    if second["member"] == f"external/{other}":
                        members.append((zipfile.ZipInfo(second["member"]), original_value))

                archive = self._rewrite_archive(backup_zip, mutate)
                with self.assertRaises(BackupArchiveError):
                    load_external_backup_entries(
                        backup_zip_path=archive,
                        allowed_entries=two_allowed,
                        require_manifest=True,
                    )

    def test_external_manifest_rejects_missing_extra_nonregular_and_symlink_members(self):
        _, _, backup_zip = self._source_backup()
        allowed = self._external_entries()

        cases = []
        cases.append(("missing member", lambda members: members.__setitem__(slice(None), [(info, value) for info, value in members if info.filename != "external/qidi_homing"])))
        cases.append(("extra member", lambda members: members.append((zipfile.ZipInfo("external/extra"), b"extra"))))

        def nonregular(members):
            for index, (info, value) in enumerate(members):
                if info.filename == "external/qidi_homing":
                    info.external_attr = stat.S_IFDIR << 16
                    members[index] = (info, value)
                    return
            self.fail("external payload missing")

        def symlink(members):
            for index, (info, value) in enumerate(members):
                if info.filename == "external/qidi_homing":
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    members[index] = (info, value)
                    return
            self.fail("external payload missing")

        cases.extend((("non-regular member", nonregular), ("symlink member", symlink)))
        for name, mutate in cases:
            with self.subTest(name=name):
                archive = self._rewrite_archive(backup_zip, mutate)
                with self.assertRaises(BackupArchiveError):
                    load_external_backup_entries(
                        backup_zip_path=archive, allowed_entries=allowed, require_manifest=True
                    )

    def test_external_manifest_rejects_malformed_hash_mode_payload_mismatch_and_paths(self):
        _, _, backup_zip = self._source_backup()
        allowed = self._external_entries()

        def modify(field, value):
            def mutate(members):
                document = self._external_manifest(members)
                document["files"][0][field] = value
                self._replace_external_manifest(members, document)
            return mutate

        cases = (
            ("malformed hash", modify("sha256", "not-a-hash")),
            ("malformed mode", modify("mode", 0o1000)),
            ("payload hash mismatch", modify("sha256", "0" * 64)),
            ("absolute member", modify("member", "/external/qidi_homing")),
            ("traversing member", modify("member", "external/../qidi_homing")),
            ("absolute destination", modify("destination", "/klippy/extras/homing.py")),
            ("traversing destination", modify("destination", "klippy/extras/../homing.py")),
            ("unsupported destination", modify("destination", "klippy/extras/other.py")),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                archive = self._rewrite_archive(backup_zip, mutate)
                with self.assertRaises(BackupArchiveError):
                    load_external_backup_entries(
                        backup_zip_path=archive, allowed_entries=allowed, require_manifest=True
                    )

    def test_external_live_drift_and_symlink_are_rejected_before_config_write(self):
        for name, make_target in (
            ("drift", lambda target: target.write_bytes(b"drift")),
            ("symlink", lambda target: (target.unlink(), target.symlink_to("/tmp/not-a-homing.py"))),
        ):
            with self.subTest(name=name):
                _, paths, backup_zip = self._source_backup()
                target = paths.managed_klipper_root / "klippy/extras/homing.py"
                make_target(target)
                self._assert_restore_rejected_before_write(
                    paths, backup_zip, (RestoreHelperError, PathSafetyError)
                )

    def test_external_partial_write_failure_rolls_back_config_source_and_marker(self):
        printer_root, paths, backup_zip = self._source_backup()
        (printer_root / "config/printer.cfg").write_text("[printer]\nmodified: yes\n", encoding="utf-8")
        before_config = snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config")
        source = paths.managed_klipper_root / "klippy/extras/homing.py"
        before_source = source.read_bytes()
        before_marker = paths.restart_marker_path.read_bytes() if paths.restart_marker_path.exists() else None
        original_restore = restore_helper_runtime.restore_external_backup_entries

        def write_then_fail(**kwargs):
            original_restore(**kwargs)
            self.assertNotEqual(source.read_bytes(), before_source)
            raise BackupArchiveError("simulated failure after external source write")

        with mock.patch.object(
            restore_helper_runtime,
            "restore_external_backup_entries",
            side_effect=write_then_fail,
        ):
            with self.assertRaises(BackupArchiveError):
                run_restore_helper(
                    paths,
                    self.manifest,
                    stream=io.StringIO(),
                    input_stream=io.StringIO("RESTORE\n"),
                    backup_path=str(backup_zip),
                    urlopen=moonraker_urlopen(),
                )

        self.assertEqual(
            snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config"),
            before_config,
        )
        self.assertEqual(source.read_bytes(), before_source)
        self.assertEqual(
            paths.restart_marker_path.read_bytes() if paths.restart_marker_path.exists() else None,
            before_marker,
        )

    def test_external_partial_write_with_failed_compensation_retains_recovery_sentinel(self):
        _, paths, backup_zip = self._source_backup()
        source = paths.managed_klipper_root / "klippy/extras/homing.py"
        original_restore = restore_helper_runtime.restore_external_backup_entries
        original_restore_file = restore_helper_runtime.RollbackJournal._restore_file

        def write_then_fail(**kwargs):
            original_restore(**kwargs)
            raise BackupArchiveError("simulated failure after external source write")

        def fail_source_restore(journal, snapshot):
            if snapshot.path == source:
                raise OSError("simulated external rollback failure", str(source))
            return original_restore_file(journal, snapshot)

        with mock.patch.object(
            restore_helper_runtime,
            "restore_external_backup_entries",
            side_effect=write_then_fail,
        ), mock.patch.object(
            restore_helper_runtime.RollbackJournal,
            "_restore_file",
            side_effect=fail_source_restore,
            autospec=True,
        ):
            with self.assertRaises(RollbackFailedError):
                run_restore_helper(
                    paths,
                    self.manifest,
                    stream=io.StringIO(),
                    input_stream=io.StringIO("RESTORE\n"),
                    backup_path=str(backup_zip),
                    urlopen=moonraker_urlopen(),
                )
        sentinel = paths.recovery_sentinel_path.read_text(encoding="utf-8")
        self.assertIn(str(source), sentinel)
        self.assertIn(str(backup_zip), sentinel)

    def test_restore_helper_supports_direct_restore_stages_before_live_write_and_restores_full_snapshot_without_clearing_sentinel(self):
        printer_root = copy_base_runtime()
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        install_result = run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen())

        backup_zip = install_result.backup_zip_path
        self.assertIsNotNone(backup_zip)
        assert backup_zip is not None
        backup_snapshot = load_backup_snapshot(
            backup_zip_path=backup_zip,
            source_directory="config",
        )

        sentinel = printer_root / ".tltg_optimized_recovery_required"
        sentinel.write_text(
            "error: rollback failed\n"
            f"backup_label: {install_result.backup_label}\n"
            f"backup_zip_path: {backup_zip}\n",
            encoding="utf-8",
        )
        (printer_root / "config/printer.cfg").write_text("[printer]\n", encoding="utf-8")
        (printer_root / "config/box.cfg").write_text("[box_extras]\n", encoding="utf-8")
        state_path = printer_root / "config/tltg_optimized_state.yaml"
        if state_path.exists():
            state_path.unlink()
        managed_tree = printer_root / "config/tltg-optimized-macros"
        if managed_tree.exists():
            shutil.rmtree(managed_tree)
        drifted_runtime_snapshot = snapshot_runtime_tree(
            printer_data_root=printer_root,
            source_directory="config",
        )

        original_stage_backup_snapshot = backup_runtime.stage_backup_snapshot
        stage_observation: dict[str, object] = {}

        def stage_and_inspect(*args, **kwargs):
            staged = original_stage_backup_snapshot(*args, **kwargs)
            stage_observation["staged_snapshot"] = snapshot_runtime_tree(
                printer_data_root=staged.staging_root,
                source_directory="config",
            )
            self.assertEqual(stage_observation["staged_snapshot"], backup_snapshot)
            self.assertEqual(
                snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config"),
                drifted_runtime_snapshot,
            )
            self.assertTrue((staged.source_root / "printer.cfg").exists())
            return staged

        stream = io.StringIO()
        with mock.patch(
            "installer.runtime.backup.stage_backup_snapshot",
            side_effect=stage_and_inspect,
        ):
            rc = run_restore_helper(
                paths,
                self.manifest,
                stream=stream,
                input_stream=io.StringIO("RESTORE\n"),
                backup_path=str(backup_zip),
                urlopen=moonraker_urlopen(),
            )

        self.assertEqual(rc, 0)
        self.assertEqual(stage_observation["staged_snapshot"], backup_snapshot)
        self.assertEqual(
            snapshot_runtime_tree(printer_data_root=printer_root, source_directory="config"),
            backup_snapshot,
        )
        self.assertTrue(sentinel.exists())
        output = stream.getvalue()
        self.assertIn("Warning: restore will overwrite current config changes under", output)
        self.assertIn("Recovery sentinel was not cleared.", output)
