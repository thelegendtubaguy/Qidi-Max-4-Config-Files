## ADDED Requirements

### Requirement: Public pressure advance calibration entrypoint
The system SHALL expose one public `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` G-code macro that is visible as an operator-invokable macro in Fluidd.

#### Scenario: Valid invocation starts calibration setup
- **WHEN** the printer is idle, filament is loaded to the toolhead, and the user invokes `TLTG_PA_CALIBRATE` with supported `TEMP` and `NOZZLE` values
- **THEN** the system starts guarded pressure advance calibration setup
- **AND** the system uses the supplied temperature and nozzle diameter for heating, flow planning, quality limits, and result reporting

#### Scenario: Sweep details remain internal
- **WHEN** the user supplies valid `TEMP` and `NOZZLE` values
- **THEN** the system selects its bounded K sweep, flow-transition, capture, analysis, and chute-clearing parameters internally

### Requirement: Calibration preconditions fail before side effects
The system SHALL validate printer, motion, temperature, sensor, and calibration ownership preconditions before starting sensor acquisition, moving the toolhead, extruding filament, or changing pressure advance.

#### Scenario: Active print is rejected
- **WHEN** `TLTG_PA_CALIBRATE` is invoked while a print is printing, paused, or otherwise active
- **THEN** the system rejects the request with an actionable reason
- **AND** no calibration motion, extrusion, sensor query, or pressure advance change occurs

#### Scenario: Loaded filament is detected while event handling is disabled
- **WHEN** the toolhead filament switch reports `filament_detected=true` and `enabled=false`
- **THEN** preflight treats filament as physically present
- **AND** it does not enable the sensor, alter runout policy, or execute insert/runout actions

#### Scenario: Toolhead filament is absent
- **WHEN** `filament_switch_sensor filament_switch_sensor.filament_detected` is false or unavailable
- **THEN** calibration is rejected before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes

#### Scenario: QIDI Box loaded state is corroborated
- **WHEN** QIDI Box is enabled and the toolhead switch, controller extruder state, E-endstop states, synchronized slot, last-loaded slot, and `IN_EXTRUDER` slot state agree
- **THEN** preflight accepts the already-loaded QIDI Box source without loading, unloading, cutting, switching, or changing source configuration

#### Scenario: Loaded-state sources disagree
- **WHEN** the physical toolhead switch and QIDI compiled/synchronized Box state are missing, busy, unsynchronized, or inconsistent
- **THEN** calibration fails closed before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes
- **AND** it does not call a private loaded-filament detection method with an unvalidated side-effect contract

#### Scenario: Missing or unsupported temperature is rejected
- **WHEN** `TEMP` is absent, outside the printer's safe extrusion range, or unsupported by the calibration limits
- **THEN** the system rejects the request before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes
- **AND** the system does not select a default material temperature

#### Scenario: All Max 4 nozzle sizes are accepted
- **WHEN** `NOZZLE` is exactly `0.2`, `0.4`, `0.6`, or `0.8`
- **THEN** the system selects the corresponding nozzle-specific flow schedule, extrusion bounds, and signal-quality limits

#### Scenario: Missing or unsupported nozzle diameter is rejected
- **WHEN** `NOZZLE` is absent or is not exactly `0.2`, `0.4`, `0.6`, or `0.8`
- **THEN** the system rejects the request before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes
- **AND** the system does not silently use the QIDI screen database or Klipper `nozzle_diameter` value

#### Scenario: Valid preflight performs full homing and lowers the bed
- **WHEN** all non-motion preconditions pass
- **THEN** the system performs full `G28` homing through the installed QIDI-compatible probing path before calibration heating
- **AND** it moves the build plate to absolute `Z=200` before parking the toolhead over the trash chute

#### Scenario: Concurrent calibration or probe ownership is rejected
- **WHEN** the CS1237 signal is owned by probing, homing, bed meshing, Z tilt, or another calibration
- **THEN** the system rejects or aborts calibration without sharing the sensor stream

### Requirement: Stock load-cell compatibility is guarded
The system SHALL access the stock `probe_air` CS1237 acquisition path through a compatibility adapter that validates every private runtime dependency before calibration side effects.

#### Scenario: Supported stock sensor contract is accepted
- **WHEN** `probe_air` exposes a hardware-validated non-homing acquisition transaction with bounded conversion freshness, distinct response identity, exclusive pin ownership, and a non-disruptive stock-state verification or restoration contract
- **THEN** the adapter permits bounded sensor capture only through that validated transaction
- **AND** the stock probe configuration, calibration, zero, trigger threshold, and endstop behavior remain unchanged

#### Scenario: Cached direct reads are insufficient
- **WHEN** `query_cs1237_read` returns cached object state without request identity or conversion timestamps
- **THEN** the adapter does not treat response cadence or cardinality as proof of conversion freshness
- **AND** public and developer capture remain disabled

#### Scenario: GPIO-passive origin cache remains partially validated
- **WHEN** `read_origin_data()` returns changing cached values without driving sensor pins
- **THEN** the adapter may retain that path for bounded source-gated characterization at no more than 50 Hz and 250 calls
- **AND** it validates capture bounds and acquires exclusive process-local ownership before pressure-advance mutation or trapq queueing
- **AND** it records host and estimated print-time call intervals, retains ownership through capture, owned-motion completion, and temporary-state restoration, then verifies homing state after release
- **AND** an unverifiable post-capture homing state or ownership loss forces shutdown before subsequent probing
- **AND** public capture remains disabled until cached-conversion age, deterministic schedule alignment, invalid-value classification, and candidate repeatability are hardware-validated

#### Scenario: Configuration reads are not side-effect-free
- **WHEN** `query_cs1237_config_r` drives the live CS1237 clock without a validated serialization contract
- **THEN** the adapter does not use repeated configuration reads as a pre/post capture fence
- **AND** any observed non-stock result requires `FIRMWARE_RESTART`

#### Scenario: Changed private interface is rejected
- **WHEN** a required `probe_air` or CS1237 private attribute, command, configuration, payload field, or state-preservation invariant is absent or incompatible
- **THEN** the system reports an unsupported sensor interface
- **AND** no motion, extrusion, heating-target change, or pressure advance change occurs

#### Scenario: Homing trigger acquisition is rejected for calibration
- **WHEN** continuous CS1237 bulk data requires `CS1237.setup_home()`, `cs1237_setup_home`, `TriggerDispatch`, or `trsync` ownership
- **THEN** the adapter rejects that acquisition path for calibration
- **AND** it does not arm probe thresholds, endstop reasons, stepper-stop behavior, or homing watchdogs

#### Scenario: Stock probing remains available after calibration
- **WHEN** calibration completes, fails, or is cancelled and the validated stock-state transaction proves the sensor state was preserved or restored
- **THEN** subsequent QIDI Z homing, probing, Z tilt, and bed mesh operations use their stock behavior

#### Scenario: Sensor state cannot be proven safe
- **WHEN** acquisition or verification reports a changed, ambiguous, or unverified CS1237 configuration or homing state
- **THEN** calibration reports no candidate and requires `FIRMWARE_RESTART`
- **AND** Klipper enters shutdown before any subsequent probing or calibration motion can use the unverified sensor state

### Requirement: Calibration uses stationary PA-enabled extrusion
The system SHALL test pressure advance with bounded positive E-only trapezoids injected into the active extruder trapq with PA eligibility enabled while the toolhead remains stationary over the rear trash chute.

#### Scenario: Measured pulses have no XY motion
- **WHEN** a candidate pressure advance value is tested
- **THEN** the measured low/high/low flow transitions generate extruder motion without X, Y, or Z step generation
- **AND** the toolhead remains at the trash-chute park position for the complete measured pulse

#### Scenario: Injected moves exercise the real PA transform
- **WHEN** a measured E-only trapezoid is queued
- **THEN** its extruder trapq PA eligibility field is enabled
- **AND** the installed Klipper pressure-advance transform processes that trapezoid
- **AND** normal E-only G-code or `toolhead.manual_move()` is not treated as PA-enabled evidence

#### Scenario: Private trapq contract is incompatible
- **WHEN** the installed extruder trapq API, CFFI ABI, PA flag behavior, or nominal-position contract does not match a supported version
- **THEN** the system rejects calibration before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes

#### Scenario: Bypassed move limits are enforced explicitly
- **WHEN** a direct extruder trapq pulse is planned
- **THEN** the module independently validates heater extrusion readiness, positive extrusion, start time, nominal E position, distance, velocity, acceleration, total extrusion, PA smoothing lead-in and lead-out, and absence of overlapping ordinary extrusion
- **AND** planning fails before queueing if any limit cannot be satisfied

#### Scenario: Resource bounds are enforced
- **WHEN** calibration is planned for the supplied nozzle diameter
- **THEN** total extrusion, pulse count, flow rates, velocity, acceleration, duration, tested K range, and number of cycles are bounded by validated nozzle-specific limits
- **AND** planning fails before homing or heating if any bound cannot be satisfied

### Requirement: Trash chute is cleared between measured pulse groups
The system SHALL cycle the trash-chute flap with `CLEAR_FLUSH` after no more than two measured extrusion pulses and SHALL exclude all clearing motion from sensor analysis.

#### Scenario: Intermediate flap clearing follows a pulse group
- **WHEN** one or two measured extrusion pulses complete and more pulses remain
- **THEN** the system stops the current measurement window
- **AND** it runs `CLEAR_FLUSH`, waits for the toolhead to return over the trash chute, waits for sensor settling, and acquires a new baseline before the next measured pulse

#### Scenario: No cleanup precedes the first pulse
- **WHEN** calibration setup parks the toolhead over the trash chute
- **THEN** the system does not run `CLEAR_OOZE` or `CLEAR_FLUSH` before the first measured pulse

#### Scenario: Final cleanup follows calibration extrusion
- **WHEN** the final measured pulse completes or a recoverable failure occurs after any calibration extrusion
- **THEN** the system restores normal toolhead and extruder queue ownership
- **AND** it runs `CLEAR_OOZE` followed by `CLEAR_FLUSH`
- **AND** it leaves the toolhead parked over the trash chute

#### Scenario: Cleanup commands are unavailable
- **WHEN** `CLEAR_OOZE`, `CLEAR_FLUSH`, or `OPTIMIZED_MOVE_TO_TRASH` is not registered
- **THEN** calibration is rejected before homing, heating, motion, extrusion, sensor acquisition, or pressure advance changes

### Requirement: Loaded filament source is preserved
The system SHALL support filament already loaded from either the QIDI Box or the external-spool path without loading, unloading, cutting, switching, or changing source configuration.

#### Scenario: QIDI Box filament is loaded
- **WHEN** the selected QIDI Box filament is already loaded to the toolhead
- **THEN** calibration uses the active toolhead extruder and leaves QIDI Box tool and source selection unchanged

#### Scenario: External-spool filament is loaded
- **WHEN** external-spool filament is already loaded to the toolhead
- **THEN** calibration uses the active toolhead extruder without invoking QIDI Box operations

### Requirement: Sensor capture is synchronized and bounded
The system SHALL collect bounded CS1237 samples through a validated non-homing acquisition and state-preservation transaction, establish a hardware-validated bound on conversion freshness and conversion-time error, and align accepted samples to queued low/high/low extrusion transitions in Klipper print-time coordinates.

#### Scenario: Capture covers the measured motion window
- **WHEN** a calibration sweep runs
- **THEN** sensor capture begins before the first measured transition and ends after the final measured transition completes
- **AND** each analyzed sample is assigned a time relative to the queued transition schedule using a validated bound on cached-conversion age rather than assuming response receive time equals ADC conversion time

#### Scenario: Conversion freshness is inconclusive
- **WHEN** duplicate responses, cached-value behavior, firmware timing, or physical force-response delay prevents the system from bounding ADC conversion age and alignment error
- **THEN** the system marks the measured cycle inconclusive
- **AND** the system reports no PA candidate

#### Scenario: Identical excitation is not repeatable
- **WHEN** repeated cycles at one K value vary in low-flow baseline, response amplitude, polarity, or normalized transition metrics beyond validated limits
- **THEN** the system stops before a K sweep
- **AND** thermal soaking or longer lead segments are not treated as sufficient unless the recorded cycles pass the same repeatability gates
- **AND** the system reports no PA candidate

#### Scenario: Raw processing respects host limits
- **WHEN** the validated acquisition path samples the nominally 1280 SPS ADC
- **THEN** the system uses a repeat-validated under-load request rate no greater than 500 Hz and bounds query duration, queued command count, memory, and reactor occupancy without per-sample console output
- **AND** normal calibration does not persist raw sample files

#### Scenario: Invalid direct-read excursions are detected
- **WHEN** direct responses contain impossible or uncorroborated transient values such as the observed near-zero and bit-pattern-like excursions
- **THEN** the system excludes only values classified by a hardware-validated invariant that remains valid during force transitions
- **AND** the system rejects the complete measured cycle when classification is ambiguous or invalid-read contamination exceeds tolerance
- **AND** the system does not interpolate samples in a way that can reshape transition timing

#### Scenario: Timing uncertainty exceeds tolerance
- **WHEN** missing messages, clock reconstruction error, or alignment residuals exceed the validated tolerance
- **THEN** the system marks the capture inconclusive
- **AND** the system reports no PA candidate

### Requirement: Candidate selection fails closed
The system SHALL report a pressure advance candidate only when the force response demonstrates a repeatable transition from insufficient compensation to excessive compensation inside the tested K range and all quality gates pass.

#### Scenario: Valid response produces an interior candidate
- **WHEN** repeated cycles at two distinct validated E accelerations have adequate coverage and signal strength, no saturation, consistent polarity, acceptable timing, one ordered signed recovery-area bracket, and a unique composite-objective minimum inside the tested range
- **THEN** each acceleration profile selects the same final non-excessive pressure advance grid value from the refined range
- **AND** transition tracking, rise/fall delay, settling, recovery, plateau-slope, overshoot, undershoot, signed-area, and repeatability metrics agree within validated tolerances

#### Scenario: Candidate depends on acceleration
- **WHEN** complete repeated sweeps at the two validated E accelerations select different refined-grid K values
- **THEN** the system reports an acceleration-dependent inconclusive reason
- **AND** the system reports no PA candidate

#### Scenario: Signed recovery evidence does not bracket compensation
- **WHEN** post-deceleration signed recovery area does not transition once from positive residual pressure to negative reversal inside the tested K range near the composite minimum
- **THEN** the system reports an inconclusive recovery-evidence reason
- **AND** the system reports no PA candidate

#### Scenario: No detectable force response is inconclusive
- **WHEN** flow transitions do not produce force changes distinguishable from baseline noise
- **THEN** the system reports an inconclusive signal-strength reason
- **AND** the system reports no PA candidate

#### Scenario: Dropout or saturation is inconclusive
- **WHEN** required sample coverage is missing or the ADC saturates during a measured cycle
- **THEN** the system rejects the capture
- **AND** the system reports no PA candidate

#### Scenario: Candidate reaches a search boundary
- **WHEN** the best estimate is at the minimum or maximum tested K boundary
- **THEN** the system reports that the tested range did not bracket a candidate
- **AND** the system does not present the boundary value as a PA candidate

#### Scenario: Repeated cycles disagree
- **WHEN** cycle-level candidate evidence varies beyond the validated repeatability tolerance
- **THEN** the system reports an inconclusive repeatability reason
- **AND** the system reports no PA candidate

### Requirement: Successful value is reported but not persisted
The system SHALL return a successful pressure advance value through the standard Klipper G-code response channel and SHALL take no action based on how the user may use that value.

#### Scenario: Value is visible to the user
- **WHEN** calibration succeeds
- **THEN** the response includes `PA_VALUE=<decimal>`, the supplied calibration temperature, the supplied nozzle diameter, and `PERSISTED=0`
- **AND** the command completes without prescribing or inspecting downstream use of the value

#### Scenario: Value is not retained by Klipper
- **WHEN** calibration succeeds
- **THEN** the system restores the pressure advance and smooth time that were active before calibration
- **AND** the reported value is not retained as the active pressure advance after cleanup

#### Scenario: Persistent stores remain unchanged
- **WHEN** calibration succeeds, fails, or is cancelled
- **THEN** the calibration does not call `SAVE_CONFIG`, `SAVE_VARIABLE`, or persist the result through a G-code variable
- **AND** it does not modify OrcaSlicer, QIDI Studio, or printer configuration files

#### Scenario: Inconclusive result has no value token
- **WHEN** calibration is rejected by a precondition or quality gate
- **THEN** the response contains an actionable failure reason
- **AND** the response does not contain `PA_VALUE=`

### Requirement: Calibration cleanup is complete and idempotent
The system SHALL use one idempotent cleanup path to stop sensor acquisition and restore every temporary software state after success, failure, cancellation, command error, or Klipper shutdown.

#### Scenario: Successful cleanup restores temporary state
- **WHEN** calibration succeeds
- **THEN** sensor querying stops and the original pressure advance, smooth time, coordinate modes, extrusion mode, nominal E bookkeeping, trapq ownership, and modified motion limits are restored
- **AND** the bed remains at absolute `Z=200`
- **AND** final `CLEAR_OOZE` and `CLEAR_FLUSH` leave the toolhead parked over the trash chute

#### Scenario: Mid-sweep cancellation restores state
- **WHEN** the user cancels calibration during capture or motion
- **THEN** the system stops queuing calibration work, finalizes or drains owned trapq work, stops owned sensor acquisition, and restores temporary state
- **AND** cleanup can run again without changing the restored state or raising a second failure

#### Scenario: Analysis failure restores state
- **WHEN** capture completes but analysis rejects the data or raises an error
- **THEN** the system restores temporary state before reporting the failure

#### Scenario: Klipper shutdown interrupts calibration
- **WHEN** Klipper enters shutdown while calibration owns the sensor stream
- **THEN** the module releases its in-process ownership state without attempting unsafe new motion
- **AND** the next successful Klipper start does not treat calibration as active

### Requirement: Python extra deployment is reversible
The installer SHALL manage the PA calibration Python extra as a guarded external file and the macro/config entrypoint as part of the optimized macro tree.

#### Scenario: Fresh installation deploys the capability
- **WHEN** installation runs on a supported firmware with a compatible stock sensor layout
- **THEN** the installer atomically deploys the project-namespaced Python extra to the Klipper extras directory
- **AND** it deploys the `TLTG_PA_CALIBRATE` macro/config through `installer/klipper/tltg-optimized-macros/`

#### Scenario: Python extra upgrade reloads module code
- **WHEN** installation or auto-update changes an already imported managed Python extra
- **THEN** the installer requires or performs a process-level Klipper restart before claiming that the new module code is active
- **AND** a Moonraker `/printer/restart` response alone is not treated as proof that the imported Python module was reloaded

#### Scenario: Historical external-file ownership is version-proven
- **WHEN** install or uninstall loads a prior state ledger containing the managed PA Python extra
- **THEN** the recorded destination and installed hash must match an external-file baseline enumerated for that package version
- **AND** install and uninstall backups capture the exact state-owned payload bytes and mode
- **AND** restore requires those archived bytes to match the recorded version-proven hash
- **AND** unrecognized hashes fail before backup or live mutation

#### Scenario: Destination collision fails safely
- **WHEN** an untracked or drifted file exists at the Python extra destination
- **THEN** installation stops before overwriting the file
- **AND** the installer reports the conflicting path

#### Scenario: Failed installation rolls back the extra
- **WHEN** installation fails after staging or replacing the Python extra
- **THEN** rollback removes the staged managed file or restores its recorded preimage
- **AND** installer state does not claim a deployment that was rolled back

#### Scenario: Uninstall removes only the managed extra
- **WHEN** uninstall processes an unchanged managed Python extra
- **THEN** it removes that extra and the optimized macro/config entrypoint
- **AND** it restores any recorded preimage instead of deleting pre-existing content

### Requirement: Analysis and hardware behavior are validated
The implementation SHALL include deterministic analysis tests and controlled Max 4 evidence before candidate reporting is considered complete.

#### Scenario: Synthetic response fixtures cover selection behavior
- **WHEN** the focused analysis test suite runs
- **THEN** fixtures cover insufficient PA, acceptable PA, excessive PA, noise, dropout, saturation, timing offset, range-boundary, and inconsistent-cycle responses
- **AND** expected candidate or inconclusive outcomes are asserted

#### Scenario: Failure injection covers cleanup
- **WHEN** state-machine tests inject failures at acquisition start, motion queueing, capture, analysis, reporting, cancellation, and cleanup
- **THEN** temporary pressure advance and acquisition ownership are restored for every recoverable path

#### Scenario: Controlled printer comparison validates candidate reporting
- **WHEN** hardware validation is performed on representative Max 4 filament and temperature combinations
- **THEN** load-cell candidates are compared with conventional printed PA calibration results
- **AND** the recorded evidence defines the accepted agreement and repeatability tolerances
