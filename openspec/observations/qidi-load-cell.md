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
- **Unresolved:** direct-read coverage, ordering, invalid-excursion classification, and saturation behavior during stationary extruder trapq work require controlled hardware validation.
- **Unresolved:** stock probing behavior must be checked after concurrent capture and extrusion tests before calibration acquisition is enabled.

Sanitized idle cadence evidence is stored in `openspec/changes/add-load-cell-pa-calibration/evidence/direct-read-cadence.json`. Host, MCU, and compiled-artifact hashes are recorded in `openspec/changes/add-load-cell-pa-calibration/reverse-engineering.md`.

## Extruder pressure-advance surface

- **Static-recovered:** installed QIDI `extruder.py` queues extruder trapq entries with independent `axis_r_e` and `can_pressure_advance` fields.
- **Static-recovered:** normal E-only G-code and `toolhead.manual_move()` do not mark extrusion as pressure-advance eligible.
- **Static-recovered:** direct `TrapqMove.append()` can represent E-only movement with `axis_r_e=1` and `can_pressure_advance=1` without generating X, Y, or Z movement.
- **Unresolved:** direct trapq injection bypasses `PrinterExtruder.check_move()` and has not been physically validated for stationary pressure-advance extrusion, nominal-E bookkeeping, cancellation, or cleanup on the Max 4.

## Filament and nozzle state

- **Runtime-confirmed:** `filament_switch_sensor filament_switch_sensor.filament_detected` remains readable when its `enabled` event-policy field is false.
- **Runtime-confirmed:** QIDI Box state exposes controller loaded state, extruder and Box E-endstop state, synchronized slot, last-loaded slot, and physical-slot `IN_EXTRUDER` state; `slot16` is the direct-feed sentinel.
- **Runtime-confirmed:** the QIDI screen stores nozzle selection in Moonraker's custom `config` table as `nozzle.diameter`, while Klipper separately reports `printer.configfile.settings.extruder.nozzle_diameter`.
- **Static-recovered:** QIDI's Moonraker config-update handler updates the custom database value without updating `printer.cfg`, so the two nozzle values can diverge.
