# Chinook 初赛运行面收敛设计

## 目标

将初赛正式运行面收敛为 Chinook 单数据集：Live 为主模式，模型失败时使用保留查询义务的受控离线兜底；AdventureWorks 的接入代码、配置、测试和文档完整保留，但默认不初始化、不进入问答路由，并在前端显示为不可用。

## 运行边界

正式服务只初始化 `chinook-music`。Chinook 固定为 `hybrid`，能力包括 SQL、RAG 和本地计算，编排策略固定为 `c_v1`。AdventureWorks 仍存在于完整 profile catalog，但状态为 `unavailable`，请求它时返回 `PROFILE_UNAVAILABLE`。

运行模式统一为 `live` 和 `demo`：

- `live`：模型优先；生成失败、超时或不可用时，只有在已登记查询或受控查询完整覆盖时间、指标、过滤条件、粒度和输出要求时才降级执行。
- `demo`：启动即使用本地登记查询、RAG 和计算，不调用外部模型，用于开发与离线评测。
- `unavailable`：服务状态，不作为用户可选模式；配置错误或启动失败时使用。

`AGENT_RUN_MODE` 是新的主配置。为保持已有测试和外部调用兼容，旧的 `AGENT_MODEL_MODE` 和 Python `model_mode` 参数暂时保留为兼容别名，但正式启动只从统一运行模式向下传递一次。

## 结构调整

主路径保持以下单向流程：

```text
request -> Chinook parser/router -> SQL/RAG/calculation tools -> evidence check -> response composer
```

`fast_sql` 和 `fast_rag` 仅作为内部策略，不再表达独立运行模式。`execution_mode` 只保留在测试和 fixture 装配中。`legacy` 只为历史测试保留，不作为默认 profile 编排策略。

## 前端体验

数据集选择器继续展示两个 profile。Chinook 显示“可用”，AdventureWorks 显示“暂不可用”并禁用选择。前端不增加 Live/Demo 切换；页面显示当前 Agent 运行状态，模式由服务启动配置决定。

## 删除原则

本轮不删除 AdventureWorks 接入资产、评测集、历史文档、第三方冻结副本和正式契约。只移除或合并确认不再被主链路、测试或脚本引用的运行包装层；每个删除前先做引用扫描并单独提交。

## 验收

1. 默认配置只初始化 Chinook，启动期间不读取 AdventureWorks 数据库连接。
2. `/api/v1/profiles` 返回 Chinook ready、AdventureWorks unavailable。
3. AdventureWorks 请求返回结构化 `PROFILE_UNAVAILABLE`。
4. Live 失败降级只在查询义务完整覆盖时执行，否则返回证据不足或不可用。
5. Demo 模式不调用模型，Chinook SQL/RAG/计算回归继续通过。
6. 前端显示 AdventureWorks 不可用，不允许选择。
7. AdventureWorks 独立接入测试、文件和文档仍保留。
