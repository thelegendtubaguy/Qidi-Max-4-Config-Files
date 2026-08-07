#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPTIMIZED_MACRO_DIR = REPO_ROOT / "installer" / "klipper" / "tltg-optimized-macros"

GCODE_MACRO_RE = re.compile(r"^\[gcode_macro\s+([^\]]+)\]\s*$", re.IGNORECASE)
COMMAND_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)(?=$|[\s\[])")
CONTROL_BLOCK_RE = re.compile(r"\{(?:if|else|elsif|endif)\b[^}]*\}", re.IGNORECASE)
NUMERIC_SUFFIX_RE = re.compile(r"^(.*?)(\d+)$")

ALLOWED_EXTERNAL_COMMANDS = {
    "CLEAR_PAUSE",
    "DISABLE_ALL_SENSOR",
    "DISABLE_BOX_HEATER",
    "ENABLE_ALL_SENSOR",
    "G0",
    "G1",
    "G2",
    "G29.0",
    "G4",
    "G90",
    "G92",
    "G92_",
    "M82",
    "M83",
    "M1002",
    "M104",
    "M106",
    "M107",
    "M109",
    "M109.0",
    "M109.1",
    "M140",
    "M141",
    "M204",
    "M220",
    "M221",
    "M400",
    "PAUSE",
    "PRINT_END",
    "SET_PRINT_MAIN_STATUS",
    "SET_PRINT_STATS_INFO",
    "TIMELAPSE_TAKE_FRAME",
    "TOOL_CHANGE_END",
    "TOOL_CHANGE_START",
}

ALLOWED_EXTERNAL_DYNAMIC_PREFIXES = {
    "T",
    "UNLOAD_T",
}

BANNED_SLICER_COMMANDS = {
    "SET_INPUT_SHAPER": "Klipper saved input-shaper calibration remains authoritative; slicer G-code must not override it.",
}

QIDISTUDIO_BANNED_SNIPPETS = {
    "activate_air_filtration_on_completion[": "QIDI Studio rejects indexed completion-air-filtration placeholders in end G-code; keep them out of the QIDI Studio pack.",
    "complete_print_exhaust_fan_speed[": "QIDI Studio rejects indexed completion-air-filtration placeholders in end G-code; keep them out of the QIDI Studio pack.",
}

QIDISTUDIO_BANNED_PATTERNS = (
    (
        re.compile(r"\bM106\s+P4\b", re.IGNORECASE),
        "Direct polar cooler controls are not allowed in QIDI Studio G-code.",
    ),
)


def iter_macro_names(macro_dir: Path = OPTIMIZED_MACRO_DIR) -> set[str]:
    macros: set[str] = set()
    for path in sorted(macro_dir.rglob("*.cfg")):
        for line in path.read_text().splitlines():
            match = GCODE_MACRO_RE.match(line.strip())
            if match:
                macros.add(match.group(1).strip().upper())
    return macros


def build_dynamic_prefixes(macros: set[str]) -> set[str]:
    prefixes: set[str] = set()
    for macro in macros:
        match = NUMERIC_SUFFIX_RE.match(macro)
        if match and match.group(1):
            prefixes.add(match.group(1))
    return prefixes


def iter_slicer_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    files: list[Path] = []
    for directory in (
        repo_root / "orcaslicer_gcode",
        repo_root / "qidistudio_gcode",
    ):
        if directory.exists():
            files.extend(sorted(directory.glob("*.gcode")))
    return files


def iter_commands(path: Path):
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.split(";", 1)[0]
        if not line.strip():
            continue
        for segment in CONTROL_BLOCK_RE.split(line):
            segment = segment.strip()
            if not segment:
                continue
            match = COMMAND_RE.match(segment)
            if match:
                yield line_number, match.group(1).upper(), segment, match.end(1)


def is_dynamic_macro_call(
    command: str, segment: str, command_end: int, dynamic_prefixes: set[str]
) -> bool:
    return command in dynamic_prefixes and segment[command_end:].lstrip().startswith(
        "["
    )


def is_allowed_external_command(command: str, segment: str, command_end: int) -> bool:
    if command in ALLOWED_EXTERNAL_COMMANDS:
        return True
    return is_dynamic_macro_call(
        command, segment, command_end, ALLOWED_EXTERNAL_DYNAMIC_PREFIXES
    )


def validate_qidistudio_compatibility(repo_root: Path, path: Path) -> list[str]:
    if path.parent != repo_root / "qidistudio_gcode":
        return []

    failures: list[str] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.split(";", 1)[0]
        for snippet, reason in QIDISTUDIO_BANNED_SNIPPETS.items():
            if snippet in line:
                failures.append(
                    f"{path.relative_to(repo_root)}:{line_number}: QIDI Studio-incompatible placeholder '{snippet}': {reason}"
                )
        for pattern, reason in QIDISTUDIO_BANNED_PATTERNS:
            if pattern.search(line):
                failures.append(
                    f"{path.relative_to(repo_root)}:{line_number}: QIDI Studio-incompatible G-code '{line.strip()}': {reason}"
                )
    return failures


def validate_repo(
    repo_root: Path = REPO_ROOT,
) -> tuple[list[str], list[Path], set[str]]:
    macro_dir = repo_root / "installer" / "klipper" / "tltg-optimized-macros"
    macros = iter_macro_names(macro_dir)
    dynamic_prefixes = build_dynamic_prefixes(macros)
    failures: list[str] = []
    checked_files = iter_slicer_files(repo_root)

    for path in checked_files:
        failures.extend(validate_qidistudio_compatibility(repo_root, path))
        for line_number, command, segment, command_end in iter_commands(path):
            banned_reason = BANNED_SLICER_COMMANDS.get(command)
            if banned_reason:
                failures.append(
                    f"{path.relative_to(repo_root)}:{line_number}: banned command '{command}' in '{segment}': {banned_reason}"
                )
                continue
            if command in macros:
                continue
            if is_dynamic_macro_call(command, segment, command_end, dynamic_prefixes):
                continue
            if is_allowed_external_command(command, segment, command_end):
                continue
            failures.append(
                f"{path.relative_to(repo_root)}:{line_number}: unknown command '{command}' in '{segment}'"
            )

    return failures, checked_files, macros


def main() -> int:
    failures, checked_files, macros = validate_repo(REPO_ROOT)

    if failures:
        print("Optimized slicer G-code references unknown commands:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(
        f"Validated {len(checked_files)} optimized slicer G-code files against {len(macros)} optimized source macros."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
