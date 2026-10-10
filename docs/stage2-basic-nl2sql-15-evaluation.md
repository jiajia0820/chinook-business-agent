# 第三次修改：基础 NL2SQL 15题专项结果

日期：2026-10-10；模型：MiMo-V2.6-Flash；运行代码为S2-007对应实现，业务代码未改；评分v1.5。

## 本轮结论

全部15题均有完整响应，未用--limit。首次真实模型调用出现MODEL_HTTP_FAILED，约250ms后失败；未保存具体HTTP状态码，不能据此认定限流或密钥失效。后续25次尝试按累计1次外部失败预算暂缓，实际模型成功0次。未进行第二轮碰运气。

| 范围 | 受控系统业务结果 | 本轮live确认通过 | 外部待复测题数 |
|---|---|---|---|
| 原001–010 | 10/10 | 0/10 | 10 |
| 新011–015 | 2/5 | 0/5 | 3 |
| 合计 | 12/15（80.0%） | 0/15（0.0%） | 13 |

0/15表示没有题满足本轮完整live通过条件；13题因外部调用未完成仍在分母内，不代表模型实际答错了13题。无成功模型响应，无法估计模型问数准确率。12/15只是API故障下受控查询的系统业务诊断结果，不作为live达标证据。

新增012逐月金额/订单与013多条件三指标正确，但来自受控计划，仍待在线复测。011和014有独立功能失败；015同时存在受控零成交保留缺陷与API待复测。

## 标准答案和检查

五题独立只读SQL与参考线索一致。数据库SHA256为`adb73b5b0ea57598926fa8a72ffb2848c64f7f17a4e57b5e0c04a182e24f565c`，前后未变。原001–010原始字节保留；其他专项、根目录历史快照不变。manifest为80单轮、5会话，实际85评分单位、95请求，没有新增未经约定的汇总字段。可读版题目/答案与机器版一致。生成器在隔离目录重新生成的15题逐字段一致，评分正反例21项通过，最后新增生成目录说明亦已同步。

评分v1.5仅对新增五题收紧逐行排序、金额精度、零值、字段/route/期间/截断检查，旧十题及其他专项逻辑保持。历史S2-006旧十题原始响应按新评分仍10/10，属于无模型调用的历史重评分，未混入本次成绩。

## 完整证据

独立目录：`tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/`。

- [最终报告](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/report.json)：全部15题、业务/外部状态；报告的answer/rows为摘录。
- [完整响应](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/responses.jsonl)：15个完整回答、接受SQL、参数、全量结果、trace与限制。
- [真实模型记录](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/model-calls.jsonl)：1次HTTP失败、25次未调用暂缓、13个受控降级响应；本轮没有成功模型候选SQL，不能虚构。
- [标准SQL核实](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/gold-verification.json)、[题库检查](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/dataset-checks.json)、[评分检查日志](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/scoring-tests.log)。
- [分类汇总](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/acceptance-summary.json)、[开始元数据](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/run-meta.json)、[结束指纹](../tmp/stage2-eval/20261010T121832Z-basic15-live-e5398f7a/end-fingerprint-check.json)：代码/评分/题库/数据全部一致。
- 原始证据在本机tmp，未跟踪入Git；本说明及提交前备注由Git保留，长期归档需独立保留原始目录。

## 全部未通过题：完整实际回答、原因和建议位置

### basic-nl2sql-001

题目：2025年第三季度音频销售额、销量、订单数和购买客户数分别是多少？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：销售额 112.86 USD；购买客户数 19 人；销量 114 件；订单数 21 单
> 来源：SQL sql-bc35315c-ba8b-4e3f-9b59-6bcf9041fbb0。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COUNT(DISTINCT i.CustomerId) AS purchasing_customers, COALESCE(SUM(il.Quantity), 0) AS units_sold, COUNT(DISTINCT i.InvoiceId) AS order_count FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5)
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-002

题目：2025年第二季度音频销售额是多少？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q2；当前确认范围。
> 查询结果：销售额 108.9 USD
> 来源：SQL sql-94b95a82-888e-4eff-a2dc-694b83e0f38e。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5)
```

参数：`{"start_date": "2025-04-01", "end_date": "2025-07-01"}`

### basic-nl2sql-003

题目：2025年第三季度 Rock 的音频销售额是多少？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；Rock（Genre:1）。
> 查询结果：销售额 47.52 USD
> 来源：SQL sql-60e11e87-71ad-4d15-b7fd-cecf67ce9d6e。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) AND t.GenreId = :genre_id
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01", "genre_id": 1}`

### basic-nl2sql-004

题目：2025年第三季度 Metal 卖了多少件？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；Metal（Genre:3）。
> 查询结果：销量 22 件
> 来源：SQL sql-95a7e022-4f2a-4bef-85d6-afe1bdd5cdab。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT COALESCE(SUM(il.Quantity), 0) AS units_sold FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) AND t.GenreId = :genre_id
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01", "genre_id": 3}`

### basic-nl2sql-005

题目：2025年第三季度有多少张音频订单？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：订单数 21 单
> 来源：SQL sql-a7f37e16-2d36-4f4f-ae93-8db579fa5820。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT COUNT(DISTINCT i.InvoiceId) AS order_count FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5)
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-006

题目：2025年第三季度有多少位购买音频的客户？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：购买客户数 19 人
> 来源：SQL sql-55661640-5732-472a-b1d6-6938b57ecf93。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT COUNT(DISTINCT i.CustomerId) AS purchasing_customers FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5)
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-007

题目：2025年第三季度音频销售额最高的三个音乐分类是什么？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 品类 Rock；销售额 47.52 USD
> 2. 品类 Metal；销售额 21.78 USD
> 3. 品类 Latin；销售额 15.84 USD
> 来源：SQL sql-ef47c113-8a77-4f30-b92b-2e91a3706c40。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT g.Name AS genre, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId JOIN main.Genre g ON g.GenreId = t.GenreId WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) GROUP BY g.GenreId, g.Name ORDER BY sales_amount DESC, g.GenreId LIMIT 3
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-008

题目：2025年第三季度销售额最高的五个音频商品及金额是什么？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 商品 ID 1786；名称 Save The Children；销售额 0.99 USD
> 2. 商品 ID 1787；名称 You Sure Love To Ball；销售额 0.99 USD
> 3. 商品 ID 1789；名称 Praise；销售额 0.99 USD
> 4. 商品 ID 1791；名称 Down Under；销售额 0.99 USD
> 5. 商品 ID 1793；名称 Be Good Johnny；销售额 0.99 USD
> 来源：SQL sql-c51f4e2d-09a6-475d-a6ea-b8198641851c。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT t.TrackId AS TrackId, t.Name AS Name, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) GROUP BY t.TrackId, t.Name ORDER BY sales_amount DESC, t.TrackId LIMIT 5
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-009

题目：2025年第三季度各媒体类型的销售额和销量是多少？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 媒体类型 ID 1；名称 MPEG audio file；销售额 111.87 USD；销量 113 件
> 2. 媒体类型 ID 2；名称 Protected AAC audio file；销售额 0.99 USD；销量 1 件
> 来源：SQL sql-42a0a349-ae80-4b77-9562-6b3d5e6e388b。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT mt.MediaTypeId AS MediaTypeId, mt.Name AS Name, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COALESCE(SUM(il.Quantity), 0) AS units_sold FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId JOIN main.MediaType mt ON mt.MediaTypeId = t.MediaTypeId WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date GROUP BY mt.MediaTypeId, mt.Name ORDER BY mt.MediaTypeId
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-010

题目：2025年第三季度音频销售额最高的五个账单国家是什么？

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 账单国家 Brazil；销售额 28.71 USD
> 2. 账单国家 Canada；销售额 24.75 USD
> 3. 账单国家 USA；销售额 19.8 USD
> 4. 账单国家 Netherlands；销售额 15.84 USD
> 5. 账单国家 France；销售额 8.91 USD
> 来源：SQL sql-1b4f562c-0413-42a3-be6e-84e531cd2409。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT i.BillingCountry AS billing_country, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) GROUP BY i.BillingCountry ORDER BY sales_amount DESC, i.BillingCountry LIMIT 5
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

### basic-nl2sql-011

题目：TrackId 为 1791 的商品叫什么？属于哪个音乐分类、哪种媒体类型，标价是多少美元？

实际状态：unsupported；路由：unsupported；业务诊断通过：False；API待复测：False。

实际完整回答：

> 来源：当前业务配置与能力边界；本轮未形成数据库查询结论。
> 查询周期不在当前样例数据覆盖范围 2021-01-01 至 2025-12-22 内。

未通过原因：status: 期望 answered，实际 unsupported

业务缺口：编号正则不支持“TrackId 为 1791”，未屏蔽该编号；期间解析把1791识别成年份，随后被覆盖范围检查拒绝。

建议修复位置：services/agent_api/app/agent/entity_ids.py 的 ID_PATTERN、agent/parsing.py 的期间解析及商品属性任务识别；补齐“为/等于”编号表达和不依赖年份的目录属性查询，不按题号写答案。

接受SQL与参数（若形成查询）：

本题没有形成SQL查询。

### basic-nl2sql-012

题目：按月份列出 2025 年音频销售额和音频订单数，按月份升序排列。音频订单数按订单去重统计。

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 年；当前确认范围。
> 查询结果：
> 1. month 2025-01；销售额 37.62 USD；订单数 7 单
> 2. month 2025-02；销售额 27.72 USD；订单数 5 单
> 3. month 2025-03；销售额 37.62 USD；订单数 7 单
> 4. month 2025-04；销售额 33.66 USD；订单数 5 单
> 5. month 2025-05；销售额 37.62 USD；订单数 7 单
> 6. month 2025-06；销售额 37.62 USD；订单数 7 单
> 7. month 2025-07；销售额 37.62 USD；订单数 7 单
> 8. month 2025-08；销售额 37.62 USD；订单数 7 单
> 9. month 2025-09；销售额 37.62 USD；订单数 7 单
> 10. month 2025-10；销售额 37.62 USD；订单数 7 单
> 11. month 2025-11；销售额 25.74 USD；订单数 7 单
> 12. month 2025-12；销售额 36.63 USD；订单数 6 单
> 来源：SQL sql-8b09a26c-047a-43e2-98e1-156d96768e80。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT strftime('%Y-%m', i.InvoiceDate) AS month, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COUNT(DISTINCT i.InvoiceId) AS order_count FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) GROUP BY strftime('%Y-%m', i.InvoiceDate) ORDER BY strftime('%Y-%m', i.InvoiceDate)
```

参数：`{"start_date": "2025-01-01", "end_date": "2026-01-01"}`

### basic-nl2sql-013

题目：2025 年第三季度，账单国家为美国（USA）的 Rock 音频商品，销售额、销量和订单数分别是多少？订单数按订单去重统计。

实际状态：answered；路由：sql；业务诊断通过：True；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；Rock（Genre:1）；USA（Country:USA）。
> 查询结果：账单国家 USA；销售额 0.99 USD；销量 1 件；订单数 1 单
> 来源：SQL sql-e63b2ad9-d163-461e-92b3-63e8fe29d091。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：外部API未完成：待复测；受控本地结果不计作live通过

原因与建议：本题业务结果核对正确，但模型调用暂缓；先检查既有模型服务的HTTP故障与可用配置，后续用届时明确可用模型完整复测15题，保持同一数据和评分，不因本轮外部故障擅自修改业务代码。

接受SQL与参数（若形成查询）：

```sql
SELECT i.BillingCountry AS billing_country, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COALESCE(SUM(il.Quantity), 0) AS units_sold, COUNT(DISTINCT i.InvoiceId) AS order_count FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId  WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (1,2,4,5) AND t.GenreId = :genre_id AND i.BillingCountry = :billing_country GROUP BY i.BillingCountry ORDER BY i.BillingCountry
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01", "genre_id": 1, "billing_country": "USA"}`

### basic-nl2sql-014

题目：2025 年第三季度，音频消费总额不少于 10 美元的客户有哪些？列出客户编号和消费额，按消费额降序排列，同额按客户编号升序排列。

实际状态：insufficient_evidence；路由：rag；业务诊断通过：False；API待复测：False。

实际完整回答：

> 来源：当前业务配置与能力边界；本轮未形成数据库查询结论。
> 本轮证据、适用范围或计算绑定不完整，不能形成确定结论。

未通过原因：status: 期望 answered，实际 insufficient_evidence

业务缺口：“音频消费总额/消费额”未匹配现有销售额指标词；未知意图进入document_rule/RAG，而客户聚合后金额阈值尚无明确意图与HAVING计划，检索为空。该题没有模型调用。

建议修复位置：services/agent_api/app/profiles/configs/chinook-music.json 的业务指标词、agent/parsing.py 的事实/规则路由；backend/sql_module/query_intent.py 与service.py支持客户粒度、参数化金额阈值>=10和严格次级排序。

接受SQL与参数（若形成查询）：

本题没有形成SQL查询。

### basic-nl2sql-015

题目：列出数据库中所有媒体类型在 2025 年第三季度的销售额和销量，没有成交的类型也要列出并显示为 0，按媒体类型编号升序排列。

实际状态：answered；路由：sql；业务诊断通过：False；API待复测：True。

实际完整回答：

> 来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。
> 查询口径：2025 Q3；当前确认范围。
> 查询结果：
> 1. 媒体类型 ID 1；名称 MPEG audio file；销售额 111.87 USD；销量 113 件
> 2. 媒体类型 ID 2；名称 Protected AAC audio file；销售额 0.99 USD；销量 1 件
> 来源：SQL sql-7145a4b6-3a3d-420b-a162-2b0ea7a865fa。展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。

未通过原因：结果行数 2，期望 5；外部API未完成：待复测；受控本地结果不计作live通过

业务缺口：受控媒体分组从成交明细INNER JOIN出发，只返回本季度有成交的媒体1、2；缺少媒体3、4、5。日期筛选正确，错误是未保留零成交全集。

建议修复位置：backend/sql_module/query_intent.py 显式识别保留全部类型/零成交；service.py 增加维度全集LEFT JOIN已筛选事实聚合计划；semantic_scope.py允许并验证该外连接形式，避免把合规外连接修复回内连接。

接受SQL与参数（若形成查询）：

```sql
SELECT mt.MediaTypeId AS MediaTypeId, mt.Name AS Name, ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, COALESCE(SUM(il.Quantity), 0) AS units_sold FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId = il.InvoiceId JOIN main.Track t ON t.TrackId = il.TrackId JOIN main.MediaType mt ON mt.MediaTypeId = t.MediaTypeId WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date GROUP BY mt.MediaTypeId, mt.Name ORDER BY mt.MediaTypeId
```

参数：`{"start_date": "2025-07-01", "end_date": "2025-10-01"}`

## 本次修改文件

必改：eval/agent/eval_basic_nl2sql.jsonl、eval/human/eval_basic_nl2sql.md、eval/agent/eval_manifest.json。同步：eval/human/README.md、eval/human/eval_validation_report.json、原指令第21节、docs/stage2-scoring-policy.md。生成与验证：scripts/basic_nl2sql_extension.py、scripts/build_eval_set.py、scripts/eval_scoring.py及新增评分反例测试。证据入口：scripts/run_agent_eval.py复用scripts/stage2_eval.py，保留完整记录并严格排除API待复测题。新增本文与提交前进度备注。

未修改backend、业务app、业务profile、数据、冻结第三方副本、根目录历史题库；未进行大范围业务修复，未运行其他专项live。未推送、未创建远程PR或合并。
