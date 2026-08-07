## MODIFIED Requirements

### Requirement: Guarded closed-loop X/Y homing optimization
The optimized configuration and managed QIDI homing payload SHALL apply X/Y speed and timing reductions only to stock or proven prior-managed state while preserving closed-loop homing safety and a production-safe command surface.

#### Scenario: Recognized X/Y speeds are optimized
- **WHEN** supported stock X/Y `homing_speed` values are `50`, or valid prior state proves live values of `65` were installer-managed
- **THEN** both X and Y first-strike `homing_speed` values become `100 mm/s`
- **AND** both second-strike speeds become `55 mm/s`
- **AND** the ledger retains original expected values for uninstall

#### Scenario: Unowned X/Y speed is preserved
- **WHEN** a live X/Y homing speed matches neither stock, current desired state, nor a value proven managed by prior state
- **THEN** the target is classified as user-modified and is not overwritten

#### Scenario: Controller and axis waits are reduced
- **WHEN** X or Y homing runs
- **THEN** existing controller command order is retained
- **AND** homing-entry transition waits are `100 ms`
- **AND** recovery transition waits are `50 ms`
- **AND** pre-home dwell is `0.25 s` for X/Y and remains `1 s` for other axes

#### Scenario: Closed-loop safety is retained
- **WHEN** X or Y homes
- **THEN** QIDI's two-strike sequence, `20 mm` retract, second-strike tolerance validation, retry limit, endstop reset, controller-state ordering, and final macro backoff remain active
- **AND** firmware `01.01.06.04` and `01.01.06.05` stock variants with SHA-256 `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761` retain their conditional `endstop_sync_reset()` behavior before homing and probing moves
- **AND** stock variants without that synchronization-reset block do not gain it
- **AND** an in-tolerance second strike succeeds without another retry
- **AND** an out-of-tolerance strike retries and exceeding the retry limit remains an error

#### Scenario: Production payloads are valid and exclude project diagnostics
- **WHEN** a managed `homing.py` payload is built or inspected
- **THEN** firmware `01.01.06.03` recovery scripts keep `G4 P50` and `SET_HOMING_MODE STEPPER=y VALUE=2` on separate G-code lines
- **AND** each Python source compiles and matches its variant-specific desired SHA-256 declared in `installer/package.yaml`
- **AND** temporary wall-clock logging and `TLTG_HOME_TIMING`, `TLTG_HOME_MACRO_TIMING`, and `TLTG_HOME_TIME_MARK` commands are absent

## ADDED Requirements

### Requirement: Firmware-scoped polar-cooler pause and resume behavior
The optimized configuration SHALL preserve QIDI firmware `01.01.06.05` direct-output-pin ownership without recreating removed smart-pin objects or adding optimized polar-cooler state changes to pause and resume.

#### Scenario: Firmware 01.01.06.05 pause retains P4 state
- **WHEN** firmware `01.01.06.05` pauses an active print
- **THEN** optimized pause handling emits no `M106 P4`, `SET_PIN PIN=polar_cooler`, or `ENABLE_SMART_PIN PIN=polar_cooler` command
- **AND** the existing P4 state remains unchanged unless a firmware-managed or qidiclient command changes it

#### Scenario: Firmware 01.01.06.05 resume retains P4 state
- **WHEN** firmware `01.01.06.05` resumes a paused print
- **THEN** optimized resume handling emits no polar-cooler state command
- **AND** optimized code does not force the cooler on from `enable_polar_cooler` solely because print state returned to `printing`

#### Scenario: Existing direct polar-cooler control remains available
- **WHEN** optimized start, cooling helper, end, or cancellation behavior requires P4 control
- **THEN** existing direct `M106 P4` behavior remains available through `[output_pin polar_cooler]`
- **AND** `[smart_output_pin polar_cooler]` and `[smart_output_pin beeper]` are not recreated by optimized configuration
