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
profiles/              问数 profile 配置与示例
eval/                  JSONL 评测题和评测脚本
scripts/               索引构建、评测与校验脚本
docs/                  接口契约、数据方案、赛题材料和设计文档
```

## 开发基线

统一接口契约见 [interface-contract.md](interface-contract.md)。

C 模块的实现与测试见 `backend/` 与 `third_party/chinook_c/`，接口约定见 [interface-contract.md](interface-contract.md)。

数据集与评测集方案见 [数据集与评测集方案.md](数据集与评测集方案.md)。

第一轮先跑通三个问题：

1. “2025 年 Rock 音乐销售额是多少？”
2. “Rock 达到第三季度经营目标了吗？”
3. “销售增长率是多少？”——系统需要主动澄清比较周期。

## C 模块阶段交付

阶段标签：`c-baseline-v0.1.0`。Chinook Schema 与只读 SQL 已完成本地验收；基础 NL2SQL 已接入真实模型。早期 10 题抽样为执行成功率 60%、数值匹配率 20%、严格业务正确率 10%。B 整体联调与 AdventureWorks 实库验证待完成。

最新一轮全量评测见 [评测结果与优劣势分析-2026-10-09.md](docs/评测结果与优劣势分析-2026-10-09.md)：基础 NL2SQL 已 10/10，多表关联、公式计算、跨源问答和多轮会话仍为 0 分。交接与对外结论以该文档为准。

## 数据说明

Chinook 是公开样例数据库，不是真实企业经营数据。销售额按 `InvoiceLine.UnitPrice * InvoiceLine.Quantity` 计算。当前数据不包含成本、利润、退款、库存、广告投放和播放日志，系统遇到这些问题应说明数据不支持。

自建目标、运营规则、活动方案和复盘材料属于模拟业务设定，文档中会明确标注。

## 本地启动

完整的依赖清单、首次准备、验证命令和故障排查见 [环境说明与启动指南](docs/环境说明与启动指南.md)（2026-10-09 逐项实测核对）。

最短路径，两个终端：

```powershell
# 终端 1，仓库根目录
uv run uvicorn services.agent_api.app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端 2，services/web 目录
npm run dev
```

浏览器打开 <http://localhost:5173>。启动前确认 `.env` 里 `AGENT_MODEL_MODE=live`，否则服务能起、网页能开，但走的是固定问答替身而不是真实模型。

`data/knowledge/D01-D12` 与 `third_party/chinook_c/` 受哈希校验保护，直接编辑会导致后端启动失败，改动方式见环境指南第 5、6 节。

## 协作约定

当前仓库处于模块实现阶段。各模块先按接口契约独立开发，再通过 Pull Request 合并到 `main`。API Key 等敏感配置只放在本地 `.env`，不要提交到仓库；环境变量名称见 `.env.example`。

## 队友环境搭建（新机器从零到跑通）

前置：Python 3.11+（`pyproject.toml` 声明 `requires-python = ">=3.11"`，本机 `.venv` 为 3.12.10）与 [uv](https://docs.astral.sh/uv/)；Node 22 / 24 / 26 任一。

1. 克隆仓库后安装依赖：后端在仓库根目录执行 `uv sync`；前端执行 `cd services/web` 后 `npm ci`。
2. 复制 `.env.example` 为 `.env` 并填写 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。没有密钥时先保持 `AGENT_MODEL_MODE=offline`、`AGENT_BACKEND_MODE=offline_sql_d12` 跑离线链路。`.env` 已在 `.gitignore`，只留本地。
3. 按上面"本地启动"一节起两个终端（后端 8000、前端 5173），浏览器打开 http://localhost:5173 即可提问。
4. 自检：访问 http://127.0.0.1:8000/health 应返回 `"model_mode":"live"`；再问一句"2025 年 Rock 音乐销售额是多少？"，应返回 `status=answered`。

数据与索引都在库内，不需要额外下载或构建：`data/chinook/Chinook.db`、`data/knowledge/` 的 D01-D12 文档及其 `source-manifest.json` 快照清单。索引本身在启动时由 `services/agent_api/app/knowledge/` 的代码从这些原始文件构建，不是库内的产物。

不在库内的内容及其来源：`.env` 每人自行填写密钥；`tmp/` 是评测与实验的本地产物，用 `scripts/run_agent_eval.py` 等脚本重新生成；`.venv`、`node_modules`、`dist` 由上面的安装命令生成。

测试命令：后端 `uv sync --extra test` 后 `.venv\Scripts\python.exe -m unittest discover -s services\agent_api\tests -t .`（2026-10-09 实测 106 tests OK）；前端 `services/web` 下 `npm run verify`。

不要用 `uv run pytest`：`pytest` 未声明在依赖里也不在 `.venv` 内，该命令会落到 venv 外的全局 pytest，收集阶段即报 `No module named 'sqlalchemy'`。详见环境指南第 8 节。
