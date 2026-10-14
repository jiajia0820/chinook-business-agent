# Agent 优化阶段进度日志

## 基线

- 代码基线：`258a849 checkpoint: save latest evaluation improvements`
- 执行起点 HEAD：`68e684d docs: add staged agent optimization plan`
- 工作区起点：干净
- 最新既有 live 评测：`tmp/stage2-eval/20261013T123629Z-live-5c4ea696/`
- 既有结果：94/107（87.85%）

## 阶段目标

1. P3 鲁棒性专项评测、路由不变量和多轮报表聚合；
2. 多文档答案事实覆盖；
3. 多轮上下文状态和条件继承；
4. 多表关联指标契约与 SQL 路由；
5. P2 拒答、P5 边界、P6 澄清槽位；
6. 全量测试与三次 live 评测。

## 提交记录

| 阶段 | 提交 | 验证 |
| --- | --- | --- |
| 计划文档 | 68e684d | `git diff --check` 通过，工作区干净 |
| Task 0 | 进行中 | — |
| Task 1 | e46a313 | 14 passed, 12 subtests; knowledge snapshot verified |
| Task 2 | 172f50c + 848aef9 + 7993cd3 | 19 passed, 6 subtests；证据角色覆盖和多轮 Rock 运营追问补充完成；6 个集成失败为本地 live 模型配置缺失 |
| Task 3 | 25c0bc1 | 18 passed；客户分层首轮澄清、Rock 运营追问上下文验证通过 |
| Task 4 | c8c122e | 3 个多表契约测试通过；离线集成验证支持负责人 8/6/5、混合订单 17 |
| Task 5 | 2e836e6 | 20 passed, 13 subtests；拒答、定义边界、目标澄清契约通过 |
| Task 6 | — | — |
| 回退恢复 | 工作区未提交 | 恢复 Task 3 客户分层首轮澄清；全量单测 15 failed / 439 passed，失败清单与 `c043566` 基线逐条一致（无新增）；live 全量 118/119 |

## 回退恢复记录（2026-10-14）

`340640d` + `c043566` 两次 revert 误删了 Task 3 的客户分层澄清契约（`task_policy.py` 中
“客户怎么分层/客户如何分层 → observation_period”两行）及 `test_multiturn_contract.py`，
导致 `eval_multiturn` session-004 首轮不再澄清。已按计划 Task 3 Step 3 恢复：

- `task_policy.py`：补回两行触发词（置于 `高价值客户` 分支之前）；
- `test_multiturn_contract.py`：重建首轮澄清与“分层期间”回复仍走文档路由两个用例；
- `test_c_v2_semantics.py` / `test_clarification_v3_replies.py`：区分“客户分层规则是什么？”
  （ready）与“客户怎么/如何分层？”（澄清 observation_period），与评测集契约对齐；
- `test_all_documents_rag.py`：D04 路由探针改用同义问法“客户分层规则是什么？”，
  避免与 session-004 的澄清契约冲突。

契约冲突判定：`eval/c_v2/supplemental.json` 期望“客户怎么/如何分层”直接作答，而
`eval/agent/eval_multiturn.jsonl` session-004 期望首轮澄清。以评测集为准——
`run_c_v2_supplemental.py` 自述为独立诊断且不改原始 80 题评分器，计划最终验收使用
`run_agent_eval.py --all --mode live`。

### live 全量评测

| 运行目录 | 结果 | 说明 |
| --- | --- | --- |
| `tmp/stage2-eval/live-c043566-tierfix-r1` | 117/119 | `basic-nl2sql-015`、`robust-009` 网关 HTTP 500 落受控回退，标为待复测 |
| `tmp/stage2-eval/live-c043566-retest2` | 25/27（仅 basic_nl2sql + robustness） | 上述 2 题复测真实通过；`basic-nl2sql-004/007` 本轮改为待复测（网关抖动轮换） |
| `tmp/stage2-eval/live-c043566-tierfix-r2` | 118/119（99.2%），待复测 0 题 | 除 multiturn 4/5 外全部能力 100% |

r2 唯一失败为 `session-003` 第 2 轮（期望 answered，实际 insufficient_evidence）：
live 模型自行返回的候选 SQL 为 `t.GenreId = :genre_id AND t.GenreId <> :exclude_genre_id`，
且 params 把 `exclude_genre_id` 也填成 1（应为 Rock And Roll 的 Genre:5），形成自相矛盾谓词，
`SUM` 返回 NULL。`semantic_scope.covers_context` 只校验必需谓词是否存在，未识别多出的
矛盾排除条件，因此候选被接受、未回退到能得出 47.52 的受控计划。属 live 模型输出抖动
叠加校验缺口，非本次回退恢复引入（r1 同题通过，当轮模型生成的是不含排除参数的 SQL）。

## 执行约束

每阶段遵循：先写失败测试、确认失败、最小实现、回归测试、`git diff --check`、独立提交。所有 live 评测结果记录运行目录、提交哈希、题库哈希和分类分数。
