# Agent 鲁棒性、多文档、多表关联与多轮上下文优化实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 在当前可回退基线之上，补齐评分要求中的输入鲁棒性、多文档完整回答、多表查询契约和多轮上下文保持能力，并用分阶段提交保证每一步可验证、可回退。

**Architecture:** 保留现有“解析/路由 → SQL、RAG 或跨源执行 → 证据检查 → 答案合成”的分层结构。新增能力优先落在确定性契约、状态合并和证据覆盖检查上；模型只负责无法由本地规则确定的表达生成，不承担路由和关键事实完整性判断。

**Tech Stack:** Python 3.12、Pydantic、pytest、SQLite Chinook、现有 Agent Graph、受控 SQL 服务、文档检索和 stage2 评测脚本。

---

## 一、执行前基线和提交规则

当前基线提交为 258a849：

    checkpoint: save latest evaluation improvements

最新 live 评测快照：

- 107 条评测记录；
- 94/107，通过率 87.85%；
- 多文档 11/15；
- 多表关联 6/8；
- 多轮会话 3/5；
- 无证据处理 2/5；
- 澄清 7/8。

每个任务必须按以下顺序执行：

- [ ] 先写或修改测试；
- [ ] 运行针对性测试，确认失败原因与任务一致；
- [ ] 写最小实现；
- [ ] 再次运行针对性测试；
- [ ] 运行 git diff --check；
- [ ] 提交一个独立 checkpoint；
- [ ] 记录提交哈希和测试结果。

每个阶段的提交信息使用以下格式：

    test: ...
    fix: ...
    docs: ...
    chore: ...

禁止在阶段中间执行 git reset --hard，除非明确回退到上一阶段提交。

---

## 二、文件职责边界

计划涉及的文件按职责分组：

- 评测集与评分：eval/agent/、eval/human/、scripts/build_eval_set.py、scripts/eval_scoring.py。
- 路由与澄清：services/agent_api/app/agent/route_policy.py、task_policy.py、parsing.py、clarification.py、clarification_reply.py。
- 多轮状态：services/agent_api/app/agent/turn_update.py、state.py、services/agent_api/app/services/graph_ask_service.py、services/agent_api/app/agent/graph.py。
- 文档证据与答案：services/agent_api/app/knowledge/facts.py、services/agent_api/app/response/document_answer.py、fast_renderer.py、composer.py。
- 多表 SQL 契约：profiles/chinook/metrics.json、services/agent_api/app/profiles/configs/chinook-music.json、backend/sql_module/query_intent.py、backend/sql_module/service.py。
- 测试：services/agent_api/tests/test_document_answer.py、test_agent_session_updates.py、test_agent_clarification_policy.py、test_agent_task_policy.py、test_stage2_multitable.py、test_product_sql_queries.py，必要时新增同目录测试文件。

不要把评测样本硬编码到生产路由函数中。题库只进入 eval/ 和评测脚本；生产代码只读取 profile、指标契约、规则和证据。

---

## Task 0：确认工作区与创建阶段日志

**Files:**

- Create: docs/superpowers/progress/2026-10-13-agent-optimization-progress.md

- [ ] Step 1: 检查基线

Run:

    git status --short
    git log -1 --oneline --decorate

Expected:

    258a849 checkpoint: save latest evaluation improvements

工作区必须为空；如果有修改，先单独记录来源并提交，不能覆盖。

- [ ] Step 2: 建立阶段日志

在进度文件中记录：

- 起始提交：258a849；
- 当前评测快照路径：tmp/stage2-eval/20261013T123629Z-live-5c4ea696/；
- 本计划的五个功能阶段；
- 每阶段提交哈希、测试命令和结果。

- [ ] Step 3: 提交阶段日志

    git add docs/superpowers/progress/2026-10-13-agent-optimization-progress.md
    git diff --cached --check
    git commit -m "docs: start staged agent optimization log"

---

## Task 1：建立 P3 鲁棒性专项评测与路由不变量

**目标：** 覆盖评分表中 10 分的鲁棒性要求。当前别名、负例和澄清题只能覆盖局部鲁棒性，不能证明面对错别字、噪声、冲突条件和非法时间时系统行为稳定。

**Files:**

- Create: eval/agent/eval_robustness.jsonl
- Create: eval/human/eval_robustness.md
- Modify: scripts/build_eval_set.py
- Modify: scripts/eval_scoring.py
- Create: services/agent_api/tests/test_eval_robustness_contract.py

- [ ] Step 1: 写 12 条最小鲁棒性题

题目和预期契约固定如下：

| ID | 输入变化 | 预期状态 | 预期路由/不变量 |
| --- | --- | --- | --- |
| robust-001 | “摇滚”错写成“摇滚类” | answered 或 clarification | 不得把 Rock And Roll 静默并入 Rock |
| robust-002 | Rock、rock、 ROCK 混写 | answered | 统一到 Genre:1 |
| robust-003 | “2025 Q3 音频销售额”插入无关描述 | answered | 仍路由 sql，数值与标准题一致 |
| robust-004 | 缺年份的季度销售额 | clarification_required | 必须要求年份，不得默认为当前年份 |
| robust-005 | “2025Q3 但又说 2025Q2” | clarification_required | 必须指出时间冲突 |
| robust-006 | “第三季度利润” | unsupported | 必须说明缺成本/结算金额 |
| robust-007 | “活动点击和转化” | unsupported | 必须说明缺事件日志，不能用交易额替代 |
| robust-008 | “全店销售额和音乐销售额范围差异” | answered | 走 rag，不得追问时间 |
| robust-009 | 已问 Q2 后追问“换成 Q3” | answered | 只替换季度，保留音频范围和指标 |
| robust-010 | 非法“第六季度销售额” | clarification_required | 指出季度非法，不得降级为全年 |
| robust-011 | “音频和视频分别统计” | clarification_required 或 answered | 不能静默返回合计值 |
| robust-012 | 空泛“卖得怎么样” | clarification_required | 要求指标和观察范围 |

每条记录必须包含 id、question、expected_status、expected_route、required_answer_fields、invariants。

- [ ] Step 2: 接入 manifest 和评分器

在 scripts/build_eval_set.py 中加入 eval_robustness.jsonl 的生成、数量统计和 human README 映射。在 eval/agent/eval_manifest.json 中加入 robustness 映射：

    "robustness": "eval/agent/eval_robustness.jsonl"

评分器至少检查：

- status；
- route；
- clarification 是否包含指定槽位；
- unsupported 是否包含真实数据缺口；
- answer 是否包含必要边界词；
- 同义、大小写和噪声变体是否保持同一路由。

同时修复 per-file 汇总显示：eval_multiturn 的统计单位是 5 组会话，不是 0 道单轮题。报告中必须显示多轮会话的实际通过数（例如 3/5），不能显示 0/0；该修复只影响报表聚合，不改变总分口径。

- [ ] Step 3: 写契约测试

测试至少包含以下断言：

    def assert_contract(result, case):
        assert result.status == case["expected_status"]
        assert result.route == case["expected_route"]
        for token in case["required_tokens"]:
            assert token in result.answer or token in result.limitations or token in result.clarification

另外增加三组等价输入测试：大小写变化、中文别名变化、插入无关文本后路由保持一致。

- [ ] Step 4: 运行测试

    python -m pytest services/agent_api/tests/test_eval_robustness_contract.py -q
    python scripts/verify_knowledge_snapshot.py

Expected：测试全部通过，题库校验无 duplicate id、无 result mismatch。

- [ ] Step 5: 提交

    git diff --check
    git add eval/agent/eval_robustness.jsonl eval/human/eval_robustness.md scripts/build_eval_set.py scripts/eval_scoring.py services/agent_api/tests/test_eval_robustness_contract.py
    git commit -m "test: add robustness evaluation contracts"

---

## Task 2：修复多文档答案的事实覆盖和点名文档职责

**目标：** 将多文档从 11/15 提升到至少 15/15。当前主要问题是文档已召回，但最终摘要漏掉关键事实。

**Files:**

- Modify: services/agent_api/app/knowledge/facts.py
- Modify: services/agent_api/app/response/document_answer.py
- Modify: services/agent_api/app/response/fast_renderer.py
- Modify: services/agent_api/app/response/composer.py
- Modify: services/agent_api/tests/test_document_answer.py
- Modify: services/agent_api/tests/test_stage2_cross_workflows.py

- [ ] Step 1: 为四类问题定义文档角色

在 facts.py 中定义可序列化的角色映射：

    DOCUMENT_ROLES = {
        "target_review_activity": {"D06": "target", "D08": "actual_review", "D10": "operation"},
        "customer_category_action": {"D04": "segmentation", "D09": "category_operation"},
        "activity_material_crosscheck": {"D10": "schedule_and_version", "D11": "registration_material"},
        "metric_method_crosscheck": {"D01": "metric_definition", "D02": "analysis_method"},
    }

角色名称必须进入 answer_obligations，不能只依赖关键词重合。

- [ ] Step 2: 强制每个角色提供至少一个事实片段

在 document_answer.py 中让答案合成按角色处理：

1. 先按 primary document 取证；
2. 每个角色至少保留一个与问题相关的句子；
3. 同一文档可提供多个角色，但不能因为一个角色命中而跳过其他文档；
4. 片段重复时按 chunk id 去重；
5. 仍缺角色时返回缺失角色清单给 composer。

- [ ] Step 3: 在 composer 中做最终覆盖检查

在 composer.py 的文档答案路径加入：

    missing_roles = [
        item["role"]
        for item in coverage(task, documents)
        if not item["complete"]
    ]

若缺的是关键角色，先执行一次定向检索；定向检索后仍缺失时，返回 insufficient_evidence，不得返回看似完整但漏项的摘要。

- [ ] Step 4: 添加四组回归断言

必须覆盖：

- multidoc-002：出现目标、实际达成、Rock 主页面、Metal 延伸栏目；
- multidoc-003：出现分层、专辑、覆盖、专题陈列；
- multidoc-009：出现 10 月 13 日、18:00、活动周期、材料和版本核对；
- session-001 第 3 轮：出现商品覆盖、订单集中、专辑结构。

- [ ] Step 5: 运行测试并提交

    python -m pytest services/agent_api/tests/test_document_answer.py services/agent_api/tests/test_stage2_cross_workflows.py -q
    git diff --check
    git add services/agent_api/app/knowledge/facts.py services/agent_api/app/response/document_answer.py services/agent_api/app/response/fast_renderer.py services/agent_api/app/response/composer.py services/agent_api/tests/test_document_answer.py services/agent_api/tests/test_stage2_cross_workflows.py
    git commit -m "fix: enforce complete multi-document answer coverage"

---

## Task 3：稳定多轮上下文状态和条件继承

**目标：** 将多轮从 3/5 提升到 5/5，并满足评分表的上下文保持要求。

**Files:**

- Modify: services/agent_api/app/agent/turn_update.py
- Modify: services/agent_api/app/agent/clarification_reply.py
- Modify: services/agent_api/app/agent/graph.py
- Modify: services/agent_api/app/services/graph_ask_service.py
- Modify: services/agent_api/tests/test_agent_session_updates.py
- Modify: services/agent_api/tests/test_agent_clarification_policy.py
- Create: services/agent_api/tests/test_multiturn_contract.py

- [ ] Step 1: 固定会话状态合并规则

在 turn_update.py 中明确：

- new：清空旧的业务条件；
- supplement：只补充缺失槽位；
- correct：只替换用户明确修改的槽位；
- followup：继承年份、季度、范围、媒体范围、实体和上一轮答案职责；
- confirm：只解决 pending clarification，不得把“确认”当成任意默认值。

保留 task.answer_obligations，不能在 follow-up 中重新根据短问题生成空 obligations。

- [ ] Step 2: 修复 pending clarification 状态

在 clarification_reply.py 和 graph.py 中保证：

- 首轮需要澄清时状态为 clarification_required；
- pending task 保留原始问题和缺失槽位；
- 用户只补季度时，不丢失原范围；
- 用户补充“目标”时，不丢失上一轮实体；
- 用户明确取消后清空 pending 和 last task。

- [ ] Step 3: 添加会话测试

至少测试：

    def test_target_followup_keeps_rock_scope():
        # session-001: Rock -> 和目标比 -> 下一步重点看什么
        assert "Rock" in answer
        assert "覆盖" in answer

    def test_customer_tiering_first_turn_requires_period():
        # session-004 turn 1
        assert response.status == "clarification_required"
        assert "观察周期" in response.clarification

同时覆盖 period switch、metric switch、scope correction、cancel、new question reset。

- [ ] Step 4: 运行测试并提交

    python -m pytest services/agent_api/tests/test_agent_session_updates.py services/agent_api/tests/test_agent_clarification_policy.py services/agent_api/tests/test_multiturn_contract.py -q
    git diff --check
    git add services/agent_api/app/agent/turn_update.py services/agent_api/app/agent/clarification_reply.py services/agent_api/app/agent/graph.py services/agent_api/app/services/graph_ask_service.py services/agent_api/tests/test_agent_session_updates.py services/agent_api/tests/test_agent_clarification_policy.py services/agent_api/tests/test_multiturn_contract.py
    git commit -m "fix: stabilize multi-turn context inheritance"

---

## Task 4：补齐多表关联指标契约和 SQL 路由

**目标：** 将多表关联从 6/8 提升到 8/8。两个失败点都是确定性契约缺口，不应继续依赖模型自由生成。

**Files:**

- Modify: profiles/chinook/metrics.json
- Modify: services/agent_api/app/profiles/configs/chinook-music.json
- Modify: backend/sql_module/query_intent.py
- Modify: backend/sql_module/service.py
- Modify: services/agent_api/app/agent/parsing.py
- Modify: services/agent_api/app/agent/task_policy.py
- Modify: services/agent_api/app/response/composer.py
- Modify: services/agent_api/tests/test_stage2_multitable.py
- Modify: services/agent_api/tests/test_product_sql_queries.py

- [ ] Step 1: 增加支持负责人分布操作

在 query_intent.py 中加入操作名：

    support_rep_customer_distribution

解析问题“按支持负责人分布”“购买音频客户按支持负责人统计”时，生成：

    {
      "operation": "support_rep_customer_distribution",
      "measures": ["purchasing_customers"],
      "result_grain": "support_rep"
    }

- [ ] Step 2: 增加受控 SQL

在 backend/sql_module/service.py 的 controlled query 分支中执行：

    SELECT e.FirstName || ' ' || e.LastName AS support_rep,
           COUNT(DISTINCT c.CustomerId) AS customer_count
    FROM Employee e
    JOIN Customer c ON c.SupportRepId = e.EmployeeId
    JOIN Invoice i ON i.CustomerId = c.CustomerId
    JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
    JOIN Track t ON t.TrackId = il.TrackId
    WHERE t.MediaTypeId IN (1,2,4,5)
      AND i.InvoiceDate >= :start_date
      AND i.InvoiceDate < :end_date
    GROUP BY e.EmployeeId, e.FirstName, e.LastName
    ORDER BY support_rep

输出应包含 Jane Peacock=8、Margaret Park=6、Steve Johnson=5，并说明这是客户维护分布，不是员工销售排名。

- [ ] Step 3: 放行混合订单的无周期查询

mixed_order_count 已在 profiles/chinook/metrics.json 注册且不需要周期。修改 task_policy.py 的全库周期门禁，使该指标直接进入 SQL 受控路径。

期望结果：

    mixed_order_count = 17

答案必须说明：统计混合订单时按订单同时存在音频和视频明细判断；计算音乐销售额时仍按 InvoiceLine 的音频范围筛选。

- [ ] Step 4: 添加回归测试

    def test_support_rep_customer_distribution():
        assert rows == [
            {"support_rep": "Jane Peacock", "customer_count": 8},
            {"support_rep": "Margaret Park", "customer_count": 6},
            {"support_rep": "Steve Johnson", "customer_count": 5},
        ]

    def test_mixed_order_count_without_period():
        assert result["mixed_order_count"] == 17

- [ ] Step 5: 运行测试并提交

    python -m pytest services/agent_api/tests/test_stage2_multitable.py services/agent_api/tests/test_product_sql_queries.py -q
    git diff --check
    git add profiles/chinook/metrics.json services/agent_api/app/profiles/configs/chinook-music.json backend/sql_module/query_intent.py backend/sql_module/service.py services/agent_api/app/agent/parsing.py services/agent_api/app/agent/task_policy.py services/agent_api/app/response/composer.py services/agent_api/tests/test_stage2_multitable.py services/agent_api/tests/test_product_sql_queries.py
    git commit -m "fix: complete multi-table query contracts"

---

## Task 5：统一 P2 拒答、P5 边界和 P6 澄清槽位

**目标：** 修复低成本但高确定性的模板和策略失分。

**Files:**

- Modify: services/agent_api/app/agent/task_policy.py
- Modify: services/agent_api/app/agent/route_policy.py
- Modify: services/agent_api/app/agent/parsing.py
- Modify: services/agent_api/app/agent/clarification.py
- Modify: services/agent_api/app/agent/semantic_gate.py
- Modify: services/agent_api/tests/test_agent_task_policy.py
- Modify: services/agent_api/tests/test_agent_clarification_policy.py

- [ ] Step 1: 固定拒答模板

模板必须包含以下完整语义：

    利润：当前资料没有成本或结算金额，不能计算利润。
    活动转化：当前没有活动曝光、点击或转化事件日志，只能统计交易变化。
    SupportRep：SupportRep 表示客户维护责任，不等同于个人销售归属或提成贡献。

修正 SupportRep 员工销售排名 的错误字符，统一使用“员工”。

- [ ] Step 2: 固定边界规则

- “全店销售额和音乐销售额有什么范围差异？”直接走 rag；
- “客单价和成交均价分别用什么分母？”直接走 rag；
- “销售额达到目标了吗？”必须澄清 period、scope、target_metric；
- 有年份但无季度的文档解释题，不追加数据库查询周期；
- 只有出现实际值、数据库、查数、差额等词时，才升级到 SQL 或跨源。

- [ ] Step 3: 添加回归测试

覆盖：

- special-negative-001；
- special-negative-004；
- special-negative-005；
- special-alias-006；
- multidoc-010；
- special-clarify-007。

- [ ] Step 4: 运行测试并提交

    python -m pytest services/agent_api/tests/test_agent_task_policy.py services/agent_api/tests/test_agent_clarification_policy.py -q
    git diff --check
    git add services/agent_api/app/agent/task_policy.py services/agent_api/app/agent/route_policy.py services/agent_api/app/agent/parsing.py services/agent_api/app/agent/clarification.py services/agent_api/app/agent/semantic_gate.py services/agent_api/tests/test_agent_task_policy.py services/agent_api/tests/test_agent_clarification_policy.py
    git commit -m "fix: align refusal boundary and clarification contracts"

---

## Task 6：阶段验收、三次 live 复跑和最终提交

**目标：** 证明优化收益来自代码，而不是单次模型波动。

- [ ] Step 1: 运行完整 Python 测试

    python -m pytest services/agent_api/tests -q

Expected：无失败；若存在与本计划无关的既有失败，记录测试名和原因，不能隐藏。

- [ ] Step 2: 运行 C 与 SQL 合同测试

    python -m pytest eval/test_sql_contract.py eval/test_c_stage1.py eval/test_c_stage2.py eval/test_c_stage3.py -q

- [ ] Step 3: 固定同一提交连续运行三次 live 评测

    python scripts/run_agent_eval.py --all --mode live --model MiMo-V2.6-Flash --concurrency 1 --out tmp/stage2-eval/final-run-1.json
    python scripts/run_agent_eval.py --all --mode live --model MiMo-V2.6-Flash --concurrency 1 --out tmp/stage2-eval/final-run-2.json
    python scripts/run_agent_eval.py --all --mode live --model MiMo-V2.6-Flash --concurrency 1 --out tmp/stage2-eval/final-run-3.json

验收目标：

- 多文档至少 15/15；
- 多表至少 8/8；
- 多轮至少 5/5；
- 无证据至少 5/5；
- 澄清至少 8/8；
- 别名至少 15/15；
- 三次运行中上述确定性题不出现新的回归；
- 现有 107 条正式题库总体目标至少 104/107；
- 新增 12 条鲁棒性专项题全部满足 status、route、关键事实和拒答边界契约；
- 如果将鲁棒性题并入全量命令，报告总量应为 119 条，综合目标至少 116/119；
- 报表中的多轮会话显示真实的 x/5，不得出现 0/0。

- [ ] Step 4: 更新阶段日志

记录每个阶段提交哈希、测试结果、三次 live 的总分和分类分数。对有波动的题单独记录，不把单轮波动直接归因于代码。

- [ ] Step 5: 最终提交

    git diff --check
    git status --short
    git add docs/superpowers/progress/2026-10-13-agent-optimization-progress.md
    git commit -m "chore: record staged optimization verification"

最终交付给后续 agent 的入口：

- 本计划：docs/superpowers/plans/2026-10-13-agent-robustness-multidoc-multitable-multiturn.md
- 阶段日志：docs/superpowers/progress/2026-10-13-agent-optimization-progress.md
- 基线提交：258a849

后续 agent 应从 Task 0 开始，按顺序完成 Task 1 至 Task 6；每一阶段完成后必须先提交，再进入下一阶段。
