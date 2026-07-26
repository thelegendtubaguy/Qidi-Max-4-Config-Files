# Max 4 load-cell pressure advance calibration support.
#
# This module is intentionally disabled for calibration until the private QIDI
# CS1237 and Klipper trapq contracts have been validated on printer hardware.
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass


SAMPLE_RATE = 1280.0
DEFAULT_CAPTURE_RATE = 500
MAX_CAPTURE_DURATION = 5.0
MAX_CAPTURE_RATE = 1000
MAX_CAPTURE_REQUESTS = 5000
CS1237_CONFIG_1280_SPS = 60
SUPPORTED_NOZZLES = (0.2, 0.4, 0.6, 0.8)
ADC_MIN = -0x800000
ADC_MAX = 0x7FFFFF
CALIBRATION_ENABLED = False
SAFE_PRINT_STATES = frozenset(("standby", "complete", "error", "cancelled"))
SAFE_IDLE_TIMEOUT_STATES = frozenset(("Idle", "Ready"))
REQUIRED_CALIBRATION_COMMANDS = frozenset(
    ("G28", "G90", "G1", "M109", "M400", "OPTIMIZED_MOVE_TO_TRASH", "CLEAR_OOZE", "CLEAR_FLUSH")
)
EXPECTED_TRAPQ_RUNTIME_HASHES = {
    "extruder.py": "cb61a97829ef29ff7848f6e6c0a96cf974d8a57e5a6f517f8f314610a1e08494",
    "toolhead.py": "077b0832047989b17267689155198444f2820c43c5f08372ca87ea351cb7473b",
    "chelper/__init__.py": "b875718a4655bdd256f0e2fad59e31f12ac0f9b5f06654814e0f032b97cba7f3",
    "trapq.c": "156e73502d2ce86a384d145e78946df215b542efb32274b9940d3625faca8f2f",
    "kin_extruder.c": "4a352a7a287b782a47d813e94b85e33bfd662cdd8877f8507e2f60d847cd9538",
    "c_helper.so": "214d1ab79a78b2aa28ba8cd7ba0d7383afcaaf9d36561112aa7bf73cf6591714",
}
_ACTIVE_SENSOR_IDS = set()


class CalibrationError(Exception):
    pass


class CaptureOperationError(CalibrationError):
    def __init__(self, reason, capture):
        super().__init__(reason)
        self.reason = reason
        self.capture = capture


class CalibrationRunError(CalibrationError):
    def __init__(self, reason, cause=None, cleanup_errors=()):
        super().__init__(reason)
        self.reason = reason
        self.cause = cause
        self.cleanup_errors = tuple(cleanup_errors)


@dataclass(frozen=True)
class RawBatch:
    receive_time: float
    payload: bytes


@dataclass(frozen=True)
class TimedSample:
    time: float
    counts: int
    saturated: bool = False


@dataclass(frozen=True)
class TransitionWindow:
    k: float
    start: float
    rise: float
    fall: float
    end: float
    rise_end: object = None
    fall_end: object = None
    acceleration: float = 0.0
    low_velocity: float = 0.0
    high_velocity: float = 0.0


@dataclass(frozen=True)
class PulseGroup:
    transitions: tuple


@dataclass(frozen=True)
class CaptureQuality:
    coverage: float
    max_gap: float
    timing_residual: float


@dataclass(frozen=True)
class DirectReadCapture:
    messages: tuple
    print_start: float
    requested_responses: int
    issues: tuple = ()
    rejected_messages: tuple = ()
    operation_errors: tuple = ()

    @property
    def received_responses(self):
        return len(self.messages)

    @property
    def complete(self):
        return (
            self.received_responses == self.requested_responses
            and not self.issues
        )


@dataclass(frozen=True)
class CycleMetrics:
    k: float
    amplitude: float
    noise: float
    rise_delay: float
    fall_delay: float
    overshoot: float
    undershoot: float
    settling_error: float
    saturated: bool = False
    tracking_error: float = 0.0
    fall_signed_area: float = 0.0
    recovery_error: float = 0.0
    plateau_slope: float = 0.0
    acceleration: float = 0.0
    low_velocity: float = 0.0
    high_velocity: float = 0.0
    polarity: int = 1


@dataclass(frozen=True)
class SelectionResult:
    value: object = None
    reason: object = None
    profile_values: tuple = ()

    @property
    def successful(self):
        return self.value is not None and self.reason is None


@dataclass(frozen=True)
class FilamentState:
    source: str
    sensor_enabled: bool
    slot: object = None


@dataclass(frozen=True)
class NozzleResourcePlan:
    nozzle: float
    hardware_validated: bool
    low_velocity: float = 0.0
    high_velocity: float = 0.0
    acceleration: float = 0.0
    high_time: float = 0.0
    lead_time: float = 0.0
    pulse_count: int = 0
    max_group_pulses: int = 2
    max_pulse_distance: float = 0.0
    max_total_distance: float = 0.0
    max_duration: float = 0.0


@dataclass(frozen=True)
class CalibrationPreflightResult:
    temperature: float
    nozzle: float
    filament: FilamentState
    plan: NozzleResourcePlan
    sensor_adapter: object
    trapq_adapter: object


NOZZLE_RESOURCE_PLANS = {
    nozzle: NozzleResourcePlan(nozzle=nozzle, hardware_validated=False)
    for nozzle in SUPPORTED_NOZZLES
}


@dataclass(frozen=True)
class CalibrationRunResult:
    selection: SelectionResult
    captures: tuple


class CalibrationStateMachine:
    """Software-only orchestration; the physical backend remains disabled."""

    def __init__(self, backend):
        self.backend = backend
        self.state = "new"
        self._cleanup_complete = False

    def run(self, prepared, groups):
        if self.state != "new":
            raise CalibrationError("CALIBRATION_SESSION_ALREADY_USED")
        groups = self._validate_groups(prepared, groups)
        captures = []
        active_capture = None
        extruded = False
        state_captured = False
        primary_error = None
        selection = None
        try:
            self.state = "snapshotting"
            self.backend.capture_temporary_state()
            state_captured = True
            self.state = "homing"
            self.backend.home_all()
            self.state = "lowering_bed"
            self.backend.move_absolute_z(200.0)
            self.state = "parking"
            self.backend.park_at_trash()
            self.state = "heating"
            self.backend.heat_and_wait(prepared.temperature)

            for index, group in enumerate(groups):
                self.state = "capturing"
                active_capture = self.backend.start_capture(group, prepared)
                try:
                    for transition in group.transitions:
                        self.state = "pulsing"
                        extruded = True
                        self.backend.queue_measured_pulse(transition, prepared)
                    self.backend.wait_for_owned_work()
                finally:
                    capture = self.backend.finish_capture(active_capture)
                    active_capture = None
                    captures.append(capture)
                if index + 1 < len(groups):
                    self.state = "clearing"
                    self.backend.clear_flush()
                    self.backend.park_at_trash()
                    self.backend.wait_for_sensor_settling()

            self.state = "analyzing"
            selection = self.backend.analyze(tuple(captures), groups, prepared)
            if not isinstance(selection, SelectionResult):
                raise CalibrationError("ANALYSIS_INTERFACE_FAILED")
            if not selection.successful:
                raise CalibrationError(selection.reason or "ANALYSIS_INCONCLUSIVE")
        except Exception as exc:
            primary_error = exc
        cleanup_errors = self._cleanup(
            active_capture, extruded, state_captured
        )
        if primary_error is not None or cleanup_errors:
            reason = (
                "CALIBRATION_CLEANUP_FAILED"
                if cleanup_errors
                else _calibration_reason(primary_error)
            )
            self.state = "failed"
            raise CalibrationRunError(
                reason,
                cause=primary_error,
                cleanup_errors=cleanup_errors,
            ) from primary_error
        self.state = "done"
        return CalibrationRunResult(selection=selection, captures=tuple(captures))

    def _validate_groups(self, prepared, groups):
        groups = tuple(groups)
        if not groups:
            raise CalibrationError("EMPTY_PULSE_PLAN")
        transitions = []
        for group in groups:
            if (
                not isinstance(group, PulseGroup)
                or not 1 <= len(group.transitions) <= prepared.plan.max_group_pulses
            ):
                raise CalibrationError("INVALID_PULSE_GROUP")
            transitions.extend(group.transitions)
        if len(transitions) != prepared.plan.pulse_count:
            raise CalibrationError("INVALID_PULSE_COUNT")
        regrouped = group_transitions(
            transitions, max_pulses=prepared.plan.max_group_pulses
        )
        if regrouped != groups:
            raise CalibrationError("INVALID_PULSE_GROUPING")
        return groups

    def _cleanup(self, active_capture, extruded, state_captured=True):
        if self._cleanup_complete:
            return ()
        self._cleanup_complete = True
        self.state = "cleaning"
        errors = []

        def attempt(name, operation):
            try:
                operation()
            except Exception as exc:
                errors.append((name, type(exc).__name__))

        if active_capture is not None:
            attempt(
                "capture",
                lambda: self.backend.abort_capture(active_capture),
            )
        attempt("trapq", self.backend.finalize_owned_work)
        if state_captured:
            attempt("restore", self.backend.restore_temporary_state)
        if extruded:
            try:
                motion_safe = self.backend.motion_is_safe()
            except Exception as exc:
                errors.append(("motion_safety", type(exc).__name__))
                motion_safe = False
            if motion_safe:
                attempt("clear_ooze", self.backend.clear_ooze)
                attempt("clear_flush", self.backend.clear_flush)
            else:
                errors.append(("motion_safety", "UnsafeMotion"))
                attempt(
                    "manual_cleanup",
                    self.backend.note_manual_chute_cleanup_required,
                )
        return tuple(errors)


@dataclass(frozen=True)
class StationaryPulsePlan:
    moves: tuple
    transition: TransitionWindow
    end_time: float
    end_e: float


@dataclass(frozen=True)
class TrapqMove:
    print_time: float
    accel_time: float
    cruise_time: float
    decel_time: float
    start_e: float
    start_velocity: float
    cruise_velocity: float
    acceleration: float

    @property
    def distance(self):
        accel_distance = (
            self.start_velocity * self.accel_time
            + 0.5 * self.acceleration * self.accel_time * self.accel_time
        )
        cruise_distance = self.cruise_velocity * self.cruise_time
        decel_distance = (
            self.cruise_velocity * self.decel_time
            - 0.5 * self.acceleration * self.decel_time * self.decel_time
        )
        return accel_distance + cruise_distance + decel_distance

    def append(self, extruder):
        extruder.trapq_append(
            extruder.get_trapq(),
            self.print_time,
            self.accel_time,
            self.cruise_time,
            self.decel_time,
            self.start_e,
            0.0,
            0.0,
            1.0,
            1.0,  # Klipper's extruder PA eligibility field.
            0.0,
            self.start_velocity,
            self.cruise_velocity,
            self.acceleration,
        )


def decode_cs1237_payload(payload):
    """Decode the four-byte CS1237 framing assumed by deterministic fixtures."""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) % 4:
        raise CalibrationError("CS1237 payload length is not divisible by four")
    result = []
    for offset in range(0, len(payload), 4):
        raw = payload[offset] | payload[offset + 1] << 8 | payload[offset + 2] << 16
        if raw & 0x800000:
            raw -= 0x1000000
        result.append(raw)
    return result


def reconstruct_samples(batches, sample_rate=SAMPLE_RATE, gap_factor=3.0):
    if sample_rate <= 0:
        raise CalibrationError("sample_rate must be positive")
    period = 1.0 / sample_rate
    samples = []
    gaps = []
    previous_time = None
    for batch in sorted(batches, key=lambda item: item.receive_time):
        counts = decode_cs1237_payload(batch.payload)
        if not counts:
            continue
        first_time = batch.receive_time - period * (len(counts) - 1)
        for index, value in enumerate(counts):
            sample_time = first_time + period * index
            if previous_time is not None and sample_time - previous_time > period * gap_factor:
                gaps.append((previous_time, sample_time))
            samples.append(
                TimedSample(
                    time=sample_time,
                    counts=value,
                    saturated=value in (ADC_MIN, ADC_MAX),
                )
            )
            previous_time = sample_time
    return samples, tuple(gaps)


def exclude_intervals(samples, intervals):
    return [
        sample
        for sample in samples
        if not any(start <= sample.time <= end for start, end in intervals)
    ]


def group_transitions(windows, max_pulses=2):
    if not isinstance(max_pulses, int) or max_pulses < 1:
        raise CalibrationError("max_pulses must be a positive integer")
    ordered = sorted(windows, key=lambda item: item.start)
    if any(current.end > following.start for current, following in zip(ordered, ordered[1:])):
        raise CalibrationError("transition windows overlap")
    return tuple(
        PulseGroup(tuple(ordered[offset : offset + max_pulses]))
        for offset in range(0, len(ordered), max_pulses)
    )


def schedule_timing_residual(samples, windows):
    if not samples or not windows:
        raise CalibrationError("schedule alignment requires samples and transitions")
    sample_times = [sample.time for sample in samples]
    transitions = [value for window in windows for value in (window.rise, window.fall)]
    return max(min(abs(sample_time - transition) for sample_time in sample_times) for transition in transitions)


def build_bounded_k_values(start, stop, step, max_values):
    if step <= 0 or stop <= start or not isinstance(max_values, int) or max_values < 2:
        raise CalibrationError("invalid K range")
    count = int(round((stop - start) / step)) + 1
    if count > max_values or abs(start + step * (count - 1) - stop) > 1.0e-9:
        raise CalibrationError("K range is not exactly bounded")
    return tuple(round(start + step * index, 9) for index in range(count))


def assess_capture_quality(
    samples,
    start,
    end,
    gaps=(),
    timing_residual=0.0,
    sample_rate=SAMPLE_RATE,
):
    if end <= start or sample_rate <= 0 or timing_residual < 0:
        raise CalibrationError("invalid capture quality inputs")
    expected = max(1, int(round((end - start) * sample_rate)) + 1)
    observed = sum(1 for sample in samples if start <= sample.time <= end)
    relevant_gaps = [
        gap_end - gap_start
        for gap_start, gap_end in gaps
        if gap_end >= start and gap_start <= end
    ]
    return CaptureQuality(
        coverage=min(1.0, float(observed) / expected),
        max_gap=max(relevant_gaps) if relevant_gaps else 0.0,
        timing_residual=timing_residual,
    )


def analyze_cycle(samples, window):
    _validate_transition_excitation(window)
    rise_end = window.rise if window.rise_end is None else window.rise_end
    fall_end = window.fall if window.fall_end is None else window.fall_end
    if not (
        window.start < window.rise <= rise_end < window.fall <= fall_end < window.end
    ):
        raise CalibrationError("transition window is not ordered")
    ordered_samples = sorted(
        (
            sample
            for sample in samples
            if window.start <= sample.time < window.end
        ),
        key=lambda sample: sample.time,
    )
    before = _values(ordered_samples, window.start, window.rise)
    high = _values(ordered_samples, rise_end, window.fall)
    after = _values(ordered_samples, window.fall, window.end)
    if min(len(before), len(high), len(after)) < 4:
        raise CalibrationError("insufficient cycle coverage")

    before_level = statistics.median(before[len(before) // 2 :])
    high_level = statistics.median(high[len(high) // 2 :])
    signed_amplitude = high_level - before_level
    amplitude = abs(signed_amplitude)
    if amplitude == 0:
        raise CalibrationError("force response has zero amplitude")
    polarity = 1 if signed_amplitude > 0 else -1
    normalized = [
        (
            sample.time,
            (sample.counts - before_level) * polarity / amplitude,
            _expected_flow_fraction(sample.time, window, rise_end, fall_end),
        )
        for sample in ordered_samples
    ]
    transition_residuals = [
        (sample_time, observed - expected)
        for sample_time, observed, expected in normalized
        if window.rise <= sample_time < fall_end
    ]
    fall_residuals = [
        (sample_time, observed - expected)
        for sample_time, observed, expected in normalized
        if window.fall <= sample_time < window.end
    ]
    post_decel_residuals = [
        (sample_time, observed - expected)
        for sample_time, observed, expected in normalized
        if fall_end <= sample_time < window.end
    ]
    if min(
        len(transition_residuals),
        len(fall_residuals),
        len(post_decel_residuals),
    ) < 4:
        raise CalibrationError("insufficient cycle coverage")

    after_level = statistics.median(after[len(after) // 2 :])
    rise_delay = _crossing_delay(
        ordered_samples,
        window.rise,
        window.fall,
        before_level,
        high_level,
        0.9,
    )
    fall_delay = _crossing_delay(
        ordered_samples,
        window.fall,
        window.end,
        high_level,
        after_level,
        0.1,
    )
    noise = _median_absolute_deviation(before) / amplitude
    overshoot = max(0.0, max(value for unused_time, value in transition_residuals))
    undershoot = max(0.0, -min(value for unused_time, value in fall_residuals))
    tracking_error = _time_average_absolute(transition_residuals)
    fall_signed_area = _time_integral(post_decel_residuals)
    recovery_error = _time_average_absolute(post_decel_residuals)
    plateau = [
        (sample_time, observed)
        for sample_time, observed, unused_expected in normalized
        if rise_end <= sample_time < window.fall
    ]
    plateau_slope = _linear_slope(plateau[len(plateau) // 2 :])
    settling = statistics.fmean(
        (
            tracking_error,
            recovery_error,
            abs(plateau_slope) * max(window.fall - rise_end, 0.0),
        )
    )
    saturated = any(sample.saturated for sample in ordered_samples)
    return CycleMetrics(
        k=window.k,
        amplitude=amplitude,
        noise=noise,
        rise_delay=rise_delay,
        fall_delay=fall_delay,
        overshoot=overshoot,
        undershoot=undershoot,
        settling_error=settling,
        saturated=saturated,
        tracking_error=tracking_error,
        fall_signed_area=fall_signed_area,
        recovery_error=recovery_error,
        plateau_slope=plateau_slope,
        acceleration=window.acceleration,
        low_velocity=window.low_velocity,
        high_velocity=window.high_velocity,
        polarity=polarity,
    )


def select_pa_value(
    metrics,
    quality=None,
    min_amplitude=1.0,
    max_noise=0.20,
    max_repeatability=0.04,
    max_area_repeatability=0.02,
    area_deadband=0.005,
    min_objective_separation=0.05,
    min_acceleration_profiles=2,
    min_coverage=0.95,
    max_gap=0.005,
    max_timing_residual=0.003,
):
    if quality is not None:
        if quality.coverage < min_coverage:
            return SelectionResult(reason="INSUFFICIENT_COVERAGE")
        if quality.max_gap > max_gap:
            return SelectionResult(reason="SAMPLE_GAP")
        if quality.timing_residual > max_timing_residual:
            return SelectionResult(reason="TIMING_MISALIGNMENT")
    metrics = tuple(metrics)
    if not metrics:
        return SelectionResult(reason="NO_DATA")
    if any(item.saturated for item in metrics):
        return SelectionResult(reason="SATURATED")
    if any(item.amplitude < min_amplitude or item.noise > max_noise for item in metrics):
        return SelectionResult(reason="WEAK_OR_NOISY_SIGNAL")
    if any(not _valid_cycle_metrics(item) for item in metrics):
        return SelectionResult(reason="INVALID_ANALYSIS_DATA")
    if len({item.polarity for item in metrics}) != 1:
        return SelectionResult(reason="INCONSISTENT_POLARITY")

    profiles = {}
    for item in metrics:
        profile = (
            round(item.acceleration, 9),
            round(item.low_velocity, 9),
            round(item.high_velocity, 9),
        )
        profiles.setdefault(profile, {}).setdefault(round(item.k, 9), []).append(
            item
        )
    accelerations = {profile[0] for profile in profiles}
    flow_schedules = {profile[1:] for profile in profiles}
    if len(accelerations) < min_acceleration_profiles:
        return SelectionResult(reason="INCOMPLETE_ACCELERATION_PROFILES")
    if len(flow_schedules) != 1:
        return SelectionResult(reason="INCOMPATIBLE_ACCELERATION_PROFILES")
    k_sets = {tuple(sorted(grouped)) for grouped in profiles.values()}
    if len(k_sets) != 1:
        return SelectionResult(reason="INCOMPLETE_PROFILE_COVERAGE")
    ordered = next(iter(k_sets))
    if len(ordered) < 3:
        return SelectionResult(reason="INSUFFICIENT_K_RANGE")

    profile_values = []
    for profile in sorted(profiles):
        grouped = profiles[profile]
        aggregated = []
        for k in ordered:
            cycles = grouped[k]
            if len(cycles) < 2:
                return SelectionResult(reason="INSUFFICIENT_CORROBORATION")
            if not _cycles_are_repeatable(
                cycles, max_repeatability, max_area_repeatability
            ):
                return SelectionResult(reason="INCONSISTENT_CYCLES")
            aggregated.append(_median_cycle_metrics(cycles))
        result = _select_profile_candidate(
            aggregated,
            area_deadband=area_deadband,
            min_objective_separation=min_objective_separation,
            max_area_repeatability=max_area_repeatability,
        )
        if not result.successful:
            return result
        profile_values.append(result.value)
    if len(set(profile_values)) != 1:
        return SelectionResult(
            reason="ACCELERATION_CANDIDATE_DISAGREEMENT",
            profile_values=tuple(profile_values),
        )
    return SelectionResult(
        value=profile_values[0],
        profile_values=tuple(profile_values),
    )


def _validate_transition_excitation(window):
    has_shape = window.rise_end is not None or window.fall_end is not None
    if not has_shape:
        return
    if (
        window.rise_end is None
        or window.fall_end is None
        or not all(
            _finite_number(value)
            for value in (
                window.rise_end,
                window.fall_end,
                window.acceleration,
                window.low_velocity,
                window.high_velocity,
            )
        )
        or window.acceleration <= 0.0
        or window.high_velocity <= window.low_velocity
    ):
        raise CalibrationError("invalid transition excitation")
    expected_ramp = (
        window.high_velocity - window.low_velocity
    ) / window.acceleration
    if (
        abs((window.rise_end - window.rise) - expected_ramp) > 1.0e-9
        or abs((window.fall_end - window.fall) - expected_ramp) > 1.0e-9
    ):
        raise CalibrationError("transition acceleration does not match ramp timing")


def _expected_flow_fraction(sample_time, window, rise_end, fall_end):
    if window.rise_end is None or window.fall_end is None:
        return 1.0 if window.rise <= sample_time < window.fall else 0.0
    if sample_time < window.rise:
        return 0.0
    if sample_time < rise_end:
        return (sample_time - window.rise) / (rise_end - window.rise)
    if sample_time < window.fall:
        return 1.0
    if sample_time < fall_end:
        return 1.0 - (sample_time - window.fall) / (fall_end - window.fall)
    return 0.0


def _time_integral(values):
    if len(values) < 2:
        raise CalibrationError("insufficient integration coverage")
    return sum(
        (previous_value + current_value)
        * 0.5
        * (current_time - previous_time)
        for (previous_time, previous_value), (current_time, current_value) in zip(
            values, values[1:]
        )
    )


def _time_average_absolute(values):
    duration = values[-1][0] - values[0][0]
    if duration <= 0.0:
        raise CalibrationError("invalid integration duration")
    absolute = [(sample_time, abs(value)) for sample_time, value in values]
    return _time_integral(absolute) / duration


def _linear_slope(values):
    if len(values) < 2:
        raise CalibrationError("insufficient slope coverage")
    center_time = statistics.fmean(item[0] for item in values)
    center_value = statistics.fmean(item[1] for item in values)
    denominator = sum((sample_time - center_time) ** 2 for sample_time, unused in values)
    if denominator <= 0.0:
        raise CalibrationError("invalid slope coverage")
    return sum(
        (sample_time - center_time) * (value - center_value)
        for sample_time, value in values
    ) / denominator


def _valid_cycle_metrics(item):
    numeric = (
        item.k,
        item.amplitude,
        item.noise,
        item.rise_delay,
        item.fall_delay,
        item.overshoot,
        item.undershoot,
        item.settling_error,
        item.tracking_error,
        item.fall_signed_area,
        item.recovery_error,
        item.plateau_slope,
        item.acceleration,
        item.low_velocity,
        item.high_velocity,
    )
    return (
        all(_finite_number(value) for value in numeric)
        and item.k >= 0.0
        and item.amplitude > 0.0
        and item.noise >= 0.0
        and item.rise_delay >= 0.0
        and item.fall_delay >= 0.0
        and item.overshoot >= 0.0
        and item.undershoot >= 0.0
        and item.settling_error >= 0.0
        and item.tracking_error >= 0.0
        and item.recovery_error >= 0.0
        and item.acceleration > 0.0
        and item.low_velocity > 0.0
        and item.high_velocity > item.low_velocity
        and item.polarity in (-1, 1)
    )


def _cycles_are_repeatable(cycles, max_repeatability, max_area_repeatability):
    normalized_fields = (
        "rise_delay",
        "fall_delay",
        "overshoot",
        "undershoot",
        "settling_error",
        "tracking_error",
        "recovery_error",
        "plateau_slope",
    )
    if any(
        max(getattr(item, field) for item in cycles)
        - min(getattr(item, field) for item in cycles)
        > max_repeatability
        for field in normalized_fields
    ):
        return False
    areas = [item.fall_signed_area for item in cycles]
    return max(areas) - min(areas) <= max_area_repeatability


def _median_cycle_metrics(cycles):
    def median(field):
        return statistics.median(getattr(item, field) for item in cycles)

    return CycleMetrics(
        k=cycles[0].k,
        amplitude=median("amplitude"),
        noise=median("noise"),
        rise_delay=median("rise_delay"),
        fall_delay=median("fall_delay"),
        overshoot=median("overshoot"),
        undershoot=median("undershoot"),
        settling_error=median("settling_error"),
        saturated=False,
        tracking_error=median("tracking_error"),
        fall_signed_area=median("fall_signed_area"),
        recovery_error=median("recovery_error"),
        plateau_slope=median("plateau_slope"),
        acceleration=cycles[0].acceleration,
        low_velocity=cycles[0].low_velocity,
        high_velocity=cycles[0].high_velocity,
        polarity=cycles[0].polarity,
    )


def _select_profile_candidate(
    metrics,
    *,
    area_deadband,
    min_objective_separation,
    max_area_repeatability,
):
    ordered = sorted(metrics, key=lambda item: item.k)
    areas = [item.fall_signed_area for item in ordered]
    if any(
        current > previous + max_area_repeatability
        for previous, current in zip(areas, areas[1:])
    ):
        return SelectionResult(reason="NON_MONOTONIC_RESPONSE")
    positive_indices = [
        index for index, area in enumerate(areas) if area > area_deadband
    ]
    negative_indices = [
        index for index, area in enumerate(areas) if area < -area_deadband
    ]

    costs = _composite_costs(ordered)
    candidate_index = min(range(len(ordered)), key=lambda index: costs[index])
    cost_span = max(costs) - min(costs)
    second_cost = sorted(costs)[1]
    if (
        cost_span <= 1.0e-12
        or (second_cost - costs[candidate_index]) / cost_span
        < min_objective_separation
    ):
        return SelectionResult(reason="COMPOSITE_OBJECTIVE_AMBIGUOUS")
    if candidate_index in (0, len(ordered) - 1):
        return SelectionResult(reason="K_RANGE_NOT_BRACKETED")
    if (
        not positive_indices
        or not negative_indices
        or max(positive_indices) >= min(negative_indices)
    ):
        return SelectionResult(reason="RECOVERY_EVIDENCE_INCONCLUSIVE")

    lower_index = max(positive_indices)
    upper_index = min(negative_indices)
    lower = ordered[lower_index]
    upper = ordered[upper_index]
    zero_fraction = lower.fall_signed_area / (
        lower.fall_signed_area - upper.fall_signed_area
    )
    zero_estimate = lower.k + (upper.k - lower.k) * zero_fraction
    steps = [
        current.k - previous.k for previous, current in zip(ordered, ordered[1:])
    ]
    if min(steps) <= 0.0 or abs(ordered[candidate_index].k - zero_estimate) > max(steps):
        return SelectionResult(reason="RECOVERY_EVIDENCE_INCONCLUSIVE")
    if areas[candidate_index] < -area_deadband:
        return SelectionResult(reason="RECOVERY_EVIDENCE_INCONCLUSIVE")
    return SelectionResult(value=ordered[candidate_index].k)


def _composite_costs(metrics):
    tracking_fields = ("tracking_error", "rise_delay", "fall_delay")
    excessive_fields = ("overshoot", "undershoot")
    recovery_fields = ("settling_error", "recovery_error", "plateau_slope")
    scales = {
        field: _robust_scale(abs(getattr(item, field)) for item in metrics)
        for field in tracking_fields + excessive_fields + recovery_fields
    }

    def component(item, fields):
        return statistics.fmean(
            abs(getattr(item, field)) / scales[field] for field in fields
        )

    return [
        0.45 * component(item, tracking_fields)
        + 0.35 * component(item, excessive_fields)
        + 0.20 * component(item, recovery_fields)
        for item in metrics
    ]


def _robust_scale(values):
    ordered = sorted(values)
    if not ordered:
        return 1.0
    rank = 0.9 * (len(ordered) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    fraction = rank - lower
    scale = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return max(scale, 1.0e-12)


def format_result(value, temperature, nozzle):
    if value is None:
        raise CalibrationError("cannot format an empty PA value")
    return "PA_VALUE=%.6f TEMP=%.1f NOZZLE=%.1f PERSISTED=0" % (
        value,
        temperature,
        nozzle,
    )


def format_failure(reason):
    if (
        not isinstance(reason, str)
        or not reason
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in reason)
        or "PA_VALUE" in reason
    ):
        reason = "CALIBRATION_INTERNAL_ERROR"
    return "PA_CALIBRATION_FAILED REASON=%s" % reason


def execute_and_report(gcmd, machine, prepared, groups):
    try:
        result = machine.run(prepared, groups)
    except Exception as exc:
        message = format_failure(_calibration_reason(exc))
        gcmd.respond_info(message)
        raise gcmd.error(message) from exc
    message = format_result(
        result.selection.value,
        prepared.temperature,
        prepared.nozzle,
    )
    gcmd.respond_info(message)
    return result


def validate_inputs(temperature, nozzle, min_temp=135.0, max_temp=375.0):
    if temperature is None or not min_temp <= temperature <= max_temp:
        raise CalibrationError("INVALID_TEMP")
    if nozzle is None or not any(abs(nozzle - item) < 1.0e-9 for item in SUPPORTED_NOZZLES):
        raise CalibrationError("INVALID_NOZZLE")


def inspect_loaded_filament(printer, eventtime):
    sensor = printer.lookup_object(
        "filament_switch_sensor filament_switch_sensor", None
    )
    sensor_status = _object_status(sensor, eventtime)
    filament_detected = sensor_status.get("filament_detected")
    sensor_enabled = sensor_status.get("enabled")
    if not isinstance(filament_detected, bool) or not isinstance(
        sensor_enabled, bool
    ):
        raise CalibrationError("UNSUPPORTED_FILAMENT_SENSOR")
    if not filament_detected:
        raise CalibrationError("FILAMENT_NOT_LOADED")

    save_variables = printer.lookup_object("save_variables", None)
    saved_status = _object_status(save_variables, eventtime)
    variables = saved_status.get("variables")
    if not isinstance(variables, dict):
        raise CalibrationError("UNSUPPORTED_FILAMENT_STATE")
    enable_box = variables.get("enable_box", 0)
    if type(enable_box) not in (bool, int) or int(enable_box) not in (0, 1):
        raise CalibrationError("UNSUPPORTED_FILAMENT_STATE")
    if not bool(enable_box):
        return FilamentState("external", sensor_enabled)

    controller = printer.lookup_object("multi_color_controller", None)
    controller_status = _object_status(controller, eventtime)
    system = controller_status.get("system")
    hardware = controller_status.get("hardware")
    extruder = controller_status.get("extruder")
    operation = controller_status.get("operation")
    slots = controller_status.get("slots")
    sensors = controller_status.get("sensors")
    if not all(
        isinstance(value, dict)
        for value in (system, hardware, extruder, operation, slots, sensors)
    ):
        raise CalibrationError("UNSUPPORTED_FILAMENT_STATE")
    if system.get("ready") is not True or hardware.get("connected") is not True:
        raise CalibrationError("FILAMENT_SOURCE_UNAVAILABLE")
    if (
        operation.get("current") != -1
        or operation.get("error") is not None
        or operation.get("is_waiting_user") is not False
    ):
        raise CalibrationError("FILAMENT_SOURCE_BUSY")
    if (
        extruder.get("loaded") is not True
        or extruder.get("filament_detected") is not True
        or sensors.get("e_endstop") != 1
    ):
        raise CalibrationError("FILAMENT_STATE_INCONSISTENT")

    box_extras = printer.lookup_object("box_extras", None)
    box_status = _object_status(box_extras, eventtime)
    if box_status.get("e_endstop_state") != 1:
        raise CalibrationError("FILAMENT_STATE_INCONSISTENT")

    slot_sync = variables.get("slot_sync")
    last_load_slot = variables.get("last_load_slot")
    controller_last_loaded = slots.get("last_loaded")
    if (
        not isinstance(slot_sync, str)
        or slot_sync in ("", "slot-1")
        or last_load_slot != slot_sync
        or controller_last_loaded != slot_sync
    ):
        raise CalibrationError("FILAMENT_SOURCE_UNSYNCED")
    if slot_sync == "slot16":
        return FilamentState("external", sensor_enabled, slot_sync)
    if _physical_box_slot_index(slot_sync) is None:
        raise CalibrationError("FILAMENT_SOURCE_UNSYNCED")

    slot_states = slots.get("states")
    if not isinstance(slot_states, dict) or slot_states.get(slot_sync) != 2:
        raise CalibrationError("FILAMENT_STATE_INCONSISTENT")
    return FilamentState("qidi_box", sensor_enabled, slot_sync)


def _physical_box_slot_index(slot):
    if not isinstance(slot, str) or not slot.startswith("slot"):
        return None
    suffix = slot[4:]
    if not suffix.isdigit():
        return None
    index = int(suffix)
    return index if 0 <= index <= 15 else None


def validate_calibration_preflight(
    printer,
    eventtime,
    temperature,
    nozzle,
    plans=None,
    sensor_adapter_factory=None,
    trapq_adapter_factory=None,
):
    validate_inputs(temperature, nozzle)
    _require_idle_print_state(printer, eventtime)
    _require_calibration_commands(printer, eventtime)
    filament = inspect_loaded_filament(printer, eventtime)

    plan_map = NOZZLE_RESOURCE_PLANS if plans is None else plans
    plan = plan_map.get(nozzle) if isinstance(plan_map, dict) else None
    _validate_nozzle_resource_plan(plan, nozzle)

    sensor_factory = sensor_adapter_factory or QidiCS1237Adapter
    trapq_factory = trapq_adapter_factory or QidiDirectTrapqAdapter
    sensor_adapter = sensor_factory(printer)
    trapq_adapter = trapq_factory(printer)
    trapq_adapter.validate_plan(plan, temperature)
    sensor_adapter.validate_configuration()
    return CalibrationPreflightResult(
        temperature=temperature,
        nozzle=nozzle,
        filament=filament,
        plan=plan,
        sensor_adapter=sensor_adapter,
        trapq_adapter=trapq_adapter,
    )


def _require_idle_print_state(printer, eventtime):
    print_stats = printer.lookup_object("print_stats", None)
    status = _object_status(
        print_stats, eventtime, "CALIBRATION_REQUIRES_IDLE_PRINTER"
    )
    state = status.get("state")
    if state not in SAFE_PRINT_STATES:
        raise CalibrationError("CALIBRATION_REQUIRES_IDLE_PRINTER")
    idle_timeout = printer.lookup_object("idle_timeout", None)
    idle_status = _object_status(
        idle_timeout, eventtime, "CALIBRATION_REQUIRES_IDLE_PRINTER"
    )
    if idle_status.get("state") not in SAFE_IDLE_TIMEOUT_STATES:
        raise CalibrationError("CALIBRATION_REQUIRES_IDLE_PRINTER")
    return state


def _require_calibration_commands(printer, eventtime):
    gcode = printer.lookup_object("gcode", None)
    status = _object_status(gcode, eventtime, "UNSUPPORTED_GCODE_INTERFACE")
    commands = status.get("commands")
    if not isinstance(commands, dict):
        raise CalibrationError("UNSUPPORTED_GCODE_INTERFACE")
    available = {str(name).upper() for name in commands}
    missing = REQUIRED_CALIBRATION_COMMANDS - available
    if missing:
        raise CalibrationError("CALIBRATION_COMMAND_UNAVAILABLE")


def _validate_nozzle_resource_plan(plan, nozzle):
    if not isinstance(plan, NozzleResourcePlan) or plan.nozzle != nozzle:
        raise CalibrationError("NOZZLE_PLAN_UNAVAILABLE")
    if not plan.hardware_validated:
        raise CalibrationError("NOZZLE_PLAN_UNVALIDATED")
    numeric = (
        plan.low_velocity,
        plan.high_velocity,
        plan.acceleration,
        plan.high_time,
        plan.lead_time,
        plan.max_pulse_distance,
        plan.max_total_distance,
        plan.max_duration,
    )
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0.0
        for value in numeric
    ):
        raise CalibrationError("INVALID_NOZZLE_PLAN")
    if (
        plan.high_velocity <= plan.low_velocity
        or not isinstance(plan.pulse_count, int)
        or isinstance(plan.pulse_count, bool)
        or plan.pulse_count < 1
        or plan.max_group_pulses not in (1, 2)
    ):
        raise CalibrationError("INVALID_NOZZLE_PLAN")
    pulse = build_trapq_pulse(
        0.0,
        0.0,
        plan.low_velocity,
        plan.high_velocity,
        plan.acceleration,
        plan.high_time,
    )
    pulse_distance = pulse.distance + 2.0 * plan.low_velocity * plan.lead_time
    pulse_duration = (
        pulse.accel_time
        + pulse.cruise_time
        + pulse.decel_time
        + 2.0 * plan.lead_time
    )
    if (
        pulse_distance > plan.max_pulse_distance
        or pulse_distance * plan.pulse_count > plan.max_total_distance
        or pulse_duration * plan.pulse_count > plan.max_duration
        or pulse_duration * plan.max_group_pulses > MAX_CAPTURE_DURATION
    ):
        raise CalibrationError("NOZZLE_PLAN_RESOURCE_LIMIT")


def build_stationary_pulse_plan(print_time, start_e, k, plan):
    _validate_nozzle_resource_plan(plan, plan.nozzle)
    if not all(_finite_number(value) for value in (print_time, start_e, k)):
        raise CalibrationError("INVALID_PULSE_PLAN")
    if print_time < 0.0 or k < 0.0:
        raise CalibrationError("INVALID_PULSE_PLAN")

    lead_in = TrapqMove(
        print_time=print_time,
        accel_time=0.0,
        cruise_time=plan.lead_time,
        decel_time=0.0,
        start_e=start_e,
        start_velocity=plan.low_velocity,
        cruise_velocity=plan.low_velocity,
        acceleration=0.0,
    )
    transition_time = print_time + plan.lead_time
    transition_e = start_e + lead_in.distance
    transition = build_trapq_pulse(
        transition_time,
        transition_e,
        plan.low_velocity,
        plan.high_velocity,
        plan.acceleration,
        plan.high_time,
    )
    transition_duration = (
        transition.accel_time + transition.cruise_time + transition.decel_time
    )
    lead_out_time = transition_time + transition_duration
    lead_out_e = transition_e + transition.distance
    lead_out = TrapqMove(
        print_time=lead_out_time,
        accel_time=0.0,
        cruise_time=plan.lead_time,
        decel_time=0.0,
        start_e=lead_out_e,
        start_velocity=plan.low_velocity,
        cruise_velocity=plan.low_velocity,
        acceleration=0.0,
    )
    end_time = lead_out_time + plan.lead_time
    end_e = lead_out_e + lead_out.distance
    return StationaryPulsePlan(
        moves=(lead_in, transition, lead_out),
        transition=TransitionWindow(
            k=k,
            start=print_time,
            rise=transition_time,
            rise_end=transition_time + transition.accel_time,
            fall=transition_time + transition.accel_time + transition.cruise_time,
            fall_end=lead_out_time,
            end=end_time,
            acceleration=plan.acceleration,
            low_velocity=plan.low_velocity,
            high_velocity=plan.high_velocity,
        ),
        end_time=end_time,
        end_e=end_e,
    )


def build_trapq_pulse(print_time, start_e, low_velocity, high_velocity, acceleration, high_time):
    if not all(
        _finite_number(value)
        for value in (
            print_time,
            start_e,
            low_velocity,
            high_velocity,
            acceleration,
            high_time,
        )
    ):
        raise CalibrationError("trapq pulse values must be finite")
    if print_time < 0.0 or min(low_velocity, high_velocity, acceleration, high_time) <= 0:
        raise CalibrationError("trapq pulse values must be positive")
    if high_velocity <= low_velocity:
        raise CalibrationError("high velocity must exceed low velocity")
    accel_time = (high_velocity - low_velocity) / acceleration
    move = TrapqMove(
        print_time=print_time,
        accel_time=accel_time,
        cruise_time=high_time,
        decel_time=accel_time,
        start_e=start_e,
        start_velocity=low_velocity,
        cruise_velocity=high_velocity,
        acceleration=acceleration,
    )
    if move.distance <= 0:
        raise CalibrationError("trapq pulse distance must be positive")
    return move


class QidiDirectTrapqAdapter:
    """Fail-closed validator for QIDI's private direct-extruder trapq ABI."""

    def __init__(self, printer, hash_provider=None):
        self.toolhead = printer.lookup_object("toolhead", None)
        get_extruder = getattr(self.toolhead, "get_extruder", None)
        if not callable(get_extruder):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        self.extruder = get_extruder()
        self.extruder_stepper = getattr(self.extruder, "extruder_stepper", None)
        required_toolhead = (
            "commanded_pos",
            "e_enable",
            "e_accumulator",
            "flush_step_generation",
            "note_mcu_movequeue_activity",
            "_advance_move_time",
            "kin_flush_delay",
        )
        required_extruder = (
            "last_position",
            "max_e_velocity",
            "max_e_accel",
            "max_e_dist",
            "heater",
            "get_name",
            "get_trapq",
            "trapq_append",
            "trapq_finalize_moves",
        )
        required_extruder_stepper = (
            "pressure_advance",
            "pressure_advance_smooth_time",
            "stepper",
            "_set_pressure_advance",
        )
        if self.toolhead is None or any(
            not hasattr(self.toolhead, name) for name in required_toolhead
        ):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        if self.extruder is None or any(
            not hasattr(self.extruder, name) for name in required_extruder
        ):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        if self.extruder_stepper is None or any(
            not hasattr(self.extruder_stepper, name)
            for name in required_extruder_stepper
        ):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        if any(
            not callable(getattr(self.toolhead, name))
            for name in (
                "flush_step_generation",
                "note_mcu_movequeue_activity",
                "_advance_move_time",
            )
        ) or any(
            not callable(getattr(self.extruder, name))
            for name in (
                "get_name",
                "get_trapq",
                "trapq_append",
                "trapq_finalize_moves",
            )
        ) or not callable(self.extruder_stepper._set_pressure_advance):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        get_stepper_trapq = getattr(
            self.extruder_stepper.stepper, "get_trapq", None
        )
        if (
            self.extruder.get_name() != "extruder"
            or not callable(get_stepper_trapq)
            or get_stepper_trapq() is not self.extruder.get_trapq()
        ):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        provider = hash_provider or _runtime_trapq_hashes
        if provider(self.toolhead, self.extruder) != EXPECTED_TRAPQ_RUNTIME_HASHES:
            raise CalibrationError("UNSUPPORTED_TRAPQ_RUNTIME")
        self._validate_nominal_state()

    def _validate_nominal_state(self):
        position = self.toolhead.commanded_pos
        if (
            not isinstance(position, (list, tuple))
            or len(position) != 4
            or any(not _finite_number(value) for value in position)
            or not _finite_number(self.extruder.last_position)
            or abs(position[3] - self.extruder.last_position) > 1.0e-9
            or self.toolhead.e_enable is not False
            or not _finite_number(self.toolhead.e_accumulator)
            or abs(self.toolhead.e_accumulator) > 1.0e-12
            or not _finite_number(self.toolhead.kin_flush_delay)
            or self.toolhead.kin_flush_delay < 0.0
        ):
            raise CalibrationError("UNSUPPORTED_TRAPQ_STATE")

    def validate_plan(self, plan, temperature):
        self._validate_nominal_state()
        heater = self.extruder.heater
        limits = (
            getattr(heater, "min_extrude_temp", None),
            getattr(heater, "max_temp", None),
            self.extruder.max_e_velocity,
            self.extruder.max_e_accel,
            self.extruder.max_e_dist,
            self.extruder_stepper.pressure_advance,
            self.extruder_stepper.pressure_advance_smooth_time,
        )
        if any(not _finite_number(value) for value in limits):
            raise CalibrationError("UNSUPPORTED_TRAPQ_INTERFACE")
        min_extrude_temp, max_temp = limits[:2]
        if temperature < min_extrude_temp or temperature > max_temp:
            raise CalibrationError("INVALID_TEMP")
        if (
            plan.high_velocity > self.extruder.max_e_velocity
            or plan.acceleration > self.extruder.max_e_accel
            or plan.max_pulse_distance > self.extruder.max_e_dist
            or plan.lead_time
            < self.extruder_stepper.pressure_advance_smooth_time * 0.5
        ):
            raise CalibrationError("NOZZLE_PLAN_EXCEEDS_EXTRUDER_LIMIT")


def _runtime_trapq_hashes(toolhead, extruder):
    toolhead_file = _class_source_file(toolhead)
    extruder_file = _class_source_file(extruder)
    chelper_module = sys.modules.get("chelper")
    if chelper_module is None:
        try:
            chelper_module = __import__("chelper")
        except Exception as exc:
            raise CalibrationError("UNSUPPORTED_TRAPQ_RUNTIME") from exc
    chelper_file = getattr(chelper_module, "__file__", None)
    if not all(isinstance(path, str) for path in (toolhead_file, extruder_file, chelper_file)):
        raise CalibrationError("UNSUPPORTED_TRAPQ_RUNTIME")
    chelper_dir = os.path.dirname(os.path.realpath(chelper_file))
    paths = {
        "extruder.py": extruder_file,
        "toolhead.py": toolhead_file,
        "chelper/__init__.py": chelper_file,
        "trapq.c": os.path.join(chelper_dir, "trapq.c"),
        "kin_extruder.c": os.path.join(chelper_dir, "kin_extruder.c"),
        "c_helper.so": os.path.join(chelper_dir, "c_helper.so"),
    }
    try:
        return {name: _sha256_file(path) for name, path in paths.items()}
    except (OSError, ValueError) as exc:
        raise CalibrationError("UNSUPPORTED_TRAPQ_RUNTIME") from exc


def _class_source_file(value):
    path = inspect.getsourcefile(type(value)) or inspect.getfile(type(value))
    return os.path.realpath(path) if isinstance(path, str) else path


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class QidiCS1237Adapter:
    """Narrow adapter around the private QIDI probe_air sensor helper."""

    READ_COMMAND = "query_cs1237_read oid=%c reg=%u read_len=%u"
    RESPONSE_NAME = "query_cs1237_data"

    def __init__(self, printer):
        probe_air = printer.lookup_object("probe_air", None)
        sensor = getattr(probe_air, "sensor_helper", None)
        required = (
            "query_cs1237_config_read_cmd",
            "query_cs1237_home_state_cmd",
            "mcu",
            "oid",
        )
        if sensor is None or any(not hasattr(sensor, name) for name in required):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        if not isinstance(sensor.oid, int) or sensor.oid < 0:
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        mcu_required = (
            "_serial",
            "estimated_print_time",
            "lookup_command",
            "print_time_to_clock",
            "register_response",
        )
        if any(not hasattr(sensor.mcu, name) for name in mcu_required):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        handlers = getattr(sensor.mcu._serial, "handlers", None)
        if not isinstance(handlers, dict):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        config_send = getattr(sensor.query_cs1237_config_read_cmd, "send", None)
        home_state_send = getattr(sensor.query_cs1237_home_state_cmd, "send", None)
        if not callable(config_send) or not callable(home_state_send):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        try:
            read_command = sensor.mcu.lookup_command(self.READ_COMMAND)
        except Exception as exc:
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE") from exc
        if not callable(getattr(read_command, "send", None)):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        self.printer = printer
        self.sensor = sensor
        self.read_command = read_command
        self.active = False

    def validate_configuration(self):
        sensor_id = id(self.sensor)
        response_key = (self.RESPONSE_NAME, self.sensor.oid)
        handlers = self.sensor.mcu._serial.handlers
        if self.active or sensor_id in _ACTIVE_SENSOR_IDS or response_key in handlers:
            raise CalibrationError("SENSOR_BUSY")
        home_state = self.sensor.query_cs1237_home_state_cmd.send(
            [self.sensor.oid]
        )
        homing = home_state.get("homing") if isinstance(home_state, dict) else None
        if type(homing) not in (bool, int) or int(homing) not in (0, 1):
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
        if bool(homing):
            raise CalibrationError("SENSOR_BUSY")
        config = self.sensor.query_cs1237_config_read_cmd.send([self.sensor.oid])
        if (
            not isinstance(config, dict)
            or config.get("oid") != self.sensor.oid
            or config.get("config") != CS1237_CONFIG_1280_SPS
        ):
            raise CalibrationError("UNSUPPORTED_SENSOR_CONFIG")
        return config

    def capture(self, reactor, duration, rate):
        request_count = _bounded_capture_request_count(duration, rate)
        self.validate_configuration()
        sensor_id = id(self.sensor)
        response_key = (self.RESPONSE_NAME, self.sensor.oid)
        handlers = self.sensor.mcu._serial.handlers

        messages = []
        issues = []
        rejected_messages = []
        operation_errors = []
        response_identities = set()

        def reject_response(params, issue):
            issues.append(issue)
            if len(rejected_messages) < request_count:
                rejected_messages.append(_diagnostic_response(params))
            else:
                issues.append("REJECTED_RESPONSE_OVERFLOW")

        def handle_sample(params):
            issue = _validate_direct_read_response(params, self.sensor.oid)
            if issue is not None:
                reject_response(params, issue)
                return
            identity = (params.get("#sent_time"), bytes(params.get("data")))
            if identity in response_identities:
                reject_response(params, "DUPLICATE_RESPONSE_IDENTITY")
                return
            if len(messages) >= request_count:
                reject_response(params, "RESPONSE_OVERFLOW")
                return
            response_identities.add(identity)
            messages.append(dict(params))

        event_start = reactor.monotonic()
        print_start = self.sensor.mcu.estimated_print_time(event_start) + 0.100
        first_clock = self.sensor.mcu.print_time_to_clock(print_start)
        if not isinstance(first_clock, int) or first_clock < 0:
            raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")

        _ACTIVE_SENSOR_IDS.add(sensor_id)
        self.active = True
        registered = False
        operation_error = None
        cleanup_error = None
        post_state_error = None
        try:
            self.sensor.mcu.register_response(
                handle_sample, self.RESPONSE_NAME, self.sensor.oid
            )
            registered = True
            for index in range(request_count):
                request_time = print_start + float(index) / rate
                request_clock = self.sensor.mcu.print_time_to_clock(request_time)
                if not isinstance(request_clock, int) or request_clock < first_clock:
                    raise CalibrationError("UNSUPPORTED_SENSOR_INTERFACE")
                self.read_command.send(
                    [self.sensor.oid, 0, 0],
                    minclock=request_clock,
                    reqclock=request_clock,
                )
            reactor.pause(event_start + duration + 0.600)
        except Exception as exc:
            operation_error = exc
            issues.append("CAPTURE_OPERATION_FAILED")
            operation_errors.append(type(exc).__name__)
        finally:
            try:
                if registered:
                    current_handler = handlers.get(response_key)
                    if current_handler is handle_sample:
                        try:
                            self.sensor.mcu.register_response(
                                None, self.RESPONSE_NAME, self.sensor.oid
                            )
                        except Exception as exc:
                            cleanup_error = exc
                            issues.append("SENSOR_HANDLER_CLEANUP_FAILED")
                            operation_errors.append(type(exc).__name__)
                    elif current_handler is not None:
                        issues.append("SENSOR_HANDLER_OWNERSHIP_LOST")
                    else:
                        issues.append("SENSOR_HANDLER_REMOVED")
            finally:
                self.active = False
                _ACTIVE_SENSOR_IDS.discard(sensor_id)

        handler_released = handlers.get(response_key) is None
        if handler_released:
            try:
                self.validate_configuration()
            except CalibrationError as exc:
                post_state_error = CalibrationError("STOCK_SENSOR_STATE_CHANGED")
                issues.append("STOCK_SENSOR_STATE_CHANGED")
                operation_errors.append(type(exc).__name__)
        else:
            issues.append("STOCK_SENSOR_STATE_UNVERIFIED")
            if cleanup_error is None:
                post_state_error = CalibrationError("SENSOR_OWNERSHIP_UNSAFE")
                operation_errors.append(type(post_state_error).__name__)
        if post_state_error is not None or not handler_released:
            invoke_shutdown = getattr(self.printer, "invoke_shutdown", None)
            if callable(invoke_shutdown):
                invoke_shutdown(
                    "PA direct-read capture did not restore verified stock "
                    "sensor state; FIRMWARE_RESTART required"
                )

        if len(messages) != request_count:
            issues.append("RESPONSE_COUNT_MISMATCH")
        capture = DirectReadCapture(
            messages=tuple(messages),
            print_start=print_start,
            requested_responses=request_count,
            issues=tuple(dict.fromkeys(issues)),
            rejected_messages=tuple(rejected_messages),
            operation_errors=tuple(operation_errors),
        )
        if post_state_error is not None:
            raise CaptureOperationError(
                _calibration_reason(post_state_error), capture
            ) from post_state_error
        if cleanup_error is not None:
            raise CaptureOperationError(
                "SENSOR_HANDLER_CLEANUP_FAILED", capture
            ) from cleanup_error
        if operation_error is not None:
            raise CaptureOperationError(
                "CAPTURE_OPERATION_FAILED", capture
            ) from operation_error
        return capture


class TLTGPressureAdvance:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.developer_capture = config.getboolean("developer_capture", False)
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_command("_TLTG_PA_CALIBRATE", self.cmd_calibrate)
        if self.developer_capture:
            self.gcode.register_command("_TLTG_PA_CAPTURE", self.cmd_capture)

    def get_status(self, eventtime):
        return {"calibration_enabled": CALIBRATION_ENABLED}

    def cmd_calibrate(self, gcmd):
        temperature = gcmd.get_float("TEMP", None)
        nozzle = gcmd.get_float("NOZZLE", None)
        try:
            validate_inputs(temperature, nozzle)
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        if not CALIBRATION_ENABLED:
            raise gcmd.error("PA_CALIBRATION_UNVALIDATED")
        reactor = self.printer.get_reactor()
        try:
            validate_calibration_preflight(
                self.printer,
                reactor.monotonic(),
                temperature,
                nozzle,
            )
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        raise gcmd.error("PA_CALIBRATION_NOT_IMPLEMENTED")

    def cmd_capture(self, gcmd):
        duration = gcmd.get_float("SECONDS", 1.0, above=0.0, maxval=5.0)
        rate = gcmd.get_int(
            "RATE", DEFAULT_CAPTURE_RATE, minval=1, maxval=1000
        )
        reactor = self.printer.get_reactor()
        try:
            _require_idle_print_state(self.printer, reactor.monotonic())
        except CalibrationError as exc:
            raise gcmd.error("PA_CAPTURE_REQUIRES_IDLE_PRINTER") from exc
        try:
            adapter = QidiCS1237Adapter(self.printer)
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        toolhead = self.printer.lookup_object("toolhead")
        try:
            start_marker = _toolhead_print_time(toolhead, reactor.monotonic())
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        capture_error = None
        try:
            capture = adapter.capture(reactor, duration, rate)
        except CaptureOperationError as exc:
            capture = exc.capture
            capture_error = exc
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        path = "/tmp/tltg_pa_capture_%d.json" % int(time.time())
        try:
            end_marker = _toolhead_print_time(toolhead, reactor.monotonic())
        except CalibrationError as exc:
            raise gcmd.error(_calibration_reason(exc)) from exc
        document = {
            "adc_sample_rate": SAMPLE_RATE,
            "requested_rate": rate,
            "requested_responses": capture.requested_responses,
            "received_responses": capture.received_responses,
            "capture_complete": capture.complete,
            "capture_issues": capture.issues,
            "capture_operation_errors": capture.operation_errors,
            "rejected_messages": list(capture.rejected_messages),
            "scheduled_print_time": capture.print_start,
            "duration": duration,
            "motion_markers": {
                "start": start_marker,
                "end": end_marker,
            },
            "messages": [_json_message(item) for item in capture.messages],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
        gcmd.respond_info("PA_CAPTURE=%s" % path)
        if capture_error is not None:
            raise gcmd.error(capture_error.reason)


def _calibration_reason(error):
    if isinstance(error, CalibrationRunError):
        return error.reason
    if isinstance(error, CalibrationError):
        reason = str(error)
        return reason if reason else "CALIBRATION_FAILED"
    return "CALIBRATION_INTERNAL_ERROR"


def _finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _object_status(obj, eventtime, reason="UNSUPPORTED_FILAMENT_STATE"):
    get_status = getattr(obj, "get_status", None)
    if not callable(get_status):
        raise CalibrationError(reason)
    status = get_status(eventtime)
    if not isinstance(status, dict):
        raise CalibrationError(reason)
    return status


def _toolhead_print_time(toolhead, eventtime):
    get_status = getattr(toolhead, "get_status", None)
    if not callable(get_status):
        raise CalibrationError("UNSUPPORTED_TOOLHEAD_INTERFACE")
    status = get_status(eventtime)
    print_time = status.get("print_time") if isinstance(status, dict) else None
    if (
        not isinstance(print_time, (int, float))
        or isinstance(print_time, bool)
        or not math.isfinite(print_time)
        or print_time < 0.0
    ):
        raise CalibrationError("UNSUPPORTED_TOOLHEAD_INTERFACE")
    return print_time


def _bounded_capture_request_count(duration, rate):
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration <= 0.0
        or duration > MAX_CAPTURE_DURATION
    ):
        raise CalibrationError("INVALID_CAPTURE_DURATION")
    if (
        not isinstance(rate, int)
        or isinstance(rate, bool)
        or rate < 1
        or rate > MAX_CAPTURE_RATE
    ):
        raise CalibrationError("INVALID_CAPTURE_RATE")
    request_count = max(1, int(round(duration * rate)))
    if request_count > MAX_CAPTURE_REQUESTS:
        raise CalibrationError("CAPTURE_RESOURCE_LIMIT")
    return request_count


def _validate_direct_read_response(params, expected_oid):
    if not isinstance(params, dict) or params.get("oid") != expected_oid:
        return "INVALID_RESPONSE_OID"
    payload = params.get("data")
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != 4:
        return "INVALID_RESPONSE_PAYLOAD"
    sent_time = params.get("#sent_time")
    receive_time = params.get("#receive_time")
    if (
        not isinstance(sent_time, (int, float))
        or isinstance(sent_time, bool)
        or not math.isfinite(sent_time)
        or not isinstance(receive_time, (int, float))
        or isinstance(receive_time, bool)
        or not math.isfinite(receive_time)
        or receive_time < sent_time
    ):
        return "INVALID_RESPONSE_TIMING"
    return None


def _diagnostic_response(params):
    if not isinstance(params, dict):
        return {"value_type": type(params).__name__}
    return _json_message(params)


def _json_message(message):
    result = {}
    for key, value in message.items():
        if isinstance(value, (bytes, bytearray)):
            result[key] = value.hex()
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _values(samples, start, end):
    return [sample.counts for sample in samples if start <= sample.time < end]


def _median_absolute_deviation(values):
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def _crossing_delay(samples, start, end, initial, final, fraction):
    target = initial + (final - initial) * fraction
    increasing = final >= initial
    for sample in samples:
        if sample.time < start or sample.time >= end:
            continue
        if (increasing and sample.counts >= target) or (not increasing and sample.counts <= target):
            return sample.time - start
    return end - start


def load_config(config):
    return TLTGPressureAdvance(config)
