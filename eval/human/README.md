# 人工核查版评测集说明

这里汇总题库用途、简单用法和来源索引。点击下方题库名称即可阅读问题、参考答案和核查依据。

测试配置名为 `chinook-music`，定义见 [测试配置](../agent/interface_profile_fixture.json)。正式测试前，开发同学需要在应用中接入这个配置。

当前包含 75 道单轮题和 5 组多轮会话，机器文件清单见 [eval_manifest.json](../agent/eval_manifest.json)。

## 目录结构

- [agent/](../agent/)：评测程序读取的 JSONL 文件，包含问题、参考答案和评分依据。一行是一道单轮题，或一组多轮会话。
- 当前 `human/`：供人阅读的 Markdown 题库。
- [eval_validation_report.json](eval_validation_report.json)：题库校验记录，不是 Agent 实际答题的成绩。

## 简单使用方法

**人工核查：**打开下方题库，逐题看“问题—标准答案—来源—评分要点”。有疑问时打开对应业务文档；数值题可以进一步到同名 JSONL 文件查看完整的 SQL、参数和结果。

**手动测试 Agent：**复制题目中的自然语言问题到 Agent，保存它的回答，再对照参考答案检查数字、规则和证据。多轮题按顺序在同一会话提问，单轮题使用独立会话。

**程序批量测试：**按 manifest 加载 `agent/` 中的题库，提取 `interface_expectation.request`，发送至 `POST /api/v1/ask`。请求格式见 [接口契约](../../interface-contract.md)。标准答案、标准 SQL、预期文档和评分要点留在评测端，不随请求发送。然后保存实际响应，与题目的参考结果和评分要求比较。当前生成脚本负责准备题库，接口请求和评分需要由评测程序执行。

已知冲突题 `cross-009` 同时列出数据库和 D08 的不同数字，核查时要看回答有没有指出冲突。

## 常用字段

- `expected_tables`：预期使用的 Chinook 表；
- `expected_documents`：预期引用的 D01-D12 文档；
- `evidence_locations`：页码、标题或图片区域；
- `gold_sql` / `gold_result`：可执行 SQL 和实际结果；
- `gold_calculation`：目标差额、达成率、增长率等计算输入和公式；
- `gold_answer`：人工可读的参考答案；
- `gold_answer_requirements`：评分时必须出现的事实点。
- `interface_expectation`：与 `interface-contract.md` 对应的 profile、mode、预期 route/status、请求体和必需响应字段。

生成时间：2026-10-03T10:52:03



## 题库总览

| 题库 | 数量 | 作用和核查重点 |
| --- | ---: | --- |
| [基础 NL2SQL](eval_basic_nl2sql.md) | 10 题 | 自然语言查数；核对数值、时间和统计范围 |
| [基础文档问答](eval_basic_rag.md) | 10 题 | 文档问答；核对事实与原文依据 |
| [专项 澄清](eval_special_clarification.md) | 8 题 | 条件不完整；检查是否提出必要的补充问题 |
| [专项 别名与分类](eval_special_alias_robustness.md) | 8 题 | 业务别名与相近分类；检查名称和分类是否对应正确 |
| [专项 多表关联](eval_special_multitable.md) | 8 题 | 多表关联；核对关联关系、去重和汇总结果 |
| [专项 公式计算](eval_special_formula.md) | 6 题 | 公式计算；核对输入、公式、单位及结果 |
| [专项 多模态](eval_special_multimodal.md) | 5 题 | PDF 表格与海报读取；对照表格位置或图片区域 |
| [专项 无证据处理](eval_special_negative.md) | 5 题 | 数据不足的问题；检查是否说明缺口 |
| [跨源问答](eval_cross_source.md) | 10 题 | 数据库与业务文档联合分析；分别核对实际值和规则 |
| [多文档问答](eval_multidoc.md) | 5 题 | 多份文档综合问答；核对各项结论的依据 |
| [多轮会话](eval_multiturn.md) | 5 组 | 连续对话；在同一会话逐轮检查条件继承和修改 |

## 来源索引

| 来源 | 主要覆盖题型 |
| --- | --- |
| Chinook.db：Invoice、InvoiceLine、Track、Album、Artist、Genre、MediaType、Customer、Employee、Playlist、PlaylistTrack | 基础 NL2SQL、多表关联、客户/品类/商品排名、媒体范围 |
| D01 经营指标说明 | 指标定义、时间范围、音频范围、公式 |
| D02 销售分析方法说明 | 分析周期、增长率、目标差额、异常解释 |
| D03 音乐分类与商品范围说明 | Genre、MediaType、Rock/Rock And Roll、Metal/Heavy Metal |
| D04 客户分层与运营规则 | A/B/C 阈值、沉睡/回流客户、维护动作 |
| D05 商品推荐与选品规则 | 商品池、候选优先级、Rock/Metal/Latin选品 |
| D06 2025Q3经营目标表 | 品类目标、月度计划、目标口径 |
| D07 2025Q2经营复盘 | 二季度实际表现和历史比较 |
| D08 2025Q3经营复盘 | 三季度实际表现、达成率和四季度承接 |
| D09 重点音乐品类运营手册 | Rock、Metal、Latin品类动作 |
| D10 音乐主题活动方案 | 活动时间、商品候选和执行安排 |
| D11 活动报名与选品条件海报 | 图片文字、报名截止、提交材料和区域证据 |
| D12 经营查询与数据使用FAQ | 数据边界、无证据问题和常见指标解释 |

每道题的 `evidence_locations` 是实际核查入口；数据库题的 `gold_sql` 和 `gold_result` 是可复算入口。
