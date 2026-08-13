## ADDED Requirements

### Requirement: Optional staggered print-start heating
Optimized configuration SHALL provide a default-disabled print-start mode that orders requested heater warm-up as bed, chamber, then nozzle, applies a configurable non-negative dwell between active stages, and preserves the existing start entrypoint for previously installed slicer G-code.

#### Scenario: Default startup behavior remains compatible
- **WHEN** staggered heating is disabled or not configured
- **THEN** the established concurrent print-start heating behavior remains active
- **AND** target-bearing and prior no-argument start invocations both continue through homing and filament preparation without requiring a slicer profile migration

#### Scenario: Target-bearing start uses ordered heating
- **WHEN** staggered heating is enabled and the start entrypoint receives bed and chamber targets
- **THEN** the requested bed reaches its startup wait threshold and the configured dwell completes before requested chamber heating begins
- **AND** the requested chamber reaches its startup wait threshold and the configured dwell completes before nozzle heating begins
- **AND** the inter-stage dwell defaults to 10 seconds
- **AND** saved enablement and dwell overrides survive optimized installer updates
- **AND** homing and subsequent print preparation retain their established probing-temperature and safety behavior

#### Scenario: Zero dwell retains ordered activation
- **WHEN** staggered heating is enabled and the inter-stage dwell is configured to zero
- **THEN** no fixed inter-stage delay is added
- **AND** each requested heater still reaches its startup wait threshold before the next heater is activated

#### Scenario: Prior slicer G-code uses active targets
- **WHEN** staggered heating is enabled and prior slicer G-code establishes bed and chamber targets before invoking the start entrypoint without temperature arguments
- **THEN** the optimized start derives the requested stages from the active targets
- **AND** assumes ordered heater control without rejecting the prior invocation

#### Scenario: Unrequested heater stages are skipped
- **WHEN** staggered heating is enabled and the bed or chamber target is zero or its heater is unavailable
- **THEN** that stage is skipped without waiting
- **AND** the remaining requested stages retain bed-before-chamber-before-nozzle order
