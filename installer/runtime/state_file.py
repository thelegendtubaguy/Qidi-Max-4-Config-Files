from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .fs_atomic import atomic_delete, atomic_write_text
from .models import (
    InstalledState,
    ManagedTreeFileRecord,
    ManagedTreeState,
    PatchLedgerEntry,
    SourcePatchState,
)


class StateValidationError(ValueError):
    pass


def load_installed_state(path: Path) -> InstalledState:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise StateValidationError(f"Could not read state file: {path}") from exc
    except yaml.YAMLError as exc:
        raise StateValidationError(f"Could not parse state file: {path}") from exc
    return parse_installed_state(raw)


def parse_installed_state(raw: Any) -> InstalledState:
    if not isinstance(raw, dict):
        raise StateValidationError("State file root must be a mapping.")
    if raw.get("schema_version") != 1:
        raise StateValidationError("State file schema_version must be 1.")
    package, runtime, backup, managed_tree = (
        _require_mapping(raw, key) for key in ("package", "runtime", "backup", "managed_tree")
    )
    files_raw = managed_tree.get("files")
    if not isinstance(files_raw, list):
        raise StateValidationError("managed_tree.files must be a list.")
    tree_files = tuple(ManagedTreeFileRecord(
        path=_validate_config_path(_require_str(item, "path")), sha256=_sha256(item, "sha256")
    ) for item in _mapping_list(files_raw, "managed_tree.files"))
    ledger_raw = raw.get("patch_ledger")
    if not isinstance(ledger_raw, list):
        raise StateValidationError("patch_ledger must be a list.")
    patch_ledger = []
    for item in _mapping_list(ledger_raw, "patch_ledger"):
        result = _require_str(item, "install_result")
        if result not in {"applied", "noop_desired", "user_modified"}:
            raise StateValidationError("Unsupported patch install_result.")
        patch_ledger.append(PatchLedgerEntry(
            id=_require_str(item, "id"), file=_validate_config_path(_require_str(item, "file")),
            section=_require_str(item, "section"), option=_require_str(item, "option"),
            expected=_require_str(item, "expected"), desired=_require_str(item, "desired"), install_result=result,
        ))
    source_patches = _parse_source_patches(raw.get("source_patches", []))
    system_ledger = raw.get("system_ledger")
    if system_ledger is not None and not isinstance(system_ledger, dict):
        raise StateValidationError("system_ledger must be a mapping when present.")
    return InstalledState(
        schema_version=1, package_id=_require_str(package, "id"), package_version=_require_str(package, "version"),
        runtime_firmware=_require_str(runtime, "firmware"), backup_label=_require_str(backup, "label"),
        installed_at=_require_str(raw, "installed_at"),
        managed_tree=ManagedTreeState(root=_validate_config_path(_require_str(managed_tree, "root")), files=tree_files),
        patch_ledger=tuple(patch_ledger), source_patches=source_patches, system_ledger=system_ledger,
    )


def _parse_source_patches(raw: Any) -> tuple[SourcePatchState, ...]:
    if not isinstance(raw, list):
        raise StateValidationError("source_patches must be a list when present.")
    result = []
    ids: set[str] = set()
    destinations: set[str] = set()
    for item in _mapping_list(raw, "source_patches"):
        patch_id = _require_str(item, "id")
        destination = _validate_source_destination(_require_str(item, "destination"))
        if patch_id in ids or destination in destinations:
            raise StateValidationError("source_patches IDs and destinations must be unique.")
        ids.add(patch_id); destinations.add(destination)
        original_sha = _sha256(item, "original_sha256")
        desired_sha = _sha256(item, "desired_sha256")
        encoded = _require_str(item, "original_bytes")
        try:
            original_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
            raise StateValidationError("source_patches original_bytes must be strict base64.") from exc
        if hashlib.sha256(original_bytes).hexdigest() != original_sha:
            raise StateValidationError("source_patches original bytes do not match original_sha256.")
        mode = item.get("original_mode")
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise StateValidationError("source_patches original_mode must be an integer from 0000 through 0777.")
        install_result = _require_str(item, "install_result")
        if install_result not in {"applied", "noop_desired", "prior_managed"}:
            raise StateValidationError("Unsupported source patch install_result.")
        result.append(SourcePatchState(
            id=patch_id, destination=destination, firmware=_require_str(item, "firmware"),
            original_sha256=original_sha, desired_sha256=desired_sha, original_mode=mode,
            original_bytes=original_bytes, install_result=install_result,
        ))
    return tuple(result)


def write_installed_state(path: Path, state: InstalledState) -> None:
    document: dict[str, Any] = {
        "schema_version": 1, "package": {"id": state.package_id, "version": state.package_version},
        "runtime": {"firmware": state.runtime_firmware}, "backup": {"label": state.backup_label},
        "installed_at": state.installed_at,
        "managed_tree": {"root": state.managed_tree.root, "files": [item.__dict__ for item in state.managed_tree.files]},
        "patch_ledger": [entry.__dict__ for entry in state.patch_ledger],
    }
    if state.source_patches:
        document["source_patches"] = [{
            "id": item.id, "destination": item.destination, "firmware": item.firmware,
            "original_sha256": item.original_sha256, "desired_sha256": item.desired_sha256,
            "original_mode": item.original_mode,
            "original_bytes": base64.b64encode(item.original_bytes).decode("ascii"),
            "install_result": item.install_result,
        } for item in state.source_patches]
    if state.system_ledger is not None:
        document["system_ledger"] = state.system_ledger
    atomic_write_text(path, yaml.safe_dump(document, sort_keys=False), mode=0o600, force_mode=True)


def delete_state_file(path: Path) -> None:
    atomic_delete(path)


def _mapping_list(raw: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise StateValidationError(f"{name} entries must be mappings.")
    return raw


def _require_mapping(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict): raise StateValidationError(f"Expected mapping at {key}.")
    return value


def _require_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value: raise StateValidationError(f"Expected non-empty string at {key}.")
    return value


def _sha256(mapping: dict[str, Any], key: str) -> str:
    value = _require_str(mapping, key).lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise StateValidationError(f"{key} must be SHA-256 hex.")
    return value


def _validate_config_path(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != "config":
        raise StateValidationError(f"State paths must stay under config/: {path}")
    return pure.as_posix()


def _validate_source_destination(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 3 or pure.parts[:2] != ("klippy", "extras"):
        raise StateValidationError(f"Source patch destination must stay under klippy/extras/: {path}")
    return pure.as_posix()
