from __future__ import annotations

import unittest

from installer.tests.helpers import REPO_ROOT
from installer.tests.integration.test_macro_call_graph import extract_gcode_lines, iter_sections, parse_macro_definitions


OPTIMIZED_MACRO_ROOT = REPO_ROOT / "installer" / "klipper" / "tltg-optimized-macros"


class OptimizedMacroContractTests(unittest.TestCase):
    def setUp(self):
        files = tuple(sorted(OPTIMIZED_MACRO_ROOT.glob("*.cfg")))
        self.macros, duplicates = parse_macro_definitions(files)
        if duplicates:
            self.fail("Optimized macro duplicate definitions are invalid for this test.")

    def test_user_helper_macros_are_available(self):
        helpers = (OPTIMIZED_MACRO_ROOT / "helpers.cfg").read_text(encoding="utf-8")
        self.assertIn("[screws_tilt_adjust]", helpers)
        self.assertIn("screw_thread: CW-M4", helpers)

        probe_gcode = self._macro_gcode("TLTG_PROBE_ACCURACY_CENTER")
        self.assertIn("G1 X195 Y195 F24000", probe_gcode)
        self.assertIn("PROBE_ACCURACY SAMPLES={samples}", probe_gcode)
        self.assertNotIn("params.X", probe_gcode)
        self.assertNotIn("params.Y", probe_gcode)
        self.assertNotIn("params.Z", probe_gcode)
        self.assertNotIn("params.BED", probe_gcode)

        screws_gcode = self._macro_gcode("TLTG_CORNER_BED_SCREW_CHECK")
        self.assertIn("Z_TILT_ADJUST", screws_gcode)
        self.assertIn("SCREWS_TILT_CALCULATE", screws_gcode)
        self.assertNotIn("params.", screws_gcode)

    def test_cancel_on_error_reenables_bed_mesh_without_moving(self):
        gcode = self._macro_gcode("OPTIMIZED_CANCEL_PRINT_ON_ERROR")
        self.assertIn("G31", gcode)
        self.assertLess(gcode.index("G31"), gcode.index("CLEAR_PAUSE"))

    def test_firmware_05_polar_cooler_pause_resume_boundary(self):
        optimized_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(OPTIMIZED_MACRO_ROOT.glob("*.cfg"))
        )
        self.assertNotIn("[smart_output_pin polar_cooler]", optimized_text)
        self.assertNotIn("[smart_output_pin beeper]", optimized_text)

        resume = self._macro_gcode("RESUME")
        for forbidden in (
            "M106 P4",
            "SET_PIN PIN=polar_cooler",
            "ENABLE_SMART_PIN PIN=polar_cooler",
        ):
            self.assertNotIn(forbidden, resume)

        stock_root = (
            REPO_ROOT
            / "installer/stock/qidi-max4-defaults/firmwares/01.01.06.05/config"
        )
        pause_resume_path = stock_root / "klipper-macros-qd/pause_resume_cancel.cfg"
        pause = self._section_gcode(pause_resume_path, "gcode_macro PAUSE")
        self.assertNotIn("M106 P4", pause)
        self.assertNotIn("SET_PIN PIN=polar_cooler", pause)
        self.assertNotIn("ENABLE_SMART_PIN PIN=polar_cooler", pause)

        self.assertIn("M106 P4 S{polar_cooler}", self._macro_gcode("OPTIMIZED_M1004"))
        self.assertIn("M106 P4 S0", self._macro_gcode("OPTIMIZED_CANCEL_PRINT_ON_ERROR"))
        stock_start_end = stock_root / "klipper-macros-qd/start_end.cfg"
        self.assertIn(
            "M106 P4 S{polar_cooler}",
            self._section_gcode(
                stock_start_end, "gcode_macro _print_start_phase_extruder"
            ),
        )
        self.assertIn(
            "M106 P4 S0",
            self._section_gcode(stock_start_end, "gcode_macro PRINT_END"),
        )

    def test_print_offset_capture_uses_volatile_saved_value(self):
        text = (OPTIMIZED_MACRO_ROOT / "offset.cfg").read_text(encoding="utf-8")
        gcode = self._macro_gcode("_KM_APPLY_PRINT_OFFSET")
        self.assertIn("variable_captured_z_offset: 0.0", text)
        self.assertIn("variable_captured_z_offset_valid: 0", text)
        self.assertIn("printer.save_variables.variables.z_offset|default(0)|float", gcode)
        self.assertIn("SET_GCODE_VARIABLE MACRO=_km_apply_print_offset VARIABLE=captured_z_offset VALUE={z}", gcode)
        self.assertIn("SET_GCODE_VARIABLE MACRO=_km_apply_print_offset VARIABLE=captured_z_offset_valid VALUE=1", gcode)
        self.assertEqual(gcode.count('action_respond_info("Your Z Offset will be set to: %.3f" % z)'), 1)
        self.assert_ordered(
            gcode,
            "SET_GCODE_VARIABLE MACRO=_km_apply_print_offset VARIABLE=captured_z_offset VALUE={z}",
            'action_respond_info("Your Z Offset will be set to: %.3f" % z)',
            "SET_GCODE_OFFSET Z=0 MOVE=0",
            "{% elif params.SET|default(0)|int %}",
            "SET_GCODE_OFFSET Z={z} MOVE=0",
        )

    def test_filament_sensor_policy_preserves_events_and_box_recovery(self):
        text = (OPTIMIZED_MACRO_ROOT / "filament.cfg").read_text(encoding="utf-8")
        self.assertIn("[filament_switch_sensor filament_switch_sensor]", text)
        self.assertIn("pause_on_runout: False", text)
        self.assertIn("runout_gcode:\n  OPTIMIZED_FILAMENT_SENSOR_RUNOUT", text)
        self.assertIn("insert_gcode:\n  OPTIMIZED_FILAMENT_SENSOR_INSERTED", text)

        control = self._macro_gcode("TLTG_FILAMENT_SENSOR")
        self.assertIn("variable_pause_enabled: 1", text)
        self.assertIn("SET_GCODE_VARIABLE MACRO=TLTG_FILAMENT_SENSOR VARIABLE=pause_enabled VALUE={pause_enabled}", control)
        self.assertIn("ENABLE=0 or ENABLE=1", control)
        self.assertIn("QIDI Box runout handling remains enabled.", control)

        runout = self._macro_gcode("OPTIMIZED_FILAMENT_SENSOR_RUNOUT")
        self.assertIn("TOOLHEAD SENSOR TRIPPED", runout)
        self.assertIn("box_enabled", runout)
        self.assertIn("AUTO_RELOAD_FILAMENT", runout)
        self.assertIn("SET_PRINT_SUB_STATUS SUB_STATUS=box_filament_exhausted", runout)
        self.assertIn("SET_PRINT_SUB_STATUS SUB_STATUS=ext_filament_exhausted", runout)
        self.assert_ordered(runout, "QIDI Box handling", "PAUSE", "M118 Filament run out")

        inserted = self._macro_gcode("OPTIMIZED_FILAMENT_SENSOR_INSERTED")
        self.assertIn("TOOLHEAD SENSOR UNTRIPPED", inserted)
        self.assertIn("M118 Filament detected", inserted)

        resume = self._macro_gcode("RESUME")
        self.assertIn("TRY_RESUME_PRINT", resume)
        self.assertIn("sensor.pause_enabled|int == 0 or printer['filament_switch_sensor filament_switch_sensor'].filament_detected == True", resume)

    def test_tool_mapping_lifecycle_preserves_active_prints_and_resets_idle_state(self):
        ensure = self._macro_gcode("_TLTG_ENSURE_TOOL_MAPPINGS")
        self.assertIn("active_tool_count = [box_count * 4, 16]|min", ensure)
        self.assertIn("for tool in range(active_tool_count)", ensure)
        self.assertIn("current = svv[variable]|default('')|string|trim", ensure)
        self.assertIn("{% if not current %}", ensure)
        self.assertIn("SAVE_VARIABLE VARIABLE={variable} VALUE='\"slot{tool}\"'", ensure)

        reset = self._macro_gcode("_TLTG_RESET_TOOL_MAPPINGS")
        self.assertIn("for tool in range(16)", reset)
        self.assertIn("exists = variable in svv", reset)
        self.assertIn("(exists and current != expected) or (not exists and tool < active_tool_count)", reset)
        self.assertIn("SAVE_VARIABLE VARIABLE={variable} VALUE='\"{expected}\"'", reset)
        self.assertIn("params.REPORT|default(0)|int", reset)

        public_reset = self._macro_gcode("TLTG_RESET_TOOL_MAPPINGS")
        self.assertIn('printer.idle_timeout.state|string == "Printing"', public_reset)
        self.assertIn("printer.virtual_sdcard|default({})", public_reset)
        self.assertIn("printer.pause_resume.is_paused", public_reset)
        self.assertIn("_TLTG_RESET_TOOL_MAPPINGS REPORT=1", public_reset)
        self.assertNotIn("SAVE_VARIABLE", public_reset)

        start_prep = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        self.assertIn("mapped_tool_slot = svv['value_t' ~ tool]|default('')|string|trim", start_prep)
        self.assertIn("tool_slot = mapped_tool_slot if mapped_tool_slot else 'slot' ~ tool", start_prep)
        self.assert_ordered(
            start_prep,
            "_TLTG_ENSURE_TOOL_MAPPINGS",
            "SET_GCODE_VARIABLE MACRO=OPTIMIZED_END_NOZZLE_COOLDOWN_START VARIABLE=reset_tool_mappings VALUE=0",
            "SAVE_VARIABLE VARIABLE=retained_tool_ready VALUE=0",
            "{% if reuse_loaded %}",
        )
        self.assertNotIn("\n  G31\n", f"\n{start_prep}\n")

        end_prep = self._macro_gcode("OPTIMIZED_END_PRINT_FILAMENT_PREP")
        self.assert_ordered(
            end_prep,
            "OPTIMIZED_UNLOAD_FILAMENT T={tool} CLEANUP=0",
            "{% endif %}",
            "SET_GCODE_VARIABLE MACRO=OPTIMIZED_END_NOZZLE_COOLDOWN_START VARIABLE=reset_tool_mappings VALUE=1",
        )

        cooldown = self._macro_gcode("OPTIMIZED_END_NOZZLE_COOLDOWN_START")
        self.assertIn("variable_reset_tool_mappings: 0", (OPTIMIZED_MACRO_ROOT / "filament.cfg").read_text(encoding="utf-8"))
        self.assertIn("not printer.pause_resume.is_paused", cooldown)
        self.assertIn('printer.idle_timeout.state|string == "Printing"', cooldown)
        self.assertIn("printer.virtual_sdcard|default({})", cooldown)
        self.assert_ordered(
            cooldown,
            "M104 S0",
            "OPTIMIZED_END_FAN_COOLDOWN S={exhaust_speed} T={exhaust_duration}",
            "{% if reset_tool_mappings %}",
            "SET_GCODE_VARIABLE MACRO=OPTIMIZED_END_NOZZLE_COOLDOWN_START VARIABLE=reset_tool_mappings VALUE=0",
            "_TLTG_RESET_TOOL_MAPPINGS",
        )

        for relative_path in (
            "orcaslicer_gcode/start.gcode",
            "qidistudio_gcode/start.gcode",
            "orcaslicer_gcode/end.gcode",
            "qidistudio_gcode/end.gcode",
        ):
            slicer_gcode = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("_TLTG_ENSURE_TOOL_MAPPINGS", slicer_gcode)
            self.assertNotIn("_TLTG_RESET_TOOL_MAPPINGS", slicer_gcode)
            self.assertNotIn("TLTG_RESET_TOOL_MAPPINGS", slicer_gcode)

        cancel_gcode = (OPTIMIZED_MACRO_ROOT / "cancel.cfg").read_text(encoding="utf-8")
        self.assertNotIn("_TLTG_RESET_TOOL_MAPPINGS", cancel_gcode)
        self.assertNotIn("TLTG_RESET_TOOL_MAPPINGS", cancel_gcode)

    def test_optimized_g29_always_calibrates_kamp_mesh(self):
        gcode = self._macro_gcode("OPTIMIZED_G29_ZSAFE")
        self.assertIn("BED_MESH_CLEAR", gcode)
        self.assertIn("_OPTIMIZED_G29_HOME_Z_OR_FULL", gcode)
        self.assertIn("BED_MESH_CALIBRATE PROFILE=kamp", gcode)
        self.assertNotIn("bedmesh_before_print", gcode)
        self.assertNotIn("BED_MESH_PROFILE LOAD=default", gcode)
        self.assertNotIn("BED_MESH_CALIBRATE PROFILE=default", gcode)

        z_home_gcode = self._macro_gcode("_OPTIMIZED_G29_HOME_Z_OR_FULL")
        self.assertIn("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT", z_home_gcode)
        self.assertNotIn("G28.6245197 Z", z_home_gcode)

    def test_start_mesh_preparation_uses_optional_saved_profile(self):
        helper = self._macro_gcode("_OPTIMIZED_PREPARE_PRINT_MESH")
        self.assertIn(
            "profile = printer.save_variables.variables.tltg_start_bed_mesh_profile|default('')|string",
            helper,
        )
        self.assertNotIn("tltg_start_bed_mesh_profile|default('')|string|trim", helper)
        self.assertIn("command_profile = profile|replace", helper)
        self.assertGreaterEqual(helper.count("|replace("), 2)

        saved = helper[helper.index("{% if profile %}") : helper.index("{% else %}")]
        adaptive = helper[helper.index("{% else %}") :]
        self.assertEqual(saved.count("action_respond_info"), 1)
        self.assertIn("saved bed mesh profile", saved)
        self.assertIn("% profile", saved)
        self.assert_ordered(
            saved,
            "action_respond_info",
            "G32",
            "SET_STEPPER_ENABLE STEPPER=extruder enable=0",
            "BED_MESH_CLEAR",
            'BED_MESH_PROFILE LOAD="{command_profile}"',
        )
        self.assertNotIn("SAVE_VARIABLE", saved)
        self.assertNotIn("BED_MESH_CALIBRATE", saved)
        self.assertNotIn("SAVE_CONFIG_QD", saved)

        self.assertEqual(adaptive.count("action_respond_info"), 1)
        self.assertIn("adaptive KAMP bed mesh", adaptive)
        self.assert_ordered(
            adaptive,
            "action_respond_info",
            "G31",
            "SET_STEPPER_ENABLE STEPPER=extruder enable=0",
            "BED_MESH_CLEAR",
            "_OPTIMIZED_G29_HOME_Z_OR_FULL",
            "BED_MESH_CALIBRATE PROFILE=kamp",
            "SAVE_VARIABLE VARIABLE=profile_name VALUE='\"kamp\"'",
            "G4 P500",
            "SAVE_CONFIG_QD",
        )
        self.assertNotIn("BED_MESH_PROFILE LOAD=", adaptive)

        start = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        self.assertEqual(start.count("_OPTIMIZED_PREPARE_PRINT_MESH"), 3)
        self.assertNotIn("BED_MESH_CALIBRATE", start)
        self.assertNotIn("BED_MESH_PROFILE LOAD=", start)
        for branch in (
            start[start.index("{% if reuse_loaded %}") : start.index("{% elif box_enabled %}")],
            start[start.index("{% elif box_enabled %}") : start.index("{% else %}")],
            start[start.index("{% else %}") :],
        ):
            self.assert_ordered(
                branch,
                "Z_TILT_ADJUST",
                "M400",
                "SET_PRINT_SUB_STATUS SUB_STATUS=auto_bed_adjust",
                "_OPTIMIZED_PREPARE_PRINT_MESH",
                "M1002 A1",
                "ENABLE_ALL_SENSOR",
            )

    def test_slicer_start_keeps_mesh_selection_printer_side(self):
        expected_calls = {
            "orcaslicer_gcode/start.gcode": "OPTIMIZED_START_PRINT_FILAMENT_PREP EXTRUDER=[initial_no_support_extruder] FIRSTLAYERTEMP=[nozzle_temperature_initial_layer] PURGETEMP={nozzle_temperature_range_high[initial_tool]} BEDTEMP=[bed_temperature_initial_layer_single] CHAMBER=[chamber_temperature]",
            "qidistudio_gcode/start.gcode": "OPTIMIZED_START_PRINT_FILAMENT_PREP EXTRUDER=[initial_no_support_extruder] FIRSTLAYERTEMP=[nozzle_temperature_initial_layer] PURGETEMP={nozzle_temperature_range_high[initial_tool]} BEDTEMP=[bed_temperature_initial_layer_single] CHAMBER=[chamber_temperatures]",
        }
        for relative_path, expected_call in expected_calls.items():
            gcode = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertEqual(gcode.count("OPTIMIZED_START_PRINT_FILAMENT_PREP"), 1)
            self.assertIn(expected_call, gcode)
            self.assertNotIn("BED_MESH_PROFILE", gcode)
            self.assertNotIn("tltg_start_bed_mesh_profile", gcode)

    def test_z_home_uses_fast_randomized_center_target(self):
        globals_text = (OPTIMIZED_MACRO_ROOT / "globals.cfg").read_text(encoding="utf-8")
        self.assertIn("variable_z_home_randomize_radius: 10", globals_text)
        self.assertIn("variable_move_to_z_travel_speed_xy: 45000", globals_text)

        move_gcode = self._macro_gcode("_OPTIMIZED_MOVE_TO_Z_HOME_POINT")
        self.assertEqual(move_gcode.count("range(-radius, radius + 1)|random"), 2)
        self.assertIn("center_x + (range(-radius, radius + 1)|random)", move_gcode)
        self.assertIn("center_y + (range(-radius, radius + 1)|random)", move_gcode)
        self.assertIn("G1 X{target_x} Y{target_y} F{opt.move_to_z_travel_speed_xy}", move_gcode)

    def test_safe_z_home_raw_path_is_not_reentrant(self):
        public_gcode = self._macro_gcode("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT")
        self.assertIn("G28 Z", public_gcode)
        self.assertNotIn("G28.6245197 Z", public_gcode)
        self.assertNotIn("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT_RAW", public_gcode)

        raw_gcode = self._macro_gcode("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT_RAW")
        self.assertIn("printer.configfile.settings.printer.max_accel", raw_gcode)
        self.assertLess(raw_gcode.index("G28.6245197 Z"), raw_gcode.index("SET_VELOCITY_LIMIT ACCEL={default_accel}"))

        optimized_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(OPTIMIZED_MACRO_ROOT.glob("*.cfg")))
        self.assertEqual(optimized_text.count("G28.6245197 Z"), 1)

        homing_override = self._section_gcode(OPTIMIZED_MACRO_ROOT / "kinematics.cfg", "homing_override")
        self.assertEqual(homing_override.count("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT_RAW"), 2)
        self.assertNotIn("\n      _OPTIMIZED_HOME_Z_FROM_SAFE_POINT\n", f"\n{homing_override}\n")

    def test_homing_prep_does_not_nudge_xy_before_raw_homing(self):
        homing_override = self._section_gcode(OPTIMIZED_MACRO_ROOT / "kinematics.cfg", "homing_override")
        self.assertIn("_OPTIMIZED_PREP_XY_HOME SET_Z=1 MOVE_Z=5", homing_override)
        self.assertNotIn("MOVE_X=", homing_override)
        self.assertNotIn("MOVE_Y=", homing_override)

    def test_public_motion_helpers_restore_modal_state_and_acceleration(self):
        cut_gcode = self._macro_gcode("OPTIMIZED_CUT_FILAMENT")
        self.assertIn("saved_accel = printer.toolhead.max_accel|float", cut_gcode)
        self.assert_ordered(
            cut_gcode,
            "SAVE_GCODE_STATE NAME=optimized_cut_filament_state",
            "G90",
            "M204 S10000",
            "M83",
            "G1 E-4 F1000",
            "SET_VELOCITY_LIMIT ACCEL={saved_accel}",
            "RESTORE_GCODE_STATE NAME=optimized_cut_filament_state",
        )

        move_gcode = self._macro_gcode("OPTIMIZED_MOVE_TO_TRASH")
        self.assertIn("saved_accel = printer.toolhead.max_accel|float", move_gcode)
        self.assert_ordered(
            move_gcode,
            "SAVE_GCODE_STATE NAME=optimized_move_to_trash_state",
            "G90",
            "M204 S10000",
            "SET_VELOCITY_LIMIT ACCEL={saved_accel}",
            "RESTORE_GCODE_STATE NAME=optimized_move_to_trash_state",
        )

    def test_end_filament_prep_uses_explicit_relative_extrusion_for_e_only_moves(self):
        end_gcode = self._macro_gcode("OPTIMIZED_END_PRINT_FILAMENT_PREP")
        self.assert_ordered(
            end_gcode,
            "SAVE_GCODE_STATE NAME=optimized_end_print_filament_prep_state",
            "M83",
            "G1 E-3 F1800",
            "RESTORE_GCODE_STATE NAME=optimized_end_print_filament_prep_state",
        )

        unload_gcode = self._macro_gcode("OPTIMIZED_UNLOAD_FILAMENT")
        self.assertIn("saved_accel = printer.toolhead.max_accel|float", unload_gcode)
        self.assert_ordered(
            unload_gcode,
            "SAVE_GCODE_STATE NAME=optimized_unload_filament_state",
            "G90",
            "M83",
            "CUT_FILAMENT T={T}",
            "OPTIMIZED_MOVE_TO_TRASH",
            "UNLOAD_T{T}",
            "G1 E25 F300",
            "SET_VELOCITY_LIMIT ACCEL={saved_accel}",
            "RESTORE_GCODE_STATE NAME=optimized_unload_filament_state",
        )


    def test_retained_filament_tracks_active_box_sync_slot(self):
        start_gcode = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        self.assertIn("slot_sync = svv.slot_sync|default('slot-1')", start_gcode)
        self.assertIn("retained_slot == tool_slot", start_gcode)
        self.assertIn("slot_sync == retained_slot", start_gcode)
        self.assertNotIn("last_load_slot == retained_slot", start_gcode)
        self.assertNotIn("retained_tool == tool", start_gcode)

        end_gcode = self._macro_gcode("OPTIMIZED_END_PRINT_FILAMENT_PREP")
        self.assertIn("active_slot = slot_sync if slot_sync != 'slot-1' else tool_slot", end_gcode)
        self.assertIn("active_tool = namespace(value=tool)", end_gcode)
        self.assertIn("{% for candidate in range(16) %}", end_gcode)
        self.assertIn("mapped_candidate_slot = svv['value_t' ~ candidate]|default('')|string|trim", end_gcode)
        self.assertIn("candidate_slot = mapped_candidate_slot if mapped_candidate_slot else 'slot' ~ candidate", end_gcode)
        self.assertIn("{% if candidate_slot == active_slot %}", end_gcode)
        self.assertIn("SAVE_VARIABLE VARIABLE=retained_tool VALUE={active_tool.value}", end_gcode)
        self.assertIn("SAVE_VARIABLE VARIABLE=retained_slot VALUE='\"{active_slot}\"'", end_gcode)
        self.assertIn("SAVE_VARIABLE VARIABLE=retained_filament_id VALUE={active_filament_id}", end_gcode)
        self.assertIn("SAVE_VARIABLE VARIABLE=retained_vendor_id VALUE={active_vendor_id}", end_gcode)
        self.assertNotIn("SAVE_VARIABLE VARIABLE=retained_tool VALUE={tool}", end_gcode)
        self.assertNotIn("SAVE_VARIABLE VARIABLE=retained_slot VALUE='\"{tool_slot}\"'", end_gcode)

    def test_end_sequence_waits_for_staged_hotend_cooldown_before_wiping(self):
        orca_end_gcode = (REPO_ROOT / "orcaslicer_gcode/end.gcode").read_text(
            encoding="utf-8"
        )
        self.assert_ordered(
            orca_end_gcode,
            "G0 Z{min(max_print_height, max_layer_z + 3)} F600",
            "OPTIMIZED_MOVE_TO_TRASH",
            "OPTIMIZED_END_PRINT_FILAMENT_PREP T=[current_extruder]",
            "{if activate_air_filtration_on_completion[current_extruder]}",
            "OPTIMIZED_END_NOZZLE_COOLDOWN_START EXHAUST_SPEED={complete_print_exhaust_fan_speed[current_extruder] * 255 / 100}",
            "{else}",
            "OPTIMIZED_END_NOZZLE_COOLDOWN_START EXHAUST_SPEED=0",
            "G1 Z{min(max_print_height, max_print_height / 2 + 10)} F600",
            "OPTIMIZED_END_STAGED_NOZZLE_WIPE",
            "PRINT_END",
        )
        self.assertNotIn("OPTIMIZED_END_FAN_COOLDOWN", orca_end_gcode)

        qidistudio_end_gcode = (REPO_ROOT / "qidistudio_gcode/end.gcode").read_text(
            encoding="utf-8"
        )
        self.assert_ordered(
            qidistudio_end_gcode,
            "G0 Z{min(max_print_height, max_layer_z + 3)} F600",
            "OPTIMIZED_MOVE_TO_TRASH",
            "OPTIMIZED_END_PRINT_FILAMENT_PREP T=[current_extruder]",
            "OPTIMIZED_END_NOZZLE_COOLDOWN_START EXHAUST_SPEED=0",
            "G1 Z{min(max_print_height, max_print_height / 2 + 10)} F600",
            "OPTIMIZED_END_STAGED_NOZZLE_WIPE",
            "PRINT_END",
        )
        self.assertNotIn("activate_air_filtration_on_completion", qidistudio_end_gcode)
        self.assertNotIn("complete_print_exhaust_fan_speed", qidistudio_end_gcode)
        self.assertNotIn("OPTIMIZED_END_FAN_COOLDOWN", qidistudio_end_gcode)

        cooldown_start = self._macro_gcode("OPTIMIZED_END_NOZZLE_COOLDOWN_START")
        self.assert_ordered(
            cooldown_start,
            "M106 S255",
            "exhaust_speed = params.EXHAUST_SPEED|default(0)|int",
            "OPTIMIZED_DISABLE_BOX_HEATER",
            "M141 S0",
            "M140 S0",
            "M104 S0",
            "OPTIMIZED_END_FAN_COOLDOWN S={exhaust_speed} T={exhaust_duration}",
            "_TLTG_RESET_TOOL_MAPPINGS",
        )

        staged_wipe = self._macro_gcode("OPTIMIZED_END_STAGED_NOZZLE_WIPE")
        self.assert_ordered(
            staged_wipe,
            "TEMPERATURE_WAIT SENSOR={extruder} MAXIMUM={first_wipe_temp}",
            "CLEAR_OOZE",
            "CLEAR_FLUSH",
            "TEMPERATURE_WAIT SENSOR={extruder} MAXIMUM={final_wipe_temp}",
            "CLEAR_OOZE",
            "CLEAR_FLUSH",
            "G1 Y-{pull_forward_y} F6000",
        )

        end_prep = self._macro_gcode("OPTIMIZED_END_PRINT_FILAMENT_PREP")
        self.assertIn("OPTIMIZED_UNLOAD_FILAMENT T={tool} CLEANUP=0", end_prep)

        cleanup_alias = self._macro_gcode("OPTIMIZED_END_NOZZLE_CLEANUP")
        self.assert_ordered(
            cleanup_alias,
            "OPTIMIZED_MOVE_TO_TRASH",
            "OPTIMIZED_END_NOZZLE_COOLDOWN_START {rawparams}",
            "OPTIMIZED_END_STAGED_NOZZLE_WIPE",
        )

    def test_retained_filament_start_waits_for_bed_and_chamber_at_trash(self):
        start_gcode = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        retained_gcode = start_gcode[start_gcode.index("M118 Reusing retained filament on T{tool}") : start_gcode.index("{% elif box_enabled %}")]
        self.assertNotIn("G1 X20 Y20", retained_gcode)
        self.assert_ordered(
            retained_gcode,
            "G1 Z20 F480",
            "OPTIMIZED_MOVE_TO_TRASH",
            "OPTIMIZED_WAIT_BED S={bed_target} STATUS=wait_bed_temp",
            "OPTIMIZED_WAIT_CHAMBER S={chamber_target} STATUS=wait_chamber_temp",
            "OPTIMIZED_WAIT_HOTEND S={reuse_nozzle_target} STATUS=clear_nozzle",
            "CLEAR_OOZE",
            "CLEAR_FLUSH",
            "Z_TILT_ADJUST",
        )

    def test_start_reports_live_bed_temperature_before_each_z_tilt(self):
        report_gcode = self._macro_gcode("_OPTIMIZED_REPORT_BED_TEMP")
        self.assertIn(
            "M118 Bed temp wait reached.  Target: {printer.heater_bed.target|round(1)} Actual: {printer.heater_bed.temperature|round(1)}",
            report_gcode,
        )

        start_gcode = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        self.assertEqual(start_gcode.count("_OPTIMIZED_REPORT_BED_TEMP"), 3)
        self.assertEqual(start_gcode.count("Z_TILT_ADJUST"), 3)
        for branch in start_gcode.split("Z_TILT_ADJUST")[:-1]:
            self.assertTrue(branch.rstrip().endswith("_OPTIMIZED_REPORT_BED_TEMP"))

    def test_chamber_wait_accepts_three_degree_startup_window(self):
        chamber_gcode = self._macro_gcode("OPTIMIZED_WAIT_CHAMBER")
        self.assertIn('TEMPERATURE_WAIT SENSOR="heater_generic chamber" MINIMUM={([target - 3, 0]|max)}', chamber_gcode)
        self.assertNotIn("target, 65", chamber_gcode)


    def test_rear_bed_scrape_orients_cable_chain_and_uses_shared_chute_speed(self):
        globals_text = (OPTIMIZED_MACRO_ROOT / "globals.cfg").read_text(encoding="utf-8")
        self.assertIn("variable_trash_final_approach_speed_xy: 3500", globals_text)
        self.assertIn("variable_rear_scrape_orient_speed_xy: 24000", globals_text)

        move_to_trash = self._macro_gcode("OPTIMIZED_MOVE_TO_TRASH")
        self.assertEqual(move_to_trash.count("F{opt.trash_final_approach_speed_xy}"), 4)
        self.assertNotIn("F3500", move_to_trash)

        scrape = self._macro_gcode("_OPTIMIZED_REAR_BED_SCRAPE")
        self.assertIn("saved_accel = printer.toolhead.max_accel|float", scrape)
        self.assert_ordered(
            scrape,
            "OPTIMIZED_MOVE_TO_TRASH",
            "M204 S10000",
            "G1 Y{km.park_y - 50} F{opt.rear_scrape_orient_speed_xy}",
            "G1 X380 F{opt.rear_scrape_orient_speed_xy}",
            "G1 X188 F{opt.rear_scrape_orient_speed_xy}",
            "G1 Y392 F{opt.trash_final_approach_speed_xy}",
            "G1 Z-0.2 F480",
            "G1 X15 F200",
            "G1 Y3",
            "G1 X-15",
            "G1 Y-3",
            "G1 X15",
            "G1 Z10",
            "G1 Y383 F12000",
            "SET_VELOCITY_LIMIT ACCEL={saved_accel}",
        )
        self.assertNotIn("G1 Y395 F6000", scrape)
        self.assertNotIn("G1 Y2", scrape)
        self.assertNotIn("G1 Y-2", scrape)

        wipe = self._macro_gcode("OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE")
        start = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        self.assertEqual(wipe.count("_OPTIMIZED_REAR_BED_SCRAPE"), 1)
        self.assertEqual(start.count("_OPTIMIZED_REAR_BED_SCRAPE"), 1)
        self.assertNotIn("G1 Z-0.2 F480", wipe)
        self.assertEqual(start.count("G1 Z-0.2 F480"), 0)

    def test_no_box_start_path_wipes_and_scrapes_without_rear_purge(self):
        start_gcode = self._macro_gcode("OPTIMIZED_START_PRINT_FILAMENT_PREP")
        no_box_gcode = start_gcode[start_gcode.index("M118 Starting without QIDI Box filament prep") :]
        self.assertIn("OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE TARGET={scrape_target}", no_box_gcode)
        self.assertNotIn("CLEAR_NOZZLE", no_box_gcode)
        self.assert_ordered(
            no_box_gcode,
            "OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE TARGET={scrape_target}",
            "Z_TILT_ADJUST",
            "M400",
            "_OPTIMIZED_PREPARE_PRINT_MESH",
        )
        self.assertNotIn("BED_MESH_CALIBRATE", no_box_gcode)

        wipe_gcode = self._macro_gcode("OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE")
        self.assertNotIn("G1 E", wipe_gcode)
        self.assertNotIn("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT", wipe_gcode)
        self.assertNotIn("_OPTIMIZED_HOME_Z_FROM_SAFE_POINT_RAW", wipe_gcode)
        self.assertIn("OPTIMIZED_WAIT_HOTEND S={scrape_target} STATUS=clear_nozzle", wipe_gcode)
        self.assertIn("_OPTIMIZED_REAR_BED_SCRAPE", wipe_gcode)
        self.assertNotIn("G1 Z-0.2 F480", wipe_gcode)

    def assert_ordered(self, text: str, *needles: str):
        position = -1
        for needle in needles:
            next_position = text.index(needle, position + 1)
            self.assertGreater(next_position, position, needle)
            position = next_position

    def _macro_gcode(self, name: str) -> str:
        macro = self.macros[name]
        return "\n".join(line for _, line in macro.gcode_lines)

    def _section_gcode(self, path, name: str) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        for section_name, header_index, end_index in iter_sections(lines):
            if section_name.lower() == name.lower():
                return "\n".join(line for _, line in extract_gcode_lines(lines, header_index + 1, end_index))
        self.fail(f"Missing section [{name}] in {path}")
