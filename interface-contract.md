# 经营分析智能体接口契约

> 版本：v0.1；A（队长）维护，B、C 按本契约实现。

这是 Chinook 结构化经营数据与自建经营知识文档之间的统一接口约定。网页不直接访问数据库或文档索引，只调用统一提问接口；Agent 再决定调用 SQL、RAG、文档处理工具，或先向用户澄清。

```text
网页端 -> POST /api/v1/ask -> Agent 路由 -> SQL / RAG / 文档工具 -> 统一回答对象
```

## 一、三人责任边界

| 模块 | 负责人 | 交付内容 |
|---|---|---|
| 产品与口径 | A（队长） | 用户问题、指标口径、实体别名、文档元数据、评测标准、接口变更 |
| 网页与 Agent | B | 页面、对外 API、路由、主动澄清、结果展示、会话状态、RAG 接入 |
| 数据库与 NL2SQL | C | Chinook Schema、候选表字段、SQL 生成、只读执行、查询校验 |

接口先固定字段，内部实现可以变化。B 不依赖 C 的文件结构，C 不依赖 B 的页面代码，A 负责解释字段含义和验收标准。

## 二、对外提问接口

### 1. 请求

接口：`POST /api/v1/ask`

```json
{
  "question": "2025年 Rock 音乐销售额是多少？",
  "session_id": "demo-session-001",
  "user_role": "operator",
  "options": {
    "show_trace": true,
    "max_rows": 50,
    "top_k": 5
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question` | string | 是 | 用户当前问题，不能为空 |
| `session_id` | string | 否 | 多轮会话标识；没有时服务端生成 |
| `user_role` | string | 否 | `operator` 或 `manager`，默认 `operator` |
| `options.show_trace` | boolean | 否 | 是否返回工具执行记录，默认 `true` |
| `options.max_rows` | integer | 否 | 表格最多返回 50 行，上限 200 |
| `options.top_k` | integer | 否 | RAG 候选片段数，默认 5，上限 10 |

第一版前端不传 SQL、文件路径或任意过滤表达式。查询计划由 Agent 根据字段字典、指标配置和安全规则生成。

### 2. 返回

```json
{
  "request_id": "req-20260929-000001",
  "session_id": "demo-session-001",
  "status": "answered",
  "answer": "2025年 Rock 音乐销售额为……。",
  "normalized_question": "查询2025-01-01至2026-01-01期间音乐范围内Rock分类的销售额。",
  "route": "sql",
  "intent": {"name": "metric_query", "confidence": 0.96},
  "entities": [
    {"type": "genre", "text": "Rock", "normalized_value": "Rock", "entity_id": 1, "confidence": 0.99, "source": "entity_aliases"}
  ],
  "time_range": {"start": "2025-01-01", "end": "2026-01-01", "end_inclusive": false, "label": "2025年"},
  "data_scope": "music",
  "sql_results": [],
  "documents": [],
  "calculations": [],
  "metric_definitions": [],
  "trace": [],
  "limitations": [],
  "clarification": null,
  "error": null
}
```

业务状态：

| `status` | 含义 | 页面处理 |
|---|---|---|
| `answered` | 数据和证据足够 | 展示答案、结果和来源 |
| `clarification_required` | 缺少时间、指标、范围或对比周期 | 展示 `clarification.question`，等待补充 |
| `insufficient_evidence` | 当前数据没有支撑答案的证据 | 明确展示数据缺口，不编造 |
| `error` | 工具或接口异常 | 展示错误和是否可重试 |

字段约定：

- `route`：`sql`、`rag`、`cross_source`、`clarification`、`unsupported`。
- `data_scope`：`music`、`video`、`all`。
- `time_range.end` 默认不包含，使用左闭右开区间。
- `trace` 只记录可核验的工具和执行状态，不展示模型隐性思维链。

## 三、Agent 内部查询计划

Agent 先把用户原话转换为结构化计划，再交给 SQL 或 RAG 工具。

```json
{
  "intent": "metric_query",
  "route": "sql",
  "entities": [{"type": "genre", "value": "Rock", "entity_id": 1, "confidence": 0.99}],
  "metrics": [{"metric_id": "sales_amount", "aggregation": "sum"}],
  "time_range": {"start": "2025-01-01", "end": "2026-01-01", "end_inclusive": false},
  "data_scope": "music",
  "filters": [],
  "group_by": [],
  "sort": [],
  "missing_slots": [],
  "needs_document": false
}
```

固定规则：

- 销售额：`SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)`。
- 音乐范围必须筛选音频媒体类型；不能把含视频订单的整张 `Invoice.Total` 当作音乐销售额。
- 缺少必要时间或比较周期时，`missing_slots` 不为空，路由必须为 `clarification`。
- 无法映射到字段字典的要素不能静默丢弃，应进入澄清或 `limitations`。

## 四、SQL 工具接口：C 提供

第一版可以先实现为 Python 函数，后续再封装为内部 HTTP 接口；B 和 Agent 只依赖下面的输入输出。

输入：

```json
{
  "query_id": "sql-001",
  "sql": "SELECT COUNT(*) AS customer_count FROM Customer",
  "params": {},
  "purpose": "统计客户数量",
  "max_rows": 50,
  "timeout_ms": 5000
}
```

输出：

```json
{
  "query_id": "sql-001",
  "status": "success",
  "sql": "SELECT COUNT(*) AS customer_count FROM Customer",
  "columns": ["customer_count"],
  "rows": [{"customer_count": 59}],
  "row_count": 1,
  "truncated": false,
  "execution_ms": 8,
  "error": null
}
```

`status`：`success`、`rejected`、`failed`。

C 必须保证：只允许单条 `SELECT` 或 `WITH ... SELECT`；拒绝 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`ATTACH`；使用只读连接；超出行数上限要标记 `truncated=true`；错误返回结构化 `error`，不把堆栈返回给用户。

## 五、RAG 检索接口：B 提供

输入：

```json
{
  "retrieval_id": "rag-001",
  "query": "Rock 2025年第三季度经营目标和达成标准",
  "filters": {"doc_ids": ["D06"], "entity_ids": ["Genre:1"], "period_start": "2025-07-01", "period_end": "2025-10-01"},
  "top_k": 5
}
```

输出：

```json
{
  "retrieval_id": "rag-001",
  "status": "success",
  "chunks": [
    {
      "chunk_id": "D06-p1-c2",
      "doc_id": "D06",
      "title": "2025年第三季度经营目标表",
      "doc_type": "pdf",
      "page": 1,
      "section": "音乐分类目标",
      "text": "Rock：第三季度销售目标为……",
      "score": 0.91,
      "entity_refs": ["Genre:1"],
      "period_start": "2025-07-01",
      "period_end": "2025-10-01",
      "source_uri": "data/knowledge/D06_2025Q3经营目标表.pdf"
    }
  ],
  "error": null
}
```

文档片段必须保留文档 ID、页码或段落位置；图片或扫描件在可行时保留 `bbox`，供页面高亮来源区域。

## 六、文档处理接口：B 负责

第一版可先实现质量检测和 OCR，公式计算只支持已定义的经营公式。

输入：

```json
{"document_id": "D06", "operations": ["quality_check", "ocr", "extract_formula"]}
```

输出：

```json
{
  "document_id": "D06",
  "status": "success",
  "quality": {"score": 0.86, "issues": [{"type": "low_ocr_confidence", "page": 1, "confidence": 0.72, "message": "第1页表格部分文字识别置信度较低"}]},
  "ocr_text": [],
  "formulas": [],
  "error": null
}
```

公式必须来自文档证据，参数必须能回指数据库结果或文档片段，并记录单位、输入来源和计算结果；不能直接执行模型任意生成的表达式。

## 七、来源、计算和执行记录

SQL 结果示例：

```json
{
  "query_id": "sql-001",
  "purpose": "计算2025年Rock音乐销售额",
  "sql": "SELECT ...",
  "columns": ["sales_amount"],
  "rows": [{"sales_amount": 123.45}],
  "row_count": 1,
  "execution_ms": 12,
  "source": {"type": "database", "name": "Chinook.db", "tables": ["Invoice", "InvoiceLine", "Track", "Genre"]}
}
```

文档证据示例：

```json
{
  "doc_id": "D06",
  "title": "2025年第三季度经营目标表",
  "page": 1,
  "section": "音乐分类目标",
  "quote": "Rock：第三季度销售目标为……",
  "source_uri": "data/knowledge/D06_2025Q3经营目标表.pdf"
}
```

工具调用记录：

```json
{"step": 2, "tool": "sql.query", "status": "success", "summary": "查询2025年Rock音乐销售额", "source_refs": ["sql-001"], "duration_ms": 12}
```

## 八、三个核心问题的路由

1. “2025年 Rock 音乐销售额是多少？”：`route=sql`，调用 C 的 SQL 工具，返回销售额、SQL、指标口径和表来源。
2. “Rock 达到第三季度经营目标了吗？”：`route=cross_source`，C 查实际销售额，B 检索 D06，系统计算实际值、目标值、差额和达成率，并返回 SQL、D06 页码、公式和结论。
3. “销售增长率是多少？”：`route=clarification`，返回“请指定比较周期，并说明统计全部音乐还是某个品类”，用户补充后沿用 `session_id` 继续执行。

## 九、错误格式和协作规则

统一错误对象：

```json
{"code": "SQL_READ_ONLY_VIOLATION", "message": "查询被拒绝：只允许执行只读查询。", "retryable": false, "details": null}
```

建议错误码：`INVALID_REQUEST`、`MISSING_REQUIRED_SLOT`、`SCHEMA_LINKING_FAILED`、`SQL_READ_ONLY_VIOLATION`、`SQL_EXECUTION_FAILED`、`RAG_NO_EVIDENCE`、`DOCUMENT_PROCESSING_FAILED`、`UNSUPPORTED_METRIC`、`INTERNAL_ERROR`。

- A 负责维护本文件；新增或修改字段先更新文档，再写代码。
- B 和 C 不依赖对方的内部文件，通过契约传递数据。
- 新增字段优先保持兼容；删除字段、修改含义或状态值时升级为 `/api/v2/ask`。
- 每次合并前用三个核心问题跑一遍；修改 SQL 或口径后由 A 复核标准答案。
- 示例中的 `123.45` 只是格式示例，不能写入评测答案。

## 十、第一轮落地顺序

1. A 确认本文的请求、返回、查询计划和业务口径。
2. C 按 SQL 工具契约实现 `sql.query`，先验证客户数量和 2025 年 Rock 销售额。
3. B 按提问接口用模拟响应搭页面，再接 C 的真实返回。
4. B 接入 D01、D06、D09 的 RAG 返回。
5. 三人共同联调三个核心问题。

第一轮只要求接口可跑通，不要求立刻实现全部 12 份文档、所有中级任务或复杂视觉效果。
