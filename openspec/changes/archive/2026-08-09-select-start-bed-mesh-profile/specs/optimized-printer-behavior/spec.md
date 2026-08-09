## MODIFIED Requirements

### Requirement: Guarded motion and calibration optimization
Optimized configuration SHALL reduce homing, probing, mesh, and filament-transition overhead only where the selected firmware baseline, valid installer state, or explicit saved-mesh preference proves the change safe.

#### Scenario: Recognized state receives optimized behavior
- **WHEN** supported stock or proven prior-managed motion and timing inputs are installed
- **THEN** guarded values and firmware-scoped payloads from `installer/package.yaml` are applied
- **AND** unrecognized live values are preserved as user-modified
- **AND** production payloads remain syntax-valid and exclude diagnostic-only commands

#### Scenario: Closed-loop homing safety remains intact
- **WHEN** X or Y homes
- **THEN** QIDI controller ordering, two-strike validation, retry/error handling, synchronization behavior for the selected firmware, and final backoff remain active
- **AND** reduced waits and speeds do not add firmware behavior absent from that baseline

#### Scenario: Homing and print preparation avoid redundant motion
- **WHEN** full, per-axis, or lazy homing and print preparation run
- **THEN** normal homing performs the requested axes, lazy homing skips requested axes already known, and Z homes required unknown X/Y first
- **AND** temporary motion settings are restored
- **AND** Z tilt and the selected fresh adaptive or named saved-mesh preparation run without redundant homing or stale active mesh state

#### Scenario: Runtime Z offset survives preparation
- **WHEN** start G-code captures the saved runtime Z offset
- **THEN** the offset is cleared before homing, tilt, and mesh preparation
- **AND** the captured value is reapplied after mesh preparation
- **AND** saved fallback is used only when the session has no capture

### Requirement: Controlled slicer start paths
OrcaSlicer and QIDI Studio packs SHALL implement the same functional print-start branches while printer-side Klipper state selects mesh preparation and parser-specific syntax and ordered invariants remain controlled by `openspec/contracts/gcode-paths/start-print.path.json`.

#### Scenario: Slicer entrypoints satisfy the path contract
- **WHEN** either slicer pack is validated
- **THEN** its required and forbidden commands, ordering, temperature inputs, selected tool, and parser-specific placeholders satisfy the controlled path contract
- **AND** no slicer start call supplies or configures a bed-mesh profile preference
- **AND** first-layer temperature and the selected tool are established before front-bed priming

#### Scenario: Existing printer state retains adaptive meshing
- **WHEN** `tltg_start_bed_mesh_profile` is absent from Klipper saved variables or contains an empty string
- **THEN** optimized print start reports fresh adaptive `kamp` calibration to the Klipper console
- **AND** it performs fresh adaptive `kamp` calibration
- **AND** the behavior applies to existing sliced files and both repository slicer packs
- **AND** the behavior applies to retained Box filament, fresh Box filament, and external-spool starts

#### Scenario: Named saved profile is selected persistently
- **WHEN** `tltg_start_bed_mesh_profile` contains a non-empty profile name
- **AND** the named saved profile exists
- **THEN** optimized print start reports the named saved profile to the Klipper console
- **AND** it clears stale active mesh state and asks Klipper to load that exact profile name
- **AND** it skips print-start bed-mesh calibration
- **AND** the behavior applies to existing sliced files and both repository slicer packs
- **AND** the behavior applies to retained Box filament, fresh Box filament, and external-spool starts

#### Scenario: Named profile is unavailable
- **WHEN** `tltg_start_bed_mesh_profile` contains a non-empty profile name
- **AND** Klipper cannot load that saved profile name
- **THEN** print preparation stops with Klipper's profile-load error
- **AND** it does not silently continue without mesh compensation or create a new mesh

#### Scenario: Saved-profile preference is operator-controlled
- **WHEN** the operator saves a non-empty string to `tltg_start_bed_mesh_profile`
- **THEN** subsequent optimized starts use that named profile until the saved value changes
- **AND** saving an empty string restores fresh adaptive calibration
- **AND** installation does not create or overwrite the optional preference

#### Scenario: Proven retained Box filament avoids reload
- **WHEN** Box availability, filament detection, logical mapping, synchronized physical slot, material, and vendor identity all prove retained-filament reuse
- **THEN** vendor loading and rear purge/cleanup actions are skipped
- **AND** common temperature waits, tilt, selected mesh preparation, offset, and sensor preparation still run

#### Scenario: Fresh Box filament retains vendor ownership
- **WHEN** the Box is enabled and retained reuse is not proven
- **THEN** start delegates feeder, cutter, retry, runout, RFID, and vendor cleaning ownership to the Box stack
- **AND** optimized purge cleanup and collision-safe rear scraping run before common print preparation

#### Scenario: External spool avoids Box-only actions
- **WHEN** the Box is unavailable or disabled
- **THEN** retained Box state is invalidated and Box load or purge actions are not called
- **AND** collision-safe non-extruding rear cleaning runs before common print preparation

#### Scenario: Prime line remains first-layer aware
- **WHEN** common preparation completes
- **THEN** the prime line uses available room ahead of first-layer bounds or a fixed safe fallback
- **AND** nozzle-temperature Z compensation is applied from a known absolute reference after mesh and offset application
