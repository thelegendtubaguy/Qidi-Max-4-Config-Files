from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import os
import re
import shutil
import tempfile
import zipfile

from .fs_atomic import atomic_write_bytes, fsync_directory
from .path_safety import (
    ensure_external_path_has_no_symlink_components,
    ensure_runtime_path_has_no_symlink_components,
    ensure_runtime_tree_has_no_symlinks,
)
from .naming import UNINSTALL_BACKUP_LABEL_PREFIX


UNKNOWN_FIRMWARE_TOKEN = "unknown-firmware"
INSTALLER_BACKUP_RETENTION = 3
CONFIG_BACKUP_IGNORED_SYMLINKS = ("KAMP", "fluidd.cfg")
CONFIG_BACKUP_IGNORED_SYMLINK_PARTS = tuple(
    Path(path).parts for path in CONFIG_BACKUP_IGNORED_SYMLINKS
)
BACKUP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
BACKUP_DISPLAY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
LEGACY_CONFIG_ONLY_PACKAGE_VERSIONS = frozenset({
    "26.04.21.1", "26.04.27.1", "26.04.27.2", "26.04.27.3", "26.04.27.4",
    "26.05.04.1", "26.05.19.1", "26.05.20.1", "26.05.21.1", "26.05.27.1",
    "26.06.01.1", "26.06.02.1", "26.06.03.1", "26.06.04.1", "26.06.11.1",
    "26.06.13.1", "26.06.15.1", "26.07.03.1", "26.07.03.2", "26.07.13.1",
    "26.07.13.2",
})
_PACKAGE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


class BackupArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class InstallerBackupArchive:
    path: Path
    label: str
    kind: str
    firmware_token: str
    package_version: str
    created_at: datetime

    @property
    def display_created_at(self) -> str:
        return format_backup_display_timestamp(self.created_at)


@dataclass(frozen=True)
class StagedBackupSnapshot:
    staging_root: Path
    source_root: Path
    snapshot: dict[str, bytes]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_backup_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)


def format_backup_display_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(BACKUP_DISPLAY_TIMESTAMP_FORMAT)


def format_installed_at(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_install_backup_label(
    *, label_prefix: str, firmware_version: str, package_version: str, moment: datetime
) -> str:
    return (
        f"{label_prefix}-{firmware_version}-{package_version}-{format_backup_timestamp(moment)}"
    )


def build_uninstall_backup_label(
    *, current_firmware: str | None, package_version: str, moment: datetime
) -> str:
    firmware_token = current_firmware or UNKNOWN_FIRMWARE_TOKEN
    return (
        f"{UNINSTALL_BACKUP_LABEL_PREFIX}-{firmware_token}-{package_version}-{format_backup_timestamp(moment)}"
    )


def parse_installer_backup_archive(
    path: Path,
    *,
    install_label_prefix: str,
) -> InstallerBackupArchive | None:
    if not path.is_file() or path.suffix != ".zip":
        return None
    try:
        left, package_version, timestamp_text = path.stem.rsplit("-", 2)
    except ValueError:
        return None
    created_at = _parse_backup_timestamp(timestamp_text)
    if created_at is None or not package_version:
        return None

    prefix = f"{install_label_prefix}-"
    uninstall_prefix = f"{UNINSTALL_BACKUP_LABEL_PREFIX}-"
    if left.startswith(prefix):
        kind = "install"
        firmware_token = left[len(prefix) :]
    elif left.startswith(uninstall_prefix):
        kind = "uninstall"
        firmware_token = left[len(uninstall_prefix) :]
    else:
        return None
    if not firmware_token:
        return None
    return InstallerBackupArchive(
        path=path,
        label=path.stem,
        kind=kind,
        firmware_token=firmware_token,
        package_version=package_version,
        created_at=created_at,
    )


def list_installer_backups(
    printer_data_root: Path,
    *,
    install_label_prefix: str,
) -> tuple[InstallerBackupArchive, ...]:
    if not printer_data_root.exists():
        return ()
    archives = []
    for path in printer_data_root.iterdir():
        archive = parse_installer_backup_archive(
            path,
            install_label_prefix=install_label_prefix,
        )
        if archive is not None:
            archives.append(archive)
    return tuple(sorted(archives, key=_backup_sort_key))


def prune_installer_backups(
    printer_data_root: Path,
    *,
    install_label_prefix: str,
    retain: int = INSTALLER_BACKUP_RETENTION,
    keep_path: Path | None = None,
) -> tuple[Path, ...]:
    archives = list_installer_backups(
        printer_data_root,
        install_label_prefix=install_label_prefix,
    )
    if len(archives) <= retain:
        return ()

    retained_paths: set[Path] = set()
    if keep_path is not None:
        retained_paths.add(keep_path)
    for archive in sorted(archives, key=_backup_sort_key, reverse=True):
        if archive.path in retained_paths:
            continue
        if len(retained_paths) >= retain:
            break
        retained_paths.add(archive.path)

    removed_paths = []
    for archive in archives:
        if archive.path in retained_paths:
            continue
        archive.path.unlink()
        removed_paths.append(archive.path)
    return tuple(removed_paths)


def requires_external_backup_manifest(
    *, package_version: str | None, state_declares_source_patches: bool
) -> bool:
    return (
        state_declares_source_patches
        or package_version is None
        or package_version not in LEGACY_CONFIG_ONLY_PACKAGE_VERSIONS
    )


def package_version_from_backup_label(label: str | None) -> str | None:
    if not label:
        return None
    try:
        _, package_version, timestamp = label.rsplit("-", 2)
    except ValueError:
        return None
    if _parse_backup_timestamp(timestamp) is None or not _PACKAGE_VERSION_RE.fullmatch(package_version):
        return None
    return package_version


def create_config_backup(
    *,
    printer_data_root: Path,
    source_directory: str,
    backup_label: str,
    external_files: tuple[tuple[str, str, Path], ...] = (),
    external_firmware: str | None = None,
) -> Path:
    source_root = printer_data_root / source_directory
    if not source_root.exists() or not source_root.is_dir() or source_root.is_symlink():
        raise BackupArchiveError(
            f"Cannot create backup because {source_directory}/ is missing or not a real directory."
        )
    ensure_runtime_path_has_no_symlink_components(
        printer_data_root=printer_data_root,
        target=source_root,
    )
    ensure_runtime_tree_has_no_symlinks(
        source_root,
        allowed_relative_symlinks=CONFIG_BACKUP_IGNORED_SYMLINKS,
    )
    source_files = [
        item
        for item in sorted(source_root.rglob("*"))
        if item.is_file() and not item.is_symlink()
    ]
    if not source_files:
        raise BackupArchiveError(
            f"Cannot create backup because {source_directory}/ does not contain any files."
        )

    backup_root = printer_data_root
    backup_root.mkdir(parents=True, exist_ok=True)
    final_path = backup_root / f"{backup_label}.zip"
    fd, temp_name = tempfile.mkstemp(prefix=f".{backup_label}.", suffix=".zip", dir=backup_root)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in source_files:
                archive.write(item, item.relative_to(printer_data_root))
            if external_files:
                if not isinstance(external_firmware, str) or not external_firmware:
                    raise BackupArchiveError(
                        "External source backups require detected firmware provenance."
                    )
                entries = []
                for patch_id, destination, item in external_files:
                    if item.is_symlink() or not item.is_file():
                        raise BackupArchiveError(f"External backup target is not a regular file: {item}")
                    member = f"external/{patch_id}"
                    value = item.read_bytes()
                    archive.writestr(member, value)
                    entries.append({"id": patch_id, "destination": destination, "member": member,
                                    "sha256": hashlib.sha256(value).hexdigest(),
                                    "mode": stat.S_IMODE(item.stat().st_mode)})
                archive.writestr(
                    ".tltg-external-files.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "firmware": external_firmware,
                            "files": entries,
                        },
                        sort_keys=True,
                    ),
                )
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, final_path)
        fsync_directory(backup_root)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return final_path


def load_external_backup_entries(
    *,
    backup_zip_path: Path,
    allowed_entries: dict[str, str],
    require_manifest: bool,
) -> tuple[str | None, tuple[tuple[str, str, bytes, int], ...]]:
    """Load only the complete, allowlisted external snapshot set from an archive."""
    try:
        with zipfile.ZipFile(backup_zip_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BackupArchiveError("Backup archive contains duplicate members.")
            manifest_name = ".tltg-external-files.json"
            if manifest_name not in names:
                if require_manifest:
                    raise BackupArchiveError("Backup archive is missing external source metadata.")
                return None, ()
            raw = json.loads(archive.read(manifest_name).decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("files"), list):
                raise BackupArchiveError("External source metadata is malformed.")
            firmware = raw.get("firmware")
            if firmware is not None and (not isinstance(firmware, str) or not firmware):
                raise BackupArchiveError("External source firmware provenance is malformed.")
            entries: list[tuple[str, str, bytes, int]] = []
            ids: set[str] = set()
            destinations: set[str] = set()
            members: set[str] = set()
            for item in raw["files"]:
                if not isinstance(item, dict):
                    raise BackupArchiveError("External source metadata entry is malformed.")
                patch_id, destination, member = item.get("id"), item.get("destination"), item.get("member")
                digest, mode = item.get("sha256"), item.get("mode")
                if (
                    not isinstance(patch_id, str)
                    or allowed_entries.get(patch_id) != destination
                    or not isinstance(member, str)
                    or member != f"external/{patch_id}"
                    or member.startswith("/")
                    or ".." in PurePosixPath(member).parts
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest.lower())
                    or isinstance(mode, bool)
                    or not isinstance(mode, int)
                    or not 0 <= mode <= 0o777
                    or patch_id in ids
                    or destination in destinations
                    or member in members
                    or member not in names
                ):
                    raise BackupArchiveError("External source metadata is invalid.")
                info = archive.getinfo(member)
                file_type = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or file_type not in {0, stat.S_IFREG}:
                    raise BackupArchiveError("External backup member is not a regular file.")
                value = archive.read(member)
                if hashlib.sha256(value).hexdigest() != digest.lower():
                    raise BackupArchiveError("External backup member hash mismatch.")
                ids.add(patch_id)
                destinations.add(destination)
                members.add(member)
                entries.append((patch_id, destination, value, mode))
            if {name for name in names if name.startswith("external/")} != members:
                raise BackupArchiveError("External backup members do not match metadata.")
            if require_manifest and (ids != set(allowed_entries) or destinations != set(allowed_entries.values())):
                raise BackupArchiveError("External source metadata is incomplete.")
            return firmware, tuple(entries)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupArchiveError("Could not validate external backup metadata.") from exc


def restore_external_backup_entries(*, entries: tuple[tuple[str, str, bytes, int], ...], destination_root: Path) -> None:
    try:
        for _, destination, value, mode in entries:
            target = destination_root / destination
            ensure_external_path_has_no_symlink_components(root=destination_root, target=target)
            if target.is_symlink() or not target.is_file():
                raise BackupArchiveError(f"External restore target is not a regular file: {destination}")
            atomic_write_bytes(target, value, mode=mode, force_mode=True)
            if target.read_bytes() != value:
                raise BackupArchiveError(f"External restore verification failed: {destination}")
    except OSError as exc:
        raise BackupArchiveError("External restore failed.") from exc


def snapshot_runtime_tree(
    *, printer_data_root: Path, source_directory: str
) -> dict[str, bytes]:
    source_root = printer_data_root / source_directory
    snapshot: dict[str, bytes] = {}
    if not source_root.exists():
        return snapshot
    for item in sorted(source_root.rglob("*")):
        if _is_ignored_config_backup_symlink(source_root, item):
            continue
        if item.is_file():
            snapshot[item.relative_to(printer_data_root).as_posix()] = item.read_bytes()
    return snapshot


def _is_ignored_config_backup_symlink(source_root: Path, item: Path) -> bool:
    return (
        item.is_symlink()
        and item.relative_to(source_root).parts in CONFIG_BACKUP_IGNORED_SYMLINK_PARTS
    )


def load_backup_snapshot(
    *, backup_zip_path: Path, source_directory: str
) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    try:
        with zipfile.ZipFile(backup_zip_path, "r") as archive:
            for member in archive.infolist():
                member_path = _restore_member_path(
                    member=member,
                    source_directory=source_directory,
                    seen_paths=seen_paths,
                )
                if member_path is None:
                    continue
                snapshot[member_path.as_posix()] = archive.read(member)
    except OSError as exc:
        raise BackupArchiveError(f"Could not read backup zip: {backup_zip_path}") from exc
    except zipfile.BadZipFile as exc:
        raise BackupArchiveError("Backup zip is corrupt.") from exc
    _ensure_non_empty_snapshot(snapshot, source_directory)
    return snapshot


def stage_backup_snapshot(
    *,
    backup_zip_path: Path,
    staging_root: Path,
    source_directory: str,
) -> StagedBackupSnapshot:
    staging_root.mkdir(parents=True, exist_ok=True)
    seen_paths: set[str] = set()
    try:
        with zipfile.ZipFile(backup_zip_path, "r") as archive:
            for member in archive.infolist():
                member_path = _restore_member_path(
                    member=member,
                    source_directory=source_directory,
                    seen_paths=seen_paths,
                )
                if member_path is None:
                    continue
                target_path = staging_root / Path(*member_path.parts)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(archive.read(member))
    except OSError as exc:
        raise BackupArchiveError(f"Could not read backup zip: {backup_zip_path}") from exc
    except zipfile.BadZipFile as exc:
        raise BackupArchiveError("Backup zip is corrupt.") from exc

    snapshot = snapshot_runtime_tree(
        printer_data_root=staging_root,
        source_directory=source_directory,
    )
    _ensure_non_empty_snapshot(snapshot, source_directory)
    if set(snapshot) != seen_paths:
        raise BackupArchiveError(
            f"Staged backup snapshot validation failed for {source_directory}/."
        )
    return StagedBackupSnapshot(
        staging_root=staging_root,
        source_root=staging_root / source_directory,
        snapshot=snapshot,
    )


def restore_backup_snapshot(
    *,
    backup_zip_path: Path,
    printer_data_root: Path,
    source_directory: str,
) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="tltg-restore-stage-") as temp_dir:
        staged = stage_backup_snapshot(
            backup_zip_path=backup_zip_path,
            staging_root=Path(temp_dir),
            source_directory=source_directory,
        )
        restore_staged_snapshot_tree(
            staged_source_root=staged.source_root,
            printer_data_root=printer_data_root,
            source_directory=source_directory,
        )
        return staged.snapshot


def restore_staged_snapshot_tree(
    *,
    staged_source_root: Path,
    printer_data_root: Path,
    source_directory: str,
) -> None:
    source_files = {
        item.relative_to(staged_source_root): item
        for item in sorted(staged_source_root.rglob("*"))
        if item.is_file()
    } if staged_source_root.exists() else {}
    if not source_files:
        raise BackupArchiveError(
            f"Staged backup snapshot for {source_directory}/ is empty."
        )

    destination_root = printer_data_root / source_directory
    ensure_runtime_path_has_no_symlink_components(
        printer_data_root=printer_data_root,
        target=destination_root,
    )
    if destination_root.exists() and not destination_root.is_dir():
        raise BackupArchiveError(
            f"Restore target is not a directory: {destination_root}"
        )

    replacement_root, old_root = _copy_staged_tree_for_swap(
        staged_source_root=staged_source_root,
        destination_root=destination_root,
    )
    try:
        if destination_root.exists():
            os.replace(destination_root, old_root)
            fsync_directory(destination_root.parent)
        os.replace(replacement_root, destination_root)
        fsync_directory(destination_root.parent)
    except Exception:
        if old_root.exists() and not destination_root.exists():
            os.replace(old_root, destination_root)
            fsync_directory(destination_root.parent)
        raise
    finally:
        if replacement_root.exists():
            shutil.rmtree(replacement_root)
    if old_root.exists():
        shutil.rmtree(old_root)
        fsync_directory(destination_root.parent)


def _copy_staged_tree_for_swap(
    *, staged_source_root: Path, destination_root: Path
) -> tuple[Path, Path]:
    parent = destination_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    replacement_root = Path(
        tempfile.mkdtemp(prefix=f".{destination_root.name}.restore-new.", dir=parent)
    )
    old_root = Path(
        tempfile.mkdtemp(prefix=f".{destination_root.name}.restore-old.", dir=parent)
    )
    shutil.rmtree(replacement_root)
    shutil.rmtree(old_root)
    try:
        shutil.copytree(staged_source_root, replacement_root)
        _preserve_ignored_config_symlinks(
            destination_root=destination_root,
            replacement_root=replacement_root,
        )
        _fsync_tree(replacement_root)
        return replacement_root, old_root
    except Exception:
        if replacement_root.exists():
            shutil.rmtree(replacement_root)
        if old_root.exists():
            shutil.rmtree(old_root)
        raise


def _preserve_ignored_config_symlinks(
    *, destination_root: Path, replacement_root: Path
) -> None:
    for relative in CONFIG_BACKUP_IGNORED_SYMLINKS:
        source = destination_root / relative
        if not source.is_symlink():
            continue
        target = replacement_root / relative
        if target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.readlink(source), target_is_directory=source.is_dir())


def _fsync_tree(root: Path) -> None:
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            continue
        if item.is_file():
            fd = os.open(item, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    directories = [
        root,
        *(item for item in root.rglob("*") if item.is_dir() and not item.is_symlink()),
    ]
    for directory in sorted(
        directories,
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        fsync_directory(directory)


def describe_snapshot_difference(
    runtime_snapshot: dict[str, bytes], backup_snapshot: dict[str, bytes]
) -> str:
    runtime_paths = set(runtime_snapshot)
    backup_paths = set(backup_snapshot)
    missing = len(backup_paths - runtime_paths)
    extra = len(runtime_paths - backup_paths)
    changed = sum(
        1
        for path in runtime_paths & backup_paths
        if runtime_snapshot[path] != backup_snapshot[path]
    )
    parts = []
    if missing:
        parts.append(f"missing {missing} file(s)")
    if extra:
        parts.append(f"extra {extra} file(s)")
    if changed:
        parts.append(f"changed {changed} file(s)")
    if not parts:
        return "Current config does not match the recorded backup."
    return "Current config differs from the recorded backup: " + ", ".join(parts) + "."


def _restore_member_path(
    *,
    member: zipfile.ZipInfo,
    source_directory: str,
    seen_paths: set[str],
) -> PurePosixPath | None:
    member_path = PurePosixPath(member.filename)
    if member_path.is_absolute() or any(part == ".." for part in member_path.parts):
        raise BackupArchiveError("Backup zip contains an invalid path.")
    if not member_path.parts or member_path.parts[0] != source_directory:
        return None
    if member.is_dir():
        return None
    key = member_path.as_posix()
    if key in seen_paths:
        raise BackupArchiveError("Backup zip contains duplicate paths.")
    seen_paths.add(key)
    return member_path


def _ensure_non_empty_snapshot(snapshot: dict[str, bytes], source_directory: str) -> None:
    if snapshot:
        return
    raise BackupArchiveError(
        f"Backup zip does not contain a non-empty archived snapshot for {source_directory}/."
    )


def _parse_backup_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, BACKUP_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _backup_sort_key(archive: InstallerBackupArchive) -> tuple[datetime, str, str]:
    return (archive.created_at, archive.label, archive.path.name)
