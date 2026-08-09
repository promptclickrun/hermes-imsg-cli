"""Convert supported Markdown emphasis into native Messages format ranges."""

from __future__ import annotations

from dataclasses import dataclass
import re
import string
from typing import Any, Iterable


_STYLE_ORDER = {"bold": 0, "italic": 1, "underline": 2, "strikethrough": 3}
_DUNDER_RE = re.compile(r"__[A-Za-z_][A-Za-z0-9_]*__")
_ESCAPABLE = frozenset(string.punctuation)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    styles: tuple[str, ...]


@dataclass(frozen=True)
class _Protection:
    token: str
    value: str


def _code_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] in _ESCAPABLE:
            spans.append((index, index + 2))
            index += 2
            continue
        if text[index] != "`":
            index += 1
            continue
        end_of_run = index
        while end_of_run < len(text) and text[end_of_run] == "`":
            end_of_run += 1
        marker = text[index:end_of_run]
        closing = text.find(marker, end_of_run)
        if closing < 0:
            index = end_of_run
            continue
        spans.append((index, closing + len(marker)))
        index = closing + len(marker)
    return spans


def _overlaps(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _dunder_spans(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _DUNDER_RE.finditer(text):
        start, end = match.span()
        if _overlaps(start, end, protected):
            continue
        before = text[start - 1] if start else ""
        before_before = text[start - 2] if start > 1 else ""
        after = text[end] if end < len(text) else ""
        member = before == "."
        call = after == "("
        function_argument = before == "(" and bool(re.match(r"[A-Za-z0-9_]", before_before))
        indexed = after == "["
        if member or call or function_argument or indexed:
            spans.append((start, end))
    return spans


def _protect_literals(text: str) -> tuple[str, list[_Protection]]:
    spans = _code_spans(text)
    spans.extend(_dunder_spans(text, spans))
    spans.sort()
    protections: list[_Protection] = []
    output: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        token = f"\ue000IMSG{len(protections)}\ue001"
        while token in text:
            token += "x"
        output.append(text[cursor:start])
        output.append(token)
        protections.append(_Protection(token=token, value=text[start:end]))
        cursor = end
    output.append(text[cursor:])
    return "".join(output), protections


def _can_open(text: str, index: int, marker: str) -> bool:
    after_index = index + len(marker)
    after = text[after_index] if after_index < len(text) else ""
    if not after or after.isspace():
        return False
    if marker == "_":
        before = text[index - 1] if index else ""
        if before.isalnum() and after.isalnum():
            return False
    return True


def _can_close(text: str, index: int, marker: str) -> bool:
    before = text[index - 1] if index else ""
    if not before or before.isspace():
        return False
    after_index = index + len(marker)
    after = text[after_index] if after_index < len(text) else ""
    if marker == "_" and before.isalnum() and after.isalnum():
        return False
    return True


def _has_closer(text: str, start: int, marker: str) -> bool:
    index = text.find(marker, start)
    while index >= 0:
        if _can_close(text, index, marker):
            return True
        index = text.find(marker, index + len(marker))
    return False


def _render_supported_markdown(text: str) -> tuple[str, list[_Span]]:
    symmetric = {
        "**": "bold",
        "__": "bold",
        "~~": "strikethrough",
        "*": "italic",
        "_": "italic",
    }
    html_open = {"<u>": ("</u>", "underline"), "<ins>": ("</ins>", "underline")}
    output: list[str] = []
    output_length = 0
    stack: list[tuple[str, str, int]] = []
    spans: list[_Span] = []
    index = 0

    def append(value: str) -> None:
        nonlocal output_length
        output.append(value)
        output_length += len(value)

    while index < len(text):
        handled = False
        for opener, (closer, style) in html_open.items():
            if text.startswith(opener, index):
                close_index = text.lower().find(closer, index + len(opener))
                if close_index >= 0:
                    stack.append((closer, style, output_length))
                    index += len(opener)
                    handled = True
                break
        if handled:
            continue
        if stack and text.lower().startswith(stack[-1][0], index):
            closer, style, start = stack.pop()
            if output_length > start:
                spans.append(_Span(start, output_length, (style,)))
            index += len(closer)
            continue

        marker = next((value for value in symmetric if text.startswith(value, index)), None)
        if marker:
            style = symmetric[marker]
            if stack and stack[-1][0] == marker and _can_close(text, index, marker):
                _, _, start = stack.pop()
                if output_length > start:
                    spans.append(_Span(start, output_length, (style,)))
                index += len(marker)
                continue
            if _can_open(text, index, marker) and _has_closer(text, index + len(marker), marker):
                stack.append((marker, style, output_length))
                index += len(marker)
                continue

        append(text[index])
        index += 1

    # The closer pre-check normally prevents this. Preserve an unexpected
    # unmatched opener rather than silently deleting user-authored text.
    if stack:
        return text, []
    spans.sort(key=lambda span: (span.start, -(span.end - span.start), _STYLE_ORDER[span.styles[0]]))
    return "".join(output), spans


def _restore_literals(
    text: str, spans: list[_Span], protections: list[_Protection]
) -> tuple[str, list[_Span]]:
    edits: list[tuple[int, int, str]] = []
    for protection in protections:
        start = text.find(protection.token)
        if start >= 0:
            edits.append((start, start + len(protection.token), protection.value))
    edits.sort()
    if not edits:
        return text, spans

    def map_offset(offset: int) -> int:
        delta = 0
        for start, end, replacement in edits:
            if end <= offset:
                delta += len(replacement) - (end - start)
        return offset + delta

    output: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        output.append(text[cursor:start])
        output.append(replacement)
        cursor = end
    output.append(text[cursor:])
    return "".join(output), [
        _Span(map_offset(span.start), map_offset(span.end), span.styles) for span in spans
    ]


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def extract_markdown_format_runs(source: str) -> dict[str, Any]:
    """Return marker-free text and native format ranges in UTF-16 units."""
    protected, protections = _protect_literals(str(source or ""))
    rendered, spans = _render_supported_markdown(protected)
    restored, spans = _restore_literals(rendered, spans, protections)
    return {
        "text": restored,
        "ranges": [
            {
                "start": _utf16_length(restored[:span.start]),
                "length": _utf16_length(restored[span.start:span.end]),
                "styles": list(span.styles),
            }
            for span in spans
        ],
    }
