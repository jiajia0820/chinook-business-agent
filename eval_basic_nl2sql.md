# 基础 NL2SQL

本文是本轮 10 题评测的题目与标准答案快照。原始题库路径记录为 `eval/agent/eval_basic_nl2sql.jsonl`，该 JSONL 未纳入本次提交；本批次依据以下题目核验。
题量：10

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
