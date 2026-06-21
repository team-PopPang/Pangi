from __future__ import annotations

import re


FENCE_PATTERN = re.compile(r"^\s*```")
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
UNORDERED_LIST_PATTERN = re.compile(r"^(\s*)[*+]\s+(.+)$")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK_PATTERN = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
BOLD_PATTERN = re.compile(r"\*\*([^*\n]+)\*\*")


def markdown_to_slack(text: str | None) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    output: list[str] = []
    in_code_fence = False
    index = 0

    while index < len(lines):
        line = lines[index]

        if FENCE_PATTERN.match(line):
            in_code_fence = not in_code_fence
            output.append(line)
            index += 1
            continue

        if in_code_fence:
            output.append(line)
            index += 1
            continue

        if _is_table_start(lines, index):
            index = _append_table_as_code_block(lines, index, output)
            continue

        transformed, is_heading = _transform_line(line)
        output.append(transformed)
        if is_heading and _has_next_content_line(lines, index):
            output.append("")
        index += 1

    return _collapse_excess_blank_lines("\n".join(output)).strip()


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and TABLE_SEPARATOR_PATTERN.match(lines[index + 1]) is not None


def _append_table_as_code_block(lines: list[str], index: int, output: list[str]) -> int:
    output.append("```")
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        output.append(lines[index])
        index += 1
    output.append("```")
    return index


def _transform_line(line: str) -> tuple[str, bool]:
    heading = HEADING_PATTERN.match(line)
    if heading:
        title = _transform_inline(heading.group(1).strip())
        return f"*{title}*", True

    unordered = UNORDERED_LIST_PATTERN.match(line)
    if unordered:
        return f"{unordered.group(1)}- {_transform_inline(unordered.group(2))}", False

    return _transform_inline(line), False


def _transform_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in INLINE_CODE_PATTERN.finditer(text):
        parts.append(_transform_non_code_inline(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_transform_non_code_inline(text[cursor:]))
    return "".join(parts)


def _transform_non_code_inline(text: str) -> str:
    text = IMAGE_PATTERN.sub(_replace_image, text)
    text = LINK_PATTERN.sub(r"<\2|\1>", text)
    return BOLD_PATTERN.sub(r"*\1*", text)


def _replace_image(match: re.Match[str]) -> str:
    label = match.group(1).strip() or "image"
    url = match.group(2)
    return f"<{url}|{label}>"


def _has_next_content_line(lines: list[str], index: int) -> bool:
    return index + 1 < len(lines) and bool(lines[index + 1].strip())


def _collapse_excess_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)
