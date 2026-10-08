# B 端 Agent 分阶段方案

> 本文只描述架构选择、阶段目标和模块边界，供负责落地的同学执行。当前不包含 B 端代码实现。

## 一、总体选择

B 端采用 Python，与 C 端共用语言和数据模型，减少跨语言服务与联调成本。

核心方案为：

```text
LangGraph       负责业务流程编排和会话状态
Haystack        负责 RAG 检索管线
Docling         负责离线文档解析
ToolRuntime     B 自己实现，借鉴 pi-agent-core 的运行时设计
```

`pi-agent-core` 只作为设计参考，不直接作为 B 的依赖。LangGraph 是唯一负责全局流程的 Agent 框架，ToolRuntime 只负责单次工具调用、事件和错误处理。

整体流程：

```text
用户问题
  -> 意图与要素识别
  -> 语义校验/主动澄清
  -> 路由
      -> SQL：调用 C
      -> RAG：调用 Haystack
      -> cross_source：按依赖编排 SQL 和 RAG
      -> unsupported：说明数据缺口
  -> 证据与计算结果合并
  -> 答案、来源和执行轨迹
```

## 二、模块与开源项目分工

| 模块 | 采用方案 | 直接使用还是自建 | 责任边界 |
|---|---|---|---|
| 流程编排 | LangGraph | 直接使用 | 节点、条件路由、循环、Checkpoint、跨源流程 |
| 工具运行时 | pi-agent-core 的设计思想 | B 自建轻量层 | 工具注册、参数校验、超时、错误、事件和结果标准化 |
| 文档解析 | Docling | 第二阶段直接使用 | PDF、DOCX、图片、表格、页码和章节结构解析 |
| 文档检索 | Haystack | 第二阶段直接使用 | 文档入库、召回、过滤、排序和证据片段返回 |
| SQL 能力 | C 端服务 | B 只做适配 | B 调用 C；C 负责 Schema Linking、SQL 生成、校验和执行 |
| 意图/要素识别 | LangGraph 节点 + profile 配置 | 自建 | 指标、实体、时间、媒体、比较关系和置信度 |
| 计算与证据合并 | B 业务节点 | 自建 | 公式计算、来源对齐、限制说明和最终答案 |
| API 和响应 | FastAPI + Pydantic | 直接使用基础库，自建模型 | 对齐现有请求、响应、错误和 trace 契约 |

明确不采用：把 LangGraph、pi、Haystack 各自作为 Agent 主循环再嵌套；也不在 B 中直接连接数据库或执行任意 SQL。

## 三、与现有接口契约的约束

B 的外部入口保持：

```text
POST /api/v1/ask
```

请求继续使用 `question`、`profile_id`、`session_id`、`user_role` 和 `options`。前端不传 SQL、数据库连接、文件路径或任意过滤表达式。

返回必须继续使用现有字段：

```text
status
answer
route
intent
entities
time_range
sql_results
documents
calculations
metric_definitions
limitations
clarification
trace
error
```

路由只使用契约已有值：

```text
sql / rag / cross_source / clarification / unsupported
```

工具实现可以替换，但不得改变上述字段含义。所有数字、规则和结论应能通过 `sql_results`、`documents` 或 `calculations` 回溯；`trace` 只展示结构化执行轨迹，不暴露模型隐式思维链。

## 四、阶段一：先跑通最小闭环

### 目标

先搭起可启动的 B 端流程，证明 LangGraph、ToolRuntime 和现有接口能够协同工作。阶段一使用 Demo 工具，暂不接入真实 Haystack、Docling 和 C 服务。

### 各部分怎么做

| 部分 | 阶段一计划 | 借鉴/自建 |
|---|---|---|
| 流程 | 问题解析 -> 路由 -> 工具 -> 答案 | LangGraph 直接使用 |
| SQL | 用 Chinook 的少量只读查询模板模拟 C 的结果格式 | Demo 自建，接口按 C 的结果形状设计 |
| RAG | 从现有 FAQ Markdown 返回带文档 ID 和位置的片段 | Demo 自建，接口按 Haystack 结果形状设计 |
| 澄清 | 对缺少比较周期、时间或业务范围的问题返回澄清 | 规则自建，挂在 LangGraph 节点上 |
| 工具事件 | 记录工具开始、结束、失败、耗时和来源 | 自建 ToolRuntime，借鉴 pi 的事件粒度 |
| 会话 | 保存 `session_id` 和当前业务状态 | 使用 LangGraph 的 Checkpoint 能力 |
| 答案 | 返回答案、工具结果、来源、限制和 trace | 自建答案节点，遵守现有契约 |

### 阶段一交付标准

- 能回答基础 SQL 问题和单文档 RAG 问题；
- 能对“销售增长率是多少”主动澄清；
- 能对利润、库存、退款、播放量等数据缺口返回 `unsupported`；
- `status`、`route`、结果字段和 `trace` 与现有接口契约一致；
- 之后替换真实工具时，不需要修改前端请求和响应格式。

## 五、阶段二：接入真实工具和基础跨源

### 目标

保持阶段一的接口不变，把 Demo 工具替换成 C、Docling 和 Haystack，并补上多轮对话与基础跨源流程。

### 各部分怎么做

| 部分 | 阶段二计划 | 借鉴/自建 |
|---|---|---|
| SQL 接入 | `sql.query` 通过适配器调用 C 的内部接口 | C 能力由 C 端实现，B 自建适配器 |
| 文档处理 | 离线解析 D01-D12，保留文档 ID、页码、章节、表格位置和来源 URI | Docling 直接使用，元数据规则自建 |
| RAG | 将解析结果转为 Haystack 文档，建立检索、过滤和排序管线 | Haystack 直接使用，证据格式按契约自建 |
| 要素标准化 | 使用 profile 中的别名、分类边界和时间规则 | 规则与配置自建，低置信度进入澄清 |
| 多轮对话 | 澄清后恢复原问题状态，不重复猜测已确认条件 | LangGraph Checkpoint + 自建槽位模型 |
| 跨源 | 先实现固定的 SQL→RAG 和 SQL∥RAG 两种路径 | LangGraph 子图自建，不做自由多 Agent 协作 |
| 可解释性 | 展示 SQL、检索片段、计算输入和工具链 | ToolRuntime 事件 + 契约字段 |

### 阶段二交付标准

- B 能稳定调用 C，并完整保留 SQL 结果和来源；
- RAG 证据可定位到文档和章节/页码；
- 能连续处理多轮澄清；
- 至少完成三类跨源问题；
- 错误、空结果和证据不足都有结构化返回。

## 六、阶段三：多跳、鲁棒性和比赛证明

### 目标

围绕决赛的多源、多跳、多模态、可解释和性能要求增强系统，并形成可写进技术文档的评测数据。

### 各部分怎么做

| 部分 | 阶段三计划 | 借鉴/自建 |
|---|---|---|
| 多跳任务 | 将复杂问题拆成有依赖的 LangGraph 子图，例如先 SQL 找实体，再 RAG 查方法 | LangGraph 编排，任务模板自建 |
| 多表查询协同 | B 只负责调用和传递上下文，JOIN、聚合、粒度和 SQL 安全仍由 C 负责 | C 自建，B 保持适配边界 |
| 多文档 RAG | 查询改写、并行召回、重排、证据去重和多文档合并 | Haystack 管线，融合规则自建 |
| 多模态资料 | 处理扫描件、图片、复杂表格并保留区域来源 | Docling 为主，必要时增加 OCR 组件 |
| 公式计算 | 从来源中提取输入，交给受控计算器，记录公式和输入引用 | 计算器自建，不让 LLM 直接决定关键数字 |
| 鲁棒性 | 处理错别字、低置信度、空结果、来源冲突和工具超时 | LangGraph 分支 + 自建降级策略 |
| 评测 | 评估答案、路由、来源、澄清、多轮一致性和端到端时延 | 使用现有 `eval/agent`、`eval/human`，评测逻辑自建 |
| 性能 | 对解析、检索、SQL 和答案生成分别测时，增加缓存和并行 | LangGraph 并行节点 + 自建缓存/限流 |

### 阶段三交付标准

- 复杂跨源、多跳问题能按固定流程复现；
- 复杂文档的答案能返回可定位证据；
- 关键计算都有公式、输入和来源；
- 能提供准确率、来源命中率、澄清成功率、多轮一致性和时延数据；
- 演示页面能展示完整结构化链路。

## 七、实施时的边界和注意事项

1. LangGraph 负责“下一步做什么”，ToolRuntime 负责“这一步怎么执行”。
2. pi-agent-core 只借鉴运行时设计；当前 Python 方案不直接引入 Node 子进程或 RPC。
3. Haystack 只负责 RAG 子系统，不决定 SQL/RAG 全局路由。
4. Docling 负责离线解析，用户提问时只做检索，不重复解析原文件。
5. C 负责 SQL 的正确性和安全性，B 不绕过 C 直接访问数据库。
6. 先按契约固定适配器，再扩展内部实现；不要因为开源项目提供额外功能就引入第二套状态或 Agent Loop。
