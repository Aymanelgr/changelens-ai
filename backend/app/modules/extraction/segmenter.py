"""Splits documents into citable segments (pipeline step 1).

Invariant relied on by citation checks: text[segment.start_char:segment.end_char] == segment.text
"""

import hashlib
import re
from dataclasses import dataclass

MAX_SEGMENT_CHARS = 1500

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BLANK_LINE_RE = re.compile(r"\r?\n[ \t]*\r?\n")


@dataclass(frozen=True)
class SegmentData:
    section_path: str | None
    start_char: int
    end_char: int
    text: str
    sha256: str


def _make_segment(source: str, start: int, end: int, section: str | None) -> SegmentData | None:
    """Trim surrounding whitespace while keeping offsets exact. None if nothing remains."""
    chunk = source[start:end]
    stripped = chunk.strip()
    if not stripped:
        return None
    start += len(chunk) - len(chunk.lstrip())
    end = start + len(stripped)
    return SegmentData(section, start, end, stripped, hashlib.sha256(stripped.encode()).hexdigest())


def _split_paragraphs(source: str, start: int, end: int, section: str | None) -> list[SegmentData]:
    """Group paragraphs into segments no longer than MAX_SEGMENT_CHARS where possible."""
    segments: list[SegmentData] = []
    chunk_start = start
    last_break = None
    position = start
    while True:
        match = _BLANK_LINE_RE.search(source, position, end)
        break_at = match.start() if match else end
        if break_at - chunk_start > MAX_SEGMENT_CHARS and last_break is not None:
            segment = _make_segment(source, chunk_start, last_break, section)
            if segment:
                segments.append(segment)
            chunk_start = last_break
        if not match:
            break
        last_break = break_at
        position = match.end()
    segment = _make_segment(source, chunk_start, end, section)
    if segment:
        segments.append(segment)
    return segments


def segment_markdown(source: str) -> list[SegmentData]:
    sections: list[tuple[int, str | None]] = [(0, None)]
    stack: list[tuple[int, str]] = []
    in_fence = False
    offset = 0
    for line in source.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and (match := _HEADING_RE.match(line.rstrip("\r\n"))):
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2).strip()))
            sections.append((offset, " > ".join(title for _, title in stack)))
        offset += len(line)

    segments: list[SegmentData] = []
    for index, (start, path) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(source)
        segments.extend(_split_paragraphs(source, start, end, path))
    return segments


def segment_text(source: str) -> list[SegmentData]:
    """Plain text has no sections: one segment per paragraph."""
    segments: list[SegmentData] = []
    position = 0
    for match in _BLANK_LINE_RE.finditer(source):
        segment = _make_segment(source, position, match.start(), None)
        if segment:
            segments.append(segment)
        position = match.end()
    segment = _make_segment(source, position, len(source), None)
    if segment:
        segments.append(segment)
    return segments
