## 1. Klipper heater orchestration

- [x] 1.1 Add the default-disabled staggered-start variable, the default-10-second inter-stage dwell variable, durable saved-variable overrides, and keep the optimized package version synchronized.
- [x] 1.2 Extend `OPTIMIZED_PRINT_START_HOME` with optional bed and chamber targets while preserving no-argument behavior.
- [x] 1.3 Implement enabled bed-wait, dwell, chamber-wait, dwell, then nozzle-start sequencing with scaled explicit targets, active-target compatibility fallback, zero-target handling, zero-dwell handling, and optional-chamber guards.

## 2. Slicer and path contracts

- [x] 2.1 Update OrcaSlicer and QIDI Studio start entrypoints to pass parser-correct bed and chamber targets without pre-activating both heaters.
- [x] 2.2 Extend the start-print path contract for target-bearing entrypoints, disabled behavior, enabled ordering, and the prior no-argument invocation.
- [x] 2.3 Regenerate the start-print Markdown and Mermaid views.
- [x] 2.4 Document durable staggered-heating enablement and dwell configuration for operators.

## 3. Validation

- [x] 3.1 Run the Klipper formatter and confirm only intended authored configuration changes remain.
- [x] 3.2 Run slicer macro and start-print path validation for both slicer packs.
- [x] 3.3 Run installer known-version and core tests for the synchronized package payload.
- [x] 3.4 Run strict OpenSpec validation and review the final diff for stock-mapped edits or compatibility regressions.
