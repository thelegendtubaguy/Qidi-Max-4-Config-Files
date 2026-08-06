from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:[#;].*)?$")
OPTION_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+)(?P<sep>\s*[:=]\s*)(?P<rest>.*?)(?P<newline>\r?\n?)$"
)
COMMENT_PREFIXES = ("#", ";")


class TargetResolutionError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SectionSpan:
    name: str
    header_index: int
    option_start: int
    option_end: int


@dataclass(frozen=True)
class ResolvedOption:
    value: str
    line_index: int
    line: str
    prefix: str
    comment_suffix: str
    newline: str


@dataclass(frozen=True)
class ResolvedSection:
    name: str
    header_index: int
    end_index: int
    text: str


@dataclass(frozen=True)
class ParsedOptionLine:
    key: str
    value: str
    prefix: str
    comment_suffix: str
    newline: str



def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.read()




def has_section(text: str, section_name: str) -> bool:
    return any(span.name == section_name for span in _section_spans(text.splitlines(keepends=True)))



def resolve_unique_section(text: str, section_name: str) -> ResolvedSection:
    lines = text.splitlines(keepends=True)
    sections = [span for span in _section_spans(lines) if span.name == section_name]
    if not sections:
        raise TargetResolutionError("missing")
    if len(sections) > 1:
        raise TargetResolutionError("ambiguous")
    section = sections[0]
    return ResolvedSection(
        name=section.name,
        header_index=section.header_index,
        end_index=section.option_end,
        text="".join(lines[section.header_index:section.option_end]),
    )



def resolve_unique_option(text: str, section_name: str, option_name: str) -> ResolvedOption:
    lines = text.splitlines(keepends=True)
    section = resolve_unique_section(text, section_name)
    matches: list[ResolvedOption] = []
    for line_index in range(section.header_index + 1, section.end_index):
        parsed = parse_option_line(lines[line_index])
        if parsed is None or parsed.key != option_name:
            continue
        matches.append(
            ResolvedOption(
                value=parsed.value,
                line_index=line_index,
                line=lines[line_index],
                prefix=parsed.prefix,
                comment_suffix=parsed.comment_suffix,
                newline=parsed.newline,
            )
        )
    if not matches:
        raise TargetResolutionError("missing")
    if len(matches) > 1:
        raise TargetResolutionError("ambiguous")
    return matches[0]



def set_option_value(text: str, section_name: str, option_name: str, desired_value: str) -> str:
    lines = text.splitlines(keepends=True)
    resolved = resolve_unique_option(text, section_name, option_name)
    lines[resolved.line_index] = (
        f"{resolved.prefix}{desired_value}{resolved.comment_suffix}{resolved.newline}"
    )
    return "".join(lines)




def delete_section(text: str, section_name: str) -> str:
    lines = text.splitlines(keepends=True)
    resolved = resolve_unique_section(text, section_name)
    del lines[resolved.header_index:resolved.end_index]
    return "".join(lines)



def append_section(text: str, section_text: str) -> str:
    separator = "" if not text or text.endswith(("\n", "\r\n")) else "\n"
    return text + separator + section_text



def normalized_section_sha256(section_text: str) -> str:
    return hashlib.sha256(_normalize_section_for_hash(section_text).encode("utf-8")).hexdigest()



def parse_option_line(raw_line: str) -> ParsedOptionLine | None:
    stripped = raw_line.lstrip()
    if not stripped or stripped.startswith(COMMENT_PREFIXES):
        return None
    if SECTION_RE.match(raw_line):
        return None
    match = OPTION_RE.match(raw_line)
    if not match:
        return None
    key = match.group("key").strip()
    rest = match.group("rest")
    value_part, comment_suffix = _split_value_and_comment(rest)
    return ParsedOptionLine(
        key=key,
        value=value_part.strip(),
        prefix=match.group("indent") + key + match.group("sep"),
        comment_suffix=comment_suffix,
        newline=match.group("newline"),
    )



def _section_spans(lines: list[str]) -> list[SectionSpan]:
    sections: list[SectionSpan] = []
    current_name = None
    current_header_index = None
    current_start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        match = SECTION_RE.match(line)
        if match:
            if current_name is not None:
                sections.append(
                    SectionSpan(
                        name=current_name,
                        header_index=current_header_index,
                        option_start=current_start,
                        option_end=index,
                    )
                )
            current_name = match.group("name").strip()
            current_header_index = index
            current_start = index + 1
    if current_name is not None:
        sections.append(
            SectionSpan(
                name=current_name,
                header_index=current_header_index,
                option_start=current_start,
                option_end=len(lines),
            )
        )
    return sections



def _split_value_and_comment(rest: str) -> tuple[str, str]:
    for index, char in enumerate(rest):
        if char not in COMMENT_PREFIXES:
            continue
        if index == 0:
            return "", " " + rest
        if rest[index - 1].isspace():
            value = rest[:index].rstrip()
            return value, rest[len(value) :]
    return rest.rstrip(), ""



def _normalize_section_for_hash(section_text: str) -> str:
    normalized: list[str] = []
    for line in section_text.splitlines():
        in_single = False
        in_double = False
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                normalized.append(char)
                escaped = False
                continue
            if char == "\\" and (in_single or in_double):
                normalized.append(char)
                escaped = True
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                normalized.append(char)
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                normalized.append(char)
                continue
            if not in_single and not in_double:
                if char in COMMENT_PREFIXES and (index == 0 or line[index - 1].isspace()):
                    break
                if char.isspace():
                    continue
            normalized.append(char)
    return "".join(normalized)
