# Max 4 CS1237 Acquisition and Calibration Evidence

## Current status

`installer/klipper/extras/tltg_pa_calibration.py` contains a hardware-tested non-homing CS1237 direct-response path. It schedules bounded `query_cs1237_read` commands and receives `query_cs1237_data` responses without calling the probe homing path. Idle and stationary direct-trapq captures are recorded, but ADC conversion freshness and conversion-time alignment are not yet validated.

`CALIBRATION_ENABLED = False` remains mandatory. `TLTG_PA_CALIBRATE TEMP=<celsius> NOZZLE=<mm>` returns `PA_CALIBRATION_UNVALIDATED` before heating, homing, motion, extrusion, capture, or pressure-advance changes. The production code includes fail-closed preflight, exact trapq runtime hashes, nozzle resource-plan validation, stationary lead-in/transition/lead-out planning, pulse grouping, orchestration ordering, idempotent cleanup, and result/failure formatting. Production nozzle plans remain `hardware_validated=False`, and no physical backend is wired to the public command.

Controlled `0.4 mm` nozzle testing with QIDI Box-fed PLA at `215 °C` established stationary direct-trapq extrusion, load-cell response under flow, PA/smooth-time restoration, ordinary E-move continuity, source preservation, chute clearing after at most two pulses, final cleanup, and successful stock `G28` after direct extrusion. Three of nine `500 Hz` under-load captures missed responses, isolated invalid excursions persisted, and repeated K responses were not repeatable enough to select a candidate. A single `250 Hz` under-load capture returned all `363/363` responses but is insufficient to establish a production rate.

Sanitized idle cadence traces are in `evidence/direct-read-cadence.json`; the controlled `0.4 mm` summary is in `evidence/controlled-0.4-pla-215.json`. Absolute host timestamps and temporary harness paths are not retained. Receive time is not treated as proven ADC conversion time, and the summary's exploratory outlier heuristic is not a production classifier.

## Installed Max 4 contract

`config/printer.cfg` defines `[probe_air]` with `sensor_type: c_sensor`, `dout_pin: THR:PB3`, and `sclk_pin: THR:PB4`. The compiled `air` module constructs the compiled `CS1237` helper used by QIDI Z homing and probing.

Live runtime introspection established this object contract:

| Object or field | Observed value |
|---|---|
| Klipper object | `probe_air` |
| Sensor helper type | compiled `CS1237` |
| Toolhead MCU OID | `5` |
| ADC configuration | `60` / `0x3c` |
| Host rate mapping | `USE_SPEED = 1280`; `{40: 28, 640: 44, 1280: 60}` |
| `bytes_per_block` | `4` |
| `blocks_per_msg` | `12` |
| Registered bulk handler | `('cs1237_data', 5)` |
| Direct-read response | `query_cs1237_data oid=5 data=%*s` |

`PrinterAirProbe.add_client(cb)` is a no-op in the inspected Max 4 host firmware. It is not a continuous public subscription interface.

`query_cs1237_zero oid=5` returned four-byte data including `b4 7a fb 00`. The first three bytes decode as signed little-endian 24-bit count `-296268`:

```text
unsigned = data[0] | data[1] << 8 | data[2] << 16
signed   = unsigned - 0x1000000 when unsigned & 0x800000 else unsigned
```

The fourth byte was zero in every retained cadence response. The production decoder still requires exactly four response bytes and uses only the first three as the signed ADC value until a separate fourth-byte meaning is proven.

The controlled printer reported Max 4 main firmware `01.01.06.04` during the live session. The toolhead protocol version and artifact SHA-256 are the compatibility authorities because the retained binaries do not cryptographically encode their association with the main firmware version.

## Explicit nozzle input evidence

The QIDI screen stores its nozzle selection in QIDI's custom Moonraker `config` table. Live `POST /server/database/config/select_all` output returned `nozzle.diameter: ["0.4"]`.

Klipper separately exposes the static `printer.configfile.settings.extruder.nozzle_diameter` loaded from `config/printer.cfg`. Inspection of QIDI's Moonraker update handler showed the screen selection being written to the custom database without a corresponding `printer.cfg` update. A screen nozzle change can therefore leave the two stores different even when both happened to report `0.4` during inspection.

`TLTG_PA_CALIBRATE` requires explicit `NOZZLE=0.2`, `0.4`, `0.6`, or `0.8`. It does not query the custom database, read Moonraker SQLite state, or infer nozzle size from Klipper's static setting.

## Loaded-filament sensor contract

The Max 4 exposes the toolhead filament switch directly as Klipper object `filament_switch_sensor filament_switch_sensor`. Its status contains independent fields:

```text
filament_detected: bool
  enabled: bool
```

`enabled` controls event processing, not physical status collection. QIDI's installed `filament_switch_sensor.py` executes `self.filament_present = is_filament_present` before returning early when `sensor_enabled` is false. Disabling the sensor therefore suppresses runout/insert actions while `filament_detected` continues to report the switch state.

`TLTG_FILAMENT_SENSOR ENABLE=0` does not call `SET_FILAMENT_SENSOR`; it only disables this project's automatic external-spool pause policy. Stock `DISABLE_ALL_SENSOR` can set the Klipper sensor's `enabled` field false, but that still does not invalidate `filament_detected`.

A read-only live query while the printer was `standby` established this aligned loaded state:

| Status field | Value |
|---|---|
| `filament_switch_sensor filament_switch_sensor.filament_detected` | `true` |
| `filament_switch_sensor filament_switch_sensor.enabled` | `false` |
| `multi_color_controller.extruder.loaded` | `true` |
| `multi_color_controller.extruder.filament_detected` | `true` |
| `multi_color_controller.sensors.e_endstop` | `1` |
| `box_extras.e_endstop_state` | `1` |
| `save_variables.slot_sync` / `last_load_slot` | same active slot |
| active `multi_color_controller.slots.states[slot]` | `2` / `IN_EXTRUDER` |

The current QIDI Box source was synchronized and idle during the query. No motion, extrusion, heater command, file change, or service restart was issued.

`multi_color_controller.so` publishes the compiled aggregate needed for Box consistency checks. `box_extras.so` also contains `detect_filament_loaded()`, but calibration preflight does not call that private method: its complete side-effect contract is not established, and the same physical and aggregate state is already available through `get_status()`.

Calibration preflight uses the toolhead switch as the primary physical gate and requires `filament_detected is True` regardless of the event-enabled flag. The public command remains blocked by the earlier `CALIBRATION_ENABLED` gate. When QIDI Box is enabled, preflight additionally requires:

- controller system ready and hardware connected;
- no active, failed, or user-blocked Box operation;
- compiled `extruder.loaded` and `extruder.filament_detected` both true;
- controller and `box_extras` E-endstop states both `1`;
- `slot_sync`, `last_load_slot`, and controller `last_loaded` agreement;
- physical Box slot state `2` for a Box slot.

`slot16` remains the direct-feed sentinel and does not require an entry in physical `slots.states`. Box-disabled filament and synchronized `slot16` filament are classified as external-spool input. Any absent field, unloaded switch, busy operation, unsynchronized source, or disagreement between physical and compiled status fails closed before homing, heating, movement, extrusion, or CS1237 acquisition.

## Reverse-engineering artifacts

The vendor binaries are not committed. Hashes identify the exact analyzed inputs:

| Artifact | SHA-256 | Binary metadata |
|---|---|---|
| `QD_MAX4_THR.bin` | `1d34b4b0142f2a2047f08c2fae59d827bcee0adbd1563b4bd1de311dff8b2d62` | STM32F103 toolhead MCU firmware; protocol version `02.02.01.08` |
| `QD_MAX4_SOC.deb` | `75fcc3a9729d93c6be90908c0e7014d1319a0a8e0f224418257894f423720035` | QIDI host package |
| `cs1237.so` | `7beb56413a902356d0aee2d0580e820342083b3b7f7194d1f5d7217f4b7ff4b5` | unstripped AArch64 ELF64, DWARF, BuildID `495a978bcac6c976efcf12050bc06d7743314d7c` |
| `air.so` | `8487fe77b0b6b621e168d9c0df54997b05bdd6f37e155979c0dab9186dedd24c` | unstripped AArch64 ELF64, DWARF, BuildID `4d8e76ef51afc90e8a0a39a1be31a1c1fd19705c` |

DWARF records `cs1237.so` as compiled from `/home/qidi/studio/cs1237/cs1237.c` and `air.so` from `/home/mks/studio/so/air/air.c`. Those are Cython-generated compile paths, not available vendor source trees.

The extracted `QD_MAX4_SOC.deb` copies of `cs1237.so` and `air.so` were byte-identical to the separately acquired modules.

Analysis used `file`, `nm`, `rabin2`, `radare2`, `strings`, `dwarfdump`, package extraction, the embedded Klipper protocol dictionary, QIDI-derived decompiled Python, and live runtime introspection. Function names inferred only from imperfect raw disassembly are not treated as authoritative when the protocol dictionary or DWARF contradicts them.

## Host command protocol

`CS1237._build_config()` creates these command wrappers:

| Purpose | Command and response | Host behavior |
|---|---|---|
| Configure pins | `config_cs1237 oid=%d dout_pin=%s sclk_pin=%s` | MCU configuration callback |
| Configure/restart ADC | `query_cs1237_begin oid=%c config=%u` → `query_cs1237_begin_read oid=%c config=%u` | Ready handler and `CS_WEIGHT_BEGIN`; expected config `60` |
| Read configured value | `query_cs1237_config_r oid=%c` → `query_cs1237_zero_config_read oid=%c config=%u` | Side-effect-free compatibility check |
| Direct data read | `query_cs1237_read oid=%c reg=%u read_len=%u` → `query_cs1237_data oid=%c data=%*s` | Validated non-homing acquisition uses `[oid, 0, 0]` |
| Read zero value | `query_cs1237_zero_read_only oid=%c` → `query_cs1237_zero_read_o oid=%c data=%*s` | Stock zero checking |
| Re-zero | `query_cs1237_zero oid=%c` → `query_cs1237_zero_read oid=%c data=%*s` | Mutates the stock zero state; forbidden for calibration capture |
| Set recurring poll interval | `query_cs1237 oid=%c rest_ticks=%u` | Nonzero only in recovered homing start; zero in homing clear |
| Arm homing trigger | `cs1237_setup_home oid=%c clock=%u threshold=%u trsync_oid=%c trigger_reason=%c error_reason=%c filter=%u` | Threshold, watchdog, `trsync`, and stepper-stop semantics; forbidden for calibration capture |
| Query homing state | `query_cs1237_home_state oid=%c` → `cs1237_home_state oid=%c homing=%c trigger_clock=%u` | Homing cleanup |

`query_cs1237_begin` and `query_cs1237` are different commands. `query_cs1237_begin` establishes ADC configuration and returns `config`; it is not the recurring host report command. `query_cs1237` carries `rest_ticks`, where nonzero starts the recovered recurring homing poll and zero stops it.

`CS1237._handle_ready()` and G-code `CS_WEIGHT_BEGIN` use `query_cs1237_begin`, not `query_cs1237`. Their non-homing ADC configuration does not prove a bulk-message stream.

## QIDI homing acquisition lifecycle

The recovered QIDI start path is:

```text
HomingMove.homing_move() or probing_xy_move()
  -> WeighEndstopWrapper.home_start(..., rest_time, ...)
  -> CS1237.setup_home(print_time, trsync_oid, hit_reason,
                       error_reason, rest_time)
  -> query_cs1237([oid, rest_ticks])
  -> cs1237_setup_home([oid, clock, threshold, trsync_oid,
                        hit_reason, error_reason, cs_fil_f])
```

`CS1237.setup_home()` is at `cs1237.so` ELF address `0x19ee0`. It computes:

```python
clock = mcu.print_time_to_clock(print_time)
rest_ticks = mcu.print_time_to_clock(print_time + rest_time) - clock
```

QIDI `HomingMove._calc_endstop_rate()` computes `rest_time = move_t / max_steps`. `rest_ticks` is therefore an MCU-clock polling interval scaled to approximately one check per maximum participating step during the homing move. It is not a host sleep, sample count, microsecond value, settling delay, or ADC rate register.

The recovered stop path is:

```text
WeighEndstopWrapper.home_wait(home_end_time)
  -> TriggerDispatch.wait_end(home_end_time)
  -> CS1237.clear_home()
  -> cs1237_setup_home([oid, 0, 0, 0, 0, 0, 0])
  -> query_cs1237_home_state([oid])
  -> query_cs1237([oid, 0])
  -> TriggerDispatch.stop()
```

`CS1237.clear_home()` is at `cs1237.so` ELF address `0xbf20`. Its explicit zero `rest_ticks` stop means an out-of-lifecycle nonzero `query_cs1237` call can leave firmware polling state active if cleanup fails.

The compiled setup-home protocol has a seventh `filter=%u` field sourced from `cs_fil_f`. Older decompiled QIDI-derived Python shows six fields. The decompiled signature must not be replayed against this Max 4 firmware.

Calibration acquisition must not call `CS1237.setup_home()`, `cs1237_setup_home`, `TriggerDispatch`, or `trsync`. Those calls own probe thresholds, homing trigger reasons, watchdog behavior, and potential stepper-stop semantics.

## Toolhead MCU dictionary and direct-read implementation

The decompressed `QD_MAX4_THR.bin` dictionary reports:

| ID | Command |
|---:|---|
| 80 | `query_cs1237_config_r oid=%c` |
| 81 | `query_cs1237_begin oid=%c config=%u` |
| 82 | `query_cs1237_zero_read_only oid=%c` |
| 83 | `query_cs1237_zero oid=%c` |
| 84 | `cs1237_setup_home ... filter=%u` |
| 85 | `query_cs1237 oid=%c rest_ticks=%u` |
| 86 | `query_cs1237_home_state oid=%c` |
| 87 | `query_cs1237_read oid=%c reg=%u read_len=%u` |
| 88 | `config_cs1237 oid=%c dout_pin=%u sclk_pin=%c` |

The response dictionary includes `query_cs1237_data oid=%c data=%*s` as response `-8`. It does **not** include a `cs1237_data` response, even though the compiled host constructs `BulkDataQueue(mcu, "cs1237_data", oid)`.

The absence of `cs1237_data` from the matching MCU dictionary explains why passive `query_cs1237` experiments produced no messages consumable by the host bulk queue. A Klipper serial response absent from the MCU dictionary cannot be decoded by the matching host message parser.

The firmware routine at `0x0800bd28`, identified from the command table as `command_query_cs1237_read`, validates the OID/type, loads CS1237 object state at offsets `0x74` and `0x78`, and emits the direct-read response through the response descriptor near `0x0800f4a4`. It appears to return cached sensor state rather than initiating a complete host-visible bulk stream.

A nearby routine at `0x0800bd74` packages a 32-bit value from SRAM `0x20000174` as four bytes through response metadata near `0x0800f494`. The exact association of this helper with zero/configuration operations remains unresolved; the four-byte little-endian packaging is consistent with live direct and zero-read payloads.

The raw firmware is Cortex-M Thumb code with an apparent image base near `0x08003000`. Disassembly labels around mixed code/data boundaries are provisional unless corroborated by the command table.

## Rejected passive bulk path

Live `query_cs1237 oid=5 rest_ticks=<nonzero>` experiments used the stock `BulkDataQueue` and produced zero `cs1237_data` messages. Tests included a nominal one-sample period and a `50 us` polling interval.

Calling `query_cs1237` without `cs1237_setup_home` did not expose a passive stream. Calling `CS1237.setup_home()` merely to force data would have armed the exact probe/endstop state that calibration must avoid.

`query_cs1237_end_cmd` is the host's synchronous query wrapper around `query_cs1237_read`; it is not a continuous-stream stop command. The recurring stream stop command is `query_cs1237([oid, 0])` and belongs to the homing lifecycle.

No toolhead firmware patch is required for the validated direct-read route. Adding a new MCU bulk response would require flashing constrained toolhead firmware and is unnecessary unless direct reads fail under stationary trapq load.

## Validated idle scheduled direct-response path

The developer adapter uses the existing `probe_air.sensor_helper` and its MCU/OID. It does not create a second CS1237 MCU object, claim the sensor pins, replace QIDI's driver, alter probe zero, alter threshold state, or reconfigure the ADC.

Capture preflight performs these checks before scheduling reads:

1. `print_stats.state` is exactly `standby`, `complete`, `error`, or `cancelled`; absent, malformed, active, and unknown states fail closed.
2. `probe_air.sensor_helper` exposes `query_cs1237_home_state_cmd`, `query_cs1237_config_read_cmd`, `mcu`, and `oid`.
3. The MCU exposes the required lookup, clock conversion, response registration, and serial-handler interfaces.
4. Read-only `query_cs1237_home_state` reports `homing=0`; an active stock homing/probing owner fails with `SENSOR_BUSY`.
5. Read-only `query_cs1237_config_r` reports exact configuration `60`.
6. No response handler already owns `('query_cs1237_data', oid)`.
7. No in-process calibration token already owns the same sensor object.

The capture sequence is:

```python
read_command = mcu.lookup_command(
    "query_cs1237_read oid=%c reg=%u read_len=%u"
)

mcu.register_response(callback, "query_cs1237_data", oid)
for index in range(round(duration * rate)):
    request_time = print_start + index / rate
    request_clock = mcu.print_time_to_clock(request_time)
    read_command.send(
        [oid, 0, 0],
        minclock=request_clock,
        reqclock=request_clock,
    )
reactor.pause(event_start + duration + 0.600)
mcu.register_response(None, "query_cs1237_data", oid)
```

`print_start` is scheduled `0.100 s` after `mcu.estimated_print_time(event_start)`. Registration and ownership release run through `finally` cleanup. The developer command bounds duration to five seconds and rate to 1000 Hz; the supported default is 500 Hz.

A plain `lookup_command()` is deliberate. `lookup_query_command().send()` serializes request/reply handling. The capture path instead registers one temporary OID-scoped callback and queues bounded requests asynchronously.

The direct response has no request sequence field. The MCU command handler emits one response for each processed `query_cs1237_read`, and Klipper's serial transport preserves command ordering and prevents retransmitted protocol blocks from being executed as new commands. The adapter therefore accounts by ordered accepted-response cardinality, rejects underflow and overflow, caps retained accepted and rejected responses at the planned request count, and records malformed responses separately. Any changed firmware that violates this one-command/one-response contract fails the capture rather than being treated as a complete batch.

Both `minclock` and `reqclock` are required. A 100 Hz experiment using only `reqclock` sent requests in bursts, returned `40/100` responses, and showed a `0.739 s` gap. In Klipper's serial queue, `reqclock` is a requested deadline; it is not a not-before time. Equal future `minclock` and `reqclock` produced the intended pacing.

## Idle host-response cadence measurements

Each retained run was one second with no heating, motion, extrusion, homing, probe trigger, PA change, or per-response console logging. These measurements validate host request/response delivery only; firmware disassembly indicates the direct command reads cached object state, so repeated values may represent the same ADC conversion.

| Requested rate | Requested | Received | Receive span | Median interval | P95 interval | Maximum interval | Median round trip |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 Hz | 100 | 100 | `0.989942 s` | `10.114 ms` | `10.627 ms` | `12.251 ms` | `0.520 ms` |
| 250 Hz | 250 | 250 | `0.996361 s` | `4.054 ms` | `4.392 ms` | `7.314 ms` | `0.478 ms` |
| 500 Hz | 500 | 500 | `0.997434 s` | `2.038 ms` | `3.053 ms` | `4.111 ms` | `0.468 ms` |
| 800 Hz | 800 | 800 | `0.999480 s` | `0.957 ms` | `2.458 ms` | `3.957 ms` | `0.563 ms` |
| 1000 Hz | 1000 | 994 | `0.998915 s` | `0.899 ms` | `2.426 ms` | `7.038 ms` | `0.579 ms` |

The idle 500 Hz path returned full host-response coverage with lower command pressure and less duplicate-response behavior than 800 Hz, so it was selected as the starting rate for trapq validation. Under-load testing later found incomplete 500 Hz runs; it is not a validated production default. The 1000 Hz path failed idle full-response-coverage requirements.

The CS1237 remains configured for 1280 SPS while host capture requests 500 reads per second. The ADC configuration rate and host requested-read rate are separate values and are both recorded in diagnostic artifacts. A `500/500` response count does not prove 500 distinct conversions or bound the age of the cached conversion returned in each response.

## Invalid direct reads

Idle captures were centered near `-296350` counts with median absolute deviation near tens of counts. Every tested rate also contained a small number of large excursions. Examples include values near `-3`, `-5`, `-17`, `-262145`, and `-2196737`.

For evidence summarization only, an idle outlier was defined as `abs(count - trace_median) > 5000`. This is not a production classifier:

| Requested rate | Idle-summary outliers | Consecutive duplicate samples |
|---:|---:|---:|
| 100 Hz | 1 / 100 | 2 |
| 250 Hz | 4 / 250 | 7 |
| 500 Hz | 5 / 500 | 25 |
| 800 Hz | 11 / 800 | 124 |
| 1000 Hz | 14 / 994 | 222 |

The repeated near-zero and bit-pattern-like values indicate invalid or transient reads, but firmware evidence has not established a status bit that distinguishes them from valid force data. A median-distance filter derived from idle data cannot be used during extrusion because a real force transition may be large.

Production analysis must classify invalid reads using a bounded invariant that remains valid during force transitions, such as impossible slew relative to adjacent samples, repeated firmware error patterns, conversion-ready timing, or corroborated local continuity. It must also bound conversion freshness and the error between ADC conversion time, scheduled request time, and host receive time. If either classification or conversion-time alignment is ambiguous, the complete measured cycle must fail closed. Silent interpolation that can reshape rise/fall timing is not acceptable.

The fourth payload byte was zero for all retained responses and did not identify invalid excursions.

## `read_origin_data` alternative

The Max 4 `cs1237.so` contains a `read_origin_data` method at ELF address `0xb070`. Its exact live cadence, reactor cost, zero-state interaction, and returned freshness have not been benchmarked on this Max 4.

The Reddit implementation described below reports polling `sensor_helper.read_origin_data` at approximately 40 Hz on Q2/Q2C without creating another MCU object or touching sensor pins. That route may be useful as a low-rate compatibility fallback or validation cross-check, but 40 Hz provides only one sample every `25 ms` and may not resolve the PA transition timing required by this change.

The developer adapter defaults to the idle-validated 500 Hz scheduled direct-read path and fails accounting when responses are missing. Production calibration must select a repeat-validated under-load rate no greater than 500 Hz and must not silently fall back to `read_origin_data` without separate timing and force-response evidence.

## Installed pressure-advance and trapq contract

Read-only comparison on the controlled printer established that the live files exactly match the extracted host package:

| Installed file | SHA-256 |
|---|---|
| `klippy/kinematics/extruder.py` | `cb61a97829ef29ff7848f6e6c0a96cf974d8a57e5a6f517f8f314610a1e08494` |
| `klippy/toolhead.py` | `077b0832047989b17267689155198444f2820c43c5f08372ca87ea351cb7473b` |
| `klippy/chelper/__init__.py` | `b875718a4655bdd256f0e2fad59e31f12ac0f9b5f06654814e0f032b97cba7f3` |
| `klippy/chelper/trapq.c` | `156e73502d2ce86a384d145e78946df215b542efb32274b9940d3625faca8f2f` |
| `klippy/chelper/kin_extruder.c` | `4a352a7a287b782a47d813e94b85e33bfd662cdd8877f8507e2f60d847cd9538` |
| `klippy/chelper/c_helper.so` | `214d1ab79a78b2aa28ba8cd7ba0d7383afcaaf9d36561112aa7bf73cf6591714` |

No motion or heater target change was used to obtain these hashes. The printer was `standby`; the extruder target and power were zero during inspection.

The installed CFFI declaration for `trapq_append` is:

```text
trapq_append(trapq, print_time,
             accel_t, cruise_t, decel_t,
             start_pos_x, start_pos_y, start_pos_z,
             axes_r_x, axes_r_y, axes_r_z,
             start_v, cruise_v, accel)
```

`PrinterExtruder.move()` maps nominal E to `start_pos_x`, E direction to `axes_r_x = 1`, and the PA eligibility flag to `axes_r_y`; `axes_r_z` remains zero. `kin_extruder.c:pa_move_integrate()` tests `m->axes_r.y != 0` and applies `pressure_advance * nominal_velocity` only for eligible segments. A direct stationary extrusion segment therefore uses start position `(E, 0, 0)` and axes ratio `(1, 1, 0)`. The toolhead XY/Z trapq receives no corresponding move, so this contract generates extruder steps without X, Y, or Z steps.

Normal QIDI `PrinterExtruder.move()` sets `can_pressure_advance` only when extrusion is positive and the associated toolhead move has nonzero X or Y distance. Normal E-only G-code and `toolhead.manual_move()` do not exercise the PA transform.

Normal nominal-E bookkeeping has two coupled values: `PrinterExtruder.last_position` is updated to `move.end_pos[3]`, while `ToolHead.commanded_pos[3]` is updated when the G-code move enters lookahead. A direct adapter must update both consistently for its owned range and rebase the extruder trapq before restoring the original nominal G-code E coordinate. Merely changing either Python field would create a discontinuity in the next ordinary extrusion move.

Pressure-advance smoothing integrates across preceding and following trapq entries over `smooth_time / 2`. Every planned transition therefore requires continuous PA-eligible low-flow lead-in and lead-out context of at least that half-window. A single immediate accel/cruise/decel append without surrounding context is not a valid measured pulse.

Direct `trapq_append` only adds C queue entries. Normal `ToolHead._process_moves()` also calls `note_mcu_movequeue_activity()` and `_advance_move_time()` so step generation and MCU flushing cover the appended interval. The direct adapter must reproduce that timing ownership after flushing ordinary lookahead; appending a move without advancing toolhead generation time is incomplete.

`trapq_finalize_moves()` expires completed queue entries into history and later frees old history. Calling it with `NEVER_TIME` flushes entries from the queue but is not a selective cancellation primitive and does not recall steps already generated or compressed for an MCU. Cancellation must stop future pulse queueing and drain the currently bounded segment; unsafe interruption requires Klipper shutdown semantics rather than pretending queued extrusion was removed. Physical cancellation behavior remains a controlled-printer validation item.

`installer/klipper/extras/tltg_pa_calibration.py` contains pure `TrapqMove` and `StationaryPulsePlan` abstractions. Each planned pulse has continuous PA-eligible low-flow lead-in, low/high/low transition, and lead-out segments; unit tests verify temporal and nominal-E continuity. Physical queue scheduling remains disabled until queue timing, rebasing, ordinary queue exclusion, cancellation, and generation lifecycle are implemented and physically validated.

`QidiDirectTrapqAdapter` compares the installed `extruder.py`, `toolhead.py`, `chelper/__init__.py`, `trapq.c`, `kin_extruder.c`, and `c_helper.so` files with the exact hashes above. Live preflight exposed that QIDI stores `pressure_advance`, `pressure_advance_smooth_time`, and `_set_pressure_advance()` on `PrinterExtruder.extruder_stepper`, not directly on `PrinterExtruder`. The adapter now validates that owner and requires its physical stepper trapq to be the active `PrinterExtruder` trapq. It also rejects a non-default QIDI `toolhead.e_enable`, a nonzero `toolhead.e_accumulator`, disagreement between `toolhead.commanded_pos[3]` and `extruder.last_position`, unsupported heater/extruder limits, and smoothing context shorter than half the active smooth time.

A temporary bounded hardware command queued one PA-eligible direct trapq pulse at a time with `0.5 mm/s` low flow, `2.0` or `3.0 mm/s` high flow, `10` or `20 mm/s²` acceleration, and K values `0`, `0.032`, and `0.08`. Every artifact recorded identical start/end XYZ `(135, 403, 200)`, restored PA `0.032` and smooth time `0.03`, and no command failure. A following ordinary relative E move advanced nominal E by exactly `1 mm`, and a final full `G28` completed before the bed returned to `Z=200` and the toolhead returned to the trash chute.

The production resource registry contains entries for `0.2`, `0.4`, `0.6`, and `0.8 mm` nozzles, but each entry has `hardware_validated=False` and no pulse values. The `0.4 mm` exploration proves feasibility, not safe production bounds or candidate repeatability. Preflight therefore continues to return `NOZZLE_PLAN_UNVALIDATED`.

Under-load direct responses showed measurable low/high/low force changes, but response completeness and cycle shape were not stable enough for candidate selection. Three of nine `500 Hz` captures missed one to three responses and maximum send intervals reached `15.096 ms`; the one `250 Hz` capture was complete. Near-zero and large isolated excursions remained present during force transitions. Conversion freshness, force-safe invalid classification, repeated-cycle timing, and candidate selection remain unresolved.

## Reddit post and comment evidence

Source: [Automatic Pressure Advance Calibration for the Qidi Q2 / Q2C/ Max4 — Bambu-style load-cell PA calibration on existing hardware](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/), posted `2026-07-25T09:52:40Z` by `Own_Moose_9495`. The old-Reddit HTML exposed all 47 comments; the user-provided screenshots corroborated the substantive threads.

The post reports a Q2/Q2C prototype that:

- waits for touchscreen filament loading to finish;
- runs an approximately three-minute sweep over the purge chute;
- measures nozzle pressure buildup with the bed-leveling load cell;
- reports clean PETG results without a separate flow calibration;
- reports repeated PLA calibration values within approximately six percent;
- skips calibration during mid-print filament runout and resumes the print;
- was still adding nozzle-size compensation;
- warns that load, heat, and motion behavior are modified and should be observed during early use;
- identifies Max 4 support as in progress and Plus 4 as a different-sensor platform.

Those print-quality and six-percent repeatability statements are anecdotal external evidence. No raw traces, printed-reference tolerance, source code, or Max 4 validation dataset was published in the page at inspection time. They do not satisfy tasks 5.2, 5.3, or 5.4.

The author's technical comment [t1_oznwy4x](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/oznwy4x/) states:

- compiled Cython objects remain normal live Python objects inside Klippy;
- another extra can obtain the existing probe through `printer.lookup_object()` and inspect regular attributes;
- `sensor_helper.read_origin_data` was polled at approximately 40 Hz;
- this avoided a second MCU object, pin conflicts, and vendor-driver replacement;
- `strings` exposed CS1237 commands resembling Klipper HX71x commands;
- direct 640/1280 Hz MCU querying was considered possible for a later version.

The first five points align with the independent Max 4 host-binary and runtime-object analysis. The final native-rate claim is only partially supported: the firmware exposes direct and `rest_ticks` commands, but the matching dictionary lacks `cs1237_data`, and live 1000 Hz direct reads lost six responses. This change uses bounded scheduled direct reads and rejects incomplete captures rather than assuming native-rate bulk behavior.

The comment thread also identifies validation areas already represented in `tasks.md`:

- [different nozzle sizes, filament types, and speed limits](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/ozo3449/);
- [QIDI Box and mid-print reload uncertainty](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/ozq1j90/);
- [QIDI Box multicolor testing pending](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/ozpuveo/);
- [a single-spool implementation preceding QIDI Box work](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/ozpwumk/);
- [a reported filament-runout test pass](https://old.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/ozqcr5r/).

The Reddit prototype saves and applies PA by filament type and blocks slicer PA overrides. Those behaviors are deliberately out of scope here. This change reports `PA_VALUE=<decimal> TEMP=<celsius> NOZZLE=<mm> PERSISTED=0`, restores the prior active PA, and does not modify slicers, saved variables, or filament-source state.

No Reddit source code was available at inspection time. No implementation was copied from the post or comments.

## External provenance and licensing

- `53Aries/Q2-Klipper` commit `076ac9789e72c219fd8b93cf439c69670b8cdea2`, GPL-3.0, supplied decompiled QIDI CS1237 lifecycle context. The analyzed Max 4 binary has a seven-field setup-home protocol that differs from the older decompiled source.
- `Klipper3d/klipper` commit `7046bd00ef5c30dec6febc724f8d22967433c45c`, GPL-3.0, supplied trapq and PA transform behavior.
- `CNCKitchen/PrusaPATuner`, AGPL-3.0, demonstrates the general load-cell back-pressure calibration concept. Analysis and orchestration code in this change is independently implemented; no AGPL code is copied.
- The Reddit post and comments are treated as public claims and design-risk prompts, not implementation source.
- QIDI binaries are analyzed for interoperability and are identified by hash rather than redistributed.

## Installer and runtime state

The extra source is `installer/klipper/extras/tltg_pa_calibration.py`. `installer/package.yaml` pins SHA-256 `b6af2f05a1f635a5cc71398e6e9456197b0c1cbd63dad1d0c0b27b278d93e191` for package `26.07.26.3`.

Package `26.07.26.3` was installed on the controlled Max 4 from the validated development bundle. The state ledger and live extra reported the expected package version and module hash after a verified Klipper service-process restart. Klipper reported `ready`, the heater target was zero, the temporary hardware-spike module/config were absent, `developer_capture` was false, and `TLTG_PA_CALIBRATE TEMP=215 NOZZLE=0.4` returned `PA_CALIBRATION_UNVALIDATED` without physical effects.

The managed destination supports collision detection, drift rejection, atomic replacement, rollback, state-ledger recording, upgrade, uninstall, and optional preimage restoration. Recovery-sentinel validation checks expected existence, SHA-256, and mode for rollback-tracked files outside `config/`.

A Moonraker `/printer/restart` reloads configuration but does not reload an already imported Python extra after an upgrade. A process-level Klipper service restart loaded the changed module during validation. New or changed managed Python files share the guarded activation marker introduced by the QIDI homing source patch: the marker is written before deployment, binds each destination to its expected SHA-256 or expected absence, and is removed only after Moonraker reports `ready` under a new Klipper process ID. Unchanged Python files retain the ordinary `/printer/restart` path. Restore reconstructs the PA extra's presence from archived installed state and requires an exact matching bundle payload.

## Validation record

The direct-read implementation passed:

- `python3 -m unittest installer.tests.unit.test_tltg_pa_calibration -v` — 52 tests;
- `python3 scripts/run_installer_core_tests.py` — 242 tests;
- `python3 scripts/format_klipper_configs.py` — no formatting changes;
- `python3 scripts/check_installer_known_versions.py` — compatibility metadata valid;
- `openspec validate add-load-cell-pa-calibration --strict` — valid;
- `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test` — bundle smoke test passed;
- `git diff --check` — clean.

Earlier merged validation also covered optimized slicer macros and G-code path checks. PA calibration is an operator-invoked path and does not change start-print branch invariants.

## Scenario implementation audit

| Scenario | Implemented evidence | Remaining gap |
|---|---|---|
| Valid invocation starts calibration setup | Macro forwarding and input parsing exist. | Public execution is disabled; no physical backend is wired. |
| Sweep details remain internal | K builders, plans, grouping, and analysis expose no sweep parameters in the macro. | Production sweep values require hardware traces. |
| Active print is rejected | Preflight requires safe `print_stats` and `idle_timeout` states. | Controlled-printer race testing remains. |
| Loaded filament is detected while event handling is disabled | `inspect_loaded_filament()` reads `filament_detected` independently from `enabled`; unit coverage uses `enabled=false`. | None for the read-only contract. |
| Toolhead filament is absent | Missing, malformed, and false switch states fail before adapter construction. | None for the read-only contract. |
| QIDI Box loaded state is corroborated | Controller, Box E-endstop, synchronized slot, and `IN_EXTRUDER` checks have unit coverage. | Repeat on hardware with each loaded-source path. |
| Loaded-state sources disagree | Busy, unsynchronized, invalid-slot, and E-endstop disagreement fail closed. | Repeat on hardware during Box state transitions. |
| Missing or unsupported temperature is rejected | `validate_inputs()` and heater-bound validation reject missing and out-of-range values. | Confirm target limits on each supported firmware baseline. |
| All Max 4 nozzle sizes are accepted | Input parsing accepts exactly `0.2`, `0.4`, `0.6`, and `0.8`. | Each production resource plan remains unvalidated. |
| Missing or unsupported nozzle diameter is rejected | Unit tests cover missing and unsupported diameters without fallback lookup. | None. |
| Valid preflight performs full homing and lowers the bed | State-machine order is `home_all`, absolute `Z=200`, then trash parking before heating; the controlled `0.4 mm` run completed that physical sequence. | Production backend and abort behavior remain unvalidated. |
| Concurrent calibration or probe ownership is rejected | In-process ownership, response-handler ownership, and read-only stock `homing` state are checked. | Probe/calibration race testing remains. |
| Supported stock sensor contract is accepted | Structural, OID, command, handler, configuration `60`, scheduling, and payload checks have unit and idle-printer evidence. | Conversion freshness under trapq work remains. |
| Changed private interface is rejected | Missing attributes, malformed configuration, handler collision, and runtime mismatches fail closed. | Repeat against every supported firmware artifact. |
| Homing trigger acquisition is rejected for calibration | Calibration code contains no `setup_home`, `cs1237_setup_home`, `TriggerDispatch`, or `trsync` acquisition path. | None. |
| Stock probing remains available after calibration | Full `G28` succeeded after direct-trapq pulses and final cleanup without modifying probe zero, threshold, or homing objects. | Post-capture Z tilt and mesh validation remain. |
| Measured pulses have no XY motion | Controlled direct pulses extruded `1.275` or `2.0375 mm` while start/end XYZ remained `(135, 403, 200)`. | Repeat with production backend and every nozzle plan. |
| Injected moves exercise the real PA transform | Live moves used `axes_r_y=1` and K values `0`, `0.032`, and `0.08`; the installed PA owner was exercised and restored. | Force responses were not repeatable enough to validate candidate interpretation. |
| Private trapq contract is incompatible | Live preflight exposed the `extruder_stepper` PA owner; the adapter now validates that owner and active stepper trapq in addition to six runtime hashes. | Repeat against every supported firmware artifact. |
| Bypassed move limits are enforced explicitly | Resource-plan and extruder velocity, acceleration, distance, temperature, smoothing, and positive-value checks exist; bounded controlled pulses and a following ordinary E move completed. | Production queue ownership and cancellation remain. |
| Resource bounds are enforced | Pulse/group distance, duration, count, and capture-window arithmetic fail closed. | Production per-nozzle bounds remain `hardware_validated=False`. |
| Intermediate flap clearing follows a pulse group | Controlled `0.4 mm` pulses ran in groups of one or two followed by `CLEAR_FLUSH` and return to `(135, 403, 200)`. | Sensor settling time and production orchestration remain unvalidated. |
| No cleanup precedes the first pulse | State-machine order and the controlled run contain no clear operation before the first measured pulse. | Production backend remains unwired. |
| Final cleanup follows calibration extrusion | Controlled cleanup completed `CLEAR_OOZE`, `CLEAR_FLUSH`, heater shutdown, post-capture `G28`, `Z=200`, and trash parking. | Failure and cancellation cleanup remain unvalidated. |
| Cleanup commands are unavailable | Preflight requires `OPTIMIZED_MOVE_TO_TRASH`, `CLEAR_OOZE`, and `CLEAR_FLUSH`. | None for command registration. |
| QIDI Box filament is loaded | Box 2 slot 0/global `slot4` remained loaded, synchronized, and detected through controlled PLA extrusion and cleanup. | Repeat with other physical slots and transition states. |
| External-spool filament is loaded | Box-disabled and synchronized `slot16` paths classify as `external`. | Controlled external-spool extrusion remains. |
| Capture covers the measured motion window | Controlled captures bracketed scheduled direct-trapq transitions and retained send/receive timing. | Cached-conversion age and print-time conversion bounds remain unknown. |
| Conversion freshness is inconclusive | Under-load captures are retained without promoting response receive time to conversion time, and public reporting remains disabled. | Hardware invariant for cached conversion age remains unknown. |
| Raw processing respects host limits | Six of nine `500 Hz` under-load runs were complete; one `250 Hz` run returned `363/363`. | Select and repeat-validate a rate with acceptable timing and reactor load. |
| Invalid direct-read excursions are detected | Near-zero and isolated large excursions were retained during force transitions. | A force-safe raw-count classifier remains unknown. |
| Timing uncertainty exceeds tolerance | Coverage, gap, and timing-residual gates return stable inconclusive reasons. | Thresholds require conversion-time evidence. |
| Valid response produces an interior candidate | Deterministic fixtures require corroboration and reject edge candidates. | Hardware-selected candidates remain disabled. |
| No detectable force response is inconclusive | Signal-amplitude and noise gates return `WEAK_OR_NOISY_SIGNAL`. | Threshold tuning requires traces. |
| Dropout or saturation is inconclusive | Coverage, gap, and saturation fixtures fail closed. | Hardware tolerances remain. |
| Candidate reaches a search boundary | Selection returns `K_RANGE_NOT_BRACKETED`. | Production K range remains. |
| Repeated cycles disagree | Corroboration, repeatability, and monotonicity gates have fixtures. | Production tolerances remain. |
| Value is visible to the user | `execute_and_report()` formats the required success response only after cleanup. | Public execution remains disabled. |
| Value is not retained by Klipper | Controlled pulses restored PA `0.032` and smooth time `0.03`; a following ordinary relative E move advanced exactly `1 mm`. | Production backend restoration remains unwired. |
| Persistent stores remain unchanged | Runtime interfaces contain no persistence or slicer-writing operation; macro only forwards inputs. | Recheck after the physical backend is implemented. |
| Inconclusive result has no value token | Failure formatting sanitizes reasons and tests reject `PA_VALUE` injection. | Public execution remains disabled. |
| Successful cleanup restores temporary state | Controlled pulses restored PA/smooth time and ordinary E continuity; final motion cleanup completed. | Concrete production backend, G-code modes, motion limits, and failure paths remain. |
| Mid-sweep cancellation restores state | Injected pulse/capture failures run finalize and restore once. | Real Klipper cancellation and already-generated-step behavior remain. |
| Analysis failure restores state | Injected analysis failure restores before emitting failure. | Concrete backend remains. |
| Klipper shutdown interrupts calibration | Motion-unsafe cleanup omits clear motion and records manual cleanup. | Shutdown event wiring remains. |
| Fresh installation deploys the capability | Installer external-file and managed-tree integration tests pass. | No current deployment is requested. |
| Python extra upgrade reloads module code | Packages `26.07.26.2` and `26.07.26.3` installed and loaded through verified Klipper service-process restarts. | Auto-update activation remains to be observed. |
| Destination collision fails safely | Installer collision and drift tests pass before writes. | None. |
| Failed installation rolls back the extra | Integration tests cover external-file rollback and recovery state. | None. |
| Uninstall removes only the managed extra | Exact-hash removal and preimage restoration tests pass. | None. |
| Synthetic response fixtures cover selection behavior | Deterministic trace and selection fixtures cover the specified classes. | None. |
| Failure injection covers cleanup | Unit tests inject setup, capture, pulse, clear, settle, analysis, and cleanup failures. | Concrete backend boundaries and printer cancellation remain. |
| Controlled printer comparison validates candidate reporting | No candidate is enabled without this evidence. | All printed comparisons remain. |
| Operator-visible behavior is specified | The delta spec and `design.md` define command inputs, positioning, clearing, source preservation, output, and non-persistence. | Final measured limits remain unresolved. |
| Current optimized behavior is represented | `openspec/specs/optimized-printer-behavior/spec.md` records the staged fail-closed interface and implementation paths. | Candidate reporting remains disabled. |
| Installer lifecycle is represented | `openspec/specs/installer-lifecycle/spec.md` records external-file deployment, drift, rollback, restore, activation, and uninstall behavior. | None. |

## Required next evidence

The following findings remain unresolved after the controlled `0.4 mm` PLA run:

1. A bound on cached-conversion age and conversion-time error relative to scheduled requests and Klipper print time.
2. A raw/timing invariant that rejects invalid reads during real force transitions without suppressing valid signal edges.
3. A repeat-validated under-load request rate; `500 Hz` missed responses in three of nine runs and `250 Hz` has one complete run.
4. Production direct-trapq ownership, queue drain, and real cancellation/shutdown behavior; bounded generation and ordinary E continuity passed.
5. Normal E-only and `CLEAR_FLUSH` force traces; idle, heated-idle, and stationary PA-eligible low/high/low traces are retained.
6. Force polarity, baseline drift, sensor settling, flap contamination, signal-to-noise limits, and repeatable K-response classification.
7. Safe flow schedules, extrusion caps, pulse duration, and PA search bounds for `0.2`, `0.4`, `0.6`, and `0.8 mm` nozzles.
8. External-spool operation and additional Box slots; Box-fed global `slot4` remained unchanged in the controlled run.
9. Failure and cancellation cleanup paths; successful full homing, `Z=200`, trash parking, intermediate clearing, final clearing, heater shutdown, and post-capture homing passed.
10. Repeatability across materials and temperatures and agreement tolerances against conventional printed PA calibration.
11. Auto-update process activation; normal installer service-process restart was verified.

Candidate reporting must remain disabled until these items satisfy the scenarios in `specs/load-cell-pa-calibration/spec.md`.
