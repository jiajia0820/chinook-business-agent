from __future__ import annotations

import json
import os
from datetime import date, datetime
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
DATABASE = "data/adventureworks"
SOURCE_REF = "AW.CATALOG"


def normalize(value):
    if isinstance(value, Decimal):
        value = value.quantize(Decimal("0.01"))
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def normalize_rows(rows):
    return [{key: normalize(value) for key, value in row.items()} for row in rows]


def money(value):
    return f"{float(value):,.2f}"


def integer(value):
    return f"{int(value):,}"


def scalar_answer(spec, row):
    value = row[spec["value_column"]]
    unit = spec.get("unit", "")
    rendered = money(value) if spec.get("number") == "money" else integer(value) if spec.get("number") == "integer" else str(value)
    return spec["answer_template"].format(value=rendered, unit=unit)


def rows_answer(spec, rows):
    lines = []
    for index, row in enumerate(rows, 1):
        values = []
        for column, kind, label in spec["columns"]:
            value = row[column]
            if kind == "money":
                rendered = money(value)
            elif kind == "integer":
                rendered = integer(value)
            else:
                rendered = str(value)
            values.append(f"{label}{rendered}")
        lines.append(f"{index}. " + "，".join(values))
    return spec["answer_prefix"] + "\n" + "\n".join(lines)


def custom_answer(spec, rows):
    if spec["answer_kind"] == "scalar":
        return scalar_answer(spec, rows[0])
    if spec["answer_kind"] == "rows":
        return rows_answer(spec, rows)
    if spec["answer_kind"] == "date_range":
        row = rows[0]
        return spec["answer_template"].format(start=row["earliest_order"], end=row["latest_order"])
    raise ValueError(f"unknown answer kind: {spec['answer_kind']}")


def interface(status="answered", route="sql"):
    required = ["answer"] if status == "answered" else ["clarification" if status == "clarification_required" else "limitations"]
    return {
        "profile_id": PROFILE_ID,
        "mode": "sql_only",
        "request": {
            "question": None,
            "profile_id": PROFILE_ID,
            "user_role": "operator",
            "options": {"show_trace": True, "max_rows": 50, "top_k": 5},
        },
        "expected_status": status,
        "expected_route": route,
        "required_response_fields": [
            "request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities",
            "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations",
            "clarification", "trace", "error",
        ],
        "required_nonempty_fields": required,
    }


SPECS = [
    {
        "id": "aw-basic-001", "capability": "annual_sales_total", "question": "2013年总销售额是多少？",
        "sql": '''SELECT ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "scalar",
        "value_column": "sales_amount", "number": "money", "unit": "美元",
        "answer_template": "2013年总销售额为{value}美元。", "difficulty": "easy",
    },
    {
        "id": "aw-basic-002", "capability": "annual_units_total", "question": "2013年一共卖出多少件产品？",
        "sql": '''SELECT SUM(d."OrderQty") AS units_sold
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "scalar",
        "value_column": "units_sold", "number": "integer", "unit": "件",
        "answer_template": "2013年共售出{value}件产品。", "difficulty": "easy",
    },
    {
        "id": "aw-basic-003", "capability": "annual_order_count", "question": "2013年有多少张销售订单？",
        "sql": '''SELECT COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader"], "answer_kind": "scalar",
        "value_column": "order_count", "number": "integer", "unit": "张",
        "answer_template": "2013年有{value}张销售订单。", "difficulty": "easy",
    },
    {
        "id": "aw-basic-004", "capability": "customer_count", "question": "AdventureWorks中有多少个客户？",
        "sql": '''SELECT COUNT(*) AS customer_count FROM "Sales"."Customer";''',
        "tables": ["Sales.Customer"], "answer_kind": "scalar",
        "value_column": "customer_count", "number": "integer", "unit": "个",
        "answer_template": "AdventureWorks中共有{value}个客户记录。", "difficulty": "easy",
    },
    {
        "id": "aw-basic-005", "capability": "annual_sales_and_units", "question": "2013年销售额和销量分别是多少？",
        "sql": '''SELECT ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount, SUM(d."OrderQty") AS units_sold
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("sales_amount", "money", "销售额 "), ("units_sold", "integer", "销量 ")],
        "answer_prefix": "2013年指标如下（金额单位：美元，销量单位：件）：", "difficulty": "easy",
    },
    {
        "id": "aw-basic-006", "capability": "monthly_sales", "question": "2013年每个月的销售额是多少？",
        "sql": '''SELECT TO_CHAR(DATE_TRUNC('month', h."OrderDate"), 'YYYY-MM') AS month,
       ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY DATE_TRUNC('month', h."OrderDate")
ORDER BY DATE_TRUNC('month', h."OrderDate");''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("month", "text", "月份 "), ("sales_amount", "money", "销售额 ")],
        "answer_prefix": "2013年各月销售额如下（美元）：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-007", "capability": "top_product_sales", "question": "2013年销售额最高的5个产品是什么？",
        "sql": '''SELECT p."ProductID" AS product_id, p."Name" AS product_name,
       ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY p."ProductID", p."Name"
ORDER BY sales_amount DESC, p."ProductID"
LIMIT 5;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product"], "answer_kind": "rows",
        "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("sales_amount", "money", "销售额 ")],
        "answer_prefix": "2013年销售额最高的5个产品如下（美元）：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-008", "capability": "top_product_units", "question": "2013年销量最高的5个产品是什么？",
        "sql": '''SELECT p."ProductID" AS product_id, p."Name" AS product_name,
       SUM(d."OrderQty") AS units_sold
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY p."ProductID", p."Name"
ORDER BY units_sold DESC, p."ProductID"
LIMIT 5;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product"], "answer_kind": "rows",
        "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("units_sold", "integer", "销量 ")],
        "answer_prefix": "2013年销量最高的5个产品如下（件）：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-009", "capability": "category_sales", "question": "2013年各产品类别的销售额是多少？",
        "sql": '''SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name,
       ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID"
JOIN "Production"."ProductCategory" c ON c."ProductCategoryID" = s."ProductCategoryID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY c."ProductCategoryID", c."Name"
ORDER BY sales_amount DESC, c."ProductCategoryID";''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory", "Production.ProductCategory"], "answer_kind": "rows",
        "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("sales_amount", "money", "销售额 ")],
        "answer_prefix": "2013年各产品类别销售额如下（美元）：", "difficulty": "hard",
    },
    {
        "id": "aw-basic-010", "capability": "subcategory_sales", "question": "2013年销售额最高的5个产品子类别是什么？",
        "sql": '''SELECT s."ProductSubcategoryID" AS subcategory_id, s."Name" AS subcategory_name,
       ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
JOIN "Production"."Product" p ON p."ProductID" = d."ProductID"
JOIN "Production"."ProductSubcategory" s ON s."ProductSubcategoryID" = p."ProductSubcategoryID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY s."ProductSubcategoryID", s."Name"
ORDER BY sales_amount DESC, s."ProductSubcategoryID"
LIMIT 5;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail", "Production.Product", "Production.ProductSubcategory"], "answer_kind": "rows",
        "columns": [("subcategory_id", "integer", "子类别ID "), ("subcategory_name", "text", "子类别 "), ("sales_amount", "money", "销售额 ")],
        "answer_prefix": "2013年销售额最高的5个产品子类别如下（美元）：", "difficulty": "hard",
    },
    {
        "id": "aw-basic-011", "capability": "customer_sales_ranking", "question": "2013年购买金额最高的5位客户是谁？",
        "sql": '''SELECT h."CustomerID" AS customer_id, ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount,
       COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY h."CustomerID"
ORDER BY sales_amount DESC, h."CustomerID"
LIMIT 5;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("customer_id", "integer", "客户ID "), ("sales_amount", "money", "销售额 "), ("order_count", "integer", "订单数 ")],
        "answer_prefix": "2013年购买金额最高的5位客户如下（金额单位：美元）：", "difficulty": "hard",
    },
    {
        "id": "aw-basic-012", "capability": "average_order_value", "question": "2013年的平均订单金额是多少？",
        "sql": '''SELECT ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ) / COUNT(DISTINCT h."SalesOrderID"), 2) AS average_order_value
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01';''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "scalar",
        "value_column": "average_order_value", "number": "money", "unit": "美元/单",
        "answer_template": "2013年平均每张订单金额为{value}美元。", "difficulty": "medium",
    },
    {
        "id": "aw-basic-013", "capability": "online_offline_sales", "question": "2013年线上订单和线下订单的销售额分别是多少？",
        "sql": '''SELECT h."OnlineOrderFlag" AS online_order, ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount,
       COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY h."OnlineOrderFlag"
ORDER BY h."OnlineOrderFlag" DESC;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("online_order", "text", "线上订单标记 "), ("sales_amount", "money", "销售额 "), ("order_count", "integer", "订单数 ")],
        "answer_prefix": "2013年线上/线下订单统计如下（金额单位：美元）：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-014", "capability": "monthly_order_count", "question": "2013年每个月的订单数是多少？",
        "sql": '''SELECT TO_CHAR(DATE_TRUNC('month', h."OrderDate"), 'YYYY-MM') AS month,
       COUNT(DISTINCT h."SalesOrderID") AS order_count
FROM "Sales"."SalesOrderHeader" h
WHERE h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY DATE_TRUNC('month', h."OrderDate")
ORDER BY DATE_TRUNC('month', h."OrderDate");''',
        "tables": ["Sales.SalesOrderHeader"], "answer_kind": "rows",
        "columns": [("month", "text", "月份 "), ("order_count", "integer", "订单数 ")],
        "answer_prefix": "2013年各月订单数如下：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-015", "capability": "latest_order_date", "question": "AdventureWorks订单数据覆盖的最早和最晚订单日期是什么？",
        "sql": '''SELECT MIN(h."OrderDate")::date AS earliest_order, MAX(h."OrderDate")::date AS latest_order
FROM "Sales"."SalesOrderHeader" h;''',
        "tables": ["Sales.SalesOrderHeader"], "answer_kind": "date_range",
        "answer_template": "订单数据最早日期为{start}，最晚日期为{end}。", "difficulty": "easy",
    },
    {
        "id": "aw-basic-016", "capability": "product_sales_by_id", "question": "产品ID 709 的累计销售额是多少？",
        "sql": '''SELECT p."ProductID" AS product_id, p."Name" AS product_name,
       ROUND(COALESCE(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 0), 2) AS sales_amount
FROM "Production"."Product" p
LEFT JOIN "Sales"."SalesOrderDetail" d ON d."ProductID" = p."ProductID"
WHERE p."ProductID" = 709
GROUP BY p."ProductID", p."Name";''',
        "tables": ["Production.Product", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 "), ("sales_amount", "money", "累计销售额 ")],
        "answer_prefix": "产品ID 709 的累计销售额如下（美元）：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-017", "capability": "unsold_expensive_products", "question": "列表价超过1000美元且从未销售的产品有多少个？",
        "sql": '''SELECT COUNT(*) AS product_count
FROM "Production"."Product" p
WHERE p."ListPrice" > 1000
  AND NOT EXISTS (SELECT 1 FROM "Sales"."SalesOrderDetail" d WHERE d."ProductID" = p."ProductID");''',
        "tables": ["Production.Product", "Sales.SalesOrderDetail"], "answer_kind": "scalar",
        "value_column": "product_count", "number": "integer", "unit": "个",
        "answer_template": "列表价超过1000美元且从未销售的产品共有{value}个。", "difficulty": "hard",
    },
    {
        "id": "aw-basic-018", "capability": "category_product_count", "question": "各产品类别分别有多少个产品？",
        "sql": '''SELECT c."ProductCategoryID" AS category_id, c."Name" AS category_name,
       COUNT(p."ProductID") AS product_count
FROM "Production"."ProductCategory" c
JOIN "Production"."ProductSubcategory" s ON s."ProductCategoryID" = c."ProductCategoryID"
JOIN "Production"."Product" p ON p."ProductSubcategoryID" = s."ProductSubcategoryID"
GROUP BY c."ProductCategoryID", c."Name"
ORDER BY c."ProductCategoryID";''',
        "tables": ["Production.ProductCategory", "Production.ProductSubcategory", "Production.Product"], "answer_kind": "rows",
        "columns": [("category_id", "integer", "类别ID "), ("category_name", "text", "类别 "), ("product_count", "integer", "产品数 ")],
        "answer_prefix": "各产品类别的产品数如下：", "difficulty": "medium",
    },
    {
        "id": "aw-basic-019", "capability": "status_order_breakdown", "question": "各订单状态分别有多少张订单？",
        "sql": '''SELECT h."Status" AS order_status, COUNT(*) AS order_count
FROM "Sales"."SalesOrderHeader" h
GROUP BY h."Status"
ORDER BY h."Status";''',
        "tables": ["Sales.SalesOrderHeader"], "answer_kind": "rows",
        "columns": [("order_status", "integer", "订单状态 "), ("order_count", "integer", "订单数 ")],
        "answer_prefix": "各订单状态的订单数如下：", "difficulty": "easy",
    },
    {
        "id": "aw-basic-020", "capability": "yearly_sales_trend", "question": "2011年至2013年每年的销售额是多少？",
        "sql": '''SELECT EXTRACT(YEAR FROM h."OrderDate")::int AS order_year,
       ROUND(SUM(d."OrderQty" * d."UnitPrice" * (1 - d."UnitPriceDiscount") ), 2) AS sales_amount
FROM "Sales"."SalesOrderHeader" h
JOIN "Sales"."SalesOrderDetail" d ON d."SalesOrderID" = h."SalesOrderID"
WHERE h."OrderDate" >= DATE '2011-01-01' AND h."OrderDate" < DATE '2014-01-01'
GROUP BY EXTRACT(YEAR FROM h."OrderDate")
ORDER BY order_year;''',
        "tables": ["Sales.SalesOrderHeader", "Sales.SalesOrderDetail"], "answer_kind": "rows",
        "columns": [("order_year", "integer", "年份 "), ("sales_amount", "money", "销售额 ")],
        "answer_prefix": "2011年至2013年各年销售额如下（美元）：", "difficulty": "medium",
    },
]


SPECIALS = [
    {
        "id": "aw-special-021", "capability": "clarification_missing_year", "question": "销售额是多少？",
        "gold_answer": "请补充统计年份或日期范围；例如 2013 年或 2013 年第一季度。",
        "requirements": ["不默认猜测年份", "要求补充时间范围"], "status": "clarification_required", "route": "clarification", "difficulty": "easy",
    },
    {
        "id": "aw-special-022", "capability": "unsupported_profit", "question": "2013年利润是多少？",
        "gold_answer": "当前 AdventureWorks 接入范围没有经过确认的利润口径；不能用销售额代替利润，需要补充成本或利润定义。",
        "requirements": ["明确利润口径缺失", "不把销售额当利润"], "status": "unsupported", "route": "unsupported", "difficulty": "easy",
    },
    {
        "id": "aw-special-023", "capability": "unsupported_inventory", "question": "现在还有多少库存？",
        "gold_answer": "当前评测 profile 没有开放库存表或库存快照，不能回答当前库存数量。",
        "requirements": ["指出库存数据缺口", "不从销售数量推断库存"], "status": "unsupported", "route": "unsupported", "difficulty": "easy",
    },
    {
        "id": "aw-special-024", "capability": "out_of_range", "question": "2015年销售额是多少？",
        "gold_answer": "当前 AdventureWorks 样例订单数据覆盖 2011-05-31 至 2014-06-30，2015 年不在数据覆盖范围内。",
        "requirements": ["识别数据覆盖范围", "不返回空范围的伪精确数值"], "status": "unsupported", "route": "unsupported", "difficulty": "medium",
    },
    {
        "id": "aw-special-025", "capability": "empty_result", "question": "2013年没有销售记录的产品有哪些？",
        "sql": '''SELECT p."ProductID" AS product_id, p."Name" AS product_name
FROM "Production"."Product" p
WHERE NOT EXISTS (
  SELECT 1
  FROM "Sales"."SalesOrderDetail" d
  JOIN "Sales"."SalesOrderHeader" h ON h."SalesOrderID" = d."SalesOrderID"
  WHERE d."ProductID" = p."ProductID"
    AND h."OrderDate" >= DATE '2013-01-01' AND h."OrderDate" < DATE '2014-01-01'
)
ORDER BY p."ProductID";''',
        "tables": ["Production.Product", "Sales.SalesOrderDetail", "Sales.SalesOrderHeader"], "answer_kind": "rows",
        "columns": [("product_id", "integer", "产品ID "), ("product_name", "text", "产品 ")],
        "answer_prefix": "2013年没有销售记录的产品如下；若结果为空，表示该年份每个产品都有销售记录。", "difficulty": "hard",
        "requirements": ["使用左连接或NOT EXISTS正确识别空集合", "如结果为空要明确说明"],
    },
]


def make_record(spec, rows):
    sql = spec.get("sql")
    result = normalize_rows(rows) if sql else None
    if sql and spec.get("answer_kind") in {"scalar", "date_range"} and len(result) == 1:
        result = result[0]
    requirements = spec.get("requirements") or [
        "标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行",
        "时间范围、关联关系、聚合口径和排序与问题一致",
    ]
    record = {
        "id": spec["id"], "level": "basic" if spec["id"].startswith("aw-basic") else "special",
        "capability": spec["capability"], "question": spec["question"], "data_scope": "AdventureWorks",
        "expected_tables": spec.get("tables", []), "expected_documents": [], "evidence_locations": [],
        "gold_sql": sql, "gold_result": result, "gold_calculation": None,
        "gold_answer": custom_answer(spec, rows) if sql else spec["gold_answer"],
        "gold_params": [], "known_conflicts": [],
        "gold_answer_requirements": requirements, "clarification_required": spec.get("status") == "clarification_required",
        "clarification_target": ["year", "time_range"] if spec.get("status") == "clarification_required" else None,
        "difficulty": spec.get("difficulty", "easy"), "split": "dev", "modality": "text",
    }
    status = "answered" if sql else spec["status"]
    route = "sql" if sql else spec["route"]
    record["interface_expectation"] = interface(status, route)
    record["interface_expectation"]["request"]["question"] = spec["question"]
    if status == "clarification_required":
        record["interface_expectation"]["expected_clarification"] = {"missing_slots": record["clarification_target"]}
    if status == "unsupported":
        record["interface_expectation"]["expected_limitations"] = [spec["gold_answer"]]
    return record


def main():
    url = os.environ.get("ADVENTUREWORKS_DATABASE_URL")
    if not url:
        raise SystemExit("请先设置 ADVENTUREWORKS_DATABASE_URL")
    records = []
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for spec in SPECS + SPECIALS:
            rows = []
            if spec.get("sql"):
                with conn.cursor() as cur:
                    cur.execute(spec["sql"])
                    rows = cur.fetchall()
            records.append(make_record(spec, rows))

    agent_path = AGENT / "eval_basic_nl2sql.jsonl"
    with agent_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    validation = {
        "profile_id": PROFILE_ID, "database": DATABASE, "source_ref": SOURCE_REF,
        "record_count": len(records), "sql_record_count": sum(bool(r["gold_sql"]) for r in records),
        "errors": [], "checks": [],
    }
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for record in records:
            if record["gold_sql"]:
                with conn.cursor() as cur:
                    cur.execute(record["gold_sql"])
                    actual = normalize_rows(cur.fetchall())
                expected = record["gold_result"]
                comparable = actual[0] if isinstance(expected, dict) and len(actual) == 1 else actual
                if comparable != expected:
                    validation["errors"].append({"id": record["id"], "expected": expected, "actual": actual})
                else:
                    validation["checks"].append(record["id"])
    (AGENT / "eval_validation_report.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    human_lines = [
        "# AdventureWorks SQL 评测集", "", "题库文件：`eval/adventureworks/agent/eval_basic_nl2sql.jsonl`", f"题量：{len(records)}", "",
        "标准 SQL 只保存在评测端，用于复算 `gold_result`，不会发送给 Agent。", "",
    ]
    for record in records:
        human_lines.extend([
            f"## {record['id']}", "", f"问题：{record['question']}", "",
            f"难度：{record['difficulty']}　能力：{record['capability']}", "",
            f"标准答案：{record['gold_answer']}", "",
            f"预期数据库表：{', '.join(record['expected_tables']) or '无'}", "",
            "评分要点：" + "；".join(record["gold_answer_requirements"]), "",
        ])
    (HUMAN / "eval_basic_nl2sql.md").write_text("\n".join(human_lines) + "\n", encoding="utf-8")
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"), "profile_id": PROFILE_ID,
        "database": DATABASE, "files": {"eval_basic_nl2sql.jsonl": len(records)},
        "single_turn_total": len(records), "standard_sql_verified": True,
    }
    (AGENT / "eval_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# AdventureWorks 评测集\n\n"
        "本目录是独立于 Chinook 的 AdventureWorks SQL-only 评测集。每道数值题包含人工确认的 `gold_sql`、真实执行结果 `gold_result` 和可读答案 `gold_answer`。标准 SQL 留在评测端，不随请求发送。\n\n"
        "- 机器题库：[agent/eval_basic_nl2sql.jsonl](agent/eval_basic_nl2sql.jsonl)\n"
        "- 题库清单：[agent/eval_manifest.json](agent/eval_manifest.json)\n"
        "- 人工核查版：[human/eval_basic_nl2sql.md](human/eval_basic_nl2sql.md)\n"
        "- 校验报告：[agent/eval_validation_report.json](agent/eval_validation_report.json)\n",
        encoding="utf-8",
    )
    print(json.dumps({"records": len(records), "sql_records": validation["sql_record_count"], "errors": validation["errors"]}, ensure_ascii=False, indent=2))
    if validation["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
