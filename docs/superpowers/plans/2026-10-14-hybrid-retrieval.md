# 混合检索（词法 + 语义 RRF 融合）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 D01–D12 知识检索从「硬编码关键词 + 绝对覆盖率门槛」改成「词法 ∪ 语义 RRF 融合 + 排名截断」，让同义改写问法的 gold 文档召回率从 0/15 提升到 60% 以上，同时原句召回和全量评测不回归。

**Architecture:** 检索分两阶段。Phase A 只做词法打分，不加任何门槛，产出完整的 `lexical` 排名；Phase B 按融合模式决定候选池——`lexical` 模式沿用今天的绝对覆盖率门槛，`rank` 模式换成「词法 top-N ∪ 契约文档」的排名截断，`hybrid` 模式再并入预计算向量的语义排名并用 RRF（`Σ 1/(60+rank)`）融合。业务加分从「百万级绝对量」重新标定为「RRF 量级」，`fact_role` 命中和结构化表格命中改为整数优先级层，避免量级互相淹没。向量在离线脚本里预计算并落盘，运行时只加载校验、绝不重算，冷启动从 55 秒降到 0.3 秒以内。

**Tech Stack:** Python 3.12、fastembed 0.7.4（`BAAI/bge-small-zh-v1.5`，512 维，输出已 L2 归一化）、stdlib `array`/`json`（运行时不引入 numpy）、Pydantic v2、unittest、`scripts/run_agent_eval.py`。

---

## 一、问题定位（已实测，不是推测）

基线数据来自 `tmp/probe-clean.log`（各通道天花板，top_k=5，15 题 × 3 档问法）与本次现场验证。

### 1.1 端到端召回：改写一换词就归零

| 问法 | 端到端 gold 召回 | 数据来源 |
|---|---:|---|
| orig（题库原句） | 15/15 | `tmp/probe-clean-lex.json` |
| t1（同义替换） | 0/15 | 同上 |
| t2（口语重排） | 0/15 | 同上 |

**但 0/15 是两个独立故障叠加出来的，不能全算在检索头上。** 同一份数据里的路由分布：

| 问法 | 进 rag（真的检索了） | 进 clarification（没检索） | 进 sql（没检索） |
|---|---:|---:|---:|
| orig | 15 | 0 | 0 |
| t1 | 5 | 8 | 2 |
| t2 | 5 | 10 | 0 |

t1 有 8/15、t2 有 10/15 **根本没走到检索**，被上游路由判成「需要澄清」或「走 SQL」直接短路。真正落到 `retrieve()` 的只有 5 题，而这 5 题的 `retr_any` 全是 `false`（检索门槛清空候选）。

**这条决定了本计划的验收口径：** Task 0–6 只改检索层，`scripts/eval_retrieval_recall.py` 直接调 `index.retrieve()`、绕过路由，所以它测的是「检索层泛化」。端到端改写正确率的上限被路由卡死在 5/15（t1）与 5/15（t2），**不会因为本计划做完就涨到 60%**。路由与上游规划的泛化属于方案 C，见 1.6。

### 1.2 各通道天花板：语义通道明显更强

`tmp/probe-clean.log` 同时给了两种口径，**必须分清，否则验收线会定错**：

- **chunk_any@5**：取排序后前 5 个**片段**，看它们的 `doc_id` 是否命中 gold。这才是 `retrieve(top_k=5)` 的真实语义（`index.py:433` 就是 `ranked[:request.top_k]`），Task 0 的评测脚本用的就是它。
- **doc_any@5**：把整条排序的 `doc_id` 去重后取前 5 个**文档**。它宽松得多——某文档占满前 5 个片段时，chunk_any 只算 1 个文档命中，doc_any 却能算 5 个。

| 通道 | chunk_any（orig / t1 / t2） | chunk_any 合计 | doc_any（orig / t1 / t2） | doc_any 合计 |
|---|---|---:|---|---:|
| lex（去掉门槛的纯词法） | 8/15 · 10/15 · 5/15 | 23/45 = 51.1% | 10/15 · 11/15 · 6/15 | 27/45 = 60.0% |
| dense（纯语义 bge-small-zh） | 13/15 · 11/15 · 8/15 | 32/45 = 71.1% | 14/15 · 12/15 · 8/15 | 34/45 = 75.6% |
| RRF（词法 + 语义，k=60） | 14/15 · 12/15 · 8/15 | 34/45 = 75.6% | 14/15 · 12/15 · 9/15 | 35/45 = 77.8% |

**验收线一律按 chunk_any 定**，因为那是线上真正返回的东西。RRF 的真实天花板是 **34/45 = 75.6%**，不是 77.8%（此前按 doc_any 报的 77.8% 会高估约 2 个点）。

关键读法：**去掉门槛的纯词法在 t1 上 chunk_any 有 10/15，而端到端只有 0/15。** 说明改写问法失分的主因不是「没有语义检索」，而是词法通道上的门槛把候选全清空了。语义融合是把天花板从 51.1% 抬到 75.6% 的第二步，不是第一步。

> 线上还有一个纯排序达不到的补偿机制：`index.py:382-392` 会为每个 `required_sources` / `hinted_docs` 文档强制挑一个最佳片段插到队首。所以真实的文档覆盖率高于 chunk_any 天花板——**这也正是 Task 3/4 绝不能删掉这段强制选择的原因**，删了 orig 立刻崩。

### 1.3 真正的元凶：0.35 绝对覆盖率门槛

`services/agent_api/app/knowledge/index.py:313-315`：

```python
if not role_hit and (not hinted_doc and item.record.doc_id not in required_sources
        and query_weight and sum(self._weight(term) for term in matched) / query_weight < 0.35):
    continue
```

现场实测（`max_chars=800`，`RAG_ENABLE_DENSE=0`）：

```text
basic-rag-001 orig: pipeline_chunks=5 docs=['D01','D01','D06','D06','D06'] | gate_pass=20/231
basic-rag-001 para: pipeline_chunks=5 docs=['D01','D01','D06','D06','D11'] | gate_pass=0/138
basic-rag-005 orig: pipeline_chunks=5 docs=['D04','D04','D04','D04','D04'] | gate_pass=0/290
basic-rag-005 para: pipeline_chunks=0 docs=[]                              | gate_pass=0/208
```

三条结论：

1. 门槛的通过率是 `0/290`、`0/208`、`0/138` 这个量级——**真实问法几乎全部落在门槛之外**。`gate_pass=20/231` 是原句的最好情况。
2. 现在还能召回，靠的是 `not hinted_doc` 这个短路：一旦 `hinted_docs` 非空，门槛被整体跳过。而 `hinted_docs` 由 `index.py:255-303` 约 40 条硬编码关键词规则构造（`"指标"→D01`、`"分层"→D04`、`"报名"→D11`…）。`basic-rag-001 para` 的原生词法 top5 是 `D08/D12/D07/D05/D04`，gold `D01` 根本不在里面，它能排第一纯粹因为改写句里还留着「指标」两个字。
3. 改写句一旦绕开硬编码关键词，`hinted_docs` 为空 → 门槛生效 → `gate_pass=0` → 返回空 chunks → 上层判 `insufficient_evidence`。这就是 0/15 的成因。

`basic-rag-005 para` 尤其说明问题：原生词法 top5 distinct 是 `D03/D04/D08/D05/D07`，**gold `D04` 就排在第 2 位**，但门槛把它连同其他 207 个候选一起清空了。

### 1.4 次要问题清单

| # | 位置 | 问题 | 本计划处理 |
|---|---|---|---|
| a | `index.py:309` | `if not matched: continue`，零词法命中直接出局，语义无法兜底 | Task 5 |
| b | `index.py:322` | `score += dense * 10.0`，量级 ~6，对 `+1_000_000`/`+2_000_000` 完全无效——语义通道即使打开也是死的 | Task 4 |
| c | `index.py:145-147` | `except Exception: self._dense_failed = True; return {}`，静默吞掉一切异常，线上无法区分「没开」和「挂了」 | Task 2 |
| d | `index.py:126-136` | 首次查询现场编码整个语料（432 片段 @ `max_chars=800`），实测 **54.61s**；而模型初始化只要 0.19s、单次查询编码 0.03s | Task 1 |
| e | `index.py:471` | `knowledge_snapshot_version()` 只含 doc sha256+size，**不含 `max_chars`**；而生产用 1200（425 片段）、测试用 800（432 片段），同一快照版本会对应两套 chunk_id | Task 1 |

实测冷启动分解（模型已本地缓存）：

```text
import         1.25s
model init     0.19s
1 query        0.00s   (约 30ms)
index load     7.08s
432 chunks    54.61s   <- 冷启动全部成本在这里
dim 512  norm 1.0000001  <- fastembed 输出已 L2 归一化，cosine 即点积
```

切片参数与片段数的对应关系已现场复核（`load_knowledge_index` 三次，快照版本都是 `d7dac902594b...`）：

```text
max_chars  800 -> 432 片段   （services/agent_api/tests/test_knowledge_index.py 用这个）
max_chars 1000 -> 427 片段   （load_d12_index 的默认值，本计划不涉及）
max_chars 1200 -> 425 片段   （生产：sql_d12.py:134 -> load_knowledge_index）
```

**三者的 `knowledge_snapshot_version()` 完全相同**，这就是问题 e 的实证：只靠快照哈希无法区分切片参数，向量会静默错位。Task 1 的文件名因此必须带 `-m<max_chars>`，加载时还要逐条比对 `chunk_ids`。

### 1.5 不在本计划范围内（方案 C）

`index.py` 打分循环里的题目级硬编码，本计划**只做量级重标定，不删除**，以保证 `lexical` 模式行为逐位不变：

`role_hit +2_000_000`、`hinted_docs +1_000_000`、`doc_id=="D09"` + Rock + 覆盖 `+400`、`D06 page==2 +150`、`D06 目标明细 +200`、`D08 page==2 +100`、答案数值 `"12.00"`/`"5.00"` `+100`、workflow `customer_segmentation` 下 `'层级 | 名称 | 当季音频销售额'` `+3000/+2900/+2800`、`poster_candidates` `+3000`、facets 里硬编码日期 `10月13日18:00` / `2025.*10.*20.*11` / `Rock主页面` / `Metal延伸栏目`。

这些是「针对已知题库调参」的产物，去掉它们属于泛化专项（方案 C），需要单独的计划和单独的验收集。本计划先把检索机制换成可泛化的形态，让后续删硬编码有可测量的落脚点。

### 1.6 检索层之外还有两层硬编码（方案 C 范围，本计划不动）

把 `index.py` 的门槛换掉，只解决了三层里的最内一层。完整链路是：

| 层 | 位置 | 硬编码形态 | 改写问法下的后果 |
|---|---|---|---|
| 路由 | `agent/semantic_router.py`、`agent/task_policy.py` | 意图/澄清判定靠关键词 | t1 有 8/15、t2 有 10/15 直接判 `clarification_required`，检索根本没被调用 |
| 规划 | `knowledge/source_plan.py:36-62`（`ROLE_TERMS` / `ROLE_QUERY_TERMS`，约 60 条词表） | 关键词 → `required_docs` / `primary_docs` / `query_terms` | 绕开词表后 `SourcePlan` 为空，`filters` 里没有任何文档约束，检索完全靠打分 |
| 检索 | `knowledge/index.py:246-303`（`hints` / `topics`，约 40 条）+ `:317-318`（0.35 门槛） | 关键词 → `hinted_docs` → 百万级加分 + 强制选片段 | 本计划处理的就是这一层 |

**这三层是串联的，最外层的通过率决定了端到端上限。** 只做本计划，改写集的端到端正确率最高只能到 5/15（t1）、5/15（t2）——因为另外 10/15、8/15 在路由就被拦掉了。要真正拿到「改写问法 60%+ 正确率」，必须在本计划之后再做一轮路由与 `source_plan` 的泛化（方案 C），并复用 Task 0 的评测集把三层分别量出来。

本计划的价值因此要说清楚：它不是「端到端 60%」的充分条件，而是**必要条件 + 可测量底座**。没有它，方案 C 改了路由也拿不到证据（检索层返回空）；有了它，方案 C 每一层的收益都能被单独量出来。

---

## 二、目标与验收

**口径统一为 chunk_any@5**（`retrieve(top_k=5)` 返回的 5 个片段的 doc_id 去重后是否命中 gold），这是 `scripts/eval_retrieval_recall.py` 实际算出来的东西。doc_any 只作参考，不作验收线。

| 指标 | 当前 | 目标 | 天花板 | 验收方式 |
|---|---:|---:|---:|---|
| 改写集 orig chunk_any | 15/15 | 15/15（不得回归） | — | `scripts/eval_retrieval_recall.py --mode hybrid` |
| 改写集 t1 chunk_any | 0/15 | ≥ 9/15 | 12/15 | 同上 |
| 改写集 t2 chunk_any | 0/15 | ≥ 6/15 | 8/15 | 同上 |
| 改写集三档合计 chunk_any | 15/45 = 33% | ≥ 30/45 = 66.7% | 34–35/45 | 同上 |
| offline 全量评测 | 91/119 | ≥ 91/119 | 119 | `scripts/run_agent_eval.py --all --mode offline` |
| 单元测试 | 15 failed / 439 passed（454 collected） | 不新增 failed | — | `pytest services/agent_api/tests -q` |
| 语义通道增量冷启动 | 54.6s | ≤ 0.1s | — | Task 1 Step 9 计时脚本 |
| 域外问题仍返回空 chunks | 是 | 是（不得回归） | — | `test_unknown_query_is_success_with_no_evidence` |

> 天花板一列来自 1.2 的纯排序 chunk_any。orig 之所以能到 15/15 而不是 14/15，是因为评测脚本走真实 `retrieve()`，`hints`/`topics` 与 `index.py:382-392` 的强制选片段仍然生效。t2 的目标定 6/15 而不是 8/15，是因为 8/15 就是天花板本身，留不出余量。

**明确不在验收范围内（写下来避免误判成功）**

| 指标 | 当前 | 本计划完成后 | 为什么 |
|---|---:|---:|---|
| 端到端改写集正确率（走完整 agent） | 0/15（t1）、0/15（t2） | 仍会很低，上限 5/15 | 路由层把 8/15（t1）、10/15（t2）判成 clarification，检索没被调用。见 1.6，属方案 C |

**灰度与回退**

- 环境变量 `RAG_FUSION_MODE` ∈ {`lexical`, `rank`, `hybrid`}，默认 `lexical`（= 今天的行为）。
- 回退分支已建：`checkpoint/pre-hybrid-retrieval-20261014` → `335e6d2`（= 本计划落地前的 HEAD）。回退命令：`git reset --hard checkpoint/pre-hybrid-retrieval-20261014`（**这条会丢未提交改动，执行前先 `git status` 确认**）。
- 运行时回退不需要动 git：`RAG_FUSION_MODE=lexical` 就是今天的行为，Task 2–6 的每一步都保持这个默认值可用。
- 任何一步 offline 全量掉到 91 以下，先切回 `RAG_FUSION_MODE=lexical` 恢复，再排查，不要在红线上继续叠加改动。

**已知风险（事先说明）**

分数会先掉再爬回来。Task 3 去掉覆盖率门槛后，`lexical` 模式之外的候选池会变大，offline 全量很可能先掉几个点；Task 4 把 `hinted_docs` 从「百万级绝对优先」降为「RRF 量级加分」后，orig 也可能掉。所以每个 Task 都必须跑完整评测并记录数字，不允许「改完看着还行就提交」。

---

## 三、文件结构

**新建**

| 文件 | 职责 |
|---|---|
| `eval/agent/paraphrase_retrieval.jsonl` | 15 题 × 3 档问法的检索改写评测集（数据，不含逻辑） |
| `scripts/eval_retrieval_recall.py` | 跑改写集，按 variant × mode 输出 doc_any/doc_top1/doc_all |
| `services/agent_api/app/knowledge/embeddings.py` | 向量存储的落盘格式、加载与校验。只管 I/O，不含检索逻辑 |
| `scripts/build_knowledge_embeddings.py` | 离线预计算向量并写盘（唯一会调用 fastembed 批量编码的地方） |
| `services/agent_api/app/knowledge/scoring.py` | 打分量级表（`BonusScale`）、请求上下文（`BonusContext`）、业务加分、RRF 融合。纯函数，不碰索引 I/O |
| `scripts/measure_dense_floor.py` | 实测域内/域外余弦分布，标定 `DENSE_FLOOR`。换语料或换模型后必须重跑 |
| `services/agent_api/tests/test_knowledge_embeddings.py` | 向量存储往返、快照/切片不匹配拒绝加载 |
| `services/agent_api/tests/test_dense_retrieval.py` | 合成向量下的语义打分、降级日志、负分防护 |
| `services/agent_api/tests/test_retrieval_scoring.py` | 打分纯函数：LEGACY 逐位复刻、RRF 量级、优先级层、融合模式解析 |
| `services/agent_api/tests/test_hybrid_retrieval.py` | 融合模式行为：门槛替换、RRF 排序、零命中兜底、域外仍为空 |

**修改**

| 文件 | 改动 |
|---|---|
| `services/agent_api/app/knowledge/index.py` | 两阶段打分（Phase A 词法基础分 → Phase B 候选池 → Phase C 加分 → Phase D 排序截断）、`RAG_FUSION_MODE` 分支、`_dense_scores` 改用预计算存储、显式降级日志、`ScoredChunk` 三元组、补 `LOGGER` |
| `pyproject.toml` | 无新增依赖（运行时用 stdlib `array`；fastembed 已声明） |

> `sql_d12.py` **不需要改**：`max_chars` 已经通过 `load_knowledge_index(root, profile, max_chars=max_chars)` 传进来了（`sql_d12.py:148`），Task 1 在 `load_knowledge_index` 内部把它转给向量校验即可。

**设计取舍**

- 运行时不引入 numpy。425 × 512 的 float32 用 stdlib `array("f")` 读取（870,400 字节，约 850 KB），点积用纯 Python，实测单查询 0.027s，够用。构建脚本里 numpy 只是 fastembed 的返回类型，用 `.tolist()` 转掉。
- 向量存**已归一化**的 float32 原始二进制，不存 `.npz`，避免把 numpy 变成运行时依赖。
- 文件名同时含快照哈希前 8 位和 `max_chars`，加载时再逐一比对 `chunk_ids`，三重校验，任一不符**拒绝加载**并记日志，绝不静默重算。

---

## 四、执行约束

- 分支：从 `codex/four-task-semantic-optimization` 的 `72cc36a` 继续。
- 每个 Task 先写失败测试，看到 RED 再实现 GREEN，每个 Task 独立提交。
- commit message 用英文，前缀 `feat:` / `fix:` / `docs:` / `test:`。
- **环境约束（每条命令都要）**：仓库路径含中文，PowerShell 命令前必须加
  `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8`；
  Python 一律用 `.venv\Scripts\python.exe -X utf8`。
- `rg` 的 `dir/*.py` glob 在此环境报错，用目录路径代替。
- `tmp/` 已被 gitignore，评测产物写 `tmp/hybrid-t<N>/`。
- 不改评测器金标准，不改 `eval/agent/eval_*.jsonl`（只新增 `paraphrase_retrieval.jsonl`）。
- 离线评测约 15s，live 约 15min；Task 2–6 每个只跑 offline，Task 7 收尾跑一次 live。

**命名与既有术语的冲突（务必读）**

`hybrid` 这个词在本仓库已经被占用了：`profiles/models.py:93` 的 `mode: Literal["sql_only", "rag_only", "hybrid"]` 指的是「SQL + RAG 双能力」的 profile 运行模式，`chinook-music.json:5` 就是 `"mode": "hybrid"`。本计划引入的 `RAG_FUSION_MODE=hybrid` 指的是「词法 + 语义双通道融合」，两者毫无关系。

保留 `hybrid` 这个取值的理由：它是检索领域的通用术语，且环境变量前缀 `RAG_FUSION_` 已经把命名空间隔开。**但代码里禁止出现裸的 `mode == "hybrid"` 比较**——一律写 `fusion_mode() == FUSION_HYBRID`，用 `scoring.py` 里的常量，避免和 `profile.mode` 混读。

**其余约定**

- `index.py` 当前没有 logger（只 import 了 `dataclasses/hashlib/json/math/os/pathlib/re/unicodedata`）。Task 2 补 `import logging` 与 `LOGGER = logging.getLogger(__name__)`，降级一律走 `LOGGER.warning`，不用 `print`。
- `DocumentChunk.score` 契约是 `float | None = Field(default=None, ge=0)`（`contracts/models.py:179`）。**负分会直接抛 ValidationError**，不是被静默截断。全仓库只有 `services/web/src/components/evidence/DocumentEvidence.vue:16` 把它当展示字段渲染（旁边已标注「不是结论可信度」），没有任何阈值逻辑消费它——所以量级可以自由换，但 `ge=0` 这条硬约束必须在 Task 2 就守住（详见 Task 2 Step 7）。
- 换成 RRF 量级后，前端「检索分数」会从 `1000047.52` 这种数变成 `0.0234`，可读性反而变好，不需要改前端。

**常用命令**

```powershell
# 单元测试（全量约 3-5 分钟）
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests -q --no-header

# 单个测试文件
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_index.py -q

# offline 全量评测（约 15s）
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t3/report.json

# 只跑基础 RAG 题库
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --files eval_basic_rag --mode offline --quiet --out tmp/hybrid-t3/rag.json

# 改写集召回评测（Task 0 产出）
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode lexical
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode hybrid
```

---

## Task 0：改写检索评测集与召回评测脚本

**为什么先做这个：** 后面每个 Task 的验收都依赖它。今天改写集只存在于 gitignore 的 `tmp/probe_generalization.py` 里，而且那个文件的 `CASES` 变量已经被覆盖丢失（现存版本只有 `PAIRS`），`tmp/probe_clean.py`、`tmp/probe_1010.py`、`tmp/probe_rank_ceiling.py` 都还在 import 它——**这些探针现在已经跑不起来了**。所以第一件事是把评测集固化进仓库。

**Files:**
- Create: `eval/agent/paraphrase_retrieval.jsonl`
- Create: `scripts/eval_retrieval_recall.py`
- Test: `services/agent_api/tests/test_paraphrase_eval_set.py`

- [ ] **Step 1: 写 RED 测试（评测集自身的完整性约束）**

创建 `services/agent_api/tests/test_paraphrase_eval_set.py`：

```python
import json
from pathlib import Path
import unittest


EVAL_ROOT = Path(__file__).resolve().parents[3] / "eval" / "agent"
BASIC_RAG = EVAL_ROOT / "eval_basic_rag.jsonl"
PARAPHRASE = EVAL_ROOT / "paraphrase_retrieval.jsonl"
VARIANTS = ("orig", "t1", "t2")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ParaphraseEvalSetTests(unittest.TestCase):
    """改写集必须与基础 RAG 题库同源同 gold，否则召回数字不可比。"""

    def setUp(self):
        self.rows = read_jsonl(PARAPHRASE)
        self.baseline = {row["id"]: row for row in read_jsonl(BASIC_RAG)}

    def test_every_case_has_exactly_three_variants(self):
        grouped = {}
        for row in self.rows:
            grouped.setdefault(row["id"], []).append(row["variant"])
        self.assertEqual(len(grouped), 15)
        for case_id, variants in grouped.items():
            self.assertEqual(sorted(variants), sorted(VARIANTS), case_id)

    def test_original_question_and_gold_match_basic_rag_suite(self):
        for row in self.rows:
            if row["variant"] != "orig":
                continue
            source = self.baseline[row["id"]]
            self.assertEqual(row["question"], source["question"], row["id"])
            self.assertEqual(row["expected_documents"], source["expected_documents"], row["id"])

    def test_paraphrases_differ_from_original_and_stay_in_snapshot(self):
        by_case = {}
        for row in self.rows:
            by_case.setdefault(row["id"], {})[row["variant"]] = row
        for case_id, variants in by_case.items():
            original = variants["orig"]["question"]
            for name in ("t1", "t2"):
                question = variants[name]["question"]
                self.assertNotEqual(question, original, f"{case_id}/{name} 与原句相同")
                self.assertGreaterEqual(len(question), 10, f"{case_id}/{name} 过短")
                self.assertEqual(
                    variants[name]["expected_documents"], variants["orig"]["expected_documents"],
                    f"{case_id}/{name} gold 与原句不一致",
                )
            self.assertTrue(set(variants["orig"]["expected_documents"]) <= {f"D{i:02}" for i in range(1, 13)})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_paraphrase_eval_set.py -q`

Expected: FAIL，3 个用例全部 `FileNotFoundError: ... paraphrase_retrieval.jsonl`

- [ ] **Step 3: 创建评测集**

创建 `eval/agent/paraphrase_retrieval.jsonl`，45 行。orig 逐字取自 `eval/agent/eval_basic_rag.jsonl`，gold 与其 `expected_documents` 一致；t1 做同义替换（销售额→营收、销量→件数、订单数→单量、客户→顾客/买家、目标→指标值/任务额、阈值→门槛、品类→曲风、报名截止→提交时限、客单价→单客成交额），t2 做口语化重排。改写刻意绕开 `index.py:255-303` 的硬编码关键词，这样它才真的在测泛化。

```jsonl
{"id": "basic-rag-001", "variant": "orig", "question": "销售额、销量、订单数和购买客户数分别怎么计算？", "expected_documents": ["D01"]}
{"id": "basic-rag-001", "variant": "t1", "question": "营收、件数、单量和成交顾客数这四个指标的口径各是什么？", "expected_documents": ["D01"]}
{"id": "basic-rag-001", "variant": "t2", "question": "我们平时说的收入、卖出数量、下单笔数以及买过东西的人数，各自是按什么规则算出来的？", "expected_documents": ["D01"]}
{"id": "basic-rag-002", "variant": "orig", "question": "2025年第三季度的订单日期范围怎么写？", "expected_documents": ["D01"]}
{"id": "basic-rag-002", "variant": "t1", "question": "2025 Q3 的下单时间区间应该如何界定？", "expected_documents": ["D01"]}
{"id": "basic-rag-002", "variant": "t2", "question": "想筛今年七到九月的单子，时间条件该卡在哪两天之间？", "expected_documents": ["D01"]}
{"id": "basic-rag-003", "variant": "orig", "question": "Rock和Rock And Roll在统计上是一个分类吗？", "expected_documents": ["D03"]}
{"id": "basic-rag-003", "variant": "t1", "question": "统计口径上，Rock 与 Rock And Roll 算不算同一个曲风？", "expected_documents": ["D03"]}
{"id": "basic-rag-003", "variant": "t2", "question": "数据里出现的 Rock 和 Rock And Roll 到底要不要合并成一类来看？", "expected_documents": ["D03"]}
{"id": "basic-rag-004", "variant": "orig", "question": "默认音乐销售额包括哪些MediaType？", "expected_documents": ["D03", "D01"]}
{"id": "basic-rag-004", "variant": "t1", "question": "音乐类收入涵盖哪几种载体格式？", "expected_documents": ["D03", "D01"]}
{"id": "basic-rag-004", "variant": "t2", "question": "算音频营收的时候，哪些播放介质要被算进来？", "expected_documents": ["D03", "D01"]}
{"id": "basic-rag-005", "variant": "orig", "question": "客户分层的A、B、C层金额阈值分别是什么？", "expected_documents": ["D04"]}
{"id": "basic-rag-005", "variant": "t1", "question": "顾客等级划分里 A/B/C 三档的判定门槛各是多少？", "expected_documents": ["D04"]}
{"id": "basic-rag-005", "variant": "t2", "question": "把买家分成三档时，每档对应的消费金额线分别是多少？", "expected_documents": ["D04"]}
{"id": "basic-rag-006", "variant": "orig", "question": "选品时A、B、C、D四类候选分别怎么处理？", "expected_documents": ["D05"]}
{"id": "basic-rag-006", "variant": "t1", "question": "挑货时 A、B、C、D 四类备选商品的处置方式有什么不同？", "expected_documents": ["D05"]}
{"id": "basic-rag-006", "variant": "t2", "question": "商品候选被标成四档之后，每一档接下来分别该怎么操作？", "expected_documents": ["D05"]}
{"id": "basic-rag-007", "variant": "orig", "question": "2025年第三季度Rock、Metal、Latin和全音频目标是多少？", "expected_documents": ["D06"]}
{"id": "basic-rag-007", "variant": "t1", "question": "2025 Q3 Rock、Metal、Latin 以及整体音频的指标值各是多少？", "expected_documents": ["D06"]}
{"id": "basic-rag-007", "variant": "t2", "question": "今年第三季度给摇滚、金属、拉丁还有全部音频定的任务额分别是多少？", "expected_documents": ["D06"]}
{"id": "basic-rag-008", "variant": "orig", "question": "二季度音频经营的核心结果是什么？", "expected_documents": ["D07"]}
{"id": "basic-rag-008", "variant": "t1", "question": "2025 年 Q2 音频业务的整体表现怎么样？", "expected_documents": ["D07"]}
{"id": "basic-rag-008", "variant": "t2", "question": "上一季度四到六月音频这块做完之后，关键结论有哪些？", "expected_documents": ["D07"]}
{"id": "basic-rag-009", "variant": "orig", "question": "三季度全音频目标完成了吗？", "expected_documents": ["D08"]}
{"id": "basic-rag-009", "variant": "t1", "question": "2025 Q3 整体音频的指标值有没有达成？", "expected_documents": ["D08"]}
{"id": "basic-rag-009", "variant": "t2", "question": "第三季度全部音频的任务额完成情况如何，到底达没达成？", "expected_documents": ["D08"]}
{"id": "basic-rag-010", "variant": "orig", "question": "活动报名截止时间和需要提交的材料是什么？", "expected_documents": ["D11"]}
{"id": "basic-rag-010", "variant": "t1", "question": "这次活动的材料提交时限是哪天，都要交些什么？", "expected_documents": ["D11"]}
{"id": "basic-rag-010", "variant": "t2", "question": "想参加这个活动，最晚什么时候之前要把哪些东西交上去？", "expected_documents": ["D11"]}
{"id": "basic-rag-011", "variant": "orig", "question": "客单价是按销售额除以销量，还是除以订单数？订单数为零时怎么处理？", "expected_documents": ["D02"]}
{"id": "basic-rag-011", "variant": "t1", "question": "单客成交额的分母应该用件数还是单量？分母为零时又该如何处理？", "expected_documents": ["D02"]}
{"id": "basic-rag-011", "variant": "t2", "question": "算每位顾客平均贡献时，是拿收入除以卖出的件数还是除以订单笔数？要是笔数是零怎么办？", "expected_documents": ["D02"]}
{"id": "basic-rag-012", "variant": "orig", "question": "我想看一个月的销售额有没有增长，应该和什么时间段比较？", "expected_documents": ["D02"]}
{"id": "basic-rag-012", "variant": "t1", "question": "判断某个月营收有没有上升，参照的时间段应该选哪个？", "expected_documents": ["D02"]}
{"id": "basic-rag-012", "variant": "t2", "question": "想看这个月收入是不是涨了，该拿它跟哪一段时间做对比？", "expected_documents": ["D02"]}
{"id": "basic-rag-013", "variant": "orig", "question": "Rock现在是重点增长品类，运营上优先安排哪些动作？", "expected_documents": ["D09"]}
{"id": "basic-rag-013", "variant": "t1", "question": "Rock 目前被列为重点发力曲风，运营侧要先做哪几件事？", "expected_documents": ["D09"]}
{"id": "basic-rag-013", "variant": "t2", "question": "既然摇滚是现在的主推方向，具体应该优先落实哪些动作？", "expected_documents": ["D09"]}
{"id": "basic-rag-014", "variant": "orig", "question": "第四季度主题活动里，Rock、Metal和Latin分别怎么安排？", "expected_documents": ["D10"]}
{"id": "basic-rag-014", "variant": "t1", "question": "Q4 的主题活动里，摇滚、金属和拉丁各自是怎么规划的？", "expected_documents": ["D10"]}
{"id": "basic-rag-014", "variant": "t2", "question": "第四季度的活动安排中，Rock、Metal、Latin 三个曲风分别对应什么内容？", "expected_documents": ["D10"]}
{"id": "basic-rag-015", "variant": "orig", "question": "同一张订单里同时有音频和视频明细，算音乐销售额时应该怎么处理？", "expected_documents": ["D12"]}
{"id": "basic-rag-015", "variant": "t1", "question": "一张单子里音频和视频明细混在一起时，音乐收入该怎么归集？", "expected_documents": ["D12"]}
{"id": "basic-rag-015", "variant": "t2", "question": "如果同一笔订单既买了音频又买了视频，算音频营收时这部分要怎么处理？", "expected_documents": ["D12"]}
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_paraphrase_eval_set.py -q`

Expected: `3 passed`

> 若 `test_original_question_and_gold_match_basic_rag_suite` 失败，说明上面的 orig 文本或 gold 与 `eval_basic_rag.jsonl` 有出入。**以题库为准**，改 JSONL，不要改题库。

- [ ] **Step 5: 写 RED 测试（评测脚本的纯函数部分）**

在 `services/agent_api/tests/test_paraphrase_eval_set.py` 的 `if __name__` 之前追加：

```python
class RecallSummaryTests(unittest.TestCase):
    """summarize/format_table 是纯函数，不加载索引即可测。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "eval_retrieval_recall", Path(__file__).resolve().parents[3] / "scripts" / "eval_retrieval_recall.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_summarize_counts_any_top1_all_per_variant(self):
        results = [
            {"case_id": "c1", "variant": "orig", "gold": ["D01"], "docs": ["D01", "D06"], "status": "success"},
            {"case_id": "c1", "variant": "t1", "gold": ["D01"], "docs": ["D08", "D01"], "status": "success"},
            {"case_id": "c1", "variant": "t2", "gold": ["D01"], "docs": [], "status": "success"},
            {"case_id": "c2", "variant": "orig", "gold": ["D04"], "docs": ["D04"], "status": "success"},
            {"case_id": "c2", "variant": "t1", "gold": ["D04"], "docs": ["D03"], "status": "failed"},
            {"case_id": "c2", "variant": "t2", "gold": ["D04"], "docs": ["D04", "D05"], "status": "success"},
        ]
        summary = self.mod.summarize(results, top_k=5)
        self.assertEqual(summary["orig"]["doc_any"], 2)
        self.assertEqual(summary["orig"]["doc_top1"], 2)
        self.assertEqual(summary["t1"]["doc_any"], 1)
        self.assertEqual(summary["t1"]["doc_top1"], 0)
        self.assertEqual(summary["t2"]["doc_any"], 1)
        self.assertEqual(summary["cases"], 2)
        self.assertEqual(summary["total"]["doc_any"], 4)
        self.assertEqual(summary["total"]["n"], 6)

    def test_summarize_doc_all_requires_full_gold_coverage(self):
        results = [
            {"case_id": "c1", "variant": "orig", "gold": ["D03", "D01"], "docs": ["D03", "D06", "D01"], "status": "success"},
            {"case_id": "c2", "variant": "orig", "gold": ["D03", "D01"], "docs": ["D03", "D06"], "status": "success"},
        ]
        summary = self.mod.summarize(results, top_k=5)
        self.assertEqual(summary["orig"]["doc_all"], 1)
        self.assertEqual(summary["orig"]["doc_any"], 2)

    def test_format_table_has_one_row_per_variant_plus_total(self):
        results = [
            {"case_id": "c1", "variant": v, "gold": ["D01"], "docs": ["D01"], "status": "success"}
            for v in ("orig", "t1", "t2")
        ]
        table = self.mod.format_table(self.mod.summarize(results, top_k=5), mode="hybrid")
        self.assertIn("hybrid", table)
        for variant in ("orig", "t1", "t2", "TOTAL"):
            self.assertIn(variant, table)
```

- [ ] **Step 6: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_paraphrase_eval_set.py -q`

Expected: FAIL，`RecallSummaryTests` 3 个用例报 `FileNotFoundError: ... scripts/eval_retrieval_recall.py`（`ParaphraseEvalSetTests` 仍 3 passed）

- [ ] **Step 7: 实现评测脚本**

创建 `scripts/eval_retrieval_recall.py`：

```python
"""改写问法下的检索召回评测：只测 KnowledgeIndex.retrieve()，不走完整 agent。

把「检索机制」和「路由/答案生成」分开测量。端到端分数掉了的时候，
先用这个脚本判断是检索没召回，还是召回了但下游没用好。

用法:
    python scripts/eval_retrieval_recall.py --mode lexical
    python scripts/eval_retrieval_recall.py --mode hybrid --top-k 5 --out tmp/hybrid-t4/recall.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARAPHRASE_PATH = ROOT / "eval" / "agent" / "paraphrase_retrieval.jsonl"
VARIANTS = ("orig", "t1", "t2")


def load_cases(path: Path = PARAPHRASE_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(results: list[dict], top_k: int) -> dict:
    """按 variant 汇总 doc_any / doc_top1 / doc_all。纯函数，便于单测。"""
    def blank():
        return {"n": 0, "doc_any": 0, "doc_top1": 0, "doc_all": 0, "empty": 0, "failed": 0}

    per = {variant: blank() for variant in VARIANTS}
    cases: set[str] = set()
    for row in results:
        bucket = per.setdefault(row["variant"], blank())
        cases.add(row["case_id"])
        gold = set(row["gold"])
        # 只取前 top_k 个「去重后」的文档，与线上 top_k 语义一致
        seen: list[str] = []
        for doc in row["docs"]:
            if doc not in seen:
                seen.append(doc)
        seen = seen[:top_k]
        bucket["n"] += 1
        bucket["doc_any"] += int(bool(gold & set(seen)))
        bucket["doc_top1"] += int(bool(seen) and seen[0] in gold)
        bucket["doc_all"] += int(gold <= set(seen))
        bucket["empty"] += int(not row["docs"])
        bucket["failed"] += int(row["status"] != "success")
    total = blank()
    for bucket in per.values():
        for key in total:
            total[key] += bucket[key]
    return {"cases": len(cases), "top_k": top_k, **per, "total": total}


def format_table(summary: dict, mode: str) -> str:
    lines = [f"== RAG_FUSION_MODE={mode}  top_k={summary['top_k']}  cases={summary['cases']} =="]
    lines.append(f"{'variant':<8}{'n':>4}{'doc_any':>10}{'doc_top1':>11}{'doc_all':>10}{'empty':>7}{'failed':>8}")
    for name in (*VARIANTS, "total"):
        bucket = summary.get(name)
        if not bucket or not bucket["n"]:
            continue
        label = "TOTAL" if name == "total" else name
        n = bucket["n"]
        lines.append(f"{label:<8}{n:>4}"
                     f"{bucket['doc_any']:>7}/{n:<2}"
                     f"{bucket['doc_top1']:>8}/{n:<2}"
                     f"{bucket['doc_all']:>7}/{n:<2}"
                     f"{bucket['empty']:>7}{bucket['failed']:>8}")
    return "\n".join(lines)


def run(mode: str, top_k: int, verbose: bool) -> list[dict]:
    os.environ["RAG_FUSION_MODE"] = mode
    os.environ.setdefault("RAG_EMBEDDING_CACHE", str(ROOT / "tmp" / "embedding-models"))
    from services.agent_api.app.contracts import RetrievalRequest
    from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
    from services.agent_api.app.profiles import ProfileRegistry

    profile = ProfileRegistry.defaults().get("chinook-music")
    index = load_knowledge_index(KNOWLEDGE_ROOT, profile)
    store = index.embedding_store
    print(f"索引 {len(index.chunks)} 片段 / {len(index.documents)} 文档；向量存储 "
          f"{'已加载 ' + str(store.count) + ' 条' if store else '未加载（hybrid 会退化为纯词法）'}")
    results = []
    for case in load_cases():
        response = index.retrieve(RetrievalRequest(
            retrieval_id=f"recall-{case['id']}-{case['variant']}", profile_id=profile.profile_id,
            query=case["question"], filters={}, top_k=top_k,
        ))
        docs = [chunk.doc_id for chunk in response.chunks]
        results.append({"case_id": case["id"], "variant": case["variant"], "question": case["question"],
                        "gold": case["expected_documents"], "docs": docs, "status": response.status})
        if verbose:
            flag = "HIT " if set(case["expected_documents"]) & set(docs) else "MISS"
            print(f"  [{flag}] {case['id']:<15}{case['variant']:<5} "
                  f"gold={case['expected_documents']} got={docs}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="改写问法检索召回评测")
    parser.add_argument("--mode", default="lexical", choices=["lexical", "rank", "hybrid"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", default="")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    results = run(args.mode, args.top_k, args.verbose)
    summary = summarize(results, args.top_k)
    print(format_table(summary, args.mode))
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> `index.embedding_store` 属性在 Task 1 才存在。为了让 Task 0 能独立跑通，此步骤先在 `index.py` 的 `KnowledgeIndex.__init__` 里加一行占位属性 `self.embedding_store = None`（放在 `self._dense_failed = False` 之后）。Task 1 会把它换成真正的存储对象。
>
> `RAG_FUSION_MODE` 在 Task 3 才被 `retrieve()` 读取。此步骤里 `--mode rank/hybrid` 的行为等同 `lexical`，这是预期的：Task 0 只交付测量工具，不改产品行为。

- [ ] **Step 8: 跑测试确认 GREEN**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_paraphrase_eval_set.py services/agent_api/tests/test_knowledge_index.py -q`

Expected: `12 passed`（6 个新用例 + 6 个既有知识索引用例；占位属性不得破坏任何现有行为）

- [ ] **Step 9: 跑评测，记录 lexical 基线**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode lexical -v --out tmp/hybrid-t0/recall-lexical.json
```

Expected: 约 15s 跑完（索引加载 ~7s + 45 次检索）。**关键要看 t1/t2 的 `empty` 列是否接近 15**——改写句返回空 chunks，这正是 1.3 节诊断的现象。把输出表格抄进本计划末尾的「六、实测记录」章节。

参考形态（数字以实际为准）：
```text
== RAG_FUSION_MODE=lexical  top_k=5  cases=15 ==
variant    n   doc_any   doc_top1   doc_all  empty  failed
orig      15     15/15      13/15     14/15      0       0
t1        15      2/15       1/15      1/15     12       0
t2        15      0/15       0/15      0/15     15       0
TOTAL     45     17/45      14/45     15/45     27       0
```

> 这张表是后面所有 Task 的对照基线。**必须抄进计划文档并提交**，不能只留在 gitignore 的 `tmp/` 里。

- [ ] **Step 10: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add eval/agent/paraphrase_retrieval.jsonl scripts/eval_retrieval_recall.py services/agent_api/tests/test_paraphrase_eval_set.py services/agent_api/app/knowledge/index.py docs/superpowers/plans/2026-10-14-hybrid-retrieval.md
git commit -m "test: add paraphrase retrieval recall eval set and runner"
```

> `docs/superpowers/` 被 `.gitignore:47` 忽略，但既有计划文档都是被追踪的。本文件首次提交需要 `git add -f docs/superpowers/plans/2026-10-14-hybrid-retrieval.md`，之后正常 `git add` 即可。

---

## Task 1：向量预计算、持久化与加载校验

**目标：** 冷启动从 54.6s 降到 ≤1.0s，且向量与知识快照/切片参数严格绑定，不匹配就拒绝加载（绝不静默重算）。

**Files:**
- Create: `services/agent_api/app/knowledge/embeddings.py`
- Create: `scripts/build_knowledge_embeddings.py`
- Create: `data/knowledge/embeddings/BAAI--bge-small-zh-v1.5-<hash8>-m1200.json` + `.f32`（脚本产出，提交进仓库）
- Modify: `services/agent_api/app/knowledge/index.py`（`KnowledgeIndex.__init__`、`attach_embedding_store`、`load_knowledge_index`）
- Test: `services/agent_api/tests/test_knowledge_embeddings.py`

**设计要点**

- 存储格式：JSON 边车（元数据 + 有序 `chunk_ids`）+ 裸 float32 行主序二进制（已 L2 归一化）。**运行时不引入 numpy**，用 stdlib `array("f")` 读取；numpy 只在构建脚本里作为 fastembed 的返回类型出现，用 `.tolist()` 转掉。
- 文件名三重键：`<model_slug>-<snapshot8>-m<max_chars>`。`model_slug` 把 `/` 换成 `--`。
- **必须带 `max_chars`**：`knowledge_snapshot_version()`（`index.py:471`）只哈希 doc 的 sha256+size，不含切片参数；而生产用 `max_chars=1200`（425 片段，`sql_d12.py:134`）、测试用 `800`（432 片段），两者的快照版本实测完全相同（`d7dac902594b...`）。少了这个键，同一快照版本会对应两套 chunk_id，向量会静默错位。
- 加载时逐条比对 `chunk_ids`，这是最强的一道保险，能自动捕获 `max_chars` 漂移、文档改动、切片逻辑变更。
- 校验失败**一律返回 `None` 并记日志**，检索退化为纯词法。不抛异常、不重算。
- 向量文件**提交进仓库**（约 850 KB）。理由：比赛现场可能没有网络下载模型、也不该等 55s 编码；仓库里已经追踪了约 1.5 MB 的 PDF/DOCX/PNG 原件，这个量级是一致的。备选方案（gitignore + 部署时构建）会把 55s 的编码成本推到最不该出问题的时刻，不采纳。

- [ ] **Step 1: 写 RED 测试**

创建 `services/agent_api/tests/test_knowledge_embeddings.py`：

```python
"""向量存储的往返与校验。全部用合成数据，不依赖 fastembed 或真实快照。"""

import json
from pathlib import Path
import tempfile
import unittest

from services.agent_api.app.knowledge.embeddings import (
    FORMAT_VERSION,
    EmbeddingStore,
    load_embedding_store,
    store_paths,
    write_embedding_store,
)


CHUNK_IDS = ("D01#0", "D01#1", "D03#0")
# 三行二维向量，故意不做成单位长度，由 write_embedding_store 负责归一化
VECTORS = ((3.0, 4.0), (0.0, 2.0), (1.0, 0.0))
SNAPSHOT = "a" * 64
MODEL = "BAAI/bge-small-zh-v1.5"


class EmbeddingStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def build(self, **overrides):
        kwargs = {"root": self.root, "model_name": MODEL, "snapshot_version": SNAPSHOT,
                  "max_chars": 1200, "chunk_ids": CHUNK_IDS, "vectors": VECTORS}
        kwargs.update(overrides)
        return write_embedding_store(**kwargs)

    def load(self, **overrides):
        kwargs = {"root": self.root, "chunk_ids": CHUNK_IDS, "snapshot_version": SNAPSHOT,
                  "max_chars": 1200, "model_name": MODEL}
        kwargs.update(overrides)
        return load_embedding_store(**kwargs)

    def test_round_trip_preserves_ids_and_normalizes_rows(self):
        self.build()
        store = self.load()
        self.assertIsNotNone(store)
        self.assertEqual(store.chunk_ids, CHUNK_IDS)
        self.assertEqual((store.count, store.dim), (3, 2))
        first = store.row(0)
        self.assertAlmostEqual(first[0], 0.6, places=5)
        self.assertAlmostEqual(first[1], 0.8, places=5)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=5)

    def test_similarity_is_dot_product_of_normalized_rows(self):
        self.build()
        store = self.load()
        # 查询向量与第一行同向 -> 1.0；与第三行正交 -> 0.0
        scores = store.similarity((0.6, 0.8))
        self.assertAlmostEqual(scores["D01#0"], 1.0, places=5)
        self.assertAlmostEqual(scores["D01#1"], 0.8, places=5)
        self.assertAlmostEqual(scores["D03#0"], 0.6, places=5)
        scores = store.similarity((0.0, 1.0))
        self.assertAlmostEqual(scores["D03#0"], 0.0, places=5)

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.load())

    def test_snapshot_mismatch_is_rejected(self):
        self.build()
        self.assertIsNone(self.load(snapshot_version="b" * 64))

    def test_max_chars_mismatch_is_rejected(self):
        self.build()
        self.assertIsNone(self.load(max_chars=800))

    def test_chunk_id_mismatch_is_rejected(self):
        self.build()
        self.assertIsNone(self.load(chunk_ids=("D01#0", "D01#1", "D99#0")))
        self.assertIsNone(self.load(chunk_ids=("D01#0", "D01#1")))

    def test_model_mismatch_is_rejected(self):
        self.build()
        self.assertIsNone(self.load(model_name="BAAI/bge-large-zh-v1.5"))

    def test_truncated_vector_file_is_rejected(self):
        self.build()
        _, vector_path = store_paths(self.root, MODEL, SNAPSHOT, 1200)
        vector_path.write_bytes(vector_path.read_bytes()[:-4])
        self.assertIsNone(self.load())

    def test_unknown_format_version_is_rejected(self):
        self.build()
        meta_path, _ = store_paths(self.root, MODEL, SNAPSHOT, 1200)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["format_version"] = FORMAT_VERSION + 1
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(self.load())

    def test_unnormalized_store_is_rejected(self):
        self.build()
        meta_path, _ = store_paths(self.root, MODEL, SNAPSHOT, 1200)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["normalized"] = False
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(self.load())

    def test_store_is_a_frozen_value_object(self):
        self.build()
        store = self.load()
        self.assertIsInstance(store, EmbeddingStore)
        with self.assertRaises(Exception):
            store.chunk_ids = ()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_embeddings.py -q`

Expected: FAIL，`ModuleNotFoundError: No module named 'services.agent_api.app.knowledge.embeddings'`

- [ ] **Step 3: 实现 `embeddings.py`**

创建 `services/agent_api/app/knowledge/embeddings.py`：

```python
"""Precomputed chunk embeddings for the manifest-locked knowledge snapshot.

Vectors are built offline by ``scripts/build_knowledge_embeddings.py`` and stored
as L2-normalized float32 rows plus a JSON sidecar.  Loading never encodes
anything: batch-encoding the corpus costs ~55s, which must never happen inside a
live query.  A store whose model, snapshot, chunking, or chunk ids do not match
the live index is rejected outright so retrieval degrades to lexical instead of
serving vectors that no longer line up with the chunks.

The runtime path deliberately avoids numpy: 425 x 512 float32 is ~850 KB and a
pure-Python dot product over it costs ~27ms, which is well inside budget.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path


LOGGER = logging.getLogger(__name__)
EMBEDDING_DIRNAME = "embeddings"
DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
FORMAT_VERSION = 1
FLOAT32_BYTES = 4


def model_slug(model_name: str) -> str:
    return model_name.replace("/", "--")


def store_paths(root: Path, model_name: str, snapshot_version: str, max_chars: int) -> tuple[Path, Path]:
    """元数据与向量文件的路径。文件名同时编码模型、快照和切片参数。"""
    stem = f"{model_slug(model_name)}-{snapshot_version[:8]}-m{max_chars}"
    directory = root / EMBEDDING_DIRNAME
    return directory / f"{stem}.json", directory / f"{stem}.f32"


@dataclass(frozen=True)
class EmbeddingStore:
    """只读的归一化向量存储。``similarity`` 因此等价于点积。"""

    model_name: str
    snapshot_version: str
    max_chars: int
    dim: int
    chunk_ids: tuple[str, ...]
    flat: array  # array("f")，行主序，长度 == dim * len(chunk_ids)

    @property
    def count(self) -> int:
        return len(self.chunk_ids)

    def row(self, position: int) -> tuple[float, ...]:
        start = position * self.dim
        return tuple(self.flat[start:start + self.dim])

    def similarity(self, query_vector: tuple[float, ...]) -> dict[str, float]:
        dim = self.dim
        flat = self.flat
        return {
            chunk_id: sum(left * right for left, right in zip(query_vector, flat[start:start + dim]))
            for start, chunk_id in zip(range(0, len(flat), dim), self.chunk_ids)
        }


def normalize_rows(vectors, dim: int) -> list[float]:
    """展平并逐行 L2 归一化。零向量原样保留（点积为 0，不会除零）。"""
    flat: list[float] = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != dim:
            raise ValueError(f"embedding dim mismatch: expected {dim}, got {len(values)}")
        norm = math.sqrt(sum(value * value for value in values))
        flat.extend(value / norm for value in values) if norm else flat.extend(values)
    return flat


def write_embedding_store(*, root: Path, model_name: str, snapshot_version: str, max_chars: int,
                          chunk_ids, vectors) -> EmbeddingStore:
    """把向量写盘并返回内存对象。只有离线构建脚本会调用它。"""
    chunk_ids = tuple(chunk_ids)
    rows = [[float(value) for value in vector] for vector in vectors]
    if len(rows) != len(chunk_ids):
        raise ValueError(f"vector/chunk count mismatch: {len(rows)} vs {len(chunk_ids)}")
    if not rows:
        raise ValueError("refusing to write an empty embedding store")
    dim = len(rows[0])
    flat = normalize_rows(rows, dim)
    meta_path, vector_path = store_paths(root, model_name, snapshot_version, max_chars)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = array("f", flat)
    vector_path.write_bytes(buffer.tobytes())
    meta = {
        "format_version": FORMAT_VERSION,
        "model": model_name,
        "snapshot_version": snapshot_version,
        "max_chars": max_chars,
        "dim": dim,
        "count": len(chunk_ids),
        "normalized": True,
        "dtype": "float32",
        "byte_order": "native",
        "chunk_ids": list(chunk_ids),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("embedding store written: %s (%d x %d)", vector_path.name, len(chunk_ids), dim)
    return EmbeddingStore(model_name=model_name, snapshot_version=snapshot_version, max_chars=max_chars,
                          dim=dim, chunk_ids=chunk_ids, flat=buffer)


def _reject(meta_path: Path, reason: str, detail: str) -> None:
    """统一记录拒绝原因。缺文件是全新克隆的正常状态，用 INFO；其余用 WARNING。"""
    message = "embedding store unavailable (%s): %s [%s]"
    if reason == "missing":
        LOGGER.info(message, reason, detail, meta_path.name)
    else:
        LOGGER.warning(message, reason, detail, meta_path.name)


def load_embedding_store(*, root: Path, chunk_ids, snapshot_version: str, max_chars: int,
                         model_name: str | None = None) -> EmbeddingStore | None:
    """加载并校验预计算向量。任何不符都返回 None，绝不重算。"""
    model_name = model_name or os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_MODEL)
    chunk_ids = tuple(chunk_ids)
    meta_path, vector_path = store_paths(root, model_name, snapshot_version, max_chars)
    if not meta_path.is_file() or not vector_path.is_file():
        _reject(meta_path, "missing", f"{meta_path.name} 或对应 .f32 不存在，"
                                      f"运行 scripts/build_knowledge_embeddings.py 生成")
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _reject(meta_path, "unreadable", f"{type(exc).__name__}: {exc}")
        return None
    if meta.get("format_version") != FORMAT_VERSION:
        _reject(meta_path, "format", f"format_version={meta.get('format_version')!r} != {FORMAT_VERSION}")
        return None
    if meta.get("model") != model_name:
        _reject(meta_path, "model", f"{meta.get('model')!r} != {model_name!r}")
        return None
    if meta.get("snapshot_version") != snapshot_version:
        _reject(meta_path, "snapshot", f"{str(meta.get('snapshot_version'))[:8]} != {snapshot_version[:8]}")
        return None
    if meta.get("max_chars") != max_chars:
        _reject(meta_path, "chunking", f"max_chars={meta.get('max_chars')!r} != {max_chars!r}")
        return None
    if meta.get("normalized") is not True:
        _reject(meta_path, "normalized", "向量未归一化，点积不等于余弦")
        return None
    if meta.get("byte_order") != "native":
        _reject(meta_path, "byte_order", f"byte_order={meta.get('byte_order')!r}")
        return None
    stored_ids = meta.get("chunk_ids")
    if not isinstance(stored_ids, list) or tuple(stored_ids) != chunk_ids:
        _reject(meta_path, "chunk_ids",
                f"{len(stored_ids) if isinstance(stored_ids, list) else 'n/a'} 条 != 索引 {len(chunk_ids)} 条")
        return None
    dim = meta.get("dim")
    if not isinstance(dim, int) or dim <= 0:
        _reject(meta_path, "dim", f"dim={dim!r}")
        return None
    expected_bytes = dim * len(chunk_ids) * FLOAT32_BYTES
    try:
        payload = vector_path.read_bytes()
    except OSError as exc:
        _reject(meta_path, "unreadable", f"{type(exc).__name__}: {exc}")
        return None
    if len(payload) != expected_bytes:
        _reject(meta_path, "size", f"{len(payload)} bytes != {expected_bytes} bytes")
        return None
    flat = array("f")
    flat.frombytes(payload)
    LOGGER.info("embedding store loaded: %d chunks x %d dim (%s)", len(chunk_ids), dim, meta_path.name)
    return EmbeddingStore(model_name=model_name, snapshot_version=snapshot_version, max_chars=max_chars,
                          dim=dim, chunk_ids=chunk_ids, flat=flat)
```

> `byte_order: "native"` 是一道诚实的保护：裸 float32 二进制不跨字节序。比赛环境同为 x86-64 Windows，实际不会触发，但一旦有人在别的机器上构建、拿到这边加载，会得到明确的拒绝日志而不是静默的乱码分数。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_embeddings.py -q`

Expected: `10 passed`

- [ ] **Step 5: 把存储挂到 `KnowledgeIndex`**

`services/agent_api/app/knowledge/index.py`，在文件头 import 区（`from .documents import ...` 之后）加：

```python
from .embeddings import EmbeddingStore, load_embedding_store
```

把 Task 0 Step 7 加的占位行 `self.embedding_store = None` 替换为带类型标注的正式声明（位置仍在 `self._dense_failed = False` 之后）：

```python
        self.embedding_store: EmbeddingStore | None = None
```

在 `KnowledgeIndex` 里、`_build_idf` 之前插入：

```python
    def attach_embedding_store(self, store: EmbeddingStore | None, query_encoder=None) -> None:
        """绑定预计算向量存储与查询编码器。

        store 为 None 时检索退化为纯词法，不抛异常。query_encoder 在 Task 2 才被
        `_dense_scores` 消费，这里先把签名定型，避免 Task 2 再改一次公开接口。
        """
        self.embedding_store = store
        self._query_encoder = query_encoder
```

改 `load_knowledge_index` 的结尾（当前是 `return KnowledgeIndex(manifest, tuple(chunks), profile, structured_records)`）：

```python
    structured_records = extract_d06_structured_records(tuple(chunks))
    index = KnowledgeIndex(manifest, tuple(chunks), profile, structured_records)
    # 向量按 (模型, 快照, 切片参数, chunk_ids) 四重校验；不匹配则退化词法，绝不重算。
    index.attach_embedding_store(load_embedding_store(
        root=root,
        chunk_ids=tuple(item.record.chunk_id for item in index.chunks),
        snapshot_version=knowledge_snapshot_version(index),
        max_chars=max_chars,
    ))
    return index
```

- [ ] **Step 6: 跑既有知识索引测试，确认没破坏**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_paraphrase_eval_set.py -q`

Expected: `12 passed`。此时向量文件还不存在，日志里会出现一条 INFO 级 `embedding store unavailable (missing)`，这是预期的。

- [ ] **Step 7: 实现构建脚本**

创建 `scripts/build_knowledge_embeddings.py`：

```python
"""离线预计算知识快照的片段向量。

这是唯一会批量编码语料的地方：整个语料实测约 55s，绝不能发生在在线查询里。
产物写入 data/knowledge/embeddings/ 并提交进仓库，运行时只做加载与校验。

用法:
    python scripts/build_knowledge_embeddings.py                  # 生产切片 max_chars=1200
    python scripts/build_knowledge_embeddings.py --max-chars 800  # 测试切片
    python scripts/build_knowledge_embeddings.py --model BAAI/bge-small-zh-v1.5
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="预计算知识片段向量")
    parser.add_argument("--max-chars", type=int, default=1200,
                        help="切片长度，必须与线上一致（生产 1200，测试 800）")
    parser.add_argument("--model", default=os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    parser.add_argument("--cache", default=os.getenv("RAG_EMBEDDING_CACHE", str(ROOT / "tmp" / "embedding-models")))
    parser.add_argument("--root", default=str(ROOT / "data" / "knowledge"))
    args = parser.parse_args()

    from services.agent_api.app.knowledge.embeddings import store_paths, write_embedding_store
    from services.agent_api.app.knowledge.index import (
        KNOWLEDGE_ROOT, knowledge_snapshot_version, load_knowledge_index)
    from services.agent_api.app.profiles import ProfileRegistry

    knowledge_root = Path(args.root).resolve() if args.root else KNOWLEDGE_ROOT
    profile = ProfileRegistry.defaults().get("chinook-music")

    started = time.monotonic()
    index = load_knowledge_index(knowledge_root, profile, max_chars=args.max_chars)
    snapshot = knowledge_snapshot_version(index)
    loaded = time.monotonic()
    print(f"索引加载 {loaded - started:5.2f}s：{len(index.chunks)} 片段 / {len(index.documents)} 文档")
    print(f"快照版本 {snapshot[:16]}...  max_chars={args.max_chars}  model={args.model}")

    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=args.model, cache_dir=args.cache, threads=2)
    initialized = time.monotonic()
    print(f"模型初始化 {initialized - loaded:5.2f}s")

    texts = [item.normalized_text for item in index.chunks]
    raw = list(model.embed(texts))
    encoded = time.monotonic()
    print(f"编码 {len(texts)} 片段 {encoded - initialized:5.2f}s")

    vectors = [row.tolist() for row in raw]
    dim = len(vectors[0])
    if any(len(row) != dim for row in vectors):
        raise SystemExit("向量维度不一致，拒绝写盘")

    store = write_embedding_store(
        root=knowledge_root, model_name=args.model, snapshot_version=snapshot,
        max_chars=args.max_chars,
        chunk_ids=[item.record.chunk_id for item in index.chunks], vectors=vectors)
    meta_path, vector_path = store_paths(knowledge_root, args.model, snapshot, args.max_chars)
    digest = hashlib.sha256(vector_path.read_bytes()).hexdigest()
    print(f"写盘完成 {time.monotonic() - encoded:5.2f}s")
    print(f"  {meta_path}  ({meta_path.stat().st_size:,} bytes)")
    print(f"  {vector_path}  ({vector_path.stat().st_size:,} bytes, dim={store.dim}, sha256={digest[:16]}...)")

    # 立刻回读校验，确保刚写的文件能被加载器接受
    from services.agent_api.app.knowledge.embeddings import load_embedding_store
    reloaded = load_embedding_store(root=knowledge_root,
                                    chunk_ids=tuple(item.record.chunk_id for item in index.chunks),
                                    snapshot_version=snapshot, max_chars=args.max_chars,
                                    model_name=args.model)
    if reloaded is None:
        raise SystemExit("回读校验失败：刚写的存储无法被加载器接受")
    sample = reloaded.similarity(vectors[0])
    top = max(sample.items(), key=lambda pair: pair[1])
    print(f"  回读校验通过：{reloaded.count} 条，首行自相似度 "
          f"{sample[store.chunk_ids[0]]:.4f}，最高分 {top[0]}={top[1]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: 构建生产向量文件**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_EMBEDDING_CACHE = "$PWD\tmp\embedding-models"
.venv\Scripts\python.exe -X utf8 scripts/build_knowledge_embeddings.py --max-chars 1200
```

Expected（约 70s，其中编码约 55s）：
```text
索引加载   7.1s：425 片段 / 12 文档
快照版本 xxxxxxxxxxxxxxxx...  max_chars=1200  model=BAAI/bge-small-zh-v1.5
模型初始化  0.2s
编码 425 片段 54.6s
写盘完成  0.1s
  data\knowledge\embeddings\BAAI--bge-small-zh-v1.5-xxxxxxxx-m1200.json  (~30,000 bytes)
  data\knowledge\embeddings\BAAI--bge-small-zh-v1.5-xxxxxxxx-m1200.f32  (~870,400 bytes, dim=512, sha256=...)
  回读校验通过：425 条，首行自相似度 1.0000，最高分 D0x#n=1.0000
```

> 自相似度必须是 `1.0000`（±0.001）。不是 1 说明归一化没生效，后面所有余弦分数都会失真——**这一步不过就不要往下走**。

- [ ] **Step 9: 验证冷启动达标（≤1.0s）**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -c "
import sys, time
sys.path.insert(0, '.')
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.profiles import ProfileRegistry
p = ProfileRegistry.defaults().get('chinook-music')
t0 = time.monotonic()
idx = load_knowledge_index(KNOWLEDGE_ROOT, p, max_chars=1200)
t1 = time.monotonic()
store = idx.embedding_store
assert store is not None, '向量存储没加载上'
s = store.similarity(tuple([0.02] * store.dim))
t2 = time.monotonic()
print(f'索引+向量加载 {t1-t0:6.2f}s   425 次相似度 {t2-t1:6.3f}s   store={store.count}x{store.dim}')
"
```

Expected: `索引+向量加载` 约 7.2–7.5s（其中 ~7.1s 是既有的 PDF/DOCX 解析，与本 Task 无关），`425 次相似度` ≤ 0.05s，`store=425x512`。

**验收口径要说清楚：** 本 Task 消灭的是 54.6s 的编码成本。索引加载本身的 ~7.1s 是既有的文档解析开销，不在本计划范围内（它是进程级一次性成本，且离线评测已在承受）。对比改造前：`dense` 首次查询 = 7.1s 索引 + 54.6s 编码 + 0.03s 查询 ≈ 62s；改造后 = 7.1s 索引 + 0.03s 加载 + 0.03s 查询。**语义通道的增量成本从 54.6s 降到 0.06s。**

- [ ] **Step 10: 跑向量存储 + 知识索引测试**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_embeddings.py services/agent_api/tests/test_knowledge_index.py -q`

Expected: `16 passed`（10 + 6）。注意 `test_knowledge_index.py` 用 `max_chars=800`（432 片段），而仓库里只有 `-m1200` 的向量文件，所以它会命中 `missing` 拒绝分支并记一条 **INFO** 级日志（`_reject` 对 `missing` 用 INFO，其余才用 WARNING）——这是设计意图，不是错误，也不会让测试失败。

- [ ] **Step 11: 跑 offline 全量，确认零行为变化**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t1/report.json
```

Expected: `91/119`，与基线完全一致。本 Task 只新增了「加载但尚未被消费」的存储，`retrieve()` 一行未改，**任何分数变化都说明改错了**。

- [ ] **Step 12: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/embeddings.py services/agent_api/app/knowledge/index.py scripts/build_knowledge_embeddings.py services/agent_api/tests/test_knowledge_embeddings.py data/knowledge/embeddings
git commit -m "feat: persist precomputed knowledge embeddings with snapshot validation"
```

---

## Task 2：打分量级表、降级日志与编码器接缝（行为零变化）

**目的**：把散在 `retrieve()` 打分循环里的 20 多个魔法数字收进一张显式量级表（`BonusScale`），把 `_dense_scores` 的静默 `except` 换成可见日志，并把「现场编码整个语料」的入口抽成可替换的 `_query_encoder`。本 Task **不改变任何排序结果**：`lexical` 模式下每个片段的分数必须与改造前逐位相同。

**为什么先做这个**：Task 3 动候选池、Task 4 动量级、Task 5 动语义通道。三件事叠在一个 160 行的循环里改，出问题无法归因。先把常量抽出来，后面每个 Task 的 diff 才能小到逐行可审。

**涉及文件**：新建 `services/agent_api/app/knowledge/scoring.py`、`services/agent_api/tests/test_retrieval_scoring.py`；修改 `services/agent_api/app/knowledge/index.py`（加 `LOGGER`、换 `_dense_scores` 实现、打分字面量换成 `scale.*`）。

- [ ] **Step 1: 写 RED 测试**

创建 `services/agent_api/tests/test_retrieval_scoring.py`：

```python
"""打分量级表与融合算子的纯函数测试。不加载知识索引，跑起来 <1s。"""

from dataclasses import FrozenInstanceError
import os
from unittest.mock import patch
import unittest

from services.agent_api.app.knowledge import scoring
from services.agent_api.app.knowledge.scoring import (
    FUSION_HYBRID, FUSION_LEXICAL, FUSION_RANK, LEGACY, RRF,
    clamp_score, fuse_rrf, rrf_weight, scale_for)


class FusionModeTests(unittest.TestCase):
    def test_unset_defaults_to_lexical(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_FUSION_MODE", None)
            self.assertEqual(scoring.fusion_mode(), FUSION_LEXICAL)

    def test_known_values_pass_through(self):
        for mode in (FUSION_LEXICAL, FUSION_RANK, FUSION_HYBRID):
            with patch.dict(os.environ, {"RAG_FUSION_MODE": mode}):
                self.assertEqual(scoring.fusion_mode(), mode)

    def test_unknown_value_falls_back_to_lexical(self):
        """打错环境变量不能让线上检索直接崩，必须退回今天的行为。"""
        for raw in ("HYBRID ", "semantic", "1", "true", ""):
            with patch.dict(os.environ, {"RAG_FUSION_MODE": raw}):
                self.assertEqual(scoring.fusion_mode(), FUSION_LEXICAL)

    def test_scale_for_maps_modes(self):
        self.assertIs(scale_for(FUSION_LEXICAL), LEGACY)
        self.assertIs(scale_for(FUSION_RANK), RRF)
        self.assertIs(scale_for(FUSION_HYBRID), RRF)


class LegacyScaleTests(unittest.TestCase):
    def test_reproduces_every_constant_in_index_scoring_loop(self):
        """逐位复刻 index.py:321-379 现有字面量。任何一个不等，lexical 就会变行为。"""
        self.assertEqual((LEGACY.tier_spacing, LEGACY.role_tier), (1_000_000.0, 2))
        self.assertEqual(LEGACY.role_tier * LEGACY.tier_spacing, 2_000_000.0)
        self.assertEqual(LEGACY.hint_bonus, 1_000_000.0)
        self.assertEqual(LEGACY.required_source_bonus, 250.0)
        self.assertEqual((LEGACY.authority_multiplier, LEGACY.required_role_multiplier), (10.0, 5.0))
        self.assertEqual(LEGACY.structured_bonus, 2_000.0)
        self.assertEqual(LEGACY.target_value_bonus, 500.0)
        self.assertEqual(LEGACY.figure_context_bonus, 100.0)
        self.assertEqual(LEGACY.table_section_bonus, 12.0)
        self.assertEqual(LEGACY.table_context_bonus, 200.0)
        self.assertEqual(LEGACY.threshold_bonus, 100.0)
        self.assertEqual(LEGACY.rock_section_bonus, 100.0)
        self.assertEqual(LEGACY.d09_rock_bonus, 400.0)
        self.assertEqual(LEGACY.d06_page2_bonus, 150.0)
        self.assertEqual(LEGACY.d06_target_bonus, 200.0)
        self.assertEqual(LEGACY.d08_page2_bonus, 100.0)
        self.assertEqual(LEGACY.entity_bonus, 8.0)
        self.assertEqual(LEGACY.section_term_bonus, 3.0)
        self.assertEqual(LEGACY.title_term_bonus, 1.5)
        self.assertEqual(LEGACY.dense_multiplier, 10.0)
        self.assertEqual(LEGACY.workflow_bonus, (3_000.0, 2_900.0, 2_800.0))
        self.assertEqual(LEGACY.poster_bonus, 3_000.0)

    def test_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            LEGACY.hint_bonus = 0.0


class RrfScaleTests(unittest.TestCase):
    def test_hint_bonus_dominates_every_other_flat_bonus(self):
        """hint 是文档路由信号，必须压过所有单条平铺加分，否则 1.5 节的硬编码失去意义。"""
        flats = (RRF.required_source_bonus, RRF.structured_bonus, RRF.target_value_bonus,
                 RRF.figure_context_bonus, RRF.table_section_bonus, RRF.table_context_bonus,
                 RRF.threshold_bonus, RRF.rock_section_bonus, RRF.d09_rock_bonus,
                 RRF.d06_page2_bonus, RRF.d06_target_bonus, RRF.d08_page2_bonus,
                 RRF.entity_bonus, RRF.section_term_bonus, RRF.title_term_bonus, RRF.poster_bonus)
        self.assertLess(max(flats), RRF.hint_bonus)

    def test_all_flat_bonuses_cannot_outvote_a_priority_tier(self):
        """平铺加分全部叠满也不许翻越一个优先级层，这是 RRF 量级成立的前提。"""
        self.assertLess(RRF.flat_total() * 3, RRF.tier_spacing)
        self.assertLess(RRF.hint_bonus + RRF.flat_total(), RRF.tier_spacing)

    def test_dense_is_not_additive_in_rrf_regime(self):
        """语义通道在 RRF 量级下走融合排名（Task 6），不再做加法。留着 10.0 就是 1.4-b 那个死通道。"""
        self.assertEqual(RRF.dense_multiplier, 0.0)

    def test_base_rrf_weight_dwarfs_flat_bonuses(self):
        """基础分（RRF 权重）与加分同量级，加分才有能力重排；量级差 100 倍以上等于没加。"""
        self.assertAlmostEqual(rrf_weight(1), 1 / 61, places=12)
        self.assertLess(RRF.hint_bonus, rrf_weight(1) * 4)
        self.assertGreater(RRF.hint_bonus, rrf_weight(1) - rrf_weight(5))


class ClampScoreTests(unittest.TestCase):
    def test_negative_scores_are_clamped_not_raised(self):
        """contracts/models.py:179 是 ge=0，负分会抛 ValidationError 让整次检索 500。"""
        self.assertEqual(clamp_score(-0.5), 0.0)
        self.assertEqual(clamp_score(0.0), 0.0)
        self.assertEqual(clamp_score(1.25), 1.25)


class FuseRrfTests(unittest.TestCase):
    def test_single_list_is_monotonic(self):
        fused = fuse_rrf([["a", "b", "c"]])
        self.assertEqual([key for key, _ in fused], ["a", "b", "c"])
        self.assertAlmostEqual(fused[0][1], 1 / 61, places=12)
        self.assertGreater(fused[0][1], fused[1][1])

    def test_consensus_beats_single_channel_rank_one(self):
        """两通道都排第二的片段，必须压过只在单通道排第一的片段——这就是 RRF 的全部意义。"""
        fused = dict(fuse_rrf([["a", "shared"], ["b", "shared"]]))
        self.assertGreater(fused["shared"], fused["a"])
        self.assertGreater(fused["shared"], fused["b"])

    def test_empty_lists_are_ignored(self):
        self.assertEqual(fuse_rrf([]), [])
        self.assertEqual(fuse_rrf([[], []]), [])
        self.assertEqual(dict(fuse_rrf([[], ["a"]])), {"a": 1 / 61})

    def test_duplicates_within_a_list_use_first_position(self):
        fused = dict(fuse_rrf([["a", "b", "a"]]))
        self.assertAlmostEqual(fused["a"], 1 / 61, places=12)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_retrieval_scoring.py -q`

Expected: collection error（`ModuleNotFoundError: ... knowledge.scoring`）。看到 RED 再往下。

- [ ] **Step 3: 实现 `scoring.py`**

创建 `services/agent_api/app/knowledge/scoring.py`：

```python
"""Retrieval scoring scales and fusion operators.

Pure functions only: no index I/O, no document parsing, nothing but ``fusion_mode``
touching the environment.  Two scales exist because the legacy constants are
calibrated for an absolute regime (millions) while RRF fusion produces values
around 0.016.  Mixing the two silently kills one channel, which is exactly what
``dense * 10.0`` against ``+1_000_000`` does today (plan 1.4-b).
"""

from __future__ import annotations

from dataclasses import dataclass
import os


FUSION_LEXICAL = "lexical"
FUSION_RANK = "rank"
FUSION_HYBRID = "hybrid"
FUSION_MODES = (FUSION_LEXICAL, FUSION_RANK, FUSION_HYBRID)
FUSION_MODE_ENV = "RAG_FUSION_MODE"

RRF_K = 60
# rank/hybrid 模式的候选池大小：词法前 top_k*6，且不少于 30 条。
# 取 6 倍是因为 Phase D 会为契约文档强制插队，池子太小会让非契约文档完全没有机会。
RANK_POOL_MULTIPLIER = 6
RANK_POOL_FLOOR = 30


def fusion_mode() -> str:
    """读取融合模式。未设置、拼错、大小写不一致都回退 lexical（= 今天的行为）。"""
    raw = (os.getenv(FUSION_MODE_ENV) or "").strip().casefold()
    return raw if raw in FUSION_MODES else FUSION_LEXICAL


def scale_for(mode: str) -> "BonusScale":
    return LEGACY if mode == FUSION_LEXICAL else RRF


def clamp_score(score: float) -> float:
    """DocumentChunk.score 契约是 ge=0；负分必须截断，不能抛 ValidationError。"""
    return score if score > 0.0 else 0.0


def rrf_weight(position: int, k: int = RRF_K) -> float:
    """position 从 1 开始。"""
    return 1.0 / (k + position)


def fuse_rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion over chunk_id rankings, best first.

    同一片段在同一列表里重复出现只按首次位置计分，避免某通道刷重复片段拿分。
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for position, chunk_id in enumerate(ranking, start=1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            fused[chunk_id] = fused.get(chunk_id, 0.0) + rrf_weight(position, k)
    return sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))


@dataclass(frozen=True)
class BonusScale:
    """一整套业务加分的量级。字段顺序对应 index.py 打分循环里的出现顺序。"""

    name: str
    tier_spacing: float
    role_tier: int
    structured_tier: int
    hint_bonus: float
    required_source_bonus: float
    authority_multiplier: float
    required_role_multiplier: float
    structured_bonus: float
    target_value_bonus: float
    figure_context_bonus: float
    table_section_bonus: float
    table_context_bonus: float
    threshold_bonus: float
    rock_section_bonus: float
    d09_rock_bonus: float
    d06_page2_bonus: float
    d06_target_bonus: float
    d08_page2_bonus: float
    entity_bonus: float
    section_term_bonus: float
    title_term_bonus: float
    dense_multiplier: float
    workflow_bonus: tuple[float, float, float]
    poster_bonus: float

    def tier(self, level: int) -> float:
        return level * self.tier_spacing

    def flat_total(self) -> float:
        """所有平铺加分同时命中时的上界，供不变量测试使用。"""
        return sum((self.hint_bonus, self.required_source_bonus, self.structured_bonus,
                    self.target_value_bonus, self.figure_context_bonus, self.table_section_bonus,
                    self.table_context_bonus, self.threshold_bonus, self.rock_section_bonus,
                    self.d09_rock_bonus, self.d06_page2_bonus, self.d06_target_bonus,
                    self.d08_page2_bonus, self.entity_bonus, self.section_term_bonus,
                    self.title_term_bonus, self.poster_bonus))


# 今天线上的量级。LEGACY 存在的唯一目的是让 lexical 模式逐位不变，不要「顺手优化」它。
LEGACY = BonusScale(
    name="legacy", tier_spacing=1_000_000.0, role_tier=2, structured_tier=0,
    hint_bonus=1_000_000.0, required_source_bonus=250.0, authority_multiplier=10.0,
    required_role_multiplier=5.0, structured_bonus=2_000.0, target_value_bonus=500.0,
    figure_context_bonus=100.0, table_section_bonus=12.0, table_context_bonus=200.0,
    threshold_bonus=100.0, rock_section_bonus=100.0, d09_rock_bonus=400.0,
    d06_page2_bonus=150.0, d06_target_bonus=200.0, d08_page2_bonus=100.0,
    entity_bonus=8.0, section_term_bonus=3.0, title_term_bonus=1.5,
    dense_multiplier=10.0, workflow_bonus=(3_000.0, 2_900.0, 2_800.0), poster_bonus=3_000.0,
)

# RRF 量级。基础分是 1/(60+rank) ≈ 0.0164 起，所以：
#   - 优先级层（role/structured）用整数层 × 1.0，绝对压过一切平铺加分；
#   - hint 0.030 ≈ 两个 rank-1 基础分，足够把契约文档顶到前面，又不会淹没层；
#   - 其余平铺加分 ≤ 0.008，只能微调同层内顺序；
#   - dense_multiplier=0，语义通道改走 fuse_rrf（Task 6），不做加法。
RRF = BonusScale(
    name="rrf", tier_spacing=1.0, role_tier=3, structured_tier=2,
    hint_bonus=0.030, required_source_bonus=0.020, authority_multiplier=0.002,
    required_role_multiplier=0.001, structured_bonus=0.0, target_value_bonus=0.008,
    figure_context_bonus=0.004, table_section_bonus=0.001, table_context_bonus=0.006,
    threshold_bonus=0.004, rock_section_bonus=0.004, d09_rock_bonus=0.008,
    d06_page2_bonus=0.005, d06_target_bonus=0.006, d08_page2_bonus=0.004,
    entity_bonus=0.002, section_term_bonus=0.001, title_term_bonus=0.0005,
    dense_multiplier=0.0, workflow_bonus=(0.015, 0.012, 0.010), poster_bonus=0.015,
)
```

> `structured_bonus` 在 RRF 下取 0 而不是小正数：结构化表格命中改用 `structured_tier=2` 表达（整数层），再叠一个平铺分等于同一信号计两次。`index.py:337-338` 的 `+2_000.0` 在 Task 4 会变成 `scale.tier(scale.structured_tier)`。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_retrieval_scoring.py -q`

Expected: `15 passed`

- [ ] **Step 5: 给 `index.py` 补 logger，并把打分字面量换成 `scale.*`**

`services/agent_api/app/knowledge/index.py` import 区（`from dataclasses import dataclass` 之后）加：

```python
import logging
```

在 `ARCHIVE_SHA256` 之前加：

```python
LOGGER = logging.getLogger(__name__)
```

在 `from .documents import ...` 之后加：

```python
from .scoring import LEGACY, clamp_score, fusion_mode, scale_for
```

把 `retrieve()` 打分循环里的字面量逐个替换（**只换数字来源，不改任何判断条件**）：

| 现有代码（行号为改造前） | 改成 |
|---|---|
| `score += dense_scores.get(...) * 10.0`（322） | `score += dense_scores.get(item.record.chunk_id, 0.0) * scale.dense_multiplier` |
| `score += 2_000_000`（324） | `score += scale.tier(scale.role_tier)` |
| `score += 1_000_000`（329） | `score += scale.hint_bonus` |
| `* 10`（332） | `* scale.authority_multiplier` |
| `* 5`（334） | `* scale.required_role_multiplier` |
| `score += 250.0`（336） | `score += scale.required_source_bonus` |
| `score += 2_000.0`（338） | `score += scale.structured_bonus` |
| `score += 500.0`（340） | `score += scale.target_value_bonus` |
| `score += 100`（342） | `score += scale.figure_context_bonus` |
| `score += 12`（344） | `score += scale.table_section_bonus` |
| `score += 200`（346） | `score += scale.table_context_bonus` |
| `score += 100`（348） | `score += scale.threshold_bonus` |
| `score += 100`（350） | `score += scale.rock_section_bonus` |
| `score += 400`（352） | `score += scale.d09_rock_bonus` |
| `score += 150`（354） | `score += scale.d06_page2_bonus` |
| `score += 200`（356） | `score += scale.d06_target_bonus` |
| `score += 100`（358） | `score += scale.d08_page2_bonus` |
| `score += 8`（361） | `score += scale.entity_bonus` |
| `score += 3.0`（363） | `score += scale.section_term_bonus` |
| `score += 1.5`（365） | `score += scale.title_term_bonus` |
| `+3000/+2900/+2800`（372-374） | `+scale.workflow_bonus[0/1/2]` |
| `score+=3000`（376-377） | `score += scale.poster_bonus` |
| `ranked.append((float(score), item))`（366） | `ranked.append((clamp_score(float(score)), item))` |

在 `candidates = ...`（303）之前取一次模式与量级：

```python
        mode = fusion_mode()
        scale = scale_for(mode)
```

> `clamp_score` 在 LEGACY 下是恒等变换（所有加分都是正的，基础分也 > 0），加它只为 Task 5/6 引入可能为负的余弦分时不用回头再改一遍契约防线。

- [ ] **Step 6: 把 `_dense_scores` 的静默 except 换成显式降级**

替换 `index.py:121-147` 整个 `_dense_scores`：

```python
    def _dense_scores(self, query: str) -> dict[str, float]:
        """Semantic recall over precomputed vectors. Never encodes the corpus.

        Three outcomes, all logged: a store is attached -> real cosine scores;
        no store -> lexical-only by design (INFO, once); encoder failure ->
        explicit degradation (WARNING) instead of the old silent ``except``
        that made "disabled" and "broken" indistinguishable in production.
        """
        if self._dense_failed:
            return {}
        store = getattr(self, "embedding_store", None)
        if store is None:
            if not self._dense_store_warned:
                self._dense_store_warned = True
                LOGGER.info("dense channel disabled: no embedding store attached; lexical-only retrieval")
            return {}
        encoder = getattr(self, "_query_encoder", None)
        if encoder is None:
            if not self._dense_store_warned:
                self._dense_store_warned = True
                LOGGER.info("dense channel idle: store loaded (%d chunks) but no query encoder", store.count)
            return {}
        try:
            query_vector = encoder(query)
        except Exception as exc:  # noqa: BLE001 - 降级必须吞异常，但要留下证据
            self._dense_failed = True
            LOGGER.warning("dense channel degraded to lexical: %s: %s", type(exc).__name__, exc)
            return {}
        if query_vector is None:
            self._dense_failed = True
            LOGGER.warning("dense channel degraded to lexical: query encoder returned no vector")
            return {}
        return store.similarity(tuple(float(value) for value in query_vector))
```

在 `KnowledgeIndex.__init__` 里 `self._dense_failed = False` 之后加：

```python
        self._dense_store_warned = False
        self._query_encoder = None
```

> **注意**：这一步删掉了「首次查询现场编码整个语料」的旧路径（1.4-d 的 54.6s 冷启动）。Task 1 已经把预计算存储挂上来了，所以 `_dense_vectors` / `_dense_model` 两个属性在 Task 2 结束时成为死代码——**本 Task 一并删掉它们的初始化**（`__init__` 里的 `self._dense_vectors` / `self._dense_model`），避免留下第二条能触发批量编码的路径。如果 Task 1 尚未执行，先执行 Task 1。

- [ ] **Step 7: 跑既有测试，确认零回归**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_retrieval_scoring.py services/agent_api/tests/test_knowledge_embeddings.py -q
```

Expected: `6 + 15 + 10 = 31 passed`。其中 `test_unknown_query_is_success_with_no_evidence`（域外返回空）必须仍然通过——本 Task 没动门槛，它靠 `if not matched: continue` 守住。

- [ ] **Step 8: 跑 offline 全量，确认逐题不变**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t2/report.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t1/report.json tmp/hybrid-t2/report.json
```

Expected: `91/119`，且对比脚本报告 **0 处差异**。本 Task 是纯重构，**任何一题的状态或证据变化都说明量级表抄错了字面量**——回去逐行核对 Step 5 的对照表，不要往下走。

> 如果 `tmp/hybrid-t1/report.json` 不存在（Task 1 没跑或产物被清），先用 `git stash` 之外的方式重建基线：`git worktree` 不必，直接 `RAG_FUSION_MODE` 未设置时跑一次 offline 存成 `tmp/hybrid-t2/baseline.json`，与改造前 HEAD 的产物对比。最省事的做法是 Task 1 Step 11 就把 `tmp/hybrid-t1/report.json` 留着。

- [ ] **Step 9: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/scoring.py services/agent_api/app/knowledge/index.py services/agent_api/tests/test_retrieval_scoring.py
git commit -m "refactor: extract retrieval bonus scale and log dense degradation"
```

---

## Task 3：两阶段候选池——去掉 0.35 绝对覆盖率门槛

**目的**：把「门槛过滤」和「排名截断」分开。`lexical` 模式保持今天的绝对覆盖率门槛（行为逐位不变）；`rank` / `hybrid` 模式换成「词法前 N ∪ 契约文档全部片段」的排名截断，让 1.3 节里 `gate_pass=0/290` 那种「gold 排第 2 却被整体清空」的情况不再发生。

**本 Task 仍用 LEGACY 量级**，目的是把「去门槛」这一件事的收益单独量出来。量级切换留给 Task 4，两个变量不同时动。

**预期**：`lexical` offline 逐题不变；`rank` offline **很可能掉分**（候选池变大后，非契约文档的片段会挤进 top-5）。掉分是正常的，**本 Task 不调参**，只把数字记进「六、实测记录」。真正的收益要看改写集：t1/t2 的 `empty` 列应从 12/15 降到接近 0。

- [ ] **Step 1: 写 RED 测试**

创建 `services/agent_api/tests/test_hybrid_retrieval.py`：

```python
"""融合模式行为测试：门槛替换、候选池、域外仍为空。

索引加载约 7s，用 setUpClass 只加载一次；三个模式共享同一份索引，
靠 patch.dict 切换 RAG_FUSION_MODE（模式在 retrieve() 内部读取，不缓存）。
"""

import os
from unittest.mock import patch
import unittest

from services.agent_api.app.contracts import RetrievalRequest
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.knowledge.scoring import RANK_POOL_FLOOR, RANK_POOL_MULTIPLIER
from services.agent_api.app.profiles import ProfileRegistry

# 改写问法：gold 是 D04，但绕开了 index.py:257-261 的 topics 词表（分层/高价值/a层…），
# 所以 hinted_docs 为空 -> 0.35 门槛生效 -> lexical 模式返回空。见计划 1.3 basic-rag-005。
PARAPHRASE_QUERY = "哪些客人算是最值钱的那一批，判断标准写在哪里"
OUT_OF_DOMAIN_QUERY = "火星移民政策"


class FusionRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ProfileRegistry.defaults().get("chinook-music")
        cls.index = load_knowledge_index(KNOWLEDGE_ROOT, cls.profile, max_chars=800)

    def ask(self, query: str, mode: str, top_k: int = 5, **filters):
        with patch.dict(os.environ, {"RAG_FUSION_MODE": mode}):
            return self.index.retrieve(RetrievalRequest(
                retrieval_id=f"fusion-{mode}", profile_id="chinook-music",
                query=query, filters=filters, top_k=top_k))

    def test_lexical_mode_keeps_absolute_coverage_gate(self):
        """基线锚点：门槛在 lexical 下必须还在，否则「行为逐位不变」这句话是假的。"""
        response = self.ask(PARAPHRASE_QUERY, "lexical")
        self.assertEqual(response.status, "success")
        self.assertEqual(response.chunks, [])

    def test_rank_mode_replaces_gate_with_rank_truncation(self):
        """同一个改写问法，rank 模式不再返回空——这就是 0/15 的直接修复。"""
        response = self.ask(PARAPHRASE_QUERY, "rank")
        self.assertEqual(response.status, "success")
        self.assertTrue(response.chunks, "rank 模式仍返回空，说明门槛没被真正绕过")

    def test_rank_pool_is_bounded(self):
        """池子有上界：不许退化成「把 432 个片段全打分」。"""
        limit = max(5 * RANK_POOL_MULTIPLIER, RANK_POOL_FLOOR)
        lexical = [(1.0, item) for item in self.index.chunks]
        pool = self.index._candidate_pool(lexical, mode="rank", top_k=5, contract_docs=set())
        self.assertEqual(len(pool), limit)
        everything = self.index._candidate_pool(lexical, mode="lexical", top_k=5, contract_docs=set())
        self.assertEqual(len(everything), len(self.index.chunks))

    def test_contract_documents_are_always_in_pool(self):
        """Phase D 的强制选片段依赖契约文档在池里；漏了 orig 立刻崩（计划 1.2 注）。"""
        lexical = [(1.0, item) for item in self.index.chunks]
        pool = self.index._candidate_pool(lexical, mode="rank", top_k=5, contract_docs={"D06"})
        d06 = {item.record.doc_id for _, item in pool}
        self.assertIn("D06", d06)
        expected = sum(1 for item in self.index.chunks if item.record.doc_id == "D06")
        self.assertEqual(sum(1 for _, item in pool if item.record.doc_id == "D06"), expected)

    def test_out_of_domain_stays_empty_in_every_mode(self):
        """域外问题的空结果靠 `if not matched: continue` 守住，三个模式都不许松动。

        这条是 Task 6 的护栏：语义通道接入后，域外查询会拿到一堆低余弦分片段，
        只有 DENSE_FLOOR 能挡住它们。先在这里把「必须为空」钉死。
        """
        for mode in ("lexical", "rank", "hybrid"):
            response = self.ask(OUT_OF_DOMAIN_QUERY, mode, top_k=3)
            self.assertEqual((response.status, response.chunks), ("success", []), mode)

    def test_scores_stay_non_negative(self):
        for mode in ("lexical", "rank"):
            response = self.ask(PARAPHRASE_QUERY, mode)
            self.assertTrue(all(chunk.score is None or chunk.score >= 0 for chunk in response.chunks), mode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py -q`

Expected: `test_rank_mode_replaces_gate_with_rank_truncation` 失败（rank 模式此时等同 lexical，返回空），`_candidate_pool` 相关两条报 AttributeError。**注意 `PARAPHRASE_QUERY` 必须先在 lexical 下实测返回空**——如果它恰好命中了 `topics` 里的词（比如你把「分层」写进句子），第一条测试就会假绿。跑一次确认：

```powershell
.venv\Scripts\python.exe -X utf8 -c "
import sys; sys.path.insert(0, '.')
from services.agent_api.app.contracts import RetrievalRequest
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.profiles import ProfileRegistry
p = ProfileRegistry.defaults().get('chinook-music')
idx = load_knowledge_index(KNOWLEDGE_ROOT, p, max_chars=800)
r = idx.retrieve(RetrievalRequest(retrieval_id='x', profile_id='chinook-music', query='哪些客人算是最值钱的那一批，判断标准写在哪里', top_k=5))
print('chunks=', len(r.chunks), [c.doc_id for c in r.chunks])
"
```

Expected: `chunks= 0 []`。不是 0 就换一句绕开词表的改写，并把新句子同步进 `eval/agent/paraphrase_retrieval.jsonl` 的 t2 档。

- [ ] **Step 3: 抽出门槛判定**

`index.py`，在 `_dense_scores` 之后新增：

```python
    def _passes_coverage_gate(self, *, role_hit: bool, hinted_doc: bool, doc_id: str,
                             required_sources, matched, query_weight: float) -> bool:
        """lexical 模式专用的 0.35 绝对覆盖率门槛（原 index.py:317-318）。

        保留它只为「RAG_FUSION_MODE=lexical 就是今天的行为」这句承诺；
        rank/hybrid 模式改用 _candidate_pool 的排名截断。见计划 1.3：真实问法
        在这个门槛下的通过率是 0/290 量级，gold 排第 2 也会被整体清空。
        """
        if role_hit or hinted_doc or doc_id in required_sources or not query_weight:
            return True
        return sum(self._weight(term) for term in matched) / query_weight >= 0.35
```

> 这段是**原条件的等价重写**，不是新逻辑。原来是一个巨大的 `if not role_hit and (not hinted_doc and ... < 0.35): continue`，取反后就是上面的短路顺序。写完必须用 Step 8 的逐题对比证明等价。

- [ ] **Step 4: 抽出候选池**

紧接着新增：

```python
    def _candidate_pool(self, lexical: list[tuple[float, IndexedChunk]], *, mode: str,
                        top_k: int, contract_docs) -> list[tuple[float, IndexedChunk]]:
        """Phase B：决定哪些片段有资格进入加分与最终排序。

        lexical -> 全量（今天的行为）；rank/hybrid -> 词法前 N ∪ 契约文档全部片段。
        契约文档必须整体入池：Phase D（index.py:382-410）会为每个 required_source /
        hinted_doc 挑最佳片段插到队首，池子里没有它们就等于删掉了 orig 15/15 的真因。
        """
        if mode == FUSION_LEXICAL:
            return list(lexical)
        ordered = sorted(lexical, key=lambda pair: (-pair[0], pair[1].record.doc_id,
                                                    pair[1].record.chunk_id))
        limit = max(top_k * RANK_POOL_MULTIPLIER, RANK_POOL_FLOOR)
        pool = ordered[:limit]
        chosen = {item.record.chunk_id for _, item in pool}
        pool.extend(pair for pair in ordered
                    if pair[1].record.chunk_id not in chosen
                    and pair[1].record.doc_id in contract_docs)
        return pool
```

import 行相应改为：

```python
from .scoring import (FUSION_LEXICAL, LEGACY, RANK_POOL_FLOOR, RANK_POOL_MULTIPLIER,
                      clamp_score, fusion_mode, scale_for)
```

- [ ] **Step 5: 改造打分循环为 Phase A / B / C**

把 `index.py:303-366`（`candidates = ...` 到 `ranked.append(...)`）整体换成：

```python
        mode = fusion_mode()
        scale = scale_for(mode)
        contract_docs = set(required_sources) | set(hinted_docs)
        candidates = self.chunks if doc_id is None else self._document_chunks[doc_id]
        query_weight = sum(self._weight(term) for term in query_terms)
        dense_scores = self._dense_scores(request.query) if dense_requested else {}
        # Phase A：只算词法基础分，不做任何过滤（门槛只属于 lexical 模式）。
        lexical: list[tuple[float, IndexedChunk]] = []
        for item in candidates:
            if section and not (item.record.section == section or (item.record.section or "").startswith(str(section) + " / ")):
                continue
            matched = [term for term in query_terms if term in item.normalized_text]
            if not matched:
                continue
            role_hit = role and fact_matches(role, item.record.text)
            if mode == FUSION_LEXICAL and not self._passes_coverage_gate(
                    role_hit=bool(role_hit), hinted_doc=hinted_doc, doc_id=item.record.doc_id,
                    required_sources=required_sources, matched=matched, query_weight=query_weight):
                continue
            # 词频取对数，避免长段落靠重复高频词压过真正讲该主题的段落。
            base = sum(self._weight(term) * (1 + math.log(item.normalized_text.count(term))) for term in matched)
            lexical.append((base, item))
        # Phase B：候选池。lexical 全量，rank/hybrid 排名截断 ∪ 契约文档。
        pool = self._candidate_pool(lexical, mode=mode, top_k=request.top_k, contract_docs=contract_docs)
        # Phase C：业务加分（Task 4 会把基础分也换成 RRF 权重）。
        ranked: list[tuple[float, IndexedChunk]] = []
        for base, item in pool:
            score = base + dense_scores.get(item.record.chunk_id, 0.0) * scale.dense_multiplier
            if role and fact_matches(role, item.record.text):
                score += scale.tier(scale.role_tier)
            if item.record.doc_id in hinted_docs:
                # Configured whole-question vocabulary is a document-routing
                # signal. Keep lexical scoring for chunks inside that document,
                # but do not let a long recap win merely by repeating terms.
                score += scale.hint_bonus
            metadata = self.profile.document_metadata.get(item.record.doc_id)
            if metadata and answer_type:
                score += float(metadata.authority_scope.get(answer_type, 0)) * scale.authority_multiplier
            if metadata and required_roles:
                score += max((metadata.authority_scope.get(role_name, 0) for role_name in required_roles), default=0) * scale.required_role_multiplier
            if required_sources and item.record.doc_id in required_sources:
                score += scale.required_source_bonus
            if structured_matches and item.record.source_uri == structured_matches[0].source_uri:
                score += scale.structured_bonus
            if answer_type == "target_value" and "目标" in item.record.text:
                score += scale.target_value_bonus
            if "全图上下文" in (item.record.section or ""):
                score += scale.figure_context_bonus
            if "表格" in (item.record.section or ""):
                score += scale.table_section_bonus
            if "表格上下文" in (item.record.section or ""):
                score += scale.table_context_bonus
            if any(word in q for word in ("阈值", "分层", "a、b、c", "a层")) and any(word in item.record.text for word in ("阈值", "12.00", "5.00")) and "层级定义" in (item.record.section or ""):
                score += scale.threshold_bonus
            if any(word in q for word in ("运营", "建议", "重点")) and "Rock" in request.query and "Rock" in (item.record.section or ""):
                score += scale.rock_section_bonus
            if item.record.doc_id == "D09" and any(word in q for word in ("运营", "建议", "重点")) and "Rock" in item.record.text and "覆盖" in item.record.text:
                score += scale.d09_rock_bonus
            if item.record.doc_id == "D06" and (filters.get("slots", {}).get("month") or "月度" in q or "每月" in q) and item.record.page == 2:
                score += scale.d06_page2_bonus
            elif item.record.doc_id == "D06" and "目标" in q and "目标明细" in item.record.text:
                score += scale.d06_target_bonus
            if item.record.doc_id == "D08" and any(word in q for word in ("rock", "metal", "latin", "品类", "活动方案", "d10")) and item.record.page == 2:
                score += scale.d08_page2_bonus
            for entity in ("rock and roll", "heavy metal", "rock", "metal", "latin"):
                if entity in q and entity in item.normalized_text:
                    score += scale.entity_bonus
            if item.record.section and any(term in normalize(item.record.section) for term in query_terms):
                score += scale.section_term_bonus
            if any(term in normalize(item.record.title) for term in query_terms):
                score += scale.title_term_bonus
            ranked.append((clamp_score(float(score)), item))
```

**Phase D（原 367-434 行：workflow 加分、排序、强制选片段、facets、monthly_bundle、截断）一行都不改。** 它是 orig 15/15 的真因（计划 1.2 注），改了就等于同时动两个变量。

> 一处刻意的语义变化：原代码在循环内算 `role_hit` 一次并同时用于门槛与加分，上面拆成两次 `fact_matches` 调用（Phase A 一次、Phase C 一次）。`fact_matches` 是纯字符串匹配，重复调用只影响性能不影响结果；432 片段量级下可忽略。如果实测 offline 耗时明显上升，再把它塞进 `lexical` 的元组里带下来，**不要为此改变 Phase A/B/C 的边界**。

- [ ] **Step 6: 跑测试确认 GREEN**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_retrieval_scoring.py -q
```

Expected: `6 + 6 + 15 = 27 passed`（`test_hybrid_retrieval.py` 的 6 条全绿，其中 rank 模式那条从 RED 转 GREEN）。

- [ ] **Step 7: lexical 模式 offline 必须逐题不变**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t3/report-lexical.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t2/report.json tmp/hybrid-t3/report-lexical.json
```

Expected: `91/119` 且 **0 处差异**。这是本 Task 的硬红线：`_passes_coverage_gate` 的等价重写只要有一处布尔取反写错，这里立刻会暴露。**不为 0 就不要提交**，回去对比原 `index.py:317-318`。

- [ ] **Step 8: 记录 rank 模式的代价（不调参）**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_FUSION_MODE = "rank"
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t3/report-rank.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t3/report-lexical.json tmp/hybrid-t3/report-rank.json
Remove-Item Env:RAG_FUSION_MODE
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode rank -v --out tmp/hybrid-t3/recall-rank.json
```

Expected: offline 分数**允许掉**（掉多少记进「六、实测记录」，这是 Task 4 要修的对象）；改写集的 t1/t2 `empty` 列应从 12/15、15/15 显著下降。如果 t1/t2 的 `empty` 没降，说明门槛还在生效（检查 `mode == FUSION_LEXICAL` 的判断是否被 `patch.dict` 正确读到），**不是**说明方案无效。

> 跑完务必 `Remove-Item Env:RAG_FUSION_MODE`。这个变量留在 shell 里会污染后面所有测试与评测，是本计划最容易踩的坑。

- [ ] **Step 9: 跑全量单测，确认没新增失败**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests -q --no-header`

Expected: `454 + 15 + 10 + 6 + 6 = 491 collected`，failed 数不超过基线的 15（基线：15 failed / 439 passed）。

- [ ] **Step 10: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/index.py services/agent_api/tests/test_hybrid_retrieval.py docs/superpowers/plans/2026-10-14-hybrid-retrieval.md
git commit -m "feat: replace absolute coverage gate with rank truncation for rank fusion mode"
```

---

## Task 4：RRF 量级重标定——把百万级绝对加分换成排名量级

**目的**：`rank` / `hybrid` 模式下，基础分从「词法加权和」（量级 0–300，方差极大）换成 `rrf_weight(position) = 1/(60+名次)`（量级 0.0164 起，单调于词法排名），业务加分改用 `BonusScale.RRF`，`fact_role` 与结构化命中改用整数优先级层。这样加分才第一次真正有能力重排，而不是被基础分的方差淹没或被 `+1_000_000` 碾平。

**为什么必须换基础分**：LEGACY 量级下 `hint +1_000_000` 能压过一切，代价是「非契约文档之间完全由原始词法分决定」，而原始词法分偏爱长段落（1.4 的 `+400`/`+200` 那堆补丁就是为了修这个）。换成排名量级后，长段落只能靠名次拿分，堆词不再有效——**这也是后面敢删硬编码的前提**。

**预期**：`lexical` offline 逐题不变（LEGACY 逐位复刻）；`rank` offline 相对 Task 3 应当**回升**（加分重新有意义了），但仍可能低于 91，把数字记进「六、实测记录」；改写集 orig 必须守住 15/15（靠 Phase D 强制选片段 + hint 层）。前端「检索分数」会从 `1000047.52` 变成 `0.0234`，这是预期的（计划 §四已确认前端只做展示）。

- [ ] **Step 1: 写 RED 测试**

在 `services/agent_api/tests/test_retrieval_scoring.py` 的 `LegacyScaleTests` 里补一条（证明 LEGACY 的结构化信号仍走平铺分，不走层）：

```python
    def test_structured_signal_is_flat_in_legacy_and_tiered_in_rrf(self):
        self.assertEqual((LEGACY.structured_tier, LEGACY.structured_bonus), (0, 2_000.0))
        self.assertEqual(LEGACY.structured_bonus + LEGACY.tier(LEGACY.structured_tier), 2_000.0)
        self.assertEqual((RRF.structured_tier, RRF.structured_bonus), (2, 0.0))
        self.assertEqual(RRF.structured_bonus + RRF.tier(RRF.structured_tier), 2.0)
```

在 `services/agent_api/tests/test_hybrid_retrieval.py` 里追加一个 TestCase：

```python
class RrfMagnitudeTests(FusionRetrievalTests):
    """量级重标定的可观测后果。继承 setUpClass 复用同一份索引。"""

    def test_lexical_scores_stay_in_absolute_regime(self):
        """锚点：lexical 模式的分数必须还是百万级，否则 LEGACY 被误改。"""
        response = self.ask("2025年第三季度经营目标是多少", "lexical", top_k=5)
        self.assertTrue(response.chunks)
        self.assertGreater(response.chunks[0].score, 1_000.0)

    def test_rank_scores_are_rrf_magnitude(self):
        """rank 模式下没有 role/structured 命中时，分数必须落在 1 以下。"""
        response = self.ask(PARAPHRASE_QUERY, "rank", top_k=5)
        self.assertTrue(response.chunks)
        self.assertLess(max(chunk.score for chunk in response.chunks), 1.0)

    def test_rank_mode_keeps_hinted_document_on_top(self):
        """hint 层（0.030）必须仍然压过非契约文档的名次分，否则 orig 会崩。"""
        response = self.ask("2025年第三季度经营目标是多少", "rank", top_k=5)
        self.assertEqual(response.chunks[0].doc_id, "D06")

    def test_role_hit_climbs_to_priority_tier_in_rank_mode(self):
        """fact_role 命中 = 第 3 层，分数 ≥ 3.0，与平铺加分完全隔开。"""
        response = self.ask("2025 Q3 Rock 销售额目标 50.00 美元", "rank",
                            top_k=3, doc_id="D06", fact_role="quarterly_target")
        self.assertEqual(response.status, "success")
        if response.chunks:
            self.assertGreaterEqual(response.chunks[0].score, 3.0)

    def test_structured_hit_climbs_to_second_tier_in_rank_mode(self):
        response = self.ask("2025年9月的合计销售额目标", "rank", top_k=3,
                            answer_type="target_value", slots={"year": 2025, "month": 9})
        self.assertEqual(response.status, "success")
        scores = [chunk.score for chunk in response.chunks]
        self.assertTrue(any(score >= 2.0 for score in scores), scores)

    def test_score_ordering_is_strictly_non_increasing(self):
        for mode in ("lexical", "rank"):
            response = self.ask("2025年第三季度经营目标是多少", mode, top_k=5)
            scores = [chunk.score for chunk in response.chunks]
            self.assertEqual(scores, sorted(scores, reverse=True), mode)
```

> `fact_role` / `answer_type` / `slots` 的取值必须与 `test_knowledge_index.py` 里既有用例一致（例如 `fact_role` 只能配 `FACTS` 里登记的角色，且 `FACTS[role][0]` 必须等于 `doc_id`，否则 `retrieve()` 直接返回 `INVALID_REQUEST`）。写之前先 `rg "fact_role" services/agent_api/tests` 抄一个能跑通的组合，不要凭空造。

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_retrieval_scoring.py -q`

Expected: `test_rank_scores_are_rrf_magnitude` 失败（此时 rank 仍用原始词法基础分，分数远大于 1）；其余量级相关的 RED 视既有硬编码命中情况而定。

- [ ] **Step 3: 基础分换成 RRF 权重**

`index.py`，import 行补 `rrf_weight`：

```python
from .scoring import (FUSION_LEXICAL, LEGACY, RANK_POOL_FLOOR, RANK_POOL_MULTIPLIER,
                      clamp_score, fusion_mode, rrf_weight, scale_for)
```

Phase C 的循环头（Task 3 Step 5 写的那段）改成：

```python
        ranked: list[tuple[float, IndexedChunk]] = []
        for position, (base, item) in enumerate(pool, start=1):
            # pool 已经按词法基础分降序（_candidate_pool 保持 ordered 的顺序），
            # 所以 enumerate 出来的 position 就是词法名次，可直接换成 RRF 权重。
            foundation = base if mode == FUSION_LEXICAL else rrf_weight(position)
            score = foundation + dense_scores.get(item.record.chunk_id, 0.0) * scale.dense_multiplier
```

结构化命中改成「平铺分 + 优先级层」的单一表达式（LEGACY 下 `tier(0)=0`，等价于原来的 `+2_000.0`）：

```python
            if structured_matches and item.record.source_uri == structured_matches[0].source_uri:
                score += scale.structured_bonus + scale.tier(scale.structured_tier)
```

> Task 2 的对照表已经把 Phase D 里 `dependency_priority` 的 `+3000/+2900/+2800` 换成了 `scale.workflow_bonus[...]`、`+3000` 换成 `scale.poster_bonus`。Task 3 说的「Phase D 一行不改」指的是**不改它的逻辑与顺序**，常量来源的替换在 Task 2 就做完了。这里再确认一次：`dependency_priority` 是 `retrieve()` 内的闭包，直接读外层的 `scale` 即可，不需要传参。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_retrieval_scoring.py services/agent_api/tests/test_knowledge_index.py -q`

Expected: `12 + 16 + 6 = 34 passed`

- [ ] **Step 5: lexical 逐题不变（硬红线）**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t4/report-lexical.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t3/report-lexical.json tmp/hybrid-t4/report-lexical.json
```

Expected: `91/119` 且 **0 处差异**。LEGACY 是逐位复刻的，这里出现任何差异都说明 Step 3 的 `foundation` 分支写错（例如把 `mode == FUSION_LEXICAL` 判断漏掉）。

- [ ] **Step 6: 记录 rank 模式的量级收益**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_FUSION_MODE = "rank"
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t4/report-rank.json
Remove-Item Env:RAG_FUSION_MODE
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t3/report-rank.json tmp/hybrid-t4/report-rank.json
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode rank -v --out tmp/hybrid-t4/recall-rank.json
```

Expected: rank offline **相对 Task 3 回升**（回升多少记进「六、实测记录」）；改写集 `orig` 必须 15/15。若 orig 掉了，先看是不是 `hint_bonus=0.030` 不够压过名次分——**只允许在 `scoring.py` 里调 RRF 常量，不允许回去改判断条件或加新硬编码**。

- [ ] **Step 7: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/index.py services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_retrieval_scoring.py docs/superpowers/plans/2026-10-14-hybrid-retrieval.md
git commit -m "feat: rescale retrieval bonuses to rrf magnitude for rank fusion mode"
```

---

## Task 5：语义通道接入——预计算向量、DENSE_FLOOR 标定、编码器接缝

**目的**：让 `_dense_scores` 真正可用：只编码查询（~30ms），从 Task 1 的预计算存储取片段向量做点积，并用实测标定出的 `DENSE_FLOOR` 截断。本 Task **不改 `retrieve()` 的排序**（RRF 量级下 `dense_multiplier=0`，语义分暂时不参与加法），只交付「一条可信、可标定、可降级的语义排名」，供 Task 6 融合。

**为什么单独一个 Task**：语义通道有两个独立的失败面——编码器（模型加载、超时、异常）和门槛（阈值定高了召回归零，定低了域外问题会返回垃圾）。把它们和融合排序一起改，出问题时无法判断是哪一面。

- [ ] **Step 1: 写 RED 测试**

创建 `services/agent_api/tests/test_dense_retrieval.py`：

```python
"""语义通道：合成向量下的打分、门槛、降级日志。不依赖 fastembed，不需要模型缓存。"""

from array import array
import os
from unittest.mock import patch
import unittest

from services.agent_api.app.knowledge.embeddings import EmbeddingStore, normalize_query
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index, normalize
from services.agent_api.app.knowledge.scoring import dense_floor
from services.agent_api.app.profiles import ProfileRegistry


def fake_store(chunk_ids, rows):
    dim = len(rows[0])
    flat: list[float] = []
    for row in rows:
        norm = sum(value * value for value in row) ** 0.5 or 1.0
        flat.extend(value / norm for value in row)
    return EmbeddingStore(model_name="fake", snapshot_version="x", max_chars=800, dim=dim,
                          chunk_ids=tuple(chunk_ids), flat=array("f", flat))


class DenseFloorTests(unittest.TestCase):
    def test_floor_takes_the_stricter_of_absolute_and_relative(self):
        scores = {"a": 0.80, "b": 0.60, "c": 0.30}
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.10", "RAG_DENSE_FLOOR_RATIO": "0.75"}):
            self.assertAlmostEqual(dense_floor(scores), 0.60, places=9)  # 0.80 * 0.75
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.70", "RAG_DENSE_FLOOR_RATIO": "0.10"}):
            self.assertAlmostEqual(dense_floor(scores), 0.70, places=9)

    def test_empty_scores_use_absolute_floor_only(self):
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.42", "RAG_DENSE_FLOOR_RATIO": "0.9"}):
            self.assertAlmostEqual(dense_floor({}), 0.42, places=9)

    def test_malformed_env_falls_back_to_defaults(self):
        """环境变量写错不能让检索崩，退回默认值（默认 0 = 不截断）。"""
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "abc", "RAG_DENSE_FLOOR_RATIO": ""}):
            self.assertEqual(dense_floor({"a": 0.5}), 0.0)


class DenseChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ProfileRegistry.defaults().get("chinook-music")
        cls.index = load_knowledge_index(KNOWLEDGE_ROOT, cls.profile, max_chars=800)
        cls.ids = tuple(item.record.chunk_id for item in cls.index.chunks[:3])

    def build(self, rows, encoder):
        self.index.attach_embedding_store(fake_store(self.ids, rows), encoder)
        self.addCleanup(self.index.attach_embedding_store, None, None)
        self.index._dense_failed = False
        self.index._dense_store_warned = False

    def test_scores_are_cosine_and_ranked(self):
        self.build([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], lambda query: (1.0, 0.0))
        scores = self.index._dense_scores("随便")
        self.assertEqual(sorted(scores, key=scores.get, reverse=True), list(self.ids))
        self.assertAlmostEqual(scores[self.ids[0]], 1.0, places=5)

    def test_dense_ranking_applies_floor_and_limit(self):
        self.build([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], lambda query: (1.0, 0.0))
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.0", "RAG_DENSE_FLOOR_RATIO": "0.75"}):
            self.assertEqual(self.index.dense_ranking("随便"), [self.ids[0]])  # 0.6 < 0.75
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.0", "RAG_DENSE_FLOOR_RATIO": "0.0"}):
            self.assertEqual(self.index.dense_ranking("随便"), list(self.ids))

    def test_encoder_is_called_once_per_query_and_never_encodes_corpus(self):
        """红线：编码器只吃查询字符串。一旦有人把语料塞进去，就是 54.6s 冷启动复发。"""
        calls = []

        def encoder(query):
            calls.append(query)
            return (1.0, 0.0)

        self.build([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], encoder)
        self.index._dense_scores("第一个问题")
        self.index._dense_scores("第二个问题")
        self.assertEqual(calls, ["第一个问题", "第二个问题"])

    def test_encoder_failure_degrades_with_a_warning(self):
        def boom(query):
            raise RuntimeError("model cache missing")

        self.build([[1.0, 0.0]], boom)
        with self.assertLogs("services.agent_api.app.knowledge.index", level="WARNING") as captured:
            self.assertEqual(self.index._dense_scores("随便"), {})
        self.assertTrue(any("degraded" in line for line in captured.output), captured.output)
        self.assertTrue(self.index._dense_failed)
        self.assertEqual(self.index._dense_scores("再问一次"), {})  # 熔断，不重试

    def test_missing_store_is_info_not_warning(self):
        """全新克隆没有向量文件是正常状态，不该刷 WARNING（计划 Task 1 Step 10 的同一口径）。"""
        self.index.attach_embedding_store(None, None)
        self.addCleanup(self.index.attach_embedding_store, None, None)
        self.index._dense_failed = False
        self.index._dense_store_warned = False
        with self.assertLogs("services.agent_api.app.knowledge.index", level="INFO") as captured:
            self.assertEqual(self.index._dense_scores("随便"), {})
        self.assertFalse(any("WARNING" in line for line in captured.output), captured.output)


class QueryNormalizationTests(unittest.TestCase):
    def test_embeddings_normalize_matches_index_normalize(self):
        """查询与语料必须用同一套归一化，否则余弦分系统性偏低。"""
        for sample in ("2025 Q3 Rock 目标", "Ａ层客户", "  重金属/Metal  "):
            self.assertEqual(normalize_query(sample), normalize(sample))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_dense_retrieval.py -q`

Expected: ImportError（`dense_floor` / `normalize_query` / `dense_ranking` 都还不存在）。

- [ ] **Step 3: `scoring.py` 加门槛算子**

```python
DENSE_FLOOR_ENV = "RAG_DENSE_FLOOR"
DENSE_FLOOR_RATIO_ENV = "RAG_DENSE_FLOOR_RATIO"
# 标定后回填（Step 5）。默认 0 = 不截断，只用于让单测可以显式控制。
DEFAULT_DENSE_FLOOR = 0.0
DEFAULT_DENSE_FLOOR_RATIO = 0.0
# 语义通道最多贡献多少条候选。取 60 ≈ RRF_K，够 RRF 收敛，又不会把域外噪声全放进来。
DENSE_RANK_LIMIT = 60


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def dense_floor(scores: dict[str, float]) -> float:
    """绝对门槛与相对门槛取严（max）。

    相对门槛（top1 × ratio）是主判据：换语料、换模型、换切片长度后不用重新标定，
    它跟着分布走。绝对门槛是兜底，防止「整条查询都很烂但 top1 相对分仍然过线」。
    """
    absolute = _float_env(DENSE_FLOOR_ENV, DEFAULT_DENSE_FLOOR)
    ratio = _float_env(DENSE_FLOOR_RATIO_ENV, DEFAULT_DENSE_FLOOR_RATIO)
    if not scores:
        return absolute
    return max(absolute, max(scores.values()) * ratio)
```

- [ ] **Step 4: `embeddings.py` 加查询编码器，`index.py` 加 `dense_ranking`**

`embeddings.py` import 区补 `import unicodedata`，并在 `model_slug` 之前加：

```python
def normalize_query(query: str) -> str:
    """与 index.normalize 完全一致（NFKC + casefold）。

    不 import index 是为了避免循环依赖（index 已经 import embeddings）。
    语料向量是用 normalized_text 编码的，查询必须走同一套归一化，
    否则全角/大小写差异会让余弦分系统性偏低。
    """
    return unicodedata.normalize("NFKC", query).casefold()
```

在 `load_embedding_store` 之后加：

```python
def build_query_encoder(model_name: str | None = None, cache_dir: str | None = None):
    """惰性初始化的查询编码器：query -> 单位向量。

    只编码单条查询（实测 ~30ms），模型初始化 ~0.19s 发生在首次调用。
    批量编码语料（~55s）是 scripts/build_knowledge_embeddings.py 的职责，
    这里绝不触碰 self.chunks——测试用 test_encoder_is_called_once_per_query 钉死。
    """
    model_name = model_name or os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_MODEL)
    cache_dir = cache_dir or os.getenv("RAG_EMBEDDING_CACHE")
    state: dict[str, object] = {}

    def encode(query: str) -> tuple[float, ...]:
        model = state.get("model")
        if model is None:
            from fastembed import TextEmbedding
            model = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=2)
            state["model"] = model
            LOGGER.info("query encoder ready: %s (cache=%s)", model_name, cache_dir or "fastembed default")
        vector = next(model.embed([normalize_query(query)]))
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(value / norm for value in values)

    return encode
```

`index.py` import 改为：

```python
from .embeddings import EmbeddingStore, build_query_encoder, load_embedding_store
from .scoring import (DENSE_RANK_LIMIT, FUSION_HYBRID, FUSION_LEXICAL, LEGACY, RANK_POOL_FLOOR,
                      RANK_POOL_MULTIPLIER, clamp_score, dense_floor, fusion_mode, rrf_weight, scale_for)
```

在 `_dense_scores` 之后加：

```python
    def dense_ranking(self, query: str) -> list[str]:
        """语义通道的候选排名（chunk_id，best first），已按 DENSE_FLOOR 截断。

        门槛是这个方法唯一的防线：域外问题（「火星移民政策」）在向量空间里
        一样会拿到 0.3-0.5 的余弦分，没有门槛就会污染 top_k。标定见
        scripts/measure_dense_floor.py 与计划 §六。
        """
        scores = self._dense_scores(query)
        if not scores:
            return []
        floor = dense_floor(scores)
        ranked = sorted(((score, chunk_id) for chunk_id, score in scores.items() if score >= floor),
                        key=lambda pair: (-pair[0], pair[1]))
        return [chunk_id for _, chunk_id in ranked[:DENSE_RANK_LIMIT]]
```

改 `load_knowledge_index` 里 Task 1 写的挂载段，让编码器只在需要时构造：

```python
    index = KnowledgeIndex(manifest, tuple(chunks), profile, structured_records)
    store = load_embedding_store(
        root=root,
        chunk_ids=tuple(item.record.chunk_id for item in index.chunks),
        snapshot_version=knowledge_snapshot_version(index),
        max_chars=max_chars,
    )
    # 语义通道只在 hybrid 模式或显式打开 dense 时才需要编码器；
    # 惰性构造，避免每个进程都付 0.19s 模型初始化。
    dense_wanted = fusion_mode() == FUSION_HYBRID or os.getenv("RAG_ENABLE_DENSE", "0") == "1"
    index.attach_embedding_store(store, build_query_encoder() if (store and dense_wanted) else None)
    return index
```

> `fusion_mode()` 在 `load_knowledge_index` 里读一次环境变量。索引是进程级缓存的，所以**运行中改 `RAG_FUSION_MODE` 不会给已加载的索引补上编码器**——这是刻意的（避免热切换时半开状态），文档里要写清楚：切模式必须重启进程。评测脚本 `eval_retrieval_recall.py` 在 `run()` 里先设环境变量再 import/加载索引，天然满足这个顺序。

- [ ] **Step 5: 标定 DENSE_FLOOR**

创建 `scripts/measure_dense_floor.py`：

```python
"""实测域内/域外余弦分布，标定 DENSE_FLOOR。

换语料、换模型、换 max_chars 之后必须重跑，并把结果回填 scoring.py 的
DEFAULT_DENSE_FLOOR / DEFAULT_DENSE_FLOOR_RATIO 与计划 §六。

用法:
    python scripts/measure_dense_floor.py
    python scripts/measure_dense_floor.py --ratios 0.60,0.65,0.70,0.75,0.80
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 域外样本：与 D01-D12 知识快照毫无关系的问题。门槛的松紧全靠它们判定。
OUT_OF_DOMAIN = (
    "火星移民政策", "如何做红烧肉", "2024年美国大选结果", "量子纠缠的实验验证",
    "Python 的 GIL 是什么", "北京到上海的高铁票价", "如何治疗失眠",
    "股票开户需要哪些材料", "世界杯小组赛赛程", "怎么申请信用卡",
)


def quantiles(values: list[float]) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)

    def pick(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]

    return (f"min={ordered[0]:.3f} p25={pick(0.25):.3f} p50={pick(0.50):.3f} "
            f"p75={pick(0.75):.3f} max={ordered[-1]:.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="标定语义检索门槛")
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--ratios", default="0.60,0.65,0.70,0.75,0.80,0.85")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    os.environ.setdefault("RAG_EMBEDDING_CACHE", str(ROOT / "tmp" / "embedding-models"))
    from services.agent_api.app.contracts import RetrievalRequest
    from services.agent_api.app.knowledge.embeddings import build_query_encoder
    from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
    from services.agent_api.app.profiles import ProfileRegistry

    profile = ProfileRegistry.defaults().get("chinook-music")
    index = load_knowledge_index(KNOWLEDGE_ROOT, profile, max_chars=args.max_chars)
    if index.embedding_store is None:
        raise SystemExit("向量存储没加载上，先跑 scripts/build_knowledge_embeddings.py")
    encode = build_query_encoder()
    index.attach_embedding_store(index.embedding_store, encode)

    from scripts.eval_retrieval_recall import load_cases
    cases = load_cases()
    gold_scores: list[float] = []
    top1_scores: list[float] = []
    gold_ranks: list[int] = []
    for case in cases:
        scores = index._dense_scores(case["question"])
        if not scores:
            continue
        ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        top1_scores.append(ordered[0][1])
        gold = set(case["expected_documents"])
        index_of_gold = next((position for position, (chunk_id, _) in enumerate(ordered, start=1)
                              if chunk_id.split("#")[0] in gold), 0)
        gold_ranks.append(index_of_gold)
        gold_scores.append(ordered[index_of_gold - 1][1] if index_of_gold else 0.0)

    outside: list[float] = []
    for query in OUT_OF_DOMAIN:
        scores = index._dense_scores(query)
        if scores:
            outside.append(max(scores.values()))

    print(f"== 域内（{len(cases)} 题 × 3 档，max_chars={args.max_chars}）==")
    print(f"  top1 余弦   {quantiles(top1_scores)}")
    print(f"  gold 余弦   {quantiles(gold_scores)}")
    hit = sum(1 for rank in gold_ranks if rank and rank <= args.top)
    print(f"  gold 进 top{args.top}：{hit}/{len(gold_ranks)}")
    print(f"== 域外（{len(OUT_OF_DOMAIN)} 题）==")
    print(f"  top1 余弦   {quantiles(outside)}")

    print("== 相对门槛扫描（top1 × ratio）==")
    print(f"{'ratio':>7}{'域内 gold 保留':>16}{'域外全部拦下':>16}")
    for ratio in [float(item) for item in args.ratios.split(",") if item.strip()]:
        kept = sum(1 for top1, gold in zip(top1_scores, gold_scores) if gold >= top1 * ratio)
        blocked = sum(1 for value in outside if value < max(outside) * ratio) if outside else 0
        print(f"{ratio:>7.2f}{kept:>10}/{len(gold_scores):<5}"
              f"{sum(1 for value in outside if value * 1.0 < (max(top1_scores) * ratio)):>10}/{len(outside):<5}")
    print("\n决策规则：选『域内 gold 保留率 ≥ 90%』里最大的 ratio，同时要求域外 top1 全部落在门槛下。")
    print("把选中的值回填 scoring.DEFAULT_DENSE_FLOOR_RATIO，并抄进计划 §六。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/build_knowledge_embeddings.py --max-chars 800
.venv\Scripts\python.exe -X utf8 scripts/measure_dense_floor.py --max-chars 800
.venv\Scripts\python.exe -X utf8 scripts/measure_dense_floor.py --max-chars 1200
```

> 两个切片都要标定：测试用 800、生产用 1200，片段不同余弦分布也不同。**以 1200 的结果为准回填默认值**，800 的结果记在 §六 里备查。如果两者选出的 ratio 差超过 0.05，说明分布对切片敏感，那就改用绝对门槛为主判据，并把这个结论写进 §六。

- [ ] **Step 6: 回填默认门槛并跑测试**

把 Step 5 选中的值写进 `scoring.py`：

```python
DEFAULT_DENSE_FLOOR = 0.0        # 绝对门槛：填实测域外 top1 的最大值（留 0.02 余量）
DEFAULT_DENSE_FLOOR_RATIO = 0.0  # 相对门槛：填扫描选中的 ratio
```

同步写进 `.env.example`（带注释说明「换语料/换模型后必须重跑 `scripts/measure_dense_floor.py`」）。

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_dense_retrieval.py services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_knowledge_index.py -q
```

Expected: `9 + 12 + 6 = 27 passed`。`test_out_of_domain_stays_empty_in_every_mode` 必须仍然绿——本 Task 没把语义分接进排序，域外仍靠 `if not matched: continue` 挡住。

- [ ] **Step 7: 验证首查延迟（AGENT_RAG_TIMEOUT_MS=5000 的约束）**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_FUSION_MODE = "hybrid"
.venv\Scripts\python.exe -X utf8 -c "
import sys, time; sys.path.insert(0, '.')
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.profiles import ProfileRegistry
p = ProfileRegistry.defaults().get('chinook-music')
t0 = time.monotonic(); idx = load_knowledge_index(KNOWLEDGE_ROOT, p, max_chars=1200); t1 = time.monotonic()
r1 = idx.dense_ranking('2025年第三季度经营目标是多少'); t2 = time.monotonic()
r2 = idx.dense_ranking('哪些客人算是最值钱的那一批'); t3 = time.monotonic()
print(f'索引+向量加载 {t1-t0:6.2f}s  首查(含模型初始化) {t2-t1:6.3f}s  次查 {t3-t2:6.3f}s')
print(f'首查排名 {len(r1)} 条 top3={r1[:3]}')
"
Remove-Item Env:RAG_FUSION_MODE
```

Expected: `首查 ≤ 0.6s`（0.19s 模型初始化 + 0.03s 编码 + 0.03s 点积 + 加载余量），`次查 ≤ 0.08s`，两者都远低于 5000ms 的检索超时。**首查超过 1s 就不要往下走**——先确认 `RAG_EMBEDDING_CACHE` 指向本地已有模型缓存，网络下载会把它推到几十秒。

- [ ] **Step 8: offline 全量确认零变化**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t5/report-lexical.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t4/report-lexical.json tmp/hybrid-t5/report-lexical.json
```

Expected: `91/119` 且 **0 处差异**。本 Task 只新增了「尚未被排序消费」的语义排名，`dense_multiplier=0`，任何差异都说明误改了 Phase A/C。

- [ ] **Step 9: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/scoring.py services/agent_api/app/knowledge/embeddings.py services/agent_api/app/knowledge/index.py scripts/measure_dense_floor.py services/agent_api/tests/test_dense_retrieval.py .env.example docs/superpowers/plans/2026-10-14-hybrid-retrieval.md
git commit -m "feat: add calibrated dense retrieval channel over precomputed embeddings"
```

---

## Task 6：RRF 融合排序与零词法兜底

**目的**：`hybrid` 模式下把基础分从「单一词法名次」换成 `fuse_rrf([词法排名, 语义排名])`，并允许**零词法命中的片段经语义通道入池**。这是把天花板从 51.1%（纯词法）抬到 75.6%（RRF）的那一步，也是本计划唯一会改变 `hybrid` 端到端行为的 Task。

**为什么零词法兜底要单独设门槛**：`index.py:310` 的 `if not matched: continue` 是域外问题返回空的唯一防线（`test_unknown_query_is_success_with_no_evidence`）。放开它以后，防线就完全落在 `DENSE_FLOOR` 上——所以 Task 5 的标定必须先做完，且 Task 6 的测试里必须有一条「域外仍为空」。

- [ ] **Step 1: 写 RED 测试**

在 `services/agent_api/tests/test_hybrid_retrieval.py` 追加（沿用 `FusionRetrievalTests.ask`）：

```python
class HybridFusionTests(FusionRetrievalTests):
    """融合机制的结构性验证：用合成向量，不依赖 fastembed 与模型缓存。"""

    def setUp(self):
        # 取三个真实片段：一个与查询有词法重叠，两个完全没有。
        self.hit = self.index.chunks[0]
        self.miss_a = next(item for item in self.index.chunks[1:] if item.record.doc_id != self.hit.record.doc_id)
        self.miss_b = next(item for item in self.index.chunks[2:] if item.record.chunk_id != self.miss_a.record.chunk_id)
        self.ids = (self.hit.record.chunk_id, self.miss_a.record.chunk_id, self.miss_b.record.chunk_id)

    def attach(self, rows, encoder):
        from services.agent_api.tests.test_dense_retrieval import fake_store
        self.index.attach_embedding_store(fake_store(self.ids, rows), encoder)
        self.addCleanup(self.index.attach_embedding_store, None, None)
        self.index._dense_failed = False
        self.index._dense_store_warned = False

    def test_dense_only_chunk_enters_pool_in_hybrid_only(self):
        """零词法命中的片段只有 hybrid 能捞回来；lexical/rank 必须仍然看不见它。"""
        # 语义空间里 miss_b 与查询最像，而它的文本与查询没有任何公共词。
        self.attach([[0.2, 0.98], [0.3, 0.95], [1.0, 0.0]], lambda query: (1.0, 0.0))
        query = self.hit.normalized_text[:12]
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.0", "RAG_DENSE_FLOOR_RATIO": "0.0"}):
            hybrid = self.ask(query, "hybrid", top_k=5)
            rank = self.ask(query, "rank", top_k=5)
        self.assertIn(self.miss_b.record.chunk_id, {chunk.chunk_id for chunk in hybrid.chunks})
        self.assertNotIn(self.miss_b.record.chunk_id, {chunk.chunk_id for chunk in rank.chunks})

    def test_dense_floor_blocks_low_similarity_chunks(self):
        """同一个合成场景，门槛抬高后 miss_b 必须被挡在外面——域外防线就靠它。"""
        self.attach([[0.2, 0.98], [0.3, 0.95], [1.0, 0.0]], lambda query: (1.0, 0.0))
        query = self.hit.normalized_text[:12]
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.0", "RAG_DENSE_FLOOR_RATIO": "0.90"}):
            hybrid = self.ask(query, "hybrid", top_k=5)
        self.assertNotIn(self.miss_b.record.chunk_id, {chunk.chunk_id for chunk in hybrid.chunks})

    def test_consensus_chunk_beats_single_channel_leader(self):
        """两通道都认可的片段应当排第一，这是 RRF 的定义性质。"""
        self.attach([[0.6, 0.8], [1.0, 0.0], [0.9, 0.44]], lambda query: (1.0, 0.0))
        query = self.hit.normalized_text[:12]
        with patch.dict(os.environ, {"RAG_DENSE_FLOOR": "0.0", "RAG_DENSE_FLOOR_RATIO": "0.0"}):
            response = self.ask(query, "hybrid", top_k=3)
        self.assertEqual(response.chunks[0].chunk_id, self.hit.record.chunk_id)

    def test_out_of_domain_stays_empty_with_dense_enabled(self):
        """域外问题在 hybrid 下仍必须为空。真实编码器在场时这条才真正考验 DENSE_FLOOR。"""
        response = self.ask(OUT_OF_DOMAIN_QUERY, "hybrid", top_k=3)
        self.assertEqual((response.status, response.chunks), ("success", []))

    def test_explicit_dense_filter_false_disables_channel(self):
        """filters 里显式关掉语义检索时，hybrid 必须退化为 rank，尊重调用方契约。"""
        response = self.ask(PARAPHRASE_QUERY, "hybrid", top_k=5, dense_retrieval=False)
        baseline = self.ask(PARAPHRASE_QUERY, "rank", top_k=5)
        self.assertEqual([chunk.chunk_id for chunk in response.chunks],
                         [chunk.chunk_id for chunk in baseline.chunks])
```

> 合成向量的数值是随手取的，**必须先在本地跑一遍确认 RED/GREEN 的方向对**：`fake_store` 会做归一化，所以 `[0.2, 0.98]` 与 `[1.0, 0.0]` 的点积约 0.196。如果某条测试因为向量数值巧合而假绿，调整数值而不是放宽断言。

- [ ] **Step 2: 跑测试确认 RED**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py -q`

Expected: `test_dense_only_chunk_enters_pool_in_hybrid_only` 与 `test_consensus_chunk_beats_single_channel_leader` 失败（hybrid 此时等同 rank）。

- [ ] **Step 3: `scoring.py` 加语义池上限**

```python
# 语义通道最多为候选池贡献多少条「零词法命中」的片段。
# 太小则改写问法捞不回 gold，太大则域外噪声有机会挤进 top_k；20 条在 425 片段语料上实测够用。
DENSE_POOL_LIMIT = 20
```

- [ ] **Step 4: 候选池接受语义通道贡献的片段**

`index.py` 的 `_candidate_pool` 加一个参数：

```python
    def _candidate_pool(self, lexical: list[tuple[float, IndexedChunk]], *, mode: str,
                        top_k: int, contract_docs, extra_ids: tuple[str, ...] = ()) -> list[tuple[float, IndexedChunk]]:
        if mode == FUSION_LEXICAL:
            return list(lexical)
        ordered = sorted(lexical, key=lambda pair: (-pair[0], pair[1].record.doc_id,
                                                    pair[1].record.chunk_id))
        limit = max(top_k * RANK_POOL_MULTIPLIER, RANK_POOL_FLOOR)
        pool = ordered[:limit]
        chosen = {item.record.chunk_id for _, item in pool}
        pool.extend(pair for pair in ordered
                    if pair[1].record.chunk_id not in chosen
                    and pair[1].record.doc_id in contract_docs)
        chosen.update(item.record.chunk_id for _, item in pool)
        # 零词法命中的片段只能由语义通道带进来（extra_ids 已过 DENSE_FLOOR）。
        # 基础分记 0.0：它在 Phase C 里由 fused 分数取代，这里的 0.0 只是占位。
        if extra_ids:
            by_id = {item.record.chunk_id: item for item in self.chunks}
            pool.extend((0.0, by_id[chunk_id]) for chunk_id in extra_ids
                        if chunk_id not in chosen and chunk_id in by_id)
        return pool
```

- [ ] **Step 5: `retrieve()` 接入融合基础分**

Phase A 的循环里放宽零命中过滤（**只对 hybrid 放宽**）：

```python
        dense_enabled = dense_requested or (mode == FUSION_HYBRID and "dense_retrieval" not in filters)
        dense_ids = self.dense_ranking(request.query) if dense_enabled else []
        dense_scores = self._dense_scores(request.query) if (dense_enabled and dense_ids) else {}
```

（替换 Task 3 写的 `dense_scores = self._dense_scores(...) if dense_requested else {}` 那一行；注意 `dense_ids` 与 `dense_scores` 各调一次 `_dense_scores`，编码器会被调用两次 ≈ 60ms。若实测超时紧张，把 `_dense_scores` 的结果缓存到局部变量后同时算出 `dense_ids`，**不要**为此改动 `dense_ranking` 的公开语义。）

循环体内：

```python
            matched = [term for term in query_terms if term in item.normalized_text]
            if not matched and not (mode == FUSION_HYBRID and item.record.chunk_id in dense_id_set):
                continue
```

其中 `dense_id_set = set(dense_ids)` 在循环前算好。`role_hit` 与门槛判定不变（门槛只在 lexical 生效）。

Phase B / C 之间插入融合分：

```python
        lexical_ids = [item.record.chunk_id for _, item in sorted(
            lexical, key=lambda pair: (-pair[0], pair[1].record.doc_id, pair[1].record.chunk_id))]
        fused = dict(fuse_rrf([ranking for ranking in (lexical_ids, dense_ids) if ranking])) \
            if mode == FUSION_HYBRID else {}
        extra_ids = tuple(chunk_id for chunk_id in dense_ids if chunk_id not in set(lexical_ids))[:DENSE_POOL_LIMIT]
        pool = self._candidate_pool(lexical, mode=mode, top_k=request.top_k,
                                    contract_docs=contract_docs, extra_ids=extra_ids)
```

Phase C 的基础分三选一：

```python
        for position, (base, item) in enumerate(pool, start=1):
            chunk_id = item.record.chunk_id
            if mode == FUSION_LEXICAL:
                foundation = base
            elif mode == FUSION_HYBRID:
                # 融合分缺省（例如该片段只被门槛外的通道命中）用名次分兜底，保证 > 0。
                foundation = fused.get(chunk_id) or rrf_weight(position)
            else:
                foundation = rrf_weight(position)
            score = foundation + dense_scores.get(chunk_id, 0.0) * scale.dense_multiplier
```

> `dense_multiplier` 在 RRF 量级下是 0，所以语义信息**只通过 `fused` 影响排序**，不会重复计一次分。这正是 1.4-b 那个「`dense * 10.0` 死通道」的正确修法：不是把 10.0 调大，而是让它以排名的身份参与。

- [ ] **Step 6: 跑测试确认 GREEN**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests/test_hybrid_retrieval.py services/agent_api/tests/test_dense_retrieval.py services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_retrieval_scoring.py -q
```

Expected: `17 + 9 + 6 + 16 = 48 passed`（hybrid 6+6+5、dense 3+5+1、index 6、scoring 15+1）

- [ ] **Step 7: lexical / rank 不回归**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t6/report-lexical.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t5/report-lexical.json tmp/hybrid-t6/report-lexical.json
$env:RAG_FUSION_MODE = "rank"
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t6/report-rank.json
Remove-Item Env:RAG_FUSION_MODE
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t4/report-rank.json tmp/hybrid-t6/report-rank.json
```

Expected: 两次对比都 **0 处差异**。`rank` 模式不接语义通道（`dense_ids` 为空），所以它必须与 Task 4 完全一致；不一致说明 Step 5 的 `mode == FUSION_HYBRID` 分支漏了条件。

- [ ] **Step 8: 改写集验收（本计划的成败判据）**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -X utf8 scripts/eval_retrieval_recall.py --mode hybrid -v --out tmp/hybrid-t6/recall-hybrid.json
```

验收线（计划 §二）：

| variant | 目标 | 天花板 |
|---|---:|---:|
| orig | 15/15（不得回归） | — |
| t1 | ≥ 9/15 | 12/15 |
| t2 | ≥ 6/15 | 8/15 |
| 合计 | ≥ 30/45 = 66.7% | 34/45 = 75.6% |

未达标时的排查顺序（**按这个顺序，不要乱调**）：

1. 先看 `empty` 列。还有空结果 → `DENSE_FLOOR` 太严，回 Task 5 Step 5 重扫 ratio。
2. 非空但 gold 不在 top5 → 看 `-v` 输出里 gold 文档排第几。排在 6-10 名是 `RANK_POOL_MULTIPLIER` / `DENSE_POOL_LIMIT` 的问题；排在 20 名开外是 `hint_bonus` 与名次分的相对量级问题（只调 `scoring.py`）。
3. orig 掉了 → 一定是 Phase D 的强制选片段被候选池挤掉了，检查 `contract_docs` 是否包含了 `required_sources` 与 `hinted_docs` 的并集。
4. 以上都排除后仍不达标 → 说明剩下的失分在路由/规划层（计划 1.6），**不要试图用检索层的硬编码去补**，把结论写进 §六 并停在这里。

- [ ] **Step 9: offline 全量验收**

Run:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_FUSION_MODE = "hybrid"
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode offline --quiet --out tmp/hybrid-t6/report-hybrid.json
Remove-Item Env:RAG_FUSION_MODE
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t6/report-lexical.json tmp/hybrid-t6/report-hybrid.json
```

Expected: **≥ 91/119**。低于 91 就先切回 `RAG_FUSION_MODE=lexical` 恢复，再按 Step 8 的顺序排查掉分的具体题目（对比脚本会列出 case_id）。

- [ ] **Step 10: 提交**

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add services/agent_api/app/knowledge/index.py services/agent_api/app/knowledge/scoring.py services/agent_api/tests/test_hybrid_retrieval.py docs/superpowers/plans/2026-10-14-hybrid-retrieval.md
git commit -m "feat: fuse lexical and dense rankings with rrf in hybrid retrieval mode"
```

---

## Task 7：收尾——live 评测、默认值决策、模型交付

**目的**：把前六个 Task 的结果落到可交付状态：跑一次 live、决定默认融合模式、解决「模型缓存在 gitignored 的 `tmp/` 里」这个交付硬伤、回填实测记录。

- [ ] **Step 1: 全量单测**

Run: `chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; .venv\Scripts\python.exe -X utf8 -m pytest services/agent_api/tests -q --no-header`

Expected: collected ≈ 512（基线 454 + 改写集 6 + 向量存储 10 + 打分 16 + 融合 17 + 语义 9），failed ≤ 15（基线 15 failed / 439 passed）。**新增的失败一律要修**，基线里那 15 条不属于本计划范围，但要在 §六 里写一句「与基线同集合」以证明没换一批。

- [ ] **Step 2: live 评测一次（约 15min）**

`.env` 已是 `AGENT_MODEL_MODE=live`。先确认 `AGENT_RAG_TIMEOUT_MS=5000` 在 hybrid 下够用（Task 5 Step 7 的首查 ≤0.6s 是它的前提）：

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$env:RAG_FUSION_MODE = "hybrid"
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode live --quiet --out tmp/hybrid-t7/report-live-hybrid.json
Remove-Item Env:RAG_FUSION_MODE
.venv\Scripts\python.exe -X utf8 scripts/run_agent_eval.py --all --mode live --quiet --out tmp/hybrid-t7/report-live-lexical.json
.venv\Scripts\python.exe -X utf8 tmp/cmp_offline_runs.py tmp/hybrid-t7/report-live-lexical.json tmp/hybrid-t7/report-live-hybrid.json
```

Expected: hybrid ≥ lexical。live 有模型随机性，**单次结果差 1-2 题不作结论**，但掉 5 题以上必须排查（多半是证据变多后答案生成被干扰，属于 prompt 层问题，不在本计划范围，记进 §六）。两次 live 约 30min，是本计划唯一的长任务，放在最后跑。

- [ ] **Step 3: 决定默认融合模式**

判据（全部满足才切默认）：改写集合计 ≥ 30/45、offline ≥ 91/119、live 不低于 lexical、首查 ≤ 1s。

- **达标**：在 `.env.example` 写 `RAG_FUSION_MODE=hybrid`，并在旁边注释三件事——① 取值范围 `lexical|rank|hybrid`；② 切模式必须重启进程（索引在加载时决定是否构造编码器，见 Task 5 Step 4 的注）；③ `hybrid` 需要 `data/knowledge/embeddings/` 下有匹配当前快照与 `max_chars` 的向量文件，否则自动退化为词法并记 INFO 日志。`.env` 保持 `lexical` 还是切 `hybrid` 由用户决定，**不要在计划执行中擅自改 `.env`**（它是本地未跟踪文件，改了没人能回退）。
- **未达标**：默认留 `lexical`，把 `rank` / `hybrid` 作为可开关的实验路径交付，§六 写清差在哪一层。

- [ ] **Step 4: 解决模型缓存的交付问题（硬伤，必须处理）**

现状：`RAG_EMBEDDING_CACHE` 指向 `tmp/embedding-models`（90.8 MB，`tmp/` 被 gitignore）。向量文件本身已经进仓库，但**运行时仍需要模型来编码查询**——比赛环境离线，拿不到缓存就是 hybrid 直接退化成词法。

三条路，按优先级：

1. **把模型缓存移进仓库**：`git mv` 不适用（当前未跟踪），做法是复制到 `third_party/embedding-models/`，`git add -f third_party/embedding-models`，`.env.example` 里默认 `RAG_EMBEDDING_CACHE=third_party/embedding-models`。先确认远端能接受 ~91 MB 的单次提交（`git lfs version` 有 LFS 就用 LFS 跟踪 `*.onnx`）。**这是最稳的交付形态。**
2. **随交付包附带**：模型不进 git，打包时把 `tmp/embedding-models/` 一起拷进交付目录，部署文档写明 `RAG_EMBEDDING_CACHE` 指向它，并提供 `scripts/check_dense_readiness.py`（加载索引 + 编码一条查询 + 打印 store/encoder 状态）让评委一键自检。
3. **兜底降级**：以上都不可行时，交付默认 `RAG_FUSION_MODE=rank`——它不需要任何模型，只靠「去门槛 + 排名截断 + RRF 量级」，Task 4 的数字就是它的效果。这条要写进部署文档，作为离线环境下的推荐配置。

无论选哪条，都要在部署文档里写一句：**换 `max_chars` 或改任何 `data/knowledge/*.pdf|docx` 之后，必须重跑 `scripts/build_knowledge_embeddings.py`**，否则快照哈希不匹配、向量被拒绝加载、语义通道静默退化为词法（有 WARNING 日志可查）。

- [ ] **Step 5: 回填实测记录并提交**

把 §六 的表格按实际数字填满（**不允许留空或写「待补」**，跑不出来的项要写明原因），然后：

```powershell
chcp 65001 > $null; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
git add -A
git commit -m "docs: record hybrid retrieval measurements and delivery notes"
```

- [ ] **Step 6: 收尾自检**

- `RAG_FUSION_MODE` 未设置时，全链路行为与 `checkpoint/pre-hybrid-retrieval-20261014`（`335e6d2`）一致（用 Step 1 的 offline 对比证明）。
- `rg "1_000_000|2_000_000" services/agent_api/app/knowledge` 只应命中 `scoring.py` 的 LEGACY 定义。
- `rg "except Exception" services/agent_api/app/knowledge` 剩下的每一处都有日志，没有静默吞异常。
- `git status` 干净，`tmp/` 里的评测产物没有误提交。

---

## 六、实测记录（执行时回填）

> 这张表是本计划唯一的成果证明。每个 Task 跑完就填，不要攒到最后凭记忆补。`tmp/` 是 gitignore 的，**数字只有抄进这里才算留下来**。

### 6.1 改写集召回（chunk_any@5，45 问）

| 阶段 | mode | orig | t1 | t2 | 合计 | empty 合计 | 备注 |
|---|---|---:|---:|---:|---:|---:|---|
| Task 0 基线 | lexical | /15 | /15 | /15 | /45 | | 门槛在，t1/t2 大面积返空 |
| Task 3 | rank | /15 | /15 | /15 | /45 | | 去门槛，LEGACY 量级 |
| Task 4 | rank | /15 | /15 | /15 | /45 | | RRF 量级 |
| Task 6 | hybrid | /15 | /15 | /15 | /45 | | **验收线 ≥30/45** |
| 天花板（纯排序） | — | 14 | 12 | 8 | 34/45 | | 计划 1.2 |

### 6.2 offline 全量（119 题）

| 阶段 | lexical | rank | hybrid | 与上一阶段差异题数 |
|---|---:|---:|---:|---:|
| 基线（`335e6d2`） | 91/119 | — | — | — |
| Task 2 | /119 | — | — | 必须 0 |
| Task 3 | /119 | /119 | — | lexical 必须 0 |
| Task 4 | /119 | /119 | — | lexical 必须 0 |
| Task 5 | /119 | — | — | 必须 0 |
| Task 6 | /119 | /119 | /119 | lexical/rank 必须 0；**hybrid ≥91** |

### 6.3 语义通道标定（Task 5 Step 5）

| 项 | max_chars=800 | max_chars=1200 |
|---|---:|---:|
| 域内 top1 余弦 p50 | | |
| 域内 gold 余弦 p50 | | |
| 域外 top1 余弦 max | | |
| 选中的 ratio | | |
| 选中的绝对门槛 | | |
| gold 进 top5 | /45 | /45 |

**决策理由**（绝对门槛为主还是相对门槛为主，为什么）：

### 6.4 延迟与体积

| 项 | 改造前 | 改造后 |
|---|---:|---:|
| 语义通道首次查询 | ~62s（7.1 索引 + 54.6 编码） | |
| 语义通道次查询 | ~0.03s | |
| 向量文件体积 | — | |
| 模型缓存体积 / 交付位置 | 90.8 MB @ `tmp/`（gitignored） | |

### 6.5 live 评测（Task 7 Step 2）

| mode | 分数 | 与 lexical 的差异题 |
|---|---:|---|
| lexical | /119 | — |
| hybrid | /119 | |

### 6.6 单元测试

| 阶段 | collected | failed | passed | 与基线（15 failed / 439 passed）的关系 |
|---|---:|---:|---:|---|
| 基线 | 454 | 15 | 439 | — |
| Task 7 | | | | |

### 6.7 结论与未解决项

- 改写集是否达标（≥30/45）：
- offline 是否守住（≥91/119）：
- 默认融合模式决策：
- 剩余失分归因（检索层 / 规划层 / 路由层，附 case_id）：
- 遗留给方案 C 的具体项：

---

## 七、本计划做完之后仍然存在的泛化缺口

写下来是为了避免「hybrid 上了就以为泛化解决了」这个误判。三层硬编码里本计划只动了最内一层（计划 1.6）：

| 层 | 现状 | 本计划后 | 下一步该做什么 |
|---|---|---|---|
| 检索 `index.py` | 0.35 绝对门槛 + ~40 条 hints/topics + 百万级加分 | 排名截断 + RRF 量级 + 语义融合；hints/topics **仍在**（只是量级降下来了） | 逐条删 hints/topics，用 Task 0 的改写集当安全网，每删一批跑一次 |
| 规划 `source_plan.py` | ~60 条 `ROLE_TERMS` / `ROLE_QUERY_TERMS` 关键词表 | 未动 | 改成「用检索排名反推 required_docs」，而不是用词表正推 |
| 路由 `semantic_router.py` / `task_policy.py` | 关键词判意图与澄清 | 未动，**端到端改写正确率仍被它卡在 5/15** | 这是端到端 60% 的真正瓶颈，优先级高于继续调检索 |

**顺序建议**：先做路由泛化（收益最大，端到端上限直接从 5/15 抬到 15/15），再做 `source_plan` 反推，最后才回来删检索层的 hints/topics。三层每改一层都用同一份改写集量一次，才能知道钱花在哪了。
