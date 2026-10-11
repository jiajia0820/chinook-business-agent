"""Evaluation-only third revision; never imported by the business runtime."""
from copy import deepcopy
import json


DEFINITIONS = [
    (11, 'easy', 'track_identity_attributes',
     'TrackId 为 1791 的商品叫什么？属于哪个音乐分类、哪种媒体类型，标价是多少美元？',
     ['Track', 'Genre', 'MediaType'], 'music',
     '''SELECT t.TrackId,t.Name,g.Name AS genre,mt.Name AS media_type,t.UnitPrice AS unit_price
FROM Track t JOIN Genre g ON g.GenreId=t.GenreId
JOIN MediaType mt ON mt.MediaTypeId=t.MediaTypeId
WHERE t.TrackId=1791;''',
     ['精确识别TrackId=1791，不把编号当作年份', '列出名称、音乐分类、媒体类型和标价；标价不是成交销售额']),
    (12, 'medium', 'year_month_audio_sales_distinct_orders',
     '按月份列出 2025 年音频销售额和音频订单数，按月份升序排列。音频订单数按订单去重统计。',
     ['Invoice', 'InvoiceLine', 'Track'], 'music',
     '''SELECT strftime('%Y-%m',i.InvoiceDate) AS month,
ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-01-01' AND i.InvoiceDate<'2026-01-01'
AND t.MediaTypeId IN (1,2,4,5)
GROUP BY strftime('%Y-%m',i.InvoiceDate) ORDER BY month ASC;''',
     ['2025全年左闭右开区间，音频MediaTypeId为1、2、4、5', '返回实际查询的12个月，逐月金额和去重订单数正确', '按月份升序，不能只返回第三季度或全年总额']),
    (13, 'medium', 'billing_country_genre_media_multiple_measures',
     '2025 年第三季度，账单国家为美国（USA）的 Rock 音频商品，销售额、销量和订单数分别是多少？订单数按订单去重统计。',
     ['Invoice', 'InvoiceLine', 'Track'], 'music',
     '''SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
SUM(il.Quantity) AS units_sold,COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
AND i.BillingCountry='USA' AND t.GenreId=1 AND t.MediaTypeId IN (1,2,4,5);''',
     ['2025Q3、Invoice.BillingCountry=USA、GenreId=1及音频范围同时生效', '销售额按成交UnitPrice*Quantity、销量按Quantity，订单按InvoiceId去重', '不能用Customer.Country代替账单国家，不并入Rock And Roll']),
    (14, 'medium_hard', 'customer_having_threshold_tie_order',
     '2025 年第三季度，音频消费总额不少于 10 美元的客户有哪些？列出客户编号和消费额，按消费额降序排列，同额按客户编号升序排列。',
     ['Invoice', 'InvoiceLine', 'Track'], 'music',
     '''SELECT i.CustomerId,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
AND t.MediaTypeId IN (1,2,4,5)
GROUP BY i.CustomerId HAVING SUM(il.UnitPrice*il.Quantity)>=10
ORDER BY sales_amount DESC,i.CustomerId ASC;''',
     ['客户粒度聚合全部音频消费；聚合后筛选SUM>=10，包含阈值边界', '客户编号、金额和完整名单正确', '消费额降序，同额客户编号升序，不允许交换并列客户']),
    (15, 'medium_hard', 'all_media_left_join_zero_sales',
     '列出数据库中所有媒体类型在 2025 年第三季度的销售额和销量，没有成交的类型也要列出并显示为 0，按媒体类型编号升序排列。',
     ['MediaType', 'Invoice', 'InvoiceLine', 'Track'], 'all_media',
     '''SELECT mt.MediaTypeId,mt.Name,COALESCE(f.sales_amount,0) AS sales_amount,
COALESCE(f.units_sold,0) AS units_sold
FROM MediaType mt LEFT JOIN (
SELECT t.MediaTypeId,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
SUM(il.Quantity) AS units_sold
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>='2025-07-01' AND i.InvoiceDate<'2025-10-01'
GROUP BY t.MediaTypeId
) f ON f.MediaTypeId=mt.MediaTypeId ORDER BY mt.MediaTypeId ASC;''',
     ['全部媒体类型，包括视频，不加音频限制', '日期约束真正参与聚合的成交明细，不能泄漏其他季度金额', '返回全部5种类型；3种无成交类型的金额及销量为数值0，不能是NULL或遗漏', '按MediaTypeId升序，销售额使用成交单价乘数量']),
]


def extension_cases(template, connection):
    cases = []
    for number, difficulty, capability, question, tables, scope, sql, requirements in DEFINITIONS:
        rows = [dict(row) for row in connection.execute(sql).fetchall()]
        case = deepcopy(template)
        case.update(id=f'basic-nl2sql-{number:03}', level='basic', difficulty=difficulty,
                    capability=capability, question=question, expected_tables=tables,
                    data_scope=scope, gold_sql=sql, gold_params=[],
                    gold_result=rows[0] if number in (11, 13) else rows,
                    gold_calculation=None, expected_documents=[], evidence_locations=[],
                    known_conflicts=[], gold_answer_requirements=requirements,
                    clarification_required=False, clarification_target=None)
        if number == 11:
            r = rows[0]
            answer = f"商品 {r['TrackId']}：{r['Name']}；音乐分类 {r['genre']}；媒体类型 {r['media_type']}；标价 {r['unit_price']:.2f} 美元。"
        elif number == 12:
            answer = '2025年逐月音频销售额与去重订单数：' + '；'.join(
                f"{r['month']}：{r['sales_amount']:.2f} 美元，{r['order_count']} 张订单" for r in rows) + '。按月份升序。'
        elif number == 13:
            r = rows[0]
            answer = f"2025Q3账单国家USA的Rock音频商品：销售额 {r['sales_amount']:.2f} 美元，销量 {r['units_sold']} 件，去重订单 {r['order_count']} 张。"
        elif number == 14:
            answer = '2025Q3音频消费总额不少于10美元的客户：' + '；'.join(
                f"客户 {r['CustomerId']}：{r['sales_amount']:.2f} 美元" for r in rows) + '。消费额降序，同额客户编号升序。'
        else:
            answer = '2025Q3全部媒体类型：' + '；'.join(
                f"{r['MediaTypeId']} {r['Name']}：{r['sales_amount']:.2f} 美元，{r['units_sold']} 件" for r in rows) + '。按媒体类型编号升序，零成交保留。'
        case['gold_answer'] = answer
        interface = case['interface_expectation']
        interface['request']['question'] = question
        interface.update(expected_status='answered', expected_route='sql', mode='sql_only',
                         required_nonempty_fields=['answer', 'sql_results'], source_assertions=[])
        cases.append(case)
    return cases


def render_extension(case):
    return '\n'.join([
        f"## {case['id']}", '', f"问题：{case['question']}", '',
        f"难度：{case['difficulty']}　能力：{case['capability']}", '',
        f"标准答案：{case['gold_answer']}", '',
        '来源：data/chinook/Chinook.db；独立只读标准SQL，未使用模型生成标准答案。', '',
        '预期数据库表：' + ', '.join(case['expected_tables']), '',
        '统计口径及评分要点：' + '；'.join(case['gold_answer_requirements']), '',
        '```sql', case['gold_sql'], '```', '',
        '标准结果：', '', '```json', json.dumps(case['gold_result'], ensure_ascii=False, indent=2), '```', '',
    ])
