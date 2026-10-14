# 快速路径、轻量 RAG 与跨源计算设计

## 设计目标

当前系统有三个主要问题：

1. 单表 SQL 和单文档 RAG 走了过长链路，响应时间明显高于 10 月 10 日版本；
2. 基础 RAG 仍有路由错误、过度澄清、来源缺失和答案字段不完整；
3. 公式与跨源已经有 EvidenceLedger、FormulaRegistry 和 bundle，但线上装配仍不稳定。

本设计采用“恢复旧路径、减少在线判断、只对复杂问题升级”的策略：

```text
简单单表 SQL  → Fast SQL
简单单文档问答 → Fast RAG
公式、跨源、多文档 → Deep Cross
```

目标：

```text
固定单表 SQL p95 <= 1s
固定单文档 RAG p95 <= 3s
基础 NL2SQL >= 15/15
基础 RAG >= 13/15
跨源 >= 8/10
公式 >= 5/6
```

## 10 月 10 日版本的恢复策略

10 月 10 日版本的可用基线是：

- 基础 RAG 10/10；
- 多文档 5/5；
- 多模态 5/5；
- 平均延迟约 1.1 秒；
- 文档类问题大多是本地检索和模板回答。

对应的 Git 恢复参考点为：

```text
9f1f32f  fix: narrow cross-source routing signals
4121cc5  feat: add bounded RAG evidence planning
447691a  feat: index structured target evidence
3de1b43  feat: add authority-aware hybrid RAG retrieval
```

这些提交是参考实现，不直接覆盖当前分支。实施前先创建两个本地保护点：

```powershell
git tag -f checkpoint/fast-path-before-20261013 HEAD
git branch -f checkpoint/fast-path-before-20261013 HEAD
```

如果新实现导致回归，完整回退：

```powershell
git reset --hard checkpoint/fast-path-before-20261013
```

如果只需要恢复 10 月 10 日的快速 RAG 路径，先建立对照工作树：

```powershell
git worktree add ..\rag-fast-reference-20261010 9f1f32f
```

对照工作树只用于比较运行结果、调用链和耗时，不直接复制整棵代码目录。当前分支每个阶段独立提交，失败时优先回退到上一阶段提交。

## 总体架构

```text
QuestionIR
    │
    ├── Fast SQL
    │     └── 参数化查询计划 → C 校验 → 固定答案
    │
    ├── Fast RAG
    │     └── 单来源检索 → 事实模板 → 来源展示
    │
    └── Deep Cross
          ├── SQL actual
          ├── RAG target/formula/context
          ├── EvidenceBundle
          ├── FormulaRegistry
          └── 结构化答案
```

路径选择只发生一次。进入路径后不允许再次把同一问题改成另一类任务，除非出现明确的失败类型并记录原因。

## Fast SQL

### 适用条件

同时满足以下条件时走 Fast SQL：

- 已注册指标；
- 明确时间、实体或范围；
- 不要求文档定义、规则、目标、复盘或运营建议；
- 不需要公式计算；
- 不需要跨源事实。

典型问题：

- 2025 年第三季度音频销售额是多少；
- Rock 卖了多少件；
- 各媒体类型销售额和销量是多少。

### 执行链路

```text
QuestionIR
→ MetricCatalog 匹配
→ SQLShape/参数化计划
→ C 只读校验
→ SQLite 执行
→ 固定 SQL 答案模板
```

Fast SQL 不调用：

- RAG；
- 通用答案合成模型；
- 公式 resolver；
- 多轮语义路由模型。

只有以下情况升级到 Planned SQL：

- 多表 JOIN；
- TOP-N；
- 窗口或复杂聚合；
- 零值左连接；
- 条件组合超出已注册 SQLShape。

### 速度控制

- 服务启动时加载 profile、SQL catalog 和固定 SQLShape；
- 请求内不重复初始化 profile 或知识索引；
- 对同一 profile 的 SQLShape 和 schema 做进程内缓存；
- 固定形状查询直接执行，不等待 LLM 候选；
- 答案直接从结构化行生成。

## Fast RAG

### 适用条件

同时满足以下条件时走 Fast RAG：

- 一个主要事实角色；
- 一个主要文档或一个小型来源集合；
- 不需要跨源计算；
- 不需要多文档比较；
- 不存在来源冲突。

典型问题：

- 客单价和成交均价有什么区别；
- 默认音乐销售额包含哪些媒体类型；
- Rock 和 Rock And Roll 是否同一分类；
- 活动报名截止时间是什么。

### 执行链路

```text
QuestionIR
→ SourceHint
→ 一次词法/结构化检索
→ 事实模板渲染
→ 来源校验
```

Fast RAG 只有在以下情况下才调用模型合成：

- 问题要求总结或解释；
- 检索到多个来源，需要自然语言合并；
- OCR 或表格证据需要语义转述；
- 内容存在冲突。

### RAG 软约束

RAG 来源计划只分三种来源：

```text
primary      决定核心答案的主来源
secondary    用于补充字段的来源
context      用于解释、建议或背景的来源
```

缺少 secondary 或 context 时，保留主来源答案并列出缺口。只有 primary 缺失时才返回证据不足。

示例：

```text
媒体范围：
primary   D01
secondary D03
context   D12

活动方案：
primary   D10
secondary D11

分类边界：
primary   D03
```

每轮最多执行：

```text
第一次检索 + 一次定向补检索
```

禁止对单文档问题开启无限补检索或多轮模型重写。

### 答案模板

Fast RAG 先填结构化事实，再渲染文本：

```text
media_scope:
  audio_media_ids
  video_media_ids
  inclusion_rule
  source_refs

metric_definition:
  formula
  numerator
  denominator
  zero_division_rule
  source_refs

activity_fact:
  period
  deadline
  materials
  page_position
  source_refs
```

模型不能删除已确认字段，也不能用普通相似片段替代 primary 来源。

## Deep Cross

### 适用条件

以下问题进入 Deep Cross：

- 实际值与目标值比较；
- 实际值与复盘文档联合解释；
- 实际值与活动方案或运营手册联合解释；
- 公式计算；
- Q2/Q3 多文档对比；
- SQL 先找实体、再检索文档的多跳问题。

### 并行执行

没有依赖关系时，SQL 和 RAG 并行：

```text
             ┌── SQL actual ──┐
QuestionIR ──┤                 ├── EvidenceBundle → Formula → Answer
             └── RAG target ──┘
```

只有“先从数据库找出实体，再拿实体查文档”才串行：

```text
SQL entity result → retrieval query rewrite → RAG operation context
```

### EvidenceBundle

```text
actual      SQL scalar
target      D06 scalar
formula     FormulaRegistry + D01/D02 locator
review      D07/D08 context
operation   D09/D10/D11 context
```

只有以下角色可以作为 calculator operand：

```text
actual / target / current / previous
```

review 和 operation 只参与解释、冲突说明和答案引用。

缺失规则：

```text
缺 actual 或 target → 不计算，不下结论
缺 formula         → 不计算，列出缺公式来源
缺 review          → 保留计算，缺复盘解释
缺 operation       → 保留计算，缺运营解释
```

## 具体模块调整

### 延迟路径

新增或调整：

```text
services/agent_api/app/agent/fast_path.py
services/agent_api/app/agent/route_policy.py
services/agent_api/app/response/fast_renderer.py
```

`fast_path.py` 只负责判断是否满足 Fast SQL/Fast RAG；`fast_renderer.py` 只渲染已经确认的结构化字段，不调用模型。

### RAG 路径

保留当前 `source_plan.py`，但将 required source 从硬性阻塞改为主次来源：

```text
services/agent_api/app/knowledge/source_plan.py
services/agent_api/app/knowledge/index.py
services/agent_api/app/agent/task_plan.py
```

检索失败时只对缺少的 primary/secondary 角色执行一次补检索。

### 公式和跨源

保留当前：

```text
services/agent_api/app/evidence/formulas.py
services/agent_api/app/evidence/ledger.py
services/agent_api/app/evidence/cross_source_bundle.py
```

新增职责：

- FormulaRegistry 成为公式选择唯一入口；
- CrossSourceBundleBuilder 成为 actual/target/formula/context 唯一装配入口；
- composer 只渲染 bundle，不重新猜测事实；
- SQL/RAG 独立时通过 LangGraph fan-out/fan-in 并行。

## 恢复与切换步骤

### 恢复 10 月 10 日快速路径参考

```powershell
git tag -f checkpoint/fast-path-before-20261013 HEAD
git branch -f checkpoint/fast-path-before-20261013 HEAD
git worktree add ..\rag-fast-reference-20261010 9f1f32f
```

在参考工作树中验证：

```powershell
uv run python scripts/run_agent_eval.py --files eval_basic_nl2sql,eval_basic_rag --mode live --out tmp/reference-20261010.json
```

参考工作树只用于比较延迟、route、trace 和答案内容。不要把整个旧工作树覆盖到当前分支。

### 逐步切换

1. 先启用 Fast SQL，只影响已注册单表查询；
2. 验证基础 NL2SQL 正确率和 p95；
3. 再启用 Fast RAG，只影响单文档固定事实；
4. 验证基础 RAG 正确率和 p95；
5. 最后启用 Deep Cross 的并行 actual/target 装配；
6. 每阶段提交 Git，并保存阶段报告。

### 回退规则

```powershell
# 回退到本方案开始前
git reset --hard checkpoint/fast-path-before-20261013

# 回退到上一阶段
git reset --hard <上一阶段提交>

# 对比 10 月 10 日版本
git --no-pager diff 9f1f32f...HEAD -- services/agent_api/app/agent services/agent_api/app/knowledge services/agent_api/app/response
```

出现以下任一情况立即回退：

- 基础 NL2SQL 低于 15/15；
- 基础 RAG 低于 10/15；
- Fast SQL p95 超过 2 秒；
- Fast RAG p95 超过 5 秒；
- SourceMismatch 或未授权计算增加；
- 跨源答案出现未绑定数字。

## 验收指标

### 性能

```text
Fast SQL p95 <= 2s
Fast RAG p95 <= 3s
Fast RAG max <= 8s
Deep Cross p95 <= 10s
```

### 正确率

```text
basic_nl2sql >= 15/15
basic_rag >= 13/15
cross_source >= 8/10
formula >= 5/6
overall >= 86/107
```

### 交付提交

每个阶段都必须包含：

- 失败测试和通过测试；
- 阶段评测 JSON；
- p50/p95/max 延迟；
- Git 提交；
- 可执行回退命令。

## 设计结论

当前方案以 10 月 10 日版本的低延迟和 RAG 通过率为恢复基线，只保留当前版本已经完成的 QuestionIR、EvidenceLedger 和 FormulaRegistry 作为深度能力基础。

核心原则：

```text
简单问题走短路径
复杂问题才进入深度流程
RAG 来源计划提供帮助，不把所有来源都变成硬门槛
公式只计算已授权事实
每阶段可回退
```
