# 数据问答智能体接口契约

版本：v0.3

本文只规定前端、Agent、SQL 查询模块和文档检索模块之间的接口格式。具体数据集、表名、字段名、指标口径和文档内容由 `profile_id` 对应的配置提供，不写死在通用接口中。

## 1. 整体流程

```text
网页端 -> POST /api/v1/ask -> Agent -> SQL / RAG / 联合处理 -> 统一返回
```

支持三种数据能力：

- `sql_only`：只有结构化数据；
- `rag_only`：只有非结构化文档；
- `hybrid`：结构化数据和非结构化文档同时存在。

每个 profile 至少提供 `profile_id`、`mode` 和对应的数据能力。`mode` 只决定当前 profile 可调用的能力，不改变请求和返回格式。

| `mode` | 允许的主要 `route` |
|---|---|
| `sql_only` | `sql`、`clarification`、`unsupported` |
| `rag_only` | `rag`、`clarification`、`unsupported` |
| `hybrid` | `sql`、`rag`、`cross_source`、`clarification`、`unsupported` |

## 2. 提问接口

### 请求

接口：`POST /api/v1/ask`

```json
{
  "question": "用户的问题",
  "profile_id": "example-profile",
  "session_id": "session-001",
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
| `question` | string | 是 | 用户问题，不能为空 |
| `profile_id` | string | 是 | 当前数据配置标识 |
| `session_id` | string | 否 | 多轮会话标识；没有时由服务端生成 |
| `user_role` | string | 否 | 用户角色，默认由应用配置决定 |
| `options.show_trace` | boolean | 否 | 是否返回执行记录，默认 `true` |
| `options.max_rows` | integer | 否 | SQL 结果最大行数，默认 50，上限 200 |
| `options.top_k` | integer | 否 | 文档检索片段数，默认 5，上限 10 |

前端不传 SQL、数据库连接、文件路径或任意过滤表达式。

### 返回

```json
{
  "request_id": "req-001",
  "session_id": "session-001",
  "profile_id": "example-profile",
  "status": "clarification_required",
  "answer": null,
  "route": "clarification",
  "intent": {"name": "query", "confidence": null},
  "entities": [],
  "time_range": null,
  "sql_results": [],
  "documents": [],
  "calculations": [],
  "metric_definitions": [],
  "limitations": [],
  "clarification": {
    "question": "请补充必要查询条件。",
    "missing_slots": [{"name": "required_context", "description": "由 profile 定义"}],
    "options": []
  },
  "trace": [],
  "error": null
}
```

### 返回字段

| 字段 | 说明 |
|---|---|
| `status` | `answered`、`clarification_required`、`insufficient_evidence`、`unsupported`、`error` |
| `route` | `sql`、`rag`、`cross_source`、`clarification`、`unsupported` |
| `intent` | `{ "name": string, "confidence": number|null }` |
| `entities` | 实体识别结果数组；具体实体类型和标准值由 profile 配置解释 |
| `time_range` | `{ "start": string, "end": string, "end_inclusive": boolean, "label": string }`；无法确定时为 `null` |
| `sql_results` | SQL 输出对象数组；不使用时返回空数组 |
| `documents` | 文档证据片段数组；不使用时返回空数组 |
| `calculations` | `{ "calculation_id", "formula", "inputs", "result", "unit" }` 数组 |
| `metric_definitions` | `{ "metric_id", "name", "definition", "unit", "source_refs" }` 数组 |
| `limitations` | 数据缺口、范围限制或不确定性 |
| `clarification` | `{ "question": string, "missing_slots": [], "options": string[] }`；不需要时为 `null` |
| `trace` | 工具执行状态；不返回模型隐性思维链 |
| `error` | 结构化错误；没有错误时为 `null` |

未使用的结果字段返回空数组或 `null`，不伪造内容。

`answer` 在 `answered` 时为字符串；在澄清、不支持、证据不足或错误状态下可以为 `null`。

HTTP 约定：请求格式正确但业务状态为 `answered`、`clarification_required`、`insufficient_evidence` 或 `unsupported` 时返回 HTTP 200；请求格式错误返回 4xx；服务内部异常返回 5xx。业务状态仍以响应体中的 `status` 为准。

当 `status=unsupported` 时，`route` 必须为 `unsupported`，并在 `limitations` 中说明当前 profile 不具备的能力。

## 3. SQL 查询接口

### 输入

```json
{
  "query_id": "sql-001",
  "profile_id": "example-profile",
  "sql": "SELECT ...",
  "params": {},
  "max_rows": 50,
  "timeout_ms": 5000
}
```

### 输出

```json
{
  "query_id": "sql-001",
  "profile_id": "example-profile",
  "status": "success",
  "columns": ["column_a"],
  "rows": [{"column_a": 123.45}],
  "row_count": 1,
  "truncated": false,
  "execution_ms": 8,
  "source": {"type": "database", "name": "profile-defined", "tables": []},
  "error": null
}
```

约定：

- `status` 为 `success`、`rejected` 或 `failed`；
- 只允许单条只读查询；
- 禁止写入、删除、建表、改表、外部连接等操作；
- 超出行数上限时返回 `truncated: true`；
- 错误返回结构化 `error`，不返回堆栈。

## 4. 文档检索接口

### 输入

```json
{
  "retrieval_id": "rag-001",
  "profile_id": "example-profile",
  "query": "检索问题",
  "filters": {},
  "top_k": 5
}
```

### 输出

```json
{
  "retrieval_id": "rag-001",
  "profile_id": "example-profile",
  "status": "success",
  "chunks": [
    {
      "chunk_id": "doc-001-c002",
      "doc_id": "doc-001",
      "title": "文档标题",
      "doc_type": "pdf",
      "page": 1,
      "section": "章节或段落位置",
      "text": "证据片段",
      "score": 0.91,
      "source_uri": "profile-defined"
    }
  ],
  "error": null
}
```

检索片段应尽量保留文档标题、页码、章节、段落或表格位置；扫描件或图片可增加 `bbox`。主返回中的 `documents` 使用这些片段字段，并可附带 `retrieval_id`。

## 5. 来源和计算记录

```json
{
  "calculation_id": "calc-001",
  "formula": "profile-defined",
  "inputs": ["sql-001", "doc-001"],
  "result": 123.45,
  "unit": "profile-defined"
}
```

```json
{
  "step": 1,
  "tool": "sql.query",
  "status": "success",
  "source_refs": ["sql-001"],
  "duration_ms": 8
}
```

所有回答中的数字、规则和结论，应能通过 `sql_results`、`documents` 或 `calculations` 回溯。

## 6. 错误格式

```json
{
  "code": "MISSING_REQUIRED_SLOT",
  "message": "缺少必要查询条件",
  "retryable": false,
  "details": null
}
```

建议错误码：

`INVALID_REQUEST`、`PROFILE_NOT_FOUND`、`PROFILE_UNAVAILABLE`、`MISSING_REQUIRED_SLOT`、`SCHEMA_LINKING_FAILED`、`SQL_READ_ONLY_VIOLATION`、`SQL_EXECUTION_FAILED`、`RAG_NO_EVIDENCE`、`DOCUMENT_PROCESSING_FAILED`、`UNSUPPORTED_CAPABILITY`、`INTERNAL_ERROR`。

## 7. 协作约定

- A 负责维护本文件和字段含义；
- B 负责网页、Agent 路由、RAG 和结果展示；
- C 负责各 profile 的数据接入、Schema 和只读 SQL；
- 代码不得依赖其他成员的内部文件，通过本契约传递数据；
- 新增字段保持兼容；删除字段或修改字段含义时先更新契约；
- 具体数据集、业务字段、指标口径和文档路径放在 profile 配置中，不写入通用接口。
