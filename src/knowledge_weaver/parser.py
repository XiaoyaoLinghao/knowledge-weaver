import re
from dataclasses import dataclass, field

CATEGORY_NAMES = [
    "核心要点", "决策与结论", "已完成事项", "待办与计划",
    "用户偏好与习惯", "技术/项目要点", "风险与注意事项", "创意与想法",
]

MD_CATEGORIES = {f"**{c}**": c for c in CATEGORY_NAMES}


@dataclass
class ParsedItem:
    text: str
    line_range: tuple[int, int]  # (start, end) 1-indexed


@dataclass
class ParsedSection:
    date: str
    time: str
    categories: dict[str, list[ParsedItem]] = field(default_factory=dict)


# Aliases for backward compat with plan naming
DailyItem = ParsedItem
DailySegment = ParsedSection


def parse_daily_file(filepath: str) -> list[ParsedSection]:
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return []

    date = _extract_date(lines)
    if not date:
        return []

    segments: list[ParsedSection] = []
    current_seg: ParsedSection | None = None
    current_cat: str | None = None
    i = 0

    while i < len(lines):
        line = lines[i]

        time_match = re.match(r"^##\s+(\d{2}:\d{2})", line)
        if time_match:
            if current_seg:
                segments.append(current_seg)
            current_seg = ParsedSection(date=date, time=time_match.group(1))
            current_cat = None
            i += 1
            continue

        cat_found = False
        for md_cat, cat_name in MD_CATEGORIES.items():
            if line.strip() == md_cat:
                cat_found = True
                if current_seg is None:
                    current_seg = ParsedSection(date=date, time="00:00")
                current_cat = cat_name
                if current_cat not in current_seg.categories:
                    current_seg.categories[current_cat] = []
                break

        if cat_found:
            i += 1
            continue

        if current_seg and current_cat:
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                item_text = stripped[2:].strip()
                current_seg.categories[current_cat].append(
                    ParsedItem(text=item_text, line_range=(i + 1, i + 1))
                )

        i += 1

    if current_seg:
        segments.append(current_seg)

    return segments


def _extract_date(lines: list[str]) -> str | None:
    for line in lines[:10]:
        m = re.match(r"^#\s+(\d{4}-\d{2}-\d{2})", line)
        if m:
            return m.group(1)
    return None
