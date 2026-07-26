## Why

The Max 4 hotend-integrated load cell can expose extrusion-force transitions that may identify a filament-specific pressure advance value without printing and visually inspecting a calibration pattern. A bounded calibration command should report that value on demand while preserving QIDI probing behavior and taking no action based on how the user may use the result.

## What Changes

- Add one public `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` entrypoint supporting `0.2`, `0.4`, `0.6`, and `0.8` mm nozzles that runs a guarded load-cell pressure advance calibration and reports a candidate PA value.
- Add a Klipper-side sensor acquisition and analysis path for the stock Max 4 `probe_air` CS1237 signal.
- Home the printer, move the build plate to absolute `Z=200`, and park the toolhead over the rear trash chute before calibration.
- Exercise pressure advance with stationary-toolhead E-only trapezoids injected through a guarded, version-pinned extruder trapq adapter instead of calibration XY motion.
- Run `CLEAR_FLUSH` after at most two measured extrusion pulses, then run final `CLEAR_OOZE` and `CLEAR_FLUSH` cleanup after calibration.
- Support filament already loaded from either the QIDI Box or the external-spool path without changing source selection.
- Reject inconclusive captures instead of reporting a misleading value.
- Restore temporary printer state after success, failure, or cancellation while leaving the bed lowered and toolhead parked at the chute.
- Do not persist the candidate, call `SAVE_CONFIG`, write saved variables, modify slicer profiles, or automatically reuse a prior result.
- Exclude maximum volumetric-flow calibration, print-time extrusion monitoring, automatic pause/recovery, and closed-loop extrusion correction.

## Capabilities

### New Capabilities
- `load-cell-pa-calibration`: Operator-initiated Max 4 load-cell calibration that reports a non-persistent pressure advance candidate with safety and signal-quality gates.

### Modified Capabilities

None.

## Impact

- Adds an installer-managed Python Klipper extra and a thin optimized macro/config entrypoint.
- Adds guarded installer deployment, backup, uninstall, and firmware-compatibility handling for the extra.
- Integrates with the stock `probe_air`/CS1237 implementation without replacing or changing QIDI homing, probing, or bed-mesh behavior.
- Adds deterministic analysis and direct-trapq scheduling tests using synthetic force traces, controlled-printer validation against conventional printed PA calibration, and durable reverse-engineering evidence for the QIDI host/MCU sensor contract.
- Updates operator, installer, and optimized-versus-stock documentation for command usage, result interpretation, limitations, and recovery behavior.
