"""Tests for the entity extractor module."""

from knowledge_weaver.parser import ParsedSection, ParsedItem
from knowledge_weaver.extractor import (
    ExtractedEntity,
    generate_entity_id,
    slugify,
    extract_entities_from_section,
    extract_entities_from_item,
    extract_projects,
    extract_decisions,
    extract_preferences,
    extract_risks,
    extract_tasks,
    extract_tech_keywords,
)


def _make_section(categories: dict[str, list[str]] | None = None) -> ParsedSection:
    """Helper to build a ParsedSection with text items."""
    # Collect all items under the first category
    items: list[ParsedItem] = []
    first_cat = ""
    if categories:
        for cat, texts in categories.items():
            if first_cat == "":
                first_cat = cat
            for text in texts:
                items.append(ParsedItem(text=text, time=None, line_start=1, line_end=1))
    return ParsedSection(title=first_cat, category=first_cat, items=items)


# --- generate_entity_id / slugify ---

def test_generate_entity_id():
    result = generate_entity_id("decision", "设备聚合采用规则引擎优先")
    assert result.startswith("decision:")
    assert "_" not in result
    assert "exampleproject" in generate_entity_id("project", "ExampleProject")
    assert generate_entity_id("preference", "用户偏好将功能集成在 ExampleProject 内部").startswith("pref:")


def test_slugify_basic():
    assert slugify("ExampleProject") == "exampleproject"
    assert slugify("HA Entity Naming Rule") == "ha_entity_naming_rule"
    assert slugify("Docker Build Cache") == "docker_build_cache"


# --- extract_projects ---

def test_extract_project_entity():
    text = "ExampleProject项目需要保持聚合器稳定运行"
    results = extract_projects(text)
    assert len(results) >= 1
    names = [r["name"] for r in results]
    assert "ExampleProject" in names
    for r in results:
        assert r["type"] == "project"


# --- extract_decisions ---

def test_extract_decision_entity():
    text = "决定将聚合逻辑集成到ExampleProject自身，不依赖外部脚本"
    results = extract_decisions(text)
    assert len(results) >= 1
    assert results[0]["type"] == "decision"
    # Should extract a meaningful name, not the whole sentence
    assert len(results[0]["name"]) < len(text)


# --- extract_preferences ---

def test_extract_preference_entity():
    text = "用户偏好将功能集成在ExampleProject内部，而非外部脚本"
    results = extract_preferences(text)
    assert len(results) >= 1
    assert results[0]["type"] == "preference"


# --- extract_risks ---

def test_extract_risk_entity():
    text = "LLM聚合存在JSON格式错乱、输出截断的风险"
    results = extract_risks(text)
    assert len(results) >= 1
    assert results[0]["type"] == "risk"


# --- extract_tasks ---

def test_extract_task_completed():
    text = "完成了ExampleProject设备聚合API的开发和测试"
    results = extract_tasks(text)
    assert len(results) >= 1
    assert results[0]["type"] == "task"


def test_extract_task_todo():
    text = "后续计划给DemoRobot增加视觉避障模块"
    results = extract_tasks(text)
    assert len(results) >= 1
    assert results[0]["type"] == "task"


# --- extract_tech_keywords ---

def test_extract_tech_keyword():
    text = "使用ESP32和Python开发，部署在Docker中，HA集成通过STM32"
    results = extract_tech_keywords(text)
    assert len(results) >= 1
    keywords = [r["name"] for r in results]
    assert "ESP32" in keywords
    assert "Python" in keywords
    assert "Docker" in keywords


# --- extract_entities_from_item ---

def test_extract_from_item():
    item = ParsedItem(text="决定使用规则引擎而非LLM批量处理", time=None, line_start=5, line_end=5)
    entities = extract_entities_from_item(item, "决策与结论", "2026-05-24.md")
    assert len(entities) >= 1
    assert entities[0].type == "decision"
    assert "2026-05-24.md:5:5" in entities[0].source_lines


# --- extract_entities_from_section ---

def test_extract_from_section():
    sec = _make_section(categories={
        "决策与结论": ["决定使用规则引擎而非LLM批量处理"],
        "风险与注意事项": ["Docker build缓存可能导致nginx配置未更新"],
    })
    entities = extract_entities_from_section(sec, "2026-05-24.md")
    types = {e.type for e in entities}
    assert "decision" in types or "risk" in types
    assert len(entities) >= 1


# --- dedup ---

def test_extract_and_dedup():
    sec = _make_section(categories={
        "技术/项目要点": [
            "HA实体命名规则：{房间} {设备名} [{子类别}] {参数}，95.9%包含三段",
            "HA实体命名规则：{房间} {设备名} [{子类别}] {参数}，95.9%包含三段",
        ]
    })
    entities = extract_entities_from_section(sec, "2026-05-24.md")
    ids = [e.id for e in entities]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"


# --- Noise filter tests (Work Item 1) ---


def test_timestamp_log_is_filtered():
    """Timestamp log entries like '15:20 UTC - 先生上线' must not become fact entities."""
    item = ParsedItem(text="15:20 UTC - 先生上线", time=None, line_start=1, line_end=1)
    entities = extract_entities_from_item(item, "核心要点", "2026-05-25.md")
    # The text itself is garbage (timestamp log), so no entity should be produced
    for e in entities:
        assert e.type != "fact" or "UTC" not in e.name
    # More directly: _is_garbage_name should catch it
    from knowledge_weaver.extractor import _is_garbage_name
    assert _is_garbage_name("15:20 UTC - 先生上线")


def test_tech_common_words_filtered():
    """Generic tech abbreviations like AI, API should not produce tech entities."""
    text = "使用 AI 和 API 进行开发"
    results = extract_tech_keywords(text)
    names = [r["name"] for r in results]
    assert "AI" not in names
    assert "API" not in names


def test_tech_common_word_with_context_kept():
    """When a common word is part of a larger term, the larger term should be kept."""
    text = "AI聚合策略v3方案"
    results = extract_tech_keywords(text)
    names = [r["name"] for r in results]
    # "AI聚合策略v3方案" is a composite term — AI alone is filtered,
    # but the full term is NOT just a common word
    # At minimum, no entity should be just "AI"
    for r in results:
        assert r["name"].strip().upper() != "AI"


def test_structural_tech_filtered():
    """Structural markers like P0, P1, date patterns should not produce tech entities."""
    text = "P0 P1 2026-05"
    results = extract_tech_keywords(text)
    names = [r["name"] for r in results]
    assert "P0" not in names
    assert "P1" not in names
    assert "2026-05" not in names


def test_project_regex_does_not_swallow_verbs():
    from knowledge_weaver.extractor import extract_projects
    r = extract_projects("启动ExampleProject项目，确认采用模块化架构")
    names = [p["name"] for p in r]
    assert "ExampleProject" in names
    assert "启动ExampleProject" not in names


def test_project_regex_rejects_verbal_phrases():
    from knowledge_weaver.extractor import extract_projects
    r = extract_projects("决定基于开源框架作为项目后端基础")
    names = [p["name"] for p in r]
    assert all("决定" not in n for n in names)
    assert all("基于" not in n for n in names)

def test_tech_does_not_extract_cjk_numbers():
    from knowledge_weaver.extractor import extract_tech_keywords
    for text in ["2000+实体压缩87%，耗时<1秒", "但95%以上实体命名包含三段"]:
        r = extract_tech_keywords(text)
        for kw in r:
            assert not any('一' <= ch <= '鿿' for ch in kw["name"]), \
                f"unexpected CJK in tech name: {kw['name']!r}"


def test_tech_still_extracts_versioned_terms():
    from knowledge_weaver.extractor import extract_tech_keywords
    r = extract_tech_keywords("Use Python3 and ESP32-S3 with Node18")
    names = [k["name"] for k in r]
    assert "Python3" in names
    assert any("ESP32" in n for n in names)
