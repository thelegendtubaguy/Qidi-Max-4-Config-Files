## 1. Installer Mapping Reconciliation

- [x] 1.1 Preserve non-empty tool-slot mismatches during noninteractive install and update while retaining automatic missing-mapping creation.
- [x] 1.2 Keep mismatch details and optional identity alignment for interactive initial installs and manual updates.
- [x] 1.3 Add installer coverage for accepted, declined, and noninteractive mismatch handling.

## 2. Runtime Mapping Lifecycle

- [x] 2.1 Add missing-only start reconciliation, differential identity reset, and the idle-only `TLTG_RESET_TOOL_MAPPINGS` console command.
- [x] 2.2 Invoke missing-only reconciliation from optimized filament start and update the start-print path contract without changing slicer G-code.
- [x] 2.3 Arm identity reset after end filament prep and consume it after nozzle cooldown starts during normal active-print completion.
- [x] 2.4 Add macro contract coverage for mapping scope, idle safety, macro-side orchestration, successful-end ordering, and interrupted-print exclusion.
- [x] 2.5 Document `TLTG_RESET_TOOL_MAPPINGS` for Fluidd and console operators.

## 3. Release Metadata

- [x] 3.1 Bump installer and runtime package metadata to `26.08.05.1` through `scripts/bump_installer_version.py`.
- [x] 3.2 Add `CHANGELOG.md` release notes for tool-mapping normalization and bundled GPLv3/third-party licensing material.

## 4. Validation

- [x] 4.1 Format optimized Klipper configuration and regenerate/check the start-print path views.
- [x] 4.2 Run optimized slicer, installer core, known-version, OpenSpec strict, and focused mapping contract validation.
- [x] 4.3 Build and smoke-test the dev installer bundle, confirming release licensing artifacts remain included.
- [x] 4.4 Review the final diff for stock-mapped config changes, sensitive data, and issue `#78` scope before commit.
