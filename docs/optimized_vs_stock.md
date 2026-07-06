# Current optimized behavior vs stock QIDI Max 4 configs

Stock baseline:
- `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults`

Optimized runtime source paths:
- `installer/package.yaml`
- `installer/klipper/tltg-optimized-macros/*.cfg`
- `orcaslicer_gcode/*.gcode`
- `qidistudio_gcode/*.gcode`

Excluded comparisons:
- comment-only translation in `config/`
- redacted hardware IDs in `config/MCU_ID.cfg` and `config/box.cfg`
- generated `SAVE_CONFIG` blocks in `config/printer.cfg`
- runtime state drift in `config/saved_variables.cfg` and `config/officiall_filas_list.cfg`

## Installer-applied runtime changes

Source paths:
- `installer/package.yaml`
- `installer/runtime/legacy_manual_install.py`
- `installer/stock/qidi-max4-defaults/firmwares/<firmware>/config/`
- `config/printer.cfg`
- `config/klipper-macros-qd/kinematics.cfg`
- `installer/klipper/tltg-optimized-macros/*.cfg`

Functional changes:
- Before a fresh install, the installer detects legacy manually-copied optimized configs in stock QIDI paths, backs up `config/`, restores bundled QIDI stock-managed files from `installer/stock/qidi-max4-defaults/firmwares/<detected-firmware>/config/`, preserves `config/MCU_ID.cfg`, `config/box.cfg`, `config/fluidd.cfg`, `config/saved_variables.cfg`, and direct `config/KAMP` symlinks, restarts `qidi-client.service`, then continues with the normal installer path.
- During install, the installer writes missing or empty `value_tN = 'slotN'` entries required by `box_count` into `config/saved_variables.cfg`, so QIDI Box startup maps tools to physical slots before vendor `BOX_PRINT_START` runs; non-empty mismatches still require confirmation before rewrite.
- The installer adds `[include tltg-optimized-macros/*.cfg]` after `[include klipper-macros-qd/*.cfg]`, so stock QIDI macros remain present and optimized macros override or wrap behavior through a separate include tree.
- Guarded installer patches make X and Y homing faster, reduce repeated homing retraction distance, reduce Z homing retraction distance, increase Z-tilt travel speed, and increase bed-mesh point-to-point travel speed.
- Guarded installer patches route virtual-SD print errors to `OPTIMIZED_CANCEL_PRINT_ON_ERROR`, which cancels without parking or moving the toolhead during error handling and calls `_KM_CANCEL_PRINT_BASE` after heater shutdown, fan shutdown, pause-state restore, `G31`, and `CLEAR_PAUSE`.
- Guarded installer patches set stock `TIMELAPSE_TAKE_FRAME` `variable_verbose` to `False`, so disabled printer timelapse ignores slicer frame requests without per-layer console messages.
- The installer deletes QIDI's stock `[homing_override]` from `config/klipper-macros-qd/kinematics.cfg` only when the stock section hash matches a supported firmware baseline, then runtime `G28` is handled by `installer/klipper/tltg-optimized-macros/kinematics.cfg`.
- On firmware `01.01.06.04`, guarded stock-baseline patches preserve QIDI's closed-loop X/Y `query_cycle:10` and `trigger_*` fields, `Chamber_Thermal_Protection_Sensor max_temp:170`, and official `[fila25]` `PA6-CF` naming; firmware `01.01.06.03` does not require `.04`-only `trigger_*` fields.
- Uninstall restores the stored stock `[homing_override]` section when the optimized section deletion is still intact.

## System hardening and OS optimizations

Source paths:
- `installer/package.yaml`
- `installer/runtime/system_optimizations.py`
- `installer/system/qidiclient-static-gifs.tar.gz`
- `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py`

Functional changes:
- Install patches `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py` so `.gcode.3mf` metadata uses `Metadata/slice_info.config` `<metadata key="index" value="N"/>` for `Metadata/plate_N.gcode`, `Metadata/plate_N.json`, and `.thumbs/<job>/plate_N.png`; missing or invalid plate-index metadata falls back to plate 1.
- Interactive install can apply OS-level hardening in addition to Klipper config changes; `--skip-system-optimizations` leaves DNS, APT, service, qidiclient GIF, and AI-service changes disabled.
- DNS resolution uses DHCP-provided `resolvconf` output first by linking `/etc/resolv.conf` to `/run/resolvconf/resolv.conf`, clearing the static resolver head, and adding `1.1.1.1` then `8.8.8.8` as fallback resolvers.
- APT sources are moved from stock China mirrors to Debian Bullseye `deb.debian.org` and `security.debian.org` entries without running `apt update` or package upgrades.
- The unused `xl2tpd` VPN service and Bluetooth service are disabled when present; missing units are recorded as missing and skipped.
- `qidiclient` animated spinner GIFs are replaced from the bundled static GIF archive, reducing touchscreen CPU load from repeated GIF decoding; replaced files are backed up under `/home/qidi/QIDI_Client/access/.gif-backup-<timestamp>` and keep their existing owner and mode.
- AI detection remains enabled unless the operator chooses to disable the backend service; when kept enabled, `algo_app.service` state is recorded without changing the unit, and when disabled, `algo_app.service` is stopped and disabled while touchscreen `Settings -> Printing Options -> Spaghetti Detection` and `Foreign Object Detection` toggles remain `qidiclient` UI state.
- Auto-update reapplies opted-in system hardening when QIDI firmware updates or manual changes revert DNS, APT, service, or qidiclient GIF state.
- Auto-update service runs reconcile QIDI Box tool-slot mappings on boot and hourly checks when Klipper is reachable and idle; if `box_count` changed since the last observed count, it writes missing or empty `value_tN = 'slotN'` entries required by the new box count.
- Uninstall asks whether to restore system settings changed by the optimized installer; declining removes only Klipper config changes.

## CommunityWiki system-hardening mapping

Source references:
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/process_hardening_optimiztion.md#dns-resolution`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/process_hardening_optimiztion.md#apt-sources`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/process_hardening_optimiztion.md#vpn-client`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/process_hardening_optimiztion.md#bluetooth`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/process_hardening_optimiztion.md#algo-app`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/mods/making_qidiclient_suck_less.md`
- `https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/files/qidiclient-static-gifs.tar.gz`

Operator mapping:
- CommunityWiki DNS hardening maps to `installer/package.yaml system_optimizations.dns` and writes `/etc/resolv.conf`, `/etc/resolvconf/resolv.conf.d/head`, and `/etc/resolvconf/resolv.conf.d/tail` through `installer/runtime/system_optimizations.py`.
- CommunityWiki APT source hardening maps to `installer/package.yaml system_optimizations.apt_sources` and writes `/etc/apt/sources.list` without running `apt update` or `apt upgrade`.
- CommunityWiki VPN and Bluetooth hardening maps to `installer/package.yaml system_optimizations.services.disable` entries `xl2tpd` and `bluetooth`; missing units are recorded as missing and do not fail the Klipper config install.
- CommunityWiki AI service hardening maps to `installer/package.yaml system_optimizations.services.optional_disable` entry `algo_app.service`; the installer disables it only when the operator accepts the AI detection prompt or passes `--disable-ai-detection`.
- CommunityWiki qidiclient GIF hardening maps to bundled asset `installer/system/qidiclient-static-gifs.tar.gz`; the installer validates the SHA-256 from `installer/package.yaml system_optimizations.qidiclient_static_gifs.sha256` before replacing files under `/home/qidi/QIDI_Client/access`.

## Homing, probing, and mesh behavior

Source paths:
- `installer/klipper/tltg-optimized-macros/kinematics.cfg`
- `installer/klipper/tltg-optimized-macros/bed_mesh.cfg`
- `installer/klipper/tltg-optimized-macros/filament.cfg`
- `installer/klipper/tltg-optimized-macros/globals.cfg`

Functional changes:
- `G28` supports full-home, XY-home, X-only, Y-only, and Z-only requests instead of collapsing most requests into the stock full-home path.
- `G28 O ...` is honored as lazy homing, so helpers such as `OPTIMIZED_MOVE_TO_TRASH` can skip X/Y homing when those axes are already homed.
- X/Y homing uses shorter stepper settle waits, shorter post-endstop backoff moves, faster stock-patched homing speeds, and no longer performs the stock pre-homing relative X/Y nudge.
- Z-only homing is used when X and Y are already homed; a full home is used only when Z is requested before XY is known.
- Z homing moves to a randomized point around the bed center using `variable_z_home_randomize_enable` and `variable_z_home_randomize_radius`, avoiding repeated probing of the same bed spot.
- Homing restores the configured printer acceleration after temporary high-acceleration moves; stock homing can leave acceleration changed after the override.
- `OPTIMIZED_G29_ZSAFE` and print-start mesh preparation skip redundant XY rehoming when X and Y are already homed.
- Bed mesh always clears the active mesh and recalibrates `PROFILE=kamp`; optimized print start does not load the saved `default` mesh through `_km_globals.bedmesh_before_print`.
- Z-tilt and bed-mesh measurement moves run faster through installer-patched `[z_tilt] speed` and `[bed_mesh] speed` values.
- The post-mesh save wait is shortened before `SAVE_CONFIG_QD`.

## Start-print behavior

Source paths:
- `orcaslicer_gcode/start.gcode`
- `qidistudio_gcode/start.gcode`
- `installer/klipper/tltg-optimized-macros/start_end.cfg`
- `installer/klipper/tltg-optimized-macros/filament.cfg`
- `installer/klipper/tltg-optimized-macros/bed_mesh.cfg`
- `installer/klipper/tltg-optimized-macros/offset.cfg`

Functional changes:
- Slicer start G-code enters optimized startup through `OPTIMIZED_PRINT_START_HOME` and `OPTIMIZED_START_PRINT_FILAMENT_PREP` instead of the stock `print_start` path.
- `OPTIMIZED_PRINT_START_HOME` cancels any pending `_optimized_end_fan_cooldown_off`, preheats the hotend to probing temperature, sets the UI sub-status, and runs optimized `G28`.
- `OPTIMIZED_START_PRINT_FILAMENT_PREP` owns the retained-filament, QIDI Box fresh-load, and no-box external-spool branches.
- `M1002 R1` captures `printer.save_variables.variables.z_offset|default(0)` into `_km_apply_print_offset.captured_z_offset`, clears the active runtime Z offset for homing, Z tilt, and KAMP mesh calibration, and leaves the captured value in volatile macro state.
- `M1002 A1` applies `_km_apply_print_offset.captured_z_offset` after `SAVE_CONFIG_QD`; if no value was captured earlier in the session, it falls back to `printer.save_variables.variables.z_offset|default(0)`.
- `_km_apply_print_offset` reports `Your Z Offset will be set to: x.xxx` immediately before `M1002 A1` applies the captured or fallback Z offset.
- The retained-filament branch skips `BOX_PRINT_START` when the requested tool maps to the retained slot, `slot_sync` still points at that slot, the slot filament ID and vendor ID still match, and the filament-present sensor proves box filament is still loaded.
- The retained-filament branch reuses the loaded filament, moves to the chute before waiting, waits for bed/chamber targets as needed at the chute, performs chute-side cleanup, runs `Z_TILT_ADJUST`, and recalibrates KAMP mesh.
- The QIDI Box fresh-load branch calls vendor `BOX_PRINT_START` with the slicer high purge temperature, runs optimized extrusion and flush, cools to scrape temperature in stages, wipes/scrapes the nozzle, then goes directly to bed/chamber waits, Z tilt, and KAMP mesh.
- The QIDI Box fresh-load branch does not re-home Z between purge cleanup and the rear-bed scrape motion.
- The no-box external-spool branch skips `BOX_PRINT_START`, skips `OPTIMIZED_EXTRUSION_AND_FLUSH`, skips rear extrusion purge, wipes/scrapes the nozzle without extrusion, then runs bed/chamber waits, Z tilt, and KAMP mesh.
- `OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE` heats only to the scrape target, waits for the hotend to be no hotter than the scrape window, runs chute cleanup only when `box_extras` exists, performs the rear-bed scrape pattern, and contains no `G1 E...` extrusion move.
- `OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE` no longer calls `_OPTIMIZED_HOME_Z_FROM_SAFE_POINT`; Z homing for mesh happens later through `_OPTIMIZED_G29_HOME_Z_OR_FULL`.
- Slicer start G-code selects `T[initial_tool]` before the front prime line so prime extrusion is attributed to the initial object filament.
- Slicer start G-code does not call `SET_INPUT_SHAPER`, so Klipper uses saved `shaper_type_x` / `shaper_type_y` calibration state from `config/printer.cfg` instead of forcing per-print algorithms.
- The front prime line uses the first-layer nozzle temperature and performs a short centered prime at the front of the bed.

## Filament cutting, flushing, unloading, and end-print behavior

Source paths:
- `installer/klipper/tltg-optimized-macros/filament.cfg`
- `installer/klipper/tltg-optimized-macros/motion.cfg`
- `orcaslicer_gcode/*.gcode`
- `qidistudio_gcode/*.gcode`

Functional changes:
- `OPTIMIZED_CUT_FILAMENT` removes the stock cutter tail dwell and exits the cutter sequence faster.
- `OPTIMIZED_CUT_FILAMENT` saves caller G-code state, forces absolute XY travel and relative extrusion internally, then restores acceleration and caller state before returning.
- `OPTIMIZED_EXTRUSION_AND_FLUSH` uses a shorter initial extrusion before the high-volume flush loops, keeps the cleanup wait shorter than stock, and uses `OPTIMIZED_M1004` for polar-cooler handling.
- `OPTIMIZED_END_PRINT_FILAMENT_PREP` can keep the active QIDI Box filament loaded between prints and records the retained/unloaded branch for end cleanup.
- `OPTIMIZED_END_PRINT_FILAMENT_PREP` records retained filament from QIDI Box `slot_sync` when it is set, then reverse-maps that active slot through `value_tN` so auto-runout reloads can be reused on the next print even when the slicer `current_extruder` is stale.
- `OPTIMIZED_END_PRINT_FILAMENT_PREP` performs its end retract under relative extrusion inside saved/restored G-code state, so caller extrusion mode does not change the retract semantics.
- `OPTIMIZED_UNLOAD_FILAMENT` clears retained-tool state before unloading, routes travel through `OPTIMIZED_MOVE_TO_TRASH`, keeps the post-unload extrusion relative, and restores caller motion state; end-print unload suppresses the standalone unload cleanup so cooldown-based wiping owns the final nozzle wipe.
- OrcaSlicer and QIDI Studio end G-code lift Z by 3 mm, call `OPTIMIZED_MOVE_TO_TRASH` immediately to leave the part, run `OPTIMIZED_END_PRINT_FILAMENT_PREP`, start nozzle cooldown, then lower the bed with the same slicer height rule.
- OrcaSlicer passes `complete_print_exhaust_fan_speed[current_extruder]` to the exhaust cooldown when completion air filtration is enabled; QIDI Studio passes `EXHAUST_SPEED=0` because QIDI Studio rejects indexed filament placeholders such as `activate_air_filtration_on_completion[current_extruder]` in end-G-code conditionals.
- `OPTIMIZED_END_NOZZLE_COOLDOWN_START` starts the part fan at full speed, records the hotend cooldown reference temperature, disables the QIDI Box heater when present, turns off chamber, bed, hotend, and sensors, leaves the polar cooler in its current state until `PRINT_END`, and starts `OPTIMIZED_END_FAN_COOLDOWN` when a nonzero `EXHAUST_SPEED` is passed.
- `OPTIMIZED_END_STAGED_NOZZLE_WIPE` waits for the hotend to cool 40 °C below the captured end temperature, runs `CLEAR_OOZE` / `CLEAR_FLUSH` when `box_extras` is present, waits for 140 °C, repeats the wipe, moves Y forward 30 mm, and then calls `PRINT_END`.
- OrcaSlicer and QIDI Studio change-filament G-code call `OPTIMIZED_CUT_FILAMENT` and `OPTIMIZED_MOVE_TO_TRASH`.

## Motion helpers and cancellation

Source paths:
- `installer/klipper/tltg-optimized-macros/motion.cfg`
- `installer/klipper/tltg-optimized-macros/cancel.cfg`
- `installer/klipper/tltg-optimized-macros/cooling.cfg`

Functional changes:
- `OPTIMIZED_MOVE_TO_TRASH` lazily homes X/Y, parks at the chute, runs faster final chute-approach moves, and restores caller G-code state and acceleration.
- Layer-change G-code uses `OPTIMIZED_MOVE_TO_TRASH` for parking behavior in both slicer packs.
- `OPTIMIZED_CANCEL_PRINT_ON_ERROR` shuts down heaters and fans, disables the QIDI Box heater when present, clears pause state, and runs `G31` so bed mesh is enabled for the next print start.
- `OPTIMIZED_CANCEL_PRINT_ON_ERROR` avoids the stock cancel parking move, reducing crash risk during virtual-SD error handling.

## Heater, chamber, fan, and QIDI Box helpers

Source paths:
- `installer/klipper/tltg-optimized-macros/heaters.cfg`
- `installer/klipper/tltg-optimized-macros/cooling.cfg`

Functional changes:
- `OPTIMIZED_WAIT_HOTEND`, `OPTIMIZED_WAIT_BED`, and `OPTIMIZED_WAIT_CHAMBER` preserve explicit UI sub-status values while waiting.
- `OPTIMIZED_WAIT_CHAMBER` stops `chamber_circulation_fan` before waiting on chamber heat and continues when chamber temperature is within 3 °C of the requested target.
- `OPTIMIZED_M1004` defaults the polar cooler to off when `enable_polar_cooler` is unset.
- `OPTIMIZED_DISABLE_BOX_HEATER` calls vendor `DISABLE_BOX_HEATER` only when `box_extras` exists.
- `TLTG_SET_BOX_TEMP` validates the requested QIDI Box heater and max temperature before calling `SET_HEATER_TEMPERATURE`.
- `OPTIMIZED_END_FAN_COOLDOWN` runs the `P3` chamber/exhaust fan for the slicer-requested completion cooldown and skips the delayed shutdown when another print is active or paused.

## Calibration and user helpers

Source paths:
- `installer/klipper/tltg-optimized-macros/helpers.cfg`

Functional changes:
- `helpers.cfg` enables Klipper `SCREWS_TILT_CALCULATE` using the stock bed screw positions from `config/printer.cfg`.
- `TLTG_PROBE_ACCURACY_CENTER` homes, moves to bed center, and runs `PROBE_ACCURACY` with an overrideable sample count.
- `TLTG_CORNER_BED_SCREW_CHECK` homes, runs `Z_TILT_ADJUST`, and runs `SCREWS_TILT_CALCULATE`.

## Startup visibility

Source paths:
- `installer/klipper/tltg-optimized-macros/globals.cfg`
- `installer/package.yaml`

Functional changes:
- `_tltg_optimized_startup_banner` emits `TLTG Optimized Configs Installed v<package_version>` to the Klipper console after startup.
- `variable_package_version` in `installer/klipper/tltg-optimized-macros/globals.cfg` tracks `installer/package.yaml package.version`.

## Stock behavior retained

Source paths:
- `config/klipper-macros-qd/*.cfg`
- `config/box.cfg`
- `config/fluidd.cfg`

Retained behavior:
- Stock-named QIDI macros remain installed under `config/klipper-macros-qd/` for compatibility with Fluidd, QIDI Client, and vendor internals.
- QIDI firmware `01.01.06.03` stock helpers remain under `config/klipper-macros-qd/globals.cfg` as `SMART_STATUS` and under `config/klipper-macros-qd/qd_macro.cfg` as `prepare_filament_dry` and `restore_factory_settings`.
- `config/box.cfg` remains the active QIDI Box vendor stack unless a future replacement explicitly changes that include path.
- `config/fluidd.cfg` remains unmodified by the optimized installer.
