## Context

`installer/package.yaml` declares the current package version and 29 `package.known_versions`. `installer/supported_upgrade_sources.yaml` repeats complete uninstall target and source-patch provenance profiles for those versions. The 29 entries currently resolve to 10 distinct profiles, occupy approximately 51 KB and 1,448 lines, and all resolve to subsets of the `26.08.09.2` profile.

Direct install validates a prior state's package version against `package.known_versions`. Upgrade and uninstall use the per-version compatibility entry to admit patch-ledger target tuples and source-ledger firmware/hash provenance. Every admitted release converges directly to the current manifest; no sequential migration chain executes.

`installer/runtime/backup.py` separately enumerates releases whose archives may omit the external-source manifest. Historical release bundles remain available from GitHub and do not need to be reconstructed by the current installer.

## Goals / Non-Goals

**Goals:**

- Keep exact admission of explicitly released package versions.
- Represent historical ownership validation with one cumulative compatibility envelope.
- Make an ordinary version bump add a version identifier without copying a compatibility profile.
- Preserve fail-closed validation of package identity, ledger targets, source destinations, firmware, hashes, and backup format.
- Preserve direct upgrade, reinstall, uninstall, rollback, and restore paths for every currently admitted version.

**Non-Goals:**

- Remove historical version identifiers or changelog entries.
- Permit arbitrary version ranges or unknown package versions.
- Support installing an older release through the current installer.
- Rewrite Git history or materially reduce clone size.
- Change installer-owned targets, desired configuration, source payloads, or operator-visible lifecycle behavior.

## Decisions

### 1. Keep explicit package-version admission

Retain `package.known_versions` as the complete set of package versions accepted in installed state, including the current version. Direct install and uninstall additionally require `state.package_id == manifest.package.id`.

An explicit list is preferred over a minimum version or version range. Date-like comparison would admit unpublished, malformed-but-sortable, or otherwise unreviewed package identities. Historical release identifiers cost one short line per release and provide a clear support boundary.

### 2. Replace version profiles with one cumulative envelope

Change `installer/supported_upgrade_sources.yaml` from a `versions` mapping to one compatibility envelope containing:

- `allowed_patch_targets`: the union of target tuples that may appear in a valid historical patch ledger;
- `source_patches`: the union of approved source-patch identities, destinations, firmware baselines, original hashes, and desired hashes.

The schema version advances because the document shape and parser contract change. Compatibility models represent the envelope directly rather than materializing an `UpgradeSource` for each package version.

The initial envelope is generated from the union of every currently supported profile before the old mapping is removed. The current data resolves to the latest profile, but union generation avoids relying on that incidental ordering and protects historical-only entries.

A cumulative envelope is preferred over inheritance chains or deduplicated profile aliases. Aliases would reduce file size but preserve release-to-profile maintenance and parser complexity. The envelope records the actual trust boundary: a known package version plus recognized installer-owned ledger content.

This intentionally stops asserting that a particular target or source record first appeared in one exact historical release. Safety remains content-based: unknown versions, targets, destinations, firmware/hash provenance, malformed state, and live drift fail closed. Exact historical manifests remain in their release artifacts and Git history.

### 3. Validate current manifest coverage without requiring equality

Compatibility validation requires:

- `manifest.package.version` to occur exactly once in `package.known_versions`;
- every current manifest patch target to be present in the cumulative target envelope;
- every current manifest source variant to be present in the cumulative source envelope;
- no duplicate or malformed envelope entries.

Envelope entries not present in the current manifest are valid because uninstalling an older installed state may require recognizing ownership that the current release no longer creates.

Runtime validation requires:

```text
state.package_id       == manifest.package.id
state.package_version  in manifest.package.known_versions
state.patch_ledger     subset of allowed_patch_targets
state.source_patches   subset of approved source_patches
state.runtime_firmware == detected firmware where firmware is required
```

Install classification still uses the current manifest and prior ledger to converge live files. Uninstall still restores only ledger entries whose live values remain installer-owned.

### 4. Make version bumps append identifiers only

`scripts/bump_installer_version.py` updates:

- `package.version`;
- `package.known_versions`, adding the new version once;
- optimized `variable_package_version`.

It does not modify the compatibility envelope for an ordinary macro or installer release. A change that introduces a new patch target or source provenance must update the envelope explicitly in the same release. Final compatibility validation detects omission because the current manifest must be covered by the envelope.

This keeps one command as the version authority while preventing automatic duplication of historical metadata.

### 5. Express backup compatibility with an admitted-version boundary

Replace `LEGACY_CONFIG_ONLY_PACKAGE_VERSIONS` with a named external-manifest introduction version, currently `26.07.26.1`, evaluated only after confirming that the parsed package version occurs in `package.known_versions` or equivalent validated compatibility input.

An archive requires an external manifest when any of these conditions is true:

- installed state declares source patches;
- the package version is absent or not explicitly admitted;
- the package version is at or after the external-manifest introduction boundary.

Numeric four-component versions are parsed into integer tuples for boundary comparison; string comparison is not used. This preserves rejection of unknown old-looking versions while removing a second historical version enumeration.

If passing manifest compatibility into archive validation creates undesirable coupling, the compact alternative is to retain one explicit set of config-only versions. The preferred implementation centralizes admitted versions so package compatibility and backup compatibility cannot diverge.

### 6. Preserve historical-release installation through release artifacts

The current bundle installs only its own `package.version`. `package.known_versions` means “accepted installed-state sources,” not “versions this bundle can install.” Selecting an old GitHub release remains the supported path for installing that old release.

`CHANGELOG.md` remains unchanged except for the normal entry required by the package version shipped with the implementation.

## Risks / Trade-offs

- **[The cumulative envelope accepts a historically impossible version/ledger combination]** → Require an explicitly admitted package version and validate every ledger element against the same ownership and provenance constraints used today. Do not treat the state version as proof of ownership. Package identity validation narrows admission further.
- **[A historical-only ownership target is accidentally dropped]** → Build the initial envelope as the set union of all existing resolved profiles and add a migration test comparing old-profile unions to the new envelope.
- **[A new manifest target is omitted from the envelope]** → Make current-manifest coverage a mandatory compatibility and bundle-build check.
- **[Unknown pre-source release labels bypass external archive metadata]** → Apply the format boundary only to explicitly admitted versions; unknown versions continue requiring the external manifest.
- **[Version ordering becomes ambiguous]** → Restrict boundary parsing to the existing four-component numeric package format and fail closed for nonconforming values.
- **[Repository-size expectations exceed the result]** → The change removes approximately 46 KB of source metadata and substantial maintenance duplication; Git history and compressed release bundles see negligible size reduction.

## Migration Plan

1. Resolve every current version profile and calculate the union of patch targets and source provenance.
2. Add parser and model support for the cumulative compatibility schema, installed package-ID admission, and current-manifest subset validation.
3. Replace the compatibility YAML with the union envelope and verify it equals the union captured from the old schema.
4. Update install, uninstall, source-state, backup-format, version-bump, and compatibility-check call paths.
5. Update lifecycle and bundle tests to cover the oldest admitted config-only release, a pre-source release, the source-patch introduction release, the current release, an unknown version, a wrong package ID, historical-only ledger content, and current manifest coverage failure.
6. Remove superseded per-version profile and legacy backup-version parsing code.
7. Advance the package version with `scripts/bump_installer_version.py`, add the matching changelog section, and run installer compatibility, core, bundle smoke, and OpenSpec validation.

Rollback is a source revert: the on-printer installed-state schema and historical release bundles are unchanged, so reverting the parser and metadata restores the prior release-to-profile representation.
