"""DMA daily memory file parser.

Parses DMA-produced `memory/YYYY-MM-DD.md` files into structured data.
These files have a YAML frontmatter block, then sections marked by `##` headings.
Each section has bullet items, optionally prefixed with timestamps like `HH:MM` or `HH:MM:SS`.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

# Category mapping: DMA section headings → canonical entity types
DMA_CATEGORY_MAP: dict[str, str] = {
    "核心要点": "fact",
    "决策与结论": "decision",
    "已完成事项": "task",
    "待办与计划": "task",
    "用户偏好与习惯": "preference",
    "技术/项目要点": "tech",
    "风险与注意事项": "risk",
    "创意与想法": "idea",
}

# Regex for timestamp extraction: HH:MM or HH:MM:SS at the start of an item
_TIMESTAMP_RE = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*")
# Regex for YAML frontmatter
_FRONTMATTER_RE = re.compile(r"^---\s*$")


@dataclass
class ParsedItem:
    """A single bullet item within a section."""
    text: str
    time: str | None  # extracted timestamp like "09:30" or "09:30:45"
    line_start: int   # 1-indexed
    line_end: int     # 1-indexed


@dataclass
class ParsedSection:
    """A section marked by a ## heading."""
    title: str           # e.g., "核心要点", "决策与结论"
    category: str        # mapped: 核心要点→fact, 决策与结论→decision, etc.
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParsedFile:
    """A fully parsed DMA daily memory file."""
    date: str            # from frontmatter or filename
    title: str           # from frontmatter
    sections: list[ParsedSection] = field(default_factory=list)


def parse_dma_file(filepath: str) -> ParsedFile:
    """Parse a DMA daily memory file into structured sections and items."""
    path = Path(filepath)
    if not path.exists():
        return ParsedFile(date="", title="", sections=[])

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ParsedFile(date="", title="", sections=[])

    # Try to extract date from filename as fallback
    filename_date = _extract_date_from_filename(path.name)
    return parse_dma_content(content, default_date=filename_date)


def parse_dma_content(content: str, default_date: str | None = None) -> ParsedFile:
    """Parse DMA content string (for testing without file I/O)."""
    lines = content.splitlines()

    # Phase 1: Extract frontmatter
    title, date, body_start = _parse_frontmatter(lines)
    if not date:
        date = default_date or ""

    # Phase 2: Parse sections and items from remaining lines
    sections = _parse_body(lines, body_start)

    return ParsedFile(date=date, title=title, sections=sections)


def _parse_frontmatter(lines: list[str]) -> tuple[str, str, int]:
    """Extract YAML frontmatter. Returns (title, date, body_start_line_index)."""
    title = ""
    date = ""
    body_start = 0

    if not lines or lines[0].strip() != "---":
        return title, date, body_start

    # Find closing ---
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return title, date, body_start

    # Parse YAML-like key: value pairs
    for line in lines[1:end_idx]:
        stripped = line.strip()
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key == "title":
                title = value
            elif key == "date":
                date = value

    body_start = end_idx + 1
    return title, date, body_start


def _parse_body(lines: list[str], start: int) -> list[ParsedSection]:
    """Parse sections and items from the body of the document."""
    sections: list[ParsedSection] = []
    current_section: ParsedSection | None = None
    i = start

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines
        if not stripped:
            i += 1
            continue

        # Detect section heading: ## Title
        heading_match = re.match(r"^##\s+(.+)$", stripped)
        if heading_match:
            heading = heading_match.group(1).strip()
            category = DMA_CATEGORY_MAP.get(heading, "fact")
            current_section = ParsedSection(title=heading, category=category)
            sections.append(current_section)
            i += 1
            continue

        # Detect bullet item: - content
        if stripped.startswith("- ") and current_section is not None:
            item, end_i = _parse_item(lines, i)
            current_section.items.append(item)
            i = end_i + 1
            continue

        i += 1

    return sections


def _parse_item(lines: list[str], start: int) -> tuple[ParsedItem, int]:
    """Parse a single bullet item, possibly spanning multiple lines.

    Returns (ParsedItem, last_line_index_inclusive).
    """
    # Extract the item text from the first line
    first_text = lines[start].strip()
    assert first_text.startswith("- "), f"Expected bullet item at line {start + 1}"
    raw_text = first_text[2:].strip()

    # Extract timestamp
    time_match = _TIMESTAMP_RE.match(raw_text)
    time_str: str | None = None
    if time_match:
        time_str = time_match.group(1)
        raw_text = raw_text[time_match.end():].strip()

    # Collect continuation lines (indented lines that follow)
    end_i = start
    j = start + 1
    while j < len(lines):
        next_line = lines[j]
        # Continuation: indented line (starts with spaces) and not empty, not a heading, not a bullet
        if next_line and (next_line[0] in (" ", "\t")) and next_line.strip():
            stripped_next = next_line.strip()
            # But if it looks like a new bullet or heading, stop
            if stripped_next.startswith("- ") or re.match(r"^##\s+", stripped_next):
                break
            raw_text += " " + stripped_next
            end_i = j
            j += 1
        else:
            break

    return ParsedItem(
        text=raw_text,
        time=time_str,
        line_start=start + 1,  # 1-indexed
        line_end=end_i + 1,    # 1-indexed
    ), end_i


def _extract_date_from_filename(filename: str) -> str | None:
    """Try to extract a YYYY-MM-DD date from a filename like '2026-05-24.md'."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", filename)
    return m.group(1) if m else None
