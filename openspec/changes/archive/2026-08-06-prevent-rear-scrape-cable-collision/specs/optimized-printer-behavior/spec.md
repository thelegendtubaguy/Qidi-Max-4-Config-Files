## MODIFIED Requirements

### Requirement: Optimized homing, probing, mesh, and offset handling
Optimized macros SHALL avoid redundant motion and fixed waits while preserving valid axis state, acceleration, Z-offset, and fresh-mesh behavior.

#### Scenario: G28 honors requested and lazy axes
- **WHEN** `G28` requests full, XY, X-only, Y-only, or Z-only homing
- **THEN** only the required axes are homed unless Z requires unknown XY
- **AND** `G28 O ...` skips axes that are already homed
- **AND** Z homes at an independently randomized X/Y point within 10 mm of bed center
- **AND** travel to the Z-home point runs at 750 mm/s
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
- **AND** before rear-bed scraping moves 50 mm forward from the chute and traverses to X380 and back to X188 at 400 mm/s to orient the cable chain
- **AND** moves rearward to Y392 at the final chute-approach speed and keeps scrape motion within Y392–Y395
- **AND** does not rehome Z between purge cleanup and rear-bed scrape
- **AND** continues through bed/chamber waits, Z tilt, KAMP mesh, offset application, and sensor enablement
- **AND** vendor feeder, cutter, retry, runout, RFID, nozzle-wiper, and silicone-finger-brush ownership is not replaced

#### Scenario: External-spool start avoids absent Box behavior
- **WHEN** the Box stack is unavailable or `enable_box != 1`
- **THEN** start invalidates retained Box state
- **AND** does not call `BOX_PRINT_START`, rear extrusion/flush, `CLEAR_NOZZLE`, or `G1 E250`
- **AND** before rear-bed scraping moves 50 mm forward from the chute and traverses to X380 and back to X188 at 400 mm/s to orient the cable chain
- **AND** moves rearward to Y392 at the final chute-approach speed and keeps scrape motion within Y392–Y395
- **AND** wipes/scrapes without extrusion before bed/chamber waits, Z tilt, KAMP mesh, offset application, and sensor enablement

#### Scenario: Prime line remains first-layer aware
- **WHEN** front-bed room exists relative to first-layer bounds
- **THEN** the prime line is placed in front of those bounds
- **AND** otherwise uses the fixed front-center fallback
- **AND** QIDI's nozzle-temperature Z compensation is applied from the first-layer target at a known absolute Z reference after mesh and saved-offset application
- **AND** first-layer nozzle temperature is established before extrusion
- **AND** the prime extrusion remains attributed to the selected initial tool

#### Scenario: QIDI Studio parser restrictions are retained
- **WHEN** QIDI Studio G-code is edited
- **THEN** `{if}`, `{else}`, and `{endif}` remain on separate lines
- **AND** indexed completion-air-filtration placeholders are absent
- **AND** end G-code uses `EXHAUST_SPEED=0`
- **AND** direct polar-cooler commands are absent unless separately approved and QIDI Studio-validated
