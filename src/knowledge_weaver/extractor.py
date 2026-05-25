"""Rule-based entity extractor for Knowledge Weaver.

Extracts structured entities from DMA daily memory sections using
regex patterns (Level 1: deterministic, Level 2: heuristic).
No LLM dependency — pure rule-based extraction.
"""

import json
import re
from dataclasses import dataclass, field

from pypinyin import lazy_pinyin

from knowledge_weaver.parser import ParsedSection, ParsedItem

# DMA 8 categories → entity type mapping
CATEGORY_TO_TYPE: dict[str, str] = {
    "核心要点": "fact",
    "决策与结论": "decision",
    "已完成事项": "task",
    "待办与计划": "task",
    "用户偏好与习惯": "preference",
    "技术/项目要点": "tech",
    "风险与注意事项": "risk",
    "创意与想法": "idea",
}

# Type prefix mapping for entity IDs
TYPE_PREFIX: dict[str, str] = {
    "project": "proj",
    "preference": "pref",
    "decision": "decision",
    "fact": "fact",
    "risk": "risk",
    "task": "task",
    "tech": "tech",
    "idea": "idea",
}

# Default importance by entity type
DEFAULT_IMPORTANCE: dict[str, float] = {
    "decision": 0.6,
    "preference": 0.6,
    "risk": 0.5,
    "project": 0.5,
    "task": 0.4,
    "tech": 0.4,
    "fact": 0.35,
    "idea": 0.3,
}

# --- Level 1: Deterministic patterns ---

# Known project names
_PROJECT_PATTERN = re.compile(
    r"(HomeBrain|心连心|SpotMicro|OpenClaw|Mnemo|Knowledge\s*Weaver)",
    re.IGNORECASE,
)

# Tech keywords (case-insensitive matching, output preserves original case)
_TECH_KEYWORDS = [
    "ESP32", "STM32", "Python", "HA", "HomeAssistant", "Docker",
    "Nginx", "MQTT", "Zigbee", "Wi-Fi", "Bluetooth", "Linux",
    "Rust", "Go", "Node.js", "React", "Vue", "TypeScript",
    "JavaScript", "SQLite", "PostgreSQL", "Redis", "Git",
    "Kubernetes", "K8s", "Terraform", "Ansible",
]

_TECH_PATTERN = re.compile(
    r"(?<![a-zA-Z])(" + "|".join(re.escape(kw) for kw in _TECH_KEYWORDS) + r")(?![a-zA-Z])",
    re.IGNORECASE,
)

# File path pattern
_FILE_PATH_PATTERN = re.compile(
    r"`?([a-zA-Z0-9_./\-]+\.[a-zA-Z]{1,10})`?"
)

# --- Level 2: Heuristic patterns ---

# Decision patterns
_DECISION_PATTERNS = [
    re.compile(r"决定(.+?)(?:[，,。；;]|$)"),
    re.compile(r"采用(.+?)方案"),
    re.compile(r"放弃(.+?)(?:[，,。；;]|$)"),
    re.compile(r"选择(.+?)(?:[，,。；;]|$)"),
]

# Preference patterns
_PREFERENCE_PATTERNS = [
    re.compile(r"用户(?:偏好|倾向|喜欢|习惯)(.+?)(?:[，,。；;]|$)"),
    re.compile(r"用户要求(.+?)(?:[，,。；;]|$)"),
]

# Risk patterns
_RISK_PATTERNS = [
    re.compile(r"(.+?)风险"),
    re.compile(r"(.+?)注意"),
    re.compile(r"(.+?)避免"),
    re.compile(r"(.+?)可能导致(.+)"),
]

# Completed task patterns
_TASK_COMPLETED_PATTERNS = [
    re.compile(r"完成了(.+?)(?:[，,。；;]|$)"),
    re.compile(r"实现了(.+?)(?:[，,。；;]|$)"),
    re.compile(r"部署了(.+?)(?:[，,。；;]|$)"),
    re.compile(r"已(?:完成|实现|部署)(.+?)(?:[，,。；;]|$)"),
]

# Todo task patterns
_TASK_TODO_PATTERNS = [
    re.compile(r"后续(.+?)(?:[，,。；;]|$)"),
    re.compile(r"计划(.+?)(?:[，,。；;]|$)"),
    re.compile(r"预计(.+?)(?:[，,。；;]|$)"),
    re.compile(r"待(?:办|完成|处理)(.+?)(?:[，,。；;]|$)"),
]


@dataclass
class ExtractedEntity:
    id: str
    type: str          # project / decision / preference / fact / risk / task / tech / idea
    name: str          # human-readable name
    summary: str       # extracted summary text
    source_lines: str  # JSON array of "file:start:end"
    metadata: dict = field(default_factory=dict)  # tags, status, etc.


def slugify(name: str) -> str:
    """Convert a name to lowercase_slug format for entity IDs.

    - Chinese characters are converted to pinyin (no tone marks)
    - Non-alphanumeric chars become underscores
    - Consecutive underscores collapsed
    - Truncated to 30 chars for stability
    """
    # Convert Chinese characters to pinyin
    parts = lazy_pinyin(name)
    s = "".join(parts).lower().strip()
    # Replace non-alphanumeric with underscore
    s = re.sub(r"[^a-z0-9]", "_", s)
    # Collapse consecutive underscores
    s = re.sub(r"_+", "_", s)
    # Strip leading/trailing underscores
    s = s.strip("_")
    # Truncate
    return s[:30]


def generate_entity_id(entity_type: str, name: str) -> str:
    """Generate entity_id from type prefix + slug."""
    prefix = TYPE_PREFIX.get(entity_type, entity_type)
    slug = slugify(name)
    return f"{prefix}:{slug}"


# --- Individual extractors (return list[dict]) ---


def extract_projects(text: str) -> list[dict]:
    """Extract project name entities from text."""
    results = []
    for m in _PROJECT_PATTERN.finditer(text):
        proj_name = m.group(0)
        # Normalize capitalization
        lower = proj_name.lower().replace(" ", "")
        for original in ["HomeBrain", "OpenClaw", "SpotMicro", "Mnemo", "KnowledgeWeaver"]:
            if lower == original.lower():
                proj_name = original
                break
        eid = generate_entity_id("project", proj_name)
        results.append({
            "id": eid,
            "type": "project",
            "name": proj_name,
            "summary": text[:200],
        })
    return results


def extract_tech_keywords(text: str) -> list[dict]:
    """Extract technology keyword entities from text."""
    results = []
    seen = set()
    for m in _TECH_PATTERN.finditer(text):
        # Preserve original casing from the text
        kw = m.group(0)
        kw_lower = kw.lower()
        if kw_lower in seen:
            continue
        seen.add(kw_lower)
        # Normalize: use canonical form from _TECH_KEYWORDS
        canonical = kw
        for tk in _TECH_KEYWORDS:
            if tk.lower() == kw_lower:
                canonical = tk
                break
        eid = generate_entity_id("tech", canonical)
        results.append({
            "id": eid,
            "type": "tech",
            "name": canonical,
            "summary": f"技术栈关键词: {canonical}",
        })
    return results


def extract_decisions(text: str) -> list[dict]:
    """Extract decision entities from text using heuristic patterns."""
    results = []
    for pat in _DECISION_PATTERNS:
        for m in pat.finditer(text):
            content = m.group(1).strip()
            if not content:
                continue
            name = content[:80]
            eid = generate_entity_id("decision", name)
            results.append({
                "id": eid,
                "type": "decision",
                "name": name,
                "summary": text[:200],
            })
    return results


def extract_preferences(text: str) -> list[dict]:
    """Extract preference entities from text."""
    results = []
    for pat in _PREFERENCE_PATTERNS:
        for m in pat.finditer(text):
            content = m.group(1).strip()
            if not content:
                continue
            name = content[:80]
            eid = generate_entity_id("preference", name)
            results.append({
                "id": eid,
                "type": "preference",
                "name": name,
                "summary": text[:200],
            })
    return results


def extract_risks(text: str) -> list[dict]:
    """Extract risk entities from text."""
    results = []
    for pat in _RISK_PATTERNS:
        for m in pat.finditer(text):
            # Build name from matched groups
            groups = [g for g in m.groups() if g]
            name = "：".join(g.strip() for g in groups if g.strip())[:80]
            if not name:
                name = text[:60]
            eid = generate_entity_id("risk", name)
            results.append({
                "id": eid,
                "type": "risk",
                "name": name,
                "summary": text[:200],
            })
    return results


def extract_tasks(text: str) -> list[dict]:
    """Extract task entities from text (both completed and todo)."""
    results = []

    # Completed tasks
    for pat in _TASK_COMPLETED_PATTERNS:
        for m in pat.finditer(text):
            content = m.group(1).strip()
            if not content:
                continue
            name = content[:80]
            eid = generate_entity_id("task", f"完成_{name}")
            results.append({
                "id": eid,
                "type": "task",
                "name": name,
                "summary": text[:200],
                "status": "completed",
            })

    # Todo tasks
    for pat in _TASK_TODO_PATTERNS:
        for m in pat.finditer(text):
            content = m.group(1).strip()
            if not content:
                continue
            name = content[:80]
            eid = generate_entity_id("task", f"计划_{name}")
            results.append({
                "id": eid,
                "type": "task",
                "name": name,
                "summary": text[:200],
                "status": "todo",
            })

    return results


# --- Section-level extraction ---


def extract_entities_from_item(
    item: ParsedItem,
    category: str,
    source_file: str,
) -> list[ExtractedEntity]:
    """Extract entities from a single parsed item using pattern matching.

    Uses the DMA category to determine primary entity type,
    then runs all applicable extractors.
    """
    text = item.text
    source_ref = f"{source_file}:{item.line_start}:{item.line_end}"
    entity_type = CATEGORY_TO_TYPE.get(category, "fact")
    seen_ids: set[str] = set()
    entities: list[ExtractedEntity] = []

    def _add(eid: str, etype: str, name: str, summary: str, **meta):
        if eid in seen_ids:
            return
        seen_ids.add(eid)
        entities.append(ExtractedEntity(
            id=eid,
            type=etype,
            name=name[:100],
            summary=summary[:500],
            source_lines=json.dumps([source_ref]),
            metadata=meta,
        ))

    # Level 1: Project name detection (from any category)
    for proj in extract_projects(text):
        _add(proj["id"], "project", proj["name"], proj["summary"])

    # Level 1: Tech keyword detection
    for tech in extract_tech_keywords(text):
        _add(tech["id"], "tech", tech["name"], tech["summary"])

    # Level 2: Category-specific heuristic extraction
    if entity_type == "decision":
        for d in extract_decisions(text):
            _add(d["id"], "decision", d["name"], d["summary"])
    elif entity_type == "preference":
        for p in extract_preferences(text):
            _add(p["id"], "preference", p["name"], p["summary"])
    elif entity_type == "risk":
        for r in extract_risks(text):
            _add(r["id"], "risk", r["name"], r["summary"])
    elif entity_type == "task":
        for t in extract_tasks(text):
            status = t.get("status", "todo")
            _add(t["id"], "task", t["name"], t["summary"], status=status)

    # If no heuristic match found, create a default entity for the item
    if not entities:
        name = _extract_entity_name(text, entity_type)
        eid = generate_entity_id(entity_type, name)
        _add(eid, entity_type, name, text)

    return entities


def extract_entities_from_section(
    section: ParsedSection,
    source_file: str,
) -> list[ExtractedEntity]:
    """Extract entities from a parsed section using rules.

    Section has a single category. Iterates all items, deduplicating by entity ID.
    """
    all_entities: list[ExtractedEntity] = []
    seen_ids: set[str] = set()

    for item in section.items:
        item_entities = extract_entities_from_item(item, section.category, source_file)
        for entity in item_entities:
            if entity.id in seen_ids:
                continue
            seen_ids.add(entity.id)
            all_entities.append(entity)

    return all_entities


def _extract_entity_name(text: str, entity_type: str) -> str:
    """Extract a short meaningful name from text based on entity type."""
    # Try sentence patterns for decisions
    if entity_type == "decision":
        for pat in _DECISION_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1).strip()[:50]

    # Preference patterns
    if entity_type == "preference":
        for pat in _PREFERENCE_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1).strip()[:50]

    # Risk patterns
    if entity_type == "risk":
        for pat in _RISK_PATTERNS:
            m = pat.search(text)
            if m:
                groups = [g for g in m.groups() if g and g.strip()]
                return "：".join(g.strip() for g in groups)[:50]

    # Task patterns
    if entity_type == "task":
        for pat in _TASK_COMPLETED_PATTERNS + _TASK_TODO_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1).strip()[:50]

    # Default: first clause or first 50 chars
    first = text.split("。")[0].split("；")[0].split("，")[0]
    return first[:50]
