## Why

Simultaneous bed, chamber, and nozzle heating can exceed the available electrical capacity on constrained circuits. Operators need an optional startup mode that reduces peak heater demand without changing the default print-start behavior.

## What Changes

- Add an optimized-config variable that enables staggered print-start heating; the default remains disabled.
- Add a configurable inter-stage dwell that defaults to 10 seconds and may be set to zero; durable saved-variable overrides survive installer updates.
- When enabled, start and wait for the bed, dwell, then start and wait for the chamber when requested, dwell, then heat the nozzle for homing and print preparation.
- Preserve existing heater targets, zero-temperature handling, startup statuses, homing, filament preparation, and print-start safety behavior.
- Keep the existing `OPTIMIZED_PRINT_START_HOME` entrypoint compatible with prior OrcaSlicer and QIDI Studio start G-code so installing the new configuration does not require a slicer profile update.
- Keep current concurrent heating behavior when staggered heating is disabled.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: Add optional ordered print-start heating with default-off and prior-slicer compatibility requirements.

## Impact

- Optimized-only variables and print-start heater orchestration under `installer/klipper/tltg-optimized-macros/`.
- Print-start path contract and focused validation for enabled, disabled, zero-dwell, zero-chamber, and prior-slicer invocation paths.
- Operator configuration reference in `README.md`.
- No stock-mapped configuration changes and no required OrcaSlicer or QIDI Studio profile migration.
