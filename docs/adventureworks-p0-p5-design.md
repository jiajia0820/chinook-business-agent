# AdventureWorks P0-P5 落地设计

## 目标

在保留 Chinook 默认混合问答能力的前提下，让 Agent 可以通过同一套 `/api/v1/ask` 契约切换到 AdventureWorks PostgreSQL 数据源。当前每次启动选择一个 profile：`chinook-music` 或 `adventureworks`。

## 边界

- AdventureWorks 第一阶段为 `sql_only`，不复用 Chinook 的 D01-D12 文档和计算口径。
- PostgreSQL 使用官方 `chriseaton/adventureworks:postgres` 对应的四个初始化 SQL 文件。
- C 层负责 Schema 反射、SQL 只读校验、参数绑定、超时、行数限制和执行；Agent 只负责 profile 选择、问题解析和结果组装。
- 销售额口径采用 `Sales.SalesOrderDetail.OrderQty * UnitPrice * (1 - UnitPriceDiscount)`，订单日期采用 `Sales.SalesOrderHeader.OrderDate`。

## 配置流

```text
AGENT_ENABLED_PROFILES=adventureworks
        |
        v
ProfileRegistry -> public adventureworks profile
        |
        v
backend Registry -> PostgreSQL AdventureWorks profile
        |
        v
SchemaService -> SQLExecutor -> /api/v1/ask
```

前端通过 `VITE_PROFILE_ID` 选择请求中的 `profile_id`，不把数据集名称写死在组件逻辑中。默认值仍是 `chinook-music`。

## P0-P5 对应实现

| 阶段 | 落地内容 |
|---|---|
| P0 | `scripts/setup_adventureworks.ps1`、SQL 四文件和 PostgreSQL 连接说明 |
| P1 | `psycopg[binary]` 正式依赖与锁文件 |
| P2 | `profiles/adventureworks/profile.json`、`metrics.json`，使用精确大小写的 Schema/表名 |
| P3 | `services/agent_api/app/profiles/configs/adventureworks.json`，声明指标、槽位、数据范围和不支持项 |
| P4 | C/Agent profile registry、生命周期、SQL adapter 和 composer 去除 Chinook 单 profile 硬编码 |
| P5 | 前端 profile 环境变量、AdventureWorks SQL-only 启动分支、真实 PostgreSQL Schema/只读查询回归验证 |

## 运行方式

1. 准备 PostgreSQL 16，并按脚本提示执行 `scripts/setup_adventureworks.ps1`。
2. 设置 `ADVENTUREWORKS_DATABASE_URL`。
3. 设置 `AGENT_ENABLED_PROFILES=adventureworks`；使用 Chinook 时改回 `chinook-music`。
4. 前端需要切换时设置 `VITE_PROFILE_ID=adventureworks`。

AdventureWorks 的 NL2SQL 评测集仍需另行编写；当前仓库的 75 道单轮题和 5 组多轮题属于 Chinook。
