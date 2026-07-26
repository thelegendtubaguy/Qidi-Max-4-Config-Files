## 1. Production Homing Payload

- [x] 1.1 Add the production QIDI `homing.py` payload under `installer/klipper/` with X/Y entry waits at `100 ms`, recovery waits at `50 ms`, XY pre-axis dwell at `250 ms`, and non-XY dwell at `1 s`.
- [x] 1.2 Verify the payload retains two-strike validation, retries, retract behavior, controller command order, and contains no temporary timing instrumentation or marker commands.
- [x] 1.3 Add a deterministic payload compile/hash check for desired SHA-256 `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3`.
- [x] 1.4 Add firmware fixtures for `.03` stock hash `89428b465b7f3d62bd8b65b3155b8aa8e93cd917f59779e40a246b5d89ff8d71` and `.04` stock hash `ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0`.

## 2. Source-Patch Manifest and State

- [x] 2.1 Extend installer models and manifest parsing with firmware-scoped `install.source_patches[]` source, destination, expected-hash, and desired-hash fields.
- [x] 2.2 Restrict source-patch bundle paths to `installer/klipper/` and destinations to relative non-traversing `klippy/extras/` paths.
- [x] 2.3 Add the `homing.py` source-patch entry and both firmware variants to `installer/package.yaml`.
- [x] 2.4 Extend schema-version-1 installed-state parsing/writing with optional source-patch records containing original bytes/hash/mode and installed desired hash.
- [x] 2.5 Preserve compatibility with valid prior state ledgers that omit source-patch records.
- [x] 2.6 Add fail-closed state validation for duplicate IDs/destinations, normalized destination roots, strict base64, decoded-byte hashes, mode range, firmware binding, desired-hash compatibility, supported classifications, and original hashes bound to firmware stock or explicitly enumerated supported-upgrade baselines.
- [x] 2.7 Add supported-upgrade validation for source-patch IDs, destinations, carried original preimages, and prior desired hashes.
- [x] 2.8 Add malformed and tampered source-ledger tests covering every validation field, including self-consistent arbitrary original bytes/hash not present in the baseline allowlist, before install and uninstall writes.

## 3. Guarded Source Preflight and Deployment

- [x] 3.1 Add source-patch classification for stock, already desired, prior-managed upgrade, original no-op, and unsupported drift.
- [x] 3.2 Extend install and uninstall path-safety checks to reject symlinked source destinations and path components under `/home/qidi/klipper`.
- [x] 3.3 Extend preflight reporting and free-space accounting for source payloads, preimages, atomic temp files, and backup entries.
- [x] 3.4 Implement same-directory atomic source deployment with mode preservation and post-write SHA-256 verification.
- [x] 3.5 Carry the first original source preimage through reinstall and future desired-payload upgrades.
- [x] 3.6 Add guarded uninstall restore that preserves unknown live drift and verifies restored bytes.

## 4. Backup, Rollback, and Restore

- [x] 4.1 Extend installer backup archives with a versioned allowlisted external-file manifest that uniquely binds ID, destination, member, SHA-256, and mode.
- [x] 4.2 Add backup-label/package-version provenance rules that permit manifest omission only for explicitly listed pre-source-patch archives and require complete external metadata for `26.07.26.1`, later, unknown-format, or source-ledger archives.
- [x] 4.3 Track source destinations and the process-restart marker in install and uninstall rollback journals.
- [x] 4.4 Extend rollback postflight and recovery sentinel records to identify failed external source restoration.
- [x] 4.5 Extend recovery-sentinel clear verification across the config snapshot and declared external source snapshots.
- [x] 4.6 Extend `restore.sh` staging, validation, atomic writes, and postflight verification for allowlisted external source entries.
- [x] 4.7 Add restore tests for source-inclusive archives, proven legacy config-only archives, stripped current-format manifests, duplicate identities/members, missing or extra members, non-regular/symlink entries, malformed hashes/modes, hash mismatch, traversal, unsupported destinations, source drift, and partial-write rollback.

## 5. Homing-Speed Migration

- [x] 5.1 Change the X/Y `homing_speed` desired values in `installer/package.yaml` from `65` to `100` while retaining stock expected value `50` and second-strike value `55.0`.
- [x] 5.2 Extend option-patch classification to migrate live `65` only when the valid prior patch ledger proves installer ownership.
- [x] 5.3 Carry the prior original expected value into the new ledger so uninstall restores stock instead of `65`.
- [x] 5.4 Add fresh-stock, prior-managed migration, already-desired, missing-ledger, and user-modified speed tests for X and Y on both firmware fixtures.

## 6. Verified Klipper Process Restart

- [x] 6.1 Add the mode-`0600` `.tltg_optimized_klipper_restart_required` marker and create it before managed Python writes/restores.
- [x] 6.2 Implement and fixture `GET /printer/info` parsing that requires `result.process_id` as a positive non-boolean integer and `result.state` as a string.
- [x] 6.3 Implement `POST /machine/services/restart` with content type `application/json` and JSON `{"service":"klipper"}` separately from the existing `/printer/restart` path.
- [x] 6.4 Add bounded polling that requires `state: ready` under a different process ID before clearing the pending marker.
- [x] 6.5 Add process-restart-specific prompts, status messages, failure guidance, and debug events without changing existing runtime warning strings unnecessarily.
- [x] 6.6 Make interactive install, uninstall, and restore select process restart whenever managed Python activation is pending.
- [x] 6.7 Make `--yes` installs perform a required process restart without prompting after idle preflight.
- [x] 6.8 Validate marker schema and reclassify every destination hash before marker-driven restart; block unknown drift without activating it.
- [x] 6.9 Add tests for verified restart, missing/malformed/boolean/zero/negative PID, unchanged PID, startup timeout, transient Moonraker failures, declined interactive restart, retained marker, drifted marker target, and successful retry.

## 7. Auto-Update Activation

- [x] 7.1 Process a retained pending marker before checksum fetch, matching-checksum return, or missing-state initialization; perform idle and source-hash validation before verified restart.
- [x] 7.2 Make required process-restart verification failure return a nonzero child installer result while preserving the installed ledger and pending marker.
- [x] 7.3 Verify `run_auto_update_check()` does not advance `latest_checksum` when process activation fails.
- [x] 7.4 Add auto-update tests for source-changing install, matching checksum plus pending marker, missing checksum state plus pending marker, checksum-fetch failure plus pending marker, already-desired source with pending activation, drifted marker target, successful PID replacement, failed restart, and later retry.
- [x] 7.5 Verify config-only auto-updates retain the existing restart scope and do not force a service-process restart without a pending marker.

## 8. Version and Documentation

- [x] 8.1 Run `python3 scripts/bump_installer_version.py 26.07.26.1` and inspect `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, and optimized `globals.cfg`.
- [x] 8.2 Update `docs/optimized_vs_stock.md` with functional X/Y speed, dwell, retained safety, and service-restart behavior.
- [x] 8.3 Update `docs/installer_runtime_contract.md` with source-patch, migration, backup, restart-marker, interactive, uninstall, and auto-update contracts.
- [x] 8.4 Update `docs/installer_restore_helper.md` with external source archive validation, restoration, verification, and process activation.
- [x] 8.5 Review `docs/gcode-paths/notes.md`, `docs/gcode-paths/start-print.path.json`, and generated start-print views; update branch-level invariants only if the path model changes and record why command timing alone does not alter generated branches.

## 9. Validation

- [x] 9.1 Run focused manifest, state, source-patch, migration, rollback, restore, restart, uninstall, and auto-update unit/integration tests.
- [x] 9.2 Run `python3 scripts/format_klipper_configs.py` after the versioned optimized globals change.
- [x] 9.3 Run `python3 scripts/check_installer_known_versions.py`.
- [x] 9.4 Run `python3 scripts/run_installer_core_tests.py` and fix all regressions.
- [x] 9.5 Run `python3 scripts/check_gcode_paths.py --write` and `python3 scripts/check_gcode_paths.py`; include generated files only when branch-level views change.
- [x] 9.6 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test` and verify the production source payload is included, compiles, and matches its manifest hash.
- [x] 9.7 Validate fresh install, `26.07.13.1` upgrade, auto-update, rollback, restore, and uninstall against `.03` and `.04` fixtures.
- [x] 9.8 Perform supervised repeated cold and heat-soaked X/Y homes on supported hardware, recording retry counts and tolerance differences; do not automate motion through the installer.
