## 1. Persistent Mesh Selection

- [x] 1.1 Add one private print-mesh helper that reads optional `tltg_start_bed_mesh_profile` directly from Klipper saved variables without creating or changing the preference.
- [x] 1.2 When the saved value is absent or empty, report fresh adaptive `kamp` calibration to the console and preserve the current `G31` calibration sequence.
- [x] 1.3 For a non-empty value, report the named saved profile to the console, run `G32`, clear stale mesh state, quote and forward the exact name to Klipper's saved-profile loader, skip calibration, and retain Klipper-owned missing-profile failure.
- [x] 1.4 Route retained-filament, fresh-Box, and external-spool branches through the shared helper while preserving offset, positioning, and sensor ordering.

## 2. Operator and Path Contracts

- [x] 2.1 Document `SAVE_VARIABLE` commands for selecting a named start mesh and clearing the preference in `README.md`.
- [x] 2.2 Keep OrcaSlicer and QIDI Studio G-code unchanged and enforce that neither pack configures `BED_MESH_PROFILE` or `tltg_start_bed_mesh_profile`.
- [x] 2.3 Update the start-print path contract for all shared-helper call sites and mutually exclusive adaptive and named-profile paths; regenerate generated views.
- [x] 2.4 Advance package, runtime, known-version, and upgrade-source metadata to `26.08.09.1`.

## 3. Focused Coverage

- [x] 3.1 Extend existing macro contract coverage for absent/empty fallback, exact saved-variable lookup, one mode-specific console report per path, quoted custom-name loading, branch-wide helper use, and calibration exclusion from the saved-profile path.
- [x] 3.2 Extend existing slicer coverage to enforce printer-side-only mesh selection without changing parser-specific entrypoints.

## 4. Validation

- [x] 4.1 Format optimized Klipper configuration and run focused macro contract tests.
- [x] 4.2 Run slicer G-code validation and regenerate/check start-print path views.
- [x] 4.3 Run installer core tests and strict OpenSpec validation.
- [x] 4.4 Review the final diff for stock-mapped configuration changes, slicer parity, sensitive data, saved-variable ownership, and missing-profile failure safety.
