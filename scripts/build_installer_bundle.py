#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.compatibility import load_supported_upgrade_sources, validate_manifest_compatibility
from installer.runtime.manifest import load_manifest
from installer.runtime.naming import BUNDLE_NAME_PREFIX, BUNDLE_ROOT_NAME
ALLOWED_FILES = [
    "installer/package.yaml",
    "installer/supported_upgrade_sources.yaml",
    "installer/release/install.sh",
    "installer/release/restore.sh",
    "installer/release/auto-update.sh",
]
ALLOWED_DIRECTORIES = [
    "installer/runtime",
    "installer/klipper/tltg-optimized-macros",
    "installer/klipper/qidi",
    "installer/stock",
    "installer/system",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--channel", choices=["release", "dev"], required=True)
    parser.add_argument("--build-id")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    compatibility = load_supported_upgrade_sources(
        REPO_ROOT / "installer/supported_upgrade_sources.yaml"
    )
    validate_manifest_compatibility(manifest, compatibility)
    validate_source_payloads(manifest)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_root = REPO_ROOT / "build/installer-bundle"
    bundle_root = staging_root / BUNDLE_ROOT_NAME
    if staging_root.exists():
        shutil.rmtree(staging_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    for file_path in ALLOWED_FILES:
        source = REPO_ROOT / file_path
        destination = bundle_root / file_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.copy2(REPO_ROOT / "installer/release/install.sh", bundle_root / "install.sh")
    shutil.copy2(REPO_ROOT / "installer/release/restore.sh", bundle_root / "restore.sh")
    shutil.copy2(REPO_ROOT / "installer/release/auto-update.sh", bundle_root / "auto-update.sh")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for directory in ALLOWED_DIRECTORIES:
        source = REPO_ROOT / directory
        destination = bundle_root / directory
        shutil.copytree(source, destination, ignore=ignore)

    (bundle_root / "RELEASE_METADATA.yaml").write_text(
        render_release_metadata(manifest.package.version, args.channel, args.build_id),
        encoding="utf-8",
    )

    stable_name, traceable_name = bundle_names(
        channel=args.channel,
        package_version=manifest.package.version,
        build_id=args.build_id,
    )
    stable_archive = output_dir / stable_name
    traceable_archive = output_dir / traceable_name

    create_archive(bundle_root, stable_archive)
    if traceable_archive != stable_archive:
        shutil.copy2(stable_archive, traceable_archive)

    write_sha256(stable_archive)
    if traceable_archive != stable_archive:
        write_sha256(traceable_archive)

    validate_archive(stable_archive)
    if traceable_archive != stable_archive:
        validate_archive(traceable_archive)

    if args.smoke_test:
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/smoke_test_installer_bundle.py"), "--archive", str(stable_archive)],
            check=True,
        )

    print(stable_archive)
    if traceable_archive != stable_archive:
        print(traceable_archive)
    return 0



def validate_source_payloads(manifest) -> None:
    for patch in manifest.install.source_patches:
        for variant in patch.variants:
            payload = REPO_ROOT / "installer" / variant.source
            if payload.is_symlink() or not payload.is_file():
                raise ValueError(
                    f"Source patch payload is not a regular file: {payload}"
                )
            value = payload.read_bytes()
            compile(value, str(payload), "exec")
            digest = hashlib.sha256(value).hexdigest()
            if digest != variant.desired_sha256:
                raise ValueError(
                    "Source patch payload hash does not match "
                    f"{patch.id} for {variant.firmware} stock "
                    f"{variant.expected_sha256}"
                )


def bundle_names(*, channel: str, package_version: str, build_id: str | None) -> tuple[str, str]:
    if channel == "release":
        stable = f"{BUNDLE_NAME_PREFIX}.tar.gz"
        versioned = f"{BUNDLE_NAME_PREFIX}-{package_version}.tar.gz"
        return stable, versioned
    trace = build_id or git_short_sha()
    stable = f"{BUNDLE_NAME_PREFIX}-dev.tar.gz"
    traceable = f"{BUNDLE_NAME_PREFIX}-dev-{trace}.tar.gz"
    return stable, traceable



def create_archive(bundle_root: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as tar:
        for file_path in sorted(item for item in bundle_root.rglob("*") if item.is_file()):
            relative = file_path.relative_to(bundle_root.parent)
            info = tar.gettarinfo(str(file_path), arcname=relative.as_posix())
            if not info.isreg():
                raise ValueError(f"Only regular files are allowed in bundle archive: {relative}")
            with file_path.open("rb") as handle:
                tar.addfile(info, handle)



def validate_archive(archive_path: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/"):
                raise ValueError(f"Archive entry has absolute path: {member.name}")
            member_path = Path(member.name)
            if member_path.parts[0] != BUNDLE_ROOT_NAME:
                raise ValueError(f"Archive entry escapes bundle root: {member.name}")
            if any(part == ".." for part in member_path.parts):
                raise ValueError(f"Archive entry contains ..: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"Archive entry type is not allowed: {member.name}")



def render_release_metadata(package_version: str, channel: str, build_id: str | None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = git_commit()
    lines = [
        f"package_version: \"{package_version}\"",
        f"channel: \"{channel}\"",
        f"git_commit: \"{commit}\"",
        f"git_short_sha: \"{commit[:7]}\"",
        f"build_timestamp: \"{timestamp}\"",
    ]
    if build_id:
        lines.append(f"build_id: \"{build_id}\"")
    return "\n".join(lines) + "\n"



def write_sha256(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checksum_path = path.with_name(path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")



def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()



def git_short_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(main())
