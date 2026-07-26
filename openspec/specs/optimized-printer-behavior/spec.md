# optimized-printer-behavior Specification

## Purpose

Optimized printer behavior shortens homing, probing, filament, and print transitions while preserving QIDI machine safety, slicer compatibility, and vendor-owned Box behavior.

## Requirements

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
- **AND** an in-tolerance second strike succeeds without another retry
- **AND** an out-of-tolerance strike retries and exceeding the retry limit remains an error

#### Scenario: Production payload is valid and excludes diagnostics
- **WHEN** the managed `homing.py` payload is built or inspected
- **THEN** firmware `01.01.06.03` recovery scripts keep `G4 P50` and `SET_HOMING_MODE STEPPER=y VALUE=2` on separate G-code lines
- **AND** Python source compiles and matches the desired SHA-256 declared in `installer/package.yaml`
- **AND** temporary wall-clock logging and `TLTG_HOME_TIMING`, `TLTG_HOME_MACRO_TIMING`, and `TLTG_HOME_TIME_MARK` commands are absent

### Requirement: Optimized homing, probing, mesh, and offset handling
Optimized macros SHALL avoid redundant motion and fixed waits while preserving valid axis state, acceleration, Z-offset, and fresh-mesh behavior.

#### Scenario: G28 honors requested and lazy axes
- **WHEN** `G28` requests full, XY, X-only, Y-only, or Z-only homing
- **THEN** only the required axes are homed unless Z requires unknown XY
- **AND** `G28 O ...` skips axes that are already homed
- **AND** Z homes at a configured randomized point around bed center
- **AND** temporary homing acceleration is restored
- **AND** the stock pre-homing relative X/Y nudge is absent

#### Scenario: Print preparation avoids redundant homing
- **WHEN** optimized print start prepares Z tilt or mesh
- **THEN** known X/Y axes are not rehomed
- **AND** Z-only homing is used when valid
- **AND** the active mesh is cleared and `BED_MESH_CALIBRATE PROFILE=kamp` creates a fresh mesh rather than loading stock `default`
- **AND** optimized Z-tilt, mesh travel, and post-mesh save waits use installer-managed values

#### Scenario: Runtime Z offset is captured and reapplied
- **WHEN** slicer start calls `M1002 R1`
- **THEN** saved `z_offset` is captured in volatile macro state, reported, and cleared from runtime adjustment before homing, Z tilt, and mesh
- **AND** `M1002 A1` reapplies the captured value after `SAVE_CONFIG_QD`
- **AND** only a missing session capture falls back to saved `z_offset`

### Requirement: Aligned slicer and start-print branches
OrcaSlicer and QIDI Studio start/end packs SHALL remain functionally aligned while preserving parser-specific placeholders, and optimized start SHALL keep retained-filament, QIDI Box fresh-load, and external-spool paths distinct.

#### Scenario: Start entrypoints follow the controlled path contract
- **WHEN** either slicer start G-code is validated
- **THEN** ordered commands and forbidden patterns match `openspec/contracts/gcode-paths/start-print.path.json`
- **AND** OrcaSlicer uses `CHAMBER=[chamber_temperature]`
- **AND** QIDI Studio uses `CHAMBER=[chamber_temperatures]`
- **AND** both pass `PURGETEMP={nozzle_temperature_range_high[initial_tool]}` separately from first-layer nozzle temperature
- **AND** `T[initial_tool]` executes before the front prime line
- **AND** `SET_INPUT_SHAPER` and a post-prime retract are absent

#### Scenario: Retained Box filament is reused only when proven
- **WHEN** retained-filament reuse is selected
- **THEN** the Box is available and enabled and filament is detected
- **AND** requested tool mapping, retained slot, and `slot_sync` match
- **AND** retained material and vendor IDs match current slot metadata
- **AND** `BOX_PRINT_START`, rear extrusion/flush, scrape macro, and `CLEAR_NOZZLE` are skipped
- **AND** chute cleanup, bed/chamber waits, Z tilt, KAMP mesh, offset application, and sensor enablement still run

#### Scenario: Fresh Box filament delegates vendor motion
- **WHEN** the Box is enabled and reuse is not eligible
- **THEN** start calls `BOX_PRINT_START EXTRUDER=<tool> HOTENDTEMP=<purge temperature>`
- **AND** waits for vendor motion with `M400`
- **AND** performs optimized rear extrusion/flush and staged scrape-temperature cleanup
- **AND** does not rehome Z between purge cleanup and rear-bed scrape
- **AND** continues through bed/chamber waits, Z tilt, KAMP mesh, offset application, and sensor enablement
- **AND** vendor feeder, cutter, retry, runout, and RFID ownership is not replaced

#### Scenario: External-spool start avoids absent Box behavior
- **WHEN** the Box stack is unavailable or `enable_box != 1`
- **THEN** start invalidates retained Box state
- **AND** does not call `BOX_PRINT_START`, rear extrusion/flush, `CLEAR_NOZZLE`, or `G1 E250`
- **AND** wipes/scrapes without extrusion before bed/chamber waits, Z tilt, KAMP mesh, offset application, and sensor enablement

#### Scenario: Prime line remains first-layer aware
- **WHEN** front-bed room exists relative to first-layer bounds
- **THEN** the prime line is placed in front of those bounds
- **AND** otherwise uses the fixed front-center fallback
- **AND** first-layer nozzle temperature is established before extrusion
- **AND** the prime extrusion remains attributed to the selected initial tool

#### Scenario: QIDI Studio parser restrictions are retained
- **WHEN** QIDI Studio G-code is edited
- **THEN** `{if}`, `{else}`, and `{endif}` remain on separate lines
- **AND** indexed completion-air-filtration placeholders are absent
- **AND** end G-code uses `EXHAUST_SPEED=0`
- **AND** direct polar-cooler commands are absent unless separately approved and QIDI Studio-validated

### Requirement: Filament state and runout handling
Optimized filament handling SHALL preserve QIDI Box recovery while allowing external-spool automatic pausing to be controlled independently and retained Box filament to survive safely between prints.

#### Scenario: External-spool pause policy is independently switchable
- **WHEN** `TLTG_FILAMENT_SENSOR ENABLE=0`
- **THEN** toolhead switch events and external-spool exhausted status remain active
- **AND** automatic external-spool pause is suppressed
- **AND** external-spool resume may bypass the false switch
- **AND** QIDI Box runout, auto-reload, exhausted status, and resume remain vendor-controlled and enabled
- **AND** the setting resets enabled after Klipper restart

#### Scenario: Sensor events remain visible
- **WHEN** toolhead runout or supported insert occurs
- **THEN** console output identifies sensor state, source mode, and pause decision
- **AND** disabling automatic pause does not suppress the sensor event

#### Scenario: End-print retention follows the physical slot
- **WHEN** retention is enabled and Box filament remains loaded
- **THEN** `OPTIMIZED_END_PRINT_FILAMENT_PREP` derives the active slot from `slot_sync`
- **AND** reverse-maps it through `value_t0` through `value_t15`
- **AND** stores retained tool, slot, material, and vendor state for the next guarded reuse decision
- **AND** stale slicer `current_extruder` does not override the synced slot

#### Scenario: Unload clears retained state and preserves caller mode
- **WHEN** optimized unload runs
- **THEN** retained state is cleared before vendor unload
- **AND** chute travel uses `OPTIMIZED_MOVE_TO_TRASH`
- **AND** retract/extrusion operations run in explicit relative mode inside saved/restored G-code state
- **AND** end-print unload may defer standalone cleanup to staged cooldown wiping

### Requirement: Cutting, cooling, motion, and helper behavior
Optimized helpers SHALL shorten safe transitions, preserve caller state, and guard optional hardware objects while retaining stock compatibility surfaces.

#### Scenario: Filament cut and flush preserve state
- **WHEN** `OPTIMIZED_CUT_FILAMENT` or `OPTIMIZED_EXTRUSION_AND_FLUSH` runs
- **THEN** cutter tail dwell and fixed cleanup waits are reduced
- **AND** motion/extrusion modes and acceleration are restored to the caller
- **AND** flush uses `OPTIMIZED_M1004` for polar-cooler handling

#### Scenario: End-print cooldown completes staged cleanup
- **WHEN** slicer end G-code runs
- **THEN** Z lifts `3 mm`, the toolhead moves immediately to the chute, filament prep runs, cooldown begins, and bed lowering follows the common slicer height rule
- **AND** part cooling starts at full speed and heater/sensor shutdown occurs
- **AND** the Box heater is disabled only when available
- **AND** staged wiping runs after a `40 °C` hotend drop and again at `140 °C`, then moves Y forward `30 mm` before `PRINT_END`
- **AND** OrcaSlicer may run requested exhaust cooldown while QIDI Studio passes zero

#### Scenario: Delayed fan shutdown cannot affect another print
- **WHEN** a new print starts or print state becomes active/paused
- **THEN** pending `_optimized_end_fan_cooldown_off` work cannot shut down the active print's `P3` fan

#### Scenario: Chute and heater helpers guard runtime state
- **WHEN** `OPTIMIZED_MOVE_TO_TRASH` runs
- **THEN** X/Y are lazily homed, chute approach uses optimized final moves, and caller state/acceleration are restored
- **AND** `OPTIMIZED_DISABLE_BOX_HEATER` calls the vendor command only when `box_extras` exists
- **AND** `TLTG_SET_BOX_TEMP` validates Box index, heater existence, and maximum target before setting temperature
- **AND** chamber wait stops the circulation fan and completes within `3 °C` of target

#### Scenario: Calibration and startup helpers remain available
- **WHEN** optimized macros load
- **THEN** `SCREWS_TILT_CALCULATE`, `TLTG_PROBE_ACCURACY_CENTER`, and `TLTG_CORNER_BED_SCREW_CHECK` are available with their guarded homing/calibration sequences
- **AND** the console reports `TLTG Optimized Configs Installed v<package_version>`
- **AND** package version matches `installer/package.yaml`

#### Scenario: Stock integration surfaces remain intact
- **WHEN** optimized configuration is installed
- **THEN** stock-named QIDI macros remain available for Fluidd, QIDI Client, and vendor internals
- **AND** `config/box.cfg` remains the active vendor Box stack
- **AND** vendor Box object and command names are not redefined
- **AND** `config/fluidd.cfg` remains unmodified
- **AND** apparently unused stock globals remain preserved unless external use is disproven

### Requirement: Motion-free error cancellation
`OPTIMIZED_CANCEL_PRINT_ON_ERROR` SHALL complete shutdown and print-state cleanup before base cancellation without parking, wiping, or moving the toolhead.

#### Scenario: Virtual-SD error cancellation completes cleanup first
- **WHEN** `OPTIMIZED_CANCEL_PRINT_ON_ERROR` executes
- **THEN** heaters and fans are shut down and the Box heater is disabled when available
- **AND** pause state is restored
- **AND** `G31` and `CLEAR_PAUSE` execute
- **AND** `_KM_CANCEL_PRINT_BASE` executes after those steps
- **AND** no park, wipe, or other toolhead motion is added
