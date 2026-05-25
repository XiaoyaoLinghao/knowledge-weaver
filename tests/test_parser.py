"""Tests for the DMA file parser module."""

import os
import pytest
from knowledge_weaver.parser import (
    ParsedItem,
    ParsedSection,
    ParsedFile,
    parse_dma_file,
    parse_dma_content,
    DMA_CATEGORY_MAP,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")


# ---------------------------------------------------------------------------
# test_parse_empty_file
# ---------------------------------------------------------------------------

def test_parse_empty_file():
    result = parse_dma_content("")
    assert isinstance(result, ParsedFile)
    assert result.sections == []


def test_parse_empty_file_with_frontmatter_only():
    content = "---\ntitle: test\ndate: '2026-05-24'\n---\n"
    result = parse_dma_content(content)
    assert result.sections == []
    assert result.date == "2026-05-24"


# ---------------------------------------------------------------------------
# test_parse_frontmatter
# ---------------------------------------------------------------------------

def test_parse_frontmatter():
    content = "---\ntitle: '2026年05月24日 - 会话记忆'\ndate: '2026-05-24'\n---\n\n## 核心要点\n- 09:30 - test item\n"
    result = parse_dma_content(content)
    assert result.date == "2026-05-24"
    assert result.title == "2026年05月24日 - 会话记忆"


def test_parse_frontmatter_missing_date():
    content = "---\ntitle: 'some title'\n---\n\n## 核心要点\n- test\n"
    result = parse_dma_content(content, default_date="2026-01-01")
    assert result.date == "2026-01-01"


def test_parse_frontmatter_uses_default_date_when_absent():
    content = "## 核心要点\n- test\n"
    result = parse_dma_content(content, default_date="2026-05-20")
    assert result.date == "2026-05-20"


# ---------------------------------------------------------------------------
# test_parse_sections
# ---------------------------------------------------------------------------

def test_parse_sections():
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n- 09:00 - item1\n\n"
        "## 决策与结论\n- 10:00 - item2\n"
    )
    result = parse_dma_content(content)
    assert len(result.sections) == 2
    assert result.sections[0].title == "核心要点"
    assert result.sections[1].title == "决策与结论"


def test_parse_sections_preserve_unknown_heading():
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 自定义章节\n- some item\n"
    )
    result = parse_dma_content(content)
    assert len(result.sections) == 1
    assert result.sections[0].title == "自定义章节"
    # Unknown section → category should default to "fact"
    assert result.sections[0].category == "fact"


# ---------------------------------------------------------------------------
# test_parse_items
# ---------------------------------------------------------------------------

def test_parse_items_with_timestamp():
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n"
        "- 09:30 - 第一条要点内容\n"
        "- 10:15 - 第二条要点内容\n"
    )
    result = parse_dma_content(content)
    section = result.sections[0]
    assert len(section.items) == 2

    item1 = section.items[0]
    assert item1.time == "09:30"
    assert "第一条要点内容" in item1.text

    item2 = section.items[1]
    assert item2.time == "10:15"
    assert "第二条要点内容" in item2.text


def test_parse_items_without_timestamp():
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n"
        "- 没有时间戳的条目\n"
    )
    result = parse_dma_content(content)
    item = result.sections[0].items[0]
    assert item.time is None
    assert "没有时间戳的条目" in item.text


def test_parse_items_with_seconds_timestamp():
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n"
        "- 09:30:45 - 精确到秒的条目\n"
    )
    result = parse_dma_content(content)
    item = result.sections[0].items[0]
    assert item.time == "09:30:45"


def test_parse_items_bold_markers():
    """Items may contain **bold** text markers that should be preserved in text."""
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n"
        "- 09:00 - **重要**的要点内容\n"
    )
    result = parse_dma_content(content)
    item = result.sections[0].items[0]
    assert "**重要**" in item.text


# ---------------------------------------------------------------------------
# test_parse_sample_file
# ---------------------------------------------------------------------------

def test_parse_sample_file():
    filepath = os.path.join(FIXTURES, "sample_2026-05-24.md")
    result = parse_dma_file(filepath)

    assert isinstance(result, ParsedFile)
    assert result.date == "2026-05-24"
    assert "会话记忆" in result.title
    assert len(result.sections) == 8  # 8 DMA categories

    # Verify each section has items
    for section in result.sections:
        assert len(section.items) > 0, f"Section '{section.title}' has no items"


# ---------------------------------------------------------------------------
# test_parse_dma_structure
# ---------------------------------------------------------------------------

def test_parse_dma_structure():
    """Verify the 8 DMA categories are recognized and mapped correctly."""
    content = (
        "---\ntitle: test\ndate: '2026-05-24'\n---\n"
        "## 核心要点\n- 09:00 - a\n\n"
        "## 决策与结论\n- 10:00 - b\n\n"
        "## 已完成事项\n- 11:00 - c\n\n"
        "## 待办与计划\n- 12:00 - d\n\n"
        "## 用户偏好与习惯\n- 13:00 - e\n\n"
        "## 技术/项目要点\n- 14:00 - f\n\n"
        "## 风险与注意事项\n- 15:00 - g\n\n"
        "## 创意与想法\n- 16:00 - h\n"
    )
    result = parse_dma_content(content)

    section_map = {s.title: s for s in result.sections}
    assert len(result.sections) == 8

    for title, expected_cat in DMA_CATEGORY_MAP.items():
        assert title in section_map, f"Missing section: {title}"
        assert section_map[title].category == expected_cat, (
            f"Section '{title}' mapped to '{section_map[title].category}', expected '{expected_cat}'"
        )


# ---------------------------------------------------------------------------
# test_parse_line_range_tracking
# ---------------------------------------------------------------------------

def test_parse_line_range_tracking():
    content = (
        "---\n"              # line 1
        "title: test\n"      # line 2
        "date: '2026-05-24'\n"  # line 3
        "---\n"              # line 4
        "\n"                 # line 5
        "## 核心要点\n"       # line 6
        "- 09:00 - item one\n"  # line 7
        "- 10:00 - item two\n"  # line 8
    )
    result = parse_dma_content(content)
    items = result.sections[0].items

    assert len(items) == 2
    # First item starts at line 7
    assert items[0].line_start == 7
    assert items[0].line_end == 7
    # Second item starts at line 8
    assert items[1].line_start == 8
    assert items[1].line_end == 8


def test_parse_line_range_multiline_item():
    """Multi-line items (continuation lines) should have correct line ranges."""
    content = (
        "---\n"
        "title: test\n"
        "date: '2026-05-24'\n"
        "---\n"
        "\n"
        "## 核心要点\n"
        "- 09:00 - 这是一个\n"
        "  跨越多行的条目\n"
        "- 10:00 - 短条目\n"
    )
    result = parse_dma_content(content)
    items = result.sections[0].items

    assert len(items) == 2
    # First item spans lines 7-8
    assert items[0].line_start == 7
    assert items[0].line_end == 8
    # Second item on line 9
    assert items[1].line_start == 9
    assert items[1].line_end == 9


# ---------------------------------------------------------------------------
# test_parse_file_not_found
# ---------------------------------------------------------------------------

def test_parse_file_not_found():
    result = parse_dma_file("/nonexistent/path/to/file.md")
    assert isinstance(result, ParsedFile)
    assert result.sections == []


# ---------------------------------------------------------------------------
# test_parse_all_three_fixtures
# ---------------------------------------------------------------------------

def test_parse_all_three_fixtures():
    """All three fixture files should parse successfully with 8 sections each."""
    for filename in ["sample_2026-05-24.md", "sample_2026-05-23.md", "sample_2026-05-22.md"]:
        filepath = os.path.join(FIXTURES, filename)
        result = parse_dma_file(filepath)
        assert len(result.sections) == 8, f"{filename}: expected 8 sections, got {len(result.sections)}"
        for section in result.sections:
            assert len(section.items) > 0, f"{filename}: section '{section.title}' has no items"
