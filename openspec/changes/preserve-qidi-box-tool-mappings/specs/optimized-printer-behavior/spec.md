## ADDED Requirements

### Requirement: QIDI Box tool-mapping lifecycle
Optimized print and operator macros SHALL preserve mappings needed by the active print, restore predictable identity mappings after normal completion, and leave interrupted-print recovery under operator control.

#### Scenario: Print start repairs only missing active mappings
- **WHEN** optimized start G-code runs with `box_count` requiring logical tools `0` through `min(box_count * 4, 16) - 1`
- **THEN** each missing or empty active `value_tN` is saved as `'slotN'` before optimized filament preparation consumes mappings
- **AND** every existing non-empty mapping is preserved
- **AND** Box-unavailable or non-Box start remains available

#### Scenario: Normal completion restores identity after cooldown starts
- **WHEN** optimized slicer end G-code completes filament retention or unloading
- **THEN** nozzle cooldown starts before mapping normalization
- **AND** each existing `value_t0` through `value_t15` that is empty or differs from `'slotN'` is saved as `'slotN'`
- **AND** absent mappings are created only within the active Box range
- **AND** unchanged identity mappings are not rewritten
- **AND** normalization completes before `PRINT_END`

#### Scenario: Interrupted prints preserve current mappings
- **WHEN** a print is cancelled, errors, loses power, or otherwise bypasses normal slicer end G-code
- **THEN** optimized cancellation and error cleanup do not normalize `value_t0` through `value_t15`
- **AND** the operator decides whether to preserve or manually reset the mappings

#### Scenario: Console reset is idle-only
- **WHEN** `TLTG_RESET_TOOL_MAPPINGS` is invoked while idle
- **THEN** it normalizes every existing `value_t0` through `value_t15`
- **AND** creates missing identity mappings only within the active Box range
- **AND** reports the reset outcome to the console

#### Scenario: Console reset rejects active or paused prints
- **WHEN** `TLTG_RESET_TOOL_MAPPINGS` is invoked while idle-timeout reports printing, virtual SD is active, or pause/resume is paused
- **THEN** it raises an error without changing any mapping
