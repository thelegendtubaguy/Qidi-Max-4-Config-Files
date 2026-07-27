# QIDI Max 4 load-cell and extrusion observations

## Stock load-cell surface

- **Config-confirmed:** `config/printer.cfg` defines `[probe_air]` with `sensor_type: c_sensor`, `sclk_pin: THR:PB3`, and `dout_pin: THR:PB4`; the installed `probe_air.py` loads compiled `air.PrinterAirProbe` and `cs1237` support.
- **Runtime-confirmed:** firmware `01.01.06.04` reported CS1237 configuration `0x3c`; the compiled module maps that value to `USE_SPEED = 1280` samples per second.
- **Runtime-confirmed:** `probe_air.sensor_helper` exposed a compiled CS1237 object with `oid=5`, `bytes_per_block=4`, `blocks_per_msg=12`, and a `BulkDataQueue` registered for `('cs1237_data', 5)`.
- **Runtime-confirmed:** toolhead MCU protocol `02.02.01.08` advertised `query_cs1237_data` responses but no `cs1237_data` response, so the compiled Python bulk queue did not provide a usable continuous calibration stream.
- **Runtime-confirmed:** `query_cs1237_zero oid=5` returned four-byte little-endian frames whose low 24 bits decode as signed load-cell counts.
- **Static-recovered:** `PrinterAirProbe.add_client(callback)` accepted a callback but did not register it in the inspected compiled `air` module.
- **Static-recovered:** QIDI homing calls `CS1237.setup_home()`, starts `query_cs1237`, and arms `cs1237_setup_home` with `TriggerDispatch`/`trsync` endstop semantics.

## Scheduled direct-read behavior

- **Runtime-confirmed:** bounded `query_cs1237_read` commands scheduled with equal future `minclock` and `reqclock` returned OID-scoped `query_cs1237_data` without arming the homing-trigger path.
- **Runtime-confirmed:** one-second idle captures returned every requested response at 100, 250, 500, and 800 Hz. A 500 Hz run returned `500/500` responses with median receive interval `2.038 ms`, 95th-percentile interval `3.053 ms`, maximum interval `4.111 ms`, and median command round trip `0.468 ms`.
- **Runtime-confirmed:** a one-second 1000 Hz run returned `994/1000` responses and did not meet full-coverage requirements.
- **Runtime-confirmed:** direct captures included approximately one percent large transient excursions, including values near `-3`, `-262145`, and `-2196737`.
- **Unresolved:** response cadence does not prove ADC conversion freshness or establish conversion time relative to Klipper print time.
- **Runtime-confirmed:** with stationary direct-extruder trapq work active, six of nine 500 Hz captures returned every request; the other three missed one to three responses and maximum send intervals reached `15.096 ms`.
- **Runtime-confirmed:** one 250 Hz stationary direct-trapq capture returned `363/363` responses; one run does not establish a production capture rate.
- **Runtime-confirmed:** a later 250 Hz pulse returned `351/351`, but two accepted callbacks shared identical `#sent_time` and payload values. Ordered response cardinality alone therefore does not prove one distinct conversion per request.
- **Runtime-confirmed:** immediately after that pulse, the stock CS1237 configuration read `190` instead of required configuration `60`; the two-acceleration sweep stopped after one pulse, Klipper was firmware-restarted, final chute cleanup and full `G28` succeeded, and the loaded QIDI Box source remained unchanged.
- **Runtime-confirmed:** an idle repeat campaign completed three `50/50` captures at 50 Hz and three `250/250` captures at 250 Hz. The next two 250 Hz captures accepted `245/250` and `249/250` distinct response identities, and the following preflight rejected a non-`60` configuration before capture.
- **Runtime-confirmed:** with no direct reads, motion, heating, or extrusion, three `query_cs1237_config_r` calls spaced one second apart returned `60`, `60`, and `255`; the diagnostic guard shut Klipper down and recovery required `FIRMWARE_RESTART`.
- **Static-recovered:** `command_query_cs1237_read` at `0x0800bd28` consumes only the OID and returns cached object fields at offsets `0x74` and `0x78`; the advertised `reg` and `read_len` parameters are unused by toolhead MCU firmware `02.02.01.08`.
- **Static-recovered:** `command_query_cs1237_config_r` at `0x0800d3fc` drives ten SCLK transitions without a recovered lock against periodic CS1237 acquisition, so it is not a side-effect-free compatibility query.
- **Runtime-confirmed:** near-zero and large isolated excursions remained present during heated and stationary-extrusion captures.
- **Static-recovered:** compiled `CS1237.read_origin_data()` calls only `query_cs1237_zero`; the matching MCU handler loads cached SRAM `0x20000174` and emits four bytes without GPIO writes or object-state stores.
- **Runtime-confirmed:** three 40 Hz and three 50 Hz idle `read_origin_data()` runs returned every requested value and changed throughout each run, proving the cache is live while idle. Median synchronous call time was approximately `11.4 ms`; maximum host stalls ranged from `16.131` to `77.814 ms`.
- **Runtime-confirmed:** four captures requesting 40 Hz polling overlapped stationary `0.4 mm` PLA pulses at `215 °C`, two each at `10` and `20 mm/s²`. Mean call-start cadence remained approximately 40 Hz, but synchronous stalls created local gaps up to `61.746 ms` followed by catch-up reads; runs returned 87–93 values with 86–93 unique values, median call time `11.6–11.8 ms`, and maximum call time `27.400–56.424 ms`.
- **Runtime-confirmed:** high-flow window medians shifted by `-7418.5`, `-11376.5`, `-9548.5`, and `-10156.5` counts relative to each run's baseline. XYZ remained `(135, 403, 200)`, logical E returned to zero, PA `0.032` and smooth time `0.03` were restored, QIDI Box `slot4` remained loaded, an ordinary relative E move advanced exactly `1 mm`, and final stock `G28` completed.
- **Runtime-confirmed:** three additional K `0.020` campaigns ran five cycles at each of `10` and `20 mm/s²`. The campaigns used no soak with a `0.15 s` low-flow lead, a `300 s` soak with a `0.15 s` lead, and a `180 s` soak with a `0.5 s` lead. Every run met bounded phase coverage, retained 39.94–39.99 Hz mean call-start cadence, and restored XYZ, logical E, PA, smooth time, and QIDI Box source; maximum call duration was `52.534 ms` and maximum local call-start gap was `74.642 ms`.
- **Runtime-confirmed:** identical K `0.020` cycles did not pass signal repeatability. Depending on campaign and acceleration, idle-to-high response relative span was `18.5–71.5%`; low-to-high analyzer amplitude ranged from `45.5` to `6363` counts. Two of 30 short-lead cycles produced opposite analyzer polarity; the `0.5 s` lead retained polarity but still failed amplitude, timing, tracking, recovery, and signed-area repeatability.
- **Runtime-confirmed:** one unmeasured long-lead attempt rejected its dynamic capture-duration limit after one pulse was queued. Owned motion and temporary-state restoration completed, immediate final chute cleanup succeeded, and the temporary harness was corrected to validate the dynamic duration before queueing.
- **Runtime-confirmed:** the physical K sweep stopped before its first new K because the identical-excitation repeatability gate failed. Final `CLEAR_OOZE`, `CLEAR_FLUSH`, heater-off cleanup, stock `G28`, and source-state verification completed.
- **Repository-confirmed:** public calibration, developer direct capture, configuration diagnostics, and origin-cache capture remain hard-disabled in installed package `26.07.26.14`; staged preflight uses the GPIO-passive origin adapter and does not read configuration. The staged adapter exposes idempotent lease primitives; a temporary source-gated coordinator acquired before PA mutation or trapq queueing, held through capture and owned-motion restoration, and released with post-state homing verification. The installed public path does not invoke that coordinator; ownership or post-state uncertainty forces shutdown.
- **Unresolved:** origin-cache conversion age, deterministic placement inside each synchronous call interval, force-safe invalid-value classification, saturation behavior, and a repeatable pressure-conditioning/excitation schedule remain unproven.

Sanitized idle cadence evidence is stored in `openspec/changes/add-load-cell-pa-calibration/evidence/direct-read-cadence.json`; controlled under-force origin evidence is stored in `openspec/changes/add-load-cell-pa-calibration/evidence/origin-cache-under-force.json`; repeated K `0.020` qualification is stored in `openspec/changes/add-load-cell-pa-calibration/evidence/origin-cache-repeatability.json`. Host, MCU, and compiled-artifact hashes are recorded in `openspec/changes/add-load-cell-pa-calibration/reverse-engineering.md`.

## Extruder pressure-advance surface

- **Static-recovered:** installed QIDI `extruder.py` queues extruder trapq entries with independent `axis_r_e` and `can_pressure_advance` fields.
- **Static-recovered:** normal E-only G-code and `toolhead.manual_move()` do not mark extrusion as pressure-advance eligible.
- **Static-recovered:** direct `TrapqMove.append()` can represent E-only movement with `axis_r_e=1` and `can_pressure_advance=1` without generating X, Y, or Z movement.
- **Runtime-confirmed:** QIDI stores active pressure advance and `_set_pressure_advance()` on `PrinterExtruder.extruder_stepper`; its physical stepper trapq matched the active `PrinterExtruder` trapq.
- **Runtime-confirmed:** bounded direct-trapq pulses at `(135, 403, 200)` extruded PLA while recorded X, Y, and Z remained unchanged; PA `0.032` and smooth time `0.03` were restored after every pulse.
- **Runtime-confirmed:** an ordinary relative E move succeeded after direct pulses, and full `G28` probing succeeded after final cleanup.
- **Runtime-confirmed:** measured load-cell counts changed during `0.5` to `2.0` or `3.0 mm/s` filament-flow transitions, but repeated responses at K values `0`, `0.032`, and `0.08` did not support a repeatable candidate.
- **Operator-reported:** conventional printed calibration for the tested `0.4 mm` PLA-at-`215 °C` setup selected PA `0.020`; `0.016` through `0.024` is retained only as a provisional comparison window.
- **Repository-confirmed:** pure fixtures require the same interior composite-objective minimum and signed recovery-area bracket at `10` and `20 mm/s²`; acceleration-profile disagreement fails closed.
- **Unresolved:** cancellation, shutdown during generated steps, production queue ownership, physical two-acceleration candidate repeatability, and candidate agreement with printed calibration remain unvalidated.

## Filament and nozzle state

- **Runtime-confirmed:** `filament_switch_sensor filament_switch_sensor.filament_detected` remains readable when its `enabled` event-policy field is false.
- **Runtime-confirmed:** QIDI Box state exposes controller loaded state, extruder and Box E-endstop state, synchronized slot, last-loaded slot, and physical-slot `IN_EXTRUDER` state; `slot16` is the direct-feed sentinel.
- **Runtime-confirmed:** the QIDI screen stores nozzle selection in Moonraker's custom `config` table as `nozzle.diameter`, while Klipper separately reports `printer.configfile.settings.extruder.nozzle_diameter`.
- **Static-recovered:** QIDI's Moonraker config-update handler updates the custom database value without updating `printer.cfg`, so the two nozzle values can diverge.
