# 初赛第一天：语义安全与换词 RAG 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 在一天内提高评委自然改写问题的可用性，并阻止模型失败或受控降级时丢失过滤、粒度和输出要求。

**Architecture:** 保留现有 Chinook profile、SQL 只读校验、D01-D12 知识快照、fast_renderer 和证据面板。新增“问题义务”对象记录实体、过滤、分组、累计、排名和未消费词；模型候选与受控查询都要通过义务覆盖检查。RAG 继续支持 lexical/rank/hybrid，但默认仍为 lexical；hybrid 只有在端到端不回归时才可切换。

**Tech Stack:** Python 3.12、Pydantic v2、LangGraph、SQLGlot、现有 unittest、scripts/run_agent_eval.py。不得新增外部服务或数据库。

---

## 实施进度标记（回退锚点）

> 本节由执行 agent 维护，随 Task 推进更新。计划文件本身在 Task 7 单独提交。

- **分支**：`codex/preliminary-day1`
- **开工前 HEAD**：`a88fc9d feat: redesign agent workspace UI`
- **回退标记**：`git tag checkpoint/preliminary-day1-before`（指向开工前 HEAD）
- **整体回退**：`git reset --hard checkpoint/preliminary-day1-before`（会丢弃本次全部改动，需用户确认）
- **逐任务回退**：每个 Task 单独一个 commit，`git revert <sha>` 或 `git reset --hard <sha>^` 即可回退单个任务
- **不触碰**：`services/web/` 下用户已有未提交改动（App.vue、EvidencePanel.vue、style.css、app.test.ts、ui-redesign.test.ts）

| Task | 内容                         | 状态  | 提交        |
| ---- | -------------------------- | --- | --------- |
| 0    | 冻结基线 + 20 题封存集 + 安全评分脚本    | 已完成 | `e03e2e8` |
| 1    | 记录原问题义务（query_obligations） | 已完成 | `aaf1aa3` |
| 2    | 修复规则问题过度澄清                 | 已完成 | `4c367ec` |
| 3    | 阻断 SQL 失败降级静默简化            | 已完成 | `4ff4d73` |
| 4    | 受保护 hybrid 检索（默认仍 lexical） | 已完成 | `1c541a4` |
| 5    | 换词问题的简短可追溯答案               | 已完成 | `50f1671` |
| 6    | 封存集 / 故障注入 / 冒烟验收          | 已完成 | 无代码提交（证据在 `tmp/preliminary-day1-final/`） |
| 7    | 默认模式决策与收尾                  | 已完成 | 本次提交（计划文件单独提交） |

**Task 0 基线成绩**（证据在 `tmp/preliminary-day1-baseline/`，不提交）：

- 基础 RAG `eval_basic_rag` offline：**15/15**，平均 43ms（第一天必须守住）
- 全量离线 `--all` 119 题：**91/119（76.47%）**，lexical 对照基线
- 全量单测：454 tests，**15 项历史失败**（清单见 `known-failures.txt`，只记录不修）
- 封存集 offline：严格 **6/20**，安全 **12/20**；silent_simplification=0、missing_evidence=1、over_clarification=7

**提交记录**

- `checkpoint/preliminary-day1-before` → `a88fc9d`（tag，开工前状态）
- `e03e2e8` Task 0 封存集与评分脚本
- `aaf1aa3` Task 1 query_obligations
- `4c367ec` Task 2 换词规则问题先检索后澄清
- `4ff4d73` Task 3 阻断 SQL 降级静默简化
- `1c541a4` Task 4 受保护 hybrid 检索（默认仍 lexical）
- `f473a48` 用户自行提交的前端工作区改动（不属本计划任何 Task，回退时勿动）
- `50f1671` Task 5 换词问题的简短可追溯答案
- Task 6 无代码提交（只运行与记录；证据在 `tmp/preliminary-day1-final/`，不提交）
- Task 7 收尾提交即本计划文件自身（`docs/superpowers/` 被 gitignore，用 `git add -f` 强加）：默认保持 lexical，hybrid 仅实验开关

**Task 2 结果**（证据在 `tmp/task2/`，不提交）：

- 封存集 offline：严格 **6/20 → 8/20**，安全 **12/20 → 14/20**；`missing_evidence` 1 → 0，`over_clarification` 7 → 6，`silent_simplification` 保持 0
- 001/002/003 三类改写全部进入 RAG，来源计划分别命中 D04 / D11 / D02；007 选品门槛顺带修正到 D05
- 001/002 已能取到 D04、D10/D11 文档，但仍判 `insufficient_evidence`：答案生成属于 Task 5 范围
- 基础 RAG `eval_basic_rag` offline 保持 **15/15**；全量 `--all` offline 保持 **91/119**，失败集合与基线逐题一致
- 全量单测 471 tests，失败集合与基线 15 项**完全相同**，无新增失败
- `parsing.py` 经核对无需改动：`missing_conditions` 对 `route == "rag"` 已豁免交易年份必填

**Task 3 结果**（证据在 `tmp/task3/`，不提交）：

- 计划清单外但必要的改动：`query_obligations.py`（重写未消费残渣词判定，只保留"取值样"token）、`parsing.py`（`_unparsed_query_text` 增加 keyword-only `separator`，默认空串保持旧行为）、`c_offline.py`（探测 vendor 模块是否导出 `QUERY_OBLIGATION_KEYS` 再决定是否下发义务）
- 新增降级安全测试 3 项全部通过；Task 1 + Task 3 测试共 14 tests OK
- 全量单测 476 tests，失败集合与基线 15 项**逐项相同**（程序化比对 IDENTICAL），无新增失败
- 基础 RAG `eval_basic_rag` offline 保持 **15/15**（平均 44ms）
- 全量 `--all` offline 保持 **91/119**，失败集合与基线、Task 2 逐题一致（NEW=[]、FIXED=[]）
- 封存集 offline：严格 **8/20**、安全 **14/20**，与 Task 2 完全一致（diff=0）；`silent_simplification` 保持 0
- 有意不实现"排除过滤检测"（如"除了 Metal"）：收益低、误伤风险高，离线模式由 `parsing.py` 的 filter_scope 兜底；全 eval 题库扫描确认残渣判定只命中期望拒答题

**Task 4 结果**（证据在 `tmp/task4/`，不提交）：

- 新增 `services/agent_api/app/knowledge/retrieval_confidence.py`：`resolve_fusion_mode`（未知取值降级 lexical）、`channel_agreement`（重叠系数=交集/min，避免 Jaccard 惩罚不对称召回）、`top1_supported`（dense top-1 必须落在词法 top-3 内，硬闸）、`assess_retrieval`、`guarded_fusion`（RRF，K=60，required 来源置顶，margin 只在 dense cosine 同一分数空间内计算）
- `index.py` 增加 `RAG_FUSION_MODE`（lexical/rank/hybrid），默认 lexical：旧排序、旧 hints 与旧 `score += dense*10` 行为逐字保留；非 lexical 时走 `_guarded_fusion`，**只重排词法已召回的片段**（`semantic_recall="reorder_only"`，不做跨通道补召回，守住 required_docs / fact_role / core_content_missing 红线），dense 冲突、必要来源缺失或 dense 不可用时回退 lexical，并把 `retrieval_mode`、`retrieval_confidence`、`fallback_reason` 写入 `RetrievalResponse.diagnostics`
- 计划清单外但必要的改动：`contracts/models.py` 的 `RetrievalResponse` 增加可选字段 `diagnostics: dict[str, JsonValue]`（契约是 `extra="forbid"`，不加字段无法把诊断传出到 trace）
- `source_plan.py` 经核对无需改动：`rag_source_plan` 已提供 `required_docs`/`primary_docs`/`query_terms`，Task 4 直接复用，未新增角色或别名
- 新增测试 28 项全部通过（`test_retrieval_confidence.py` 18 项、`test_guarded_hybrid_day1.py` 10 项；后者用 `mock.patch.object(type(index), "_dense_scores", ...)` 注入假向量，不触发 fastembed 真实加载）
- Task 4 计划指定单测（含 `test_knowledge_index`、`test_fast_rag`）：**50 tests OK**
- 基础 RAG `eval_basic_rag` offline 保持 **15/15**（平均 52ms）
- 全量 `--all` offline 保持 **91/119**，失败集合与基线、Task 2、Task 3 逐题一致（NEW=[]、FIXED=[]）
- 全量单测 **Ran 504 tests**（476+28），失败集合与基线 15 项**逐项相同**（程序化比对 IDENTICAL），无新增失败
- 封存集 offline：严格 **8/20**、安全 **14/20**，逐题与 Task 3 一致（per-item diff=[]，仅耗时不同）；`silent_simplification` 保持 0
- hybrid 默认开关未启用：fastembed 模型未缓存，下载失败时经 `_dense_failed` 优雅降级为 `dense_unavailable` → lexical 回退，属安全路径；是否设为默认留待 Task 7 依据端到端对照决策

**Task 5 结果**（证据在 `tmp/task5/`，不提交）：

- `fast_renderer.py` 新增 `answer_role_from_question`，返回 `customer_segmentation` / `metric_definition` / `activity_plan` / `generic_excerpt`；只读问题措辞，不读评测期望，也不从 doc_id 反推事实。`_ROLE_RENDERERS` 的分派位置在 `_multidoc_*` 之后、`classification`/`poster`/`_excerpt` 兜底之前，渲染器取不到证据时仍落回原链路
- `document_answer.py` 新增角色谓词 `asks_tier_thresholds`（须同时命中层级语义词与索取语义词，裸「档」不入表以免撞「文档」）、`asks_metric_denominator`、`asks_activity_plan`，以及聚焦渲染器 `customer_tiers`、`metric_definition`；`poster()` 入口改用 `asks_activity_plan`，截止行补「最迟/哪天交/征集」，材料行补「得准备/准备哪些/哪些东西/要交什么/交什么」
- `_content_missing` 末尾新增内容闸：阈值角色且证据不含「大于等于 12.00」「12.00 以下」形状时追加 `分层阈值`，驱动图内补检索，而不是用分层结果示例表（客户名单+金额）冒充界线
- 阈值表补检索端到端生效：`prelim-day1-001` 首轮只召回 5 个 D04 片段（#P84/#P87-table/#T4/#P11/#P5），不含 `#P15-table`；闸触发后第二次 `rag.retrieve` 用 `' '.join(query_terms) + ' ' + question` 把 `#P15-table` 顶到第 1，状态由 `insufficient_evidence` 转 `answered`
- 答案形态守住计划要求：先结论、再一句口径、末尾 `依据：[Dxx]。`；阈值答案只留界线列（丢弃「辅助观察 / 第三季度人数」），海报答案不拼整图 OCR（`CHINOOKDIGITALMUSIC / YOUR PICKS / ALBUM PICKS / GENRE3/EXT` 均被负向断言排除），三题答案长度 81-139 字符，远低于 900 上限
- 新增测试 17 项（`ParaphraseAnswerContractTests` 8 + 补检索集成 1 + `FocusedRendererTests` 8），fixture 为 D04 阈值表 / D04 结果表 / D02 指标表 / D11 全图 OCR 的真实快照文本；Task 5 计划指定单测 **53 tests OK**
- 基础 RAG `eval_basic_rag` offline 保持 **15/15**（平均 31ms）—— `basic-rag-after-answer.json`
- 全量 `--all` offline 保持 **91/119**，失败集合与基线逐题一致（NEW=[]、FIXED=[]、MISSING=[]、EXTRA=[]）
- 答案文本影响面（对比 Task 4 HEAD `1c541a4`，剔除随机 `sql-<uuid>` 后）：119 题中只有 `basic-rag-011`、`multidoc-010` 两题变化，均为 metric_definition 角色、由指标表倾倒改为两句口径且仍通过；9 个证据含 D11 的 activity_plan 命中题答案**逐字未变**（`poster()` 原本已在兜底链中胜出，只是提前了一格）
- 封存集 offline：严格 **8/20 → 10/20**，安全 **14/20 → 16/20**；001/002 转 `answered`，003 换聚焦渲染后仍严格通过；`silent_simplification` 保持 0、`missing_evidence` 保持 0、`over_clarification` 6 → 4
- 全量单测 **Ran 521 tests**（504+17），失败集合与基线 15 项**逐项相同**（程序化比对 IDENTICAL，NEW=0 / GONE=0）
- 全语料角色扫描 134 题（`scan_roles.out.txt`）：`generic_excerpt=112`、`activity_plan=17`、`metric_definition=3`、`customer_segmentation=2`，扫描确认三个谓词不越界
- 已知边界（有意不扩大改动）：`composer.py` 的义务覆盖分支在 `local_answer` 之前就返回「已核验的资料事实」原文块，因此 `basic-rag-005`（同为阈值问题、已解析出 `answer_obligations`）仍走表格原文而不经过聚焦渲染器；换词题因未解析出 obligations 才正常走聚焦渲染。`composer.py` 不在 Task 5 计划文件清单内且该题当前通过，第一天不动，留给后续统一答案形态时处理

**Task 6 结果**（证据在 `tmp/preliminary-day1-final/`，不提交；验收记录见该目录 `README.txt`）：

- 本任务只运行与记录，未改任何产品代码；`scripts/score_preliminary_day1.py` 经核对无需适配，故 Task 6 无代码提交
- Step 2 检索对照**跳过**：仓库不存在 `scripts/eval_retrieval_recall.py`，无 embedding 缓存，`doc_any/doc_top1/doc_all` 未产出；按计划该步为条件步骤，且召回率不得写成答案准确率
- 基础 RAG offline 保持 **15/15**（avg 34ms）；全量 `--all` offline 保持 **91/119**，失败 28 题与基线逐题一致（`NEW=[]`、`FIXED=[]`、VERDICT=IDENTICAL）
- 封存集 offline：严格 **10/20（50%）**、安全 **16/20（80%）**，`silent_simplification=0`、`missing_evidence=0`、`over_clarification=4`；对照 Task 0 基线严格 6→10、安全 12→16，无退化
- 封存集 live：严格 **10/20（50%）**、安全 **13/20（65%）**，`silent_simplification=3`、`missing_evidence=0`、`over_clarification=4`；`prelim-day1-013` 标记 `pending_external_api=true`（MODEL_HTTP_FAILED + MODEL_TIMEOUT），按规则不计作 live 通过
- 模型调用：live `model_attempt` 12 次（success 9、error 3：013 HTTP 500、013 超时、014 超时），`controlled_fallback` 事件 8 次，连续外部失败预算 6 未触发中断；注入集 6 次调用全部 MODEL_TIMEOUT，结果不并入 live 成绩
- 强制模型失败注入 **7/7 通过**：英国不冒全音频 112.86、每月累计不塌缩、USA 仍得 19.80、利润与转化率拒答、缺年份澄清
- 耗时：全量 offline p50 31ms / p95 78ms / max 140ms；封存集 live p50 1906ms / p95 48500ms / max 79172ms（外部 API 抖动主导）
- 后端定向 7 模块 **Ran 97 tests OK**；前端 `npm run verify` 全绿（Test Files 11 passed、Tests 307 passed、`vue-tsc` 通过、vite build 成功、边界检查 24 文件无泄漏）；`services/web` 工作树保持干净，本轮未改任何前端文件
- 结束时 code / scorer / dataset / data 四项指纹复核一致（`end-fingerprint-check.json` 全 true），commit `50f1671`、代码 hash `380f8cd4fd6a5776`、题库 `b0cadd39e69c1af4`、封存集 `cd23e0bcbced960a`、知识库 `30f49a09244bbcf0`、Chinook `3f7b02130e162e50`

**🔴 Task 6 未解决项（违反第一日验收门槛 2，已定位未修）**

live 出现 3 例 `silent_simplification`，全部 `route=sql`，同题在 offline 下均为 `unsupported`（安全）：

- `prelim-day1-014`（每月累计）：`answered` 但只有 1 行季度标量 112.86，缺 37.62 / 75.24 / 112.86 与按月分组结构 —— **直接违反门槛 2「每月累计不能降级成季度单行」**
- `prelim-day1-016`（零成交补 0）：`answered` 但只有 10 行（期望 25 行），零成交品类未补 0
- `prelim-day1-011`（排除 Metal）：`answered` 但缺必要数值 91.08，用户排除过滤丢失
- 另 `prelim-day1-009`（英国）live 为 `insufficient_evidence`（`rows=[{"sales_amount": null}]`），评分器安全项通过——**门槛 1 守住**，未把全音频总额冒充英国结果，仅严格项因状态标签不符失败

根因（只读诊断，证据 `model-calls.jsonl`、`session-state-probe.json`）：

1. 014 模型 ord1 超时、ord2 **成功**并返回窗口函数累计 SQL；016 模型 ord1 即**成功**并返回 `Genre LEFT JOIN + COALESCE(...,0)` 补零 SQL。两条候选语义本身正确，都不是「模型失败」
2. 两条候选都被 `backend/sql_module/service.py:926-932` 的 `_candidate_covers_context` 判为「遗漏已确认筛选条件」，随后被 `_controlled_query` 的已登记计划整体替换，粒度与补零语义在替换中丢失
3. 义务闸 `service.py:938` 的条件是 `model_failure is not None and not covered`，而 `service.py:875` 在任一次尝试成功后把 `model_failure` 置回 `None` —— 「重试成功 → 候选被替换成更粗计划」这条路径**完全绕过 Task 3 的义务闸**
4. 注入测试 7/7 通过恰恰因为它只覆盖 `model_failure` 非空的分支，无法复现 live 的 014/016
5. 补零义务在 `QUERY_OBLIGATION_KEYS`（`service.py:264-266`）里没有对应字段，即使走到 938 行也拦不住；`_candidate_covers_context`（`service.py:232`）对 `include_empty` 要求模型候选与已登记计划同构，等于把正确的 LEFT JOIN 补零候选判为不合格
6. 只读探针排除了「共享 service 的会话内已确认范围」假设：先问季度总额确认范围、再问每月累计仍为 `unsupported`，`reproduced=false`

不修的理由与处置：修复位点在 `backend/sql_module/service.py`，不在 Task 6 允许改动的文件清单内（Task 6 仅允许改 `scripts/score_preliminary_day1.py`），且「第一天不能做的事」禁止扩面、执行约束要求「先写失败测试再实现」。故本轮**只记录不修**，作为第二天第一项，建议最小修复：

- `service.py:938` 条件由 `model_failure is not None` 改为 `repaired or model_failure is not None`，让任何被受控计划替换过的候选都过义务闸
- `QUERY_OBLIGATION_KEYS` 增加补零/稠密维度义务，并在 `covers_query_obligations` 中检查
- `_candidate_covers_context` 对 `include_empty` 改为校验 `LEFT JOIN + COALESCE` 结构，不再要求与已登记计划同构
- 预期影响：现在「answered 但错」的题可能转为 `unsupported`，严格通过率短期或下降、安全分上升，符合「宁可拒答，不可静默简化」的口径

**hybrid 端到端补测（为门槛 3/6 提供事实）**：基础 RAG 在 `RAG_FUSION_MODE=hybrid` 下 **2/15**，同题集 lexical **15/15**，少 13 题（门槛 6 上限为 2 题）。本机无 dense 向量模型缓存（`BAAI/bge-small-zh-v1.5` 未下载成功，`~/.cache` 与仓库内均无缓存目录）；`_dense_scores`（`services/agent_api/app/knowledge/index.py:122-148`）的 `try/except` 确实会置 `_dense_failed` 并回退 lexical，但**首次加载会阻塞在网络超时上**，13/15 题在 agent 5s deadline 处直接返回 `error`，只有 `_dense_failed` 置位之后的题目才享受回退。这是 Task 4 记录里「下载失败优雅降级」的边界补充：降级成立，但会击穿请求时限。

**Task 7 结果（默认模式决策与收尾）**

- 决策：**默认保持 `RAG_FUSION_MODE=lexical`**；hybrid 仅作显式实验开关保留，不写入 `.env`、不改代码默认值
- 代码事实核对：`services/agent_api/app/knowledge/retrieval_confidence.py:14` 为 `DEFAULT_FUSION_MODE = "lexical"`；`resolve_fusion_mode` 对 `None`/空串/未知取值一律返回 lexical（未知取值另附 `unsupported_fusion_mode` 原因）；`index.py:207` 只从环境变量读取；`.env` 与 `config/*` 均未设置该变量，只有 `test_guarded_hybrid_day1.py` 在测试内临时设置并弹出
- 门槛逐条判定（证据见 `tmp/preliminary-day1-final/README.txt` 第九节）：1 满足、2 满足、**3 不满足**（hybrid 端到端 2/15 vs lexical 15/15，少 13 题，远超门槛 6 的 2 题上限）、**4 不满足**（封存换词 offline 严格 50%；live 有 3 例静默简化）、5 满足、6 满足 → 按「任一条件不满足即保持 lexical」处理
- 语义补救边界：只有通过证据覆盖检查的补救才进入回答（Task 5 的 `_content_missing` 内容闸驱动图内补检索，不用分层结果示例表冒充金额界线）；覆盖检查不通过时保持 `insufficient_evidence` / `unsupported`，不猜业务口径
- 最终检查：`git status --short` 干净、`git diff --check` 无空白错误、`git log --oneline -8` 与「提交记录」一致
- 本计划文件在 Task 7 单独提交（`docs/superpowers/` 被 gitignore，需 `git add -f`）；`tmp/` 全部证据目录不提交

**第二天第一项（未解决项按优先级）**

1. U1/U2/U3 live 静默简化（014 月累计、011 排除 Metal、016 补零）—— 违反门槛 2，修复位点与建议见「Task 6 未解决项」，须先写失败测试
2. U8 hybrid dense 首次加载击穿 agent 5s 时限（需预缓存 dense 模型，或给 dense 加载独立超时/启动预热）
3. U4 009 / 012 未登记筛选的状态标签口径（`insufficient_evidence` vs `unsupported`）
4. U5 004 / 005 / 006 来源改写仍过度澄清
5. U9 `composer.py` 义务覆盖分支早于 `local_answer`，`basic-rag-005` 仍走表格原文（Task 5 已记录）
6. U6 008 证据不足、U7 013 外部 API 复测、U10 全量单测 15 项历史失败（只记录不修）

**回退说明**

- 单任务回退：`git revert <sha>`（sha 见「提交记录」），或 `git reset --hard <sha>^`
- 整体回退：`git reset --hard checkpoint/preliminary-day1-before`（回到 `a88fc9d`，丢弃 Task 0-7 全部改动，需用户确认）
- `f473a48` 是用户自行提交的前端工作区改动，不属本计划任何 Task，回退时勿动

---

## 执行约束

- agent 必须使用独立分支或 worktree；不覆盖用户已有的前端未提交文件。
- 不修改 data/knowledge、正式 eval/agent 原题库、third_party、.env。
- 不把 holdout gold 传给运行时，不为单题写固定答案。
- 每个 Task 先写失败测试，再实现，再运行回归，再单独提交。
- 若已有测试失败，先记录基线；不允许把新增失败带入下一 Task。
- 默认保持 RAG_FUSION_MODE=lexical；hybrid 只作实验开关。

## 第一日验收门槛

1. 英国过滤在模型超时、HTTP 失败、格式错误时不能丢失，也不能返回全音频 answered。
2. “每月累计”不能降级成季度单行。
3. 以下三类改写进入 RAG：客户分层金额界线、活动截止时间和材料、客单价与成交均价定义。
4. 基础 RAG 保持 15/15，lexical 离线总分不下降。
5. 新增语义安全测试、换词测试全部通过。
6. hybrid 端到端若比 lexical 少超过 2 题，则不切默认。

## Task 0：冻结基线和建立封存集

**Files**

- Create: tmp/preliminary-day1-baseline/
- Create: eval/agent/eval_preliminary_day1.jsonl
- Create: eval/human/eval_preliminary_day1.md
- Create: scripts/score_preliminary_day1.py

### Step 1：保存基线

~~~powershell
New-Item -ItemType Directory -Force -Path tmp/preliminary-day1-baseline | Out-Null
git status --short | Tee-Object tmp/preliminary-day1-baseline/git-status.txt
git rev-parse HEAD | Tee-Object tmp/preliminary-day1-baseline/head.txt
git diff --stat | Tee-Object tmp/preliminary-day1-baseline/diff-stat.txt
git tag -f checkpoint/preliminary-day1-before
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --files eval_basic_rag --mode offline --quiet --out tmp/preliminary-day1-baseline/basic-rag.json
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/preliminary-day1-baseline/offline-all.json
~~~

将实际通过数和运行配置写入 tmp/preliminary-day1-baseline/README.txt。

### Step 2：保存专项失败集合

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_fast_rag services.agent_api.tests.test_rag_source_plan services.agent_api.tests.test_route_policy services.agent_api.tests.test_semantic_gate services.agent_api.tests.test_query_rewrite services.agent_api.tests.test_entity_linking > tmp/preliminary-day1-baseline/targeted-tests.log 2>&1
~~~

把 FAILED 测试名称复制到 known-failures.txt。不要先修改代码。

### Step 3：建立 20 道 holdout

eval_preliminary_day1.jsonl 每行包含 id、question、capability、expected_status、expected_route、expected_documents、requirements。题目分布固定为：

- 4 道规则改写：D04、D11、D02、D01；
- 4 道来源改写：默认音乐范围、分类边界、选品、月度比较；
- 4 道语义安全：英国、USA、排除 Metal、未知艺术家；
- 4 道粒度安全：按月、累计、Top-N、无成交；
- 4 道拒答/澄清：利润、缺年份、未知指标、活动转化率。

评分脚本必须单独输出 silent_simplification、missing_evidence、over_clarification。至少把“一行代替多行”“季度代替月份”“SQL 没有用户过滤”“不支持却 answered”判为失败。

### Step 4：提交

~~~powershell
git add eval/agent/eval_preliminary_day1.jsonl eval/human/eval_preliminary_day1.md scripts/score_preliminary_day1.py
git commit -m "test: add preliminary day-one holdout evaluation"
~~~

## Task 1：记录原问题义务

**Files**

- Create: services/agent_api/app/agent/query_obligations.py
- Create: services/agent_api/tests/test_query_obligations.py
- Modify: services/agent_api/app/agent/state.py
- Modify: services/agent_api/app/agent/parsing.py

### Step 1：写失败测试

测试以下四个行为：

~~~python
def test_unknown_country_is_unresolved():
    result = extract_query_obligations("2025年第三季度英国音频销售额是多少？", "chinook-music")
    assert result.has_unresolved_condition
    assert "英国" in result.unresolved_terms

def test_running_total_requires_month_rows():
    result = extract_query_obligations("2025年第三季度每个月销售额累计到当月是多少？", "chinook-music")
    assert result.requires_month_grouping
    assert result.requires_running_total
    assert result.requires_multiple_rows

def test_registered_usa_is_consumed():
    result = extract_query_obligations("2025年第三季度 USA 音频销售额是多少？", "chinook-music")
    assert not result.has_unresolved_condition
    assert "Country:USA" in result.consumed_entities

def test_rule_question_does_not_need_transaction_year():
    result = extract_query_obligations("客单价是按销售额除以订单数吗？", "chinook-music")
    assert not result.requires_transaction_period
~~~

本仓库没有把 pytest 作为稳定依赖时，使用 unittest.TestCase 等价实现。先运行新测试，确认 RED。

### Step 2：实现纯函数和模型

query_obligations.py 只解析问题，不调用模型、SQL 或检索。定义：

~~~python
@dataclass(frozen=True)
class QueryObligations:
    consumed_entities: tuple[str, ...] = ()
    unresolved_terms: tuple[str, ...] = ()
    required_grouping: tuple[str, ...] = ()
    requires_month_grouping: bool = False
    requires_running_total: bool = False
    requires_rank: bool = False
    requested_limit: int | None = None
    requires_multiple_rows: bool = False
    requires_transaction_period: bool = True

    @property
    def has_unresolved_condition(self) -> bool:
        return bool(self.unresolved_terms)
~~~

规则：

- 每月、按月份、月度 → 月分组；
- 累计到当月、截至当月、滚动累计 → running total；
- 最高的五个、Top 5、前五 → rank 和 limit；
- SQL/跨源事实问题调用现有 _unparsed_query_text，把未消费过滤词放入 unresolved；
- 规则/定义问题不因未登记业务词而阻断，并将 requires_transaction_period 设为 False；
- 不能把音频、销售额、查询、是多少等已注册词误判为 unresolved。

在 BusinessTask 增加 query_obligations 字典字段。parse_question 返回 ready task 前写入该字段；旧 checkpoint 缺失时使用空字典。

### Step 3：验证并提交

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_query_obligations services.agent_api.tests.test_query_rewrite services.agent_api.tests.test_semantic_ir -v
git diff --check
git add services/agent_api/app/agent/query_obligations.py services/agent_api/app/agent/state.py services/agent_api/app/agent/parsing.py services/agent_api/tests/test_query_obligations.py
git commit -m "feat: record query obligations before tool execution"
~~~

## Task 2：修复规则问题的过度澄清

**Files**

- Modify: services/agent_api/app/agent/route_policy.py
- Modify: services/agent_api/app/agent/parsing.py
- Modify: services/agent_api/app/knowledge/source_plan.py
- Create: services/agent_api/tests/test_retrieval_first_clarification.py

### Step 1：先写四个回归测试

测试：

~~~python
def test_customer_threshold_paraphrase_routes_to_rag():
    turn = parse_default("给顾客分三档时，各档的消费金额界线分别是多少？")
    assert turn.decision == "ready"
    assert turn.task.route == "rag"
    assert "year" not in turn.blocking_slots

def test_poster_deadline_paraphrase_routes_to_rag():
    turn = parse_default("参加音乐专题征集最迟哪天交，得准备哪些东西？")
    assert turn.decision == "ready"
    assert turn.task.route == "rag"

def test_aov_definition_routes_to_rag():
    turn = parse_default("平均一笔交易卖多少钱，除以件数还是成交笔数？没有成交怎么办？")
    assert turn.decision == "ready"
    assert turn.task.route == "rag"

def test_actual_sales_without_year_still_clarifies():
    turn = parse_default("第三季度音频销售额是多少？")
    assert turn.decision == "clarification"
    assert "year" in turn.blocking_slots
~~~

parse_default 必须直接创建默认 profile 和 SessionMemory，不启动服务、不读 gold。

### Step 2：调整路由和来源角色

在 route_policy.py 增加方法问题标记：界线、分三档、截止、最迟、提交材料、准备哪些、除以、还是、没有成交、零订单、合理吗、应该怎么处理。

命中这些词且没有明确“数据库实际值”请求时，返回 document_rule/rag。出现实际销售额、数据库查询、查数等词时仍保留 SQL 或 cross_source。

在 parsing.py 确保 route=rag 的规则问题不执行交易年份必填检查；实际事实查询继续要求年份。

在 source_plan.py 支持以下语义候选：

- 客户阈值 → D04；
- 报名/截止/提交材料 → D11；
- 客单价/成交均价/除以件数或订单数 → D02；
- 分类界线/四档候选 → D03 或 D05。

### Step 3：测试和提交

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_retrieval_first_clarification services.agent_api.tests.test_route_policy services.agent_api.tests.test_rag_source_plan services.agent_api.tests.test_agent_task_policy -v
git diff --check
git add services/agent_api/app/agent/route_policy.py services/agent_api/app/agent/parsing.py services/agent_api/app/knowledge/source_plan.py services/agent_api/tests/test_retrieval_first_clarification.py
git commit -m "fix: route paraphrased document questions before clarification"
~~~

## Task 3：阻断 SQL 失败降级的静默简化

**Files**

- Modify: services/agent_api/app/tools/models.py
- Modify: services/agent_api/app/adapters/c_sql.py
- Modify: backend/sql_module/service.py
- Modify: services/agent_api/app/agent/graph.py
- Create: services/agent_api/tests/test_query_obligation_fallback.py

### Step 1：写失败测试

使用总是抛出 MODEL_TIMEOUT 的 TimeoutModel，测试：

~~~python
async def test_unknown_filter_never_answers_from_broad_fallback():
    answer = await ask_with_model("2025年第三季度英国音频销售额是多少？")
    assert answer.status != "answered"

async def test_running_total_never_answers_with_quarter_scalar():
    answer = await ask_with_model("2025年第三季度每个月销售额累计到当月是多少？")
    assert answer.status != "answered"

async def test_registered_country_filter_can_use_fallback():
    answer = await ask_with_model("2025年第三季度 USA 音频销售额是多少？")
    assert answer.status == "answered"
    assert answer.sql_results[0].rows[0]["sales_amount"] == 19.8
~~~

### Step 2：传递执行路径和义务

SqlTaskOutcome.diagnostics 与 C 草稿响应增加可选 execution_path：

- model_candidate；
- controlled_registered_query；
- controlled_fallback；
- none。

SqlTaskRequest、CContext、build_c_request、backend validate_request 增加 query_obligations。只允许 unresolved_terms、requires_month_grouping、requires_running_total、requires_rank、requested_limit、required_grouping、requires_multiple_rows。

旧响应缺字段时按 none 处理；不把整份 BusinessTask 序列化给 C。

### Step 3：校验受控查询覆盖

backend/sql_module/service.py 增加 covers_query_obligations(result, obligations)：

- unresolved_terms 非空时返回 False；
- requires_month_grouping 必须存在月分组结构；
- requires_running_total 必须存在窗口/累计结构；
- requires_rank 和 requested_limit 必须保留 LIMIT 与排序；
- 同时复用现有 query_intent、SQLGlot 和只读检查；
- 不确定时拒绝，不放宽校验。

模型失败后的 controlled_fallback 若不覆盖义务，返回 UNSUPPORTED_CAPABILITY；graph compose 发现 controlled_fallback 且义务未覆盖时，最终状态必须为 clarification_required、unsupported 或 error，不能是 answered。

### Step 4：测试和提交

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_query_obligation_fallback services.agent_api.tests.test_stage2_sql_semantics services.agent_api.tests.test_fast_sql -v
git diff --check
git add services/agent_api/app/tools/models.py services/agent_api/app/adapters/c_sql.py backend/sql_module/service.py services/agent_api/app/agent/graph.py services/agent_api/tests/test_query_obligation_fallback.py
git commit -m "fix: reject unsafe sql fallback simplification"
~~~

## Task 4：接入受保护的 hybrid 检索，不改变默认 lexical

**Files**

- Create: services/agent_api/app/knowledge/retrieval_confidence.py
- Create: services/agent_api/tests/test_retrieval_confidence.py
- Create: services/agent_api/tests/test_guarded_hybrid_day1.py
- Modify: services/agent_api/app/knowledge/index.py
- Modify: services/agent_api/app/knowledge/source_plan.py

### Step 1：写置信度测试

~~~python
def test_required_source_and_channel_agreement_are_high():
    result = assess_retrieval(
        query_kind="fact",
        lexical_docs=["D02", "D02", "D01"],
        dense_docs=["D02", "D02", "D01"],
        required_docs=["D02"],
        top_score=0.78,
        second_score=0.51,
        has_formula=False,
        has_comparison=False,
    )
    assert result.level == "high"
    assert not result.requires_model

def test_conflicting_channels_fall_back_to_lexical():
    result = guarded_fusion(
        lexical_docs=["D02", "D02", "D03"],
        dense_docs=["D08", "D08", "D09"],
        lexical_scores=[0.80, 0.70, 0.60],
        dense_scores=[0.82, 0.81, 0.80],
        required_docs=["D02"],
    )
    assert result.mode == "lexical_fallback"
    assert result.docs[0] == "D02"

def test_complex_query_is_never_no_call_only_by_score():
    result = assess_retrieval(
        query_kind="cross_source",
        lexical_docs=["D01", "D06"],
        dense_docs=["D01", "D06"],
        required_docs=["D01", "D06"],
        top_score=0.90,
        second_score=0.30,
        has_formula=True,
        has_comparison=True,
    )
    assert result.requires_model
~~~

### Step 2：实现保守规则

- 公式或跨源比较：requires_model=True，不能只因高分走本地答案；
- 两路来源集合有足够一致且必要来源覆盖：high；
- 必要来源未知时 coverage_complete=False；
- 只在同一分数空间内计算 margin，不混用 RRF 和 cosine 阈值。

在 KnowledgeIndex.retrieve 中保持：

- lexical：旧排序和旧 hints 不变；
- rank/hybrid：只有显式开关生效；
- dense 与 lexical 冲突、必要来源缺失或域外时回退 lexical；
- 语义召回必须再过 required_docs、fact_role 和 core_content_missing；
- 把 retrieval_mode、retrieval_confidence、fallback_reason 写入 diagnostics/trace。

### Step 3：测试和提交

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_retrieval_confidence services.agent_api.tests.test_guarded_hybrid_day1 services.agent_api.tests.test_knowledge_index services.agent_api.tests.test_fast_rag -v
.venv\Scripts\python.exe scripts/run_agent_eval.py --files eval_basic_rag --mode offline --quiet --out tmp/preliminary-day1-baseline/basic-rag-after-hybrid.json
~~~

基础 RAG 不是 15/15 时，保留代码但默认继续 lexical，停止扩大 hybrid 修改。

~~~powershell
git diff --check
git add services/agent_api/app/knowledge/retrieval_confidence.py services/agent_api/app/knowledge/index.py services/agent_api/app/knowledge/source_plan.py services/agent_api/tests/test_retrieval_confidence.py services/agent_api/tests/test_guarded_hybrid_day1.py
git commit -m "feat: add guarded hybrid retrieval confidence"
~~~

## Task 5：让三类换词问题产生简短、可追溯答案

**Files**

- Modify: services/agent_api/app/response/fast_renderer.py
- Modify: services/agent_api/app/response/document_answer.py
- Modify: services/agent_api/tests/test_fast_rag.py
- Modify: services/agent_api/tests/test_document_answer.py

### Step 1：写答案契约测试

使用真实格式 DocumentChunk fixture，不调用模型：

~~~python
def test_customer_threshold_answer_is_short_and_cited():
    answer = local_answer("给顾客分三档时，各档的消费金额界线分别是多少？", [d04_threshold_chunk()])
    assert answer is not None
    assert "A" in answer and "12.00" in answer
    assert len(answer) < 900
    assert "D04" in answer

def test_poster_answer_contains_deadline_and_materials():
    answer = local_answer("参加音乐专题征集最迟哪天交，得准备哪些东西？", [d11_poster_chunk()])
    assert "10月13日18:00" in answer
    assert "商品清单" in answer and "页面文案" in answer
    assert len(answer) < 900

def test_aov_answer_distinguishes_orders_and_units():
    answer = local_answer("平均一笔交易卖多少钱，除以件数还是成交笔数？没有成交怎么办？", [d02_metric_chunk()])
    assert "订单数" in answer and "零订单" in answer
    assert "需要补充年份" not in answer
~~~

### Step 2：实现按来源角色的最小答案

在 fast_renderer.py 增加 answer_role_from_question(question)，返回 customer_segmentation、activity_plan、metric_definition 或 generic_excerpt。只依据问题语义和 rag_source_plan，不读取评测 expected。

答案必须：

- 先结论，再一句口径说明，再写依据：[Dxx]；
- 表格只保留问题需要的列；
- 不把整张 OCR 海报或无关文档拼进正文；
- 证据不足返回 None，交给 insufficient_evidence；
- 原文片段继续由 EvidencePanel 展示。

### Step 3：测试和提交

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_fast_rag services.agent_api.tests.test_document_answer services.agent_api.tests.test_multidoc_coverage -v
.venv\Scripts\python.exe scripts/run_agent_eval.py --files eval_basic_rag --mode offline --quiet --out tmp/preliminary-day1-baseline/basic-rag-after-answer.json
git diff --check
git add services/agent_api/app/response/fast_renderer.py services/agent_api/app/response/document_answer.py services/agent_api/tests/test_fast_rag.py services/agent_api/tests/test_document_answer.py
git commit -m "feat: render concise evidence-grounded paraphrase answers"
~~~

## Task 6：运行一天封存集、模型故障集和评委式冒烟

**Files**

- Modify: scripts/score_preliminary_day1.py（仅当报告字段需要适配）
- Create: tmp/preliminary-day1-final/（不提交）
- Create: tmp/preliminary-day1-final/README.txt（不提交）

### Step 1：离线 holdout

~~~powershell
New-Item -ItemType Directory -Force -Path tmp/preliminary-day1-final | Out-Null
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --files eval_preliminary_day1 --mode offline --quiet --out tmp/preliminary-day1-final/offline-holdout.json
.venv\Scripts\python.exe -X utf8 scripts/score_preliminary_day1.py --report tmp/preliminary-day1-final/offline-holdout.json --out tmp/preliminary-day1-final/offline-score.json
~~~

### Step 2：检索对照

如果仓库包含 scripts/eval_retrieval_recall.py 和 embedding 缓存：

~~~powershell
.venv\Scripts\python.exe scripts/eval_retrieval_recall.py --mode lexical --out tmp/preliminary-day1-final/recall-lexical.json
.venv\Scripts\python.exe scripts/eval_retrieval_recall.py --mode hybrid --out tmp/preliminary-day1-final/recall-hybrid.json
~~~

记录 doc_any、doc_top1、doc_all，但不得把它们写成答案准确率。

### Step 3：在线定向集

~~~powershell
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --files eval_preliminary_day1 --mode live --quiet --out tmp/preliminary-day1-final/live-holdout.json
~~~

记录 MODEL_TIMEOUT、MODEL_HTTP_FAILED、MODEL_OUTPUT_INVALID；不要把 pending 或 controlled fallback 当作模型通过。

### Step 4：强制模型失败反例

用 TimeoutModel 检查：

- 英国筛选不能返回全音频总额；
- 每月累计不能返回季度单行；
- USA 已登记筛选仍能得到 19.80 美元；
- 利润/转化率明确拒答；
- 缺年份实际查询明确澄清。

保存 tmp/preliminary-day1-final/failure-injection.json，不把注入结果写成 live 成绩。

### Step 5：网页和后端验证

~~~powershell
.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_query_obligations services.agent_api.tests.test_retrieval_first_clarification services.agent_api.tests.test_query_obligation_fallback services.agent_api.tests.test_retrieval_confidence services.agent_api.tests.test_guarded_hybrid_day1 services.agent_api.tests.test_fast_rag services.agent_api.tests.test_document_answer -v
Push-Location services/web
npm run verify
Pop-Location
~~~

前端只做成功、澄清、拒答、错误和证据展开冒烟；发现用户已有未提交文件相关差异时停止修改。

### Step 6：生成验收记录

tmp/preliminary-day1-final/README.txt 必须列出：

- commit、代码/题库/数据 hash；
- 原始基础 RAG、封存换词、语义安全、负例分数；
- silent simplification 数量；
- 模型调用数和失败数；
- p50/p95/p最大耗时；
- 新增通过、退化和未解决题目；
- 是否允许把 hybrid 设为默认。

## Task 7：默认模式决策与收尾

只有全部满足才允许将 hybrid 作为默认：

- 基础 RAG 仍为 15/15；
- lexical 离线全量不下降；
- hybrid 端到端不低于 lexical；
- 封存换词题至少 80%，且没有静默简化；
- 新增测试无失败；
- 证据引用仍可在网页展开。

任一条件不满足：

- 默认保持 lexical；
- hybrid 保留显式实验开关；
- 只允许通过证据覆盖检查的语义补救进入回答；
- 在验收记录中写明边界。

最终检查：

~~~powershell
git status --short
git diff --check
git log --oneline -8
~~~

计划文件单独提交：

~~~powershell
git add docs/superpowers/plans/2026-10-15-preliminary-day1.md
git commit -m "docs: define preliminary day-one agent implementation plan"
~~~

## 第一天不能做的事

- 不接 AdventureWorks 正式迁移；最多读取现有题库确认接口未退化。
- 不接 OHR-Bench 全量；扫描 PDF、版面质量和 OCR 质量评估属于后续专项。
- 不全面删除词表和规则；它们继续负责安全边界与业务口径。
- 不为了提高改写集分数逐题新增别名或固定答案。
- 不把检索层召回率、离线固定模型结果或单次 live 最好成绩写成最终能力。

