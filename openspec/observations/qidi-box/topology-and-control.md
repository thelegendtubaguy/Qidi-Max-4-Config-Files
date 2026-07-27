# QIDI Box topology and control observations

Evidence qualifiers are defined in `openspec/observations/README.md`. These observations apply to QIDI Max 4 firmware `01.01.06.04`.

## Active include graph

`config/printer.cfg` loads optimized macros before the vendor Box stack:

```ini
[include tltg-optimized-macros/*.cfg]
[include box.cfg]
[multi_color_controller]
```

`[include box.cfg]` creates Box MCU, slot, heater, sensor, fan, RFID, autofeed, `box_extras`, tool-wrapper, unload, and material-helper objects. `[multi_color_controller]` registers the public multi-color facade and publishes Moonraker state.

Vendor names are occupied across the final Klipper config even when their include appears after optimized macros. Repository macros must not redefine vendor object or command names.

## Vendor ownership

| Surface | Owner | Observed role |
|---|---|---|
| `EXTRUDER_LOAD`, `EXTRUDER_UNLOAD`, `SLOT_UNLOAD`, slot preload/sync and stepper timing | `box_stepper.so` | Primary feeder and toolhead-assisted filament motion; core distances, speeds, accelerations, and branch logic are compiled. |
| `BOX_PRINT_START`, cutter, cleanup, dryer, retry, auto-reload, runout, and resume | `box_extras.so` | High-level local orchestration and saved-variable writes. |
| FM17550 reads | `box_rfid.so` | SPI RFID scheduling, query, retry window, and raw response handling. |
| `MCB_*`, limit events, feed assist, anti-wrap | `box_autofeed.so` | Separate MCU assist path; visible values do not tune primary `box_stepper.so` load/unload motion. |
| `MULTI_COLOR_*`, state machine, local/remote adapter, Moonraker schema | `multi_color_controller.so` | Public command/state facade. |
| USB topology, Box config mutation, `box_count`, restart requests | `box_detect.so` and qidiclient | Detection and generated include/config management. |
| Pins, stock wrapper macros, visible helper values | `config/box.cfg` | Hardware/config surface; not primary motion ownership. |
| Material/color/vendor lookup | `config/officiall_filas_list.cfg`, saved variables, qidiclient | Metadata resolution. |

`box_stepper.so`, `box_extras.so`, `box_autofeed.so`, `box_rfid.so`, `box_detect.so`, and `multi_color_controller.so` are captured aarch64 Cython extension modules. Their static symbols and strings locate behavior; fake-harness and runtime evidence determine readable behavior.

## Generated object topology

Plain Python `box_config.py` expands `[box_config boxN]` using:

```text
base slot = N * 4
box display index = N + 1
RFID reader base = N * 2 + 1
```

Each Box generates:

- `box_stepper slot<N*4>` through `slot<N*4+3>`;
- `heater_generic heater_box<N+1>` and `verify_heater heater_box<N+1>`;
- `temperature_sensor heater_temp_a_box<N+1>` and `heater_temp_b_box<N+1>`;
- `box_heater_fan heater_fan_a_box<N+1>` and `heater_fan_b_box<N+1>`;
- `controller_fan board_fan_box<N+1>`;
- `box_rfid card_reader_<N*2+1>` and `card_reader_<N*2+2>`.

For `box0`, generated slots are `slot0` through `slot3`, heater/sensor/fan names end in `box1`, and RFID readers are `card_reader_1` and `card_reader_2`. For `box1`, generated slots are `slot4` through `slot7`, names end in `box2`, and readers are `card_reader_3` and `card_reader_4`.

A one-Box generated graph creates two reader objects while `multi_color_controller.LocalAdapter.connect()` was recovered looking up readers `1` through `4`. Four-slot-to-two-chip-select reader multiplexing is unresolved.

An older include inventory named `temperature_sensor box1_env`; generated-source and Moonraker captures identify `heater_temp_a_box1` and `heater_temp_b_box1` as the active generated NTC sensor names, with a separate `aht20_f heater_box1` object.

## Observed topologies

### 2026-05-07 one-Box runtime

- `mcu mcu_box1`, slots `0` through `3`, heater/sensors/fans for box 1, and one local controller were visible.
- `multi_color_controller.hardware.box_count=1`, `connected=true`, and `system.mode=local`.
- `slot2` was loaded and synced.

### 2026-06-13 two-Box runtime

- `mcu_box1` owned slots `0` through `3`; `mcu_box2` owned slots `4` through `7`.
- `heater_box1`, `heater_box2`, and corresponding temperature objects were visible.
- Controller and saved-variable `box_count` both reported `2`; `enable_box=1`.
- Material metadata existed for slots `4` through `7`, but `value_t4` through `value_t7` were absent after QIDI Client added the second Box.
- Harnessed `BOX_PRINT_START EXTRUDER=4` and `EXTRUDER=7` defaulted missing mappings to `slot16`; defining `value_t4='slot4'` or `value_t7='slot7'` dispatched to the physical slot.

The installer reconciles missing mappings; the absent second-Box mappings are vendor behavior, not an alternate desired mapping.

## Local controller path

The observed controller mode is `local`. `LocalAdapter` maps public operations to stock G-code:

| Operation | Local dispatch |
|---|---|
| load / unload | `E_LOAD SLOT=N` / `E_UNLOAD SLOT=N` |
| box eject | `E_BOX SLOT=N` |
| swap | `E_UNLOAD`, then `E_LOAD` |
| print start | `BOX_PRINT_START EXTRUDER=N HOTENDTEMP=T` |
| RFID | `SLOT_RFID_READ SLOT=slotN`, `INIT_RFID_READ` |
| sync / unsync | saves `slot_sync='slotN'` / `slot_sync='slot16'` |
| cleanup | `CLEAR_FLUSH`, `CLEAR_OOZE`, `CUT_FILAMENT` |
| heater/dryer | `BOX_TEMP_SET`, `ENABLE_BOX_DRY`, `DISABLE_BOX_DRY`, `DISABLE_BOX_HEATER` |

Stock `T0` through `T15` and `UNLOAD_T0` through `UNLOAD_T15` resolve `value_tN` and dispatch load/unload only when `enable_box=1`.

Stock `UNLOAD_FILAMENT` performs cutter, chute travel, mapped unload, `G1 E25 F300`, heater-off, ooze cleanup, and flush cleanup.

## Remote controller path

`RemoteAdapter` is recovered but was not active on the observed machine. It uses newline-delimited JSON over USB serial, defaults to `115200` baud, searches `/dev/ttyACM*` and `/dev/ttyUSB*`, and probes with:

```json
{"cmd":"ping"}
```

Each command includes `cmd`, generated `timestamp`, generated `id`, and used a `5.0 s` harness timeout. `slotN` values normalize to integers.

Remote command fields:

| Command | Additional fields |
|---|---|
| `load_filament`, `unload_filament` | `slot`, `options` |
| `swap_filament` | `from_slot`, `to_slot`, `options` |
| `box_unload`, `read_rfid`, `sync_to_extruder` | `slot` |
| `unsync_from_extruder`, `init_rfid`, `auto_reload`, `try_resume`, `init_mapping`, `disable_heater`, `clear_runout`, `clear_flush`, `clear_ooze` | none |
| `start_drying` | `box`, `temp`, `hours` |
| `stop_drying` | `box` |
| `reload_all` | `first` |
| `retry` | `rfid` |
| `tighten`, `cut_filament` | `tool` |
| `print_start` | `extruder`, `hotendtemp` |
| `resume_print` | `temp` |
| `set_temp` | `temp_params` containing Box/target parameters |

## Detection and config mutation

Static-recovered `box_detect.so` symbols and strings indicate monitoring under `/dev/serial/by-id/` for QIDI Box Klipper MCUs and markers `QIDI_BOX_V1`, `QIDI_BOX_V2`, and `mcu_box_to_v2`. It references:

```text
/home/qidi/printer_data/config/box.cfg
/home/qidi/printer_data/config/box1.cfg
/home/qidi/printer_data/config/box2.cfg
/home/qidi/printer_data/config/saved_variables.cfg
```

Recovered functions and strings indicate Box config/include updates, `box_count` persistence through `SAVE_VARIABLE`, and Klipper firmware-restart requests. The include matcher is `\[include box(\d+)\.cfg\]`. Qidiclient also contains Box config templates, USB matching, and Box firmware-update strings. Live detection mutation was not captured.

Treat concurrent vendor detection mutation as possible. Installer changes involving Box config or saved mappings require idle-state and current-topology validation.

## Repository-controlled integration boundary

- Optimized fresh-load start delegates feeder movement to `BOX_PRINT_START`, then performs repository-owned purge/cleanup.
- Retained-filament start bypasses vendor start only after physical continuity and metadata checks.
- External-spool start avoids Box commands when the stack is absent or disabled.
- Repository macros may wrap vendor behavior but do not tune compiled feeder motion, replace RFID/autofeed/retry logic, or redefine vendor names.
- `config/box.cfg` remains stock-mapped and sensitive; exact MCU serial identifiers are never tracked.
