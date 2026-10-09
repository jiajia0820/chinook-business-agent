# Chinook Business Agent

基于 Chinook 数字媒体商店数据库与自建经营知识库的可解释经营分析问答智能体。

## 项目目标

面向运营人员和经营负责人，支持用自然语言完成：

- 销售额、销量、订单和客户等结构化数据查询；
- 经营规则、目标、复盘和 FAQ 文档检索；
- 数据库与业务文档联合分析；
- 条件缺失时主动澄清；
- 返回答案、SQL、文档来源、计算口径和执行记录。

项目主线是：

```text
网页提问 -> Agent 判断 -> SQL / RAG 工具 -> 结果合并 -> 答案与证据
```

## 当前协作分工

- A（队长）：业务文档、字段口径、实体别名、评测集和接口契约维护。
- B：网页、API、Agent 路由、RAG 和结果展示。
- C：Chinook 接入、Schema、只读 SQL、Schema Linking 和 NL2SQL。

## 目录结构

```text
services/agent_api/    B 后端：FastAPI＋LangGraph 问数链路
services/web/          B 前端：Vue 3＋Vite 问答工作台
backend/               C 答卷代码：只读 SQL 与 NL2SQL 模块
third_party/chinook_c/  vendored C 模块（含一份 Chinook.db）
data/chinook/          Chinook.db、SQL 建库脚本、README 和许可证
data/knowledge/        自建经营知识文档（D01-D12）
config/                字段字典、指标口径、实体别名和文档目录
profiles/              问数 profile 配置与示例
eval/                  JSONL 评测题和评测脚本
scripts/               索引构建、评测与校验脚本
docs/                  接口契约、数据方案、赛题材料和设计文档
```

## 开发基线

统一接口契约见 [interface-contract.md](interface-contract.md)。

C 模块已完成内容、当前限制、下一阶段任务与验收标准见 [C 模块阶段进展](docs/c-stage-progress.md)（2026-10-04 更新）。

数据集与评测集方案见 [数据集与评测集方案.md](数据集与评测集方案.md)。

第一轮先跑通三个问题：

1. “2025 年 Rock 音乐销售额是多少？”
2. “Rock 达到第三季度经营目标了吗？”
3. “销售增长率是多少？”——系统需要主动澄清比较周期。

## C 模块阶段交付

阶段标签：`c-baseline-v0.1.0`。A、B 请先阅读 [阶段交付与协作说明](docs/C模块阶段交付与AB协作说明-2026-10-04.md)。Chinook Schema 与只读 SQL 已完成本地验收；基础 NL2SQL 已接入真实模型。本轮 10 题执行成功率 60%、数值匹配率 20%、严格业务正确率 10%，尚未达到稳定业务问数要求。B 整体联调与 AdventureWorks 实库验证待完成。

## 数据说明

Chinook 是公开样例数据库，不是真实企业经营数据。销售额按 `InvoiceLine.UnitPrice * InvoiceLine.Quantity` 计算。当前数据不包含成本、利润、退款、库存、广告投放和播放日志，系统遇到这些问题应说明数据不支持。

自建目标、运营规则、活动方案和复盘材料属于模拟业务设定，文档中会明确标注。

## 本地启动约定

当前仓库处于模块实现阶段。各模块先按接口契约独立开发，再通过 Pull Request 合并到 `main`。API Key 等敏感配置只放在本地 `.env`，不要提交到仓库；环境变量名称见 `.env.example`。

## 队友环境搭建（新机器从零到跑通）

前置：Python 3.10+ 与 [uv](https://docs.astral.sh/uv/)；Node 22 / 24 / 26 任一。

1. 克隆仓库后安装依赖：后端在仓库根目录执行 `uv sync`；前端执行 `cd services/web` 后 `npm ci`。
2. 复制 `.env.example` 为 `.env` 并填写 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。没有密钥时先保持 `AGENT_MODEL_MODE=offline`、`AGENT_BACKEND_MODE=offline_sql_d12` 跑离线链路。`.env` 已在 `.gitignore`，只留本地。
3. 启动后端（端口 8000）：仓库根目录执行 `uv run uvicorn services.agent_api.app.main:app --host 127.0.0.1 --port 8000`。
4. 启动前端（端口 5173）：`services/web` 下执行 `npm run dev`，浏览器打开 http://127.0.0.1:5173 即可提问。

数据与索引都在库内，不需要额外下载或构建：`data/chinook/Chinook.db`、`data/knowledge/` 的 D01-D12 文档、`services/agent_api/app/knowledge/` 的知识索引快照。

不在库内的内容及其来源：`.env` 每人自行填写密钥；`tmp/` 是评测与实验的本地产物，用 `scripts/run_agent_eval.py` 等脚本重新生成；`.venv`、`node_modules`、`dist` 由上面的安装命令生成。

自检命令：后端 `uv sync --extra test` 后 `uv run pytest services/agent_api/tests/test_api.py -q`；前端 `services/web` 下 `npm run verify`。
