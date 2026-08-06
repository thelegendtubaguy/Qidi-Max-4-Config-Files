## Why

QIDI firmware installation removes non-allowlisted files under `/home/qidi/printer_data/config`, including the optimized macro tree, installer state, and auto-update checksum state. The surviving systemd auto-update timer currently treats the missing checksum as first-run initialization, so it records the current release without reinstalling and leaves optimized slicer commands unavailable.

## What Changes

- Persist auto-update enrollment outside QIDI's firmware-cleaned `config/` directory.
- Reinstall the current release when an enrolled auto-update check finds missing or drifted optimized installation state, even when the release checksum has not changed.
- Preserve first-run checksum initialization for bundles that have no durable auto-update enrollment.
- Remove durable enrollment when auto-updates are disabled or uninstalled.
- Record QIDI firmware config-cleanup behavior as external platform evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: Recover enrolled optimized installations after QIDI firmware cleanup without turning unregistered first-run checks into installs.

## Impact

- `installer/runtime/auto_update.py` and auto-update messages
- Auto-update unit and lifecycle tests
- `openspec/specs/installer-lifecycle/spec.md`
- `openspec/observations/qidi-platform.md`
