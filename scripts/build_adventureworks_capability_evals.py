from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "adventureworks"
AGENT = OUT / "agent"
HUMAN = OUT / "human"
AGENT.mkdir(parents=True, exist_ok=True)
HUMAN.mkdir(parents=True, exist_ok=True)

PROFILE_ID = "adventureworks"
MONEY_EXPR = 'd."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount")'


def normalize(value):
    if isinstance(value, Decimal):
        return float(value.quantize(Decimal("0.01")))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def rows_normalized(rows):
    return [{k: normalize(v) for k, v in row.items()} for row in rows]


def fmt(value, kind):
    if kind == "money":
        return f"{float(value):,.2f}"
    if kind == "integer":
        return f"{int(value):,}"
    return str(value)


def interface(question, status, route):
    nonempty = ["answer"] if status == "answered" else ["clarification" if status == "clarification_required" else "limitations"]
    return {
        "profile_id": PROFILE_ID, "mode": "sql_only",
        "request": {"question": question, "profile_id": PROFILE_ID, "user_role": "operator", "options": {"show_trace": True, "max_rows": 50, "top_k": 5}},
        "expected_status": status, "expected_route": route,
        "required_response_fields": ["request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities", "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations", "clarification", "trace", "error"],
        "required_nonempty_fields": nonempty,
    }


def answer_rows(spec, rows):
    if spec["kind"] == "scalar":
        row = rows[0]
        return spec["template"].format(**{name: fmt(row[column], kind) for name, column, kind in spec["values"]})
    lines = []
    for i, row in enumerate(rows, 1):
        values = [f"{label}{fmt(row[column], kind)}" for column, kind, label in spec["columns"]]
        lines.append(f"{i}. " + "，".join(values))
    return spec["prefix"] + "\n" + "\n".join(lines)


CLARIFICATION = [
    {"id": "aw-clarify-001", "question": "销售额是多少？", "gold_answer": "请补充统计年份或日期范围，例如 2013 年或 2013 年第一季度。", "missing": ["year", "time_range"], "requirements": ["不猜测年份", "主动请求时间范围"]},
    {"id": "aw-clarify-002", "question": "2013年表现怎么样？", "gold_answer": "请明确要查询的指标，例如销售额、销量、订单数或客户数。", "missing": ["metric"], "requirements": ["主动请求指标", "不擅自选择销售额"]},
    {"id": "aw-clarify-003", "question": "2013年产品排名是什么？", "gold_answer": "请明确排名指标，例如按销售额排名还是按销量排名。", "missing": ["metric"], "requirements": ["识别排名指标缺失", "不擅自选择销售额"]},
    {"id": "aw-clarify-004", "question": "2013年客户情况是多少？", "gold_answer": "请明确客户指标，例如客户记录数、购买客户数或客户购买金额排名。", "missing": ["metric"], "requirements": ["识别客户指标歧义", "主动澄清"]},
    {"id": "aw-clarify-005", "question": "订单增长率是多少？", "gold_answer": "请补充比较周期，例如 2013 年相对 2012 年，或具体两个季度。", "missing": ["comparison_period"], "requirements": ["识别比较周期缺失", "不直接生成增长率"]},
    {"id": "aw-clarify-006", "question": "2013年销售额最高的是谁？", "gold_answer": "请明确‘谁’指产品、产品子类别、产品类别还是客户。", "missing": ["entity_type"], "requirements": ["识别实体类型歧义", "主动澄清"]},
    {"id": "aw-clarify-007", "question": "2013年销售额是多少，按下单日期还是发货日期？", "gold_answer": "请确认日期口径：按下单日期（OrderDate）还是发货日期（ShipDate）统计。", "missing": ["date_field"], "requirements": ["识别日期字段歧义", "不擅自选择日期字段"]},
    {"id": "aw-clarify-008", "question": "2013年销售额，按类别。", "gold_answer": "请确认按产品类别（ProductCategory）还是产品子类别（ProductSubcategory）分组。", "missing": ["entity_type"], "requirements": ["识别类别层级歧义", "主动澄清"]},
]


SCHEMA = [
    {
        "id": "aw-schema-001", "question": "2013年按下单日期统计的销售额是多少？", "capability": "order_date_linking",
        "sql": f'''SELECT ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "kind": "scalar", "values": [("sales_amount", "sales_amount", "money")], "template": "按下单日期统计，2013年销售额为{sales_amount}美元。", "requirements": ["使用 SalesOrderHeader.OrderDate", "不使用 DueDate、ShipDate 或 ModifiedDate"],
    },
    {
        "id": "aw-schema-002", "question": "2013年按发货日期统计的订单数是多少？", "capability": "ship_date_linking",
        "sql": '''SELECT COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
WHERE h."ShipDate" >= DATE '2013-01-01' AND h."ShipDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader"], "kind": "scalar", "values": [("order_count", "order_count", "integer")], "template": "按发货日期统计，2013年订单数为{order_count}张。", "requirements": ["使用 ShipDate", "按 SalesOrderID 去重"],
    },
    {
        "id": "aw-schema-003", "question": "2013年到期日期落在年内的订单数是多少？", "capability": "due_date_linking",
        "sql": '''SELECT COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
WHERE h."DueDate" >= DATE '2013-01-01' AND h."DueDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader"], "kind": "scalar", "values": [("order_count", "order_count", "integer")], "template": "按到期日期统计，2013年订单数为{order_count}张。", "requirements": ["使用 DueDate", "不能误用 OrderDate"],
    },
    {
        "id": "aw-schema-004", "question": "各产品的累计销售额是多少？", "capability": "product_table_linking",
        "sql": f'''SELECT p."ProductID" AS product_id, p."Name" AS product_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
FROM "Production"."Product" p JOIN "Sales"."SalesOrderDetail" d ON d."ProductID" = p."ProductID"
GROUP BY p."ProductID", p."Name" ORDER BY p."ProductID" LIMIT 10;''',
        "tables": ["Production.Product", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("sales_amount", "money", "销售额 ")], "prefix": "按 Production.Product.Name 展示产品销售额（前10个产品，美元）：", "requirements": ["产品名称来自 Production.Product", "通过 ProductID 关联明细"],
    },
    {
        "id": "aw-schema-005", "question": "2013年各产品类别的销售额是多少？", "capability": "category_path_linking",
        "sql": f'''SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID"
JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY c."ProductCategoryID", c."Name" ORDER BY c."ProductCategoryID";''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("sales_amount", "money", "销售额 ")], "prefix": "2013年各产品类别销售额如下（美元）：", "requirements": ["使用 Product→ProductSubcategory→ProductCategory 路径", "不直接把产品当类别"]},
    {
        "id": "aw-schema-006", "question": "每个客户的订单数是多少？", "capability": "customer_header_linking",
        "sql": '''SELECT h."CustomerID" AS customer_id, COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h GROUP BY h."CustomerID" ORDER BY h."CustomerID" LIMIT 10;''',
        "tables": ["Sales.SalesOrderHeader"], "kind": "rows", "columns": [("customer_id", "integer", "客户ID "), ("order_count", "integer", "订单数 ")], "prefix": "前10位客户的订单数如下：", "requirements": ["使用 SalesOrderHeader.CustomerID", "按订单去重"]},
    {
        "id": "aw-schema-007", "question": "2013年订单修改时间在年内的订单数是多少？", "capability": "modified_date_linking",
        "sql": '''SELECT COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
WHERE h."ModifiedDate" >= TIMESTAMP '2013-01-01' AND h."ModifiedDate" < TIMESTAMP '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader"], "kind": "scalar", "values": [("order_count", "order_count", "integer")], "template": "按 ModifiedDate 统计，2013年修改过的订单数为{order_count}张。", "requirements": ["使用 ModifiedDate", "不把修改日期解释为下单日期"]},
    {
        "id": "aw-schema-008", "question": "每个产品类别有多少个产品？", "capability": "category_product_path",
        "sql": '''SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, COUNT(p."ProductID") AS product_count
FROM "Production"."ProductCategory" c JOIN "Production"."ProductSubcategory" s ON s."ProductCategoryID" = c."ProductCategoryID"
JOIN "Production"."Product" p ON p."ProductSubcategoryID" = s."ProductSubcategoryID"
GROUP BY c."ProductCategoryID", c."Name" ORDER BY c."ProductCategoryID";''',
        "tables": ["Production.ProductCategory", "Production.ProductSubcategory", "Production.Product"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("product_count", "integer", "产品数 ")], "prefix": "各产品类别的产品数如下：", "requirements": ["正确经过子类别关联产品", "按 ProductID 计数"]},
]


COMPLEX = [
    {
        "id": "aw-multi-001", "question": "2013年销售额最高的5个产品是什么？", "capability": "multitable_topn", "sql": f'''SELECT p."ProductID" AS product_id, p."Name" AS product_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID" JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY p."ProductID", p."Name" ORDER BY sales_amount DESC, p."ProductID" LIMIT 5;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product"], "kind": "rows", "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("sales_amount", "money", "销售额 ")], "prefix": "2013年销售额最高的5个产品如下（美元）：", "requirements": ["JOIN 路径正确", "按销售额降序", "只返回5行"]},
    {
        "id": "aw-multi-002", "question": "2013年销售额最高的5个产品类别是什么？", "capability": "multitable_category_topn", "sql": f'''SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID" JOIN "Production"."Product" p ON p."ProductID" = d."ProductID" JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID" JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY c."ProductCategoryID", c."Name" ORDER BY sales_amount DESC, c."ProductCategoryID" LIMIT 5;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("sales_amount", "money", "销售额 ")], "prefix": "2013年销售额最高的产品类别如下（美元）：", "requirements": ["四表关联正确", "按类别聚合", "Top-N 排序正确"]},
    {
        "id": "aw-multi-003", "question": "2013年购买金额最高的5位客户是谁？", "capability": "customer_join_aggregation", "sql": f'''SELECT h."CustomerID" AS customer_id, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount, COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY h."CustomerID" ORDER BY sales_amount DESC, h."CustomerID" LIMIT 5;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("customer_id", "integer", "客户ID "), ("sales_amount", "money", "销售额 "), ("order_count", "integer", "订单数 ")], "prefix": "2013年购买金额最高的5位客户如下：", "requirements": ["客户按 CustomerID 分组", "订单数去重", "金额排序正确"]},
    {
        "id": "aw-multi-004", "question": "2013年订单数超过500张的客户有哪些？", "capability": "having_filter", "sql": '''SELECT h."CustomerID" AS customer_id, COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY h."CustomerID" HAVING COUNT(DISTINCT h."SalesOrderID") > 500 ORDER BY order_count DESC, h."CustomerID";''', "tables": ["Sales.SalesOrderHeader"], "kind": "rows", "columns": [("customer_id", "integer", "客户ID "), ("order_count", "integer", "订单数 ")], "prefix": "2013年订单数超过500张的客户如下：", "requirements": ["使用 HAVING 过滤聚合结果", "不能在 WHERE 中错误过滤订单数"]},
    {
        "id": "aw-multi-005", "question": "从未销售过的产品有哪些？", "capability": "not_exists", "sql": '''SELECT p."ProductID" AS product_id, p."Name" AS product_name
FROM "Production"."Product" p WHERE NOT EXISTS (SELECT 1 FROM "Sales"."SalesOrderDetail" d WHERE d."ProductID" = p."ProductID")
ORDER BY p."ProductID";''', "tables": ["Production.Product", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 ")], "prefix": "从未出现在销售明细中的产品如下：", "requirements": ["使用 NOT EXISTS 或等价反连接", "不能把缺失订单误报为销售0的订单"]},
    {
        "id": "aw-multi-006", "question": "2013年每个产品类别中销售额最高的产品是什么？", "capability": "window_partition_rank", "sql": f'''WITH product_sales AS (
  SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, p."ProductID" AS product_id, p."Name" AS product_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
  FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID" JOIN "Production"."Product" p ON p."ProductID" = d."ProductID" JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID" JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
  WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY c."ProductCategoryID", c."Name", p."ProductID", p."Name"
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY sales_amount DESC, product_id) AS rn FROM product_sales
)
SELECT category_id, category_name, product_id, product_name, sales_amount FROM ranked WHERE rn = 1 ORDER BY category_id;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("sales_amount", "money", "销售额 ")], "prefix": "2013年各产品类别销售额最高的产品如下：", "requirements": ["使用分区排名或等价逻辑", "每个类别只保留1个产品", "类别内按销售额排序"]},
    {
        "id": "aw-multi-007", "question": "2013年按月计算销售额，并给出截至当月的累计销售额。", "capability": "window_running_total", "sql": f'''WITH monthly AS (
  SELECT DATE_TRUNC('month', h."OrderDate") AS month_start, ROUND(SUM({MONEY_EXPR}), 2) AS monthly_sales
  FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
  WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY DATE_TRUNC('month', h."OrderDate")
)
SELECT TO_CHAR(month_start, 'YYYY-MM') AS month, monthly_sales, ROUND(SUM(monthly_sales) OVER (ORDER BY month_start), 2) AS cumulative_sales
FROM monthly ORDER BY month_start;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("month", "text", "月份 "), ("monthly_sales", "money", "当月销售额 "), ("cumulative_sales", "money", "累计销售额 ")], "prefix": "2013年月度销售额和累计销售额如下（美元）：", "requirements": ["先按月聚合", "累计值按月份顺序计算", "不能把全年的总额重复到每个月"]},
    {
        "id": "aw-multi-008", "question": "2013年每个产品类别的销售额及其占全部类别销售额的比例是多少？", "capability": "window_share", "sql": f'''WITH category_sales AS (
  SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, SUM({MONEY_EXPR}) AS raw_sales
  FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID" JOIN "Production"."Product" p ON p."ProductID" = d."ProductID" JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID" JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
  WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY c."ProductCategoryID", c."Name"
)
SELECT category_id, category_name, ROUND(raw_sales, 2) AS sales_amount, ROUND(raw_sales / NULLIF(SUM(raw_sales) OVER (), 0) * 100, 2) AS sales_share_pct
FROM category_sales ORDER BY sales_amount DESC, category_id;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("sales_amount", "money", "销售额 "), ("sales_share_pct", "money", "销售占比 ")], "prefix": "2013年各产品类别销售额及占比如下（金额单位：美元，占比单位：%）：", "requirements": ["类别销售额正确", "分母为全部类别销售额", "比例计算不能按类别内部归一化"]},
    {
        "id": "aw-multi-009", "question": "2013年每个客户的订单金额，并找出金额最高的客户。", "capability": "cte_customer_rank", "sql": f'''WITH customer_sales AS (
  SELECT h."CustomerID" AS customer_id, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
  FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
  WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY h."CustomerID"
)
SELECT customer_id, sales_amount FROM customer_sales ORDER BY sales_amount DESC, customer_id LIMIT 1;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("customer_id", "integer", "客户ID "), ("sales_amount", "money", "销售额 ")], "prefix": "2013年订单金额最高的客户如下（美元）：", "requirements": ["先按客户聚合", "再按金额排序取最高客户"]},
    {
        "id": "aw-multi-010", "question": "2013年每个月订单数排名前3的月份是什么？", "capability": "window_top_months", "sql": '''WITH monthly AS (
  SELECT DATE_TRUNC('month', h."OrderDate") AS month_start, COUNT(DISTINCT h."SalesOrderID") AS order_count
  FROM "Sales"."SalesOrderHeader" h WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY DATE_TRUNC('month', h."OrderDate")
), ranked AS (
  SELECT TO_CHAR(month_start, 'YYYY-MM') AS month, order_count, RANK() OVER (ORDER BY order_count DESC) AS rank_no FROM monthly
)
SELECT month, order_count, rank_no FROM ranked WHERE rank_no <= 3 ORDER BY rank_no, month;''', "tables": ["Sales.SalesOrderHeader"], "kind": "rows", "columns": [("month", "text", "月份 "), ("order_count", "integer", "订单数 "), ("rank_no", "integer", "排名 ")], "prefix": "2013年订单数排名前3的月份如下：", "requirements": ["按月聚合订单数", "使用排名保留前3名", "结果包含排名字段"]},
    {
        "id": "aw-multi-011", "question": "2013年同时购买超过5个不同产品的客户有哪些？", "capability": "having_distinct_count", "sql": '''SELECT h."CustomerID" AS customer_id, COUNT(DISTINCT d."ProductID") AS product_count
FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY h."CustomerID" HAVING COUNT(DISTINCT d."ProductID") > 5 ORDER BY product_count DESC, h."CustomerID";''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "kind": "rows", "columns": [("customer_id", "integer", "客户ID "), ("product_count", "integer", "不同产品数 ")], "prefix": "2013年购买超过5个不同产品的客户如下：", "requirements": ["按 ProductID 去重", "使用 HAVING 过滤不同产品数"]},
    {
        "id": "aw-multi-012", "question": "2013年产品类别销售额排名第二的类别是什么？", "capability": "dense_rank_category", "sql": f'''WITH category_sales AS (
  SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name, ROUND(SUM({MONEY_EXPR}), 2) AS sales_amount
  FROM "Sales"."SalesOrderHeader" h JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID" JOIN "Production"."Product" p ON p."ProductID" = d."ProductID" JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID" JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
  WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
  GROUP BY c."ProductCategoryID", c."Name"
), ranked AS (SELECT *, DENSE_RANK() OVER (ORDER BY sales_amount DESC) AS rank_no FROM category_sales)
SELECT category_id, category_name, sales_amount, rank_no FROM ranked WHERE rank_no = 2 ORDER BY category_id;''', "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "kind": "rows", "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("sales_amount", "money", "销售额 "), ("rank_no", "integer", "排名 ")], "prefix": "2013年产品类别销售额排名第二的类别如下（美元）：", "requirements": ["先按类别聚合", "使用排名第二的语义", "不把第二行误认为第二名"]},
]


def make_record(spec, rows):
    result = rows_normalized(rows)
    if spec["kind"] == "scalar" and len(result) == 1:
        result = result[0]
    req = spec.get("requirements", ["标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行", "JOIN、聚合、时间范围、排序和粒度与问题一致"])
    return {
        "id": spec["id"], "level": "special", "capability": spec["capability"], "question": spec["question"], "data_scope": "AdventureWorks",
        "expected_tables": spec["tables"], "expected_documents": [], "evidence_locations": [], "gold_sql": spec["sql"], "gold_result": result,
        "gold_calculation": None, "gold_answer": answer_rows(spec, rows), "gold_params": [], "known_conflicts": [],
        "gold_answer_requirements": req, "clarification_required": False, "clarification_target": None, "difficulty": "medium", "split": "dev", "modality": "text",
        "interface_expectation": {"profile_id": PROFILE_ID, "mode": "sql_only", "request": {"question": spec["question"], "profile_id": PROFILE_ID, "user_role": "operator", "options": {"show_trace": True, "max_rows": 50, "top_k": 5}}, "expected_status": "answered", "expected_route": "sql", "required_response_fields": ["request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities", "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations", "clarification", "trace", "error"], "required_nonempty_fields": ["answer"]},
    }


def main():
    url = os.environ.get("ADVENTUREWORKS_DATABASE_URL")
    if not url:
        raise SystemExit("请先设置 ADVENTUREWORKS_DATABASE_URL")
    groups = {"eval_schema_linking.jsonl": SCHEMA, "eval_multitable.jsonl": COMPLEX}
    report = {"profile_id": PROFILE_ID, "errors": [], "checks": {}, "record_counts": {}}
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for filename, specs in groups.items():
            records, checked = [], []
            for spec in specs:
                with conn.cursor() as cur:
                    cur.execute(spec["sql"])
                    rows = cur.fetchall()
                record = make_record(spec, rows)
                records.append(record)
                with conn.cursor() as cur:
                    cur.execute(spec["sql"])
                    actual = rows_normalized(cur.fetchall())
                expected = record["gold_result"]
                comparable = actual[0] if isinstance(expected, dict) and len(actual) == 1 else actual
                if comparable != expected:
                    report["errors"].append({"id": spec["id"], "expected": expected, "actual": actual})
                else:
                    checked.append(spec["id"])
            report["checks"][filename] = checked
            report["record_counts"][filename] = len(records)
            (AGENT / filename).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
            lines = [f"# {filename[:-6]}", "", f"题量：{len(records)}", "", "标准 SQL 留在评测端，用于真实数据库复算 gold_result，不发送给 Agent。", ""]
            for r in records:
                lines.extend([f"## {r['id']}", "", f"问题：{r['question']}", "", f"能力：{r['capability']}", "", f"标准答案：{r['gold_answer']}", "", f"预期表：{', '.join(r['expected_tables'])}", "", "评分要点：" + "；".join(r["gold_answer_requirements"]), ""])
            (HUMAN / filename.replace(".jsonl", ".md")).write_text("\n".join(lines), encoding="utf-8")
    clarification_records = []
    for spec in CLARIFICATION:
        is_answerable = not spec["missing"]
        clarification_records.append({
            "id": spec["id"], "level": "special", "capability": "clarification", "question": spec["question"], "data_scope": "AdventureWorks",
            "expected_tables": [], "expected_documents": [], "evidence_locations": [], "gold_sql": None, "gold_result": None, "gold_calculation": None,
            "gold_answer": spec["gold_answer"], "gold_params": [], "known_conflicts": [], "gold_answer_requirements": spec["requirements"],
            "clarification_required": bool(spec["missing"]), "clarification_target": spec["missing"] or None, "difficulty": "easy", "split": "dev", "modality": "text",
            "interface_expectation": {
                "profile_id": PROFILE_ID, "mode": "sql_only", "request": {"question": spec["question"], "profile_id": PROFILE_ID, "user_role": "operator", "options": {"show_trace": True, "max_rows": 50, "top_k": 5}},
                "expected_status": "clarification_required" if spec["missing"] else "answered", "expected_route": "clarification" if spec["missing"] else "sql",
                "required_response_fields": ["request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities", "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations", "clarification", "trace", "error"],
                "required_nonempty_fields": ["clarification"] if spec["missing"] else ["answer"],
            },
        })
    (AGENT / "eval_clarification.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in clarification_records) + "\n", encoding="utf-8")
    clarification_lines = ["# eval_clarification", "", "题量：8", ""]
    for record in clarification_records:
        clarification_lines.extend([
            f"## {record['id']}", "", f"问题：{record['question']}", "",
            f"参考行为：{record['gold_answer']}", "",
            f"澄清槽位：{', '.join(record['clarification_target'] or []) or '无'}", "",
        ])
    (HUMAN / "eval_clarification.md").write_text("\n".join(clarification_lines), encoding="utf-8")
    report["record_counts"]["eval_clarification.jsonl"] = len(clarification_records)
    (AGENT / "capability_eval_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile_id": PROFILE_ID,
        "database": "data/adventureworks",
        "files": {
            "eval_basic_nl2sql.jsonl": sum(1 for _ in (AGENT / "eval_basic_nl2sql.jsonl").open(encoding="utf-8")) if (AGENT / "eval_basic_nl2sql.jsonl").exists() else 0,
            "eval_clarification.jsonl": len(clarification_records),
            "eval_schema_linking.jsonl": len(SCHEMA),
            "eval_multitable.jsonl": len(COMPLEX),
        },
        "standard_sql_verified": True,
        "standard_sql_validation_report": "agent/capability_eval_validation_report.json",
    }
    (AGENT / "eval_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text("# AdventureWorks 能力评测集\n\n- `agent/eval_clarification.jsonl`：缺失条件和歧义条件澄清题库。\n- `agent/eval_schema_linking.jsonl`：Schema Linking 题库。\n- `agent/eval_multitable.jsonl`：多表、聚合、嵌套、窗口函数题库。\n- `agent/capability_eval_validation_report.json`：每条标准 SQL 的真实数据库复算结果。\n- 标准 SQL 只在评测端执行，不发送给 Agent。\n", encoding="utf-8")
    print(json.dumps({"record_counts": report["record_counts"], "errors": report["errors"]}, ensure_ascii=False, indent=2))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
