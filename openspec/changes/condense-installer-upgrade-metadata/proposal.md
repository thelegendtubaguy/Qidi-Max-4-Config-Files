## Why

The current installer repeats complete ownership and source-provenance metadata for every historical package version, even though each supported version upgrades directly to the current release. This makes release metadata and version bumps progressively larger without materially improving admission safety.

## What Changes

- Retain an explicit list of historical package versions accepted as direct upgrade sources.
- Replace per-version patch-target and source-provenance profiles with one cumulative compatibility envelope used to validate historical installed-state ledgers.
- Keep the current release manifest authoritative for installation and convergence to the current package version.
- Change release version tooling so ordinary releases add the new package version to the accepted-source list without duplicating the compatibility envelope.
- Preserve historical release bundles as the way to install or run an older release; the current installer will not reconstruct historical manifests.
- Replace enumerated config-only backup versions with a format boundary or equivalent compact format rule while preserving validation of historical archives.
- Validate installed-state package identity alongside version and ledger compatibility.
- Preserve fail-closed upgrade, reinstall, uninstall, rollback, restore, and source-provenance safety guarantees.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This change refactors installer compatibility metadata without changing operator-visible lifecycle requirements.

## Impact

Affected areas include `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, compatibility and installed-state validation under `installer/runtime/`, backup-format classification, `scripts/bump_installer_version.py`, `scripts/check_installer_known_versions.py`, installer lifecycle tests, bundle validation, and release-maintenance guidance. `CHANGELOG.md` remains historical release documentation and is not condensed.
