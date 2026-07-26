from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .errors import PathSafetyError
from .models import Manifest, RuntimePaths



def ensure_path_under_root_has_no_symlink_components(
    *, root: Path, target: Path, root_name: str
) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise PathSafetyError(f"Runtime path is outside {root_name}: {target}") from exc
    if root.is_symlink():
        raise PathSafetyError(f"Runtime path uses a symlinked {root_name}: {root}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PathSafetyError(f"Runtime path contains a symlink: {current}")


def ensure_runtime_path_has_no_symlink_components(
    *, printer_data_root: Path, target: Path
) -> None:
    ensure_path_under_root_has_no_symlink_components(
        root=printer_data_root,
        target=target,
        root_name="printer data root",
    )


def ensure_external_path_has_no_symlink_components(*, root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise PathSafetyError(f"Runtime path is outside managed Klipper root: {target}") from exc
    if root.is_symlink():
        raise PathSafetyError(f"Managed Klipper root is a symlink: {root}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PathSafetyError(f"Runtime path contains a symlink: {current}")


def ensure_runtime_tree_has_no_symlinks(
    root: Path, *, allowed_relative_symlinks: Iterable[str | Path] = ()
) -> None:
    if not root.exists():
        return
    if root.is_symlink():
        raise PathSafetyError(f"Runtime tree root is a symlink: {root}")
    allowed = {_relative_path_parts(path) for path in allowed_relative_symlinks}
    for item in root.rglob("*"):
        if not item.is_symlink():
            continue
        relative_parts = item.relative_to(root).parts
        if relative_parts in allowed:
            continue
        raise PathSafetyError(f"Runtime tree contains a symlink: {item}")



def ensure_install_paths_safe(*, paths: RuntimePaths, manifest: Manifest) -> None:
    targets = _manifest_runtime_targets(paths=paths, manifest=manifest)
    for target in targets:
        ensure_runtime_path_has_no_symlink_components(
            printer_data_root=paths.printer_data_root,
            target=target,
        )
    _ensure_external_paths_safe(paths=paths, manifest=manifest)



def ensure_uninstall_paths_safe(
    *, paths: RuntimePaths, manifest: Manifest, state_paths: Iterable[str] = ()
) -> None:
    targets = set(_manifest_runtime_targets(paths=paths, manifest=manifest))
    targets.update(paths.printer_data_root / path for path in state_paths)
    for target in targets:
        ensure_runtime_path_has_no_symlink_components(
            printer_data_root=paths.printer_data_root,
            target=target,
        )
    _ensure_external_paths_safe(paths=paths, manifest=manifest)


def _ensure_external_paths_safe(*, paths: RuntimePaths, manifest: Manifest) -> None:
    for spec in manifest.install.external_files:
        ensure_path_under_root_has_no_symlink_components(
            root=paths.managed_klipper_root,
            target=paths.managed_klipper_root / spec.destination,
            root_name="Klipper root",
        )



def _relative_path_parts(path: str | Path) -> tuple[str, ...]:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(
            f"Allowed symlink path must be relative and contained: {path}"
        )
    return relative.parts



def _manifest_runtime_targets(*, paths: RuntimePaths, manifest: Manifest) -> set[Path]:
    targets: set[Path] = {
        paths.config_root,
        paths.printer_data_root / manifest.backup.source_directory,
        paths.printer_data_root / manifest.state_file,
        paths.printer_data_root / manifest.managed_tree.destination,
    }
    targets.update(paths.printer_data_root / path for path in manifest.install.ensure_directories)
    targets.update(paths.printer_data_root / spec.file for spec in manifest.install.ensure_lines)
    targets.update(paths.printer_data_root / path for path in manifest.preflight.required_files)
    targets.update(paths.printer_data_root / spec.file for spec in manifest.preflight.required_sections)
    targets.update(paths.printer_data_root / spec.file for spec in manifest.preflight.required_lines)
    targets.update(paths.printer_data_root / spec.file for spec in manifest.postflight.verify_lines)
    targets.update(paths.printer_data_root / patch.file for patch in manifest.patches.set_options)
    targets.update(paths.printer_data_root / patch.file for patch in manifest.patches.delete_sections)
    for patch in manifest.install.source_patches:
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root,
            target=paths.managed_klipper_root / patch.destination,
        )
    return targets
