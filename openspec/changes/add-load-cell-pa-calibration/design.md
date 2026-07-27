## Context

The Max 4 stock configuration defines `[probe_air]` in `config/printer.cfg` with `sensor_type: c_sensor` on toolhead MCU pins `THR:PB3` and `THR:PB4`. QIDI's compiled `air` module constructs a compiled CS1237 helper, and the live sensor reports configuration `0x3c`; the installed `cs1237` module maps that configuration to 1280 samples per second. A one-shot stock command exposes raw counts and differential voltage, but the stock module exposes no usable continuous public subscription API: `PrinterAirProbe.add_client(cb)` is a no-op in the inspected firmware.

The CS1237 helper retains private bulk acquisition objects and query commands. Any integration with those objects is firmware-sensitive and must leave QIDI's nozzle probing, Z homing, Z tilt, and bed mesh behavior unchanged.

The installed QIDI Klipper `extruder.py` marks normal G-code pressure advance eligible only for positive extrusion combined with X or Y motion. Klipper's C pressure-advance transform actually keys off the extruder trapq move's `can_pressure_advance` flag and does not require physical XY step generation. A Python extra can therefore inject PA-enabled E-only trapezoids directly into the extruder trapq while the toolhead remains stationary, but no stable public API exposes this behavior; the adapter must be version-pinned and must reproduce the safety, timing, and bookkeeping normally provided by the motion planner.

The force-response method drives repeated low/high/low extrusion-rate transitions while testing pressure advance values. Klipper's PA transform changes extruder velocity by approximately `K × nominal E acceleration`, so acceleration is a controlled excitation input rather than an omitted variable. Insufficient PA produces delayed rounded transitions; excessive PA produces overshoot and deceleration undershoot. The result is an estimate that requires printer-specific validation, not a guaranteed material property.

`reverse-engineering.md` records the analyzed host/MCU artifacts, hashes, protocol commands, homing lifecycle, firmware disassembly, rejected bulk path, live scheduled-read measurements, invalid-read evidence, Reddit claims, installer restart caveat, and remaining hardware gaps. `evidence/direct-read-cadence.json` preserves sanitized scheduled-read and idle origin-cache cadence. `evidence/origin-cache-under-force.json` preserves bounded host-call intervals and load-cell counts from stationary `0.4 mm` PLA pulses at `215 °C`.

## Goals / Non-Goals

**Goals:**

- Provide one `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` macro that receives the user's intended filament temperature and installed nozzle diameter.
- Home all axes, move the build plate to absolute `Z=200`, park over the rear trash chute, and keep the toolhead stationary during measured extrusion pulses.
- Capture the stock hotend load-cell signal during controlled PA-enabled E-only extrusion above the rear trash chute.
- Clear the trash-chute flap after no more than two measured pulses and run final ooze and flush cleanup after calibration.
- Support filament already loaded from either the QIDI Box or the external-spool path without changing the selected source.
- Derive and report a pressure advance candidate only when capture coverage, signal strength, repeatability, and candidate selection pass explicit quality gates.
- Restore the pre-calibration pressure advance and every temporary software or sensor-acquisition state on all exit paths.
- Deploy and remove the Python extra through the installer without replacing vendor probing modules or leaving unmanaged files.
- Keep signal processing deterministic and testable without printer hardware.

**Non-Goals:**

- Persisting, automatically applying after the command, or inserting the candidate into Klipper, OrcaSlicer, or QIDI Studio configuration.
- Maximum volumetric-flow calibration.
- Print-time extrusion monitoring, automatic pause or recovery, or closed-loop extrusion correction.
- Replacing `[probe_air]`, the compiled `air`/`cs1237` modules, or QIDI probing behavior.
- Claiming that the reported candidate is equivalent to a validated printed PA result without controlled-printer comparison.

## Decisions

### Use one public macro backed by a Python Klipper extra

`TLTG_PA_CALIBRATE` is the only public operator entrypoint. A thin macro keeps the command visible in Fluidd, while an installer-managed Python extra owns capture, trapq scheduling, analysis, cancellation, and result reporting.

The public macro requires `TEMP=<celsius>` and `NOZZLE=<mm>`. It validates both values before side effects, homes all axes while the nozzle is not being heated for calibration, moves the build plate to absolute `Z=200`, parks through `OPTIMIZED_MOVE_TO_TRASH`, sets and waits for the requested nozzle temperature, and then starts capture. It does not restore the pre-calibration XYZ position; completion leaves the bed at `Z=200` and the toolhead parked over the trash chute.

Alternative: implement the sequence entirely in Jinja/G-code macros. Rejected because macros cannot safely consume high-rate CS1237 batches, align sensor samples with queued extrusion, mark stationary E-only trapq moves as pressure-advance eligible, run deterministic analysis, or guarantee capture cleanup.

### Require nozzle diameter instead of trusting the screen setting

The QIDI screen stores its selection in QIDI's custom Moonraker config table as `nozzle.diameter`; the live value is available through `POST /server/database/config/select_all`. Klipper separately exposes `printer.configfile.settings.extruder.nozzle_diameter`. The QIDI Moonraker update handler writes the screen value to its database but does not update `printer.cfg`, so the two values can diverge after a nozzle change.

The calibration macro therefore requires `NOZZLE=<mm>` and reports it with the candidate. It accepts the four Max 4 nozzle sizes `0.2`, `0.4`, `0.6`, and `0.8` mm. It does not perform a blocking localhost HTTP request from Klipper, read Moonraker's SQLite database, or silently fall back to the potentially stale Klipper `nozzle_diameter`.

Nozzle diameter is not an input to Klipper's PA equation or the force-response candidate metric. It selects nozzle-specific low/high flow schedules, pulse duration, extrusion caps, signal thresholds, and hardware-validation fixtures because nozzle restriction changes the measured pressure response and can change the resulting K value.

Alternative: query QIDI's custom Moonraker endpoint from the Klipper reactor. Rejected because it adds a synchronous cross-service dependency and the screen database is QIDI-specific rather than part of Klipper's runtime object contract.

### Require corroborated loaded-filament state

Preflight reads `filament_switch_sensor filament_switch_sensor.filament_detected` as the primary physical toolhead gate. The sensor's `enabled` field controls event handling only: QIDI's installed `RunoutHelper.note_filament_present()` updates `filament_present` before its disabled-event early return. A disabled runout action policy therefore does not make the physical status unavailable.

When QIDI Box is disabled, a true toolhead switch is sufficient to identify loaded external-spool filament. When Box is enabled, preflight also requires the read-only compiled `multi_color_controller` aggregate, `box_extras.e_endstop_state`, `slot_sync`, and `last_load_slot` to agree that the source is loaded, synchronized, connected, and idle. A synchronized `slot16` is treated as direct-feed/external input; physical Box slots require controller state `2` (`IN_EXTRUDER`). Calibration reads these states without enabling/disabling sensors or changing source selection.

Alternative: trust saved `last_load_slot`, `slot_sync`, or `extrude_state` alone. Rejected because saved variables can be stale and do not prove that filament currently reaches the toolhead switch.

Alternative: call compiled `box_extras.detect_filament_loaded()`. Rejected because its complete side-effect contract is not established and the required state is already exposed through side-effect-free status methods.

### Adapt the existing CS1237 helper without replacing it

The extra looks up `probe_air`, verifies the expected `sensor_helper`, one-shot query commands, MCU, and rate/configuration contract, then acquires the signal through a narrow compatibility adapter. The adapter is the only code allowed to access QIDI private attributes. Unsupported or changed firmware fails before heating, motion, extrusion, or PA changes.

The analyzed toolhead MCU firmware reports protocol version `02.02.01.08` and does not advertise a `cs1237_data` response even though the compiled Python helper registers a queue for it. `CS1237.setup_home()` arms `cs1237_setup_home` with `TriggerDispatch`/`trsync` endstop semantics and is not used for calibration. Scheduled `query_cs1237_read` commands can return `query_cs1237_data` through a temporary OID-scoped handler, but this is not a validated acquisition transaction: the firmware consumes only the OID, returns cached object fields, and provides no request sequence or conversion timestamp.

The stock `query_cs1237_config_r` command is also unsuitable as a compatibility fence. Static firmware analysis shows that it drives ten SCLK transitions without recovered serialization against periodic acquisition. A controlled idle sequence with no direct reads returned `60`, `60`, and `255`; another campaign produced duplicate direct responses followed by a non-`60` preflight result. Re-reading configuration before and after capture can therefore disturb or misframe the same sensor state it is intended to verify.

Capture ownership, handler cleanup, unique response identities, and firmware-restart shutdown guards remain implemented and tested, but public calibration and all developer sensor commands are hard-disabled. `CS1237.read_origin_data()` is the remaining acquisition candidate: it calls only the GPIO-passive cached-SRAM response handler and returned live values in repeated 40 and 50 Hz idle tests. Four controlled captures requesting 40 Hz polling overlapped stationary `0.4 mm` PLA pulses at `215 °C`; synchronous stalls created local call-start gaps despite approximately 40 Hz mean cadence, and every run produced a force-correlated low/high/low response, preserved XYZ and logical E, restored PA and smooth time, retained QIDI Box `slot4`, and was followed by successful stock `G28`. Conversion age, deterministic placement inside each host call interval, and cycle repeatability remain unvalidated. `query_cs1237_begin(config=60)` is a stock reconfiguration command, not an accepted restoration path; its cache, freshness, zero-state, and post-restart behavior remain unvalidated.

Alternative: replace the stock probe with upstream Klipper `[load_cell]`. Rejected because it changes vendor homing and probing behavior and would require revalidating the printer's primary Z safety mechanism.

### Separate sensor decoding, time alignment, analysis, and printer orchestration

The Python extra is divided into four small units:

1. A QIDI CS1237 adapter polls the GPIO-passive `read_origin_data()` cache at no more than 50 Hz, validates homing ownership before and after capture, records host and estimated print-time call intervals, and never reads configuration.
2. A capture coordinator owns the sensor with a process-local exclusive token, records bounded values and the exact directly queued extruder-trapq transition schedule in Klipper print-time coordinates, releases ownership on every path, and forces shutdown if post-capture homing state or ownership cannot be verified. Hardware validation must still bound cached-conversion age before assigning a conversion time inside each synchronous call interval.
3. A pure analysis module compares the normalized force trace with the known acceleration-defined E-flow waveform and computes transition tracking error, rise/fall delay, overshoot, deceleration undershoot, signed recovery area, recovery error, plateau slope, settling, and repeatability.
4. A printer-facing state machine enforces preconditions, queues motion, restores state, and emits results.

The ADC nominally runs at 1280 SPS. Characterization scheduled requests with equal future `minclock` and `reqclock`, an OID-scoped `query_cs1237_data` handler, and bounded reactor yielding. `reqclock` alone is insufficient because it is a requested transmission deadline rather than a not-before constraint; live testing returned only 40 of 100 requests when `minclock` was omitted. Controlled stationary-extrusion testing found that three of nine 500 Hz captures missed responses. Repeated idle 250 Hz testing later produced distinct-response counts of `245/250` and `249/250`; the following preflight rejected the sensor configuration. Configuration-only testing then returned `60`, `60`, and `255` without direct reads. No direct-read rate or configuration-check cadence is a validated production choice. Optional diagnostic artifact generation remains source-gated and inaccessible in installed configuration.

Alternative: stream samples to an external service for analysis. Rejected because host/network timing would weaken motion alignment and add a runtime dependency to a single-printer calibration command.

### Inject PA-enabled E-only trapq moves with a stationary toolhead

After full homing, absolute `Z=200`, and `OPTIMIZED_MOVE_TO_TRASH`, the Python extra flushes normal lookahead and directly appends bounded E-only trapezoids to the active extruder trapq with the PA eligibility field enabled. The toolhead remains stationary over the trash chute during every measured low/high/low pulse. The adapter maintains nominal E position, schedules moves at a safe future print time, provides lead-in and lead-out segments for PA smoothing, waits for completion, and prevents overlap with ordinary queued extrusion.

Because direct trapq injection bypasses `PrinterExtruder.check_move()`, the state machine independently enforces `can_extrude`, maximum E-only distance, velocity, acceleration, total extrusion, and valid positive-extrusion constraints before queueing. It verifies the installed trapq CFFI ABI and QIDI Klipper implementation before any calibration side effect.

Alternative: use small coordinated XY+E moves through normal G-code. Rejected because the measured force would include toolhead-motion contamination and the user requires a stationary toolhead during calibration pulses.

Alternative: use normal E-only G-code or `toolhead.manual_move()`. Rejected because both leave `can_pressure_advance` false and therefore do not exercise Klipper's PA transform.

### Clear the trash chute between pulse groups

The trash-chute flap closes when the toolhead is parked over it. The sequence stops measurement capture after no more than two PA testing extrusion pulses, runs vendor `CLEAR_FLUSH` to move away and return so the flap cycles, waits for toolhead and sensor settling, and starts a fresh baseline before the next measured group. Chute-clearing motion is never included in PA analysis.

No ooze or flush cleanup runs before the first measured pulse. After the final measured group, or after a recoverable failure that has extruded material, cleanup restores normal motion ownership and runs `CLEAR_OOZE` followed by `CLEAR_FLUSH`. Cleanup availability and command registration are preflight requirements. If motion is unsafe because Klipper is shutting down, cleanup performs no new motion and reports that manual chute cleanup is required on the next start.

### Select conservatively and fail closed

Each measured cycle retains the planned low/high velocities, positive E acceleration, acceleration and deceleration ramp boundaries, and K value. Analysis validates `ramp_time = (high_velocity - low_velocity) / acceleration` before comparing the baseline-normalized force trace with the resulting ideal E-flow waveform. It does not infer acceleration from load-cell data or treat constant-flow force alone as PA evidence.

The primary objective is independently implemented as three fixed components: transition tracking (`tracking error`, `rise delay`, `fall delay`), excessive compensation (`overshoot`, `undershoot`), and recovery stability (`settling error`, `absolute recovery error`, `plateau slope`). Each component is normalized within the bounded sweep, and the fixed objective weights are covered by deterministic fixtures rather than exposed as operator controls. Signed post-deceleration recovery area is an independent corroborator: positive area indicates residual pressure lag, negative area indicates reversal, and the sweep must bracket one ordered positive-to-negative transition near the objective minimum.

A reportable sweep contains the same K grid and repeated cycles at two distinct validated E accelerations. Each acceleration profile must have one unique interior objective minimum, signed-area bracketing, stable polarity, and repeatable metrics. Both profiles must select the same refined-grid K; acceleration-dependent candidates fail closed. A coarse bounded sweep locates the transition region, and a finer bounded sweep provides the reported grid value.

A candidate is reportable only when all quality gates pass, including:

- expected sample coverage and no material dropouts;
- no ADC saturation;
- sufficient force-response amplitude relative to baseline noise;
- consistent polarity and response across repeated cycles;
- complete K coverage at both acceleration profiles;
- a unique objective minimum inside, not at the edge of, the tested range;
- one ordered signed recovery-area bracket near that minimum;
- exact refined-grid candidate agreement between acceleration profiles.

The normal success response includes `PA_VALUE=<decimal>`, the calibration temperature, the nozzle diameter, and `PERSISTED=0`. The command does not prescribe, inspect, or act on how the user uses the reported value. Inconclusive runs report a reason code and no value token.

Alternative: always return the best numeric score. Rejected because a plausible-looking value from weak or clipped data is worse than an explicit inconclusive result.

### Restore state and leave the candidate unapplied

Before the first temporary change, the state machine captures the active pressure advance, smooth time, G-code coordinate/extrusion modes, extruder nominal position, trapq ownership, velocity limits changed by setup or cleanup macros, and sensor query state. A single idempotent cleanup path runs after success, analysis rejection, G-code error, cancellation, or Klipper shutdown.

Cleanup stops capture, drains or finalizes owned trapq moves, and restores the original pressure advance plus temporary software state. It does not call `SAVE_CONFIG`, `SAVE_VARIABLE`, or `SET_GCODE_VARIABLE` for the result. It does not write slicer files. The requested `TEMP` remains the active target, the bed remains at absolute `Z=200`, and the toolhead remains parked over the trash chute after final `CLEAR_OOZE` and `CLEAR_FLUSH` cleanup.

### Install the extra as a guarded external managed file

Source lives under `installer/klipper/extras/` and installs to the printer's Klipper extras directory under a project-unique module name. `installer/package.yaml` and installer runtime state track the destination, installed hash, and any preimage. Install uses atomic replacement and rollback; uninstall removes the exact managed version or restores a recorded preimage. Drift or an unknown destination file fails safely instead of overwriting it.

The optimized macro/config section remains under `installer/klipper/tltg-optimized-macros/`. Stock-mapped `config/` files receive no direct tuning changes beyond existing include wiring.

### Implement analysis independently

The response metrics, three-component objective, signed-area corroboration, acceleration-profile agreement gates, and state machine are implemented independently from the behavior contract and locally authored fixtures. No source, formulas, weights, comments, or test vectors are copied from AGPL-licensed PrusaPATuner. External implementations consulted during development are recorded with commit and license provenance.

## Risks / Trade-offs

- **Private QIDI CS1237 attributes change between firmware releases** → Confine access to one adapter and keep acquisition disabled until a non-disruptive compatibility transaction is hardware-validated.
- **Current firmware has no advertised `cs1237_data` response** → Never arm `CS1237.setup_home()` for calibration; cached `query_cs1237_read` responses are characterization evidence only and are not an accepted production substitute.
- **Configuration reads toggle the live sensor clock without recovered serialization** → Do not treat `query_cs1237_config_r` as side-effect-free or use repeated config reads as a capture fence; require firmware restart after an observed non-stock result.
- **Direct trapq injection bypasses normal move validation and relies on a private ABI** → Pin and validate the installed Klipper implementation, isolate all CFFI calls in one adapter, reproduce heater and E-only limits, reject any ABI mismatch, and test nominal-E bookkeeping plus cancellation exhaustively.
- **Extrusion force is too weak or contaminated by flap-clearing motion** → Complete a capture-only printer spike before enabling candidate reporting; stop capture around `CLEAR_FLUSH`, reacquire a settled baseline, and use repeated-cycle fixtures.
- **Waste extrusion accumulates while the flap is closed** → Run `CLEAR_FLUSH` after no more than two measured pulses, cap total extrusion, and run final `CLEAR_OOZE` plus `CLEAR_FLUSH` cleanup.
- **QIDI screen and Klipper nozzle sizes diverge** → Require explicit `NOZZLE` and report it with the result rather than selecting either stored value implicitly.
- **The optimum force response does not match the best printed PA** → Label output as a candidate, never persist it, compare against conventional printed tests across representative materials, and tune gates from recorded evidence.
- **Synchronous origin-cache reads may stall the constrained printer host or obscure conversion time** → Cap characterization at 50 Hz and 250 calls, record each call interval, yield to the reactor, reject incomplete or non-monotonic timing, and keep candidate reporting disabled until cached-conversion age and force-safe invalid-read handling are bounded.
- **Cleanup fails after a partial start** → Make acquisition stop and state restoration idempotent, register shutdown/cancel handling, and test every state-machine transition with injected failures.
- **Installer mutation outside `config/` complicates rollback** → Track the extra as a hashed managed external file with preimage restoration and include installer-core integration tests.

## Migration Plan

1. Add analysis and state-machine tests using synthetic force traces before enabling hardware acquisition.
2. Add the guarded CS1237 adapter and an explicit developer-only capture path for controlled printer characterization.
3. Validate sample timing, force polarity, signal-to-noise ratio, direct-trapq scheduling, absolute `Z=200` clearance, repeated flap clearing, and cleanup on each supported firmware baseline when printer testing is available.
4. Enable candidate reporting only after captured traces distinguish insufficient, acceptable, and excessive PA and pass comparison against conventional printed calibration.
5. Package the Python extra and macro through installer dry-run, install, upgrade, rollback, and uninstall paths.

Rollback removes the optimized macro/config section, removes the exact managed Python extra or restores its preimage, restores installer state, and restarts Klipper through the installer flow. An imported Python extra requires a Klipper process/service restart after upgrade; Moonraker `/printer/restart` reloads configuration but does not reload changed Python module code. No PA result requires migration because no result is persisted.

## Open Questions

- What static or physical invariant bounds the age of the cached ADC conversion returned by `read_origin_data()`, and what conversion-time interval can be assigned relative to Klipper print time?
- Does 40 Hz origin polling provide repeatable transition metrics across the required K and acceleration coverage despite observed host-call stalls up to approximately 56 ms under force?
- Which raw/timing invariant distinguishes invalid sensor values from real force transitions without weakening fail-closed behavior?
- What low/high flow rates, segment duration, K bounds, and refinement step produce clean transitions without excessive purge volume on the Max 4 hotend?
- What low/high filament-feed schedule is safe and measurable for each required `NOZZLE` value (`0.2`, `0.4`, `0.6`, and `0.8`)?
- Is one or two measured pulses per `CLEAR_FLUSH` cycle the best balance between signal repeatability, flap clearing, and calibration duration?
- Does force-response polarity or baseline drift change materially with nozzle size, filament stiffness, requested temperature, or QIDI Box feed state?
- What numerical agreement threshold against printed calibration is required before candidate reporting is enabled by default?
