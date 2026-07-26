# QIDI Box hardware and protocol observations

Evidence qualifiers are defined in `openspec/observations/README.md`. These observations apply to QIDI Max 4 firmware `01.01.06.04`. Hardware identifiers are redacted; stock pin names and protocol fields are retained.

## Slot hardware

| Slot | Runout | Step | Direction | Enable | White LED | Red LED |
|---:|---|---|---|---|---|---|
| `0` | `mcu_box1:PA0` | `mcu_box1:PC14` | `mcu_box1:PC13` | `!mcu_box1:PC15` | `mcu_box1:PA1` | `mcu_box1:PA2` |
| `1` | `mcu_box1:PB3` | `mcu_box1:PB9` | `mcu_box1:PB8` | `!mcu_box1:PC0` | `mcu_box1:PB4` | `mcu_box1:PB5` |
| `2` | `mcu_box1:PA13` | `mcu_box1:PC12` | `mcu_box1:PC11` | `!mcu_box1:PD2` | `mcu_box1:PA14` | `mcu_box1:PA15` |
| `3` | `mcu_box1:PA7` | `mcu_box1:PC8` | `mcu_box1:PB2` | `!mcu_box1:PC10` | `mcu_box1:PC4` | `mcu_box1:PC5` |

Shared stepper config:

| Key | Value |
|---|---:|
| `rotation_distance` | `13.6` |
| `microsteps` | `16` |
| `step_pulse_duration` | `0.000000100` |

`box_extras` pins:

| Key | Value |
|---|---|
| `b_button_pin` | `^mcu_box1:PB1` |
| `b_endstop_pin` | `mcu_box1:PA9` |
| `e_endstop_pin` | `mcu_box1:PA10` |

## Heater, sensors, fans, and RFID pins

| Component | Stock value |
|---|---|
| Box heater pin | `mcu_box1:PA3` |
| Heater sensor | `AHT20_F`, `i2c3`, address `56`, MCU `mcu_box1` |
| PID | `63.418 / 1.342 / 749.125` |
| Heater range | min `-100`, max `100`, target max `90` |
| Verify heater | max error `400`, gain time `600`, `is_box_heater=True` |
| NTC sensor A/B | `NTC 100K MGB18-104F39050L32`, pins `PC1` / `PC2`, range `-100..130` |
| Heater fan A/B | pins `PA4` / `PA5`, threshold `35`, idle timeout `60` |
| Controller fan | pin `PA6`, heater `heater_box1`, steppers `slot0..slot3` |
| RFID chip selects | `PC6`, `PC7` |

## Compiled stepper constants

| Constant | Value |
|---|---:|
| `DISABLE_DELAY` | `0.05` |
| `HOMING_START_DELAY` | `0.001` |
| `ENDSTOP_SAMPLE_COUNT` | `4` |
| `ENDSTOP_SAMPLE_TIME` | `0.000015` |

## Harness-recovered primary motion

| Operation | Distance | Speed | Acceleration | Notes |
|---|---:|---:|---:|---|
| slot preload home | `3000` | `80` | `50` | endstop flag `False` |
| slot preload park | `-260` | `80` | `50` | after preload home |
| slot unload/eject | `-3000` | `100` | `50` | endstop flag `True` |
| extruder load home | `3000` | `85` | `50` | endstop flag `False` |
| post-load dwell | n/a | n/a | n/a | `0.05 s` |
| unload phase 1 | `-350` | `65` | `100` | double-step path |
| unload phase 2 | `-1150` | `85` | `100` | double-step path |
| unload recovery | `-1500`, twice | `65` | `50` | endstop flag `True` |
| hub sync defaults | `18` | `40` | `40` | `hub_load_length`, `hub_load_v`, `hub_load_a` |

Visible toolhead script fragments include unload pre-positioning at `Y380`, `X3`, `X3 Y17`, `M400`, and shake routines with explicit relative extrusion and high-speed X/Y wiping. Exact live branch execution is not proven by static fragments.

Harness branch observations:

- `slot_load()` and `EXTRUDER_LOAD` ran their home branch when fake `b_endstop=1` and skipped it when `b_endstop=0`; fake `e_endstop` did not change those observed outcomes.
- `EXTRUDER_UNLOAD` ran its captured unload branch when fake `b_endstop=0` and skipped it when `b_endstop=1`.
- `SLOT_UNLOAD` ran the same slot-runout home in all harnessed states.
- `SLOT_PROMPT_MOVE` produced no captured motion.
- Live loaded state reported `b_endstop=0`, `e_endstop=1`; fake predicates must not be promoted to live semantics without controlled motion evidence.

Harnessed `slot_sync()` saved the owning stepper's slot name regardless of its `value` argument. Non-extruder sync looked up the hub defaults above; no real synchronized movement was proven. `init_slot_sync()` overwrote fake prior sync state, but the harness lacked the full multi-stepper graph. `sync_unbind_extruder()`, `switch_next_slot()`, prompt movement, and auto-reload movement remain unresolved.

## Cleanup scripts

`CLEAR_FLUSH`:

```gcode
M204 S10000
G1 X180 F10000
MOVE_TO_TRASH
```

`CLEAR_OOZE`:

```gcode
M204 S10000
G1 X163 F8000
G1 X145 F5000
G1 X163 F8000
G1 X145 F5000
G1 X175 F6000
G1 X163
G1 X175
G1 X163
G1 X175
G1 X163
```

`flush_all_filament()`:

```gcode
G1 E25 F300
```

Recovered cutter strings include `CUT_FILAMENT_1`, `MOVE_TO_TRASH`, `M83`, and `G1 E-60 F300`. Exact cutter branching remains vendor-owned.

## Autofeed and anti-wrap protocol

Stock-visible `[box_autofeed]` values:

| Field | Module default | Stock value |
|---|---:|---:|
| `limit_pin` | required | `^!mcu_box1:PB0` |
| `debounce_us` | `200000.0` | `200000.0` |
| `limit_polarity` | `0` | `0` |
| `default_ticks` | `8400` | `8400` |
| `v_feed` | `2000` | `100` |
| `lmax` | `10000` | `120` |
| `dir` | `1` | `0` |
| `a_feed` | `0.0` | `0.0` |

These values configure feed assist, not compiled primary load/unload motion.

Registered commands:

```text
MCB_CONFIG MCB_QUERY SET_LIMIT_A MCB_AUTO_START MCB_AUTO_ABORT
```

MCU command formats:

```text
mcb_config oid=<oid>
mcb_config_stepper oid=%c stepper_oid=%c
mcb_query oid=%c clock=%u rest_ticks=%u retransmit_count=%c invert=%c
mcb_auto_start oid=%c v=%u a=%u lmax=%u dir=%i enable=%i invert=%i
mcb_auto_abort oid=%c
set_limit_a oid=%c state=%c
```

Response names are `MCB_STATE`, `MCB_DONE`, and `MCB_ERROR`.

Harness payload ordering:

```text
mcb_config_stepper: [oid, stepper_oid]
mcb_query: [oid, clock, rest_ticks, retransmit_count, invert]
mcb_auto_start: [oid, velocity_steps, acceleration_steps, max_length_steps, direction, enable_pin, invert]
mcb_auto_abort: [oid]
set_limit_a: [oid, state]
```

Velocity, acceleration, and max length are converted from visible millimeter units through step distance. The fake setup encoded `!PC15` as pin `47`, invert `1`.

Runtime fields included `limit_a_state`, `wrapping_num`, `bind_stepper`, `active_slot`, and selected stepper/MCU caches. `QDE_004_013` and a pause-command string associate this path with wrapped-filament handling; callback payloads, event-count gate, and physical anti-wrap behavior are unresolved.

## RFID protocol

`box_rfid.so` initializes each reader with:

```text
query_fm17550 oid=<oid> rest_ticks=0
config_fm17550 oid=<oid> spi_oid=<spi_oid>
```

The query is registered `on_restart=True`. Read command and response formats:

```text
fm17550_read_card_cb oid=%c
fm17550_read_card_return oid=%c status=%c data=%*s
```

Recovered fields:

| Field | Value |
|---|---:|
| `max_read_time` | `30.0 s` |
| initial `rfid_read_attempts` | `0` |
| initial `rfid_read_start_time` | `0` |
| `get_message_count` | `1` |
| initial `had_get_value` | `False` |

Idle commands were accepted without a visible result and did not clear stored material metadata. Raw valid-tag bytes, status interpretation, reader ordering, tag-to-metadata decoding, and the four-logical-reader/two-chip-select relationship remain unresolved.

A fake `SLOT_RFID_READ` path emitted `QDE_004_011` when loaded, but live loaded-slot commands were accepted without that error. Live evidence outranks the fake predicate.
