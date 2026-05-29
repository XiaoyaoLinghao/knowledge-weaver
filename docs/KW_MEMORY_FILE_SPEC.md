# Knowledge Weaver Memory File Specification (v1.0)

> **本文档是规范性参考（Normative Reference）**。任何为 Knowledge Weaver (KW) 提供输入数据的 agent / skill / 工具必须严格遵守本文规定。
> KW 端解析器以本规范为唯一权威；不在本规范内的格式 KW 不保证识别。

**版本**：1.0  
**生效**：2026-05-28  
**适用范围**：任何写入 KW `memory_dir` 的 markdown 文件

---

## 1. 文件命名与位置

### 1.1 文件名格式

```
<YYYY-MM-DD>.md
```

- **YYYY-MM-DD**：ISO 8601 日期，零填充（如 `2026-05-28`，而非 `2026-5-28`）
- **后缀**：`.md`（小写）
- **特殊前缀**：仅 `sample_` 允许（用于测试 fixtures），如 `sample_2026-05-28.md`

**对应正则**（KW 端使用）：
```python
^(?:sample_)?(\d{4}-\d{2}-\d{2})\.md$
```

### 1.2 不符合规范的反例

```
2026-5-28.md           ❌ 月日未零填充
26-05-28.md            ❌ 年份非 4 位
memory-2026-05-28.md   ❌ 自定义前缀（非 sample_）
2026_05_28.md          ❌ 分隔符必须为 -
2026-05-28.markdown    ❌ 扩展名必须为 .md
.2026-05-28.md         ❌ 隐藏文件，KW 跳过
```

### 1.3 目录约定

- 一个 agent 的所有 memory 文件放在一个目录下
- 文件名在该目录内唯一（一天一个文件）
- KW 端通过 `KNOWLEDGE_WEAVER_MEMORY_DIRS` 配置可读取多个目录（参见 KW-F1）

---

## 2. 文件编码

- **UTF-8**，无 BOM
- **换行符**：LF（`\n`）；CRLF 会被 KW 容忍但不推荐
- **末尾换行**：建议保留

---

## 3. 文件结构

文件由三部分组成（按出现顺序）：

```
[Frontmatter] (可选但推荐)
[Body Header] (可选)
[Body Sections] (必须)
```

### 3.1 YAML Frontmatter（可选但推荐）

```yaml
---
title: "2026-05-28 会话记忆"
date: "2026-05-28"
---
```

**字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 否 | 人类可读的标题；不提供时 KW 的 `ParsedFile.title` 为空串 |
| `date` | string | 否 | YYYY-MM-DD 格式，必须等于文件名中的日期；不提供时 KW 从文件名提取 |

**约束**：
- 起止 `---` 各占一行
- 字段顺序无关
- 值可加双引号或单引号，KW 解析时会去除外层引号
- 不要在 frontmatter 中放其它字段——KW 会忽略

### 3.2 Body Header（可选，纯装饰）

```markdown
# 2026-05-28
```

- 可选的 H1 标题行；KW 不读取此行的内容
- 仅用于人类浏览友好

### 3.3 Body Sections（必须）

KW 支持两种 body 结构。**两种格式可以在同一个文件内混用**（但不推荐）。

#### 格式 A：直接分类

```markdown
## 核心要点
- bullet 1
- bullet 2

## 决策与结论
- bullet 3
```

- 每个 `##` 标题直接是分类名（必须严格匹配 §4 的标题列表）
- 该分类下的所有 bullet 属于该分类

#### 格式 B：时间槽嵌分类（推荐）

```markdown
## 10:00
**核心要点**
- bullet 1

**决策与结论**
- bullet 2

## 14:30
**核心要点**
- bullet 3
```

- `##` 标题是时间槽（`HH:MM` 或 `HH:MM:SS`），KW 识别为时间标记
- 时间槽内用 `**bold**` 标记分类（必须严格匹配 §4 的标题列表）
- 每个分类标记后的 bullet 属于该分类，直到下一个分类标记或时间槽

**KW 解析规则**：

| 行类型 | 模式 | KW 行为 |
|---|---|---|
| `## <category>` | H2 + 已知分类名 | 切换 current_category |
| `## HH:MM` | H2 + 时间格式 | 切换 current_category 回默认 `fact` |
| `## <other>` | H2 + 其它 | current_category 切换为 `fact`，把该 H2 作为自定义 section title |
| `**<category>**` | 粗体行 + 已知分类名 | 切换 current_category |
| `**<other>**` | 粗体行 + 其它 | **静默忽略**（current_category 不变）⚠️ |
| `- <text>` | bullet | 加入到当前 current_category |
| 空行 / 其它 | — | 忽略 |

⚠️ **静默忽略未识别分类是已知行为**。这意味着写错分类名会导致内容被错误归类。**必须严格匹配 §4**。

---

## 4. 分类标题（规范性）

下列 8 个核心分类 + 1 个扩展分类是 KW 唯一识别的分类标题：

### 4.1 核心 8 分类（必须支持）

| 标题字符串 | KW 实体类型 | 语义 |
|---|---|---|
| `核心要点` | `fact` | 重要事实、状态、当前情况 |
| `决策与结论` | `decision` | 明确的决定、选择、结论 |
| `已完成事项` | `task` (status=completed) | 已完成的工作、实现、修复 |
| `待办与计划` | `task` (status=todo) | 后续要做的事、计划 |
| `用户偏好与习惯` | `preference` | 用户明确表达的偏好、工作习惯 |
| `技术/项目要点` | `tech` | 项目进展、技术方案、工具选择 |
| `风险与注意事项` | `risk` | 潜在问题、需留意的风险 |
| `创意与想法` | `idea` | 新想法、灵感、可能性 |

### 4.2 扩展分类（可选支持）

| 标题字符串 | KW 实体类型 | 用途 |
|---|---|---|
| `关键讨论` | `fact` | 重要讨论、疑问、问题点（保守归入 fact） |

> **背景**：`关键讨论` 是历史遗留分类（DMA local-extractor 早期使用）。新接入 agent 优先使用 4.1 中的 8 类；如果有"讨论"语义的内容，建议归入 `核心要点` 或 `创意与想法`。

### 4.3 字符串匹配要求

- **逐字符匹配**：不允许大小写差异（中文无此问题）、不允许多余/缺失字符、不允许标点替换
- **示例**：
  ```
  ✅ "核心要点"
  ❌ "重要事实"        ← 不在表中
  ❌ "核心要点 "       ← 末尾有空格
  ❌ "核心要点："      ← 末尾有冒号
  ❌ "[核心要点]"      ← 加了方括号
  ```
- KW 端用 Python `dict[str, str]` 查找；任何偏差都会落入"静默忽略"路径

---

## 5. Bullet 格式

### 5.1 基本格式

```
- <content>
```

- 必须以 `- ` 开头（连字符 + 空格），不能用 `*` 或 `+`
- `<content>` 为自由文本

### 5.2 时间戳前缀（可选）

```
- 09:30 - <content>
- 09:30:45 - <content>
```

- 时间戳放在内容前，用 ` - ` 分隔
- 支持 `HH:MM` 和 `HH:MM:SS`
- KW 端会提取 `time` 字段，剩余文本作为 `text`

### 5.3 续行（多行 bullet）

```
- 这是一个较长的事项
  续行内容必须缩进
  可以多行
- 下一个 bullet
```

- 续行必须以空格或 tab 开头
- 续行内容会被合并到上一个 bullet 的 `text`，用单个空格连接

### 5.4 内容建议

- **长度**：建议 ≤ 280 字符；过长会被 KW 截断到 200 字符存入 `entity.summary`
- **避免**：
  - 表格（`|...|...|`）
  - 嵌套列表（`  - sub`）
  - Markdown 代码块（` ``` `）
- **允许**：
  - 反引号代码片段（`` `code` ``）
  - 加粗、斜体等行内格式
  - 中英文混合

### 5.5 反例

```
* bullet               ❌ 必须用 -
+ bullet               ❌ 必须用 -
-bullet                ❌ 缺少空格
- - 嵌套               ❌ 嵌套 bullet 会被忽略
```

---

## 6. 失败 / 占位符约定

当 agent 因外部失败（如云端 API 失败、配额耗尽）需要在 memory 文件中留痕时，**必须使用以下错误前缀**，否则会被 KW 当作正常知识入库：

```
- *<AGENT-ID>-ERR: <reason>*
```

例如：
```
- *DMA-ERR: cloud summary failed (see log)*
- *HERMES-ERR: api timeout after 30s*
```

KW 端的 `_GARBAGE_PATTERNS` 必须包含对应规则。已知前缀：

| 前缀 | KW 已识别 |
|---|---|
| `DMA-ERR:` | ✅ (KW ≥ TBD) |

新 agent 接入时需 PR 到 KW 仓库增加对应前缀。

---

## 7. 一份合规示例

```markdown
---
title: "2026-05-28 会话记忆"
date: "2026-05-28"
---

# 2026-05-28

## 10:00

**核心要点**
- 09:30 - 用户启动 ExampleProject 项目，确认采用模块化架构

**决策与结论**
- 09:45 - 决定使用 Python FastAPI 作为后端框架

**已完成事项**
- 10:00 - 完成了登录模块的基础框架

**待办与计划**
- 10:15 - 后续增加 OAuth2 集成

**用户偏好与习惯**
- 11:00 - 用户偏好简洁的代码风格，避免过度抽象

**技术/项目要点**
- 11:30 - ExampleProject 使用 PostgreSQL 作为主数据库

**风险与注意事项**
- 12:00 - 注意数据库连接池在高并发下可能耗尽

**创意与想法**
- 13:00 - 可以引入读写分离来缓解读压力

## 14:30

**已完成事项**
- 14:00 - 修复了 ExampleProject 项目的 SQL 注入风险

- *DMA-ERR: cloud summary failed (see log)*
```

---

## 8. 兼容性与版本

- 本规范遵循语义化版本（SemVer）
- v1.x 内任何新增分类必须不破坏 v1.0 的 8 个核心分类
- 破坏性变更（删除分类、改名）需升级到 v2.0
- KW 端 README 必须声明所支持的 SPEC 版本

---

## 9. 实施检查清单

新 agent 接入 KW 前请逐项确认：

- [ ] 文件名严格 `YYYY-MM-DD.md`
- [ ] UTF-8 编码无 BOM
- [ ] frontmatter `date` 与文件名一致（若使用 frontmatter）
- [ ] 所有 `**bold**` 分类标题逐字符匹配 §4
- [ ] bullet 以 `- ` 开头
- [ ] 失败占位符使用 `*<AGENT>-ERR: ...*` 格式，并已在 KW `_GARBAGE_PATTERNS` 注册
- [ ] 用 KW 的 `parse_dma_content()` 跑过 dry-run 验证（见 §10）

---

## 10. 验证工具

KW 仓库提供 `tests/test_parser.py` 作为参考。手动验证脚本：

```bash
.venv/bin/python -c "
from knowledge_weaver.parser import parse_dma_content
import pathlib, sys

filepath = sys.argv[1] if len(sys.argv) > 1 else None
if not filepath:
    print('Usage: spec-check.py <memory.md>')
    sys.exit(1)

content = pathlib.Path(filepath).read_text(encoding='utf-8')
p = parse_dma_content(content)
print(f'title: {p.title!r}')
print(f'date:  {p.date!r}')
print(f'sections: {len(p.sections)}')

KNOWN = {
    '核心要点': 'fact', '决策与结论': 'decision',
    '已完成事项': 'task', '待办与计划': 'task',
    '用户偏好与习惯': 'preference', '技术/项目要点': 'tech',
    '风险与注意事项': 'risk', '创意与想法': 'idea',
    '关键讨论': 'fact',
}

for s in p.sections:
    items = len(s.items)
    if s.title not in KNOWN:
        print(f'  ⚠️  UNKNOWN [{s.category}] {s.title!r} ({items} items)')
    else:
        expected = KNOWN[s.title]
        ok = '✅' if s.category == expected else '❌'
        print(f'  {ok} [{s.category}] {s.title} ({items} items)')
" path/to/your/memory.md
```

任何 `⚠️ UNKNOWN` 或 `❌` 都说明文件不符合本规范。

---

## 11. 变更历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-05-28 | 初版规范 |
