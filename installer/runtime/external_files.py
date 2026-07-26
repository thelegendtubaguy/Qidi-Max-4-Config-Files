from __future__ import annotations

import base64
from pathlib import Path

from .errors import ExternalFileError
from .fs_atomic import atomic_delete, atomic_write_bytes
from .mirror import sha256_bytes, sha256_file
from .models import ExternalFileState, InstalledState, ManagedExternalFileSpec, RuntimePaths


def source_path(paths: RuntimePaths, spec: ManagedExternalFileSpec) -> Path:
    return paths.installer_root / spec.source


def destination_path(paths: RuntimePaths, destination: str) -> Path:
    return paths.managed_klipper_root / destination


def validate_install(
    *, paths: RuntimePaths, specs: tuple[ManagedExternalFileSpec, ...], prior_state: InstalledState | None
) -> None:
    prior = {item.id: item for item in prior_state.external_files} if prior_state else {}
    if prior and set(prior) != {item.id for item in specs}:
        raise ExternalFileError("External file manifest does not match installed state.")
    for spec in specs:
        source = source_path(paths, spec)
        if source.is_symlink() or not source.is_file():
            raise ExternalFileError(f"External file source is missing or unsafe: {source}")
        if sha256_file(source) != spec.sha256:
            raise ExternalFileError(f"External file source hash mismatch: {source}")
        target = destination_path(paths, spec.destination)
        previous = prior.get(spec.id)
        if previous is None:
            if target.exists() or target.is_symlink():
                raise ExternalFileError(f"Untracked external file collision: {target}")
            continue
        if previous.destination != spec.destination:
            raise ExternalFileError(f"External file destination changed: {spec.id}")
        if target.is_symlink() or not target.is_file():
            raise ExternalFileError(f"Managed external file is missing or unsafe: {target}")
        if sha256_file(target) != previous.installed_sha256:
            raise ExternalFileError(f"Managed external file drift detected: {target}")


def install_requires_process_restart(
    *, specs: tuple[ManagedExternalFileSpec, ...], prior_state: InstalledState | None
) -> bool:
    prior = {item.id: item for item in prior_state.external_files} if prior_state else {}
    return any(
        spec.id not in prior or prior[spec.id].installed_sha256 != spec.sha256
        for spec in specs
    )


def planned_state(
    *, specs: tuple[ManagedExternalFileSpec, ...], prior_state: InstalledState | None
) -> tuple[ExternalFileState, ...]:
    prior = {item.id: item for item in prior_state.external_files} if prior_state else {}
    return tuple(
        ExternalFileState(
            id=spec.id,
            destination=spec.destination,
            installed_sha256=spec.sha256,
            preimage_b64=prior.get(spec.id).preimage_b64 if spec.id in prior else None,
            preimage_sha256=prior.get(spec.id).preimage_sha256 if spec.id in prior else None,
            preimage_mode=prior.get(spec.id).preimage_mode if spec.id in prior else None,
        )
        for spec in specs
    )


def deploy(*, paths: RuntimePaths, spec: ManagedExternalFileSpec) -> None:
    atomic_write_bytes(
        destination_path(paths, spec.destination),
        source_path(paths, spec).read_bytes(),
        mode=0o644,
        force_mode=True,
    )


def validate_uninstall(*, paths: RuntimePaths, state: InstalledState) -> None:
    for record in state.external_files:
        target = destination_path(paths, record.destination)
        if target.is_symlink() or not target.is_file():
            raise ExternalFileError(f"Managed external file is missing or unsafe: {target}")
        if sha256_file(target) != record.installed_sha256:
            raise ExternalFileError(f"Managed external file drift detected: {target}")
        _validate_preimage(record)


def remove_or_restore(*, paths: RuntimePaths, record: ExternalFileState) -> None:
    target = destination_path(paths, record.destination)
    if (
        target.is_symlink()
        or not target.is_file()
        or sha256_file(target) != record.installed_sha256
    ):
        raise ExternalFileError(f"Managed external file drift detected: {target}")
    if record.preimage_b64 is None:
        atomic_delete(target)
        return
    _validate_preimage(record)
    data = base64.b64decode(record.preimage_b64, validate=True)
    if sha256_bytes(data) != record.preimage_sha256:
        raise ExternalFileError(f"External file preimage hash mismatch: {record.id}")
    atomic_write_bytes(target, data, mode=record.preimage_mode, force_mode=True)


def verify_install(*, paths: RuntimePaths, state: tuple[ExternalFileState, ...]) -> None:
    for record in state:
        target = destination_path(paths, record.destination)
        if target.is_symlink() or not target.is_file():
            raise ExternalFileError(f"External file postflight missing: {target}")
        if sha256_file(target) != record.installed_sha256:
            raise ExternalFileError(f"External file postflight hash mismatch: {target}")


def verify_uninstall(*, paths: RuntimePaths, state: InstalledState) -> None:
    for record in state.external_files:
        target = destination_path(paths, record.destination)
        if record.preimage_b64 is None:
            if target.exists() or target.is_symlink():
                raise ExternalFileError(f"External file remains after uninstall: {target}")
            continue
        data = base64.b64decode(record.preimage_b64, validate=True)
        if target.is_symlink() or not target.is_file() or sha256_file(target) != sha256_bytes(data):
            raise ExternalFileError(f"External file preimage was not restored: {target}")


def _validate_preimage(record: ExternalFileState) -> None:
    values = (record.preimage_b64, record.preimage_sha256, record.preimage_mode)
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise ExternalFileError(f"External file preimage is incomplete: {record.id}")
    try:
        data = base64.b64decode(record.preimage_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ExternalFileError(f"External file preimage is invalid: {record.id}") from exc
    if sha256_bytes(data) != record.preimage_sha256:
        raise ExternalFileError(f"External file preimage hash mismatch: {record.id}")
    if not isinstance(record.preimage_mode, int) or not 0 <= record.preimage_mode <= 0o777:
        raise ExternalFileError(f"External file preimage mode is invalid: {record.id}")
