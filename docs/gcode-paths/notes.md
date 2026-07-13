# G-code path notes

Generated path maps live under `docs/gcode-paths/generated/` and are produced from `docs/gcode-paths/*.path.json` plus parsed `.gcode` / `.cfg` files with:

```bash
python3 scripts/check_gcode_paths.py --write
```

Path contract checks run with:

```bash
python3 scripts/check_gcode_paths.py
```

`enable_box` is read from `printer.save_variables.variables.enable_box`; it is not the same signal as `printer["box_extras"] is defined`.

`BOX_PRINT_START`, `CLEAR_OOZE`, `CLEAR_FLUSH`, and `EXTRUDER_LOAD` are QIDI/vendor commands outside the visible optimized macro tree.

`OPTIMIZED_PRINT_START_HOME` cancels `_optimized_end_fan_cooldown_off` before homing so a previous print's delayed `P3` fan shutdown cannot fire during the next optimized start path.

`OPTIMIZED_START_PRINT_FILAMENT_PREP` owns the branch split between retained-filament reuse, QIDI Box fresh-load, and no-box external-spool startup.

`OPTIMIZED_END_PRINT_FILAMENT_PREP` records retained QIDI Box filament from `slot_sync` when a synced slot is present, reverse-maps that slot through `value_tN`, and `OPTIMIZED_START_PRINT_FILAMENT_PREP` requires the next requested tool slot to match that retained slot before bypassing `BOX_PRINT_START`.

`M1002 R1` captures `printer.save_variables.variables.z_offset|default(0)` into volatile macro state, reports the captured value, then clears the active runtime Z offset; `M1002 A1` reapplies that captured value after KAMP mesh `SAVE_CONFIG_QD` and falls back to `printer.save_variables.variables.z_offset|default(0)` only when no value was captured earlier in the session.

Slicer start G-code passes `FIRSTLAYERTEMP=[nozzle_temperature_initial_layer]` and `PURGETEMP={nozzle_temperature_range_high[initial_tool]}` to `OPTIMIZED_START_PRINT_FILAMENT_PREP`; `T[initial_tool]` runs before the front prime line so startup extrusion is attributed to the selected initial object filament; the front prime line moves to `X{first_layer_print_min[0]+45} Y{first_layer_print_min[1]-10}` at `Z5` when `first_layer_print_min[1] - 10 >= print_bed_min[1]` and `first_layer_print_min[0]+45 <= print_bed_max[0]`, otherwise falls back to `X218 Y0`; after `M109 S[nozzle_temperature_initial_layer]`, it runs `G1 E6 F300`, `G1 X{first_layer_print_min[0]+5} E20 F1200`, and `G1 X{first_layer_print_min[0]} E0.8` on the adaptive path, or `G1 E6 F300`, `G1 X178 E20 F1200`, and `G1 X173 E0.8` on the fallback path, with no post-prime retract before `G1 Z1 F1200`.

Slicer start G-code does not call `SET_INPUT_SHAPER`; Klipper uses the saved `shaper_type_x` and `shaper_type_y` calibration state from `config/printer.cfg`.

`docs/gcode-paths/start-print.path.json` records branch-level invariants only; exact command order comes from `orcaslicer_gcode/start.gcode`, `qidistudio_gcode/start.gcode`, and `installer/klipper/tltg-optimized-macros/*.cfg`.
