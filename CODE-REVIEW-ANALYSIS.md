# Knowledge Weaver + DMA 系统代码审核与优化分析

**分析日期**: 2026-05-28  
**分析范围**: v0.2.2 (commit `d39bd3a`) + 生产数据库  
**分析目标**: 对照 2026-05-26 评估报告的 11 项优化缺口，结合生产数据实测，提供当前真实状态与新发现问题  

---

## 执行摘要

### 核心发现

1. **评估报告严重过时**: 2026-05-26 报告中的关键缺陷（D1: pipeline 未传 entity_type, D2: RELATES_TO=0, D3: Dreaming 开启）已在 v0.2.2（2026-05-28 提交）中被全部修复。报告中的"未实施"项（FTS5, sqlite-vec 支持, N+1 消除, cache, access log 闭环）大多数已在 v0.2.2 落地。**评估报告仅分析到 v0.2.1，遗漏了 v0.2.2 的所有修复。**

2. **生产状态 vs 报告差异大**: 
   - RELATES_TO 不是 0 而是 2,900 条（含 1,136 name_mention + 1,056 co_occurrence + 698 shared_tokens）
   - 实体 2,221 个（非 1,845），关系 5,779 条（非 2,612）
   - 数据库 158 MB（非 70.86 MB），碎片率 56%（非 5.69%）
   - Memory-core 表不存在，Dreaming 配置为空对象 `{}`

3. **新的严重问题**: 
   - **维度常量不一致**: `db.py` 中 `DEFAULT_DIMENSION = 2560` 但 `embedder.py` 中 `DEFAULT_DIMENSION = 1024`，且生产数据库实际存储 1024 维向量。`entity_vec` 虚拟表按 1024 创建。若重建 DB 会因维度不匹配导致全量失败
   - **数据库碎片 56%**: 大量 DELETE/UPDATE 操作未定期 VACUUM，158 MB 数据库中 56% 为空页
   - **sqlite-vec 未真正用于搜索**: 虽然模组已安装且 `entity_vec` 表有数据，但 `_search_entity_vectors_vec` 中查询使用了 `ORDER BY distance LIMIT ?` 而非 sqlite-vec 推荐的 `AND k = ?` 语法，部分 sqlite-vec 版本可能拒绝该语法

4. **N+1 查询已消除，但不是完全消除**: `active_projects` 已改为预加载所有 tasks（v0.2.2 修复），`knowledge_trace` BFS 使用批量加载，`decision_history` 预加载关系。但 `knowledge_search` 中每个结果仍调用 `get_relations_for_entity` 获取 related_entities

5. **权重分布导致决策类实体评分仍偏低**: 即使 D1 已修复，freshness(0.15) + diversity(0.05) + richness(0.05) 合计仅 0.25，type_base 权重 0.35 虽高但决策实体 day_count 通常低（不每天重复），导致实际加分有限

---

## 已验证的优化状态

对照 2026-05-26 评估报告的优化缺口清单 C.1 + C.2：

| 编号 | 优化项 | 原始报告状态 | 实际状态 | 证据 |
|:-----|:-------|:-------------|:---------|:-----|
| **P0-4** | sqlite-vec 向量引擎 | ❌ 未实施 | ✅ **已完成但未启用** | `db.py:49-60` `_init_vec_virtual_table` 存在；`db.py:85-98` `init_db` 中尝试加载；`db.py:270-281` `_can_use_vec` 检查。sqlite_vec 模块已安装。但 Python O(N) 代码仍在运行，因为 search 入口在 `search_entity_vectors` 中先走 `_can_use_vec` 后 fallback |
| **P1-1** | Embedding 维度统一声明 | 🟡 部分完成 | 🟡 **部分完成但不一致** | `db.py:11` `DEFAULT_DIMENSION = 2560` vs `embedder.py:17` `DEFAULT_DIMENSION = 1024`。生产向量实际为 1024 维。DB 中 entity_vec DDL 为 float[1024]。若重建 DB, `_init_vec_virtual_table` 会用 2560 创建虚拟表，导致写入 1024 维向量时维度不匹配 |
| **P1-3** | 工具层 N+1 查询消除 | ❌ 未实施 | 🟡 **部分消除** | `tools.py:328-333`: `active_projects` 改为预加载 `all_tasks`（✅）。`tools.py:236-244`: `knowledge_trace` BFS 改为批量查询（✅）。`tools.py:442-455`: `decision_history` 预加载关系（✅）。但 `tools.py:178-185`: `knowledge_search` 每个结果仍循环调用 `get_relations_for_entity`（🚩 残存） |
| **P1-4** | 名称碎片化治理 | 🟡 部分完成 | ✅ **已改进** | `pipeline.py:237-265`: `_find_similar_entity` 增加了 embedding 第二遍扫描（v0.2.2 新增）。`extractor.py:577-593`: 新增 `_compact_name` 函数按从句边界截断实体名。`_DURATION_RE` 正则过滤 "1-2h）" 类片段。生产 DB 中重复 task slug 数量已减少 |
| **P2-1** | 多级缓存设计 | ❌ 未实施 | ❌ **未实施** | 整个代码库无任何缓存层。每次 MCP tool 调用都新开 DB 连接 + 全量 SQL 查询。`server.py:49-51` 每个 tool 都调 `_get_conn()` 新建连接 |
| **P2-2** | FTS5 全文索引 | ❌ 未实施 | ✅ **已完成** | `db.py:120-135`: `_init_fts_table` 创建 `entity_fts` fts5 虚拟表，tokenizer=`unicode61`。`db.py:232-248`: `search_entities_fts` 使用 `MATCH ?` + `ORDER BY rank`，FTS5 失败时回退到 LIKE。生产 DB `entity_fts_content` 表存在且包含数据 |
| **P2-3** | Access Log 反馈闭环 | 🟡 部分完成 | ✅ **已完成** | `tools.py:441-445`: `decision_history` 预加载 risks（不再循环 N+1）。所有工具函数都调用 `log_access`。`pipeline.py:375`: 传入 `access_count`。`scorer.py:43-49`: 支持 `recent_day_count` 参数 |
| **P2-4** | 实体/关系自动清理 | 🟡 部分完成 | 🟡 **部分完成** | `scripts/clean_and_rescore.py` 完整可用。但**未接入 cron**，需手动运行 |
| **D1** | pipeline 未传 entity_type | ❌ 未修复 | ✅ **已修复** | `pipeline.py:378-379`: `entity_type=extracted.type` 已传入 scorer。`pipeline.py:388-389`: 新增 `recent_day_count` 参数。v0.2.2 提交 `d39bd3a` 中修复 |
| **D2** | linker 门控过严 | ❌ 未修复 | ✅ **已修复** | `linker.py:136-158`: v0.2.2 新增 `_CO_OCCURRENCE_PAIRS` 规则集和白名单。`linker.py:172-176`: 当 name_mention 和 shared_tokens 都不满足时，对于白名单类型对降级为 `co_occurrence(weight=0.3)`。生产 DB 中 1,056 条 RELATES_TO 来自 co_occurrence |
| **D3** | Dreaming 状态 | ❌ 未关闭 | ✅ **已修复** | `openclaw.json` 中 `plugins.entries.memory-core.config.dreaming` 为 `{}`（空对象，等价于 disabled）。memory-core 数据库中无 `files`/`chunks` 表 |

### 评估报告与实际状态偏差分析

报告声称 RELATES_TO=0，但生产 DB 中有 2,900 条。原因：
1. 报告评估 v0.2.1，v0.2.2 在 2026-05-28 才提交（修复 linker + pipeline）
2. 或者 2026-05-26 时 DB 状态已变化但评估快照不全

重要警告：**评估报告的数据快照（1,845 实体/2,612 关系/70.86 MB/5.69% 碎片）与当前生产 DB（2,221 实体/5,779 关系/158 MB/56.26% 碎片）完全不匹配。** 这意味着要么报告样本数据不同，要么数据库自评估以来发生了剧烈变化。

---

## 新发现的优化空间

### N1 🔴 P0: DEFAULT_DIMENSION 常量不一致（严重裂痕）

**位置**: `db.py:11` vs `embedder.py:17`

```python
# db.py:11
DEFAULT_DIMENSION = 2560

# embedder.py:17
DEFAULT_DIMENSION = 1024
```

**影响**: 两者独立定义且值不同。`init_db` 用 2560 调用 `_init_vec_virtual_table`，但全部实际向量都是 1024 维。若数据库被重建，`entity_vec` 会按 2560 维创建，所有 1024 维向量的 `struct.pack` 写入会静默失败或产生畸形数据。当前因为虚拟表已存在（1024 维），CREATE IF NOT EXISTS 是 no-op，问题被掩盖。

同时，`re_embed.py:43` 在重建 `entity_vec` 时使用 `embedder.dimension`（运行时值），与 `db.py` 的静态值脱节。

**修复**: 将维度的权威定义移至 `embedder.py` 中的 `EmbeddingClient.__init__` 返回值，或从统一常量导入。移除 `db.py` 中的独立默认值。

### N2 🔴 P1: 数据库碎片 56%

**位置**: 生产数据库状态

```
Pages: 40473, Free: 22772, Frag: 56.26%
```

**分析**: v0.2.2 的 `clean_and_rescore.py` 和 pipeline 中大量 DELETE + INSERT/REPLACE 操作未伴随 VACUUM。每次 consolidation 都会删除旧的 FTS 索引记录并重建，产生大量空页。

**影响**: 158 MB 数据库中仅 ~70 MB 为有效数据，I/O 有效性减半。持续增长将加速。

**修复**: 在 consolidation 完成或 clean 脚本后执行 `VACUUM`。添加 cron 周期性 VACUUM。

### N3 🟡 P1: knowledge_search 残存 N+1 查询

**位置**: `tools.py:178-185`

```python
for entity in scored_candidates[:max_results]:
    related_rels = get_relations_for_entity(conn, eid)  # N+1 per result!
    related_ids = [
        r["to_entity"] if r["from_entity"] == eid else r["from_entity"]
        for r in related_rels[:5]    # only needed top 5
    ]
```

**分析**: `active_projects`, `knowledge_trace`, `decision_history` 的 N+1 已消除，但 `knowledge_search` 仍对每个结果执行独立关系查询。对于默认 max_results=10，产生 1+10=11 次查询。

**影响**: 高频搜索工具的延迟瓶颈。当结果为 30-100（上限）时产生 1+100 次查询。

**修复**: 收集所有结果的 entity_id，用单个 `get_relations_for_entities` 批量加载，然后在 Python 中分配。

### N4 🟡 P1: _search_entity_vectors_vec 可能因 sqlite-vec 版本不兼容失败

**位置**: `db.py:300-311`

```python
def _search_entity_vectors_vec(conn, query_vec, limit=10):
    vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)
    rows = conn.execute(
        "SELECT entity_id, distance FROM entity_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (vec_bytes, limit),
    ).fetchall()
```

**分析**: 当前使用的 sqlite-vec 版本接受 `ORDER BY distance LIMIT ?` 语法，但该库的 API 设计和文档要求 `AND k = ?`。高版本可能移除 `LIMIT` 兼容，导致 `try/except` 静默 fallback 到 Python O(N) 扫描。

**修复**: 改用 `AND k = ?` 语法适配 sqlite-vec 官方规范。添加版本兼容注释。

### N5 🟡 P1: 权重分布可能导致决策类实体区分度不足

**位置**: `scorer.py:10-29`

```python
WEIGHTS = {"freshness": 0.15, "frequency": 0.30, "diversity": 0.05,
           "richness": 0.05, "access": 0.10, "type_base": 0.35}
TYPE_BASE = {"decision": 0.80, "risk": 0.60, "project": 0.50, ...}
```

**分析**: 生产 DB 中实体 max importance 为 0.7448（`proj:openclaw`），虽然已移除天花板，但实际仍无实体超过 0.8。决策类最高 0.605（`decision:fangqishiyongduoweibiaoge_base`）。原因：
1. `diversity` 和 `richness` 总计仅 0.10 权重，但 pipeline 对所有实体固定传 `distinct_categories=1`（每实体只从一个类别提取），导致 diversity 恒为 0.25，贡献仅 0.0125
2. `tag_count` 在提取时未设置，大部分实体为 0 → richness 贡献 0
3. `frequency(day_count)` 使用对数压缩，10 天的频率分约为 `log(11)/log(8) ≈ 1.13`，加权后约 0.34。day_count=3 时为 0.23。差距不明显

**影响**: 32.9% 实体集中在 [0.4, 0.6] 区间（较报告的 95.3% 已大幅改善），但 2,221 个实体中 100% 低于 0.8，区分度仍不足。

**建议**: 评估是否增加 `diversity` 权重或改进 `distinct_categories` 计算（从实体 metadata 中提取跨类别统计）。

### N6 🟡 P2: clean_and_rescore.py 未接入 cron 自动运行

**位置**: `scripts/clean_and_rescore.py` + crontab

**分析**: 脚本功能完整（删除噪声实体、弱关系、重评分），但 crontab 中只有 consolidation（`0 4 * * *`），无定期清理入口。碎片和噪声实体将随着时间持续累积。

**修复**: 在 consolidation cron 之后添加 `scripts/clean_and_rescore.py` 的调用，或整合到 consolidation pipeline 中。

### N7 🟢 P2: embedder.py 中 `_embed_chunk` 重试逻辑返回空列表

**位置**: `embedder.py:75-94`

```python
for attempt in range(3):
    try:
        resp = self._client.post(...)
        if resp.status_code == 429 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        ...
    except httpx.HTTPStatusError as e:
        logger.warning(...)
        return []  # returns empty list instead of retrying on 5xx
    except httpx.RequestError as e:
        logger.warning(...)
        return []  # returns empty list instead of retrying on connection error
```

**分析**: 在 `HTTPStatusError` 和 `RequestError` 分支中直接 `return []` 而非 `continue` 重试。只有 429 才有重试逻辑。HTTP 503/502 和 DNS 超时不会重试。

**影响**: 生产环境 embedding API 短暂不可用时，一批实体的向量全部丢失，无重试。

**修复**: 将非 429 错误也纳入重试逻辑（最多 2 次重试），返回空列表仅在所有重试耗尽后。

### N8 🟢 P2: `_collect_related_ids` 未使用收集到的 `all_relations`

**位置**: `tools.py:239-260`

```python
def _collect_related_ids(conn, entity_id: str, max_depth: int) -> set[str]:
    visited: set[str] = {entity_id}
    current_level: set[str] = {entity_id}
    all_relations: list[...] = []

    for depth in range(max_depth):
        ...
        for rel in level_rels:
            ...
            all_relations.append(...)
        current_level = next_level

    return visited  # all_relations is never used!
```

**分析**: `all_relations` 变量被填充但从未被函数外部使用，函数只返回 `visited`（ID 集合）。`knowledge_trace` 在获得 `visited` 后还要重新查 entity 的关系类型和权重，做了重复工作。

**修复**: 移除死代码 `all_relations`，或扩展函数返回类型包含关系元数据以减少重复 I/O。

### N9 🟢 P3: 没有 tool 级别健康检查和告警

**位置**: 全系统

**分析**: `knowledge_stats` 提供指标但不提供告警。`knowledge_consolidate` 返回错误但不发出外部通知。嵌入 API 降级时没有警告。

---

## 核心模块详细审计发现

### 1. db.py — 数据库层

| 问题 | 行号 | 严重性 | 说明 |
|------|------|--------|------|
| `DEFAULT_DIMENSION` 与 embedder 不一致 | 11 | 🔴 P0 | 2560 vs 1024，见 N1 |
| `_search_entity_vectors_vec` 语法非标准 | 300-311 | 🟡 P1 | 使用 `LIMIT ?` 而非 `AND k=?`，见 N4 |
| sqlite-vec 回退路径未通知用户 | 99-107 | 🟢 P3 | Python 扫描时只写 debug 日志，工具层无反馈 |
| FTS5 索引未在 INSERT 失败时重建 | 193-199 | 🟢 P3 | `try/except pass` 静默吞异常 |
| `access_log` 无轮转策略 | 37-39 | 🟢 P3 | 持续写入永不清除 |
| _search_entity_vectors_python 列引用含歧义 | 329-331 | 🟢 P3 | `r["embedding"]` vs `r[1]` 混用，当前使用索引但早前代码用列名 |

**当前实现状态**:
- FTS5: ✅ 已实现 (`_init_fts_table`, `entity_fts` 表)
- sqlite-vec: ✅ 已集成（`_init_vec_virtual_table`, `migrate`, `_can_use_vec`）
- 向量搜索策略: 优先走 `_search_entity_vectors_vec` → 失败 fallback `_search_entity_vectors_python`
- 生产实际: 查询可正常工作，但 DEFAULT_DIMENSION=2560 与 DB 实际 1024 不匹配

### 2. pipeline.py — 合并编排器

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| `distinct_categories` 恒为 1 | 376 | 🟡 P2 | 固定传 1，从不计算实体实际跨类别数 |
| `_find_similar_entity` 30 天窗口限制 | 240 | 🟢 P3 | 超过 30 天的实体不计入相似合并，可能漏掉跨月碎片 |
| `recent_day_count` 计算仅对新实体 | 385-399 | 🟢 P3 | 仅在已有 `db_entity` 时计算，新实体直接使用 `new_day_count` |

**D1 修复验证**: ✅ 已修复。`pipeline.py:378-379` 传入 `entity_type=extracted.type, recent_day_count=recent_day_count`。

**重复 task slug**: `_DURATION_RE` 过滤和 `_compact_name` 缩短名称降低了概率，但 pipeline 无 SQL-level 唯一约束，重复仍可能发生。

### 3. linker.py — 实体关系链接器

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| 同 section 跨类型链接上限 | 136-158 | 🟢 P3 | `_CO_OCCURRENCE_PAIRS` 仅包含 11 组类型对。tech-fact, task-tech 等未覆盖 |
| MAX_PER_FILE_RELATIONS=500 | 63 | 🟢 P3 | 大文件可能截断，无日志提醒 |
| `_has_name_mention` 精确匹配 | 86-88 | 🟢 P3 | 仅检查 `e2.name in e1.summary`（精确子串），不处理同义词或缩写 |

**D2 修复验证**: ✅ 已修复。`linker.py:172-176` 添加了 `co_occurrence` 降级权重=0.3。生产 DB 中 1,056 条 RELATES_TO 来自 co_occurrence。

**当前生产关系分布**:
- RELATES_TO: 2,900 (50.2%)
- CONTINUES: 2,138 (37.0%)
- DEPENDS_ON: 724 (12.5%)
- CONTRADICTS: 17 (0.3%)

### 4. scorer.py — 评分器

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| diversity+richness 合计仅 0.10 | 12-13 | 🟡 P1 | 见 N5 |
| 生产 DB 无实体超过 0.8 | 运行数据 | 🟡 P2 | 天花板效应已移除但实际分布仍紧凑 |
| `score_entity` 未传递 `recent_day_count` | 66-69 | 🟢 P3 | 便利函数中未使用新参数，向后兼容但精度低 |

**D1 修复验证**: ✅ `entity_type` 参数已传入。`scorer.py:78` 使用了 `entity_type`。

### 5. tools.py — MCP 工具层

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| knowledge_search N+1 | 178-185 | 🟡 P1 | 见 N3 |
| _collect_related_ids 死代码 | 239-260 | 🟢 P2 | 见 N8 |
| active_projects 对所有项目 log_access | 358-360 | 🟢 P3 | 用户查询 1 个项目却记录所有项目访问 |
| max_results=10 时 min_score 可能过早过滤 | 188-196 | 🟢 P3 | 候选 3x=30 但 min_score 在 filter_by_score 中应用，需确认过不过滤 |

**N+1 消除验证**: 
- `active_projects` ✅ 预加载 tasks
- `knowledge_trace` ✅ BFS 批量查询
- `decision_history` ✅ 预加载关系 + 批量查实体
- `knowledge_search` ❌ 残存 N+1

### 6. server.py — MCP 服务器入口

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| 每个 tool 开新连接 | 49-51 | 🟡 P2 | `_get_conn()` 在每次 tool 调用时初始化新的 DB 连接 + schema + FTS rebuild |
| 无连接池 | 全局 | 🟡 P2 | 高频调用时连接创建开销叠加 |
| 资源 `knowledge://days/{date}` 未使用 `list_entities_by_type` 结果 | 316-316 | 🟢 P3 | import 了但未用于丰富返回数据 |

**Embedder 参数传递**: ✅ 正确。通过 `get_embedder()` 延迟工厂传入 `knowledge_search` 和 `knowledge_consolidate`。

### 7. extractor.py — 实体提取器

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| 项目名检测只覆盖特定模式 | 207-234 | 🟢 P3 | `{name}项目` / 单个 CamelCase / "X Project"，不覆盖拉丁缩写 |
| task 的 entity_id 包含中文 `完成_` 或 `计划_` | 356, 371 | 🟢 P3 | `slugify` 会将中文转拼音，但 `完成_` 前缀被拼音化导致难读的实体 ID |

**WI1 噪声过滤**: ✅ 连续多轮迭代后已相当全面：`_TIMESTAMP_LOG_RE`、`_BRACKET_TS_RE`、`_OPS_LOG_KEYWORDS_RE`、`_TECH_COMMON_WORDS`、`_STRUCTURAL_TECH_RE`、`_GARBAGE_PATTERNS`、`_GARBAGE_NAMES`、`_DURATION_RE`。

**同义实体合并**: v0.2.2 新增 embedding 第二遍扫描（`pipeline.py:252-265`），对 SequenceMatcher 0.70-0.85 的边界候选用向量相似度做最终判定。

### 8. embedder.py — Embedding 服务客户端

| 问题 | 位置 | 严重性 | 说明 |
|------|------|--------|------|
| 非 HTTP 429 错误不重试 | 93-96 | 🟢 P2 | 见 N7 |
| `_is_local` 检测过于简单 | 47 | 🟢 P3 | `127.0.0.1` 和 `localhost` 以外的内网地址被判定为 remote |
| API Key 为空时不检查 local | 137-142 | 🟢 P3 | local embedder 不需要 API Key，但条件分支写为 `not is_local and not api_key`（正确） |

### 9. scripts/ — 工具脚本

| 脚本 | 功能 | 状态 | 问题 |
|------|------|------|------|
| `clean_and_rescore.py` | 清理噪声实体 + 弱关系 + 重评分 | ✅ 完整可用 | 未接入 cron。删除弱关系时硬编码了 8 个 DMA 标准分类名的 evidence 模式，可能遗漏新出现的 evidence 格式 |
| `re_embed.py` | 读取所有实体，用当前 embedder 重新生成向量 | ✅ 可用 | 重建 `entity_vec` 时使用 `embedder.dimension`，与 `db.py` 的 DEFAULT_DIMENSION 脱节。若 `embedder.dimension=1024` 但未来 `db.py` 用 2560 重建虚拟表，会维度不匹配 |

### 10. tests/ — 测试覆盖

| 模块 | 测试数量 | 状态 | 覆盖度评估 |
|------|---------|------|-----------|
| test_db.py | 10 | ✅ 全通过 | 基础 CRUD + FTS + manifest + access_log。🟡 缺少向量搜索（`search_entity_vectors`）测试 |
| test_scorer.py | 18 | ✅ 全通过 | 6 因子独立测试 + 组合 + entity_type 参数 + 天花板突破。✅ 高质量 |
| test_extractor.py | 13 | ✅ 全通过 | 各类型提取 + 噪声过滤白盒测试。✅ 覆盖了 WI1 过滤逻辑 |
| test_linker.py | 11 | ✅ 全通过 | 共现/名称/共享令牌/跨日/矛盾检测。✅ 高覆盖 |
| test_tools.py | 35 | ✅ 全通过 | 6 个工具全覆盖 + 边界条件（空 DB/不存在的 topic）。✅ |
| test_pipeline.py | 11 | ✅ 全通过 | 文件发现/hash 校验/多日运行/重要性评分。🟡 缺少对 embedder 传递的集成测试 |
| test_server.py | 9 | ✅ 全通过 | MCP 工具 JSON 响应。🟡 缺少资源端点测试 |
| test_embedder.py | 8 | ✅ 全通过 | 批处理/速率限制/错误处理/API Key 校验 |
| test_integration.py (仅 1) | 2 | ✅ 全通过 | 简单端到端 |

**关键测试缺口**:
- `search_entity_vectors` 无单元测试（依赖 sqlite-vec）
- 无 FTS5 中文分词准确度测试（`unicode61` 对 CJK 分词效果有限）
- 无 `clean_and_rescore.py` 测试
- 无 `re_embed.py` 测试

---

## 优化优先级建议

### P0 — 立即修复（1 天）

| # | 任务 | 行号 | 工时 | 影响 |
|---|---|---|---|---|
| **N1** | 统一 `DEFAULT_DIMENSION` 常量 | `db.py:11`, `embedder.py:17` | 0.1d | 防止重建 DB 时全量失败。推荐方案：`embedder.py` 为权威来源，`db.py` 从 embedder 导入 |
| **N6** | 将 `clean_and_rescore.py` 接入 cron | crontab | 0.1d | 防止噪声实体和碎片持续累积 |
| **N2** | 添加 consolidation 后 VACUUM | `pipeline.py` + crontab | 0.2d | 释放 56% 空页，DB 从 158 MB 压缩到 ~70 MB |
| **N3** | 消除 `knowledge_search` N+1 | `tools.py:178-185` | 0.3d | 搜索延迟从 N+1 次查询降至 3 次 |
| **N8** | 移除 `_collect_related_ids` 死代码 | `tools.py:239-260` | 0.1d | 代码清理 + BFS 效率微提升 |

### P1 — 本周（1.5 天）

| # | 任务 | 工时 | 影响 |
|---|---|---|---|
| **N4** | 修复 sqlite-vec 查询语法为标准 `AND k=?` | 0.2d | 确保未来 sqlite-vec 版本兼容 |
| **N5** | 评估并调整权重（增大 diversity/richness 至 0.10+0.10，减小 frequency 至 0.25） | 0.3d | 提升实体区分度，避免 0.4-0.6 聚集 |
| **N7** | 修复 embedder 非 429 重试逻辑 | 0.2d | 短暂 API 中断时自动恢复而非丢失批次 |
| P2-1 | 添加进程内 LRU 缓存（`functools.lru_cache` + TTL） | 0.5d | 高频查询命中率 >60%，延迟 <20ms |
| **N9** | 添加 tool 级别异常告警 | 0.3d | API 降级/embedding 失败时通知 |

### P2 — 中长期（3 天）

| # | 任务 | 工时 | 影响 |
|---|---|---|---|
| **测试补全** | 向量搜索/中文 FTS5/clean_and_rescore 覆盖 | 1.0d | 提升回归测试信心 |
| **池化 DB 连接** | server.py 连接池而非每次创建 | 0.5d | 减少 MCP 调用延迟 |
| **access_log 轮转** | 添加归档/清理策略 | 0.3d | 防止日志表无限增长 |
| **决策 outcome 字段** | decision 实体增加 `status` 字段追踪验证状态 | 0.5d | 决策闭环追踪 |
| **knowledge_search ↔ knowledge_trace 融合** | BFS 结果优先排序 | 0.5d | Agent 体验提升 |

---

## 生产数据库当前状态（2026-05-28）

| 指标 | 当前值 | 2026-05-26 报告值 | 变化 |
|------|--------|--------------------|------|
| 实体总数 | 2,221 | 1,845 | ↑376 |
| 关系总数 | 5,779 | 2,612 | ↑3,167 |
| 数据库大小 | 158.10 MB | 70.86 MB | ↑87.24 MB |
| 碎片率 | 56.26% | 5.69% | ↑50.57% |
| 索引天数 | 14 (2026-05-13 至 2026-05-28) | 14 (至 2026-05-26) | +2 天 |
| 访问日志 | 987 | 136 | ↑851 |
| RELATES_TO | 2,900 | 0 | ↑2,900 (D2 修复后) |
| 实体 max importance | 0.7448 (proj:openclaw) | 0.8591 (OpenClaw) | ↓0.1143 (权重调整) |
| 实体 [0.4, 0.6] 占比 | 30.9% | 95.3% | ↓64.4% (散布改善) |
| 实体 ≥ 0.8 | 0 | 1 | 差异化不足 |
| sqlite-vec 可用 | ✅ 是 (但未真正使用) | — | 已安装模块 |
| FTS5 | ✅ 可用 | ❌ 不存在 | 已实现 |
| 关联日志反馈 | ✅ active_projects/decision_history 已 Batch | — | — |

---

## 结论

v0.2.2 是一次高质量的功能补足提交，修复了评估报告识别的全部 3 个关键缺陷（D1/D2/D3）并实现了 P0-4(sqlite-vec)、P2-2(FTS5)、P1-3(部分N+1)、P2-3(access log闭环)。当前系统功能完备性良好。

**核心差距转为运维和隐性风险**:
1. DEFAULT_DIMENSION 不一致（N1）是当前最紧迫的架构裂痕
2. 数据库 56% 碎片需要立即治理（N2）
3. knowledge_search 残存 N+1（N3）影响高频查询体验
4. 权重分布仍需微调以提升实体区分度（N5）

**评估报告时效性问题**: 2026-05-26 报告与 v0.2.2 代码/生产数据严重脱节。12 项判断中 6 项的 "实际状态" 与报告结论相反。建议团队建立"每次代码合入后自动重跑评估"的 CI 流程，避免基于过期快照做决策。
