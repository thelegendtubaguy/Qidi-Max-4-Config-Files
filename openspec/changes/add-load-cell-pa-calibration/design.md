## Context

The Max 4 stock configuration defines `[probe_air]` in `config/printer.cfg` with `sensor_type: c_sensor` on toolhead MCU pins `THR:PB3` and `THR:PB4`. QIDI's compiled `air` module constructs a compiled CS1237 helper, and the live sensor reports configuration `0x3c`; the installed `cs1237` module maps that configuration to 1280 samples per second. A one-shot stock command exposes raw counts and differential voltage, but the stock module exposes no usable continuous public subscription API: `PrinterAirProbe.add_client(cb)` is a no-op in the inspected firmware.

The CS1237 helper retains private bulk acquisition objects and query commands. Any integration with those objects is firmware-sensitive and must leave QIDI's nozzle probing, Z homing, Z tilt, and bed mesh behavior unchanged.

The installed QIDI Klipper `extruder.py` marks normal G-code pressure advance eligible only for positive extrusion combined with X or Y motion. Klipper's C pressure-advance transform actually keys off the extruder trapq move's `can_pressure_advance` flag and does not require physical XY step generation. A Python extra can therefore inject PA-enabled E-only trapezoids directly into the extruder trapq while the toolhead remains stationary, but no stable public API exposes this behavior; the adapter must be version-pinned and must reproduce the safety, timing, and bookkeeping normally provided by the motion planner.

The force-response method drives repeated low/high/low extrusion-rate transitions while testing pressure advance values. Insufficient PA produces delayed rounded transitions; excessive PA produces overshoot and deceleration undershoot. The result is an estimate that requires printer-specific validation, not a guaranteed material property.

`reverse-engineering.md` records the analyzed host/MCU artifacts, hashes, protocol commands, homing lifecycle, firmware disassembly, rejected bulk path, live scheduled-read measurements, invalid-read evidence, Reddit claims, installer restart caveat, and remaining hardware gaps. `evidence/direct-read-cadence.json` preserves sanitized per-sample counts and timing from the 100, 250, 500, 800, and 1000 Hz idle captures.

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

The analyzed toolhead MCU firmware reports protocol version `02.02.01.08` and does not advertise a `cs1237_data` response even though the compiled Python helper registers a queue for it. `CS1237.setup_home()` arms `cs1237_setup_home` with `TriggerDispatch`/`trsync` endstop semantics and is not used for calibration. The supported acquisition path schedules plain `query_cs1237_read` commands at future MCU clocks and captures their `query_cs1237_data` responses through a temporary OID-scoped handler.

Capture owns an exclusive state token. Homing, probing, Z tilt, bed mesh, another calibration, shutdown, and cancellation cannot share the sensor. Capture startup and shutdown leave the probe's zero, threshold, calibration objects, and homing dispatch untouched.

Alternative: replace the stock probe with upstream Klipper `[load_cell]`. Rejected because it changes vendor homing and probing behavior and would require revalidating the printer's primary Z safety mechanism.

### Separate sensor decoding, time alignment, analysis, and printer orchestration

The Python extra is divided into four small units:

1. A QIDI CS1237 adapter schedules bounded non-homing direct reads, decodes signed 24-bit responses, and records host receive timing.
2. A capture coordinator records bounded responses and the exact directly queued extruder-trapq transition schedule in Klipper print-time coordinates; hardware validation must bound cached-conversion age before treating receive time as sample time.
3. A pure analysis module normalizes cycle baselines and computes transition delay, rise/fall response, overshoot, deceleration undershoot, settling, and repeatability.
4. A printer-facing state machine enforces preconditions, queues motion, restores state, and emits results.

The ADC remains configured at 1280 SPS while capture requests direct reads at 500 Hz. The host queues requests with equal future `minclock` and `reqclock`, receives responses asynchronously through an OID-scoped `query_cs1237_data` handler, and yields to the Klipper reactor during the bounded capture window. `reqclock` alone is insufficient because it is a requested transmission deadline rather than a not-before constraint; live testing returned only 40 of 100 requests when `minclock` was omitted. The validated result is an idle host-response cadence, not proof that every response represents a fresh ADC conversion or that receive time equals conversion time. Missing responses, stale/duplicate conversions, conversion-age uncertainty, timing gaps, and invalid excursions fail quality gates. Optional diagnostic artifacts are written only by an explicit diagnostic mode used during controlled validation, not by the public macro's normal path.

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

The analysis uses deceleration undershoot as the primary indication that compensation has become excessive, with transition timing, settling, overshoot, and cycle repeatability as corroborating metrics. A coarse bounded sweep locates the transition region; a finer bounded sweep estimates the last non-excessive K. Exact sweep limits, rates, cycle counts, filters, and thresholds are constants covered by synthetic fixtures and controlled-printer evidence rather than user-facing knobs.

A candidate is reportable only when all quality gates pass, including:

- expected sample coverage and no material dropouts;
- no ADC saturation;
- sufficient force-response amplitude relative to baseline noise;
- consistent polarity and response across repeated cycles;
- a unique transition or cost minimum inside, not at the edge of, the tested range;
- agreement between the primary undershoot decision and corroborating response metrics within a defined tolerance.

The normal success response includes `PA_VALUE=<decimal>`, the calibration temperature, the nozzle diameter, and `PERSISTED=0`. The command does not prescribe, inspect, or act on how the user uses the reported value. Inconclusive runs report a reason code and no value token.

Alternative: always return the best numeric score. Rejected because a plausible-looking value from weak or clipped data is worse than an explicit inconclusive result.

### Restore state and leave the candidate unapplied

Before the first temporary change, the state machine captures the active pressure advance, smooth time, G-code coordinate/extrusion modes, extruder nominal position, trapq ownership, velocity limits changed by setup or cleanup macros, and sensor query state. A single idempotent cleanup path runs after success, analysis rejection, G-code error, cancellation, or Klipper shutdown.

Cleanup stops capture, drains or finalizes owned trapq moves, and restores the original pressure advance plus temporary software state. It does not call `SAVE_CONFIG`, `SAVE_VARIABLE`, or `SET_GCODE_VARIABLE` for the result. It does not write slicer files. The requested `TEMP` remains the active target, the bed remains at absolute `Z=200`, and the toolhead remains parked over the trash chute after final `CLEAR_OOZE` and `CLEAR_FLUSH` cleanup.

### Install the extra as a guarded external managed file

Source lives under `installer/klipper/extras/` and installs to the printer's Klipper extras directory under a project-unique module name. `installer/package.yaml` and installer runtime state track the destination, installed hash, and any preimage. Install uses atomic replacement and rollback; uninstall removes the exact managed version or restores a recorded preimage. Drift or an unknown destination file fails safely instead of overwriting it.

The optimized macro/config section remains under `installer/klipper/tltg-optimized-macros/`. Stock-mapped `config/` files receive no direct tuning changes beyond existing include wiring.

### Implement analysis independently

The response metrics and state machine are implemented from the force-response behavior and local test fixtures. Code is not copied from AGPL-licensed PrusaPATuner. Any external implementation consulted during development is recorded in dependency or provenance documentation with its license.

## Risks / Trade-offs

- **Private QIDI CS1237 attributes change between firmware releases** → Confine access to one adapter, validate every required attribute and sensor configuration before side effects, and gate supported firmware variants.
- **Current firmware has no advertised `cs1237_data` response** → Never arm `CS1237.setup_home()` for calibration; schedule bounded `query_cs1237_read` commands and consume OID-scoped `query_cs1237_data` responses instead.
- **Direct trapq injection bypasses normal move validation and relies on a private ABI** → Pin and validate the installed Klipper implementation, isolate all CFFI calls in one adapter, reproduce heater and E-only limits, reject any ABI mismatch, and test nominal-E bookkeeping plus cancellation exhaustively.
- **Extrusion force is too weak or contaminated by flap-clearing motion** → Complete a capture-only printer spike before enabling candidate reporting; stop capture around `CLEAR_FLUSH`, reacquire a settled baseline, and use repeated-cycle fixtures.
- **Waste extrusion accumulates while the flap is closed** → Run `CLEAR_FLUSH` after no more than two measured pulses, cap total extrusion, and run final `CLEAR_OOZE` plus `CLEAR_FLUSH` cleanup.
- **QIDI screen and Klipper nozzle sizes diverge** → Require explicit `NOZZLE` and report it with the result rather than selecting either stored value implicitly.
- **The optimum force response does not match the best printed PA** → Label output as a candidate, never persist it, compare against conventional printed tests across representative materials, and tune gates from recorded evidence.
- **Scheduled direct reads may load the constrained printer host or return invalid excursions** → Default to the validated 500 Hz rate, bound duration and queued command count, yield to the reactor, avoid raw console logging, and reject captures with insufficient timing coverage or invalid-read contamination.
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

- Does the validated 500 Hz direct-response cadence remain stable while stationary extruder trapq work is active?
- What bounds the age of the cached ADC conversion returned by `query_cs1237_read`, and what conversion-time error can be assigned relative to Klipper print time?
- Which raw/timing invariant distinguishes invalid direct reads from real force transitions without weakening fail-closed behavior?
- What low/high flow rates, segment duration, K bounds, and refinement step produce clean transitions without excessive purge volume on the Max 4 hotend?
- What low/high filament-feed schedule is safe and measurable for each required `NOZZLE` value (`0.2`, `0.4`, `0.6`, and `0.8`)?
- Is one or two measured pulses per `CLEAR_FLUSH` cycle the best balance between signal repeatability, flap clearing, and calibration duration?
- Does force-response polarity or baseline drift change materially with nozzle size, filament stiffness, requested temperature, or QIDI Box feed state?
- What numerical agreement threshold against printed calibration is required before candidate reporting is enabled by default?
