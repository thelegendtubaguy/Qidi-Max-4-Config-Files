## 1. Persistent Retention Policy

- [x] 1.1 Replace the installer-managed retention global with an exact-`1` check of optional saved variable `tltg_keep_loaded_between_prints` in optimized print-start reuse and normal print-end retention.
- [x] 1.2 Preserve the existing guarded retained-state path when enabled and the existing retained-state clearing plus QIDI Box cut-and-unload path when disabled or absent.
- [x] 1.3 Initialize an absent `tltg_keep_loaded_between_prints` preference to `1` during install and update while preserving every existing value.

## 2. Operator and Behavior Contracts

- [x] 2.1 Document the installer-enabled default and `SAVE_VARIABLE` commands for enabling and disabling retention, including the absent or zero runtime fallback.
- [x] 2.2 Update the main optimized-printer and installer-lifecycle specs with the saved preference, installed default, operator preservation, and uninstall rules.
- [x] 2.3 Update the start-print path contract so retained reuse requires saved enablement, keep both slicer packs free of the printer preference, and regenerate generated views.
- [x] 2.4 Advance package, runtime, known-version, and upgrade-source metadata to `26.08.09.2` and add the release changelog entry.

## 3. Focused Coverage

- [x] 3.1 Extend optimized macro contract coverage for the exact saved-variable lookup, absent/zero fallback, exact-`1` enablement, removal of the managed global, and preservation of cut-and-unload commands.
- [x] 3.2 Enforce that neither slicer pack configures `tltg_keep_loaded_between_prints`.
- [x] 3.3 Add installer lifecycle coverage for absent initialization, existing-zero preservation, and uninstall preservation.

## 4. Validation

- [x] 4.1 Format optimized Klipper configuration and run focused macro contract tests.
- [x] 4.2 Run slicer G-code validation and regenerate/check start-print path views.
- [x] 4.3 Run installer core tests, installer known-version validation, and strict OpenSpec validation.
- [x] 4.4 Review the final diff for stock-mapped changes, slicer parity, sensitive data, saved-variable ownership, and cut-and-unload safety.
