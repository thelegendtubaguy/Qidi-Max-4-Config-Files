from __future__ import annotations

import unittest
from unittest import mock

from installer.runtime.backup import create_config_backup
from installer.runtime.errors import RecoverySentinelClearError
from installer.runtime.rollback import RollbackJournal, clear_recovery_sentinel
from installer.tests.helpers import temp_path


class RollbackTests(unittest.TestCase):
    def test_rollback_failure_writes_recovery_sentinel(self):
        temp_root = temp_path("rollback-test-")
        sentinel = temp_root / ".sentinel"
        path = temp_root / "printer.cfg"
        path.write_text("before\n", encoding="utf-8")
        journal = RollbackJournal(sentinel)
        journal.track_file(path)
        journal.note_write()
        path.write_text("after\n", encoding="utf-8")
        with mock.patch.object(journal, "rollback", side_effect=RuntimeError("boom")):
            with self.assertRaises(Exception):
                journal.rollback_or_raise(
                    RuntimeError("write failed"),
                    backup_label="backup",
                    backup_zip_path=temp_root / "backup.zip",
                )
        self.assertTrue(sentinel.exists())

    def test_recovery_sentinel_requires_external_file_rollback_state(self):
        temp_root = temp_path("rollback-external-test-")
        printer_root = temp_root / "printer_data"
        config_root = printer_root / "config"
        config_root.mkdir(parents=True)
        (config_root / "printer.cfg").write_text("before\n", encoding="utf-8")
        backup_zip = create_config_backup(
            printer_data_root=printer_root,
            source_directory="config",
            backup_label="backup",
        )
        sentinel = printer_root / ".sentinel"
        external = temp_root / "klipper/klippy/extras/managed.py"
        external.parent.mkdir(parents=True)
        journal = RollbackJournal(sentinel, printer_data_root=printer_root)
        journal.track_file(external)
        journal.note_write()
        external.write_text("orphaned\n", encoding="utf-8")
        with mock.patch.object(journal, "rollback", side_effect=RuntimeError("boom")):
            with self.assertRaises(Exception):
                journal.rollback_or_raise(
                    RuntimeError("write failed"),
                    backup_label="backup",
                    backup_zip_path=backup_zip,
                )

        with self.assertRaises(RecoverySentinelClearError):
            clear_recovery_sentinel(sentinel, printer_data_root=printer_root)
        external.unlink()
        self.assertTrue(clear_recovery_sentinel(sentinel, printer_data_root=printer_root))
