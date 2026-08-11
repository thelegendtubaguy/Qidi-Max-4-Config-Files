from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import klipper_cfg, messages, safety
from .errors import ActivePrintError, PrinterStateError
from .fs_atomic import atomic_write_text
from .interaction import confirm_yes
from .models import RuntimePaths
from .reporter import DetailGroup

VALUE_T_RE = re.compile(r"^value_t(?P<tool>\d+)$")
RUNTIME_STATE_FILE = "config/tltg_optimized_runtime_state.json"
UrlOpenFn = Callable[..., object]


@dataclass(frozen=True)
class BoxEnablementOpportunity:
    saved_variables_path: Path
    box_count: int
    enable_box: int


@dataclass(frozen=True)
class ToolSlotMismatch:
    tool: int
    variable: str
    current: str
    expected: str


@dataclass(frozen=True)
class RequiredToolSlotGap:
    tool: int
    variable: str
    current: str
    expected: str


@dataclass(frozen=True)
class RequiredToolSlotOpportunity:
    saved_variables_path: Path
    box_count: int
    gaps: tuple[RequiredToolSlotGap, ...]


@dataclass(frozen=True)
class ToolSlotAlignmentOpportunity:
    saved_variables_path: Path
    mismatches: tuple[ToolSlotMismatch, ...]


def maybe_initialize_filament_retention(
    *,
    paths: RuntimePaths,
    reporter,
    journal,
) -> bool:
    saved_variables_path = paths.config_root / "saved_variables.cfg"
    text = (
        klipper_cfg.read_text(saved_variables_path)
        if saved_variables_path.exists()
        else "[Variables]\n"
    )
    try:
        klipper_cfg.resolve_unique_option(
            text, "Variables", "tltg_keep_loaded_between_prints"
        )
    except klipper_cfg.TargetResolutionError as exc:
        if exc.reason != "missing":
            raise
    else:
        reporter.debug(event="filament_retention_default.preserved")
        return False

    new_text = set_saved_variable(
        text, "tltg_keep_loaded_between_prints", "1"
    )
    journal.note_write()
    atomic_write_text(saved_variables_path, new_text)
    reporter.line(messages.FILAMENT_RETENTION_DEFAULT_ENABLED)
    return True


def maybe_prompt_enable_box(
    *,
    paths: RuntimePaths,
    reporter,
    input_stream,
    journal,
) -> bool:
    opportunity = detect_box_enablement_opportunity(paths)
    if opportunity is None:
        reporter.debug(event="box_enablement.skipped")
        return False

    reporter.debug(
        event="box_enablement.detected",
        box_count=opportunity.box_count,
        enable_box=opportunity.enable_box,
        saved_variables_path=opportunity.saved_variables_path,
    )
    if not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.ENABLE_QIDI_BOX_PROMPT.format(box_count=opportunity.box_count),
        instruction=messages.ENABLE_QIDI_BOX_PROMPT_INSTRUCTION,
        cancel_message=messages.ENABLE_QIDI_BOX_DECLINED,
    ):
        return False

    text = klipper_cfg.read_text(opportunity.saved_variables_path)
    new_text = set_saved_variable(text, "enable_box", "1")
    if new_text != text:
        journal.note_write()
        atomic_write_text(opportunity.saved_variables_path, new_text)
    reporter.line(messages.ENABLE_QIDI_BOX_ENABLED)
    return True


def maybe_write_required_tool_slot_variables(
    *,
    paths: RuntimePaths,
    reporter,
    journal=None,
) -> bool:
    opportunity = detect_required_tool_slot_opportunity(paths)
    if opportunity is None:
        reporter.debug(event="required_tool_slots.skipped")
        return False

    reporter.debug(
        event="required_tool_slots.detected",
        box_count=opportunity.box_count,
        gaps=len(opportunity.gaps),
        saved_variables_path=opportunity.saved_variables_path,
    )
    text = klipper_cfg.read_text(opportunity.saved_variables_path)
    new_text = align_required_tool_slot_variables(text, opportunity.gaps)
    if new_text != text:
        if journal is not None:
            journal.note_write()
        atomic_write_text(opportunity.saved_variables_path, new_text)
    reporter.line(
        messages.REQUIRED_TOOL_SLOT_MAPPINGS_WRITTEN.format(count=len(opportunity.gaps))
    )
    return True


def maybe_reconcile_tool_slots_after_box_count_change(
    *,
    paths: RuntimePaths,
    reporter,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    except ActivePrintError:
        reporter.line(messages.REQUIRED_TOOL_SLOT_RECONCILE_SKIPPED_ACTIVE_PRINT)
        return False
    except PrinterStateError:
        reporter.line(messages.REQUIRED_TOOL_SLOT_RECONCILE_SKIPPED_UNKNOWN_STATE)
        return False

    saved_variables_path = paths.config_root / "saved_variables.cfg"
    if not saved_variables_path.exists() or not _box_extras_configured(paths.config_root / "box.cfg"):
        reporter.debug(event="required_tool_slots.reconcile_skipped", reason="not_configured")
        return False
    try:
        text = klipper_cfg.read_text(saved_variables_path)
        box_count = _resolve_saved_int(text, "box_count", default=0)
    except (OSError, klipper_cfg.TargetResolutionError, ValueError):
        reporter.debug(event="required_tool_slots.reconcile_skipped", reason="unreadable_state")
        return False

    state = _read_runtime_state(paths)
    if _coerce_optional_int(state.get("last_observed_box_count")) == box_count:
        reporter.debug(event="required_tool_slots.reconcile_skipped", reason="box_count_unchanged")
        return False

    gaps = collect_required_tool_slot_gaps(text)
    if gaps:
        new_text = align_required_tool_slot_variables(text, gaps)
        if new_text != text:
            atomic_write_text(saved_variables_path, new_text)
        reporter.line(
            messages.REQUIRED_TOOL_SLOT_RECONCILED.format(box_count=box_count, count=len(gaps))
        )
    else:
        reporter.debug(event="required_tool_slots.reconcile_observed", box_count=box_count, gaps=0)
    _write_runtime_state(paths, {**state, "last_observed_box_count": box_count})
    return bool(gaps)


def maybe_prompt_align_tool_slots(
    *,
    paths: RuntimePaths,
    reporter,
    input_stream,
    journal,
) -> bool:
    if input_stream is None:
        reporter.debug(event="tool_slot_alignment.skipped", reason="noninteractive")
        return False

    opportunity = detect_tool_slot_alignment_opportunity(paths)
    if opportunity is None:
        reporter.debug(event="tool_slot_alignment.skipped")
        return False

    reporter.debug(
        event="tool_slot_alignment.detected",
        mismatches=len(opportunity.mismatches),
        saved_variables_path=opportunity.saved_variables_path,
    )
    rows = tuple(
        f"{mismatch.variable}: {mismatch.current} -> {mismatch.expected}"
        for mismatch in opportunity.mismatches
    )
    if hasattr(reporter, "emit_detail_groups"):
        reporter.emit_detail_groups(
            (DetailGroup(messages.TOOL_SLOT_MAPPING_MISMATCH_HEADER, rows),)
        )
    else:
        reporter.prepare_for_prompt()
        reporter.line(messages.TOOL_SLOT_MAPPING_MISMATCH_HEADER)
        for row in rows:
            reporter.line(f"  - {row}")
    if not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.TOOL_SLOT_MAPPING_PROMPT,
        instruction=messages.TOOL_SLOT_MAPPING_PROMPT_INSTRUCTION,
        cancel_message=messages.TOOL_SLOT_MAPPING_DECLINED,
    ):
        return False

    text = klipper_cfg.read_text(opportunity.saved_variables_path)
    new_text = align_tool_slot_variables(text, opportunity.mismatches)
    if new_text != text:
        journal.note_write()
        atomic_write_text(opportunity.saved_variables_path, new_text)
    reporter.line(messages.TOOL_SLOT_MAPPING_CORRECTED)
    return True


def detect_box_enablement_opportunity(paths: RuntimePaths) -> BoxEnablementOpportunity | None:
    saved_variables_path = paths.config_root / "saved_variables.cfg"
    if not saved_variables_path.exists():
        return None
    if not _box_extras_configured(paths.config_root / "box.cfg"):
        return None

    try:
        text = klipper_cfg.read_text(saved_variables_path)
        box_count_value = _resolve_saved_int(text, "box_count", default=0)
        enable_box_value = _resolve_saved_int(text, "enable_box", default=0)
    except (OSError, klipper_cfg.TargetResolutionError, ValueError):
        return None

    if box_count_value <= 0 or enable_box_value != 0:
        return None
    return BoxEnablementOpportunity(
        saved_variables_path=saved_variables_path,
        box_count=box_count_value,
        enable_box=enable_box_value,
    )


def detect_required_tool_slot_opportunity(paths: RuntimePaths) -> RequiredToolSlotOpportunity | None:
    saved_variables_path = paths.config_root / "saved_variables.cfg"
    if not saved_variables_path.exists():
        return None
    if not _box_extras_configured(paths.config_root / "box.cfg"):
        return None
    try:
        text = klipper_cfg.read_text(saved_variables_path)
        box_count = _resolve_saved_int(text, "box_count", default=0)
        gaps = collect_required_tool_slot_gaps(text)
    except (OSError, klipper_cfg.TargetResolutionError, ValueError):
        return None
    if not gaps:
        return None
    return RequiredToolSlotOpportunity(
        saved_variables_path=saved_variables_path,
        box_count=box_count,
        gaps=gaps,
    )


def detect_tool_slot_alignment_opportunity(
    paths: RuntimePaths,
) -> ToolSlotAlignmentOpportunity | None:
    saved_variables_path = paths.config_root / "saved_variables.cfg"
    if not saved_variables_path.exists():
        return None
    try:
        text = klipper_cfg.read_text(saved_variables_path)
        mismatches = collect_tool_slot_mismatches(text)
    except (OSError, klipper_cfg.TargetResolutionError, ValueError):
        return None
    if not mismatches:
        return None
    return ToolSlotAlignmentOpportunity(
        saved_variables_path=saved_variables_path,
        mismatches=mismatches,
    )


def collect_required_tool_slot_gaps(text: str) -> tuple[RequiredToolSlotGap, ...]:
    box_count = _resolve_saved_int(text, "box_count", default=0)
    value_t_tools, duplicate_tools = _collect_value_t_tools(text)
    if duplicate_tools:
        return ()

    gaps = []
    for tool in range(_required_tool_count(box_count)):
        variable = f"value_t{tool}"
        expected = f"slot{tool}"
        if tool not in value_t_tools:
            gaps.append(
                RequiredToolSlotGap(
                    tool=tool,
                    variable=variable,
                    current="<missing>",
                    expected=expected,
                )
            )
            continue
        variable, current = value_t_tools[tool]
        if current == "":
            gaps.append(
                RequiredToolSlotGap(
                    tool=tool,
                    variable=variable,
                    current="<empty>",
                    expected=expected,
                )
            )
    return tuple(gaps)


def collect_tool_slot_mismatches(text: str) -> tuple[ToolSlotMismatch, ...]:
    value_t_tools, duplicate_tools = _collect_value_t_tools(text)
    if duplicate_tools:
        return ()

    mismatches = []
    for tool, (variable, current) in sorted(value_t_tools.items()):
        expected = f"slot{tool}"
        if current != expected:
            mismatches.append(
                ToolSlotMismatch(
                    tool=tool,
                    variable=variable,
                    current=current or "<empty>",
                    expected=expected,
                )
            )
    return tuple(mismatches)


def align_required_tool_slot_variables(text: str, gaps: tuple[RequiredToolSlotGap, ...]) -> str:
    new_text = text
    for gap in gaps:
        new_text = set_saved_variable(new_text, gap.variable, f"'{gap.expected}'")
    return new_text


def align_tool_slot_variables(text: str, mismatches: tuple[ToolSlotMismatch, ...]) -> str:
    new_text = text
    for mismatch in mismatches:
        new_text = set_saved_variable(new_text, mismatch.variable, f"'{mismatch.expected}'")
    return new_text


def set_saved_variable(text: str, name: str, value: str) -> str:
    try:
        return klipper_cfg.set_option_value(text, "Variables", name, value)
    except klipper_cfg.TargetResolutionError as exc:
        if exc.reason != "missing":
            raise
    section = klipper_cfg.resolve_unique_section(text, "Variables")
    lines = text.splitlines(keepends=True)
    newline = _dominant_newline(lines)
    lines.insert(section.end_index, f"{name} = {value}{newline}")
    return "".join(lines)


def _collect_value_t_tools(text: str) -> tuple[dict[int, tuple[str, str]], set[int]]:
    lines = text.splitlines(keepends=True)
    section = klipper_cfg.resolve_unique_section(text, "Variables")
    value_t_tools: dict[int, tuple[str, str]] = {}
    duplicate_tools: set[int] = set()
    for line_index in range(section.header_index + 1, section.end_index):
        parsed = klipper_cfg.parse_option_line(lines[line_index])
        if parsed is None:
            continue
        match = VALUE_T_RE.match(parsed.key)
        if match is None:
            continue
        tool = int(match.group("tool"))
        if tool in value_t_tools:
            duplicate_tools.add(tool)
            continue
        value_t_tools[tool] = (parsed.key, _normalize_saved_string(parsed.value))
    return value_t_tools, duplicate_tools


def _required_tool_count(box_count: int) -> int:
    return max(0, min(16, box_count * 4))


def _box_extras_configured(path: Path) -> bool:
    try:
        return klipper_cfg.has_section(klipper_cfg.read_text(path), "box_extras")
    except OSError:
        return False


def _read_runtime_state(paths: RuntimePaths) -> dict[str, object]:
    path = paths.printer_data_root / RUNTIME_STATE_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_state(paths: RuntimePaths, state: dict[str, object]) -> None:
    path = paths.printer_data_root / RUNTIME_STATE_FILE
    atomic_write_text(path, json.dumps(state, sort_keys=True, indent=2) + "\n", mode=0o644)


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _resolve_saved_int(text: str, name: str, *, default: int) -> int:
    try:
        raw = klipper_cfg.resolve_unique_option(text, "Variables", name).value
    except klipper_cfg.TargetResolutionError as exc:
        if exc.reason == "missing":
            return default
        raise
    return _coerce_saved_int(raw)


def _coerce_saved_int(value: str) -> int:
    normalized = _normalize_saved_string(value).lower()
    if normalized == "true":
        return 1
    if normalized in {"false", "none", ""}:
        return 0
    return int(normalized)


def _normalize_saved_string(value: str) -> str:
    return value.strip().strip("'\"")


def _dominant_newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
    return "\n"
