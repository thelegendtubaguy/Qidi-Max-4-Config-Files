## 1. Hardware Contract and Validation Fixtures

- [x] 1.1 Add a developer-only capture harness that records bounded `query_cs1237_data` payloads, MCU receive timing, ADC/requested sample rates, and queued motion markers without exposing a public PA candidate.
- [ ] 1.2 Complete the sensor contract in `reverse-engineering.md`, including cached-conversion freshness and print-time alignment bounds, the hardware-valid classifier for direct-read excursions under force, capture behavior during trapq work, and post-capture stock probe verification.
- [x] 1.3 Statically characterize the installed extruder trapq append ABI, PA eligibility field, nominal E bookkeeping, smoothing context, queue finalization, and cancellation contract for stationary PA-enabled E-only moves; physical behavior remains covered by tasks 5.2 and 5.5.
- [ ] 1.4 Measure idle, heated-idle, normal E-only, stationary direct-trapq low/high/low, and `CLEAR_FLUSH` traces to establish polarity, baseline drift, flap-motion contamination, settling time, and signal-to-noise behavior.
- [ ] 1.5 Validate and record full-homing behavior, absolute `Z=200` clearance, trash-chute park position, nozzle-specific flow and extrusion limits for `0.2`, `0.4`, `0.6`, and `0.8` mm nozzles, one-or-two-pulse clearing cadence, safe completion state, and abort conditions.
- [x] 1.6 Record the QIDI screen `nozzle.diameter` database behavior and its independence from Klipper `printer.configfile.settings.extruder.nozzle_diameter` as the reason `NOZZLE` is required.
- [x] 1.7 Create anonymized deterministic trace fixtures for insufficient, acceptable, excessive, noisy, dropped, saturated, timing-shifted, boundary, inconsistent, and post-flap-settling PA responses.
- [x] 1.8 Characterize the toolhead filament-switch status when event handling is disabled and the QIDI compiled Box loaded/synchronized aggregate; implement fail-closed loaded-source inspection without changing sensor policy or filament source.

## 2. Pure Capture and Analysis Components

- [x] 2.1 Add the project-namespaced Python extra source under `installer/klipper/extras/` with pure data types for raw batches, timed samples, direct-trapq transition schedules, pulse groups, cycle metrics, and candidate results.
- [x] 2.2 Implement and unit-test signed 24-bit CS1237 decoding, fixed-rate timestamp reconstruction, batch coverage accounting, saturation detection, direct-trapq schedule alignment, and exclusion of flap-clearing windows.
- [x] 2.3 Implement and unit-test cycle baseline normalization, response amplitude, rise/fall timing, overshoot, deceleration undershoot, settling, and repeatability metrics.
- [x] 2.4 Implement and unit-test bounded coarse/fine K selection with signal-strength, dropout, saturation, timing, repeatability, uniqueness, corroboration, and search-boundary quality gates.
- [x] 2.5 Verify that inconclusive fixtures return stable reason codes without `PA_VALUE=` and that valid fixtures return an interior value deterministically.
- [x] 2.6 Record external source provenance and licenses and verify that analysis code was implemented independently rather than copied from AGPL-licensed PrusaPATuner.

## 3. Klipper Sensor and Calibration Integration

- [x] 3.1 Complete the narrow QIDI `probe_air`/CS1237 compatibility adapter that validates exact configuration `60`, direct-read command/response attributes, clock scheduling, and handler ownership before side effects.
- [x] 3.2 Complete idempotent direct-read capture cleanup that unregisters the OID-scoped response handler, releases exclusive ownership, accounts for every queued request, rejects concurrent probe/calibration ownership, and leaves stock probe calibration and thresholds untouched.
- [x] 3.3 Complete side-effect-free calibration preflight for idle print state, required `TEMP` and supported `NOZZLE`, registered chute commands, compatible sensor and trapq interfaces, corroborated toolhead/QIDI loaded-filament status, QIDI Box/external-spool preservation, and nozzle-specific resource bounds.
- [ ] 3.4 Implement setup ordering that performs full stock `G28`, moves to absolute `Z=200`, parks with `OPTIMIZED_MOVE_TO_TRASH`, sets and stabilizes the requested temperature, and starts capture only after positioning settles.
- [ ] 3.5 Implement version-pinned direct extruder trapq scheduling for stationary PA-enabled E-only low/high/low pulses with explicit heater, distance, velocity, acceleration, positive-extrusion, nominal-position, timing, smoothing, and queue-overlap checks.
- [ ] 3.6 Implement pulse grouping that stops capture and runs `CLEAR_FLUSH` after no more than two measured pulses, returns to the chute, waits for settling, and acquires a new baseline without analyzing clearing motion.
- [ ] 3.7 Implement the calibration state machine and one cleanup path that finalize owned trapq work, restore original PA, smooth time, G-code modes, nominal E bookkeeping, motion limits, and acquisition ownership, then run final `CLEAR_OOZE` and `CLEAR_FLUSH` when motion is safe.
- [ ] 3.8 Add the thin `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` macro/config under `installer/klipper/tltg-optimized-macros/` with explicit required parameters, automatic full homing, and no implicit nozzle-size or temperature defaults.
- [ ] 3.9 Implement standard G-code responses that emit `PA_VALUE=<decimal>`, supplied temperature, supplied nozzle diameter, and `PERSISTED=0` only after all gates pass and otherwise emit an actionable reason without a value token or downstream-use instruction.
- [ ] 3.10 Add tests proving the success, failure, and cancellation paths never call `SAVE_CONFIG`, `SAVE_VARIABLE`, persist a G-code result variable, alter slicer files, change QIDI Box/external-spool selection, or leave the candidate active after cleanup.
- [ ] 3.11 Add failure-injection tests at adapter validation, direct-trapq validation, acquisition start, homing, `Z=200`, chute parking, pulse queueing, intermediate clearing, capture, analysis, reporting, cancellation, final clearing, and cleanup boundaries.

## 4. Reversible Installer Lifecycle

- [x] 4.1 Extend installer manifest models and parsing for a project-managed external Klipper-extra file with source, destination, expected hash, installed hash, and optional preimage state.
- [x] 4.2 Add path-safety, preflight collision/drift checks, atomic deployment, rollback registration, state-ledger recording, upgrade handling, and postflight verification for the Python extra.
- [x] 4.3 Add uninstall and restore behavior that removes only an unchanged managed extra or restores its recorded preimage and fails safely on unrecognized drift.
- [x] 4.4 Add the PA macro/config file to the optimized managed-tree requirements and package the Python extra in installer release bundles.
- [x] 4.5 Add installer unit and integration tests for fresh install, dry run, upgrade, destination collision, changed managed file, failed-install rollback, uninstall, preimage restoration, and bundle presence.
- [x] 4.6 Require or perform a process-level Klipper restart after an installed Python extra changes; do not treat Moonraker `/printer/restart` as proof that an already imported module was reloaded.

## 5. Controlled Max 4 Validation

- [ ] 5.1 Deploy the developer capture path only after the mandatory idle-state check and verify that normal Z homing, probing, Z tilt, and bed mesh behavior remain unchanged before and after capture.
- [ ] 5.2 Run repeated stationary-toolhead calibrations for `0.2`, `0.4`, `0.6`, and `0.8` mm nozzles and a small representative material/temperature matrix across QIDI Box and external-spool sources; record sample coverage, trapq timing residuals, force-response metrics, selected candidates, purge volume, flap-clear cadence, duration, and cleanup state.
- [ ] 5.3 Compare each load-cell candidate with a conventional printed PA calibration and define the accepted agreement and repeatability tolerances from recorded evidence.
- [ ] 5.4 Tune fixed sweep, filter, and quality-gate constants from the recorded evidence; keep candidate reporting disabled when the evidence does not bracket a reliable transition.
- [ ] 5.5 Validate cancellation and injected failures during homing, direct-trapq capture, intermediate flap clearing, final cleanup, and analysis on the printer; confirm original PA, nominal E bookkeeping, normal motion queues, and stock probe operation are restored.

## 6. Documentation and Final Validation

- [x] 6.1 Record the operator-visible `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` contract, homing, absolute `Z=200`, stationary trash-chute pulses, intermediate/final clearing, filament-source preservation, result format, failure format, and non-persistence in the change spec and design.
- [x] 6.2 Update `openspec/specs/optimized-printer-behavior/spec.md` with the staged fail-closed PA command behavior and implementation paths without presenting the candidate as validated or persistent.
- [x] 6.3 Update `openspec/specs/installer-lifecycle/spec.md` with external Python-file preflight, deployment, drift, rollback, upgrade, restore, uninstall, preimage, and process-activation behavior.
- [x] 6.4 Run `python3 scripts/format_klipper_configs.py` and the focused PA analysis, state-machine, macro-contract, installer known-version, and installer core test suites.
- [x] 6.5 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test` and verify the bundle contains the managed Python extra and PA macro/config.
- [x] 6.6 Run `python3 scripts/check_gcode_paths.py --write` and `python3 scripts/check_gcode_paths.py` if the concrete start-path command graph changes; otherwise record why PA calibration is outside start-print branch invariants.
- [x] 6.7 Review the implementation against every `load-cell-pa-calibration` scenario and record any remaining hardware-validation gap before marking the change complete.
- [x] 6.8 Preserve host/MCU reverse engineering, artifact hashes, command lifecycle, rejected acquisition paths, live cadence traces, invalid-read evidence, safety decisions, and unresolved validation work in `reverse-engineering.md`, `evidence/direct-read-cadence.json`, and `openspec/observations/qidi-load-cell.md` using the required evidence qualifiers.
- [x] 6.9 Remove the temporary local analysis-workspace inventory from `reverse-engineering.md`; retain only hashes, provenance, durable evidence, and reproducible acquisition instructions.
