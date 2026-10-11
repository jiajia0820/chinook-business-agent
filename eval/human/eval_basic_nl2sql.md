# 基础 NL2SQL

题库文件：`eval/agent/eval_basic_nl2sql.jsonl`
题量：15

## basic-nl2sql-001

问题：2025年第三季度音频销售额、销量、订单数和购买客户数分别是多少？

难度：easy　能力：single_period_total

标准答案：2025年第三季度音频销售额为112.86美元，销量114件，订单21张，购买客户19位。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：四个指标均正确；说明统计范围为2025Q3音频

## basic-nl2sql-002

问题：2025年第二季度音频销售额是多少？

难度：easy　能力：single_period_total

标准答案：2025年第二季度音频销售额为108.90美元。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：结果为108.90美元；说明是音频范围

## basic-nl2sql-003

问题：2025年第三季度 Rock 的音频销售额是多少？

难度：easy　能力：genre_sales

标准答案：2025年第三季度 Rock（Genre:1）音频销售额为47.52美元。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：Genre:1；不并入Rock And Roll

## basic-nl2sql-004

问题：2025年第三季度 Metal 卖了多少件？

难度：easy　能力：genre_units

标准答案：2025年第三季度 Metal（Genre:3）销量为22件。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：销量按Quantity求和；Metal不含Heavy Metal

## basic-nl2sql-005

问题：2025年第三季度有多少张音频订单？

难度：easy　能力：order_count

标准答案：2025年第三季度有21张音频订单。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：InvoiceId去重；不把销量当订单数

## basic-nl2sql-006

问题：2025年第三季度有多少位购买音频的客户？

难度：easy　能力：customer_count

标准答案：2025年第三季度有19位购买音频的客户。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：CustomerId去重

## basic-nl2sql-007

问题：2025年第三季度音频销售额最高的三个音乐分类是什么？

难度：medium　能力：genre_ranking

标准答案：前三名为 Rock（47.52美元）、Metal（21.78美元）和 Latin（15.84美元）。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：按销售额降序；列出金额

## basic-nl2sql-008

问题：2025年第三季度销售额最高的五个音频商品及金额是什么？

难度：medium　能力：track_ranking

标准答案：返回按TrackId区分的前五个商品及其销售额，排序为销售额降序、TrackId升序。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：保留TrackId；金额排序正确

## basic-nl2sql-009

问题：2025年第三季度各媒体类型的销售额和销量是多少？

难度：medium　能力：media_breakdown

标准答案：按 MediaTypeId 分列销售额和销量；音频类型与视频类型分别展示。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, MediaType

评分要点：不把视频混入音频；保留MediaTypeId

## basic-nl2sql-010

问题：2025年第三季度音频销售额最高的五个账单国家是什么？

难度：medium　能力：billing_country_ranking

标准答案：返回按 Invoice.BillingCountry 汇总的前五个国家及销售额，不使用Customer所在地替代账单国家。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：使用账单国家；按销售额降序

## 第三次修改：基础 NL2SQL 扩充至 15 题

011–015于2026-10-10独立只读核实。金额保留两位小数；音频MediaTypeId为1、2、4、5；成交销售额按InvoiceLine.UnitPrice*Quantity，订单按InvoiceId去重。原001–010题意及评分要求不变。

## basic-nl2sql-011

问题：TrackId 为 1791 的商品叫什么？属于哪个音乐分类、哪种媒体类型，标价是多少美元？

难度：easy　能力：track_identity_attributes

标准答案：商品 1791：Down Under；音乐分类 Rock；媒体类型 MPEG audio file；标价 0.99 美元。

来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。

预期数据库表：Track, Genre, MediaType

统计口径及评分要点：精确识别TrackId=1791，不把编号当作年份；列出名称、音乐分类、媒体类型和标价；标价不是成交销售额

```sql
SELECT t.TrackId,t.Name,g.Name AS genre,mt.Name AS media_type,t.UnitPrice AS unit_price
FROM Track t JOIN Genre g ON g.GenreId=t.GenreId
JOIN MediaType mt ON mt.MediaTypeId=t.MediaTypeId
WHERE t.TrackId=1791;
```

标准结果：

```json
{
  "TrackId": 1791,
  "Name": "Down Under",
  "genre": "Rock",
  "media_type": "MPEG audio file",
  "unit_price": 0.99
}
```

## basic-nl2sql-012

问题：按月份列出 2025 年音频销售额和音频订单数，按月份升序排列。音频订单数按订单去重统计。

难度：medium　能力：year_month_audio_sales_distinct_orders

标准答案：2025年逐月音频销售额与去重订单数：2025-01：37.62 美元，7 张订单；2025-02：27.72 美元，5 张订单；2025-03：37.62 美元，7 张订单；2025-04：33.66 美元，5 张订单；2025-05：37.62 美元，7 张订单；2025-06：37.62 美元，7 张订单；2025-07：37.62 美元，7 张订单；2025-08：37.62 美元，7 张订单；2025-09：37.62 美元，7 张订单；2025-10：37.62 美元，7 张订单；2025-11：25.74 美元，7 张订单；2025-12：36.63 美元，6 张订单。按月份升序。

来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。

预期数据库表：Invoice, InvoiceLine, Track

统计口径及评分要点：2025全年左闭右开区间，音频MediaTypeId为1、2、4、5；返回实际查询的12个月，逐月金额和去重订单数正确；按月份升序，不能只返回第三季度或全年总额

```sql
SELECT strftime('%Y-%m',i.InvoiceDate) AS month,
ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-01-01' AND i.InvoiceDate<'2026-01-01'
AND t.MediaTypeId IN (1,2,4,5)
GROUP BY strftime('%Y-%m',i.InvoiceDate) ORDER BY month ASC;
```

标准结果：

```json
[
  {
    "month": "2025-01",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-02",
    "sales_amount": 27.72,
    "order_count": 5
  },
  {
    "month": "2025-03",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-04",
    "sales_amount": 33.66,
    "order_count": 5
  },
  {
    "month": "2025-05",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-06",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-07",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-08",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-09",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-10",
    "sales_amount": 37.62,
    "order_count": 7
  },
  {
    "month": "2025-11",
    "sales_amount": 25.74,
    "order_count": 7
  },
  {
    "month": "2025-12",
    "sales_amount": 36.63,
    "order_count": 6
  }
]
```

## basic-nl2sql-013

问题：2025 年第三季度，账单国家为美国（USA）的 Rock 音频商品，销售额、销量和订单数分别是多少？订单数按订单去重统计。

难度：medium　能力：billing_country_genre_media_multiple_measures

标准答案：2025Q3账单国家USA的Rock音频商品：销售额 0.99 美元，销量 1 件，去重订单 1 张。

来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。

预期数据库表：Invoice, InvoiceLine, Track

统计口径及评分要点：2025Q3、Invoice.BillingCountry=USA、GenreId=1及音频范围同时生效；销售额按成交UnitPrice*Quantity、销量按Quantity，订单按InvoiceId去重；不能用Customer.Country代替账单国家，不并入Rock And Roll

```sql
SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
SUM(il.Quantity) AS units_sold,COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
AND i.BillingCountry='USA' AND t.GenreId=1 AND t.MediaTypeId IN (1,2,4,5);
```

标准结果：

```json
{
  "sales_amount": 0.99,
  "units_sold": 1,
  "order_count": 1
}
```

## basic-nl2sql-014

问题：2025 年第三季度，音频消费总额不少于 10 美元的客户有哪些？列出客户编号和消费额，按消费额降序排列，同额按客户编号升序排列。

难度：medium_hard　能力：customer_having_threshold_tie_order

标准答案：2025Q3音频消费总额不少于10美元的客户：客户 10：15.84 美元；客户 48：15.84 美元；客户 31：13.86 美元。消费额降序，同额客户编号升序。

来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。

预期数据库表：Invoice, InvoiceLine, Track

统计口径及评分要点：客户粒度聚合全部音频消费；聚合后筛选SUM>=10，包含阈值边界；客户编号、金额和完整名单正确；消费额降序，同额客户编号升序，不允许交换并列客户

```sql
SELECT i.CustomerId,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
AND t.MediaTypeId IN (1,2,4,5)
GROUP BY i.CustomerId HAVING SUM(il.UnitPrice*il.Quantity)>=10
ORDER BY sales_amount DESC,i.CustomerId ASC;
```

标准结果：

```json
[
  {
    "CustomerId": 10,
    "sales_amount": 15.84
  },
  {
    "CustomerId": 48,
    "sales_amount": 15.84
  },
  {
    "CustomerId": 31,
    "sales_amount": 13.86
  }
]
```

## basic-nl2sql-015

问题：列出数据库中所有媒体类型在 2025 年第三季度的销售额和销量，没有成交的类型也要列出并显示为 0，按媒体类型编号升序排列。

难度：medium_hard　能力：all_media_left_join_zero_sales

标准答案：2025Q3全部媒体类型：1 MPEG audio file：111.87 美元，113 件；2 Protected AAC audio file：0.99 美元，1 件；3 Protected MPEG-4 video file：0.00 美元，0 件；4 Purchased AAC audio file：0.00 美元，0 件；5 AAC audio file：0.00 美元，0 件。按媒体类型编号升序，零成交保留。

来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。

预期数据库表：MediaType, Invoice, InvoiceLine, Track

统计口径及评分要点：全部媒体类型，包括视频，不加音频限制；日期约束真正参与聚合的成交明细，不能泄漏其他季度金额；返回全部5种类型；3种无成交类型的金额及销量为数值0，不能是NULL或遗漏；按MediaTypeId升序，销售额使用成交单价乘数量

```sql
SELECT mt.MediaTypeId,mt.Name,COALESCE(f.sales_amount,0) AS sales_amount,
COALESCE(f.units_sold,0) AS units_sold
FROM MediaType mt LEFT JOIN (
SELECT t.MediaTypeId,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
SUM(il.Quantity) AS units_sold
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
GROUP BY t.MediaTypeId
) f ON f.MediaTypeId=mt.MediaTypeId ORDER BY mt.MediaTypeId ASC;
```

标准结果：

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
