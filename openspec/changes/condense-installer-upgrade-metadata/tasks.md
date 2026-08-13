## 1. Define cumulative compatibility metadata

- [ ] 1.1 Resolve every existing `supported_upgrade_sources.yaml` profile and capture the deduplicated union of allowed patch targets and source-patch provenance before removing the version mapping.
- [ ] 1.2 Replace the per-version compatibility models and parser with a schema-versioned cumulative envelope that rejects malformed paths, duplicate targets, duplicate source provenance, invalid hashes, and unsupported roots.
- [ ] 1.3 Replace `installer/supported_upgrade_sources.yaml` with the cumulative union while retaining the explicit `package.known_versions` admission list in `installer/package.yaml`.
- [ ] 1.4 Change manifest compatibility validation to require the current package version in `known_versions` and require current manifest patch targets and source variants to be subsets of the cumulative envelope.

## 2. Integrate state and lifecycle validation

- [ ] 2.1 Require prior installed state to match the manifest package ID and an explicitly known package version before install or reinstall preflight proceeds.
- [ ] 2.2 Validate install and uninstall patch ledgers directly against the cumulative target envelope rather than a release-specific profile.
- [ ] 2.3 Validate source ledgers directly against cumulative firmware/hash provenance while preserving destination, firmware, preimage, live-drift, and first-preimage checks.
- [ ] 2.4 Replace the enumerated config-only backup-version set with an admitted-version-aware `26.07.26.1` external-manifest boundary using parsed numeric version components and fail-closed handling for unknown or malformed versions.
- [ ] 2.5 Remove superseded release-profile lookup, inheritance, and legacy backup-version code after all install, uninstall, restore, and rollback call paths use the new representation.

## 3. Simplify release tooling

- [ ] 3.1 Change `scripts/bump_installer_version.py` so a release updates `package.version`, adds the new version once to `package.known_versions`, and updates optimized globals without adding a compatibility profile.
- [ ] 3.2 Update `scripts/check_installer_known_versions.py` and bundle validation to enforce current-manifest coverage by the cumulative envelope instead of exact version-map equality.
- [ ] 3.3 Update release-maintenance guidance that currently states each package version requires a matching supported-upgrade profile.

## 4. Preserve lifecycle coverage

- [ ] 4.1 Update existing compatibility parser and validation tests for the cumulative schema, including malformed entries, duplicates, missing current coverage, unknown versions, and wrong package IDs.
- [ ] 4.2 Extend existing lifecycle matrices to prove direct update and uninstall handling for the oldest admitted config-only release, a pre-source release, `26.07.26.1`, and the current release.
- [ ] 4.3 Cover cumulative historical-only patch targets and approved source provenance without permitting unknown targets, destinations, firmware/hash combinations, or live drift.
- [ ] 4.4 Cover backup archive classification immediately before and at `26.07.26.1`, plus unknown, malformed, and state-declared-source cases.
- [ ] 4.5 Update version-bump tests to prove ordinary releases do not rewrite the compatibility envelope and compatibility-affecting manifest additions fail validation until explicitly admitted.

## 5. Release and validate

- [ ] 5.1 Select the next package version, run `python3 scripts/bump_installer_version.py <version>`, and add the matching `CHANGELOG.md` section describing the metadata condensation with no lifecycle behavior change.
- [ ] 5.2 Run `python3 scripts/check_installer_known_versions.py` and `python3 scripts/run_installer_core_tests.py`.
- [ ] 5.3 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test`.
- [ ] 5.4 Run `openspec validate --all --strict` and inspect the authored-file diff for unintended release, compatibility, or vendor-content changes.
