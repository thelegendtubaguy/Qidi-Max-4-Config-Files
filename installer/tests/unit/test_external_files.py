from __future__ import annotations

import base64
import os
import unittest

import yaml

from installer.runtime import external_files
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.errors import ExternalFileError
from installer.runtime.manifest import load_manifest
from installer.runtime.models import ExternalFileState, InstalledState, ManagedTreeState
from installer.runtime.state_file import (
    StateValidationError,
    load_installed_state,
    parse_installed_state,
    write_installed_state,
)
from installer.tests.helpers import REPO_ROOT, build_env, copy_base_runtime, MOONRAKER_QUERY_URL


class ExternalFileTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.printer_root = copy_base_runtime()
        self.paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(self.printer_root, moonraker_url=MOONRAKER_QUERY_URL),
        )
        self.spec = self.manifest.install.external_files[0]
        self.compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )
        self.destination = self.paths.klipper_root / self.spec.destination

    def test_manifest_parses_hash_pinned_external_file(self):
        self.assertEqual(self.spec.id, "tltg_pa_calibration_extra")
        self.assertEqual(self.spec.source, "klipper/extras/tltg_pa_calibration.py")
        self.assertEqual(self.spec.destination, "klippy/extras/tltg_pa_calibration.py")
        external_files.validate_install(paths=self.paths, specs=(self.spec,), prior_state=None)

    def test_matching_untracked_file_is_adopted_only_for_enrolled_recovery(self):
        self.destination.write_bytes(
            (self.paths.installer_root / self.spec.source).read_bytes()
        )

        with self.assertRaises(ExternalFileError):
            external_files.validate_install(
                paths=self.paths, specs=(self.spec,), prior_state=None
            )

        external_files.validate_install(
            paths=self.paths,
            specs=(self.spec,),
            prior_state=None,
            allow_matching_untracked=True,
        )

    def test_historical_state_requires_versioned_external_file_provenance(self):
        baselines = {
            "26.07.26.2": "c46135aeddacc1dc6653dce6edfdc61de8a61a4784d680925e46816234773ee1",
            "26.07.26.3": "b6af2f05a1f635a5cc71398e6e9456197b0c1cbd63dad1d0c0b27b278d93e191",
            "26.07.26.4": "b6af2f05a1f635a5cc71398e6e9456197b0c1cbd63dad1d0c0b27b278d93e191",
            "26.07.26.5": "b6af2f05a1f635a5cc71398e6e9456197b0c1cbd63dad1d0c0b27b278d93e191",
            "26.07.26.6": "6cc0a0e71331c7818d6ab624757f60794a3815188f18cc5e1d58c61d295e27d4",
            "26.07.26.7": "6cc0a0e71331c7818d6ab624757f60794a3815188f18cc5e1d58c61d295e27d4",
            "26.07.26.8": "6cc0a0e71331c7818d6ab624757f60794a3815188f18cc5e1d58c61d295e27d4",
            "26.07.26.9": "6cc0a0e71331c7818d6ab624757f60794a3815188f18cc5e1d58c61d295e27d4",
            "26.07.26.10": "942f4b5f2f12fd53e7be7694d2dd123e62e3d9aaae220550fbef2bf98de73c35",
            "26.07.26.11": "942f4b5f2f12fd53e7be7694d2dd123e62e3d9aaae220550fbef2bf98de73c35",
            "26.07.26.12": "79b4c849b169a059148b9c2171c21692ef6dbd3b9f90b52a14c351335ebb4994",
            "26.07.26.13": "79b4c849b169a059148b9c2171c21692ef6dbd3b9f90b52a14c351335ebb4994",
            "26.07.26.14": "cbbaa9a114b88e5c3e169a088060b1deeccfd20198e899f43026ef791720af2f",
        }
        all_hashes = set(baselines.values())
        for package_version, installed_sha256 in baselines.items():
            with self.subTest(package_version=package_version):
                known = ExternalFileState(
                    id=self.spec.id,
                    destination=self.spec.destination,
                    installed_sha256=installed_sha256,
                )
                external_files.validate_state_provenance(
                    state=_installed_state(
                        (known,), package_version=package_version
                    ),
                    specs=(self.spec,),
                    upgrade_sources=self.compatibility,
                )
                wrong = ExternalFileState(
                    id=self.spec.id,
                    destination=self.spec.destination,
                    installed_sha256=next(
                        value
                        for value in sorted(all_hashes)
                        if value != installed_sha256
                    ),
                )
                with self.assertRaises(ExternalFileError):
                    external_files.validate_state_provenance(
                        state=_installed_state(
                            (wrong,), package_version=package_version
                        ),
                        specs=(self.spec,),
                        upgrade_sources=self.compatibility,
                    )

    def test_process_restart_is_required_only_when_external_file_code_changes(self):
        self.assertTrue(
            external_files.install_requires_process_restart(
                specs=(self.spec,), prior_state=None
            )
        )
        current = external_files.planned_state(specs=(self.spec,), prior_state=None)
        self.assertFalse(
            external_files.install_requires_process_restart(
                specs=(self.spec,), prior_state=_installed_state(current)
            )
        )
        old = ExternalFileState(
            id=self.spec.id,
            destination=self.spec.destination,
            installed_sha256="0" * 64,
        )
        self.assertTrue(
            external_files.install_requires_process_restart(
                specs=(self.spec,), prior_state=_installed_state((old,))
            )
        )

    def test_deploy_and_remove_unchanged_managed_file(self):
        state = external_files.planned_state(specs=(self.spec,), prior_state=None)
        external_files.deploy(paths=self.paths, spec=self.spec)
        external_files.verify_install(paths=self.paths, state=state)

        external_files.remove_or_restore(paths=self.paths, record=state[0])

        self.assertFalse(self.destination.exists())

    def test_restore_recorded_preimage(self):
        original = b"original private extra\n"
        managed = b"managed replacement\n"
        self.destination.write_bytes(managed)
        os.chmod(self.destination, 0o640)
        record = ExternalFileState(
            id="managed_extra",
            destination=self.spec.destination,
            installed_sha256=external_files.sha256_bytes(managed),
            preimage_b64=base64.b64encode(original).decode("ascii"),
            preimage_sha256=external_files.sha256_bytes(original),
            preimage_mode=0o600,
        )

        external_files.remove_or_restore(paths=self.paths, record=record)

        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_state_file_round_trips_external_file_preimage(self):
        original = b"original\n"
        record = ExternalFileState(
            id="managed_extra",
            destination=self.spec.destination,
            installed_sha256=self.spec.sha256,
            preimage_b64=base64.b64encode(original).decode("ascii"),
            preimage_sha256=external_files.sha256_bytes(original),
            preimage_mode=0o640,
        )
        state = InstalledState(
            schema_version=1,
            package_id="pkg",
            package_version="1",
            runtime_firmware="firmware",
            backup_label="backup",
            installed_at="2026-05-04T00:00:00Z",
            managed_tree=ManagedTreeState(root="config/tltg-optimized-macros", files=()),
            patch_ledger=(),
            external_files=(record,),
        )
        path = self.printer_root / "state.yaml"

        write_installed_state(path, state)

        self.assertEqual(load_installed_state(path).external_files, (record,))

    def test_state_parser_rejects_malformed_external_preimages(self):
        original = b"original\n"
        record = ExternalFileState(
            id="managed_extra",
            destination=self.spec.destination,
            installed_sha256=self.spec.sha256,
            preimage_b64=base64.b64encode(original).decode("ascii"),
            preimage_sha256=external_files.sha256_bytes(original),
            preimage_mode=0o640,
        )
        state = InstalledState(
            schema_version=1,
            package_id="pkg",
            package_version="1",
            runtime_firmware="firmware",
            backup_label="backup",
            installed_at="2026-05-04T00:00:00Z",
            managed_tree=ManagedTreeState(root="config/tree", files=()),
            patch_ledger=(),
            external_files=(record,),
        )
        path = self.printer_root / "state-malformed.yaml"
        write_installed_state(path, state)
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = (
            ("preimage_b64", "not:base64"),
            ("preimage_sha256", "0" * 64),
            ("preimage_mode", True),
        )
        for key, value in cases:
            with self.subTest(key=key):
                candidate = yaml.safe_load(yaml.safe_dump(document))
                candidate["external_files"][0][key] = value
                with self.assertRaises(StateValidationError):
                    parse_installed_state(candidate)

    def test_uninstall_rejects_changed_managed_file(self):
        state = external_files.planned_state(specs=(self.spec,), prior_state=None)
        external_files.deploy(paths=self.paths, spec=self.spec)
        self.destination.write_text("drift\n", encoding="utf-8")

        with self.assertRaises(ExternalFileError):
            external_files.remove_or_restore(paths=self.paths, record=state[0])

        self.assertEqual(self.destination.read_text(encoding="utf-8"), "drift\n")


def _installed_state(external, *, package_version="1"):
    return InstalledState(
        schema_version=1,
        package_id="pkg",
        package_version=package_version,
        runtime_firmware="firmware",
        backup_label="backup",
        installed_at="2026-05-04T00:00:00Z",
        managed_tree=ManagedTreeState(
            root="config/tltg-optimized-macros", files=()
        ),
        patch_ledger=(),
        external_files=tuple(external),
    )


if __name__ == "__main__":
    unittest.main()
