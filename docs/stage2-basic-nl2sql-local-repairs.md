# 基础 NL2SQL 15题局部修复与本地验证

日期：2026-10-11（北京时间）。版本：S2-009；基线为S2-008本地提交`ff68919`；基础题库15题与评分`stage2-policy-1.5`均保持原样。

## 结果与验证边界

按用户“先不进行在线测试，优化这项评测集发现的问题直到100%”的要求，本轮禁止外部请求，使用真实Chinook只读执行器、正式问答编排和答案合成。仅模型适配器注入明确不可用的本地对象，检验真实受控计划；没有注入gold SQL或答案。

| 范围 | 修复前本地诊断 | 最终本地诊断 |
|---|---|---|
| 原001–010 | 10/10 | **10/10** |
| 新011–015 | 2/5 | **5/5** |
| 基础专项合计 | 12/15（80%） | **15/15（100%）** |

全部15题均有完整响应；实际模型调用0，网络尝试0。开始/结束业务代码、评分、题库与数据指纹均一致，评分、gold和题库文件与S2-008字节一致。15条标准SQL再次独立只读执行并与gold一致。数据库SHA256保持`adb73b5b0ea57598926fa8a72ffb2848c64f7f17a4e57b5e0c04a182e24f565c`。这项100%只表示当前15题的本地系统正确率，真实模型接入表现仍按用户要求暂缓，不能改写此前live失败记录。

额外85单位/95请求本地回归为**83/85**：多表8/8、公式6/6、跨源10/10，其他旧通过题无退化。仍失败的是既有`multidoc-002`与`session-002`，属于其他专项，本次没有扩题、改标准或顺带修复这两题。基础15/15不等于整个系统85/85，也不代表任意未知问法100%。

## 三项问题如何修复

### 011：精确编号与商品属性

- 将编号语法抽为B/C共享模块`backend/sql_module/entity_ids.py`，B保留原导出接口；支持`TrackId 为/等于/是/=/：`、商品编号、列表与有界范围。在日期解析前屏蔽编号，实际2025年份与四位商品编号可同时出现；无效、倒置或超过200个商品的范围进入澄清。
- 新登记`track_attributes`目录属性口径，B/C配置同步，总登记指标由14变为15。标价读取`Track.UnitPrice`，不使用成交明细，也不要求销售期间。商品→音乐分类→媒体类型通过目录关联查询，编号通过参数绑定，支持多个编号。
- 商品销售问题另保留`track_ids`筛选，防止虽然不误识别年份却遗漏商品限定。
- B配置版本更新为`b-stage2-s2-009-basic-nl2sql-2026-10-11`，C指标目录版本更新为`chinook-business-2`；避免新语义沿用旧配置/来源版本。
- 合成器明确检查名称、分类、媒体类型和标价五个结果字段，并显示中文字段和USD单位。

### 014：客户消费额、HAVING与排序

- profile登记消费总额/消费金额/消费额为成交销售额的业务词汇；支持“客户有哪些/客户编号”的客户粒度识别，转入SQL而不是RAG。
- 将一个明确美元阈值转换为结构化`amount_filter`，支持>=、>、<=、<、=等对应表达；不确定数值、其他币种或多个未经支持的阈值条件进入澄清，不删除条件。
- 成交金额仍为`InvoiceLine.UnitPrice * Quantity`，音频范围不变；先按客户聚合，再在HAVING中筛选，金额降序及客户编号升序是独立排序要求，不依赖TOP-N。
- 金额按既有两位小数口径比较，避免浮点累加使恰好15.84美元的客户被排除；题库10美元边界与独立标准SQL结果一致，gold未改。
- 修复错误模型候选时只保留用户明确要求的TOP-N，不继承候选擅自加入的LIMIT，避免完整客户名单被截断。

### 015：维度全集与零成交

- `include_empty`显式表达零成交保留，普通媒体分组原行为保留。
- 以`MediaType`全集为外层，LEFT JOIN成交汇总；日期条件放在事实子查询内部，真正约束参与SUM的明细，不能仅放在最后Invoice外连接的ON里。
- 无成交的金额与销量通过COALESCE转成数值0；按MediaTypeId升序。全部媒体不施加默认音频过滤；也支持已解析的品类/账单国家事实限定。

### 候选语义与只读安全

新目录、客户阈值和零成交形式采用保守的SQLGlot AST比对：将实际绑定值纳入比对，验证完整登记计划形状，防止错误编号、错误阈值、排序、丢失零值、日期泄漏或不完整外连接通过。暂不证明任意等价SQL形式；其他形式经明确标注的受控修复执行。原普通销售SQL范围检查与只读执行器仍保留，没有普遍放开所有外连接。

新增方案为本项目实现，未复制外部开源代码或新增依赖；仅复用已有SQLGlot AST能力和C只读执行器。第三方冻结副本、数据库、正式题库与评分器均未修改。

## 最终三题的真实本地响应

### basic-nl2sql-011

状态：answered；路由：sql；本地评分通过。

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：；当前确认范围。
> 查询结果：商品 ID 1791；名称 Down Under；品类 Rock；媒体类型 MPEG audio file；标价 0.99 USD
> 来源：SQL sql-618ad437-ac84-47e7-963c-b9ebd2b7796d。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

接受SQL（C执行前仍会校验与规范化）：

```sql
SELECT t.TrackId AS TrackId,t.Name AS Name,g.Name AS genre,mt.Name AS media_type,t.UnitPrice AS unit_price FROM main.Track t JOIN main.Genre g ON g.GenreId=t.GenreId JOIN main.MediaType mt ON mt.MediaTypeId=t.MediaTypeId WHERE t.TrackId IN (:track_0) ORDER BY t.TrackId
```

完整结构化结果：

```json
[
  {
    "TrackId": 1791,
    "Name": "Down Under",
    "genre": "Rock",
    "media_type": "MPEG audio file",
    "unit_price": 0.99
  }
]
```

### basic-nl2sql-014

状态：answered；路由：sql；本地评分通过。

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 客户编号 10；客户 Eduardo Martins；销售额 15.84 USD；订单数 2 单
> 2. 客户编号 48；客户 Johannes Van der Berg；销售额 15.84 USD；订单数 2 单
> 3. 客户编号 31；客户 Martha Silk；销售额 13.86 USD；订单数 1 单
> 来源：SQL sql-73d8e718-8a65-428f-b091-a4d1e18f9865。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

接受SQL（C执行前仍会校验与规范化）：

```sql
SELECT c.CustomerId AS CustomerId, c.FirstName || ' ' || c.LastName AS customer, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COUNT(DISTINCT i.InvoiceId) AS order_count FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId JOIN main.Customer c ON c.CustomerId = i.CustomerId WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) GROUP BY c.CustomerId, c.FirstName, c.LastName HAVING ROUND(SUM(il.UnitPrice * il.Quantity),2) >= :amount_threshold ORDER BY sales_amount DESC, c.CustomerId
```

完整结构化结果：

```json
[
  {
    "CustomerId": 10,
    "customer": "Eduardo Martins",
    "sales_amount": 15.84,
    "order_count": 2
  },
  {
    "CustomerId": 48,
    "customer": "Johannes Van der Berg",
    "sales_amount": 15.84,
    "order_count": 2
  },
  {
    "CustomerId": 31,
    "customer": "Martha Silk",
    "sales_amount": 13.86,
    "order_count": 1
  }
]
```

### basic-nl2sql-015

状态：answered；路由：sql；本地评分通过。

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 媒体类型 ID 1；名称 MPEG audio file；销售额 111.87 USD；销量 113 件
> 2. 媒体类型 ID 2；名称 Protected AAC audio file；销售额 0.99 USD；销量 1 件
> 3. 媒体类型 ID 3；名称 Protected MPEG-4 video file；销售额 0 USD；销量 0 件
> 4. 媒体类型 ID 4；名称 Purchased AAC audio file；销售额 0 USD；销量 0 件
> 5. 媒体类型 ID 5；名称 AAC audio file；销售额 0 USD；销量 0 件
> 来源：SQL sql-114dba38-6641-48f7-b7d4-39bea5141b52。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

接受SQL（C执行前仍会校验与规范化）：

```sql
SELECT mt.MediaTypeId AS MediaTypeId,mt.Name AS Name,COALESCE(f.sales_amount,0) AS sales_amount,COALESCE(f.units_sold,0) AS units_sold FROM main.MediaType mt LEFT JOIN (SELECT t.MediaTypeId,ROUND(SUM(il.UnitPrice * il.Quantity),2) AS sales_amount,SUM(il.Quantity) AS units_sold FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date GROUP BY t.MediaTypeId) f ON f.MediaTypeId=mt.MediaTypeId ORDER BY mt.MediaTypeId
```

完整结构化结果：

```json
[
  {
    "MediaTypeId": 1,
    "Name": "MPEG audio file",
    "sales_amount": 111.87,
    "units_sold": 113
  },
  {
    "MediaTypeId": 2,
    "Name": "Protected AAC audio file",
    "sales_amount": 0.99,
    "units_sold": 1
  },
  {
    "MediaTypeId": 3,
    "Name": "Protected MPEG-4 video file",
    "sales_amount": 0,
    "units_sold": 0
  },
  {
    "MediaTypeId": 4,
    "Name": "Purchased AAC audio file",
    "sales_amount": 0,
    "units_sold": 0
  },
  {
    "MediaTypeId": 5,
    "Name": "AAC audio file",
    "sales_amount": 0,
    "units_sold": 0
  }
]
```

## 工程检查和证据

最终后端工程测试**168/168通过**；C及旧SQL契约**52通过、3跳过（55项）**。跳过为两项本轮未启用的AdventureWorks PostgreSQL集成与一项按用户要求禁用的真实模型测试。完整日志：[后端工程检查](../tmp/stage2-s2-009-backend-final.log)、[C与SQL检查](../tmp/stage2-s2-009-c-final.log)。

新增回归覆盖变更商品编号/范围/实际年份、目录标价与成交额边界、金额等于阈值/严格大于/小于/并列排序、错误候选HAVING与LIMIT、全部媒体/Q2/Q3/全年、错误外连接/日期/空值，以及完整15题真实编排；模型候选用本地stub，不调用API。首次工程检查一条新测试误用了内部状态名`clarification`（实际公共契约为`clarification_required`），修正夹具；完整回归两条旧指标检查仍写14，随新目录指标同步为15并补属性来源/无期间断言，不放宽正确性。

- [最终基础15题报告](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/report.json)、[15条完整响应与SQL/结果/追踪](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/responses.jsonl)。
- [本地模型记录](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/model-calls.jsonl)、[开始元数据](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/run-meta.json)、[结束指纹](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/end-fingerprint-check.json)、[独立核实与分类汇总](../tmp/stage2-eval/20261011T131402Z-local-controlled-e2d58aed/acceptance-summary.json)。
- [85单位全量回归报告](../tmp/stage2-eval/20261011T131418Z-local-controlled-c63a4238/report.json)、[95条完整响应](../tmp/stage2-eval/20261011T131418Z-local-controlled-c63a4238/responses.jsonl)、[其他专项失败原因](../tmp/stage2-eval/20261011T131418Z-local-controlled-c63a4238/failures.md)。
- 修复前独立本地基线：`tmp/stage2-eval/20261011T125551Z-local-controlled-ff99420a/`，12/15。中间14/15及冻结前15/15目录均保留，最终成绩取本节列出的同一轮，没有拼接最好成绩。

完整命令：

```powershell
.\.venv\Scripts\python.exe scripts/stage2_local_check.py eval_basic_nl2sql
.\.venv\Scripts\python.exe scripts/stage2_local_check.py
```

内部工厂仍以live语法路径创建正式编排，但注入不可用模型并阻断HTTP；报告kind明确为local_controlled_diagnostic，不是外部live评测。未使用--limit，未更改.env。

## 修改文件、回溯与限制

生产实现：backend/sql_module/entity_ids.py（新增共享编号）、catalog_queries.py（新增目录/零成交计划）、query_intent.py、semantic_scope.py、service.py；profiles/chinook/metrics.json；services/agent_api/app/agent/entity_ids.py与parsing.py、profiles/configs/chinook-music.json、response/composer.py。

验证：scripts/stage2_local_check.py追加网络阻断、逐次本地模拟记录、终止指纹和正确退出码；新增services/agent_api/tests/test_basic_nl2sql_repairs.py，同步test_chinook_metrics.py。文档同步本报告、当前验收、实现说明入口、原指令局部修复标记与提交前版本备注。

这里只扩充已有Chinook查询能力，不新增通用schema映射；新编号/阈值/期间的变化已验证，但任意语言、任意等价SQL与AdventureWorks迁移未由本轮证明。原离线Demo模式仍保留其固定演示限制；本轮100%来自正式流程的受控计划，不把Demo冒充完整系统。

回溯标签`stage2-S2-009`；先在进度文档备注再本地提交，用户整体验收前不推送、不创建远程PR或合并。原始tmp证据仅本机保留，Git标签恢复实现与文档，不能恢复未跟踪的临时报告。
