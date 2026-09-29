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
data/chinook/       Chinook.db、SQL 建库脚本、README 和许可证
data/knowledge/     自建经营知识文档（D01-D12）
config/              字段字典、指标口径、实体别名和文档目录
backend/             SQL、RAG、Agent 和 API 后端
frontend/            网页端
eval/                JSONL 评测题和评测脚本
scripts/             启动、检查和打包脚本
docs/                接口契约、数据方案、赛题材料和设计文档
```

## 开发基线

统一接口契约见 [interface-contract.md](interface-contract.md)。

数据集与评测集方案见 [数据集与评测集方案.md](数据集与评测集方案.md)。

第一轮先跑通三个问题：

1. “2025 年 Rock 音乐销售额是多少？”
2. “Rock 达到第三季度经营目标了吗？”
3. “销售增长率是多少？”——系统需要主动澄清比较周期。

## 数据说明

Chinook 是公开样例数据库，不是真实企业经营数据。销售额按 `InvoiceLine.UnitPrice * InvoiceLine.Quantity` 计算。当前数据不包含成本、利润、退款、库存、广告投放和播放日志，系统遇到这些问题应说明数据不支持。

自建目标、运营规则、活动方案和复盘材料属于模拟业务设定，文档中会明确标注。

## 本地启动约定

当前仓库处于模块实现阶段。各模块先按接口契约独立开发，再通过 Pull Request 合并到 `main`。API Key 等敏感配置只放在本地 `.env`，不要提交到仓库；环境变量名称见 `.env.example`。
