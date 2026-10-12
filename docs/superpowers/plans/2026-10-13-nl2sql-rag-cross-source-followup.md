# NL2SQL、基础 RAG 与跨源问答后续实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 针对当前 Live 测评中基础 NL2SQL 12/15、基础 RAG 9/15、跨源 2/10 的主要失分，统一修复路由边界、权威来源计划和跨源事实装配，把三项能力提升到可稳定复测的高通过率。

**Architecture:** QuestionIR 是唯一的任务语义输入。先由 RoutePolicy 区分数据库事实、文档规则和跨源联合问题，再生成带事实角色的 DataPlan；SQL、RAG 和公式 resolver 分别产出 actual、target、formula、operation 等证据项，EvidenceLedger 负责范围校验，AnswerContract 负责最终输出。已有 C SQL 安全校验、文档来源校验和 FormulaRegistry 保持不变，只补齐它们之间的装配层。

**Tech Stack:** Python 3.12、Pydantic v2、LangGraph、SQLAlchemy、sqlglot、Chinook SQLite、D01-D12 知识库、unittest。

---

## 当前基线与目标

基线报告：`tmp/chinook-eval-live-20261013-final/report.json`。

| 能力 | 当前 | 本计划目标 |
|---|---:|---:|
| 基础 NL2SQL | 12/15 | 15/15 |
| 基础 RAG | 9/15 | 至少 13/15 |
| 跨源 | 2/10 | 至少 8/10 |
| 公式专项 | 0/6 | 至少 5/6，作为跨源依赖验收 |
| Chinook 总分 | 65/107 | 至少 86/107 |

当前三项的确定性问题：

- 基础 NL2SQL 失败的 3 题全部 route 成 `rag`，没有 `sql_results`；属于 SQL 前的路由错误。
- 基础 RAG 有 2 题过度澄清、3 题权威文档/页码不完整、1 题规则问题误走 SQL。
- 跨源有 3 题被路由成 RAG 或 clarification，3 题在 actual/target/formula 绑定阶段返回 `insufficient_evidence`，2 题仅返回文档侧或缺少数据库事实。
- FormulaRegistry 和 EvidenceLedger 已有纯模块测试，但尚未完整接入 `create_business_resolver` 和跨源答案链路。

## 执行约束

- 从当前分支 `codex/four-task-semantic-optimization` 和 HEAD `4ef586d` 继续。
- 优化范围只包含基础 NL2SQL、基础 RAG、跨源及其公式绑定依赖；不扩展 AdventureWorks、其它中级任务或高级任务。
- 不修改评测器金标准，不把文档文字直接转成 SQL 条件，不允许未授权的文档数字进入计算器。
- 每个任务先写失败测试，看到 RED 后再实现 GREEN；每个任务独立提交。
- 离线/受控测试作为阶段门槛，Live 测评作为最后复核。
- 基础 NL2SQL 已通过题和多表查询不能回归。

## 统一路由契约

所有路由必须由同一个 `QuestionIR` 决定，不允许 `parsing.py`、`task_policy.py`、`task_plan.py` 和 `graph.py` 各自重新猜测 route。

```text
SQL fact query
  明确问多少、列出、排名、按月份/国家/媒体/品类统计，要求数据库实际值

RAG rule query
  询问定义、口径、规则、怎么处理、如何解释、手册或方案

cross_source query
  同时要求数据库实际值与文档目标/公式/运营解释
```

路由优先级：

1. 有“数据库实际/列出/统计/排名/按条件汇总”等事实输出，且没有文档解释要求，优先 SQL。
2. 有“怎么处理/口径/规定/定义/建议/手册”等解释输出，且没有实际数值要求，优先 RAG。
3. 同时包含实际值和目标、公式、复盘或运营动作，优先 cross_source。
4. 只有真正缺少阻塞条件时才澄清。

## Task 1：建立路由决策表并修复三个基础 NL2SQL 误路由

**目标：** 修复 `basic-nl2sql-012/013/015` 的 `sql → rag` 误判，保持现有 12 道 NL2SQL 通过题不回归。

**Files:**

- Create: `services/agent_api/app/agent/route_policy.py`
- Create: `services/agent_api/tests/test_route_policy.py`
- Modify: `services/agent_api/app/agent/semantic_ir.py`
- Modify: `services/agent_api/app/agent/task_policy.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Test: `services/agent_api/tests/test_agent_task_policy.py`
- Test: `services/agent_api/tests/test_agent_ranking_seam.py`

- [ ] **Step 1: 写 RED 测试。**

```python
def test_monthly_database_breakdown_is_sql():
    task = route_question("按月份列出2025年音频销售额和音频订单数")
    assert task.route == "sql"
    assert task.task_type == "database_fact"


def test_multi_filter_database_fact_is_sql():
    task = route_question("2025年第三季度USA的Rock音频销售额、销量和订单数")
    assert task.route == "sql"
    assert set(task.requested_outputs) >= {"actual", "monthly_or_period_result"}


def test_left_join_zero_sales_request_is_sql():
    task = route_question("所有媒体类型都列出，没有成交的显示为0")
    assert task.route == "sql"
    assert "include_zero" in task.requested_outputs


def test_rule_question_with_order_words_is_rag():
    task = route_question("同一张订单有音频和视频时，音乐销售额应该怎么处理")
    assert task.route == "rag"
    assert task.task_type == "document_rule"


def test_actual_target_operation_question_is_cross_source():
    task = route_question("第三季度Metal未达目标，活动方案把Metal放在什么位置")
    assert task.route == "cross_source"
    assert set(task.requested_outputs) >= {"actual", "target", "operation"}
```

- [ ] **Step 2: 运行 RED。**

Run: `uv run python -m unittest services.agent_api.tests.test_route_policy -v`

Expected: 至少 3 个测试失败，失败原因是当前任务类型或 route 不正确。

- [ ] **Step 3: 实现 RoutePolicy。**

`route_policy.py` 提供纯函数：

```python
def route_question(question: str | QuestionIR, profile_id: str = "chinook-music") -> QuestionIR:
    """将问题转换为 QuestionIR，并只写入 task_type、route 和 requested_outputs；不调用工具。"""
```

必须把以下信号分开：

```text
数据库事实信号：多少、列出、排名、按月份、按国家、按媒体类型、没有成交显示0、订单去重
文档规则信号：怎么处理、口径、定义、规则、应该、规定、手册、方案、如何解释
跨源信号：实际+目标、实际+复盘、未达目标+活动、销售额+运营建议
```

`parsing.py` 只负责把旧解析结果适配为 QuestionIR；`task_policy.py` 只负责能力门禁和阻塞槽位；`graph.py` 只消费最终 route。

- [ ] **Step 4: 运行路由和已有 SQL 测试。**

Run:

```powershell
uv run python -m unittest services.agent_api.tests.test_route_policy services.agent_api.tests.test_agent_task_policy services.agent_api.tests.test_agent_ranking_seam -v
```

Expected: 新增测试全部通过，已有测试无回归。

- [ ] **Step 5: 跑三题定向离线评测。**

Run: `uv run python scripts/run_agent_eval.py --files eval_basic_nl2sql --mode offline --out tmp/nl2sql-route-check.json`

Expected: `basic-nl2sql-012/013/015` 的 route 为 `sql`；如果离线 C 后端不支持具体自由查询，只允许记录 SQL 能力边界，不能回退成 RAG。

- [ ] **Step 6: 提交。**

```bash
git add services/agent_api/app/agent/route_policy.py services/agent_api/tests/test_route_policy.py services/agent_api/app/agent/semantic_ir.py services/agent_api/app/agent/task_policy.py services/agent_api/app/agent/parsing.py services/agent_api/app/agent/graph.py services/agent_api/tests/test_agent_task_policy.py services/agent_api/tests/test_agent_ranking_seam.py
git commit -m "fix: prioritize database fact routing for complex nl2sql"
```

## Task 2：基础 RAG 权威来源计划和最小澄清

**目标：** 让基础 RAG 先确定事实角色和权威文档，再检索；避免规则问题误走 SQL，减少无必要澄清。

**Files:**

- Create: `services/agent_api/app/knowledge/source_plan.py`
- Create: `services/agent_api/tests/test_rag_source_plan.py`
- Modify: `services/agent_api/app/agent/task_plan.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/knowledge/index.py`
- Modify: `services/agent_api/app/agent/semantic_gate.py`
- Test: `services/agent_api/tests/test_all_documents_rag.py`
- Test: `services/agent_api/tests/test_document_answer.py`

- [ ] **Step 1: 写 RED 测试。**

```python
def test_default_media_scope_requires_d01_d03_d12():
    plan = rag_source_plan("默认音乐销售额包括哪些MediaType")
    assert plan.required_docs == ["D01", "D03", "D12"]
    assert "media_scope" in plan.required_roles


def test_activity_plan_requires_d10_and_d11():
    plan = rag_source_plan("第四季度Rock、Metal和Latin分别怎么安排")
    assert plan.required_docs == ["D10", "D11"]


def test_metric_method_question_does_not_require_period():
    decision = decide(QuestionIR.from_question("一个月销售额有没有增长，应该和什么时间段比较"))
    assert decision.status == "answerable"


def test_rule_question_with_order_media_uses_d12():
    plan = rag_source_plan("同一张订单同时有音频和视频，音乐销售额应该怎么处理")
    assert plan.required_docs == ["D12"]
```

- [ ] **Step 2: 运行 RED。**

Run: `uv run python -m unittest services.agent_api.tests.test_rag_source_plan -v`

Expected: 由于没有统一 source plan，至少一个断言失败。

- [ ] **Step 3: 建立事实角色到权威文档的映射。**

`source_plan.py` 固定提供：

```python
SOURCE_ROLES = {
    "metric_definition": ("D01", "D02"),
    "media_scope": ("D01", "D03", "D12"),
    "classification_boundary": ("D03",),
    "selection_rule": ("D05",),
    "activity_plan": ("D10", "D11"),
    "mixed_order_media_rule": ("D12",),
    "target_value": ("D06",),
    "quarter_review": ("D07", "D08"),
    "operation_rule": ("D09",),
}
```

`SourcePlan` 必须包含 `required_docs`、`required_roles`、`preferred_doc`、`query_terms` 和 `min_evidence_count`。`task_plan.retrieval_specs` 只消费 SourcePlan，不再在多个函数里重复判断 D01/D03/D08。

- [ ] **Step 4: 调整 RAG 检索预算。**

每个 required doc 至少分配一个检索槽位；有表格或 OCR 证据时增加一个邻接槽位；重复 chunk 在送入模型前去重。文档不足时返回缺少的角色，不把其它文档片段当成完整答案。

- [ ] **Step 5: 调整语义 Gate。**

以下问题直接进入 RAG：

- “一个月销售额有没有增长，应该和什么时间段比较”；
- “客单价按销售额除以销量还是订单数”；
- “同一张订单有音频和视频时应该怎么处理”。

只有没有 period、scope 或 target_metric 且无法从 profile 默认口径补全时才澄清。

- [ ] **Step 6: 运行 RAG 定向和回归测试。**

Run:

```powershell
uv run python -m unittest services.agent_api.tests.test_rag_source_plan services.agent_api.tests.test_all_documents_rag services.agent_api.tests.test_document_answer -v
uv run python scripts/run_agent_eval.py --files eval_basic_rag --mode offline --out tmp/basic-rag-source-plan-check.json
```

Expected: 基础 RAG 至少 13/15；失败项必须是明确的页码或文档快照问题，不能再出现 route 错误和无依据澄清。

- [ ] **Step 7: 提交。**

```bash
git add services/agent_api/app/knowledge/source_plan.py services/agent_api/tests/test_rag_source_plan.py services/agent_api/app/agent/task_plan.py services/agent_api/app/agent/graph.py services/agent_api/app/knowledge/index.py services/agent_api/app/agent/semantic_gate.py services/agent_api/tests/test_all_documents_rag.py services/agent_api/tests/test_document_answer.py
git commit -m "feat: add authoritative rag source planning"
```

## Task 3：跨源事实装配和 FormulaRegistry 接入

**目标：** 把 SQL actual、D06 target、D01/D02 formula、D07/D08 review 和 D09/D10/D11 operation 装配成可验证的 `CalculationEvidenceBundle`，使 cross_source 真正能计算和解释。

**Files:**

- Create: `services/agent_api/app/evidence/cross_source_bundle.py`
- Create: `services/agent_api/tests/test_cross_source_bundle.py`
- Modify: `services/agent_api/app/integrations/business_calculation.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/evidence/binding.py`
- Modify: `services/agent_api/app/response/composer.py`
- Test: `services/agent_api/tests/test_cross_source_calculation.py`
- Test: `services/agent_api/tests/test_agent_cross_completion.py`

- [ ] **Step 1: 写 RED 测试。**

```python
def test_q3_rock_bundle_contains_sql_actual_d06_target_and_formula():
    bundle = build_cross_source_bundle(
        task=q3_rock_target_task(),
        sql=fixture_q3_rock_actual(),
        documents=fixture_d06_target_and_d01_formula(),
    )
    assert bundle.function == "attainment_rate"
    assert set(bundle.operands) == {"actual", "target"}
    assert bundle.operands["actual"].source_ref.startswith("sql:")
    assert bundle.operands["target"].source_ref.startswith("D06:")
    assert bundle.formula.locator.doc_id == "D01"


def test_q3_metal_bundle_keeps_operation_evidence_separate():
    result = build_cross_source_result(
        task=q3_metal_activity_task(),
        sql=fixture_q3_metal_actual(),
        documents=fixture_d06_target_d08_review_d10_activity(),
    )
    assert result.actual == Decimal("21.78")
    assert result.target == Decimal("24.00")
    assert result.gap == Decimal("-2.22")
    assert result.operation_text_contains("附属")


def test_missing_operation_does_not_delete_actual_target_calculation():
    result = build_cross_source_result(
        task=q3_metal_activity_task(),
        sql=fixture_q3_metal_actual(),
        documents=fixture_d06_target_only(),
    )
    assert result.calculation.result == Decimal("-2.22")
    assert result.missing_roles == ["operation"]
```

- [ ] **Step 2: 运行 RED。**

Run: `uv run python -m unittest services.agent_api.tests.test_cross_source_bundle -v`

Expected: FAIL because current business resolver has no role-based bundle builder。

- [ ] **Step 3: 实现 bundle builder。**

`cross_source_bundle.py` 只允许以下来源角色：

```text
actual    SQL scalar evidence
target    D06 document scalar evidence
formula   D01/D02 formula evidence
review    D07/D08 contextual evidence
operation D09/D10/D11 contextual evidence
```

构建顺序：

```text
SQL rows → actual
D06 structured/table records → target
D01/D02 formula records → formula
D07/D08 → review/conflict evidence
D09/D10/D11 → operation evidence
```

`review` 和 `operation` 可以参与最终答案和冲突解释，但不能未经授权直接变成 calculator operand。只有 `actual/target/current/previous` 能进入公式绑定。

- [ ] **Step 4: 接入现有 business resolver。**

`create_business_resolver` 先调用 `build_cross_source_bundle`，再调用现有 `authorize_calculation`。失败时返回缺失角色列表，不能把已有 actual/target 清空。FormulaRegistry 只负责选择函数和执行确定性算术，证据授权仍由 `binding.py` 负责。

- [ ] **Step 5: 接入跨源答案。**

`composer._cross_answer` 必须展示：

```text
actual
target
gap 或 attainment_rate
verdict
operation/review explanation（若问题要求）
SQL source
document source
formula source
```

缺少 operation 时保留计算结果，并在 limitations 中列出 `missing_roles=["operation"]`；缺少 actual 或 target 时不能生成达标结论。

- [ ] **Step 6: 运行跨源专项。**

Run:

```powershell
uv run python -m unittest services.agent_api.tests.test_cross_source_bundle services.agent_api.tests.test_cross_source_calculation services.agent_api.tests.test_agent_cross_completion -v
uv run python scripts/run_agent_eval.py --files eval_cross_source,eval_special_formula --mode offline --out tmp/cross-formula-bundle-check.json
```

Expected:

```text
cross_source >= 8/10
formula >= 5/6
```

- [ ] **Step 7: 提交。**

```bash
git add services/agent_api/app/evidence/cross_source_bundle.py services/agent_api/tests/test_cross_source_bundle.py services/agent_api/app/integrations/business_calculation.py services/agent_api/app/agent/graph.py services/agent_api/app/evidence/binding.py services/agent_api/app/response/composer.py services/agent_api/tests/test_cross_source_calculation.py services/agent_api/tests/test_agent_cross_completion.py
git commit -m "feat: assemble cross-source evidence bundles"
```

## Task 4：三项专项验收与 Live 复核

**目标：** 证明三项重点能力共同提升，并确认没有破坏既有 SQL、多表和答案契约。

**Files:**

- Modify: `scripts/run_four_task_scorecard.py`
- Test: `services/agent_api/tests/test_four_task_scorecard.py`

- [ ] **Step 1: 运行定向离线验收。**

```powershell
uv run python scripts/run_agent_eval.py --files eval_basic_nl2sql --mode offline --out tmp/nl2sql-final.json
uv run python scripts/run_agent_eval.py --files eval_basic_rag --mode offline --out tmp/rag-final.json
uv run python scripts/run_agent_eval.py --files eval_cross_source,eval_special_formula --mode offline --out tmp/cross-formula-final.json
```

- [ ] **Step 2: 运行既有回归测试。**

```powershell
uv run python -m unittest services.agent_api.tests.test_agent_task_policy services.agent_api.tests.test_agent_ranking_seam services.agent_api.tests.test_all_documents_rag services.agent_api.tests.test_document_answer services.agent_api.tests.test_answer_contract -v
```

Expected: 无新增失败；允许单独记录需要外部 LLM 配置的旧集成测试。

- [ ] **Step 3: 运行完整离线 107 题。**

```powershell
uv run python scripts/run_agent_eval.py --all --mode offline --out tmp/nl2sql-rag-cross-full-offline.json
uv run python scripts/run_four_task_scorecard.py --report tmp/nl2sql-rag-cross-full-offline.json --out tmp/nl2sql-rag-cross-full-scorecard.json
```

Expected: 总业务通过率达到至少 80%；如果离线后端不支持自由 SQL，必须将失败归因标记为 backend capability，而不能把路由失败伪装成 RAG 通过。

- [ ] **Step 4: 运行 Live 全量。**

```powershell
uv run python scripts/run_agent_eval.py --all --mode live --out tmp/chinook-eval-live-final/report.json
uv run python scripts/run_four_task_scorecard.py --report tmp/chinook-eval-live-final/report.json --out tmp/chinook-eval-live-final/scorecard.json
```

Expected thresholds:

```text
basic_nl2sql >= 15/15
basic_rag >= 13/15
cross_source >= 8/10
formula >= 5/6
overall >= 86/107
```

- [ ] **Step 5: 提交验收记录。**

```bash
git status --short
git log -12 --oneline --decorate
git diff --check
```

验收记录只写入 `docs/`，不把 `tmp/` 运行产物提交到 Git。

## 完成判定

- [ ] 三项 route 错误归零：基础 NL2SQL 3 题、基础 RAG 1 题、跨源 2 题。
- [ ] 基础 RAG 不再因缺少权威来源而生成无来源完整结论。
- [ ] 跨源结果包含 actual、target、gap/attainment、公式和来源。
- [ ] FormulaRegistry 已由实际 resolver 调用，而不只是独立单元测试。
- [ ] 基础 NL2SQL、基础 RAG、跨源分别达到门槛。
- [ ] 总分达到至少 86/107。
- [ ] 工作区干净，当前分支可回退到 `1d60f1e`。
