## ADDED Requirements

### Requirement: Optimized X/Y homing speeds
The installer SHALL configure both X and Y first-strike homing speed to `100 mm/s` and second-strike homing speed to `55 mm/s`.

#### Scenario: Fresh stock speed is optimized
- **WHEN** installation runs on a supported stock firmware with X/Y `homing_speed: 50`
- **THEN** both X and Y `homing_speed` values are written as `100`
- **AND** both `second_homing_speed` values are `55.0`

#### Scenario: Prior managed speed is migrated
- **WHEN** a valid prior installed-state ledger records installer-managed X/Y `homing_speed` desired value `65`
- **AND** the live X/Y values remain `65`
- **THEN** installation writes both values as `100`
- **AND** the new ledger retains the prior original expected values for uninstall

#### Scenario: Unowned speed is preserved
- **WHEN** a live X/Y homing speed differs from stock, the current desired value, and a value proven managed by the prior ledger
- **THEN** the installer classifies that target as user-modified
- **AND** does not overwrite it

### Requirement: Optimized X/Y controller transition waits
The managed QIDI homing implementation SHALL use `100 ms` waits between X/Y homing-entry controller commands and `50 ms` waits between X/Y recovery controller commands.

#### Scenario: First strike enters homing mode
- **WHEN** an X or Y first strike is prepared
- **THEN** the existing controller-state command order is retained
- **AND** each entry transition wait is `100 ms`

#### Scenario: Strike recovery enters run mode
- **WHEN** an X or Y strike completes
- **THEN** the existing controller recovery command order is retained
- **AND** each recovery transition wait is `50 ms`

### Requirement: Axis-specific pre-home dwell
The QIDI homing implementation SHALL use a `250 ms` pre-home dwell for X and Y and SHALL retain the `1 s` pre-home dwell for all other axes.

#### Scenario: X/Y pre-home dwell
- **WHEN** `home_rails()` prepares `stepper_x` or `stepper_y`
- **THEN** it queues a `0.25 s` dwell before endstop reset and homing

#### Scenario: Z pre-home dwell remains unchanged
- **WHEN** `home_rails()` prepares `stepper_z`
- **THEN** it queues the vendor `1 s` dwell

### Requirement: Closed-loop homing safety behavior
The optimization SHALL retain QIDI's two-strike closed-loop homing, `20 mm` retract, second-strike tolerance validation, retry limit, endstop reset, controller-state ordering, and final macro backoff.

#### Scenario: Consistent second strike
- **WHEN** the measured second-strike retract difference is within configured tolerance
- **THEN** homing succeeds without an additional retry

#### Scenario: Inconsistent second strike
- **WHEN** the measured second-strike retract difference exceeds configured tolerance
- **THEN** the existing retry path runs
- **AND** exceeding the configured retry limit remains an error

### Requirement: Production homing payload excludes diagnostics
The production payload SHALL NOT install temporary wall-clock loggers, `TLTG_HOME_TIMING`, `TLTG_HOME_MACRO_TIMING`, or `TLTG_HOME_TIME_MARK` commands.

#### Scenario: Release payload is inspected
- **WHEN** the bundled desired `homing.py` and optimized homing macros are searched
- **THEN** temporary timing instrumentation and marker commands are absent
