# 四项中级任务统一优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持基础 NL2SQL 和已有多表能力不回归的前提下，围绕跨源问答、要素精准识别与问题改写、文档公式识别、语义检验与澄清四项任务，把 Chinook 107 条评测的稳定业务通过率提升到至少 86/107。

**Architecture:** 先建立统一的 QuestionIR、实体链接和问题改写层，再由 Semantic Gate 决定回答、澄清或拒答；跨源任务统一生成 DataPlan，通过 EvidenceLedger 绑定 SQL、文档和公式证据；公式使用 FormulaSpec 和确定性计算器；最终由 AnswerContract 校验输出。所有阶段都通过现有单元测试和专项评测门槛后再进入下一阶段。

**Tech Stack:** Python 3.12、Pydantic v2、LangGraph、FastAPI、SQLAlchemy、sqlglot、unittest、现有 Chinook profile 与 D01-D12 知识库。

---

## 执行约束

- 从 `checkpoint/pre-four-task-optimization-20261012` 对应的提交 `1d60f1e` 开始执行。
- 本计划只处理四项重点任务及其必要支撑，不扩展 AdventureWorks、其它中级任务、高级任务或外部 API 稳定性。
- 每个任务完成后单独提交，提交前运行该任务列出的测试。
- 任何阶段发现基础 NL2SQL 或已通过的多表测试回归，暂停后续阶段并回退到上一个阶段提交。
- 不修改评测器金标准来换取分数；只修改运行时行为和必要的可观察性工具。
- 使用离线/受控评测作为阶段门槛；live 结果只作为最终人工复核，不把外部 API 失败计入实现判断。

## 文件边界

第一阶段前先按以下职责建立边界。已有大文件保留兼容入口，逐步把业务判断移入新模块。

**新增文件**

- `services/agent_api/app/agent/semantic_ir.py`：QuestionIR、EntityMention、字段来源和任务输出契约。
- `services/agent_api/app/agent/entity_linking.py`：profile 驱动的别名、实体候选、边界和排除条件。
- `services/agent_api/app/agent/query_rewrite.py`：单轮规范化和多轮增量改写。
- `services/agent_api/app/agent/semantic_gate.py`：回答、澄清、拒答三态决策。
- `services/agent_api/app/evidence/ledger.py`：EvidenceLedger 和 EvidenceItem。
- `services/agent_api/app/evidence/verification.py`：周期、指标、实体、范围和单位检查。
- `services/agent_api/app/evidence/formulas.py`：FormulaSpec、公式注册表和参数绑定。
- `profiles/chinook/entities.json`：Chinook 的别名、实体 ID、边界和排除规则。
- `profiles/chinook/formulas.json`：D01/D02/D06/D07/D08 的公式和输入说明。
- `scripts/run_four_task_scorecard.py`：四项重点能力和总分的离线统计工具。

**修改文件**

- `services/agent_api/app/agent/state.py`：在保持现有 `BusinessTask` 兼容的前提下保存 IR、字段来源和证据账本。
- `services/agent_api/app/agent/parsing.py`：调用规范化改写与实体链接，删除重复的局部猜测逻辑。
- `services/agent_api/app/agent/task_policy.py`：使用 Semantic Gate 的阻塞槽位和默认口径。
- `services/agent_api/app/agent/turn_update.py`：实现 QuestionIR 的增量继承和冲突处理。
- `services/agent_api/app/agent/task_plan.py`：从 QuestionIR 生成按事实角色划分的 DataPlan。
- `services/agent_api/app/agent/graph.py`：编排新阶段，保留现有 SQL 工具调用和状态转换接口。
- `services/agent_api/app/response/composer.py`：从 AnswerContract 生成答案，并在证据合并前去重。
- `services/agent_api/app/knowledge/index.py`：支持 profile 指定的权威文档和来源定位。
- `services/agent_api/app/response/models.py`：补充结构化答案和诊断字段时保持 API 向后兼容。

**测试文件**

- 新增 `services/agent_api/tests/test_semantic_ir.py`。
- 新增 `services/agent_api/tests/test_entity_linking.py`。
- 新增 `services/agent_api/tests/test_query_rewrite.py`。
- 新增 `services/agent_api/tests/test_evidence_ledger.py`。
- 新增 `services/agent_api/tests/test_formula_binding.py`。
- 新增 `services/agent_api/tests/test_four_task_scorecard.py`。
- 扩展 `test_agent_task_policy.py`、`test_agent_session_updates.py`、`test_clarification_v3_integration.py`、`test_agent_cross_plan.py`、`test_agent_cross_completion.py`、`test_cross_source_calculation.py`、`test_document_answer.py`、`test_all_documents_rag.py`。

## Task 0: 建立离线基线和专项计分工具

**目标：** 让每个阶段都能看到四项任务的专项变化，并将 API 失败和业务失败分开。

**Files:**
- Create: `scripts/run_four_task_scorecard.py`
- Create: `services/agent_api/tests/test_four_task_scorecard.py`
- Test: `tmp/chinook-eval-20261012-2157/report.json` 或后续离线报告

- [ ] **Step 1: 写失败测试，验证题目归类和报告统计。**

```python
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_four_task_scorecard import classify_suite, score_report


class FourTaskScorecardTests(unittest.TestCase):
    def test_classify_focus_suites(self):
        self.assertEqual(classify_suite("special-alias-001"), "entity_rewrite")
        self.assertEqual(classify_suite("special-formula-001"), "formula")
        self.assertEqual(classify_suite("cross-001"), "cross_source")
        self.assertEqual(classify_suite("special-clarify-001"), "clarification")
        self.assertEqual(classify_suite("session-001"), "clarification")

    def test_score_report_excludes_external_pending_from_business_result(self):
        report = {"total": 2, "results": [
            {"id": "cross-001", "business_passed": True, "pending_external_api": True, "problems": []},
            {"id": "special-formula-001", "business_passed": False, "pending_external_api": False, "problems": ["缺少公式"]},
        ]}
        result = score_report(report)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["business_passed"], 1)
        self.assertEqual(result["pending_external_api"], 1)
        self.assertEqual(result["by_suite"]["cross_source"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认工具尚未实现时失败。**

Run: `uv run python -m unittest services.agent_api.tests.test_four_task_scorecard -v`

Expected: FAIL with `ModuleNotFoundError` or missing `classify_suite`.

- [ ] **Step 3: 实现最小计分工具。**

`classify_suite` 只允许以下映射：

```python
def classify_suite(case_id: str) -> str:
    if case_id.startswith(("special-alias-",)):
        return "entity_rewrite"
    if case_id.startswith(("special-formula-",)):
        return "formula"
    if case_id.startswith("cross-"):
        return "cross_source"
    if case_id.startswith(("special-clarify-", "session-")):
        return "clarification"
    return "regression"
```

`score_report` 必须输出 `total`、`business_passed`、`pending_external_api`、`by_suite` 和 `failures_by_reason`，不能把 `pending_external_api` 当成业务失败。

- [ ] **Step 4: 运行专项统计测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_four_task_scorecard -v`

Expected: all tests PASS。

- [ ] **Step 5: 用现有报告建立基线并提交。**

Run: `uv run python scripts/run_four_task_scorecard.py --report tmp/chinook-eval-20261012-2157/report.json --out tmp/four-task-baseline.json`

Expected: 输出四项专项分数、业务通过数和待复测数。

Commit:

```bash
git add scripts/run_four_task_scorecard.py services/agent_api/tests/test_four_task_scorecard.py
git commit -m "test: add four-task offline scorecard"
```

## Task 1: QuestionIR、实体链接和问题改写

**目标：** 让 SQL、RAG、跨源和公式模块共享同一份指标、时间、范围、实体、排除条件和输出要求。

**Files:**
- Create: `services/agent_api/app/agent/semantic_ir.py`
- Create: `services/agent_api/app/agent/entity_linking.py`
- Create: `services/agent_api/app/agent/query_rewrite.py`
- Create: `profiles/chinook/entities.json`
- Create: `services/agent_api/tests/test_semantic_ir.py`
- Create: `services/agent_api/tests/test_entity_linking.py`
- Create: `services/agent_api/tests/test_query_rewrite.py`
- Modify: `services/agent_api/app/agent/state.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `profiles/chinook/profile.json`

- [ ] **Step 1: 写 QuestionIR 和实体链接失败测试。**

```python
import unittest

from services.agent_api.app.agent.entity_linking import link_entities
from services.agent_api.app.agent.semantic_ir import QuestionIR


class EntityLinkingTests(unittest.TestCase):
    def test_rock_and_rock_and_roll_are_separate(self):
        result = link_entities("Rock，不包括 Rock And Roll", profile_id="chinook-music")
        self.assertEqual([(item.entity_type, item.entity_id) for item in result.entities], [("genre", 1)])
        self.assertEqual(result.exclusions, [("genre", 5)])
        self.assertEqual(result.entities[0].canonical_name, "Rock")

    def test_metal_aliases_keep_distinct_ids(self):
        result = link_entities("Metal 和 Heavy Metal 分开统计", profile_id="chinook-music")
        self.assertEqual([item.entity_id for item in result.entities], [3, 13])
        self.assertEqual(result.boundary_status, "separate")


class QuestionIRTests(unittest.TestCase):
    def test_ir_roundtrip_preserves_provenance(self):
        ir = QuestionIR.from_question("2025年第三季度 Rock 销售额")
        restored = QuestionIR.model_validate(ir.model_dump(mode="python"))
        self.assertEqual(restored.time_scope.quarter, 3)
        self.assertEqual(restored.entity_scope[0].entity_id, 1)
        self.assertEqual(restored.provenance["time_scope"].status, "confirmed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_semantic_ir services.agent_api.tests.test_entity_linking -v`

Expected: FAIL because the new models and profile catalog do not exist。

- [ ] **Step 3: 建立最小结构化模型和 profile 词典。**

`semantic_ir.py` 至少定义以下 Pydantic 模型：

```python
class FieldProvenance(BaseModel):
    source: Literal["user", "profile_default", "inferred", "session", "document"]
    status: Literal["confirmed", "defaulted", "inferred", "unknown", "conflict"]
    evidence: list[str] = Field(default_factory=list)


class EntityMention(BaseModel):
    surface: str
    entity_type: str
    canonical_name: str
    entity_id: int | str
    included: bool = True
    boundary_status: Literal["confirmed", "ambiguous", "separate"] = "confirmed"
    provenance: FieldProvenance


class TimeScope(BaseModel):
    year: int | None = None
    quarter: int | None = None
    month: int | None = None
    start: str | None = None
    end: str | None = None
    label: str | None = None


class QuestionIR(BaseModel):
    original_question: str
    rewritten_question: str
    task_type: str
    metrics: list[str] = Field(default_factory=list)
    time_scope: TimeScope = Field(default_factory=TimeScope)
    entity_scope: list[EntityMention] = Field(default_factory=list)
    exclusions: list[EntityMention] = Field(default_factory=list)
    media_scope: list[int] = Field(default_factory=list)
    comparison_context: dict[str, object] = Field(default_factory=dict)
    requested_outputs: list[str] = Field(default_factory=list)
    clarification_blockers: list[str] = Field(default_factory=list)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)

    @classmethod
    def from_question(cls, question: str, profile_id: str = "chinook-music") -> "QuestionIR":
        """调用 profile 解析器生成可序列化的初始 IR；不执行 SQL、检索或计算。"""
        return cls(original_question=question, rewritten_question=question, task_type="unknown")
```

`profiles/chinook/entities.json` 必须至少登记：

```json
{
  "genres": [
    {"id": 1, "name": "Rock", "aliases": ["摇滚", "摇滚类"]},
    {"id": 5, "name": "Rock And Roll", "aliases": ["摇滚乐"]},
    {"id": 3, "name": "Metal", "aliases": ["金属", "金属类"]},
    {"id": 13, "name": "Heavy Metal", "aliases": ["重金属"]}
  ],
  "media_scope": {"audio": [1, 2, 4, 5], "video": [3], "all_store": [1, 2, 3, 4, 5]}
}
```

`entity_linking.py` 必须提供以下稳定接口：

```python
class EntityLinkResult(BaseModel):
    entities: list[EntityMention] = Field(default_factory=list)
    exclusions: list[tuple[str, int | str]] = Field(default_factory=list)
    boundary_status: Literal["confirmed", "ambiguous", "separate"] = "confirmed"


def link_entities(question: str, profile_id: str = "chinook-music") -> EntityLinkResult:
    """只做实体识别和边界判断，不执行 SQL 或检索。"""
    return EntityLinkResult()
```

- [ ] **Step 4: 将现有解析结果适配为 QuestionIR。**

在 `parsing.py` 保留现有 `BusinessTask` 字段，新增 `task.question_ir` 或等价状态字段；旧字段由 IR 适配填充，避免一次性改动所有 graph 节点。解析顺序固定为：

```text
清理原问题 → 提取显式条件 → 链接实体 → 推断任务关系 → 生成规范问题 → 填充旧 BusinessTask
```

不得在 `parsing.py`、`task_policy.py` 和 `task_plan.py` 分别重复解释 Rock、Metal、时间或媒体范围。

- [ ] **Step 5: 运行 IR、实体和既有解析测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_semantic_ir services.agent_api.tests.test_entity_linking services.agent_api.tests.test_query_rewrite services.agent_api.tests.test_agent_task_policy -v`

Expected: new tests PASS，既有 task policy tests 无回归。

- [ ] **Step 6: 提交阶段一。**

```bash
git add services/agent_api/app/agent/semantic_ir.py services/agent_api/app/agent/entity_linking.py services/agent_api/app/agent/query_rewrite.py profiles/chinook/entities.json profiles/chinook/profile.json services/agent_api/app/agent/state.py services/agent_api/app/agent/parsing.py services/agent_api/tests/test_semantic_ir.py services/agent_api/tests/test_entity_linking.py services/agent_api/tests/test_query_rewrite.py
git commit -m "feat: add question ir and profile driven entity linking"
```

## Task 2: Semantic Gate、精准澄清和多轮继承

**目标：** 可回答问题直接进入取证；只有阻塞条件缺失时澄清；多轮只增量更新缺失字段。

**Files:**
- Create: `services/agent_api/app/agent/semantic_gate.py`
- Modify: `services/agent_api/app/agent/task_policy.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `services/agent_api/app/agent/turn_update.py`
- Modify: `services/agent_api/app/agent/state.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Test: `services/agent_api/tests/test_agent_task_policy.py`
- Test: `services/agent_api/tests/test_agent_session_updates.py`
- Test: `services/agent_api/tests/test_clarification_v3_integration.py`

- [ ] **Step 1: 写三态决策失败测试。**

```python
import unittest

from services.agent_api.app.agent.semantic_gate import decide
from services.agent_api.app.agent.semantic_ir import QuestionIR


class SemanticGateTests(unittest.TestCase):
    def test_document_metric_definition_is_answerable_without_period(self):
        ir = QuestionIR.from_question("客单价按销售额除以销量还是订单数？")
        decision = decide(ir)
        self.assertEqual(decision.status, "answerable")
        self.assertEqual(decision.blockers, [])

    def test_bare_target_question_asks_only_required_slots(self):
        ir = QuestionIR.from_question("销售额达到目标了吗？")
        decision = decide(ir)
        self.assertEqual(decision.status, "clarification_required")
        self.assertEqual(decision.blockers, ["period", "scope", "target_metric"])

    def test_profit_question_is_unsupported(self):
        ir = QuestionIR.from_question("第三季度利润是多少？")
        decision = decide(ir)
        self.assertEqual(decision.status, "unsupported")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_agent_task_policy services.agent_api.tests.test_clarification_v3_integration -v`

Expected: 新增断言至少有一项 FAIL，暴露当前过度澄清或槽位缺失。

- [ ] **Step 3: 实现 Semantic Gate。**

`semantic_gate.py` 提供一个纯函数接口。`profile` 可省略，省略时加载 `chinook-music` profile，便于单元测试；生产调用必须显式传入当前 profile：

```python
class GateDecision(BaseModel):
    status: Literal["answerable", "clarification_required", "unsupported"]
    blockers: list[str] = Field(default_factory=list)
    reason: str = ""


def decide(task: QuestionIR, profile: BusinessProfile | None = None) -> GateDecision:
    return GateDecision(status="answerable", blockers=[], reason="")
```

判定顺序必须是：数据能力拒答 → 冲突条件 → 任务类型 → 阻塞槽位 → profile 默认口径 → answerable。RAG 规则问题不能因为缺少数据库周期而澄清；目标比较问题必须单独检查 `target_metric`。

- [ ] **Step 4: 将多轮状态改成增量合并。**

在 `turn_update.py` 增加：

```python
def merge_question_ir(previous: QuestionIR | None, current: QuestionIR) -> QuestionIR:
    """保留已确认字段，只用当前轮显式条件覆盖同名字段；冲突字段进入 conflict。"""
```

以下输入必须通过：

```text
第三季度 Rock 销售额是多少？
→ 和目标比呢？
→ 那下一步重点看什么？
```

第二轮必须继承年份、季度、Genre:1 和销售额指标；第三轮必须继承同一范围并转为运营解释任务。

- [ ] **Step 5: 运行澄清和多轮测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_agent_task_policy services.agent_api.tests.test_agent_session_updates services.agent_api.tests.test_clarification_v3_integration -v`

Expected: 专项澄清槽位测试全部 PASS，多轮继承相关测试 PASS。

- [ ] **Step 6: 提交阶段二。**

```bash
git add services/agent_api/app/agent/semantic_gate.py services/agent_api/app/agent/task_policy.py services/agent_api/app/agent/parsing.py services/agent_api/app/agent/turn_update.py services/agent_api/app/agent/state.py services/agent_api/app/agent/graph.py services/agent_api/tests/test_agent_task_policy.py services/agent_api/tests/test_agent_session_updates.py services/agent_api/tests/test_clarification_v3_integration.py
git commit -m "feat: add semantic gate and incremental clarification state"
```

## Task 3: DataPlan、EvidenceLedger 和跨源绑定

**目标：** 将实际值、目标值、公式和运营规则按事实角色取证，并检查同一指标、周期、实体、媒体范围和单位。

**Files:**
- Create: `services/agent_api/app/evidence/ledger.py`
- Create: `services/agent_api/app/evidence/verification.py`
- Modify: `services/agent_api/app/agent/task_plan.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/response/composer.py`
- Modify: `services/agent_api/app/knowledge/index.py`
- Test: `services/agent_api/tests/test_agent_cross_plan.py`
- Test: `services/agent_api/tests/test_agent_cross_completion.py`
- Test: `services/agent_api/tests/test_cross_source_calculation.py`
- Test: `services/agent_api/tests/test_evidence_ledger.py`

- [ ] **Step 1: 写 EvidenceLedger 和跨源角色测试。**

```python
import unittest

from services.agent_api.app.evidence.ledger import EvidenceItem, EvidenceLedger
from services.agent_api.app.evidence.verification import verify_same_scope


class EvidenceLedgerTests(unittest.TestCase):
    def test_actual_and_target_bind_only_when_scope_matches(self):
        ledger = EvidenceLedger(items=[
            EvidenceItem(fact_role="actual", metric="sales_amount", value=21.78, unit="USD", period="2025 Q3", entity_scope=[3], media_scope=[1, 2, 4, 5], source_ref="sql-1", source_kind="sql"),
            EvidenceItem(fact_role="target", metric="sales_amount", value=24.0, unit="USD", period="2025 Q3", entity_scope=[3], media_scope=[1, 2, 4, 5], source_ref="D06#P2", source_kind="document"),
        ])
        self.assertTrue(verify_same_scope(ledger.required("actual"), ledger.required("target")))

    def test_mismatched_period_is_rejected(self):
        actual = EvidenceItem(fact_role="actual", metric="sales_amount", value=21.78, unit="USD", period="2025 Q3", entity_scope=[3], media_scope=[1, 2, 4, 5], source_ref="sql-1", source_kind="sql")
        target = EvidenceItem(fact_role="target", metric="sales_amount", value=24.0, unit="USD", period="2025 Q2", entity_scope=[3], media_scope=[1, 2, 4, 5], source_ref="D06#P2", source_kind="document")
        self.assertFalse(verify_same_scope(actual, target))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_evidence_ledger services.agent_api.tests.test_agent_cross_plan -v`

Expected: FAIL because ledger and role-based plan do not exist。

- [ ] **Step 3: 实现 DataPlan 和 EvidenceLedger。**

`ledger.py` 必须提供：

```python
class EvidenceItem(BaseModel):
    fact_role: Literal["actual", "target", "formula", "review", "operation"]
    metric: str
    value: object | None = None
    unit: str | None = None
    period: str | None = None
    entity_scope: list[int | str] = Field(default_factory=list)
    media_scope: list[int] = Field(default_factory=list)
    source_ref: str
    source_kind: Literal["sql", "document", "formula"]
    validation_status: Literal["unverified", "verified", "conflict"] = "unverified"


class EvidenceLedger(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)

    def required(self, role: str) -> EvidenceItem:
        matches = [item for item in self.items if item.fact_role == role and item.validation_status == "verified"]
        if len(matches) != 1:
            raise ValueError(f"expected one verified {role}, got {len(matches)}")
        return matches[0]
```

`task_plan.py` 按 `fact_role` 生成来源计划：目标值使用 D06，公式使用 D01/D02，季度实际或复盘事实使用 D08，运营动作使用 D09/D10/D11；不得再按 intent 只选择 D01 或 D06 后排除其它必要来源。

- [ ] **Step 4: 把跨源编排改为逐角色完成。**

`graph.py` 的跨源流程必须按以下顺序保存中间结果：

```text
QuestionIR → DataPlan → SQL/RAG 子任务 → EvidenceLedger → verify_same_scope → 结论
```

一个角色失败时保留其它已验证角色，并返回缺失角色；只有结论所需的全部角色齐全时才执行计算和达标判断。

- [ ] **Step 5: 在答案合成前去重并保留来源定位。**

按 `(profile_id, retrieval_id, chunk_id)` 去重 `rag.chunks`，再传给 composer。页码缺失时从已有 chunk 锚点回填；定位不一致只标记 locator 失败，不清空已经通过内容和身份校验的事实。

- [ ] **Step 6: 运行跨源测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_evidence_ledger services.agent_api.tests.test_agent_cross_plan services.agent_api.tests.test_agent_cross_completion services.agent_api.tests.test_cross_source_calculation services.agent_api.tests.test_stage2_cross_workflows -v`

Expected: 所有跨源计划、证据角色、部分证据和范围冲突测试 PASS。

- [ ] **Step 7: 提交阶段三。**

```bash
git add services/agent_api/app/evidence/ledger.py services/agent_api/app/evidence/verification.py services/agent_api/app/agent/task_plan.py services/agent_api/app/agent/graph.py services/agent_api/app/response/composer.py services/agent_api/app/knowledge/index.py services/agent_api/tests/test_evidence_ledger.py services/agent_api/tests/test_agent_cross_plan.py services/agent_api/tests/test_agent_cross_completion.py services/agent_api/tests/test_cross_source_calculation.py
git commit -m "feat: bind cross-source evidence by fact role and scope"
```

## Task 4: 文档公式注册、参数绑定和确定性计算

**目标：** 将公式从临时提示词和不稳定片段中提取为结构化注册表，在线只做公式选择和参数绑定。

**Files:**
- Create: `services/agent_api/app/evidence/formulas.py`
- Create: `profiles/chinook/formulas.json`
- Create: `services/agent_api/tests/test_formula_binding.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/agent/task_plan.py`
- Modify: `services/agent_api/app/response/composer.py`
- Modify: `services/agent_api/app/evidence/binding.py`
- Test: `services/agent_api/tests/test_cross_source_calculation.py`

- [ ] **Step 1: 写公式解析和范围校验失败测试。**

```python
import unittest

from services.agent_api.app.evidence.formulas import FormulaRegistry, bind_formula


class FormulaBindingTests(unittest.TestCase):
    def test_target_difference_requires_same_scope(self):
        registry = FormulaRegistry.from_profile("profiles/chinook/formulas.json")
        request = bind_formula(
            registry,
            function="target_difference",
            actual={"metric": "sales_amount", "value": 21.78, "period": "2025 Q3", "scope": [3], "unit": "USD", "source_ref": "sql-1"},
            target={"metric": "sales_amount", "value": 24.0, "period": "2025 Q3", "scope": [3], "unit": "USD", "source_ref": "D06#P2"},
        )
        self.assertEqual(request.function, "target_difference")
        self.assertEqual(request.operands["actual"].value, 21.78)

    def test_formula_rejects_mismatched_period(self):
        registry = FormulaRegistry.from_profile("profiles/chinook/formulas.json")
        with self.assertRaises(ValueError):
            bind_formula(
                registry,
                function="target_difference",
                actual={"metric": "sales_amount", "value": 21.78, "period": "2025 Q3", "scope": [3], "unit": "USD", "source_ref": "sql-1"},
                target={"metric": "sales_amount", "value": 24.0, "period": "2025 Q2", "scope": [3], "unit": "USD", "source_ref": "D06#P2"},
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_formula_binding -v`

Expected: FAIL because FormulaRegistry and bind_formula do not exist。

- [ ] **Step 3: 建立公式注册表。**

`profiles/chinook/formulas.json` 至少包含：

```json
{
  "formulas": [
    {"function": "target_difference", "expression": "actual - target", "inputs": ["actual", "target"], "unit": "USD", "source_refs": ["D01", "D06"]},
    {"function": "attainment_rate", "expression": "actual / target * 100", "inputs": ["actual", "target"], "unit": "%", "source_refs": ["D01", "D06"]},
    {"function": "growth_rate", "expression": "(current - previous) / previous * 100", "inputs": ["current", "previous"], "unit": "%", "source_refs": ["D01", "D02", "D07", "D08"]},
    {"function": "average_order_value", "expression": "sales_amount / order_count", "inputs": ["sales_amount", "order_count"], "unit": "USD", "source_refs": ["D01", "D02"]},
    {"function": "monthly_sum", "expression": "sum(monthly_plan)", "inputs": ["monthly_plan"], "unit": "USD", "source_refs": ["D06"]}
  ]
}
```

`formulas.py` 必须提供 `FormulaRegistry.from_profile`、`bind_formula` 和 `evaluate_formula`。`evaluate_formula` 只能接受已经通过 scope、unit、metric 和 period 校验的 operands，不接受模型自由生成的算式字符串。

接口固定为：

```python
class Operand(BaseModel):
    metric: str
    value: Decimal
    period: str
    scope: list[int | str]
    unit: str
    source_ref: str


class FormulaRequest(BaseModel):
    function: str
    operands: dict[str, Operand]


def bind_formula(registry: FormulaRegistry, function: str, actual: dict, target: dict | None = None, current: dict | None = None, previous: dict | None = None) -> FormulaRequest:
    values = {"actual": actual}
    if target is not None:
        values["target"] = target
    if current is not None:
        values["current"] = current
    if previous is not None:
        values["previous"] = previous
    operands = {name: Operand.model_validate(value) for name, value in values.items()}
    periods = {item.period for item in operands.values()}
    scopes = {tuple(item.scope) for item in operands.values()}
    units = {item.unit for item in operands.values()}
    if len(periods) != 1 or len(scopes) != 1 or len(units) != 1:
        raise ValueError("formula operands must share period, scope, and unit")
    return FormulaRequest(function=function, operands=operands)


def evaluate_formula(request: FormulaRequest) -> Decimal:
    values = {name: item.value for name, item in request.operands.items()}
    if request.function == "target_difference":
        return values["actual"] - values["target"]
    if request.function == "attainment_rate":
        if values["target"] == 0:
            raise ZeroDivisionError("target is zero")
        return values["actual"] / values["target"] * Decimal("100")
    if request.function == "growth_rate":
        if values["previous"] == 0:
            raise ZeroDivisionError("previous is zero")
        return (values["current"] - values["previous"]) / values["previous"] * Decimal("100")
    if request.function == "average_order_value":
        if values["order_count"] == 0:
            raise ZeroDivisionError("order_count is zero")
        return values["sales_amount"] / values["order_count"]
    raise ValueError(f"unsupported formula function: {request.function}")


def evaluate_monthly_sum(plans: list[Operand]) -> Decimal:
    return sum((item.value for item in plans), Decimal("0"))
```

月度计划先由文档表格解析为 `list[Operand]`，再调用 `evaluate_monthly_sum`；所有函数都只接受通过周期、范围、单位和来源校验的参数，模型输出不能直接作为公式表达式执行。

- [ ] **Step 4: 接入 graph 的计算节点。**

`graph.py` 中的计算路径必须改为：

```text
QuestionIR.required_outputs
→ FormulaRegistry 选择函数
→ EvidenceLedger 绑定参数
→ bind_formula 校验
→ runtime calculator 执行
→ AnswerContract 写入公式和结果
```

参数绑定失败时返回具体缺口，例如 `target@sales_amount@2025 Q3@Genre:3`，不能直接抛出未捕获异常或把整轮已有证据清空。

- [ ] **Step 5: 运行公式和跨源计算测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_formula_binding services.agent_api.tests.test_cross_source_calculation services.agent_api.tests.test_agent_cross_completion -v`

Expected: 公式结构、同范围绑定、除零和时间缺失测试 PASS。

- [ ] **Step 6: 提交阶段四。**

```bash
git add services/agent_api/app/evidence/formulas.py profiles/chinook/formulas.json services/agent_api/tests/test_formula_binding.py services/agent_api/app/agent/graph.py services/agent_api/app/agent/task_plan.py services/agent_api/app/response/composer.py services/agent_api/app/evidence/binding.py services/agent_api/tests/test_cross_source_calculation.py services/agent_api/tests/test_agent_cross_completion.py
git commit -m "feat: add scoped formula registry and deterministic binding"
```

## Task 5: AnswerContract、证据输出和四项专项验收

**目标：** 让答案内容、实体格式、公式结果和来源展示稳定满足评测契约，消除已确认的工程性失分。

**Files:**
- Modify: `services/agent_api/app/response/composer.py`
- Modify: `services/agent_api/app/response/models.py`
- Modify: `services/agent_api/app/knowledge/index.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `scripts/run_four_task_scorecard.py`
- Test: `services/agent_api/tests/test_document_answer.py`
- Test: `services/agent_api/tests/test_all_documents_rag.py`
- Test: `services/agent_api/tests/test_stage2_workflow_scoring.py`
- Test: `services/agent_api/tests/test_four_task_scorecard.py`

- [ ] **Step 1: 写答案契约失败测试。**

```python
import unittest

from services.agent_api.app.response.composer import render_answer_contract


class AnswerContractTests(unittest.TestCase):
    def test_cross_answer_contains_actual_target_gap_and_sources(self):
        answer = render_answer_contract({
            "task_type": "actual_vs_target",
            "facts": {
                "actual": {"value": 21.78, "unit": "USD", "source_ref": "sql-1"},
                "target": {"value": 24.0, "unit": "USD", "source_ref": "D06#P2"},
            },
            "calculations": [{"function": "target_difference", "result": -2.22, "unit": "USD"}],
            "entities": ["Genre:3"],
        })
        self.assertIn("21.78", answer)
        self.assertIn("24.0", answer)
        self.assertIn("-2.22", answer)
        self.assertIn("Genre:3", answer)

    def test_clarification_lists_only_blockers(self):
        answer = render_answer_contract({
            "task_type": "clarification",
            "clarification": {"missing_slots": ["period", "scope", "target_metric"]},
        })
        self.assertIn("period", answer)
        self.assertIn("scope", answer)
        self.assertIn("target_metric", answer)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试。**

Run: `uv run python -m unittest services.agent_api.tests.test_document_answer services.agent_api.tests.test_stage2_workflow_scoring -v`

Expected: 新增答案契约断言失败，暴露当前实体、来源或计算字段缺失。

- [ ] **Step 3: 实现最小答案契约检查。**

在 composer 生成最终文本前增加 `validate_answer_contract(payload)`，至少检查：

```text
actual_vs_target: actual + target + gap/attainment + verdict + SQL/document sources
formula: formula + operands + result + unit + period + scope
entity: canonical name + canonical ID
clarification: missing_slots + question
```

契约失败时返回结构化限制说明，保留已经验证的事实，不重新生成未经验证的数字。

- [ ] **Step 4: 修复来源合并和实体展示。**

在 composer 入口统一去重 chunk；所有实体 ID 使用 `Genre:<id>`、`MediaTypeId:<id>` 等规范形式；文档定位优先使用真实 source URI 和 chunk 锚点；页码缺失不能导致内容证据全部清空。

- [ ] **Step 5: 运行四项专项 scorecard。**

Run:

```powershell
uv run python scripts/run_agent_eval.py --files eval_special_alias_robustness,eval_special_clarification,eval_special_formula,eval_cross_source,eval_multiturn --mode offline --out tmp/four-task-final.json
uv run python scripts/run_four_task_scorecard.py --report tmp/four-task-final.json --out tmp/four-task-final-scorecard.json
uv run python scripts/run_agent_eval.py --all --mode offline --out tmp/four-task-full-final.json
uv run python scripts/run_four_task_scorecard.py --report tmp/four-task-full-final.json --out tmp/four-task-full-final-scorecard.json
```

Expected focused thresholds:

```text
cross_source >= 8/10
entity_rewrite >= 12/15
formula >= 5/6
clarification >= 8/8
multiturn >= 4/5
```

Expected overall threshold from `tmp/four-task-full-final-scorecard.json`:

```text
overall business_passed >= 86/107
```

- [ ] **Step 6: 运行完整本地测试和回归集。**

Run:

```powershell
uv sync --extra test
uv run python -m unittest discover -s services/agent_api/tests -t .
uv run python -m unittest discover -s eval -t .
```

Expected: 所有新增测试通过；基础 NL2SQL 和多表回归测试不低于优化前检查点。

- [ ] **Step 7: 提交阶段五。**

```bash
git add services/agent_api/app/response/composer.py services/agent_api/app/response/models.py services/agent_api/app/knowledge/index.py services/agent_api/app/agent/parsing.py scripts/run_four_task_scorecard.py services/agent_api/tests/test_document_answer.py services/agent_api/tests/test_all_documents_rag.py services/agent_api/tests/test_stage2_workflow_scoring.py services/agent_api/tests/test_four_task_scorecard.py
git commit -m "feat: enforce four-task answer contract and score gates"
```

## 最终交付检查

- [ ] `git status --short` 为空。
- [ ] 每个阶段都有独立提交，提交信息能对应本计划任务。
- [ ] `checkpoint/pre-four-task-optimization-20261012` 仍指向优化前代码。
- [ ] QuestionIR 能同时驱动 SQL、RAG、跨源和公式路径。
- [ ] 四项专项阈值满足设计目标。
- [ ] 基础 NL2SQL、多表查询和现有 API contract 测试无回归。
- [ ] `tmp/four-task-final-scorecard.json` 保存逐题、分组和失败原因。
- [ ] 只在所有阶段通过后，才考虑进行一次外部 API live 复核；live 失败不改变本地验收结论。
