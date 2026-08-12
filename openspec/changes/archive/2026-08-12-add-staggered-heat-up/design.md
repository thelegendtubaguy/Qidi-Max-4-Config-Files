## Context

Both slicer start files currently issue non-blocking bed and chamber targets before calling `OPTIMIZED_PRINT_START_HOME` without parameters. That macro starts nozzle preheat and homes while the other heaters continue warming. Heater commands pass through QIDI scaling wrappers, and the optimized start path must retain the existing macro name for installed slicer profiles.

## Goals / Non-Goals

**Goals:**

- Keep `OPTIMIZED_PRINT_START_HOME` callable with no arguments.
- Give current slicer packs an explicit target-bearing invocation that lets Klipper own heater activation order.
- Keep all sequencing policy in optimized Klipper configuration behind one default-disabled variable.
- Preserve scaled targets, status reporting, optional-chamber handling, and existing homing behavior.

**Non-Goals:**

- Enforce a hard whole-machine wattage limit; heaters at temperature still consume maintenance power.
- Change PID control, heater scaling, temperature tolerances, or thermal safety configuration.
- Make new target-bearing slicer G-code run against older optimized configurations.
- Modify stock-mapped configuration.

## Decisions

### Extend the existing start-home entrypoint

`OPTIMIZED_PRINT_START_HOME` accepts optional `BEDTEMP` and `CHAMBER` parameters. Current OrcaSlicer and QIDI Studio start files pass their existing placeholders on this call and no longer activate bed and chamber immediately before it.

A no-argument invocation remains valid. It reads the active bed and chamber targets established by prior slicer G-code, so upgrading the installer does not require updating installed slicer profiles.

A separate required entrypoint was rejected because prior profiles could not participate and the start path would gain two owners for heater orchestration.

### Use a default-disabled optimized variable

`_tltg_optimized_globals` owns `variable_staggered_start_heating: False` and `variable_staggered_start_heating_dwell_seconds: 10`. The start entrypoint reads `tltg_staggered_start_heating` and `tltg_staggered_start_heating_dwell_seconds` from `save_variables` when present, then falls back to the optimized defaults. The dwell is clamped to zero or greater; zero retains ordered activation without fixed dead time.

Saved overrides are selected because the installer mirrors its managed macro tree during updates. Editing installed managed defaults would be overwritten, while `saved_variables.cfg` remains operator-owned and durable.

### Separate parameterized and compatibility target handling

The target-bearing path uses the existing scaled heater commands and optimized wait helpers. The no-argument compatibility path captures already-active heater targets before changing outputs; direct waits and restoration avoid applying QIDI scaling twice.

When staggering is disabled, supplied bed and chamber targets are activated non-blockingly, nozzle probing preheat starts, and homing proceeds as it does now. A no-argument invocation leaves the slicer-established bed and chamber targets active.

When staggering is enabled:

1. Nozzle heating is disabled until its stage.
2. Chamber heating is held at zero while the bed reaches the existing bed wait threshold.
3. The configured dwell completes before a requested chamber is activated, or before nozzle heating when no chamber is requested.
4. A requested chamber is activated and reaches the existing chamber wait threshold.
5. The configured dwell completes before nozzle probing preheat starts and normal homing runs.

Zero targets and an unavailable chamber skip their stages. Subsequent filament preparation may reassert the same targets and retains ownership of final waits, cleaning, loading, tilt, and mesh behavior.

### Contract both invocation forms

The start-print path contract records the target-bearing OrcaSlicer and QIDI Studio calls, the default concurrent branch, the staggered branch order, and the no-argument compatibility fallback. Generated contract views remain derived outputs.

## Risks / Trade-offs

- [Prior slicer G-code activates bed and chamber before Klipper enters the compatibility path] → The enabled macro captures both targets and disables chamber immediately before waiting for the bed. Eliminating even that short overlap requires the target-bearing slicer update.
- [Sequential warm-up and fixed inter-stage dwell increase startup duration] → The behavior is opt-in, disabled by default, and the dwell can be set to zero.
- [Bed maintenance power overlaps chamber and nozzle warm-up] → Document the feature as staggered warm-up rather than a strict wattage governor.
- [Scaled active targets could be scaled twice] → Treat no-argument targets as active heater values and use direct restoration/waits; use scaled wrappers only for explicit slicer targets.
- [A missing chamber object could fail template evaluation] → Guard chamber target access and skip chamber operations when the object is unavailable.

## Migration Plan

1. Install the updated optimized macro payload with staggering disabled.
2. Existing slicer profiles continue using their no-argument invocation and current concurrent behavior.
3. Updated slicer packs pass bed and chamber targets to `OPTIMIZED_PRINT_START_HOME`.
4. Operators with constrained electrical service save `tltg_staggered_start_heating=1` and optionally save `tltg_staggered_start_heating_dwell_seconds`; the next print reads the saved values.
5. Rollback consists of saving enablement as `0`; reverting the payload is not required to restore concurrent startup.
