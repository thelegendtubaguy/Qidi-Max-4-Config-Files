from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .backup import (
    BackupArchiveError,
    describe_snapshot_difference,
    load_backup_snapshot,
    load_external_backup_entries,
    package_version_from_backup_label,
    requires_external_backup_manifest,
    snapshot_runtime_tree,
)
from .errors import PathSafetyError, RecoverySentinelClearError, RollbackFailedError
from .fs_atomic import atomic_delete, atomic_write_bytes, atomic_write_text, fsync_directory
from .path_safety import ensure_external_path_has_no_symlink_components


RECOVERY_CLEAR_PREFIX = (
    "Recovery sentinel can only be cleared after restoring the recorded backup and matching the current config tree to that backup."
)


@dataclass
class FileSnapshot:
    path: Path
    existed: bool
    content: bytes | None
    mode: int | None


@dataclass
class TreeSnapshot:
    root: Path
    existed: bool
    files: dict[Path, FileSnapshot]
    directories: set[Path]


@dataclass(frozen=True)
class RecoverySentinelRecord:
    error: str
    backup_label: str | None
    backup_zip_path: Path | None
    rollback_failed_paths: tuple[str, ...]


class RollbackJournal:
    def __init__(
        self,
        recovery_sentinel_path: Path,
        *,
        printer_data_root: Path | None = None,
        source_directory: str = "config",
    ):
        self.recovery_sentinel_path = recovery_sentinel_path
        self.printer_data_root = printer_data_root
        self.source_directory = source_directory
        self.file_snapshots: dict[Path, FileSnapshot] = {}
        self.tree_snapshots: dict[Path, TreeSnapshot] = {}
        self.write_started = False

    @property
    def restore_target_path(self) -> Path | None:
        if self.printer_data_root is None:
            return None
        return self.printer_data_root / self.source_directory

    def note_write(self) -> None:
        self.write_started = True

    def track_file(self, path: Path) -> None:
        if path in self.file_snapshots or any(
            path == root or root in path.parents for root in self.tree_snapshots
        ):
            return
        if path.exists() and path.is_file():
            self.file_snapshots[path] = FileSnapshot(
                path=path,
                existed=True,
                content=path.read_bytes(),
                mode=path.stat().st_mode & 0o777,
            )
        else:
            self.file_snapshots[path] = FileSnapshot(
                path=path, existed=False, content=None, mode=None
            )

    def track_tree(self, root: Path) -> None:
        if root in self.tree_snapshots:
            return
        if not root.exists():
            self.tree_snapshots[root] = TreeSnapshot(
                root=root, existed=False, files={}, directories=set()
            )
            return
        files: dict[Path, FileSnapshot] = {}
        directories: set[Path] = {root}
        for item in root.rglob("*"):
            if item.is_dir():
                directories.add(item)
                continue
            files[item] = FileSnapshot(
                path=item,
                existed=True,
                content=item.read_bytes(),
                mode=item.stat().st_mode & 0o777,
            )
        self.tree_snapshots[root] = TreeSnapshot(
            root=root, existed=True, files=files, directories=directories
        )

    def rollback_or_raise(
        self,
        original_error: Exception,
        *,
        backup_label: str | None,
        backup_zip_path: Path | None,
    ) -> None:
        failed_paths: list[str] = []
        try:
            self.rollback()
        except Exception as rollback_error:  # pragma: no cover - exercised in integration tests
            failed_paths.append(
                str(getattr(rollback_error, "filename", None) or rollback_error)
            )
        if failed_paths:
            self._write_recovery_sentinel(original_error, backup_label, backup_zip_path, failed_paths)
            raise RollbackFailedError(
                original_error,
                backup_label=backup_label,
                backup_zip_path=backup_zip_path,
                failed_paths=tuple(failed_paths),
                recovery_sentinel_path=self.recovery_sentinel_path,
                restore_target_path=self.restore_target_path,
            ) from original_error

    def rollback(self) -> None:
        for _, snapshot in reversed(list(self.tree_snapshots.items())):
            self._restore_tree(snapshot)
        for _, snapshot in reversed(list(self.file_snapshots.items())):
            self._restore_file(snapshot)

    def _restore_tree(self, snapshot: TreeSnapshot) -> None:
        root = snapshot.root
        if not snapshot.existed:
            if root.exists():
                for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    if path.is_file():
                        atomic_delete(path)
                    elif path.is_dir():
                        path.rmdir()
                        fsync_directory(path.parent)
                if root.exists():
                    root.rmdir()
                    fsync_directory(root.parent)
            return

        root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(snapshot.directories, key=lambda item: len(item.parts)):
            directory.mkdir(parents=True, exist_ok=True)
        current_files = {item for item in root.rglob("*") if item.is_file()}
        original_files = set(snapshot.files.keys())
        for extra_file in sorted(current_files - original_files, reverse=True):
            atomic_delete(extra_file)
        current_dirs = {item for item in root.rglob("*") if item.is_dir()}
        extra_dirs = sorted(
            current_dirs - snapshot.directories,
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for extra_dir in extra_dirs:
            if extra_dir.exists() and not any(extra_dir.iterdir()):
                extra_dir.rmdir()
                fsync_directory(extra_dir.parent)
        for file_snapshot in snapshot.files.values():
            self._restore_file(file_snapshot)

    def _restore_file(self, snapshot: FileSnapshot) -> None:
        if snapshot.existed:
            assert snapshot.content is not None
            atomic_write_bytes(snapshot.path, snapshot.content, mode=snapshot.mode)
            return
        if snapshot.path.exists():
            atomic_delete(snapshot.path)

    def _write_recovery_sentinel(
        self,
        original_error: Exception,
        backup_label: str | None,
        backup_zip_path: Path | None,
        failed_paths: list[str],
    ) -> None:
        lines = [
            f"error: {getattr(original_error, 'message', str(original_error))}",
            f"backup_label: {backup_label or ''}",
            f"backup_zip_path: {backup_zip_path or ''}",
        ]
        for path in failed_paths:
            lines.append(f"rollback_failed_path: {path}")
        atomic_write_text(
            self.recovery_sentinel_path,
            "\n".join(lines) + "\n",
            mode=0o600,
            force_mode=True,
        )



def clear_recovery_sentinel(
    path: Path,
    *,
    printer_data_root: Path,
    source_directory: str = "config",
    external_root: Path | None = None,
    allowed_external_entries: dict[str, str] | None = None,
) -> bool:
    if not path.exists():
        return False
    record = load_recovery_sentinel(path)
    if record.backup_zip_path is None:
        raise RecoverySentinelClearError(
            f"{RECOVERY_CLEAR_PREFIX} Sentinel is missing a recorded backup path."
        )
    if not record.backup_zip_path.exists():
        raise RecoverySentinelClearError(
            f"{RECOVERY_CLEAR_PREFIX} Recorded backup zip is missing."
        )
    runtime_snapshot = snapshot_runtime_tree(
        printer_data_root=printer_data_root,
        source_directory=source_directory,
    )
    try:
        backup_snapshot = load_backup_snapshot(
            backup_zip_path=record.backup_zip_path,
            source_directory=source_directory,
        )
    except BackupArchiveError as exc:
        raise RecoverySentinelClearError(f"{RECOVERY_CLEAR_PREFIX} {exc}") from exc
    if runtime_snapshot != backup_snapshot:
        raise RecoverySentinelClearError(
            f"{RECOVERY_CLEAR_PREFIX} {describe_snapshot_difference(runtime_snapshot, backup_snapshot)}"
        )
    if external_root is not None and allowed_external_entries is not None:
        try:
            require_manifest = requires_external_backup_manifest(
                package_version=package_version_from_backup_label(
                    record.backup_label or record.backup_zip_path.stem
                ),
                state_declares_source_patches=_snapshot_declares_source_patches(
                    backup_snapshot
                ),
            )
            _, entries = load_external_backup_entries(
                backup_zip_path=record.backup_zip_path,
                allowed_entries=allowed_external_entries,
                require_manifest=require_manifest,
            )
        except BackupArchiveError as exc:
            raise RecoverySentinelClearError(f"{RECOVERY_CLEAR_PREFIX} {exc}") from exc
        for _, destination, value, _ in entries:
            live = external_root / destination
            try:
                ensure_external_path_has_no_symlink_components(
                    root=external_root, target=live
                )
            except PathSafetyError as exc:
                raise RecoverySentinelClearError(
                    f"{RECOVERY_CLEAR_PREFIX} {exc}"
                ) from exc
            if not live.is_file() or live.is_symlink() or live.read_bytes() != value:
                raise RecoverySentinelClearError(
                    f"{RECOVERY_CLEAR_PREFIX} External source does not match backup: {destination}"
                )
    atomic_delete(path)
    return True



def _snapshot_declares_source_patches(snapshot: dict[str, bytes]) -> bool:
    state = snapshot.get("config/tltg_optimized_state.yaml")
    if state is None:
        return False
    try:
        raw = yaml.safe_load(state)
    except yaml.YAMLError as exc:
        raise RecoverySentinelClearError(
            f"{RECOVERY_CLEAR_PREFIX} Archived installed state is malformed."
        ) from exc
    return isinstance(raw, dict) and "source_patches" in raw


def load_recovery_sentinel(path: Path) -> RecoverySentinelRecord:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecoverySentinelClearError(
            f"Could not read recovery sentinel: {path}"
        ) from exc
    error = ""
    backup_label: str | None = None
    backup_zip_path: Path | None = None
    rollback_failed_paths: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        if ": " not in line:
            raise RecoverySentinelClearError(
                f"{RECOVERY_CLEAR_PREFIX} Sentinel contents are invalid."
            )
        key, value = line.split(": ", 1)
        if key == "error":
            error = value
        elif key == "backup_label":
            backup_label = value or None
        elif key == "backup_zip_path":
            backup_zip_path = Path(value) if value else None
        elif key == "rollback_failed_path":
            rollback_failed_paths.append(value)
    return RecoverySentinelRecord(
        error=error,
        backup_label=backup_label,
        backup_zip_path=backup_zip_path,
        rollback_failed_paths=tuple(rollback_failed_paths),
    )
