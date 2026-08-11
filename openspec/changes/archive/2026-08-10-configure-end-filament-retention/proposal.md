## Why

End-of-print QIDI Box filament retention is currently enabled by a managed macro global, so operators cannot persistently choose the stock-style cut-and-unload sequence without editing installer-owned files. Retention must be an upgrade-safe saved-variable preference, with absent runtime state preserving the stock sequence and installation preserving the current enabled default.

## What Changes

- Move end-of-print filament retention to a persistent preference; an absent or zero runtime value cuts and unloads QIDI Box filament.
- Read `tltg_keep_loaded_between_prints` from Klipper saved variables at optimized print start and normal print end.
- Initialize the preference to `1` during install or update only when it is absent, preserving an existing `0` or other operator value.
- Retain and reuse filament only when the preference equals `1` and existing physical-state guards pass.
- Document console commands for enabling and disabling the preference without changing slicer G-code.
- Remove the installer-managed `keep_loaded_between_prints` macro global.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: Make QIDI Box filament retention and reuse conditional on a persistent operator preference whose absent or zero runtime state selects cut-and-unload behavior.
- `installer-lifecycle`: Initialize an absent retention preference to enabled without replacing an existing operator value.

## Impact

- `installer/klipper/tltg-optimized-macros/filament.cfg`
- `installer/klipper/tltg-optimized-macros/globals.cfg`
- Installer saved-variable initialization and lifecycle tests
- Optimized macro contract tests
- Operator documentation in `README.md` and release notes in `CHANGELOG.md`
- No slicer G-code, stock-mapped configuration, or vendor Box command changes
