# QIDI Box state and command observations

Evidence qualifiers are defined in `openspec/observations/README.md`. Runtime captures apply to QIDI Max 4 firmware `01.01.06.04`; `01.01.06.05` findings are explicitly config-confirmed, harness-recovered, or static-recovered.

## Saved-variable model

| Variable family | Meaning |
|---|---|
| `enable_box`, `box_count` | Vendor Box enablement and detected count. |
| `value_t0` through `value_t15` | Logical tool-to-slot mapping. Missing mappings default to `slot16` in recovered `BOX_PRINT_START`. |
| `slot0` through `slot15` | Physical-slot `BoxState` values. |
| `last_load_slot` | Last loaded slot according to vendor state. |
| `slot_sync` | Slot currently trusted/bound to the extruder. |
| `extrude_state` | Aggregate loaded state. |
| `filament_slotN`, `color_slotN`, `vendor_slotN` | IDs resolved through `officiall_filas_list.cfg`; slot metadata extends through `slot16`. |
| `runout_0` through `runout_15` | Per-slot runout counters. |
| `load_retry_num`, `retry_step` | Vendor retry state. |
| `retained_slot`, `retained_tool`, `retained_tool_ready`, `retained_filament_id`, `retained_vendor_id` | Repository-managed between-print reuse state. |
| `is_tool_change`, `was_interrupted` | Vendor operation context. |

`slot16` is direct-feed/external-spool metadata and is not a physical slot state. `slot-1` is the observed unsynced/no-active-target sentinel.

## State values

| State | Value |
|---|---:|
| `ERROR` | `-1` |
| `UNKNOWN` | `-2` |
| `PENDING` | `-3` |
| `EMPTY` | `0` |
| `LOADED` | `1` |
| `IN_EXTRUDER` | `2` |
| `IN_FEEDER` | `3` |

Runtime alignment on the one-Box capture:

```text
last_load_slot = slot2
slot_sync = slot2
extrude_state = 2
slot2 = 2
```

`multi_color_controller.slots.states.slot2=2`, `extruder.loaded=true`, `extruder.filament_detected=true`, and `BoxState.IN_EXTRUDER=2` confirm the loaded-to-extruder interpretation.

Loaded `slot2` reported `runout_button=0`; empty slots reported `1`. Box slot runout/pre-gate polarity is inverted relative to an intuitive `filament present = 1` assumption.

## Moonraker status schema

Visible object names include:

```text
box_extras
multi_color_controller
save_variables
box_stepper slotN
heater_generic heater_boxN
temperature_sensor heater_temp_a_boxN
temperature_sensor heater_temp_b_boxN
mcu mcu_boxN
```

`box_autofeed` returned `{}` when queried but was absent from object lists. Generated `box_rfid card_reader_N` objects were not Moonraker-visible in captures.

### `box_extras`

```text
box_button_state
b_endstop_state
e_endstop_state
box_operate_state
box_drying_state.boxN.{dry_state,end_time}
is_tool_change
```

### `box_stepper slotN`

```text
runout_button
rfid_state
```

### `multi_color_controller`

| Section | Captured shape |
|---|---|
| `system` | `ready`, `mode` |
| `hardware` | `box_count`, `connected` |
| `slots` | `states.slot0..slot15`, `materials.slot0..slot16`, `last_loaded`; each material resolves `filament.{filament,min_temp,max_temp,box_min_temp,box_max_temp,type}`, `color`, and `vendor`. |
| `extruder` | `loaded`, hotend `target`, `filament_detected` |
| `operation` | `current`, `progress`, `error`, button/operate state, `steps`, `is_waiting_user` |
| `print` | `printing`, `current_tool`, `next_tool` |
| `rfid` | `reading`, `results` |
| `drying` | `boxN.dry_state`, `end_time` |
| `sensors` | `b_endstop`, `e_endstop`, `runout_sensors.slotN`, `pressure_sensor` |
| `config` | Saved Box, mapping, metadata, runout, retry, retained-filament, and interruption variables. |
| `config_summary` | `enable_box`, `auto_reload_detect`, `auto_read_rfid`, `auto_init_detect`, `slot_sync`, `retry_step`, `load_retry_num` |

`save_variables` should be consumed through its `variables` dictionary; duplicated top-level fields are a Moonraker representation detail.

`heater_generic heater_boxN.target` is authoritative for heater target. Captures showed `target=45` while both vendor drying-state surfaces reported `dry_state=0`.

The observed `mcu_box1` reported firmware `02.03.01.21`, MCU `stm32f401xc`, clock `84000000`, and both-edge stepping enabled.

## `BOX_PRINT_START`

Stock start dispatch is:

```gcode
BOX_PRINT_START EXTRUDER=<tool> HOTENDTEMP=<temperature>
```

Every harnessed call first:

1. emits `CLEAR_TOOLCHANGE_STATE`;
2. writes `load_retry_num=0`;
3. writes `retry_step=None`;
4. clears `runout_0` through `runout_15`;
5. writes `extrude_state=-1`;
6. reads `enable_box` and `value_t<tool>`, defaulting a missing mapping to `slot16`.

Harnessed branch families used `MOVE_TO_TRASH`, `M109`, `M400` where required, and downstream `EXTRUDER_LOAD` / `EXTRUDER_UNLOAD`. The fake harness emitted an unload from `slot-1` in one unsynced case; exact live predicates for same-slot, changed-slot, cut-before-unload, unsynced, and `slot16` handling remain unresolved.

When `enable_box=0`, recovered setup runs and the command returns without feeder motion. Repository code must still distinguish `enable_box` from object existence.

## Firmware `01.01.06.05` compiled-module differences

- **Harness-recovered:** `BoxExtras.detect_filament_loaded()` adds `M400` plus escalating `G1 E10 F600` and `G1 E20 F900` retries. Worst-case probing increases from `75 mm` on `.04` to `140 mm` on `.05`; physical sensor response and failure outcomes were not exercised.
- **Harness-recovered:** `BoxExtruderStepper.cmd_EXTRUDER_LOAD()` adds state paths named `diff_mv`, `box_autofeed`, `retry_success`, and `box_autofeed.limit_a_state`, with waste-area recovery extrusions of `30 mm` or `50 mm`. Exact retry thresholds and branch order remain unresolved.
- **Harness-recovered:** `BoxExtruderStepper.cmd_EXTRUDER_UNLOAD()` resolves `printer.lookup_object("virtual_sdcard", None)` into `v_sd` while printing. Downstream effects of the resolved object remain unresolved.
- **Harness-recovered:** `BoxExtruderStepper.switch_next_slot()` scans `value_t0` through `value_t15` rather than using `get_key_by_value(...)` for the next logical tool mapping. Physical auto-reload movement remains unresolved.
- **Harness-recovered:** `TaskQueueManager._decide_flow_id()` selects flow `6` (`EXT_HEAT`, `EXT_LOAD`, `EXT_BITE`, `EXT_WIPE`) for external-spool `LOAD` with slot `16`, filament present, and empty `last_load_slot`; `.04` selected flow `0` for the same controlled input.
- **Static-recovered:** public classes, methods, signatures, defaults, and recovered error-code sets are unchanged. `box_autofeed.so`, `box_detect.so`, and `box_rfid.so` have no confirmed source-level functional change beyond rebuilt binaries.

## Task queue

| Flow | Ordered steps |
|---:|---|
| `0` | none / idle |
| `1` | `BOX_HEAT`, `BOX_LOAD`, `BOX_WIPE` |
| `2` | `BOX_HEAT`, `BOX_CUT`, `BOX_UNLOAD`, `BOX_LOAD`, `BOX_WIPE` |
| `3` | `BOX_HEAT`, `BOX_CUT`, `BOX_UNLOAD` |
| `4` | `BOX_EJECT` |
| `5` | `BOX_HEAT`, `BOX_CUT`, `BOX_UNLOAD`, `BOX_EJECT` |
| `6` | `EXT_HEAT`, `EXT_LOAD`, `EXT_BITE`, `EXT_WIPE` |
| `7` | `EXT_HEAT`, `EXT_CUT`, `EXT_UNLOAD` |
| `8` | `EXT_HEAT`, `EXT_CUT`, `EXT_UNLOAD`, `WAIT_USER`, `BOX_HEAT`, `BOX_LOAD`, `BOX_WIPE` |
| `9` | `BOX_HEAT`, `BOX_CUT`, `BOX_UNLOAD`, `WAIT_USER`, `EXT_HEAT`, `EXT_LOAD`, `EXT_BITE`, `EXT_WIPE` |

Firmware `.04` fake-manager `_decide_flow_id()` results:

- uppercase `LOAD`: flow `1` without filament; flow `2` with filament when `last_load_slot != slot16`;
- uppercase `UNLOAD`: flow `3` with filament when `last_load_slot != slot16`; flow `7` when `last_load_slot=slot16`;
- uppercase `EJECT`: flow `4` in all harnessed states;
- lowercase and adapter-like action strings returned flow `0` in direct harnessing;
- `LOAD` with filament and `last_load_slot=slot16` raised `TypeError`; `last_load_slot=None` raised `AttributeError`.

`start_flow()` sets the flow ID, copies steps, initializes operation index `0`, and clears waiting/active-step state. Default-state `tick()` completed box-side flows `1`, `2`, `3`, and `5` but not extruder/mixed flows `6` through `9`; this is harness behavior, not live operation proof.

## Command risk boundary

### Runtime-confirmed query/idle-safe captures

```text
QUERY_MULTI_COLOR
QUERY_SAVE_VARIABLES
GET_MULTI_COLOR_STATUS
MCB_QUERY
SLOT_RFID_READ
INIT_RFID_READ
MULTI_COLOR_INIT_RFID
MULTI_COLOR_READ_RFID
```

The RFID commands were idle-safe only in captures with no valid visible tag result. Successful-tag behavior remains unresolved.

### State-changing or motion-adjacent

```text
MULTI_COLOR_SYNC
MULTI_COLOR_CLEAR_RUNOUT
SET_SAVE_VARIABLE
RESET_MULTI_COLOR_VARS
CLEAR_RUNOUT_NUM
MCB_CONFIG
SET_LIMIT_A
MCB_AUTO_ABORT
```

An already-matching sync or already-clear runout capture produced no delta; that does not prove no-op behavior for other state.

### Heater/dryer

```text
ENABLE_BOX_DRY DISABLE_BOX_DRY DISABLE_BOX_HEATER BOX_TEMP_SET
MULTI_COLOR_DRY MULTI_COLOR_DISABLE_HEATER MULTI_COLOR_SET_TEMP
TLTG_SET_BOX_TEMP
```

### Toolhead/extruder motion

```text
CLEAR_FLUSH CLEAR_OOZE CUT_FILAMENT MOVE_TO_TRASH
MULTI_COLOR_CLEAR_FLUSH MULTI_COLOR_CLEAR_OOZE MULTI_COLOR_CUT_FILAMENT
```

### Box motion or motion-capable

```text
EXTRUDER_LOAD EXTRUDER_UNLOAD SLOT_UNLOAD E_LOAD E_UNLOAD E_BOX
BOX_PRINT_START MULTI_COLOR_PRINT_START
MULTI_COLOR_LOAD MULTI_COLOR_UNLOAD MULTI_COLOR_SWAP MULTI_COLOR_BOX_UNLOAD
MCB_AUTO_START
```

### Retry/recovery runtime gaps

```text
TRY_MOVE_AGAIN TRY_RESUME_PRINT RESUME_PRINT_1 AUTO_RELOAD_FILAMENT
RELOAD_ALL TIGHTEN_FILAMENT
MULTI_COLOR_RELOAD_ALL MULTI_COLOR_AUTO_RELOAD MULTI_COLOR_RETRY
MULTI_COLOR_TIGHTEN MULTI_COLOR_TRY_RESUME MULTI_COLOR_RESUME_PRINT
```

Moonraker command help is incomplete: callable compiled commands such as `BOX_PRINT_START`, `EXTRUDER_LOAD`, `EXTRUDER_UNLOAD`, `SLOT_UNLOAD`, `SLOT_RFID_READ`, `CLEAR_FLUSH`, and `CLEAR_OOZE` were absent from the filtered help capture.

Additional registered controller surface:

```text
MULTI_COLOR_CONFIG MULTI_COLOR_SYNC MULTI_COLOR_READ_RFID
MULTI_COLOR_INIT_RFID MULTI_COLOR_INIT_MAPPING QUERY_MULTI_COLOR
QUERY_SAVE_VARIABLES MULTI_COLOR_CLEAR_RUNOUT
SET_SAVE_VARIABLE RESET_MULTI_COLOR_VARS
MCB_AUTO_ABORT MCB_AUTO_START MCB_CONFIG MCB_QUERY SET_LIMIT_A
```

## Runtime-confirmed idle effects

- `QUERY_MULTI_COLOR`, `QUERY_SAVE_VARIABLES`, and `GET_MULTI_COLOR_STATUS` preserved saved state and operation state.
- `MULTI_COLOR_SYNC SLOT=slot2` preserved state when already synced to slot 2.
- `MULTI_COLOR_CLEAR_RUNOUT` preserved state when counters were already zero.
- RFID commands returned `ok`, left slot metadata unchanged, ended with `rfid.reading=false` and `rfid.results={}`, and exposed no raw FM17550 payload.
- `MCB_CONFIG SLOT=slot2`, `MCB_QUERY`, `SET_LIMIT_A STATE=0/1/0`, and idle `MCB_AUTO_ABORT` returned `ok`, preserved saved variables, and exposed no `MCB_STATE`, `MCB_DONE`, or `MCB_ERROR` payload.

## Error surface

No live `QDE_004_*` failure was captured; owner and message strings are static-recovered.

| Code | Owner | Message |
|---|---|---|
| `QDE_004_001` | `box_stepper.so` | `Slot loading failure, please check the trigger, please reload %s.` |
| `QDE_004_002` | `box_stepper.so` | `Extruder has been loaded, cannot load %s.` |
| `QDE_004_003` | `box_stepper.so` | `Slot unloading failure, please unload %s again.` |
| `QDE_004_004` | `box_stepper.so` | `Please unload extruder first.` |
| `QDE_004_005` | `box_stepper.so` | `Please load the filament to %s first.` |
| `QDE_004_006` | `box_stepper.so` | `Extruder loading failure.` |
| `QDE_004_007` | `box_stepper.so` | `Extruder not loaded.` |
| `QDE_004_008` | `box_stepper.so` | `Extruder unloading failure.` |
| `QDE_004_009` | `box_stepper.so` | `Extruder unloading failure.` |
| `QDE_004_010` | `box_extras.so` | `The current feeding status is incorrect. Please exit the filament from the extruder.` |
| `QDE_004_011` | `box_stepper.so` | `Detected that filament have been loaded, please unload filament first` |
| `QDE_004_013` | `box_autofeed.so` | `Detected wrapping filament,please check the filament.` |
| `QDE_004_014` | `box_extras.so` | `Parameter setting error, please reset.` |
| `QDE_004_016` | `box_stepper.so` | `The filament has been exhausted, please load the filament to %s.` |
| `QDE_004_017` | `box_stepper.so` | `Filament flush failed, please clean and then load the filament in %s.` |
| `QDE_004_018` | `box_stepper.so` | `No filament specified, %s cannot be automatically replaced.` |
| `QDE_004_019` | `box_stepper.so` | `Please check if your PTFE Tube is bent` |
| `QDE_004_020` | `box_stepper.so` | `Detected that the filament has been unloaded, please reload.` |
| `QDE_004_021` | `box_extras.so` | `Unable to recognize loaded filament.` |
| `QDE_004_022` | `box_stepper.so` | `No replaceable slot found.` |
| `QDE_004_023` | `box_extras.so` | `Auto reload failed.` |
| `QDE_004_024` | `box_stepper.so` | `The filament failed to enter the extruder.` |
| `QDE_004_025` | `box_stepper.so` | `Extruder unloading failure.` |

`QDE_004_012` and `QDE_004_015` were absent from captured module strings. Exact live predicates for all codes remain unresolved.
