# C 模块接口适配

以根目录 interface-contract.md v0.1 为准。正式实现位于 backend/chinook_c.py，Python 3.10 及以上，仅标准库。

2026-10-01 验证：Python 3.10.11 下 8 组契约回归测试通过，覆盖写入拦截、多语句、参数、重复列、超时、空数据、截断与音乐范围；HTTP SQL 工具及三个核心问题测试通过。这是开发回归结果，不代表独立评测准确率。

## B 调用 SQL 工具

从仓库根目录导入：

```python
from backend.chinook_c import Chinook
sql_tool = Chinook()
result = sql_tool.query({
    "query_id": "sql-001",
    "sql": "SELECT COUNT(*) AS customer_count FROM Customer",
    "params": {},
    "purpose": "统计客户数量",
    "max_rows": 50,
    "timeout_ms": 5000,
})
```

输入使用 params，输出 rows 是对象数组，例如 `[{"customer_count":59}]`。公开 query 方法返回契约第四节全部字段，错误采用第九节 code/message/retryable/details。无记录的合法 SQL 仍返回 success，业务层负责解释无数据。

max_rows 默认 50，上限 200；timeout_ms 默认 5000，本实现接受 1 至 30000 毫秒。拒绝布尔值充当整数。重复结果列名返回 INVALID_REQUEST，调用方应使用唯一 AS 别名，避免转为对象时丢列。

执行层采用只读连接、授权器、参数绑定和 SQLite 进度中断。只允许指定只读函数和业务表。执行失败不返回堆栈。超时是 SQLite 指令进度限制，不是进程级内存隔离。

## 演示与测试

```powershell
python -m backend.chinook_c ask "2025年Rock音乐销售额是多少？"
python -m unittest discover -s eval -p "test_sql_contract.py" -v
python -m backend.chinook_c serve
```

HTTP `/query` 接收同一 SQL 工具对象。`/api/v1/ask` 提供 C 模块独立演示适配，响应使用团队统一回答字段；旧 `/ask` 路径是同一适配器的别名，不再返回旧格式。完整对外服务、会话管理、角色权限和 RAG 由 B 实现，C 演示服务仅绑定 127.0.0.1。session_id 仅透传，不表示实现了多轮记忆。options.show_trace、max_rows 生效，top_k 只校验，C 没有 RAG 调用。

音乐查询显式筛选 MediaType 的四种音频类型：MPEG audio file、Protected AAC audio file、Purchased AAC audio file、AAC audio file。没有音乐或品类限定的销售查询沿用全部媒体范围，并标注 data_scope=all。类别问数（例如 Rock）使用音频范围。实际库 2025 年 Rock 音乐销售额为 174.24；修改口径后的标准答案仍需 A 复核。

三个核心问题：Rock 销售额返回 answered/sql；目标问题返回 insufficient_evidence/cross_source，供 B 补齐年份并检索 D06；增长率返回 clarification_required/clarification。当前基线没有实现 RAG、任意 NL2SQL、增长率计算或结构化查询计划编译，不把这些能力标为已完成。

对外 SQL 证据遵循第七节，参数保留在 metric_definitions 中的 params 对象供复现（该数组元素细节尚待 A 统一）；trace 只含实际 SQL 执行记录。最终 Agent 可统一这些未规定的元素结构。SQL 工具本身不替 Agent 判断音乐范围，直接传入 SQL 时由 Agent 确保范围与意图一致。
