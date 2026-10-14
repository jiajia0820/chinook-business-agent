# 四项中级任务统一优化总设计

## 设计目标

围绕比赛中当前确定的四项中级任务，建设一条统一、可验证、可回退的问数/问答链路：

1. 跨源问答：联合 Chinook 数据库与 D01-D12 业务文档，完成实际值、目标值、公式和运营规则的联合分析。
2. 要素精准识别与问题改写：识别指标、实体、时间、范围、比较关系和排除条件，并将问题改写为可执行的规范任务。
3. 文档公式识别与计算：从业务文档中确认公式及其输入，绑定同周期同范围事实后进行确定性计算。
4. 语义检验与澄清：区分可直接回答、需要澄清和数据不支持三类情况，避免过度澄清、错误路由和多轮条件丢失。

目标不是分别修四套逻辑，而是让四项能力共享同一个语义任务、证据计划和答案契约。以当前 107 条 Chinook 评测集的可比业务分为基线，目标是达到至少 86/107（约 80%），并且四项重点能力分别达到可持续复测的通过门槛。

## 范围与非目标

本设计只服务于四项重点任务及其必要的支撑能力。

纳入范围：

- 问题规范化、要素抽取、别名归一化、实体边界和排除条件；
- Schema Linking，包括指标、字段、表和 JOIN 路径选择；
- SQL 与文档的跨源任务规划、证据绑定和冲突检查；
- 文档公式抽取、公式结构化、参数绑定和确定性计算；
- 语义校验、最小澄清、多轮条件继承和任务恢复；
- 这些能力所必需的来源去重、页码定位、答案字段校验和解释链路。

不纳入范围：

- AdventureWorks；
- API 网关稳定性、模型供应商更换和外部调用重试策略；
- 独立建设其它中级任务或高级任务；
- 重新设计已经稳定的基础 NL2SQL、多表 SQL 主链路；
- 为了追求单题分数而修改评测器标准。

其它能力只保留回归保护：优化四项重点任务时不能破坏当前基础 NL2SQL 和已经通过的多表查询。

## 当前问题判断

当前系统的主要问题不是单个工具能力不足，而是语义信息在阶段之间丢失或重新解释：

```text
原始问题
  → 解析出部分 slots
  → 路由
  → 分别调用 SQL/RAG
  → 各自生成局部结果
  → 尝试拼接答案
```

这种链路会产生以下失分：

- Rock、Rock And Roll、Metal、Heavy Metal 等实体边界没有形成稳定的结构化结果；
- “和目标比”“目标呢”等追问无法可靠继承上一轮条件；
- cross_source 路由虽然启动了 SQL 和 RAG，但实际值、目标值和公式没有绑定到同一周期和范围；
- 公式检索到数字后无法证明数字对应哪个指标和月份；
- 业务条件已经足够时仍然澄清；
- 一个来源缺失或 chunk 重复导致整轮证据被丢弃。

因此，设计重点从“增加更多路由规则”调整为“建立统一语义中间层”。

## 总体架构

```text
用户问题 / 对话历史
          │
          ▼
Question IR：问题改写与要素识别
          │
          ▼
Semantic Gate：回答 / 澄清 / 拒答
          │
          ▼
Data Plan：SQL、文档、公式子任务和依赖关系
          │
          ├──────────────┐
          ▼              ▼
     SQL Worker      Document Worker
          │              │
          └──────┬───────┘
                 ▼
Evidence Ledger：事实、来源、范围和冲突
                 │
                 ▼
Formula Binder：公式、参数、单位和计算结果
                 │
                 ▼
Semantic Verifier：周期、范围、实体、指标和来源校验
                 │
                 ▼
Answer Contract：答案、证据、计算、限制和解释链路
```

每一层都产生结构化结果。后续层只能使用前一层确认过的字段，不能重新猜测业务事实。

## 统一数据契约

### QuestionIR

QuestionIR 是所有路由和工具调用的唯一语义输入，至少包含：

```text
QuestionIR
├── original_question
├── rewritten_question
├── task_type
├── metrics
├── time_scope
├── entity_scope
├── media_scope
├── exclusions
├── comparison_context
├── requested_outputs
├── clarification_blockers
└── provenance
```

字段必须记录来源和状态：

```text
confirmed       用户明确说出，或由可信实体目录确认
defaulted       使用 profile 中声明的业务默认口径
inferred        根据任务类型推断，但仍需通过规则校验
unknown         当前无法确认
conflict        出现相互冲突的条件
```

问题改写不能添加原问题没有依据的条件。任何新增字段都必须标记为 `defaulted` 或 `inferred`，并在最终答案中说明。

### EntityMention

实体识别统一输出规范实体，不直接把别名写入 SQL 或答案：

```text
EntityMention
├── surface
├── entity_type
├── canonical_name
├── entity_id
├── included
├── confidence
├── boundary_status
└── provenance
```

示例：

```text
摇滚             → Rock          → Genre:1
Rock And Roll    → Rock And Roll → Genre:5
金属             → Metal         → Genre:3
Heavy Metal      → Heavy Metal   → Genre:13
```

“不包括 Rock And Roll”“Rock 和 Rock And Roll 分开统计”等表达必须转化为显式包含、排除或分组关系。

### DataPlan

DataPlan 描述问题需要哪些事实，不直接描述自然语言提示词：

```text
DataPlan
├── task_id
├── fact_role
├── source_kind        sql / document / formula
├── required_fields
├── required_scope
├── dependencies
└── acceptance_checks
```

跨源问题至少拆成以下事实角色：

```text
actual       数据库实际值或季度复盘实际值
target       目标表中的目标值
formula      指标定义和计算公式
operation    运营规则、活动方案或品类手册
```

### EvidenceLedger

EvidenceLedger 不是简单的文档引用列表，而是带完整维度的事实账本：

```text
EvidenceItem
├── fact_role
├── metric
├── value
├── unit
├── period
├── entity_scope
├── media_scope
├── source_ref
├── source_kind
└── validation_status
```

只有 `metric`、`value`、`unit`、`period`、`scope` 和 `source_ref` 都齐全，事实才可以进入公式或最终结论。

### FormulaSpec

公式不以模型生成的自然语言保存，而以结构化公式保存：

```text
FormulaSpec
├── function
├── expression
├── operands
├── result_metric
├── result_unit
├── scope_requirements
└── source_ref
```

支持的核心公式包括：

```text
target_difference = actual - target
attainment_rate   = actual / target * 100%
growth_rate       = (current - previous) / previous * 100%
average_order_value = sales_amount / order_count
monthly_sum       = sum(monthly_plan)
```

### AnswerContract

答案不是由模型自由决定内容，而是先满足任务契约：

```text
AnswerContract
├── direct_answer
├── facts
├── calculations
├── entities
├── evidence
├── limitations
└── clarification
```

跨源任务的必填事实是 `actual`、`target`、`gap` 或 `attainment_rate`、`verdict` 和两侧来源；公式任务还必须包含公式、操作数、单位和范围。

## 分阶段路线

### 阶段 0：建立基线和回退边界

目标是确保后续每个阶段都能证明收益，没有阶段之间的归因混乱。

阶段产物：

- 以 `checkpoint/pre-four-task-optimization-20261012` 作为回退点；
- 记录当前 107 条评测逐题结果；
- 建立四项任务的专项结果表；
- 建立基础 NL2SQL、多表查询的回归保护集；
- 将“业务失败”和“外部 API 待复测”分开统计。

阶段门槛：

- 基础 NL2SQL 不能低于当前稳定水平；
- 多表查询不能出现已有通过题回归；
- 每个后续阶段都能输出新增通过题、回归题和未改变题。

### 阶段 1：Question IR、要素识别和问题改写

目标是先让所有重点问题进入同一套规范语义表示。

能力边界：

- 识别指标、实体、时间、媒体范围、比较关系和排除条件；
- 将业务别名映射到实体 ID；
- 区分 Rock 与 Rock And Roll、Metal 与 Heavy Metal；
- 识别“目标”“增长”“差多少”“是否达标”等任务关系；
- 支持“目标呢”“和上期比”“换成 Metal”等追问改写；
- 保留原问题和改写依据，禁止无来源条件注入。

阶段门槛：

- 重点别名题的实体 ID、边界和排除条件全部进入结构化结果；
- 多轮追问保留已确认的时间、指标和范围；
- 改写结果可以被 SQL、RAG 和公式模块共同使用；
- 当前别名题中只因 `Genre:<id>` 格式导致的失分应归零。

### 阶段 2：语义检验、澄清和任务恢复

目标是把“可回答、需澄清、不可支持”分成明确的决策层。

决策顺序：

1. 检查任务类型是否已确认；
2. 检查是否缺少阻塞条件；
3. 检查是否存在冲突条件；
4. 检查 profile 是否有默认口径；
5. 决定直接回答、澄清或拒答。

规则：

- 文档已有默认口径时直接回答，并声明默认口径；
- 只有阻塞条件缺失时才澄清；
- 澄清只列出最小缺口；
- 不因为模型没有参与就向用户索要本可由文档补全的条件；
- 澄清回答后只更新缺失字段，不重新清空整个任务；
- 多轮更新必须保留字段来源和版本。

阶段门槛：

- 专项澄清达到 8/8；
- 基础 RAG、跨源和多文档中由过度澄清造成的失败显著下降；
- 多轮中“已确认条件丢失”和“应该澄清却返回证据不足”不再出现；
- 对同一问题重复运行，决策状态保持一致。

### 阶段 3：DataPlan、EvidenceLedger 和跨源绑定

目标是把跨源任务从“调用两个工具”升级为“完成多个事实角色的绑定”。

每个跨源任务先生成 DataPlan，再并行执行 SQL 和文档子任务。子任务完成后写入 EvidenceLedger，最后统一校验：

```text
指标一致性
周期一致性
实体一致性
媒体范围一致性
单位一致性
来源完整性
```

跨源缺失处理采用部分完成：

```text
actual 已取得
target 已取得
formula 缺失
→ 明确说明无法计算达成率，不丢弃 actual 和 target
```

阶段门槛：

- D06 目标、D01/D02 公式、D08 实际或复盘事实按角色稳定召回；
- cross_source 至少达到 8/10；
- actual、target、gap、verdict 和来源不会互相错配；
- 跨源任务不因一个非核心文档片段缺失而清空全部已验证证据。

### 阶段 4：文档公式识别与语义计算

目标是让公式识别成为可复用的文档能力，而不是每次在线临时猜测。

离线处理：

- 从 D01、D02、D06、D07、D08 中抽取公式候选；
- 标注公式输入、输出、单位、适用周期和业务范围；
- 形成 FormulaSpec 注册表；
- 为表格中的月份、指标、品类和目标值建立结构化索引。

在线处理：

- 从 QuestionIR 选择公式；
- 从 EvidenceLedger 绑定操作数；
- 校验周期、范围、实体和单位；
- 由确定性计算器执行；
- 在答案中展示公式、操作数、结果和来源。

阶段门槛：

- 公式专项达到至少 5/6；
- 不再出现未捕获的时间范围异常；
- 公式计算失败时返回明确缺失参数；
- 计算结果不依赖模型是否输出自然语言数字。

### 阶段 5：统一答案契约和四项任务回归

目标是防止事实已经正确，却因为格式、引用或输出缺字段而丢分。

答案校验包括：

- 实体 ID 使用统一格式，如 `Genre:1`；
- 跨源答案包含实际值、目标值、差额或达成率和结论；
- 公式答案包含公式、输入、单位和范围；
- 澄清答案包含精确缺失槽位；
- 引用按文档 ID、版本、chunk、页码或定位信息输出；
- 重复 chunk 在证据合并前去重；
- 定位问题不能摧毁已经确认的事实；
- 输出中的内部 metric key 必须转换为用户可理解的业务词。

最终专项门槛：

| 能力 | 目标通过率 |
|---|---:|
| 跨源问答 | ≥ 8/10 |
| 要素识别与改写相关题 | ≥ 12/15 |
| 公式识别与计算 | ≥ 5/6 |
| 语义检验与澄清 | ≥ 8/8 |
| 多轮条件继承 | ≥ 4/5 |
| 整体 Chinook 评测 | ≥ 86/107 |

## 建议的模块边界

现有大文件不立即整体重构，先按职责抽出可独立验证的边界：

```text
services/agent_api/app/agent/
├── semantic_ir.py       QuestionIR、EntityMention、字段来源
├── entity_linking.py    别名、实体候选、边界和排除条件
├── query_rewrite.py     问题规范化和多轮增量改写
├── semantic_gate.py     answer / clarification / unsupported
├── task_plan.py         DataPlan 和子任务依赖
└── turn_update.py       多轮字段继承和冲突处理

services/agent_api/app/evidence/
├── ledger.py            EvidenceLedger
├── binding.py           事实绑定和范围校验
├── formulas.py          FormulaSpec 和公式注册表
└── verification.py      语义、单位、周期和实体校验
```

`graph.py` 保留编排职责，`parsing.py` 逐步收敛为输入适配层，`composer.py` 只负责通过 AnswerContract 生成答案，不再承担大部分业务判断。

## 评测和验证原则

每个阶段都必须同时运行三类检查：

1. **结构化单元测试**：验证 QuestionIR、实体边界、公式 AST、语义门和范围校验。
2. **能力场景测试**：验证跨源、追问、目标比较、公式和澄清的完整链路。
3. **全量回归测试**：确认基础 NL2SQL、多表查询和已通过题不回归。

专项指标不能只看最终答案是否通过，还要记录：

- route 是否正确；
- QuestionIR 是否正确；
- 实体和范围是否正确；
- 证据角色是否齐全；
- 公式绑定是否成功；
- 澄清是否最小且准确；
- AnswerContract 缺失了哪些字段。

这套分层指标可以区分“理解错、取证错、绑定错、计算错、输出错”，避免再次把所有问题归类为 `insufficient_evidence`。

## 回退和阶段交付规则

- 当前代码回退点：`checkpoint/pre-four-task-optimization-20261012`。
- 每个阶段只允许形成独立、可测试的提交。
- 阶段未达到门槛时不进入下一阶段。
- 发现基础 SQL 或多表回归时，立即回到上一阶段提交，不在后续阶段继续叠加修改。
- 设计文档本身不改变运行时行为；只有实施计划获得确认后才进入代码修改。

## 设计结论

四项任务应作为一个统一的语义和证据系统实施：

```text
问题改写与要素识别
→ 语义决策与澄清
→ 跨源任务规划
→ 证据绑定
→ 公式识别和计算
→ 答案契约校验
```

这样既能针对当前评测的主要失分，也能让四项中级任务形成可展示、可解释、可复测的完整技术闭环，并为达到 86/107 的整体目标保留足够的回收空间。
