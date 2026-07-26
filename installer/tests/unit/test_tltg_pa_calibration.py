from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest

from installer.tests.helpers import REPO_ROOT


MODULE_PATH = REPO_ROOT / "installer/klipper/extras/tltg_pa_calibration.py"
FIXTURE_PATH = REPO_ROOT / "installer/tests/fixtures/pa_calibration/selection_cases.json"
TRACE_FIXTURE_PATH = REPO_ROOT / "installer/tests/fixtures/pa_calibration/trace_cases.json"
SPEC = importlib.util.spec_from_file_location("tltg_pa_calibration", MODULE_PATH)
pa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = pa
SPEC.loader.exec_module(pa)


class TLTGPressureAdvancePureTests(unittest.TestCase):
    def test_decodes_signed_24_bit_frames(self):
        fixture = json.loads(TRACE_FIXTURE_PATH.read_text(encoding="utf-8"))["decode"]
        payload = bytes.fromhex(fixture["payload_hex"])
        self.assertEqual(pa.decode_cs1237_payload(payload), fixture["counts"])
        with self.assertRaisesRegex(pa.CalibrationError, "divisible by four"):
            pa.decode_cs1237_payload(b"bad")

    def test_reconstructs_sample_times_and_reports_gaps(self):
        fixture = json.loads(TRACE_FIXTURE_PATH.read_text(encoding="utf-8"))["timing"]
        batches = [
            pa.RawBatch(item["receive_time"], _frames(*item["counts"]))
            for item in fixture["batches"]
        ]
        samples, gaps = pa.reconstruct_samples(batches, sample_rate=fixture["sample_rate"])
        self.assertEqual([item.counts for item in samples], [1, 2, 3, 4, 5, 6])
        self.assertAlmostEqual(samples[0].time, 1.0)
        self.assertAlmostEqual(samples[-1].time, 1.010)
        self.assertEqual(len(gaps), fixture["expected_gap_count"])

    def test_excludes_chute_clearing_intervals(self):
        fixture = json.loads(TRACE_FIXTURE_PATH.read_text(encoding="utf-8"))["clearing"]
        samples = [pa.TimedSample(value, index) for index, value in enumerate(fixture["sample_times"])]
        kept = pa.exclude_intervals(samples, tuple(map(tuple, fixture["excluded_intervals"])))
        self.assertEqual([item.time for item in kept], fixture["expected_times"])

    def test_accounts_for_capture_coverage_gaps_and_timing_residual(self):
        samples = [pa.TimedSample(index / 1000.0, index) for index in range(901)]
        quality = pa.assess_capture_quality(
            samples,
            0.0,
            1.0,
            gaps=((0.4, 0.5),),
            timing_residual=0.002,
            sample_rate=1000.0,
        )
        self.assertAlmostEqual(quality.coverage, 901.0 / 1001.0)
        self.assertAlmostEqual(quality.max_gap, 0.1)
        self.assertEqual(quality.timing_residual, 0.002)

    def test_groups_no_more_than_two_transitions_and_aligns_sample_timing(self):
        windows = tuple(
            pa.TransitionWindow(index * 0.01, index, index + 0.2, index + 0.6, index + 0.9)
            for index in range(5)
        )
        groups = pa.group_transitions(windows)
        self.assertEqual([len(group.transitions) for group in groups], [2, 2, 1])
        samples = [pa.TimedSample(index / 1000.0, index) for index in range(5001)]
        self.assertLessEqual(pa.schedule_timing_residual(samples, windows), 0.0005)

    def test_builds_bounded_coarse_and_fine_k_values(self):
        self.assertEqual(
            pa.build_bounded_k_values(0.0, 0.05, 0.01, max_values=8),
            (0.0, 0.01, 0.02, 0.03, 0.04, 0.05),
        )
        self.assertEqual(
            pa.build_bounded_k_values(0.02, 0.03, 0.002, max_values=8),
            (0.02, 0.022, 0.024, 0.026, 0.028, 0.03),
        )
        with self.assertRaisesRegex(pa.CalibrationError, "exactly bounded"):
            pa.build_bounded_k_values(0.0, 0.05, 0.01, max_values=5)

    def test_analyzes_positive_and_negative_force_polarity(self):
        positive = _synthetic_cycle(0.02, polarity=1, undershoot=0.08)
        negative = _synthetic_cycle(0.02, polarity=-1, undershoot=0.08)
        window = pa.TransitionWindow(0.02, 0.0, 1.0, 2.0, 3.0)
        positive_metrics = pa.analyze_cycle(positive, window)
        negative_metrics = pa.analyze_cycle(negative, window)
        self.assertGreater(positive_metrics.amplitude, 90)
        self.assertGreater(positive_metrics.undershoot, 0.05)
        self.assertAlmostEqual(
            positive_metrics.undershoot,
            negative_metrics.undershoot,
            delta=0.02,
        )
        self.assertEqual(positive_metrics.polarity, 1)
        self.assertEqual(negative_metrics.polarity, -1)

    def test_signed_recovery_area_distinguishes_lag_from_single_sample_spike(self):
        window = pa.TransitionWindow(0.02, 0.0, 1.0, 2.0, 3.0)
        lagging = pa.analyze_cycle(
            _synthetic_cycle(0.02, polarity=1, undershoot=0.0, recovery_bias=0.12),
            window,
        )
        excessive = pa.analyze_cycle(
            _synthetic_cycle(0.02, polarity=1, undershoot=0.80),
            window,
        )
        clean = pa.analyze_cycle(
            _synthetic_cycle(0.02, polarity=1, undershoot=0.0), window
        )
        spiked = _synthetic_cycle(0.02, polarity=1, undershoot=0.0)
        spiked[210] = pa.TimedSample(spiked[210].time, -100)
        spike_metrics = pa.analyze_cycle(spiked, window)
        self.assertGreater(lagging.fall_signed_area, 0.0)
        self.assertLess(excessive.fall_signed_area, 0.0)
        self.assertGreater(spike_metrics.undershoot, 0.5)
        self.assertLess(
            abs(spike_metrics.fall_signed_area - clean.fall_signed_area), 0.02
        )

    def test_shaped_flow_analysis_uses_acceleration_and_post_deceleration_area(self):
        window = _shaped_window()
        ideal = pa.analyze_cycle(_synthetic_shaped_cycle(), window)
        decel_error = pa.analyze_cycle(
            _synthetic_shaped_cycle(deceleration_error=0.20), window
        )
        self.assertLess(ideal.tracking_error, 0.01)
        self.assertAlmostEqual(ideal.fall_signed_area, 0.0, delta=0.001)
        self.assertGreater(decel_error.tracking_error, ideal.tracking_error)
        self.assertAlmostEqual(
            decel_error.fall_signed_area,
            ideal.fall_signed_area,
            delta=0.001,
        )
        self.assertEqual(decel_error.acceleration, 15.0)
        self.assertEqual(decel_error.low_velocity, 0.5)
        self.assertEqual(decel_error.high_velocity, 2.0)

    def test_composite_objective_balances_components_and_rejects_near_tie(self):
        metrics = _selection_case_metrics({"name": "competing-components"})
        competing = [
            _replace_metric(
                item,
                tracking_error=0.008,
                rise_delay=0.008,
                fall_delay=0.008,
            )
            if item.k == 0.016
            else item
            for item in metrics
        ]
        self.assertEqual(pa.select_pa_value(competing).value, 0.020)
        near_tie = [
            _replace_metric(
                item,
                tracking_error=0.005,
                rise_delay=0.005,
                fall_delay=0.005,
            )
            if item.k == 0.016
            else item
            for item in metrics
        ]
        self.assertEqual(
            pa.select_pa_value(near_tie).reason,
            "COMPOSITE_OBJECTIVE_AMBIGUOUS",
        )

    def test_selection_fixtures_fail_closed_or_return_interior_value(self):
        cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["name"]):
                metrics = _selection_case_metrics(case)
                result = pa.select_pa_value(metrics)
                if "expected_value" in case:
                    self.assertTrue(result.successful)
                    self.assertAlmostEqual(result.value, case["expected_value"])
                    self.assertEqual(result.profile_values, (0.02, 0.02))
                    reversed_result = pa.select_pa_value(tuple(reversed(metrics)))
                    self.assertEqual(reversed_result, result)
                else:
                    self.assertFalse(result.successful)
                    self.assertEqual(result.reason, case["expected_reason"])
                    self.assertNotIn("PA_VALUE=", result.reason)

    def test_weak_absolute_signal_fails_closed(self):
        metrics = _selection_case_metrics({"name": "weak", "amplitude": 0.5})
        self.assertEqual(
            pa.select_pa_value(metrics).reason,
            "WEAK_OR_NOISY_SIGNAL",
        )

    def test_capture_quality_fixtures_fail_closed(self):
        fixture = json.loads(TRACE_FIXTURE_PATH.read_text(encoding="utf-8"))
        metrics = _selection_case_metrics({"name": "quality"})
        for case in fixture["quality_cases"]:
            with self.subTest(case=case["name"]):
                quality = pa.CaptureQuality(
                    case["coverage"], case["max_gap"], case["timing_residual"]
                )
                result = pa.select_pa_value(metrics, quality=quality)
                self.assertEqual(result.reason, case.get("expected_reason"))

    def test_selection_rejects_incomplete_and_non_monotonic_profiles(self):
        metrics = _selection_case_metrics({"name": "base"})
        uncorroborated = [
            item
            for index, item in enumerate(metrics)
            if not (item.acceleration == 10.0 and item.k == 0.02 and index % 2)
        ]
        self.assertEqual(
            pa.select_pa_value(uncorroborated).reason,
            "INSUFFICIENT_CORROBORATION",
        )
        one_profile = [item for item in metrics if item.acceleration == 10.0]
        self.assertEqual(
            pa.select_pa_value(one_profile).reason,
            "INCOMPLETE_ACCELERATION_PROFILES",
        )
        incomplete = [
            item
            for item in metrics
            if not (item.acceleration == 20.0 and item.k == 0.028)
        ]
        self.assertEqual(
            pa.select_pa_value(incomplete).reason,
            "INCOMPLETE_PROFILE_COVERAGE",
        )
        inconsistent_polarity = list(metrics)
        inconsistent_polarity[0] = _replace_metric(
            inconsistent_polarity[0], polarity=-1
        )
        self.assertEqual(
            pa.select_pa_value(inconsistent_polarity).reason,
            "INCONSISTENT_POLARITY",
        )
        incompatible_flow = [
            _replace_metric(item, high_velocity=2.0)
            if item.acceleration == 20.0
            else item
            for item in metrics
        ]
        self.assertEqual(
            pa.select_pa_value(incompatible_flow).reason,
            "INCOMPATIBLE_ACCELERATION_PROFILES",
        )
        non_monotonic = [
            _replace_metric(
                item,
                fall_signed_area=(
                    0.2
                    if item.acceleration == 10.0 and item.k == 0.024
                    else item.fall_signed_area
                ),
            )
            for item in metrics
        ]
        self.assertEqual(
            pa.select_pa_value(non_monotonic).reason,
            "NON_MONOTONIC_RESPONSE",
        )

    def test_accepts_only_supported_nozzles_and_formats_nonpersistent_value(self):
        for nozzle in pa.SUPPORTED_NOZZLES:
            pa.validate_inputs(240.0, nozzle)
        for nozzle in (None, 0.3, 1.0):
            with self.assertRaisesRegex(pa.CalibrationError, "INVALID_NOZZLE"):
                pa.validate_inputs(240.0, nozzle)
        self.assertEqual(
            pa.format_result(0.032, 240.0, 0.4),
            "PA_VALUE=0.032000 TEMP=240.0 NOZZLE=0.4 PERSISTED=0",
        )

    def test_builds_stationary_pa_enabled_trapq_move(self):
        move = pa.build_trapq_pulse(5.0, 10.0, 1.0, 5.0, 20.0, 0.5)
        extruder = _FakeExtruder()
        move.append(extruder)
        args = extruder.append_args
        self.assertEqual(args[0], extruder.trapq)
        self.assertEqual(args[6:9], (0.0, 0.0, 1.0))
        self.assertEqual(args[9], 1.0)
        self.assertGreater(move.distance, 0)

    def test_stationary_pulse_plan_provides_pa_smoothing_context(self):
        plan = _validated_nozzle_plan()
        pulse = pa.build_stationary_pulse_plan(5.0, 10.0, 0.03, plan)
        self.assertEqual(len(pulse.moves), 3)
        self.assertEqual(pulse.moves[0].cruise_time, plan.lead_time)
        self.assertEqual(pulse.moves[-1].cruise_time, plan.lead_time)
        self.assertEqual(pulse.transition.rise, 5.0 + plan.lead_time)
        self.assertLess(pulse.transition.rise, pulse.transition.rise_end)
        self.assertLess(pulse.transition.rise_end, pulse.transition.fall)
        self.assertLess(pulse.transition.fall, pulse.transition.fall_end)
        self.assertLess(pulse.transition.fall_end, pulse.transition.end)
        self.assertEqual(pulse.transition.acceleration, plan.acceleration)
        self.assertEqual(pulse.transition.low_velocity, plan.low_velocity)
        self.assertEqual(pulse.transition.high_velocity, plan.high_velocity)
        self.assertGreater(pulse.end_e, 10.0)
        for previous, following in zip(pulse.moves, pulse.moves[1:]):
            previous_end = (
                previous.print_time
                + previous.accel_time
                + previous.cruise_time
                + previous.decel_time
            )
            self.assertAlmostEqual(previous_end, following.print_time)

    def test_analysis_rejects_acceleration_ramp_mismatch(self):
        window = pa.TransitionWindow(
            k=0.02,
            start=0.0,
            rise=1.0,
            rise_end=1.2,
            fall=2.0,
            fall_end=2.2,
            end=3.0,
            acceleration=20.0,
            low_velocity=0.5,
            high_velocity=2.0,
        )
        with self.assertRaisesRegex(pa.CalibrationError, "does not match"):
            pa.analyze_cycle(
                _synthetic_cycle(0.02, polarity=1, undershoot=0.0), window
            )


class TLTGPressureAdvanceRuntimeGuardTests(unittest.TestCase):
    def test_loaded_external_filament_is_detected_when_sensor_events_are_disabled(self):
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": False},
            variables={"enable_box": 0},
        )
        state = pa.inspect_loaded_filament(printer, 1.0)
        self.assertEqual(state, pa.FilamentState("external", False))

    def test_missing_toolhead_filament_fails_even_if_box_reports_loaded(self):
        printer = _filament_printer(
            sensor={"filament_detected": False, "enabled": True},
            variables={
                "enable_box": 1,
                "slot_sync": "slot2",
                "last_load_slot": "slot2",
            },
            controller=_loaded_controller("slot2"),
            box={"e_endstop_state": 1},
        )
        with self.assertRaisesRegex(pa.CalibrationError, "FILAMENT_NOT_LOADED"):
            pa.inspect_loaded_filament(printer, 1.0)

    def test_loaded_qidi_box_filament_requires_consistent_vendor_state(self):
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": False},
            variables={
                "enable_box": 1,
                "slot_sync": "slot4",
                "last_load_slot": "slot4",
            },
            controller=_loaded_controller("slot4"),
            box={"e_endstop_state": 1},
        )
        state = pa.inspect_loaded_filament(printer, 1.0)
        self.assertEqual(state, pa.FilamentState("qidi_box", False, "slot4"))

        printer.objects["box_extras"] = _StatusObject({"e_endstop_state": 0})
        with self.assertRaisesRegex(
            pa.CalibrationError, "FILAMENT_STATE_INCONSISTENT"
        ):
            pa.inspect_loaded_filament(printer, 1.0)

    def test_slot16_direct_feed_is_accepted_without_physical_slot_state(self):
        controller = _loaded_controller("slot16")
        controller["slots"]["states"] = {}
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": True},
            variables={
                "enable_box": 1,
                "slot_sync": "slot16",
                "last_load_slot": "slot16",
            },
            controller=controller,
            box={"e_endstop_state": 1},
        )
        state = pa.inspect_loaded_filament(printer, 1.0)
        self.assertEqual(state, pa.FilamentState("external", True, "slot16"))

    def test_busy_or_unsynced_box_filament_state_fails_closed(self):
        busy = _loaded_controller("slot2")
        busy["operation"]["current"] = 1
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": True},
            variables={
                "enable_box": 1,
                "slot_sync": "slot2",
                "last_load_slot": "slot2",
            },
            controller=busy,
            box={"e_endstop_state": 1},
        )
        with self.assertRaisesRegex(pa.CalibrationError, "FILAMENT_SOURCE_BUSY"):
            pa.inspect_loaded_filament(printer, 1.0)

        ready = _loaded_controller("slot2")
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": True},
            variables={
                "enable_box": 1,
                "slot_sync": "slot-1",
                "last_load_slot": "slot2",
            },
            controller=ready,
            box={"e_endstop_state": 1},
        )
        with self.assertRaisesRegex(
            pa.CalibrationError, "FILAMENT_SOURCE_UNSYNCED"
        ):
            pa.inspect_loaded_filament(printer, 1.0)

        invalid_slot = _loaded_controller("slot17")
        printer = _filament_printer(
            sensor={"filament_detected": True, "enabled": True},
            variables={
                "enable_box": 1,
                "slot_sync": "slot17",
                "last_load_slot": "slot17",
            },
            controller=invalid_slot,
            box={"e_endstop_state": 1},
        )
        with self.assertRaisesRegex(
            pa.CalibrationError, "FILAMENT_SOURCE_UNSYNCED"
        ):
            pa.inspect_loaded_filament(printer, 1.0)

    def test_preflight_accepts_idle_loaded_external_source_with_validated_plan(self):
        printer = _preflight_printer()
        sensor = _PreflightSensorAdapter(printer)
        trapq = _PreflightTrapqAdapter(printer)
        plan = _validated_nozzle_plan()

        result = pa.validate_calibration_preflight(
            printer,
            1.0,
            240.0,
            0.4,
            plans={0.4: plan},
            sensor_adapter_factory=lambda unused: sensor,
            trapq_adapter_factory=lambda unused: trapq,
        )

        self.assertEqual(result.filament.source, "external")
        self.assertIs(result.plan, plan)
        self.assertEqual(trapq.calls, [(plan, 240.0)])
        self.assertEqual(sensor.calls, ["validate_configuration"])
        self.assertEqual(printer.events, [])

    def test_preflight_sensor_checks_are_read_only_queries(self):
        printer = _preflight_printer()
        sensor = _FakeSensor()
        printer.objects["probe_air"] = type(
            "Probe", (), {"sensor_helper": sensor}
        )()
        result = pa.validate_calibration_preflight(
            printer,
            1.0,
            240.0,
            0.4,
            plans={0.4: _validated_nozzle_plan()},
            trapq_adapter_factory=_PreflightTrapqAdapter,
        )
        self.assertIsInstance(result.sensor_adapter, pa.QidiCS1237Adapter)
        self.assertEqual(sensor.query_cs1237_home_state_cmd.sends, [[sensor.oid]])
        self.assertEqual(sensor.query_cs1237_config_read_cmd.sends, [[sensor.oid]])
        self.assertEqual(sensor.mcu.read_command.sends, [])

    def test_preflight_rejects_unvalidated_production_nozzle_plans(self):
        printer = _preflight_printer()
        factories = []
        with self.assertRaisesRegex(pa.CalibrationError, "NOZZLE_PLAN_UNVALIDATED"):
            pa.validate_calibration_preflight(
                printer,
                1.0,
                240.0,
                0.4,
                sensor_adapter_factory=lambda unused: factories.append("sensor"),
                trapq_adapter_factory=lambda unused: factories.append("trapq"),
            )
        self.assertEqual(factories, [])
        self.assertEqual(printer.events, [])

    def test_preflight_fails_closed_for_active_or_unknown_print_state(self):
        for state in ("printing", "paused", "starting", None):
            with self.subTest(state=state):
                printer = _preflight_printer(print_state=state)
                with self.assertRaisesRegex(
                    pa.CalibrationError, "CALIBRATION_REQUIRES_IDLE_PRINTER"
                ):
                    pa.validate_calibration_preflight(
                        printer,
                        1.0,
                        240.0,
                        0.4,
                        plans={0.4: _validated_nozzle_plan()},
                        sensor_adapter_factory=lambda unused: self.fail(
                            "sensor adapter must not be created"
                        ),
                        trapq_adapter_factory=lambda unused: self.fail(
                            "trapq adapter must not be created"
                        ),
                    )
                self.assertEqual(printer.events, [])

        printer = _preflight_printer()
        printer.objects["idle_timeout"] = _StatusObject({"state": "Printing"})
        with self.assertRaisesRegex(
            pa.CalibrationError, "CALIBRATION_REQUIRES_IDLE_PRINTER"
        ):
            pa.validate_calibration_preflight(
                printer,
                1.0,
                240.0,
                0.4,
                plans={0.4: _validated_nozzle_plan()},
            )

    def test_preflight_requires_every_setup_and_cleanup_command(self):
        printer = _preflight_printer()
        del printer.objects["gcode"].status["commands"]["CLEAR_FLUSH"]
        with self.assertRaisesRegex(
            pa.CalibrationError, "CALIBRATION_COMMAND_UNAVAILABLE"
        ):
            pa.validate_calibration_preflight(
                printer,
                1.0,
                240.0,
                0.4,
                plans={0.4: _validated_nozzle_plan()},
            )
        self.assertEqual(printer.events, [])

    def test_nozzle_resource_plan_rejects_group_and_distance_overflow(self):
        valid = _validated_nozzle_plan()
        pa._validate_nozzle_resource_plan(valid, 0.4)
        for plan, reason in (
            (
                pa.NozzleResourcePlan(
                    **{**valid.__dict__, "max_group_pulses": 3}
                ),
                "INVALID_NOZZLE_PLAN",
            ),
            (
                pa.NozzleResourcePlan(
                    **{**valid.__dict__, "max_pulse_distance": 0.1}
                ),
                "NOZZLE_PLAN_RESOURCE_LIMIT",
            ),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(pa.CalibrationError, reason):
                    pa._validate_nozzle_resource_plan(plan, 0.4)

    def test_direct_trapq_adapter_pins_runtime_and_limits_plan(self):
        printer, toolhead, extruder = _trapq_printer()
        adapter = pa.QidiDirectTrapqAdapter(
            printer,
            hash_provider=lambda unused_toolhead, unused_extruder: dict(
                pa.EXPECTED_TRAPQ_RUNTIME_HASHES
            ),
        )
        adapter.validate_plan(_validated_nozzle_plan(), 240.0)
        self.assertIs(adapter.toolhead, toolhead)
        self.assertIs(adapter.extruder, extruder)
        self.assertIs(adapter.extruder_stepper, extruder.extruder_stepper)

        with self.assertRaisesRegex(pa.CalibrationError, "UNSUPPORTED_TRAPQ_RUNTIME"):
            pa.QidiDirectTrapqAdapter(
                printer,
                hash_provider=lambda unused_toolhead, unused_extruder: {},
            )

    def test_direct_trapq_adapter_requires_active_extruder_stepper_trapq(self):
        printer, unused_toolhead, extruder = _trapq_printer()
        extruder.extruder_stepper.stepper.trapq = object()
        with self.assertRaisesRegex(
            pa.CalibrationError, "UNSUPPORTED_TRAPQ_INTERFACE"
        ):
            pa.QidiDirectTrapqAdapter(
                printer,
                hash_provider=lambda unused_toolhead, unused_extruder: dict(
                    pa.EXPECTED_TRAPQ_RUNTIME_HASHES
                ),
            )

    def test_runtime_trapq_hash_resolver_reads_each_pinned_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = REPO_ROOT.__class__(temporary)
            chelper = root / "chelper"
            chelper.mkdir()
            paths = {
                "extruder.py": root / "extruder.py",
                "toolhead.py": root / "toolhead.py",
                "chelper/__init__.py": chelper / "__init__.py",
                "trapq.c": chelper / "trapq.c",
                "kin_extruder.c": chelper / "kin_extruder.c",
                "c_helper.so": chelper / "c_helper.so",
            }
            for index, path in enumerate(paths.values()):
                path.write_bytes(("fixture-%d" % index).encode("ascii"))
            toolhead = object()
            extruder = object()
            original_source_file = pa._class_source_file
            original_chelper = sys.modules.get("chelper")
            try:
                pa._class_source_file = lambda value: str(
                    paths["toolhead.py"]
                    if value is toolhead
                    else paths["extruder.py"]
                )
                sys.modules["chelper"] = types.SimpleNamespace(
                    __file__=str(paths["chelper/__init__.py"])
                )
                actual = pa._runtime_trapq_hashes(toolhead, extruder)
            finally:
                pa._class_source_file = original_source_file
                if original_chelper is None:
                    sys.modules.pop("chelper", None)
                else:
                    sys.modules["chelper"] = original_chelper
            expected = {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in paths.items()
            }
            self.assertEqual(actual, expected)

    def test_direct_trapq_adapter_rejects_qidi_e_accumulator_state(self):
        printer, toolhead, unused_extruder = _trapq_printer()
        toolhead.e_accumulator = 0.001
        with self.assertRaisesRegex(pa.CalibrationError, "UNSUPPORTED_TRAPQ_STATE"):
            pa.QidiDirectTrapqAdapter(
                printer,
                hash_provider=lambda unused_toolhead, unused_extruder: dict(
                    pa.EXPECTED_TRAPQ_RUNTIME_HASHES
                ),
            )

    def test_state_machine_orders_setup_groups_analysis_and_cleanup(self):
        backend = _StateMachineBackend()
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=3)

        result = machine.run(prepared, groups)

        self.assertTrue(result.selection.successful)
        self.assertEqual(machine.state, "done")
        self.assertEqual(
            backend.events,
            [
                "capture_temporary_state",
                "home_all",
                "move_absolute_z:200.0",
                "park_at_trash",
                "heat_and_wait:240.0",
                "start_capture:2",
                "queue_measured_pulse:0.00",
                "queue_measured_pulse:0.01",
                "wait_for_owned_work",
                "finish_capture:0",
                "clear_flush",
                "park_at_trash",
                "wait_for_sensor_settling",
                "start_capture:1",
                "queue_measured_pulse:0.02",
                "wait_for_owned_work",
                "finish_capture:1",
                "analyze",
                "finalize_owned_work",
                "restore_temporary_state",
                "motion_is_safe",
                "clear_ooze",
                "clear_flush",
            ],
        )
        self.assertFalse(
            {"SAVE_CONFIG", "SAVE_VARIABLE", "SET_GCODE_VARIABLE"}
            & set(backend.events)
        )

    def test_state_machine_failure_injection_always_restores_owned_state(self):
        boundaries = (
            "capture_temporary_state",
            "home_all",
            "move_absolute_z",
            "park_at_trash",
            "heat_and_wait",
            "start_capture",
            "queue_measured_pulse",
            "wait_for_owned_work",
            "finish_capture",
            "clear_flush",
            "wait_for_sensor_settling",
            "analyze",
        )
        prepared, groups = _prepared_run(pulse_count=3)
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                backend = _StateMachineBackend(fail_at=boundary)
                machine = pa.CalibrationStateMachine(backend)
                with self.assertRaises(pa.CalibrationRunError):
                    machine.run(prepared, groups)
                self.assertIn("finalize_owned_work", backend.events)
                expected_restores = 0 if boundary == "capture_temporary_state" else 1
                self.assertEqual(
                    backend.events.count("restore_temporary_state"),
                    expected_restores,
                )
                self.assertEqual(machine.state, "failed")

    def test_state_machine_rejects_invalid_grouping_before_side_effects(self):
        backend = _StateMachineBackend()
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=3)
        invalid = (pa.PulseGroup(tuple(sum((list(g.transitions) for g in groups), []))),)
        with self.assertRaisesRegex(pa.CalibrationError, "INVALID_PULSE_GROUP"):
            machine.run(prepared, invalid)
        self.assertEqual(backend.events, [])

    def test_cleanup_failure_takes_priority_over_primary_failure(self):
        backend = _StateMachineBackend(
            fail_at=("analyze", "restore_temporary_state")
        )
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=1)
        with self.assertRaisesRegex(
            pa.CalibrationRunError, "CALIBRATION_CLEANUP_FAILED"
        ) as raised:
            machine.run(prepared, groups)
        self.assertIsInstance(raised.exception.cause, pa.CalibrationError)
        self.assertEqual(
            raised.exception.cleanup_errors,
            (("restore", "CalibrationError"),),
        )

    def test_state_machine_reports_cleanup_failure_and_is_idempotent(self):
        backend = _StateMachineBackend(fail_at="clear_ooze")
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=1)
        with self.assertRaisesRegex(
            pa.CalibrationRunError, "CALIBRATION_CLEANUP_FAILED"
        ) as raised:
            machine.run(prepared, groups)
        self.assertEqual(
            raised.exception.cleanup_errors, (("clear_ooze", "CalibrationError"),)
        )
        self.assertEqual(machine._cleanup(None, True), ())
        self.assertEqual(backend.events.count("restore_temporary_state"), 1)

    def test_state_machine_does_not_move_cleanup_when_motion_is_unsafe(self):
        backend = _StateMachineBackend(motion_safe=False)
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=1)
        with self.assertRaisesRegex(
            pa.CalibrationRunError, "CALIBRATION_CLEANUP_FAILED"
        ) as raised:
            machine.run(prepared, groups)
        self.assertIn(
            ("motion_safety", "UnsafeMotion"),
            raised.exception.cleanup_errors,
        )
        self.assertIn("note_manual_chute_cleanup_required", backend.events)
        self.assertNotIn("clear_ooze", backend.events)
        self.assertNotIn("clear_flush", backend.events)

    def test_command_reports_value_only_after_successful_cleanup(self):
        backend = _StateMachineBackend()
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=1)
        gcmd = _FakeGcmd({})
        result = pa.execute_and_report(gcmd, machine, prepared, groups)
        self.assertTrue(result.selection.successful)
        self.assertEqual(
            gcmd.responses,
            ["PA_VALUE=0.032000 TEMP=240.0 NOZZLE=0.4 PERSISTED=0"],
        )
        self.assertEqual(backend.events[-1], "clear_flush")

    def test_command_failure_response_never_contains_value_token(self):
        backend = _StateMachineBackend(fail_at="analyze")
        machine = pa.CalibrationStateMachine(backend)
        prepared, groups = _prepared_run(pulse_count=1)
        gcmd = _FakeGcmd({})
        with self.assertRaisesRegex(RuntimeError, "PA_CALIBRATION_FAILED"):
            pa.execute_and_report(gcmd, machine, prepared, groups)
        self.assertEqual(len(gcmd.responses), 1)
        self.assertNotIn("PA_VALUE=", gcmd.responses[0])
        self.assertIn("REASON=INJECTED_ANALYZE", gcmd.responses[0])
        self.assertEqual(backend.events[-1], "clear_flush")

    def test_failure_formatter_rejects_value_tokens_and_free_text(self):
        self.assertEqual(
            pa.format_failure("BAD PA_VALUE=0.2"),
            "PA_CALIBRATION_FAILED REASON=CALIBRATION_INTERNAL_ERROR",
        )

    def test_developer_capture_motion_marker_is_status_only(self):
        toolhead = _StatusOnlyToolhead(12.5)
        self.assertEqual(pa._toolhead_print_time(toolhead, 1.0), 12.5)
        self.assertEqual(toolhead.status_calls, [1.0])

    def test_developer_capture_converts_toolhead_interface_error(self):
        sensor = _FakeSensor()
        printer = _CaptureRuntimePrinter(
            {
                "gcode": _FakeGcode(),
                "print_stats": _StatusObject({"state": "standby"}),
                "idle_timeout": _StatusObject({"state": "Idle"}),
                "probe_air": type("Probe", (), {"sensor_helper": sensor})(),
            }
        )
        runtime = pa.TLTGPressureAdvance(_DeveloperCaptureConfig(printer))
        with self.assertRaisesRegex(RuntimeError, "UNSUPPORTED_TOOLHEAD_INTERFACE"):
            runtime.cmd_capture(_FakeGcmd({"SECONDS": 0.01, "RATE": 100}))

    def test_sensor_adapter_schedules_non_homing_reads_and_releases_ownership(self):
        sensor = _FakeSensor()
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        stock_state = (sensor.zero_state, sensor.trigger_threshold)

        capture = adapter.capture(_FakeReactor(), 0.02, 100)

        self.assertEqual(capture.requested_responses, 2)
        self.assertEqual(capture.received_responses, 2)
        self.assertTrue(capture.complete)
        self.assertEqual(capture.issues, ())
        self.assertAlmostEqual(capture.print_start, 0.1)
        self.assertFalse(adapter.active)
        self.assertNotIn(("query_cs1237_data", sensor.oid), sensor.mcu._serial.handlers)
        sends = sensor.mcu.read_command.sends
        self.assertEqual([item[0] for item in sends], [[sensor.oid, 0, 0]] * 2)
        self.assertEqual(sends[0][1]["minclock"], sends[0][1]["reqclock"])
        self.assertGreater(sends[1][1]["minclock"], sends[0][1]["minclock"])
        self.assertEqual(
            (sensor.zero_state, sensor.trigger_threshold),
            stock_state,
        )

    def test_sensor_adapter_rejects_concurrent_ownership(self):
        sensor = _FakeSensor()
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        adapter.active = True
        with self.assertRaisesRegex(pa.CalibrationError, "SENSOR_BUSY"):
            adapter.capture(_FakeReactor(), 0.02, 100)
        self.assertEqual(sensor.query_cs1237_config_read_cmd.sends, [])

    def test_sensor_adapter_rejects_stock_homing_ownership(self):
        sensor = _FakeSensor(homing=1)
        printer = _LookupPrinter(
            {"probe_air": type("Probe", (), {"sensor_helper": sensor})()}
        )
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(pa.CalibrationError, "SENSOR_BUSY"):
            adapter.capture(_FakeReactor(), 0.02, 100)
        self.assertEqual(sensor.query_cs1237_home_state_cmd.sends, [[sensor.oid]])
        self.assertEqual(sensor.query_cs1237_config_read_cmd.sends, [])
        self.assertEqual(sensor.mcu.read_command.sends, [])

    def test_sensor_adapter_rejects_existing_response_handler_before_query(self):
        sensor = _FakeSensor()
        key = (pa.QidiCS1237Adapter.RESPONSE_NAME, sensor.oid)
        sensor.mcu._serial.handlers[key] = lambda params: None
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(pa.CalibrationError, "SENSOR_BUSY"):
            adapter.capture(_FakeReactor(), 0.02, 100)
        self.assertEqual(sensor.query_cs1237_config_read_cmd.sends, [])

    def test_sensor_adapter_rejects_unexpected_adc_configuration(self):
        sensor = _FakeSensor(config=44)
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(pa.CalibrationError, "UNSUPPORTED_SENSOR_CONFIG"):
            adapter.capture(_FakeReactor(), 0.02, 100)
        self.assertEqual(sensor.mcu.read_command.sends, [])

    def test_sensor_adapter_rejects_malformed_config_response(self):
        sensor = _FakeSensor(config_oid=99)
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(pa.CalibrationError, "UNSUPPORTED_SENSOR_CONFIG"):
            adapter.capture(_FakeReactor(), 0.02, 100)
        self.assertEqual(sensor.mcu.read_command.sends, [])

    def test_sensor_adapter_bounds_capture_resources_before_query(self):
        sensor = _FakeSensor()
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        for duration, rate, reason in (
            (0.0, 100, "INVALID_CAPTURE_DURATION"),
            (6.0, 100, "INVALID_CAPTURE_DURATION"),
            (1.0, 0, "INVALID_CAPTURE_RATE"),
            (1.0, 1001, "INVALID_CAPTURE_RATE"),
        ):
            with self.subTest(duration=duration, rate=rate):
                with self.assertRaisesRegex(pa.CalibrationError, reason):
                    adapter.capture(_FakeReactor(), duration, rate)
        self.assertEqual(sensor.query_cs1237_config_read_cmd.sends, [])

    def test_sensor_adapter_accounts_for_dropped_and_invalid_responses(self):
        dropped = _FakeSensor(drop_indices={1})
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": dropped})()})
        capture = pa.QidiCS1237Adapter(printer).capture(_FakeReactor(), 0.03, 100)
        self.assertEqual(capture.requested_responses, 3)
        self.assertEqual(capture.received_responses, 2)
        self.assertEqual(capture.issues, ("RESPONSE_COUNT_MISMATCH",))
        self.assertFalse(capture.complete)

        invalid = _FakeSensor(payload=b"bad")
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": invalid})()})
        capture = pa.QidiCS1237Adapter(printer).capture(_FakeReactor(), 0.01, 100)
        self.assertEqual(capture.received_responses, 0)
        self.assertEqual(
            capture.issues,
            ("INVALID_RESPONSE_PAYLOAD", "RESPONSE_COUNT_MISMATCH"),
        )
        self.assertEqual(capture.rejected_messages[0]["data"], "626164")

    def test_sensor_adapter_rejects_duplicate_response_identity(self):
        sensor = _FakeSensor(extra_response_indices={0})
        printer = _LookupPrinter(
            {"probe_air": type("Probe", (), {"sensor_helper": sensor})()}
        )
        capture = pa.QidiCS1237Adapter(printer).capture(
            _FakeReactor(), 0.01, 100
        )
        self.assertEqual(capture.received_responses, 1)
        self.assertEqual(capture.issues, ("DUPLICATE_RESPONSE_IDENTITY",))
        self.assertEqual(len(capture.rejected_messages), 1)
        self.assertFalse(capture.complete)

    def test_sensor_adapter_caps_unique_response_overflow(self):
        sensor = _FakeSensor(
            extra_response_indices={0}, extra_response_time_offset=0.0001
        )
        printer = _LookupPrinter(
            {"probe_air": type("Probe", (), {"sensor_helper": sensor})()}
        )
        capture = pa.QidiCS1237Adapter(printer).capture(
            _FakeReactor(), 0.01, 100
        )
        self.assertEqual(capture.received_responses, 1)
        self.assertEqual(capture.issues, ("RESPONSE_OVERFLOW",))
        self.assertEqual(len(capture.rejected_messages), 1)
        self.assertFalse(capture.complete)

    def test_sensor_adapter_cleanup_does_not_remove_replacement_handler(self):
        sensor = _FakeSensor()
        printer = _LookupPrinter(
            {"probe_air": type("Probe", (), {"sensor_helper": sensor})()}
        )
        replacement = lambda params: None
        key = (pa.QidiCS1237Adapter.RESPONSE_NAME, sensor.oid)
        reactor = _FakeReactor(
            on_pause=lambda: sensor.mcu._serial.handlers.__setitem__(
                key, replacement
            )
        )
        with self.assertRaisesRegex(
            pa.CaptureOperationError, "SENSOR_OWNERSHIP_UNSAFE"
        ) as raised:
            pa.QidiCS1237Adapter(printer).capture(reactor, 0.01, 100)
        self.assertIn(
            "SENSOR_HANDLER_OWNERSHIP_LOST", raised.exception.capture.issues
        )
        self.assertIn(
            "STOCK_SENSOR_STATE_UNVERIFIED", raised.exception.capture.issues
        )
        self.assertIs(sensor.mcu._serial.handlers[key], replacement)
        self.assertEqual(len(printer.shutdowns), 1)

    def test_sensor_adapter_releases_ownership_when_send_fails(self):
        sensor = _FakeSensor(fail_after=1)
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(pa.CaptureOperationError, "CAPTURE_OPERATION_FAILED") as raised:
            adapter.capture(_FakeReactor(), 0.03, 100)
        self.assertIn("CAPTURE_OPERATION_FAILED", raised.exception.capture.issues)
        self.assertEqual(raised.exception.capture.operation_errors, ("RuntimeError",))
        self.assertFalse(adapter.active)
        self.assertNotIn(
            (pa.QidiCS1237Adapter.RESPONSE_NAME, sensor.oid),
            sensor.mcu._serial.handlers,
        )

    def test_sensor_adapter_shuts_down_if_stock_config_changes_after_capture(self):
        sensor = _FakeSensor()
        printer = _LookupPrinter(
            {"probe_air": type("Probe", (), {"sensor_helper": sensor})()}
        )
        adapter = pa.QidiCS1237Adapter(printer)

        def mutate_config():
            sensor.query_cs1237_config_read_cmd.config = 190

        with self.assertRaisesRegex(
            pa.CaptureOperationError, "STOCK_SENSOR_STATE_CHANGED"
        ) as raised:
            adapter.capture(_FakeReactor(on_pause=mutate_config), 0.01, 100)
        self.assertIn("STOCK_SENSOR_STATE_CHANGED", raised.exception.capture.issues)
        self.assertEqual(len(printer.shutdowns), 1)
        self.assertIn("FIRMWARE_RESTART required", printer.shutdowns[0])

    def test_sensor_adapter_releases_local_ownership_when_unregister_fails(self):
        sensor = _FakeSensor(fail_unregister=True)
        printer = _LookupPrinter({"probe_air": type("Probe", (), {"sensor_helper": sensor})()})
        adapter = pa.QidiCS1237Adapter(printer)
        with self.assertRaisesRegex(
            pa.CaptureOperationError, "SENSOR_HANDLER_CLEANUP_FAILED"
        ) as raised:
            adapter.capture(_FakeReactor(), 0.01, 100)
        self.assertFalse(adapter.active)
        self.assertNotIn(id(sensor), pa._ACTIVE_SENSOR_IDS)
        self.assertIn(
            "SENSOR_HANDLER_CLEANUP_FAILED",
            raised.exception.capture.issues,
        )
        self.assertIn(
            (pa.QidiCS1237Adapter.RESPONSE_NAME, sensor.oid),
            sensor.mcu._serial.handlers,
        )
        self.assertIn(
            "STOCK_SENSOR_STATE_UNVERIFIED", raised.exception.capture.issues
        )
        self.assertEqual(len(printer.shutdowns), 1)

    def test_calibration_command_is_hard_disabled_before_side_effects(self):
        printer = _RuntimePrinter()
        config = _FakeConfig(printer)
        runtime = pa.TLTGPressureAdvance(config)
        invalid = _FakeGcmd({"TEMP": 240.0, "NOZZLE": 0.3})
        with self.assertRaisesRegex(RuntimeError, "INVALID_NOZZLE"):
            runtime.cmd_calibrate(invalid)
        gcmd = _FakeGcmd({"TEMP": 240.0, "NOZZLE": 0.4})
        with self.assertRaisesRegex(RuntimeError, "PA_CALIBRATION_UNVALIDATED"):
            runtime.cmd_calibrate(gcmd)
        self.assertEqual(printer.events, [])
        self.assertNotIn("PA_VALUE=", " ".join(gcmd.responses))


class _FakeCommand:
    def __init__(
        self,
        mcu=None,
        *,
        drop_indices=None,
        extra_response_indices=None,
        extra_response_time_offset=0.0,
        payload=b"\x01\x00\x00\x00",
        fail_after=None,
    ):
        self.mcu = mcu
        self.drop_indices = set(drop_indices or ())
        self.extra_response_indices = set(extra_response_indices or ())
        self.extra_response_time_offset = extra_response_time_offset
        self.payload = payload
        self.fail_after = fail_after
        self.sends = []

    def send(self, values, **kwargs):
        index = len(self.sends)
        self.sends.append((list(values), dict(kwargs)))
        if self.fail_after is not None and index >= self.fail_after:
            raise RuntimeError("send failed")
        if self.mcu is not None and index not in self.drop_indices:
            callback = self.mcu._serial.handlers[("query_cs1237_data", values[0])]
            clock = kwargs["reqclock"]
            params = {
                "oid": values[0],
                "data": self.payload,
                "#sent_time": clock / 1_000_000.0,
                "#receive_time": clock / 1_000_000.0 + 0.0005,
            }
            callback(params)
            if index in self.extra_response_indices:
                extra = dict(params)
                extra["#sent_time"] += self.extra_response_time_offset
                extra["#receive_time"] += self.extra_response_time_offset
                callback(extra)


class _FakeConfigReadCommand:
    def __init__(self, config, oid=None):
        self.config = config
        self.oid = oid
        self.sends = []

    def send(self, values):
        self.sends.append(list(values))
        return {
            "oid": values[0] if self.oid is None else self.oid,
            "config": self.config,
        }


class _FakeHomeStateCommand:
    def __init__(self, homing=0):
        self.homing = homing
        self.sends = []

    def send(self, values):
        self.sends.append(list(values))
        return {"homing": self.homing, "trigger_clock": 0}


class _FakeSerial:
    def __init__(self):
        self.handlers = {}


class _FakeMcu:
    def __init__(
        self,
        *,
        drop_indices=None,
        extra_response_indices=None,
        extra_response_time_offset=0.0,
        payload=b"\x01\x00\x00\x00",
        fail_after=None,
        fail_unregister=False,
    ):
        self._serial = _FakeSerial()
        self.fail_unregister = fail_unregister
        self.read_command = _FakeCommand(
            self,
            drop_indices=drop_indices,
            extra_response_indices=extra_response_indices,
            extra_response_time_offset=extra_response_time_offset,
            payload=payload,
            fail_after=fail_after,
        )

    def estimated_print_time(self, eventtime):
        return eventtime

    def lookup_command(self, message):
        return self.read_command

    def print_time_to_clock(self, print_time):
        return int(print_time * 1_000_000)

    def register_response(self, callback, name, oid=None):
        key = (name, oid)
        if callback is None:
            if self.fail_unregister:
                raise RuntimeError("unregister failed")
            self._serial.handlers.pop(key, None)
        else:
            self._serial.handlers[key] = callback


class _FakeSensor:
    def __init__(
        self,
        config=pa.CS1237_CONFIG_1280_SPS,
        *,
        config_oid=None,
        drop_indices=None,
        extra_response_indices=None,
        extra_response_time_offset=0.0,
        payload=b"\x01\x00\x00\x00",
        fail_after=None,
        fail_unregister=False,
        homing=0,
    ):
        self.mcu = _FakeMcu(
            drop_indices=drop_indices,
            extra_response_indices=extra_response_indices,
            extra_response_time_offset=extra_response_time_offset,
            payload=payload,
            fail_after=fail_after,
            fail_unregister=fail_unregister,
        )
        self.oid = 7
        self.zero_state = object()
        self.trigger_threshold = object()
        self.query_cs1237_config_read_cmd = _FakeConfigReadCommand(
            config, oid=config_oid
        )
        self.query_cs1237_home_state_cmd = _FakeHomeStateCommand(homing)


class _FakeReactor:
    def __init__(self, on_pause=None):
        self.now = 0.0
        self.on_pause = on_pause

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.now = waketime
        if self.on_pause is not None:
            self.on_pause()
        return waketime


class _LookupPrinter:
    def __init__(self, objects):
        self.objects = objects
        self.shutdowns = []

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def invoke_shutdown(self, message):
        self.shutdowns.append(message)


class _StatusObject:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime):
        return self.status


class _StatusOnlyToolhead:
    def __init__(self, print_time):
        self.print_time = print_time
        self.status_calls = []

    def get_status(self, eventtime):
        self.status_calls.append(eventtime)
        return {"print_time": self.print_time}


class _FakeExtruder:
    def __init__(self):
        self.trapq = object()
        self.append_args = None

    def get_trapq(self):
        return self.trapq

    def trapq_append(self, *args):
        self.append_args = args


class _FakeGcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, callback):
        self.commands[name] = callback


class _PreflightSensorAdapter:
    def __init__(self, printer):
        self.printer = printer
        self.calls = []

    def validate_configuration(self):
        self.calls.append("validate_configuration")


class _PreflightTrapqAdapter:
    def __init__(self, printer):
        self.printer = printer
        self.calls = []

    def validate_plan(self, plan, temperature):
        self.calls.append((plan, temperature))


class _FakeTrapqHeater:
    min_extrude_temp = 170.0
    max_temp = 360.0
    can_extrude = False


class _FakeStepperKinematics:
    def __init__(self, trapq):
        self.trapq = trapq

    def get_trapq(self):
        return self.trapq


class _FakeTrapqExtruderStepper:
    def __init__(self, trapq):
        self.pressure_advance = 0.03
        self.pressure_advance_smooth_time = 0.04
        self.stepper = _FakeStepperKinematics(trapq)

    def _set_pressure_advance(self, pressure_advance, smooth_time):
        pass


class _FakeTrapqExtruder:
    def __init__(self):
        self.last_position = 7.0
        self.max_e_velocity = 20.0
        self.max_e_accel = 100.0
        self.max_e_dist = 50.0
        self.heater = _FakeTrapqHeater()
        self.trapq = object()
        self.extruder_stepper = _FakeTrapqExtruderStepper(self.trapq)

    def get_name(self):
        return "extruder"

    def get_trapq(self):
        return self.trapq

    def trapq_append(self, *args):
        pass

    def trapq_finalize_moves(self, *args):
        pass


class _FakeTrapqToolhead:
    def __init__(self, extruder):
        self.extruder = extruder
        self.commanded_pos = [1.0, 2.0, 3.0, 7.0]
        self.e_enable = False
        self.e_accumulator = 0.0
        self.kin_flush_delay = 0.05

    def get_extruder(self):
        return self.extruder

    def flush_step_generation(self):
        pass

    def note_mcu_movequeue_activity(self, print_time, set_step_gen_time=False):
        pass

    def _advance_move_time(self, print_time):
        pass


class _StateMachineBackend:
    def __init__(self, fail_at=None, motion_safe=True):
        self.failures = set(
            fail_at if isinstance(fail_at, (tuple, list, set)) else (fail_at,)
        )
        self.failures.discard(None)
        self.safe = motion_safe
        self.events = []
        self.capture_index = 0

    def _record(self, event, boundary=None):
        self.events.append(event)
        failure_boundary = boundary or event.split(":", 1)[0]
        if failure_boundary in self.failures:
            raise pa.CalibrationError(
                "INJECTED_%s" % failure_boundary.upper()
            )

    def capture_temporary_state(self):
        self._record("capture_temporary_state")

    def home_all(self):
        self._record("home_all")

    def move_absolute_z(self, value):
        self._record("move_absolute_z:%.1f" % value, "move_absolute_z")

    def park_at_trash(self):
        self._record("park_at_trash")

    def heat_and_wait(self, temperature):
        self._record("heat_and_wait:%.1f" % temperature, "heat_and_wait")

    def start_capture(self, group, prepared):
        self._record("start_capture:%d" % len(group.transitions), "start_capture")
        return self.capture_index

    def queue_measured_pulse(self, transition, prepared):
        self._record(
            "queue_measured_pulse:%.2f" % transition.k,
            "queue_measured_pulse",
        )

    def wait_for_owned_work(self):
        self._record("wait_for_owned_work")

    def finish_capture(self, capture):
        self._record("finish_capture:%d" % capture, "finish_capture")
        self.capture_index += 1
        return "capture-%d" % capture

    def abort_capture(self, capture):
        self._record("abort_capture:%s" % capture, "abort_capture")

    def clear_flush(self):
        self._record("clear_flush")

    def wait_for_sensor_settling(self):
        self._record("wait_for_sensor_settling")

    def analyze(self, captures, groups, prepared):
        self._record("analyze")
        return pa.SelectionResult(value=0.032)

    def finalize_owned_work(self):
        self._record("finalize_owned_work")

    def restore_temporary_state(self):
        self._record("restore_temporary_state")

    def motion_is_safe(self):
        self._record("motion_is_safe")
        return self.safe

    def clear_ooze(self):
        self._record("clear_ooze")

    def note_manual_chute_cleanup_required(self):
        self._record("note_manual_chute_cleanup_required")


class _RuntimePrinter:
    def __init__(self):
        self.gcode = _FakeGcode()
        self.events = []

    def lookup_object(self, name, default=None):
        if name == "gcode":
            return self.gcode
        return default


class _FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def getboolean(self, name, default=False):
        return default


class _DeveloperCaptureConfig(_FakeConfig):
    def getboolean(self, name, default=False):
        return True


class _CaptureRuntimePrinter(_LookupPrinter):
    def __init__(self, objects):
        super().__init__(objects)
        self.reactor = _FakeReactor()

    def get_reactor(self):
        return self.reactor


class _FakeGcmd:
    def __init__(self, params):
        self.params = params
        self.responses = []

    def get_float(self, name, default=None, **kwargs):
        return self.params.get(name, default)

    def get_int(self, name, default=None, **kwargs):
        return self.params.get(name, default)

    def error(self, message):
        return RuntimeError(message)

    def respond_info(self, message):
        self.responses.append(message)


def _prepared_run(pulse_count):
    base_plan = _validated_nozzle_plan()
    plan = pa.NozzleResourcePlan(
        **{**base_plan.__dict__, "pulse_count": pulse_count}
    )
    prepared = pa.CalibrationPreflightResult(
        temperature=240.0,
        nozzle=0.4,
        filament=pa.FilamentState("external", False),
        plan=plan,
        sensor_adapter=object(),
        trapq_adapter=object(),
    )
    windows = tuple(
        pa.TransitionWindow(
            k=index * 0.01,
            start=float(index),
            rise=float(index) + 0.2,
            fall=float(index) + 0.6,
            end=float(index) + 0.9,
        )
        for index in range(pulse_count)
    )
    return prepared, pa.group_transitions(windows)


def _validated_nozzle_plan():
    return pa.NozzleResourcePlan(
        nozzle=0.4,
        hardware_validated=True,
        low_velocity=1.0,
        high_velocity=5.0,
        acceleration=20.0,
        high_time=0.2,
        lead_time=0.05,
        pulse_count=2,
        max_group_pulses=2,
        max_pulse_distance=5.0,
        max_total_distance=10.0,
        max_duration=5.0,
    )


def _preflight_printer(print_state="standby"):
    commands = {name: "" for name in pa.REQUIRED_CALIBRATION_COMMANDS}
    printer = _LookupPrinter(
        {
            "print_stats": _StatusObject({"state": print_state}),
            "idle_timeout": _StatusObject({"state": "Idle"}),
            "gcode": _StatusObject({"commands": commands}),
            "filament_switch_sensor filament_switch_sensor": _StatusObject(
                {"filament_detected": True, "enabled": False}
            ),
            "save_variables": _StatusObject({"variables": {"enable_box": 0}}),
        }
    )
    printer.events = []
    return printer


def _trapq_printer():
    extruder = _FakeTrapqExtruder()
    toolhead = _FakeTrapqToolhead(extruder)
    return _LookupPrinter({"toolhead": toolhead}), toolhead, extruder


def _loaded_controller(slot):
    return {
        "system": {"ready": True},
        "hardware": {"connected": True},
        "extruder": {"loaded": True, "filament_detected": True},
        "operation": {
            "current": -1,
            "error": None,
            "is_waiting_user": False,
        },
        "slots": {"last_loaded": slot, "states": {slot: 2}},
        "sensors": {"e_endstop": 1},
    }


def _filament_printer(*, sensor, variables, controller=None, box=None):
    objects = {
        "filament_switch_sensor filament_switch_sensor": _StatusObject(sensor),
        "save_variables": _StatusObject({"variables": variables}),
    }
    if controller is not None:
        objects["multi_color_controller"] = _StatusObject(controller)
    if box is not None:
        objects["box_extras"] = _StatusObject(box)
    return _LookupPrinter(objects)


def _selection_case_metrics(case):
    k_values = (0.012, 0.016, 0.020, 0.024, 0.028)
    default_optimum = 0.020
    configured = case.get("profile_optimums", {})
    metrics = []
    for acceleration in (10.0, 20.0):
        optimum = configured.get(str(acceleration), default_optimum)
        for k in k_values:
            distance = (k - optimum) / 0.004
            under = max(0.0, -distance)
            over = max(0.0, distance)
            for repeat_sign in (-1.0, 1.0):
                jitter = 0.001 * repeat_sign
                if case.get("ambiguous"):
                    tracking_error = 0.05 + jitter
                    rise_delay = 0.02 + jitter
                    fall_delay = 0.02 + jitter
                    overshoot = 0.03 + jitter
                    undershoot = 0.03 + jitter
                    settling_error = 0.04 + jitter
                    recovery_error = 0.04 + jitter
                    plateau_slope = 0.01 + jitter
                else:
                    tracking_error = 0.02 + 0.08 * under + 0.015 * over + jitter
                    rise_delay = 0.01 + 0.03 * under + jitter
                    fall_delay = 0.01 + 0.03 * under + jitter
                    overshoot = 0.04 * over + jitter
                    undershoot = 0.06 * over + jitter
                    settling_error = 0.015 + 0.025 * abs(distance) + jitter
                    recovery_error = 0.015 + 0.03 * abs(distance) + jitter
                    plateau_slope = 0.003 * abs(distance) + jitter
                if (
                    case.get("inconsistent")
                    and acceleration == 10.0
                    and k == 0.020
                    and repeat_sign > 0.0
                ):
                    tracking_error += 0.10
                metrics.append(
                    pa.CycleMetrics(
                        k=k,
                        amplitude=case.get("amplitude", 100.0),
                        noise=case.get("noise", 0.01),
                        rise_delay=max(0.0, rise_delay),
                        fall_delay=max(0.0, fall_delay),
                        overshoot=max(0.0, overshoot),
                        undershoot=max(0.0, undershoot),
                        settling_error=max(0.0, settling_error),
                        saturated=case.get("saturated", False),
                        tracking_error=max(0.0, tracking_error),
                        fall_signed_area=(
                            -0.03 * distance
                            + case.get("recovery_bias", 0.0)
                            + 0.0005 * repeat_sign
                        ),
                        recovery_error=max(0.0, recovery_error),
                        plateau_slope=plateau_slope,
                        acceleration=acceleration,
                        low_velocity=0.5,
                        high_velocity=3.0,
                        polarity=1,
                    )
                )
    return metrics


def _replace_metric(item, **updates):
    values = dict(item.__dict__)
    values.update(updates)
    return pa.CycleMetrics(**values)


def _frames(*values):
    frames = []
    for value in values:
        raw = value & 0xFFFFFF
        frames.append(bytes((raw & 0xFF, raw >> 8 & 0xFF, raw >> 16 & 0xFF, 0)))
    return b"".join(frames)


def _shaped_window():
    return pa.TransitionWindow(
        k=0.02,
        start=0.0,
        rise=1.0,
        rise_end=1.1,
        fall=2.0,
        fall_end=2.1,
        end=3.0,
        acceleration=15.0,
        low_velocity=0.5,
        high_velocity=2.0,
    )


def _synthetic_shaped_cycle(deceleration_error=0.0):
    samples = []
    for index in range(301):
        sample_time = index * 0.01
        if sample_time < 1.0:
            expected = 0.0
        elif sample_time < 1.1:
            expected = (sample_time - 1.0) / 0.1
        elif sample_time < 2.0:
            expected = 1.0
        elif sample_time < 2.1:
            expected = 1.0 - (sample_time - 2.0) / 0.1
        else:
            expected = 0.0
        residual = (
            deceleration_error if 2.0 <= sample_time < 2.1 else 0.0
        )
        samples.append(
            pa.TimedSample(sample_time, int(round((expected + residual) * 100.0)))
        )
    return samples


def _synthetic_cycle(k, polarity, undershoot, recovery_bias=0.0):
    samples = []
    step = 0.01
    index = 0
    while index <= 300:
        t = index * step
        if t < 1.0:
            level = 0.0
        elif t < 1.2:
            level = (t - 1.0) / 0.2
        elif t < 2.0:
            level = 1.0
        elif t < 2.1:
            recovery_level = recovery_bias - undershoot
            level = 1.0 + (t - 2.0) / 0.1 * (recovery_level - 1.0)
        elif t < 2.3:
            recovery_level = recovery_bias - undershoot
            level = recovery_level * (1.0 - (t - 2.1) / 0.2)
        else:
            level = 0.0
        counts = int(round(polarity * level * 100.0))
        samples.append(pa.TimedSample(t, counts))
        index += 1
    return samples


if __name__ == "__main__":
    unittest.main()
