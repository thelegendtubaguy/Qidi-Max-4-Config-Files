## 1. Guarded Tachometer Configuration

- [x] 1.1 Add a firmware-variant `patches.set_options` entry for `[heater_fan hotend_fan] tachometer_poll_interval`, accepting `0.0015` on firmware `.03`/`.04` and `0.0005` on `.05`, with `0.00075` as the shared desired value.
- [x] 1.2 Bump installer package and optimized macro version metadata consistently without modifying stock-mapped `config/printer.cfg`.

## 2. Installer Lifecycle Coverage

- [x] 2.1 Add manifest and fixture assertions that each supported firmware selects its declared stock hotend-fan interval and converges to `0.00075`.
- [x] 2.2 Add upgrade coverage for a prior package whose ledger does not own the stock hotend-fan interval converging to the new desired value.
- [x] 2.3 Add uninstall and drift coverage proving the recorded firmware preimage is restored only while the live desired value remains installer-owned.

## 3. Specifications and Evidence

- [x] 3.1 Merge the hotend fan tachometer sampling requirement into `openspec/specs/optimized-printer-behavior/spec.md`.
- [x] 3.2 Record firmware baseline values, the `140 °C` controlled test, `13,553 RPM` startup maximum, and `12,990–13,135 RPM` stable range in `openspec/observations/qidi-platform.md` with runtime-evidence qualification.

## 4. Validation

- [x] 4.1 Run `python3 scripts/format_klipper_configs.py` and verify no unrelated Klipper configuration changes.
- [x] 4.2 Run `python3 scripts/check_installer_known_versions.py` and `python3 scripts/run_installer_core_tests.py`.
- [x] 4.3 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test`.
- [x] 4.4 Run `openspec validate --all --strict` and authored-file whitespace validation.
