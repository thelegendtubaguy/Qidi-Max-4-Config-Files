#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.tests.integration.test_macro_call_graph import (  # noqa: E402
    MacroDefinition,
    iter_static_commands,
    manifest_deleted_sections,
    parse_macro_definitions,
)

CONTRACT_ROOT = REPO_ROOT / "openspec" / "contracts" / "gcode-paths"
CONTRACT_GLOB = "*.path.json"


@dataclass(frozen=True)
class LineSlice:
    lines: tuple[tuple[int, str], ...]
    start_line: int
    end_line: int

    @property
    def text(self) -> str:
        return "\n".join(line for _, line in self.lines)


@dataclass(frozen=True)
class BranchReport:
    name: str
    condition: str
    source_macro: MacroDefinition
    line_slice: LineSlice
    direct_macro_calls: tuple[str, ...]


@dataclass(frozen=True)
class ContractReport:
    contract_path: Path
    contract: dict[str, Any]
    macros: dict[str, MacroDefinition]
    branches: tuple[BranchReport, ...]
    markdown: str
    mermaid: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate sparse G-code path contracts and generated path maps."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite generated docs instead of checking that they are current",
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    reports: list[ContractReport] = []
    for path in sorted(CONTRACT_ROOT.glob(CONTRACT_GLOB)):
        try:
            reports.append(load_and_validate_contract(path))
        except ContractError as exc:
            errors.append(str(exc))

    if not reports and not errors:
        errors.append(f"No G-code path contracts found under {relative(CONTRACT_ROOT)}")

    if not errors:
        for report in reports:
            errors.extend(write_or_check_generated(report, write=args.write))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if args.write:
        print(f"Updated {len(reports)} G-code path contract generated view(s).")
    else:
        print(f"Validated {len(reports)} G-code path contract(s).")
    return 0


class ContractError(RuntimeError):
    pass


def load_and_validate_contract(contract_path: Path) -> ContractReport:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    macro_files = expand_macro_files(contract.get("macro_files", []), contract_path)
    macros, duplicates = parse_macro_definitions(
        macro_files,
        excluded_sections=manifest_deleted_sections(),
    )
    if duplicates:
        rows = [
            f"  {first.name}: {relative(first.path)}:{first.line_number} and {relative(second.path)}:{second.line_number}"
            for first, second in duplicates
        ]
        raise ContractError(
            f"{relative(contract_path)}: duplicate macro definitions in contract scope:\n"
            + "\n".join(rows)
        )

    for entrypoint in require_list(contract, "entrypoints", contract_path):
        validate_entrypoint(contract_path, entrypoint)

    branch_reports: list[BranchReport] = []
    for branch in require_list(contract, "branches", contract_path):
        branch_reports.append(validate_branch(contract_path, branch, macros))

    report = ContractReport(
        contract_path=contract_path,
        contract=contract,
        macros=macros,
        branches=tuple(branch_reports),
        markdown="",
        mermaid="",
    )
    markdown = render_markdown(report)
    mermaid = render_mermaid(report)
    return ContractReport(
        contract_path=contract_path,
        contract=contract,
        macros=macros,
        branches=tuple(branch_reports),
        markdown=markdown,
        mermaid=mermaid,
    )


def expand_macro_files(patterns: list[str], contract_path: Path) -> tuple[Path, ...]:
    if not patterns:
        raise ContractError(f"{relative(contract_path)}: macro_files must not be empty")
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = sorted(REPO_ROOT.glob(pattern))
        if not matches:
            raise ContractError(
                f"{relative(contract_path)}: macro_files pattern matched nothing: {pattern}"
            )
        for match in matches:
            if not match.is_file():
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(match)
    return tuple(files)


def strip_semicolon_comments(text: str) -> str:
    return "\n".join(line.split(";", 1)[0] for line in text.splitlines())


def validate_entrypoint(contract_path: Path, entrypoint: dict[str, Any]) -> None:
    path = REPO_ROOT / require_str(entrypoint, "path", contract_path)
    if not path.exists():
        raise ContractError(f"{relative(contract_path)}: missing entrypoint file {relative(path)}")
    text = strip_semicolon_comments(path.read_text(encoding="utf-8"))
    label = f"{relative(contract_path)} entrypoint {entrypoint.get('name', relative(path))}"
    assert_order(label, text, entrypoint.get("must_include_order", []))
    assert_absent(label, text, entrypoint.get("must_not_include", []))


def validate_branch(
    contract_path: Path,
    branch: dict[str, Any],
    macros: dict[str, MacroDefinition],
) -> BranchReport:
    macro_name = require_str(branch, "source_macro", contract_path)
    macro = macros.get(macro_name.upper())
    if macro is None:
        raise ContractError(
            f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: "
            f"missing source_macro {macro_name}"
        )
    line_slice = extract_branch_slice(contract_path, branch, macro)
    label = f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}"
    assert_order(label, line_slice.text, branch.get("must_include_order", []))
    assert_absent(label, line_slice.text, branch.get("must_not_include", []))
    return BranchReport(
        name=require_str(branch, "name", contract_path),
        condition=require_str(branch, "condition", contract_path),
        source_macro=macro,
        line_slice=line_slice,
        direct_macro_calls=tuple(iter_direct_macro_calls(line_slice.lines, macros)),
    )


def extract_branch_slice(
    contract_path: Path,
    branch: dict[str, Any],
    macro: MacroDefinition,
) -> LineSlice:
    slice_spec = branch.get("slice")
    if not isinstance(slice_spec, dict):
        raise ContractError(
            f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: slice must be an object"
        )
    start_marker = require_str(slice_spec, "start", contract_path)
    end_marker = slice_spec.get("end")
    if end_marker is not None and not isinstance(end_marker, str):
        raise ContractError(
            f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: slice.end must be a string"
        )

    start_index = find_marker_index(macro.gcode_lines, start_marker, 0)
    if start_index is None:
        raise ContractError(
            f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: "
            f"start marker not found in {macro.name}: {start_marker}"
        )
    end_index = len(macro.gcode_lines)
    if end_marker:
        found_end = find_marker_index(macro.gcode_lines, end_marker, start_index + 1)
        if found_end is None:
            raise ContractError(
                f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: "
                f"end marker not found in {macro.name}: {end_marker}"
            )
        end_index = found_end
    if start_index >= end_index:
        raise ContractError(
            f"{relative(contract_path)} branch {branch.get('name', '<unnamed>')}: empty slice"
        )
    lines = tuple(macro.gcode_lines[start_index:end_index])
    return LineSlice(lines=lines, start_line=lines[0][0], end_line=lines[-1][0])


def find_marker_index(lines: tuple[tuple[int, str], ...], marker: str, start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        if marker in lines[index][1]:
            return index
    return None


def assert_order(label: str, text: str, patterns: list[str]) -> None:
    if not isinstance(patterns, list):
        raise ContractError(f"{label}: must_include_order must be a list")
    cursor = 0
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise ContractError(f"{label}: must_include_order entries must be strings")
        index = text.find(pattern, cursor)
        if index == -1:
            raise ContractError(f"{label}: missing ordered pattern after byte {cursor}: {pattern}")
        cursor = index + len(pattern)


def assert_absent(label: str, text: str, patterns: list[str]) -> None:
    if not isinstance(patterns, list):
        raise ContractError(f"{label}: must_not_include must be a list")
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise ContractError(f"{label}: must_not_include entries must be strings")
        if pattern in text:
            raise ContractError(f"{label}: forbidden pattern present: {pattern}")


def iter_direct_macro_calls(
    lines: tuple[tuple[int, str], ...],
    macros: dict[str, MacroDefinition],
):
    seen: set[str] = set()
    for _, raw_line in lines:
        for command in iter_static_commands(raw_line):
            key = command.upper()
            if key in macros and key not in seen:
                seen.add(key)
                yield macros[key].name


def write_or_check_generated(report: ContractReport, *, write: bool) -> list[str]:
    generated = report.contract.get("generated", {})
    markdown_path = REPO_ROOT / require_generated_path(report, generated, "markdown")
    mermaid_path = REPO_ROOT / require_generated_path(report, generated, "mermaid")
    desired = ((markdown_path, report.markdown), (mermaid_path, report.mermaid))
    errors: list[str] = []
    for path, content in desired:
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            continue
        if not path.exists():
            errors.append(f"Missing generated file {relative(path)}; run python3 scripts/check_gcode_paths.py --write")
            continue
        current = path.read_text(encoding="utf-8")
        if current != content:
            errors.append(f"Stale generated file {relative(path)}; run python3 scripts/check_gcode_paths.py --write")
    return errors


def require_generated_path(report: ContractReport, generated: dict[str, Any], key: str) -> str:
    if not isinstance(generated, dict):
        raise ContractError(f"{relative(report.contract_path)}: generated must be an object")
    value = generated.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{relative(report.contract_path)}: generated.{key} must be a path string")
    return value


def render_markdown(report: ContractReport) -> str:
    contract = report.contract
    title = contract.get("title", contract.get("path", report.contract_path.stem))
    lines: list[str] = [
        "<!-- GENERATED by scripts/check_gcode_paths.py --write. Do not edit by hand. -->",
        f"# {title} path contract",
        "",
        f"Contract: `{relative(report.contract_path)}`",
        "",
        "## Entrypoints",
        "",
    ]
    for entrypoint in contract.get("entrypoints", []):
        lines.extend(
            [
                f"### {entrypoint['name']}",
                "",
                f"Path: `{entrypoint['path']}`",
                "",
                "Ordered invariants:",
                "",
            ]
        )
        lines.extend(f"- `{pattern}`" for pattern in entrypoint.get("must_include_order", []))
        forbidden = entrypoint.get("must_not_include", [])
        if forbidden:
            lines.extend(["", "Forbidden patterns:", ""])
            lines.extend(f"- `{pattern}`" for pattern in forbidden)
        lines.append("")

    lines.extend(["## Branches", ""])
    for branch_report in report.branches:
        source = branch_report.source_macro
        lines.extend(
            [
                f"### {branch_report.name}",
                "",
                f"Condition: `{branch_report.condition}`",
                "",
                f"Source: `{relative(source.path)}:{branch_report.line_slice.start_line}-{branch_report.line_slice.end_line}`",
                "",
                "Direct visible macro calls in branch slice:",
                "",
            ]
        )
        if branch_report.direct_macro_calls:
            lines.extend(f"- `{call}`" for call in branch_report.direct_macro_calls)
        else:
            lines.append("- none")
        branch_contract = next(
            branch
            for branch in contract.get("branches", [])
            if branch.get("name") == branch_report.name
        )
        lines.extend(["", "Ordered invariants:", ""])
        lines.extend(f"- `{pattern}`" for pattern in branch_contract.get("must_include_order", []))
        forbidden = branch_contract.get("must_not_include", [])
        if forbidden:
            lines.extend(["", "Forbidden patterns:", ""])
            lines.extend(f"- `{pattern}`" for pattern in forbidden)
        lines.append("")

    lines.extend(
        [
            "## Macro scope",
            "",
        ]
    )
    for pattern in contract.get("macro_files", []):
        lines.append(f"- `{pattern}`")
    lines.append("")
    return "\n".join(lines)


def render_mermaid(report: ContractReport) -> str:
    contract = report.contract
    title = contract.get("path", report.contract_path.stem)
    lines = [
        "%% GENERATED by scripts/check_gcode_paths.py --write. Do not edit by hand.",
        "flowchart TD",
        f"  path[{mermaid_label(title)}]",
    ]
    for index, entrypoint in enumerate(contract.get("entrypoints", []), start=1):
        entry_id = f"entry{index}"
        lines.append(f"  {entry_id}[{mermaid_label(entrypoint['name'])}]")
        lines.append(f"  path --> {entry_id}")
        for pattern_index, pattern in enumerate(entrypoint.get("must_include_order", []), start=1):
            if not pattern.strip():
                continue
            node_id = f"{entry_id}_step{pattern_index}"
            lines.append(f"  {entry_id} --> {node_id}[{mermaid_label(pattern)}]")

    for branch in report.branches:
        source_id = mermaid_node_id(branch.source_macro.name)
        branch_id = mermaid_node_id(f"branch_{branch.name}")
        lines.append(f"  {source_id} --> {branch_id}[{mermaid_label(branch.name)}]")
        for call in branch.direct_macro_calls:
            call_id = mermaid_node_id(call)
            lines.append(f"  {branch_id} --> {call_id}[{mermaid_label(call)}]")
    return "\n".join(dedupe_preserve_order(lines)) + "\n"


def first_command(pattern: str) -> str:
    stripped = pattern.strip()
    if not stripped:
        return ""
    return stripped.split()[0]


def mermaid_node_id(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"node_{sanitized}"
    return sanitized


def mermaid_label(value: str) -> str:
    return '"' + value.replace('"', "'") + '"'


def dedupe_preserve_order(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def require_list(config: dict[str, Any], key: str, path: Path) -> list[Any]:
    value = config.get(key)
    if not isinstance(value, list):
        raise ContractError(f"{relative(path)}: {key} must be a list")
    return value


def require_str(config: dict[str, Any], key: str, path: Path) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{relative(path)}: {key} must be a non-empty string")
    return value


def relative(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
