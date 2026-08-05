## 1. Firmware Baseline and Snapshot

- [x] 1.1 Verify `.05` provenance from `Qidi-Max4-Defaults` commit `c75c0b662d1d4fd2a7dd19e49843b91e6544a1ed`, its firmware tag, and archive metadata.
- [x] 1.2 Build the complete 26-file `.05` stock snapshot under `installer/stock/qidi-max4-defaults/firmwares/01.01.06.05/config/` using repository line endings and existing comment translation/redaction conventions.
- [x] 1.3 Verify the `.05` snapshot excludes `MCU_ID.cfg`, `box.cfg`, `fluidd.cfg`, and `saved_variables.cfg`, includes the package-owned `M4031` Z-positioning sequence, sets the hotend tachometer interval to `0.0005`, and leaves polar-cooler/beeper smart-pin sections inactive.
- [x] 1.4 Update `installer/stock/qidi-max4-defaults/README.md` and bundle snapshot checks for `.03`, `.04`, and `.05`.

## 2. Installer Manifest and Compatibility Metadata

- [x] 2.1 Add `01.01.06.05` to `installer/package.yaml firmware.supported` and extend all 28 `.04` configuration patch variants to `.05` without changing expected or desired values.
- [x] 2.2 Add the `.05` `qidi_homing` source variant for stock SHA-256 `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761`, source `klipper/qidi/homing-sync-reset.py`, and desired SHA-256 `09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a`.
- [x] 2.3 Extend `installer/supported_upgrade_sources.yaml` with `.05` source provenance while keeping package and optimized-global version `26.08.05.1` unchanged.
- [x] 2.4 Confirm no installer runtime schema, legacy snapshot-selection, slicer G-code, or start-print path-contract source requires modification.

## 3. Installer and Macro Contract Tests

- [x] 3.1 Extend installer fixture helpers to construct `.05` stock configuration and the sync-reset homing preimage.
- [x] 3.2 Add fresh-install and guarded-patch coverage proving `.05` is accepted, every active target selects one variant, and `.03`/`.04` behavior remains unchanged.
- [x] 3.3 Add legacy migration/reset coverage proving `.05` selects only its snapshot and a missing `.05` snapshot fails before overwrite.
- [x] 3.4 Extend managed-source unit and lifecycle matrices for `.05` install, reinstall, uninstall, restore, rollback, prior-managed migration, and rejection of unknown or `.04`-standard homing content.
- [x] 3.5 Extend bundle smoke validation to require all three firmware snapshot roots and their sanitized contents.
- [x] 3.6 Add optimized macro contract assertions that no smart-pin object or P4 pause/resume mutation is introduced, while existing direct P4 start/helper/end/cancel behavior remains available.

## 4. Specifications and External Observations

- [x] 4.1 Sync the `.05` firmware admission, snapshot, and source-provenance requirements into `openspec/specs/installer-lifecycle/spec.md`.
- [x] 4.2 Sync `.05` sync-reset homing and direct-output-pin pause/resume policy into `openspec/specs/optimized-printer-behavior/spec.md`.
- [x] 4.3 Update `openspec/observations/qidi-platform.md` with config-confirmed `.05` behavior and qualified qidiclient binary, asset, service, networking, firmware UI, and error-presentation evidence.
- [x] 4.4 Update relevant `openspec/observations/qidi-box/` files with qualified `.05` load detection, retry, unload-state, slot-selection, external-spool flow, and missing metadata default evidence without treating recovered behavior as repository requirements.
- [x] 4.5 Record that static-GIF runtime animation behavior and any qidiclient-side P4 intervention remain unresolved and do not gate installer support.

## 5. Validation and Review

- [x] 5.1 Run `python3 scripts/check_installer_known_versions.py` and resolve all manifest and supported-upgrade coverage errors.
- [x] 5.2 Run `python3 scripts/run_installer_core_tests.py` and resolve all failures.
- [x] 5.3 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test` and verify the bundle contains sanitized `.03`, `.04`, and `.05` snapshots.
- [x] 5.4 Run `python3 scripts/check_gcode_paths.py` and document that generated start-path views remain unchanged.
- [x] 5.5 Run `openspec validate support-qidi-firmware-01-01-06-05`, `openspec validate --all --strict`, and authored-file whitespace validation.
- [x] 5.6 Review the final diff for unredacted identifiers, excluded printer files, real email addresses outside approved upstream headers, unintended `config/` or `config/fluidd.cfg` edits, package-version drift, and unsupported claims about compiled vendor behavior.
