#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.compatibility import (
    CompatibilityValidationError,
    load_supported_upgrade_sources,
    validate_manifest_compatibility,
)
from installer.runtime.manifest import ManifestValidationError, load_manifest
from installer.runtime.models import Manifest

PACKAGE_PATH = Path("installer/package.yaml")
UPGRADE_SOURCES_PATH = Path("installer/supported_upgrade_sources.yaml")
GLOBALS_PATH = Path("installer/klipper/tltg-optimized-macros/globals.cfg")
SAFE_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


class VersionBumpError(ValueError):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update installer package version metadata for a release."
    )
    parser.add_argument("version", help="New package version, e.g. 26.05.04.1")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip final installer compatibility validation.",
    )
    args = parser.parse_args(argv)

    try:
        changed = bump_version(
            REPO_ROOT,
            args.version,
            validate=not args.no_validate,
        )
    except (CompatibilityValidationError, ManifestValidationError, VersionBumpError) as exc:
        raise SystemExit(str(exc)) from exc

    for path in changed:
        print(path)
    if not changed:
        print("installer version metadata already current")
    return 0


def bump_version(repo_root: Path, version: str, *, validate: bool = True) -> list[str]:
    _validate_version(version)

    package_path = repo_root / PACKAGE_PATH
    upgrade_sources_path = repo_root / UPGRADE_SOURCES_PATH
    globals_path = repo_root / GLOBALS_PATH

    manifest = load_manifest(package_path)
    changed: list[str] = []

    if _update_file(package_path, _update_package_yaml(package_path.read_text(encoding="utf-8"), version)):
        changed.append(str(PACKAGE_PATH))
    if _update_file(
        upgrade_sources_path,
        _update_upgrade_sources(
            upgrade_sources_path.read_text(encoding="utf-8"), version, manifest
        ),
    ):
        changed.append(str(UPGRADE_SOURCES_PATH))
    if _update_file(globals_path, _update_globals(globals_path.read_text(encoding="utf-8"), version)):
        changed.append(str(GLOBALS_PATH))

    if validate:
        updated_manifest = load_manifest(package_path)
        compatibility = load_supported_upgrade_sources(upgrade_sources_path)
        validate_manifest_compatibility(updated_manifest, compatibility)
        globals_text = globals_path.read_text(encoding="utf-8")
        match = re.search(
            r'^variable_package_version:\s*["\']?([^"\'\n]+)["\']?\s*$',
            globals_text,
            re.MULTILINE,
        )
        if match is None or match.group(1) != updated_manifest.package.version:
            raise VersionBumpError(
                "Optimized globals package version does not match installer/package.yaml."
            )

    return changed


def _validate_version(version: str) -> None:
    if not SAFE_VERSION_PATTERN.fullmatch(version):
        raise VersionBumpError(
            "Version must start with an alphanumeric character and contain only "
            "letters, numbers, dots, underscores, and hyphens."
        )


def _update_package_yaml(text: str, version: str) -> str:
    text, count = re.subn(
        r'(?m)^(  version:\s*)["\']?[^"\'\n]+["\']?\s*$',
        rf'\1"{version}"',
        text,
        count=1,
    )
    if count != 1:
        raise VersionBumpError("Could not update package.version in installer/package.yaml.")

    known_match = re.search(r'(?m)^(  known_versions:\n)((?:    - ["\'][^"\'\n]+["\']\n)+)', text)
    if known_match is None:
        raise VersionBumpError("Could not find package.known_versions in installer/package.yaml.")
    known_versions = set(re.findall(r'    - ["\']([^"\'\n]+)["\']', known_match.group(2)))
    if version in known_versions:
        return text
    insert_at = known_match.end(2)
    return text[:insert_at] + f'    - "{version}"\n' + text[insert_at:]


def _update_upgrade_sources(text: str, version: str, manifest: Manifest) -> str:
    if re.search(rf'(?m)^  "{re.escape(version)}":\n', text):
        return text
    separator = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
    return text + separator + _render_upgrade_source_block(version, manifest)


def _render_upgrade_source_block(version: str, manifest: Manifest) -> str:
    lines = [
        f'  "{version}":',
        f'    inherits: "{manifest.package.version}"',
    ]
    if manifest.install.source_patches:
        lines.append("")
        lines.append("    source_patches:")
        for patch in manifest.install.source_patches:
            for variant in patch.variants:
                lines.extend(
                    [
                        f'      - id: "{patch.id}"',
                        f'        destination: "{patch.destination}"',
                        f'        firmware: "{variant.firmware}"',
                        f'        original_sha256: "{variant.expected_sha256}"',
                        f'        desired_sha256: "{variant.desired_sha256}"',
                    ]
                )
    return "\n".join(lines) + "\n"


def _update_globals(text: str, version: str) -> str:
    text, count = re.subn(
        r'(?m)^variable_package_version:\s*["\']?[^"\'\n]+["\']?\s*$',
        f'variable_package_version: "{version}"',
        text,
        count=1,
    )
    if count != 1:
        raise VersionBumpError(
            "Could not update variable_package_version in optimized globals.cfg."
        )
    return text


def _update_file(path: Path, new_text: str) -> bool:
    old_text = path.read_text(encoding="utf-8")
    if old_text == new_text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
