# Knowledge Weaver

MCP Server for structured knowledge retrieval on top of DMA daily memory files.

## Overview

Knowledge Weaver provides an MCP (Model Context Protocol) server that enables structured retrieval of knowledge from DMA daily memory files. It extracts entities (decisions, projects, risks, preferences, etc.) and their relationships, stores them in a SQLite database with FTS5 and optional sqlite-vec vector search, and exposes 7 query tools + 3 resources.

## Architecture

```
DMA daily .md files → parser → extractor → linker → scorer → embed → DB
                                                                ↓
                                              MCP tools (7) + resources (3)
```

## Setup

```bash
pip install -e ".[dev]"
```

## Usage

### As an MCP server (stdio)

```bash
python -m knowledge_weaver           # default: starts MCP server
python -m knowledge_weaver serve     # explicit
```

### Run consolidation once (offline)

```bash
python -m knowledge_weaver consolidate
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `KNOWLEDGE_WEAVER_DB_PATH` | `$HOME/.openclaw/knowledge/knowledge.db` | SQLite db path |
| `KNOWLEDGE_WEAVER_MEMORY_DIR` | `$HOME/.openclaw/workspace/memory` | DMA daily files dir |
| `KNOWLEDGE_WEAVER_LOG_LEVEL` | `INFO` | logging level |
| `EMBEDDING_BASE_URL` | — | OpenAI-compatible base URL |
| `EMBEDDING_API_KEY` | — | API key |
| `EMBEDDING_MODEL` | — | model name |
| `EMBEDDING_DIMENSION` | `1024` | vector dimension |

## DMA daily memory file format

DMA daily memory files use YAML frontmatter + markdown sections:

```markdown
---
title: Daily Memory
date: 2026-05-24
---

## 核心要点
- key point 1
- key point 2

## 决策与结论
- 决策: desc, 背景: context
- 结论1: desc, 背景: context
```

The 8 recognized section headings map to entity types:
`核心要点`→fact, `决策与结论`→decision, `已完成事项`→task, `待办与计划`→task,
`用户偏好与习惯`→preference, `技术/项目要点`→tech, `风险与注意事项`→risk, `创意与想法`→idea

## MCP tools

| Tool | Description | Key Parameters |
|---|---|---|
| `knowledge_search` | Search entities by keyword or semantic similarity | query, entity_type?, max_results?, min_score? |
| `knowledge_trace` | Trace a topic's full relationship graph | topic, max_depth? |
| `active_projects` | List currently active projects | lookback_days? |
| `preference_lookup` | Look up user preferences and habits | topic?, domain? |
| `decision_history` | Query historical decisions and rationale | topic, include_risk? |
| `knowledge_stats` | Knowledge base health metrics | — |
| `knowledge_consolidate` | Manually trigger consolidation | — |

## MCP resources

| URI | Description |
|---|---|
| `knowledge://stats` | System status overview |
| `knowledge://entity/{entity_id}` | Individual entity details + relations |
| `knowledge://days/{date}` | Daily manifest entry (YYYY-MM-DD) |

## Optional: sqlite-vec & FTS5

- **sqlite-vec**: Enables vector similarity search. Install with `pip install sqlite-vec`. Falls back gracefully to FTS5 + LIKE if unavailable.
- **FTS5**: Full-text search via `entity_fts` virtual table. Always enabled.

## Development

```bash
pytest                                              # run all tests
python -m knowledge_weaver consolidate              # manual consolidation
python scripts/clean_and_rescore.py --db-path <db>   # cleanup noise entities
python scripts/re_embed.py                           # rebuild vector embeddings
```

## License

Private — internal use only.
