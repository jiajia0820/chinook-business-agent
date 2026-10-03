from __future__ import annotations

import json
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "chinook" / "Chinook.db"
EVAL_ROOT = ROOT / "eval"
EVAL = EVAL_ROOT / "agent"
HUMAN = EVAL_ROOT / "human"
EVAL.mkdir(parents=True, exist_ok=True)
HUMAN.mkdir(parents=True, exist_ok=True)

AUDIO = "(1,2,4,5)"
PROFILE_ID = "chinook-music"
Q2_START, Q2_END = "2025-04-01", "2025-07-01"
Q3_START, Q3_END = "2025-07-01", "2025-10-01"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row


def run(sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    clean = []
    for row in rows:
        clean.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()})
    return clean


def scalar(sql: str, params: tuple = ()):
    rows = run(sql, params)
    return next(iter(rows[0].values())) if rows else None


def source(doc: str, location: str, page: int | None = None, region: str | None = None):
    item = {"document_id": doc, "location": location}
    if page is not None:
        item["page"] = page
    if region is not None:
        item["image_region"] = region
    return item


def item(id_, level, capability, question, *, data_scope="music", tables=None, docs=None,
         evidence=None, gold_sql=None, gold_result=None, gold_calculation=None,
         gold_answer="", requirements=None, clarify=False, clarify_target=None,
         difficulty="easy", split="dev", modality="text", known_conflicts=None):
    route = "clarification" if clarify else ("unsupported" if capability.startswith("unsupported") else ("cross_source" if tables and docs else ("sql" if tables or gold_sql else "rag")))
    status = "clarification_required" if clarify else ("unsupported" if capability.startswith("unsupported") else "answered")
    mode = "hybrid" if route == "cross_source" else ("sql_only" if route == "sql" else ("rag_only" if route == "rag" else "hybrid"))
    interface_expectation = {
        "profile_id": PROFILE_ID,
        "mode": mode,
        "request": {
            "question": question,
            "profile_id": PROFILE_ID,
            "user_role": "operator",
            "options": {"show_trace": True, "max_rows": 50, "top_k": 5},
        },
        "expected_status": status,
        "expected_route": route,
        "required_response_fields": ["request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities", "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations", "clarification", "trace", "error"],
        "required_nonempty_fields": (["answer"] if status == "answered" else (["clarification"] if status == "clarification_required" else ["limitations"])),
    }
    if status == "clarification_required":
        interface_expectation["expected_clarification"] = {"question": gold_answer, "missing_slots": clarify_target or []}
    if status == "unsupported":
        interface_expectation["expected_limitations"] = [gold_answer]
    return {
        "id": id_, "level": level, "capability": capability, "question": question,
        "data_scope": data_scope, "expected_tables": tables or [],
        "expected_documents": docs or [], "evidence_locations": evidence or [],
        "gold_sql": gold_sql, "gold_result": gold_result,
        "gold_calculation": gold_calculation, "gold_answer": gold_answer,
        "gold_params": [Q3_START, Q3_END] if gold_sql and gold_sql.count("?") == 2 else [],
        "known_conflicts": known_conflicts or [],
        "gold_answer_requirements": requirements or [],
        "clarification_required": clarify, "clarification_target": clarify_target,
        "difficulty": difficulty, "split": split, "modality": modality,
        "interface_expectation": interface_expectation,
    }


basic_sql = []
basic_sql.append(item(
    "basic-nl2sql-001", "basic", "single_period_total", "2025年第三季度音频销售额、销量、订单数和购买客户数分别是多少？",
    tables=["Invoice", "InvoiceLine", "Track"], gold_sql=f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
SUM(il.Quantity) AS units_sold, COUNT(DISTINCT i.InvoiceId) AS order_count,
COUNT(DISTINCT i.CustomerId) AS customer_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate >= '{Q3_START}' AND i.InvoiceDate < '{Q3_END}';""",
    gold_result=run(f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) sales_amount,SUM(il.Quantity) units_sold,
COUNT(DISTINCT i.InvoiceId) order_count,COUNT(DISTINCT i.CustomerId) customer_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate >= ? AND i.InvoiceDate < ?""", (Q3_START,Q3_END))[0],
    gold_answer="2025年第三季度音频销售额为112.86美元，销量114件，订单21张，购买客户19位。",
    requirements=["四个指标均正确", "说明统计范围为2025Q3音频"], difficulty="easy"))

basic_sql.append(item(
    "basic-nl2sql-002", "basic", "single_period_total", "2025年第二季度音频销售额是多少？",
    tables=["Invoice", "InvoiceLine", "Track"], gold_sql=f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate >= '{Q2_START}' AND i.InvoiceDate < '{Q2_END}';""",
    gold_result={"sales_amount": scalar(f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<?""", (Q2_START,Q2_END))},
    gold_answer="2025年第二季度音频销售额为108.90美元。", requirements=["结果为108.90美元", "说明是音频范围"], difficulty="easy"))

basic_sql.append(item(
    "basic-nl2sql-003", "basic", "genre_sales", "2025年第三季度 Rock 的音频销售额是多少？",
    tables=["Invoice", "InvoiceLine", "Track", "Genre"], gold_sql=f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
JOIN Genre g ON g.GenreId=t.GenreId
WHERE t.MediaTypeId IN {AUDIO} AND g.GenreId=1 AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}';""",
    gold_result={"sales_amount": scalar(f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND t.GenreId=1 AND i.InvoiceDate>=? AND i.InvoiceDate<?""", (Q3_START,Q3_END))},
    gold_answer="2025年第三季度 Rock（Genre:1）音频销售额为47.52美元。", requirements=["Genre:1", "不并入Rock And Roll"], difficulty="easy"))

basic_sql.append(item(
    "basic-nl2sql-004", "basic", "genre_units", "2025年第三季度 Metal 卖了多少件？",
    tables=["Invoice", "InvoiceLine", "Track", "Genre"], gold_sql=f"""SELECT SUM(il.Quantity) AS units_sold
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND t.GenreId=3 AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}';""",
    gold_result={"units_sold": scalar(f"""SELECT SUM(il.Quantity) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND t.GenreId=3 AND i.InvoiceDate>=? AND i.InvoiceDate<?""", (Q3_START,Q3_END))},
    gold_answer="2025年第三季度 Metal（Genre:3）销量为22件。", requirements=["销量按Quantity求和", "Metal不含Heavy Metal"], difficulty="easy"))

basic_sql.append(item(
    "basic-nl2sql-005", "basic", "order_count", "2025年第三季度有多少张音频订单？",
    tables=["Invoice", "InvoiceLine", "Track"], gold_sql=f"""SELECT COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}';""",
    gold_result={"order_count": scalar(f"""SELECT COUNT(DISTINCT i.InvoiceId) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<?""", (Q3_START,Q3_END))},
    gold_answer="2025年第三季度有21张音频订单。", requirements=["InvoiceId去重", "不把销量当订单数"], difficulty="easy"))

basic_sql.append(item(
    "basic-nl2sql-006", "basic", "customer_count", "2025年第三季度有多少位购买音频的客户？",
    tables=["Invoice", "InvoiceLine", "Track"], gold_sql=f"""SELECT COUNT(DISTINCT i.CustomerId) AS customer_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}';""",
    gold_result={"customer_count": scalar(f"""SELECT COUNT(DISTINCT i.CustomerId) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<?""", (Q3_START,Q3_END))},
    gold_answer="2025年第三季度有19位购买音频的客户。", requirements=["CustomerId去重"], difficulty="easy"))

genre_top_sql = f"""SELECT g.Name AS genre, ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN Genre g ON g.GenreId=t.GenreId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}'
GROUP BY g.GenreId,g.Name ORDER BY sales_amount DESC LIMIT 3;"""
basic_sql.append(item("basic-nl2sql-007", "basic", "genre_ranking", "2025年第三季度音频销售额最高的三个音乐分类是什么？", tables=["Invoice","InvoiceLine","Track","Genre"], gold_sql=genre_top_sql, gold_result=run(genre_top_sql), gold_answer="前三名为 Rock（47.52美元）、Metal（21.78美元）和 Latin（15.84美元）。", requirements=["按销售额降序", "列出金额"], difficulty="medium"))

track_top_sql = f"""SELECT t.TrackId,t.Name,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}'
GROUP BY t.TrackId,t.Name ORDER BY sales_amount DESC,t.TrackId LIMIT 5;"""
basic_sql.append(item("basic-nl2sql-008", "basic", "track_ranking", "2025年第三季度销售额最高的五个音频商品及金额是什么？", tables=["Invoice","InvoiceLine","Track"], gold_sql=track_top_sql, gold_result=run(track_top_sql), gold_answer="返回按TrackId区分的前五个商品及其销售额，排序为销售额降序、TrackId升序。", requirements=["保留TrackId", "金额排序正确"], difficulty="medium"))

media_sql = f"""SELECT mt.MediaTypeId,mt.Name,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,SUM(il.Quantity) AS units_sold
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN MediaType mt ON mt.MediaTypeId=t.MediaTypeId
WHERE i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}' GROUP BY mt.MediaTypeId,mt.Name ORDER BY mt.MediaTypeId;"""
basic_sql.append(item("basic-nl2sql-009", "basic", "media_breakdown", "2025年第三季度各媒体类型的销售额和销量是多少？", data_scope="all_media", tables=["Invoice","InvoiceLine","Track","MediaType"], gold_sql=media_sql, gold_result=run(media_sql), gold_answer="按 MediaTypeId 分列销售额和销量；音频类型与视频类型分别展示。", requirements=["不把视频混入音频", "保留MediaTypeId"], difficulty="medium"))

country_sql = f"""SELECT i.BillingCountry AS billing_country,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}'
GROUP BY i.BillingCountry ORDER BY sales_amount DESC,i.BillingCountry LIMIT 5;"""
basic_sql.append(item("basic-nl2sql-010", "basic", "billing_country_ranking", "2025年第三季度音频销售额最高的五个账单国家是什么？", tables=["Invoice","InvoiceLine","Track"], gold_sql=country_sql, gold_result=run(country_sql), gold_answer="返回按 Invoice.BillingCountry 汇总的前五个国家及销售额，不使用Customer所在地替代账单国家。", requirements=["使用账单国家", "按销售额降序"], difficulty="medium"))

basic_rag = [
    item("basic-rag-001","basic","metric_definition","销售额、销量、订单数和购买客户数分别怎么计算？",docs=["D01"],evidence=[source("D01","销售额/销量/订单数/购买客户数",1)],gold_answer="销售额是InvoiceLine.UnitPrice乘Quantity的合计；销量是Quantity合计；订单数按InvoiceId去重；购买客户数按CustomerId去重。",requirements=["四项定义准确"],difficulty="easy"),
    item("basic-rag-002","basic","time_scope","2025年第三季度的订单日期范围怎么写？",docs=["D01"],evidence=[source("D01","时间区间",1)],gold_answer="使用左闭右开区间：2025-07-01 <= InvoiceDate < 2025-10-01。",requirements=["起止日期正确", "说明左闭右开"],difficulty="easy"),
    item("basic-rag-003","basic","genre_boundary","Rock和Rock And Roll在统计上是一个分类吗？",docs=["D03"],evidence=[source("D03","Rock的消歧")],gold_answer="不是。Rock是Genre:1，Rock And Roll是Genre:5，默认分开统计。",requirements=["两个GenreId正确"],difficulty="easy"),
    item("basic-rag-004","basic","media_scope","默认音乐销售额包括哪些MediaType？",docs=["D03","D01"],evidence=[source("D03","MediaType和商品范围"),source("D01","音乐范围",1)],gold_answer="默认音频范围是MediaTypeId 1、2、4、5；MediaTypeId 3是视频，单独统计。",requirements=["列出四个音频MediaTypeId", "排除视频"],difficulty="easy"),
    item("basic-rag-005","basic","tier_rule","客户分层的A、B、C层金额阈值分别是什么？",docs=["D04"],evidence=[source("D04","活跃客户层级")],gold_answer="A层为当季音频销售额大于等于12.00美元；B层为5.00至12.00美元以下；C层为0至5.00美元以下。",requirements=["三个阈值准确"],difficulty="easy"),
    item("basic-rag-006","basic","selection_priority","选品时A、B、C、D四类候选分别怎么处理？",docs=["D05"],evidence=[source("D05","候选筛选顺序",1)],gold_answer="A类有成交且分类、媒体明确，可进主位；B类为已成交专辑的其他商品，作组合延伸；C类为未成交但符合主题的候选，小范围观察；D类信息待核对，暂不进正式页面。",requirements=["四类处理准确"],difficulty="easy"),
    item("basic-rag-007","basic","target_lookup","2025年第三季度Rock、Metal、Latin和全音频目标是多少？",docs=["D06"],evidence=[source("D06","附件1 目标明细",1)],gold_answer="Rock销售额50.00美元、销量50件；Metal 24.00美元、25件；Latin 18.00美元、18件；全音频120.00美元、125件。",requirements=["四组目标完整"],difficulty="easy",modality="pdf"),
    item("basic-rag-008","basic","quarter_review","二季度音频经营的核心结果是什么？",docs=["D07"],evidence=[source("D07","一 经营摘要",1)],gold_answer="二季度音频销售额108.90美元、销量110件、19张订单、18位购买客户；比一季度销售额增加5.94美元，销量增加6件，订单数和客户数未增加。",requirements=["核心数字准确", "说明变化"],difficulty="easy"),
    item("basic-rag-009","basic","quarter_review","三季度全音频目标完成了吗？",docs=["D08"],evidence=[source("D08","一 经营摘要",1)],gold_answer="没有。销售额实际112.86美元，目标120.00美元，达成率94.05%；销量实际114件，目标125件，达成率91.20%。",requirements=["两个指标分别判断"],difficulty="easy"),
    item("basic-rag-010","basic","poster_lookup","活动报名截止时间和需要提交的材料是什么？",docs=["D11"],evidence=[source("D11","报名对象与截止信息",region="chips"),source("D11","提交材料",region="materials")],gold_answer="报名截止时间为2025年10月13日18:00；提交商品清单、选品理由和一段页面文案，提交至商品内容组或品类运营组。",requirements=["时间准确", "材料完整"],difficulty="easy",modality="image"),
]

clarify = [
    item("special-clarify-001","special","missing_period","销售增长率是多少？",docs=["D01","D02"],evidence=[source("D01","环比",1)],gold_answer="请先说明比较哪两个完整周期，以及统计全部音频还是某个品类。",requirements=["不猜比较期", "要求指标范围"],clarify=True,clarify_target=["comparison_period","data_scope"],difficulty="easy"),
    item("special-clarify-002","special","missing_metric","第三季度Rock卖得怎么样？",docs=["D02"],evidence=[source("D02","分析需求的接收和确认")],gold_answer="请明确想看销售额、销量、订单数、购买客户数，还是目标达成情况。",requirements=["澄清指标"],clarify=True,clarify_target=["metric"],difficulty="easy"),
    item("special-clarify-003","special","missing_year","第三季度销售额是多少？",docs=["D01"],evidence=[source("D01","时间区间",1)],gold_answer="请确认年份；如果按当前经营资料，应指定2025年第三季度。",requirements=["澄清年份"],clarify=True,clarify_target=["year"],difficulty="easy"),
    item("special-clarify-004","special","ambiguous_media","音乐和视频销售额要分开看还是合计？",docs=["D03"],evidence=[source("D03","MediaType和商品范围")],gold_answer="请确认需要分开列示音频与视频，还是统计全店合计。",requirements=["澄清合计方式"],clarify=True,clarify_target=["media_scope"],difficulty="easy"),
    item("special-clarify-005","special","ambiguous_type","某商品属于什么类型？",docs=["D03"],evidence=[source("D03","商品、专辑和艺术家范围")],gold_answer="请明确想查Genre、MediaType、所属Album，还是所在Playlist。",requirements=["澄清类型维度"],clarify=True,clarify_target=["entity_dimension"],difficulty="easy"),
    item("special-clarify-006","special","tier_period","哪些客户是高价值客户？",docs=["D04"],evidence=[source("D04","分层计算口径")],gold_answer="请指定观察周期，或确认按2025年第三季度客户分层规则判断。",requirements=["澄清周期"],clarify=True,clarify_target=["observation_period"],difficulty="medium"),
    item("special-clarify-007","special","target_scope","销售额达到目标了吗？",docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1)],gold_answer="请明确季度、品类或全音频范围；如果是第三季度，还需确认看销售额目标还是销售额和销量两个目标。",requirements=["澄清周期、范围、目标指标"],clarify=True,clarify_target=["period","scope","target_metric"],difficulty="medium"),
    item("special-clarify-008","special","genre_scope","摇滚相关销售额是多少？",docs=["D03"],evidence=[source("D03","Rock的消歧")],gold_answer="请确认是否把Rock And Roll（Genre:5）并入Rock（Genre:1）。默认Rock和Rock And Roll分开统计。",requirements=["澄清分类边界"],clarify=True,clarify_target=["genre_scope"],difficulty="medium"),
]

alias = [
    item("special-alias-001","special","alias_normalization","问题里的“摇滚”默认对应哪个Genre？",docs=["D03"],evidence=[source("D03","Rock的消歧")],gold_answer="默认对应Rock，Genre:1；不包含Rock And Roll（Genre:5）。",requirements=["标准化为Genre:1"],difficulty="easy"),
    item("special-alias-002","special","alias_normalization","“摇滚乐”在分类表里通常对应什么？",docs=["D03"],evidence=[source("D03","Genre标准目录")],gold_answer="Rock And Roll，Genre:5；它与Rock（Genre:1）分开。",requirements=["标准化为Genre:5"],difficulty="medium"),
    item("special-alias-003","special","alias_normalization","“金属类”默认按哪个分类统计？",docs=["D03"],evidence=[source("D03","Metal的消歧")],gold_answer="默认按Metal，Genre:3统计；Heavy Metal是Genre:13，需单独说明。",requirements=["标准化为Genre:3"],difficulty="easy"),
    item("special-alias-004","special","alias_normalization","“重金属”是否应该并入Metal？",docs=["D03"],evidence=[source("D03","Metal的消歧")],gold_answer="不应默认并入。重金属对应Heavy Metal（Genre:13），Metal为Genre:3。",requirements=["区分Genre:3和Genre:13"],difficulty="easy"),
    item("special-alias-005","special","alias_normalization","“音乐商品”默认包含哪些媒体格式？",docs=["D01","D03"],evidence=[source("D01","音乐范围",1),source("D03","MediaType和商品范围")],gold_answer="默认包含MediaTypeId 1、2、4、5，不包含视频MediaTypeId 3。",requirements=["列出音频范围"],difficulty="easy"),
    item("special-alias-006","special","alias_normalization","“全店销售额”和“音乐销售额”有什么范围差异？",docs=["D03"],evidence=[source("D03","MediaType和商品范围")],gold_answer="音乐销售额默认只统计音频；全店销售额可以包含全部MediaType，需明确是否纳入视频。",requirements=["区分全店与音乐"],difficulty="medium"),
    item("special-alias-007","special","typo_tolerance","用户把 Heavy Metal 写成“重金属”，系统应如何归类？",docs=["D03"],evidence=[source("D03","Metal的消歧")],gold_answer="识别为Heavy Metal，Genre:13，不应套用Metal（Genre:3）的目标。",requirements=["容错并保持边界"],difficulty="medium"),
    item("special-alias-008","special","alias_normalization","“拉丁音乐”对应哪个Genre？",docs=["D03"],evidence=[source("D03","Genre标准目录")],gold_answer="Latin，Genre:7。",requirements=["标准化为Genre:7"],difficulty="easy"),
]

multi_sql = []
multi_sql.append(item("special-multitable-001","special","track_album_artist_join","2025年第三季度销售额最高的五个商品，要同时列出专辑和艺术家。",tables=["Invoice","InvoiceLine","Track","Album","Artist"],gold_sql=track_top_sql.replace("SELECT t.TrackId,t.Name,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount", "SELECT t.TrackId,t.Name,al.Title AS album,ar.Name AS artist,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount").replace("JOIN Track t ON t.TrackId=il.TrackId\n", "JOIN Track t ON t.TrackId=il.TrackId JOIN Album al ON al.AlbumId=t.AlbumId JOIN Artist ar ON ar.ArtistId=al.ArtistId\n"), gold_result=run(track_top_sql.replace("SELECT t.TrackId,t.Name,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount", "SELECT t.TrackId,t.Name,al.Title AS album,ar.Name AS artist,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount").replace("JOIN Track t ON t.TrackId=il.TrackId\n", "JOIN Track t ON t.TrackId=il.TrackId JOIN Album al ON al.AlbumId=t.AlbumId JOIN Artist ar ON ar.ArtistId=al.ArtistId\n")), gold_answer="按TrackId汇总并连接Album、Artist，返回商品、专辑、艺术家和销售额。", requirements=["多表连接正确", "保留商品身份字段"], difficulty="medium"))

artist_sql = f"""SELECT ar.Name AS artist,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN Album al ON al.AlbumId=t.AlbumId JOIN Artist ar ON ar.ArtistId=al.ArtistId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY ar.ArtistId,ar.Name ORDER BY sales_amount DESC,ar.ArtistId LIMIT 5;"""
multi_sql.append(item("special-multitable-002","special","artist_sales_join","2025年第三季度音频销售额最高的五位艺术家是谁？",tables=["Invoice","InvoiceLine","Track","Album","Artist"],gold_sql=artist_sql,gold_result=run(artist_sql,(Q3_START,Q3_END)),gold_answer="按Artist连接Album和Track后汇总音频订单明细，返回销售额前五名。",requirements=["Artist连接路径正确", "按销售额排序"],difficulty="medium"))

album_sql = f"""SELECT al.Title AS album,ar.Name AS artist,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN Album al ON al.AlbumId=t.AlbumId JOIN Artist ar ON ar.ArtistId=al.ArtistId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY al.AlbumId,al.Title,ar.Name ORDER BY sales_amount DESC,al.AlbumId LIMIT 5;"""
multi_sql.append(item("special-multitable-003","special","album_sales_join","2025年第三季度音频销售额最高的五张专辑是什么？",tables=["Invoice","InvoiceLine","Track","Album","Artist"],gold_sql=album_sql,gold_result=run(album_sql,(Q3_START,Q3_END)),gold_answer="按Album汇总其下Track的音频订单明细，返回专辑、艺术家和销售额。",requirements=["按AlbumId聚合", "列出艺术家"],difficulty="medium"))

playlist_sql = """SELECT p.PlaylistId,p.Name,COUNT(DISTINCT pt.TrackId) AS track_count FROM Playlist p JOIN PlaylistTrack pt ON pt.PlaylistId=p.PlaylistId GROUP BY p.PlaylistId,p.Name ORDER BY p.PlaylistId;"""
multi_sql.append(item("special-multitable-004","special","playlist_track_join","每个播放列表收录了多少个不同商品？",data_scope="all_media",tables=["Playlist","PlaylistTrack","Track"],gold_sql=playlist_sql,gold_result=run(playlist_sql),gold_answer="通过PlaylistTrack按PlaylistId统计去重后的TrackId数量；这只是收录数量，不是播放量。",requirements=["TrackId去重", "不声称播放量"],difficulty="medium"))

customer_sql = f"""SELECT i.CustomerId,c.FirstName||' '||c.LastName AS customer,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,COUNT(DISTINCT i.InvoiceId) AS order_count
FROM Invoice i JOIN Customer c ON c.CustomerId=i.CustomerId JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY i.CustomerId,c.FirstName,c.LastName ORDER BY sales_amount DESC,i.CustomerId LIMIT 5;"""
multi_sql.append(item("special-multitable-005","special","customer_sales_join","2025年第三季度购买音频金额最高的五位客户是谁？",tables=["Invoice","Customer","InvoiceLine","Track"],gold_sql=customer_sql,gold_result=run(customer_sql,(Q3_START,Q3_END)),gold_answer="按CustomerId汇总第三季度音频明细，返回客户编号、客户姓名、销售额和订单数。",requirements=["Customer与Invoice连接正确", "金额按明细计算"],difficulty="medium"))

support_sql = f"""SELECT e.FirstName||' '||e.LastName AS support_rep,COUNT(DISTINCT c.CustomerId) AS customer_count
FROM Employee e JOIN Customer c ON c.SupportRepId=e.EmployeeId JOIN Invoice i ON i.CustomerId=c.CustomerId JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY e.EmployeeId,e.FirstName,e.LastName ORDER BY support_rep;"""
multi_sql.append(item("special-multitable-006","special","support_rep_customer_join","第三季度购买音频客户按支持负责人分布如何？",tables=["Employee","Customer","Invoice","InvoiceLine","Track"],gold_sql=support_sql,gold_result=run(support_sql,(Q3_START,Q3_END)),gold_answer="按SupportRepId统计其负责且在第三季度购买音频的去重客户数；这表示维护分布，不表示个人销售贡献。",requirements=["客户去重", "不解释为员工销售排名"],difficulty="hard"))

genre_orders_sql = f"""SELECT g.Name AS genre,COUNT(DISTINCT i.InvoiceId) AS order_count,COUNT(DISTINCT i.CustomerId) AS customer_count
FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN Genre g ON g.GenreId=t.GenreId
WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY g.GenreId,g.Name ORDER BY order_count DESC, g.GenreId;"""
multi_sql.append(item("special-multitable-007","special","genre_customer_order_join","第三季度各音乐分类的订单数和购买客户数是多少？",tables=["Invoice","InvoiceLine","Track","Genre"],gold_sql=genre_orders_sql,gold_result=run(genre_orders_sql,(Q3_START,Q3_END)),gold_answer="按Genre汇总，订单数按InvoiceId去重，购买客户数按CustomerId去重；品类数不能直接相加得到总数。",requirements=["两个去重口径正确"],difficulty="hard"))

mixed_sql = """SELECT COUNT(DISTINCT i.InvoiceId) AS mixed_order_count FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE i.InvoiceId IN (SELECT il2.InvoiceId FROM InvoiceLine il2 JOIN Track t2 ON t2.TrackId=il2.TrackId WHERE t2.MediaTypeId IN (1,2,4,5)) AND i.InvoiceId IN (SELECT il3.InvoiceId FROM InvoiceLine il3 JOIN Track t3 ON t3.TrackId=il3.TrackId WHERE t3.MediaTypeId=3);"""
multi_sql.append(item("special-multitable-008","special","mixed_order_join","数据库中同时含音频和视频明细的订单有多少张？",data_scope="all_media",tables=["Invoice","InvoiceLine","Track","MediaType"],gold_sql=mixed_sql,gold_result=run(mixed_sql)[0],gold_answer="有17张订单同时含音频和视频明细。计算音乐销售额时仍需按InvoiceLine筛选。",requirements=["结果为17", "说明混合订单处理"],difficulty="hard"))

formula = [
    item("special-formula-001","special","target_gap_formula","第三季度全音频销售额距离目标差多少，达成率是多少？",docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1),source("D08","一 经营摘要",1)],gold_calculation={"actual":112.86,"target":120.00,"gap":"112.86-120.00=-7.14","rate":"112.86/120.00=94.05%"},gold_answer="实际销售额112.86美元，目标120.00美元，差额-7.14美元，达成率94.05%，未达标。",requirements=["展示输入、公式和结果"],difficulty="medium"),
    item("special-formula-002","special","target_gap_formula","第三季度Rock销售额距离目标差多少，达成率是多少？",docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1),source("D08","三 目标达成情况",2)],gold_calculation={"actual":47.52,"target":50.00,"gap":"-2.48","rate":"95.04%"},gold_answer="Rock实际47.52美元，目标50.00美元，差额-2.48美元，达成率95.04%，未达标。",requirements=["金额差额正确", "百分比正确"],difficulty="medium"),
    item("special-formula-003","special","growth_formula","第三季度音频销售额比第二季度增长多少？",docs=["D07","D08"],evidence=[source("D07","一 经营摘要",1),source("D08","一 经营摘要",1)],gold_calculation={"current":112.86,"previous":108.90,"increase":"3.96","rate":"3.64%"},gold_answer="增长3.96美元，增长率约为3.64%（3.96÷108.90）。",requirements=["增长额和增长率都给出"],difficulty="medium"),
    item("special-formula-004","special","target_gap_formula","第三季度Rock销量完成了多少？",docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1),source("D08","三 目标达成情况",2)],gold_calculation={"actual":48,"target":50,"gap":"-2件","rate":"96.00%"},gold_answer="Rock销量48件，目标50件，差额-2件，达成率96.00%，未达标。",requirements=["独立判断销量目标"],difficulty="medium"),
    item("special-formula-005","special","aov_formula","二季度音频客单价是多少？",docs=["D01","D07"],evidence=[source("D01","客单价",1),source("D07","关键数字",1)],gold_calculation={"sales":108.90,"orders":19,"aov":"108.90/19=5.73"},gold_answer="二季度音频客单价为5.73美元/单（108.90÷19）。",requirements=["使用订单数作分母"],difficulty="easy"),
    item("special-formula-006","special","monthly_plan_comparison","第三季度全音频月度销售额计划合计是多少？",docs=["D06"],evidence=[source("D06","附件2 月度检查安排",2)],gold_calculation={"monthly_plan":[38.00,40.00,42.00],"sum":"38+40+42=120.00"},gold_answer="7月38.00美元、8月40.00美元、9月42.00美元，合计120.00美元。",requirements=["保留月度拆分"],difficulty="easy"),
]

multimodal = [
    item("special-multimodal-001","special","poster_ocr","海报上的活动周期和报名截止时间是什么？",docs=["D11"],evidence=[source("D11","活动主题和报名时间",region="hero")],gold_answer="活动周期为2025年10月20日至11月2日，报名截止为10月13日18:00。",requirements=["读取图片文字", "给出图片区域"],difficulty="easy",modality="image"),
    item("special-multimodal-002","special","poster_ocr","海报写的报名对象和提交材料是什么？",docs=["D11"],evidence=[source("D11","报名对象与截止信息",region="chips"),source("D11","提交材料",region="materials")],gold_answer="商品内容/品类运营可以报名；提交商品清单、选品理由和一段页面文案。",requirements=["对象和材料完整", "给出图片区域"],difficulty="easy",modality="image"),
    item("special-multimodal-003","special","pdf_table_reading","目标表中Metal的销售额目标和销量目标是多少？",docs=["D06"],evidence=[source("D06","附件1 目标明细",1)],gold_answer="Metal（Genre:3）销售额目标24.00美元，销量目标25件。",requirements=["正确读取PDF表格"],difficulty="easy",modality="pdf"),
    item("special-multimodal-004","special","pdf_table_reading","目标表中9月全音频销售额和销量计划是多少？",docs=["D06"],evidence=[source("D06","附件2 月度检查安排",2)],gold_answer="9月全音频销售额计划42.00美元，销量计划44件。",requirements=["正确读取表格行列"],difficulty="medium",modality="pdf"),
    item("special-multimodal-005","special","poster_ocr","海报主页面和延伸栏目分别是什么？",docs=["D11"],evidence=[source("D11","报名条件",region="eligibility")],gold_answer="Rock是主页面，Metal是延伸栏目；海报还展示经典专辑内容。",requirements=["识别图片卡片信息"],difficulty="medium",modality="image"),
]

negative = [
    item("special-negative-001","special","unsupported_profit","第三季度利润是多少？",docs=["D01","D12"],evidence=[source("D01","当前数据范围",1)],gold_answer="当前资料没有成本或利润数据，不能计算利润；需要补充成本或结算金额。",requirements=["明确数据缺口", "不拿销售额代替利润"],difficulty="easy"),
    item("special-negative-002","special","unsupported_playback","哪个播放列表播放量最高？",docs=["D03","D12"],evidence=[source("D03","Playlist的使用边界"),source("D12","哪些问题目前没有数据")],gold_answer="当前只有Playlist收录关系，没有播放次数或收听日志，不能判断播放量。",requirements=["拒绝播放量推断"],difficulty="easy"),
    item("special-negative-003","special","unsupported_inventory","Rock现在还剩多少库存？",docs=["D12"],evidence=[source("D12","哪些问题目前没有数据")],gold_answer="当前数据没有库存快照，无法回答库存数量。",requirements=["指出库存数据缺失"],difficulty="easy"),
    item("special-negative-004","special","unsupported_campaign","活动页面带来了多少点击和转化？",docs=["D10","D12"],evidence=[source("D10","活动内容"),source("D12","哪些问题目前没有数据")],gold_answer="当前没有活动曝光、点击或转化事件数据，只能统计活动期间的交易变化。",requirements=["区分交易数据和活动事件数据"],difficulty="medium"),
    item("special-negative-005","special","unsupported_employee_sales","能按SupportRep给员工做销售额排名吗？",docs=["D04"],evidence=[source("D04","名单使用")],gold_answer="不能。SupportRep表示客户维护责任，不等同于个人销售归属或提成贡献。",requirements=["说明字段含义边界"],difficulty="easy"),
]

cross = [
    item("cross-001","fusion","actual_vs_target","第三季度Rock销售额达到目标了吗？差多少？",tables=["Invoice","InvoiceLine","Track","Genre"],docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1),source("D08","三 目标达成情况",2)],gold_sql=f"""SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS actual_sales FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND t.GenreId=1 AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}';""",gold_result={"actual_sales":47.52,"target_sales":50.00,"gap":-2.48,"rate":"95.04%"},gold_calculation={"actual":47.52,"target":50.00,"gap":"-2.48","rate":"95.04%"},gold_answer="Rock实际销售额47.52美元，目标50.00美元，差额-2.48美元，达成率95.04%，未达标。",requirements=["数据库实际值正确", "目标取D06", "说明D08结论"],difficulty="medium"),
    item("cross-002","fusion","actual_vs_target","第三季度全音频销售额和销量是否都达标？",tables=["Invoice","InvoiceLine","Track"],docs=["D06","D08"],evidence=[source("D06","附件1 目标明细",1),source("D08","一 经营摘要",1)],gold_result={"sales_actual":112.86,"sales_target":120.00,"sales_rate":"94.05%","units_actual":114,"units_target":125,"units_rate":"91.20%"},gold_answer="两项都未达标：销售额112.86/120.00美元，达成率94.05%；销量114/125件，达成率91.20%。",requirements=["分别判断销售额和销量"],difficulty="medium"),
    item("cross-003","fusion","genre_action","第三季度销售额最高的品类是什么，运营手册建议关注什么？",tables=["Invoice","InvoiceLine","Track","Genre"],docs=["D09","D08"],evidence=[source("D08","三 目标达成情况",2),source("D09","Rock 品类运营")],gold_result={"genre":"Rock","sales_amount":47.52},gold_answer="Rock最高，销售额47.52美元。运营手册建议继续整理成交商品和专辑，关注专题陈列、订单集中和专辑覆盖。",requirements=["实际排名正确", "动作来自D09"],difficulty="medium"),
    item("cross-004","fusion","customer_tiering","第三季度购买Rock的客户，哪些属于A层重点维护？",tables=["Invoice","InvoiceLine","Track","Customer"],docs=["D04"],evidence=[source("D04","三 活跃客户层级定义"),source("D04","七 第三季度分层结果示例")],gold_sql=f"""SELECT i.CustomerId,ROUND(SUM(CASE WHEN t.GenreId=1 THEN il.UnitPrice*il.Quantity ELSE 0 END),2) AS rock_sales FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}' GROUP BY i.CustomerId HAVING rock_sales>0 ORDER BY rock_sales DESC;""",gold_result=run(f"""SELECT i.CustomerId,ROUND(SUM(CASE WHEN t.GenreId=1 THEN il.UnitPrice*il.Quantity ELSE 0 END),2) AS rock_sales FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY i.CustomerId HAVING rock_sales>0 ORDER BY rock_sales DESC""",(Q3_START,Q3_END)),gold_answer="第三季度购买Rock的客户中，按D04当季音频销售额大于等于12.00美元的A层客户是CustomerId 10、31、48。Rock购买事实来自数据库，A层判断使用客户当季全部音频销售额，不是只看Rock金额。",requirements=["列出CustomerId 10、31、48", "客户事实来自数据库", "层级阈值来自D04"],difficulty="hard"),
    item("cross-005","fusion","quarter_compare_action","第三季度比第二季度增长了多少，D02建议如何解读？",tables=["Invoice","InvoiceLine","Track"],docs=["D02","D07","D08"],evidence=[source("D07","一 经营摘要",1),source("D08","一 经营摘要",1),source("D02","增长率和特殊分母")],gold_result={"q2_sales":108.90,"q3_sales":112.86,"increase":3.96,"growth_rate":"3.64%"},gold_answer="销售额增加3.96美元，增长约3.64%。D02强调增长比较和目标达成是两种判断，因此三季度虽有增长，仍需另看120.00美元目标是否完成。",requirements=["跨期计算正确", "引用D02解释"],difficulty="hard"),
    item("cross-006","fusion","poster_product_sales","海报要求的Rock主页面商品中，TrackId 1791-1799在第三季度是否有成交？",tables=["Invoice","InvoiceLine","Track"],docs=["D05","D11"],evidence=[source("D11","报名条件",region="eligibility"),source("D05","四 Rock 商品选品",2)],gold_sql=f"""SELECT t.TrackId,t.Name,ROUND(COALESCE(SUM(CASE WHEN i.InvoiceDate>='{Q3_START}' AND i.InvoiceDate<'{Q3_END}' THEN il.UnitPrice*il.Quantity END),0),2) AS q3_sales FROM Track t LEFT JOIN InvoiceLine il ON il.TrackId=t.TrackId LEFT JOIN Invoice i ON i.InvoiceId=il.InvoiceId WHERE t.TrackId IN (1791,1793,1795,1797,1799) GROUP BY t.TrackId,t.Name ORDER BY t.TrackId;""",gold_result=run(f"""SELECT t.TrackId,t.Name,ROUND(COALESCE(SUM(CASE WHEN i.InvoiceDate>=? AND i.InvoiceDate<? THEN il.UnitPrice*il.Quantity END),0),2) AS q3_sales FROM Track t LEFT JOIN InvoiceLine il ON il.TrackId=t.TrackId LEFT JOIN Invoice i ON i.InvoiceId=il.InvoiceId WHERE t.TrackId IN (1791,1793,1795,1797,1799) GROUP BY t.TrackId,t.Name ORDER BY t.TrackId""",(Q3_START,Q3_END)),gold_answer="五个商品均有成交：TrackId 1791、1793、1795、1797、1799 的第三季度销售额均为0.99美元。候选范围来自D05，主页面条件来自D11。",requirements=["图片条件与数据库商品相接", "列出五个TrackId", "每个销售额为0.99美元"],difficulty="hard",modality="image"),
    item("cross-007","fusion","metal_gap_activity","第三季度Metal未达目标，活动方案把Metal放在什么位置？",tables=["Invoice","InvoiceLine","Track","Genre"],docs=["D06","D08","D10"],evidence=[source("D08","三 目标达成情况",2),source("D10","活动内容")],gold_result={"actual_sales":21.78,"target_sales":24.00,"gap":-2.22},gold_answer="Metal实际销售额21.78美元，目标24.00美元，差额-2.22美元；D10把Metal安排为Rock主题活动的延伸栏目，不作为主页面主品类。",requirements=["目标比较正确", "活动位置准确"],difficulty="medium"),
    item("cross-008","fusion","latin_gap_action","第三季度Latin距离目标多少，D09建议观察什么？",tables=["Invoice","InvoiceLine","Track","Genre"],docs=["D06","D08","D09"],evidence=[source("D08","三 目标达成情况",2),source("D09","Latin")],gold_result={"actual_sales":15.84,"target_sales":18.00,"gap":-2.16,"units":16},gold_answer="Latin实际15.84美元，目标18.00美元，差额-2.16美元，销量16件；D09建议继续观察商品组合和客户覆盖，先核对成交明细。",requirements=["金额和动作均正确"],difficulty="medium"),
    item("cross-009","fusion","monthly_plan_actual","第三季度每月全音频实际销售额与目标计划分别是多少？",tables=["Invoice","InvoiceLine","Track"],docs=["D06","D08"],evidence=[source("D06","附件2 月度检查安排",2),source("D08","二 月度节奏",1)],gold_sql=f"""SELECT substr(i.InvoiceDate,1,7) AS month,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS actual_sales FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY substr(i.InvoiceDate,1,7) ORDER BY month;""",gold_result=run(f"""SELECT substr(i.InvoiceDate,1,7) AS month,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS actual_sales FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE t.MediaTypeId IN {AUDIO} AND i.InvoiceDate>=? AND i.InvoiceDate<? GROUP BY substr(i.InvoiceDate,1,7) ORDER BY month""",(Q3_START,Q3_END)),gold_calculation={"database_monthly_actual":{"2025-07":37.62,"2025-08":37.62,"2025-09":37.62},"document_D08_claim":{"2025-08":36.63},"conflict":"D08的8月数字与数据库不一致；D08数字与季度112.86也不相加"},gold_answer="计划为7月38.00、8月40.00、9月42.00美元；数据库按订单明细算出的实际为7月37.62、8月37.62、9月37.62美元。D08文档写8月36.63美元，这与数据库及112.86美元季度合计冲突，回答时必须标注冲突，不能静默选择一个数字。",requirements=["给出数据库三个月实际值", "给出D08冲突值", "解释112.86与36.63不相加"],difficulty="hard",known_conflicts=["D08二 月度节奏的8月销售额36.63与数据库37.62冲突", "D08月度三项相加不等于季度112.86"]),
    item("cross-010","fusion","classification_selection","D03规定Metal和Heavy Metal分开时，D05的Metal候选应使用哪个Genre？",docs=["D03","D05"],evidence=[source("D03","Metal的消歧"),source("D05","Metal商品选品",2)],gold_answer="D05的Metal候选使用Genre:3；Heavy Metal为Genre:13，不自动并入Metal主列表。",requirements=["两个文档边界一致"],difficulty="medium"),
]

multidoc = [
    item("multidoc-001","fusion","q2_q3_review","结合二季度和三季度复盘，销售额、订单数和购买客户数发生了什么变化？",docs=["D07","D08"],evidence=[source("D07","一 经营摘要",1),source("D08","一 经营摘要",1)],gold_answer="销售额由108.90增至112.86美元，增加3.96美元；订单数由19张增至21张；购买客户数由18位增至19位。",requirements=["三项变化准确"],difficulty="medium"),
    item("multidoc-002","fusion","target_review_activity","D06目标、D08复盘和D10活动方案之间如何衔接？",docs=["D06","D08","D10"],evidence=[source("D06","附件1 目标明细",1),source("D08","三 目标达成情况",2),source("D10","活动内容")],gold_answer="D06给出第三季度目标，D08记录实际达成情况，D10依据差距安排第四季度Rock主页面和Metal延伸栏目。三者的目标、实际和活动安排要分开说明。",requirements=["三份材料职责对应正确"],difficulty="hard"),
    item("multidoc-003","fusion","customer_category_action","第三季度客户分层结果与Rock运营动作如何结合？",docs=["D04","D09"],evidence=[source("D04","活跃客户层级"),source("D09","Rock 品类运营")],gold_answer="先按D04的季度金额阈值确定A/B/C层，再对购买Rock的客户按已有品类安排推荐；D09的Rock动作重点是成交商品、专辑覆盖和专题陈列。",requirements=["层级和动作分别来自正确文档"],difficulty="hard"),
    item("multidoc-004","fusion","scope_selection","D03的分类边界如何影响D05的Rock和Metal选品？",docs=["D03","D05"],evidence=[source("D03","Rock的消歧"),source("D03","Metal的消歧"),source("D05","候选筛选顺序",1)],gold_answer="Rock选品使用Genre:1，Rock And Roll为Genre:5；Metal选品使用Genre:3，Heavy Metal为Genre:13。相近分类要分列，不能静默合并。",requirements=["四个Genre边界清楚"],difficulty="medium"),
    item("multidoc-005","fusion","activity_poster_schedule","活动方案和海报中的活动时间、报名截止和提交材料是否一致？",docs=["D10","D11"],evidence=[source("D10","活动内容"),source("D11","活动主题和报名时间",region="hero"),source("D11","提交材料",region="materials")],gold_answer="活动周期均为2025年10月20日至11月2日；海报报名截止为10月13日18:00；提交商品清单、选品理由和一段页面文案。",requirements=["时间与材料交叉核对", "给出图片区域"],difficulty="medium",modality="image"),
]

multiturn = [
    {"id":"session-001","level":"fusion","capability":"target_followup","turns":[
        {"turn":1,"question":"第三季度Rock销售额是多少？","gold_answer":"47.52美元。","expected_tables":["Invoice","InvoiceLine","Track"],"expected_documents":[]},
        {"turn":2,"question":"和目标比呢？","gold_answer":"目标50.00美元，差额-2.48美元，达成率95.04%，未达标。","expected_documents":["D06","D08"]},
        {"turn":3,"question":"那下一步重点看什么？","gold_answer":"继续检查Rock商品覆盖、订单集中和专辑结构，并结合品类运营动作安排专题陈列。","expected_documents":["D09"]},
    ]},
    {"id":"session-002","level":"fusion","capability":"period_switch","turns":[
        {"turn":1,"question":"二季度音频销售额是多少？","gold_answer":"108.90美元。","expected_documents":["D07"]},
        {"turn":2,"question":"换成三季度呢？","gold_answer":"112.86美元。","expected_documents":["D08"]},
        {"turn":3,"question":"增长率是多少？","gold_answer":"增长3.96美元，约3.64%。","expected_documents":["D07","D08","D01"]},
    ]},
    {"id":"session-003","level":"special","capability":"scope_clarification","turns":[
        {"turn":1,"question":"摇滚销售额是多少？","gold_answer":"请先指定时间范围；默认“摇滚”对应Genre:1，是否包括Rock And Roll也需确认。","expected_documents":["D03"],"clarification_required":True},
        {"turn":2,"question":"按2025年第三季度Rock，不包括Rock And Roll。","gold_answer":"继续查询2025Q3、Genre:1、音频范围，结果为47.52美元。","expected_tables":["Invoice","InvoiceLine","Track","Genre"]},
        {"turn":3,"question":"Rock的目标呢？","gold_answer":"第三季度Rock销售额目标50.00美元，销量目标50件。","expected_documents":["D06"]},
    ]},
    {"id":"session-004","level":"special","capability":"customer_tiering","turns":[
        {"turn":1,"question":"客户怎么分层？","gold_answer":"需要确认观察周期；当前规则按季度音频销售额分A、B、C层，并另列沉睡、长期未购和回流客户。","expected_documents":["D04"],"clarification_required":True},
        {"turn":2,"question":"按2025年第三季度。","gold_answer":"A层大于等于12.00美元，B层5.00至12.00美元以下，C层0至5.00美元以下。","expected_documents":["D04"]},
        {"turn":3,"question":"A层怎么维护？","gold_answer":"每月至少一次，围绕已购品类做定向推荐，并保留联络记录。","expected_documents":["D04"]},
    ]},
    {"id":"session-005","level":"special","capability":"poster_followup","turns":[
        {"turn":1,"question":"活动什么时候报名截止？","gold_answer":"2025年10月13日18:00。","expected_documents":["D11"],"modality":"image"},
        {"turn":2,"question":"报名要交什么？","gold_answer":"商品清单、选品理由和一段页面文案。","expected_documents":["D11"],"modality":"image"},
        {"turn":3,"question":"活动什么时候举行？","gold_answer":"2025年10月20日至11月2日。","expected_documents":["D11","D10"],"modality":"image"},
    ]},
]

for session in multiturn:
    if session["id"] == "session-001":
        session["turns"][1]["expected_tables"] = ["Invoice", "InvoiceLine", "Track"]
    session["interface_expectation"] = {
        "profile_id": PROFILE_ID,
        "mode": "hybrid",
        "session": True,
        "request_contract": "Each turn uses the standard /api/v1/ask request with this session_id."
    }
    for turn in session["turns"]:
        turn_route = "clarification" if turn.get("clarification_required") else ("cross_source" if turn.get("expected_tables") and turn.get("expected_documents") else ("sql" if turn.get("expected_tables") else "rag"))
        turn_status = "clarification_required" if turn.get("clarification_required") else "answered"
        turn["interface_expectation"] = {
            "profile_id": PROFILE_ID,
            "mode": "hybrid" if turn_route == "cross_source" else ("sql_only" if turn_route == "sql" else ("rag_only" if turn_route == "rag" else "hybrid")),
            "request": {"question": turn["question"], "profile_id": PROFILE_ID, "session_id": session["id"], "user_role": "operator", "options": {"show_trace": True, "max_rows": 50, "top_k": 5}},
            "expected_status": turn_status,
            "expected_route": turn_route,
            "required_response_fields": ["request_id", "session_id", "profile_id", "status", "answer", "route", "intent", "entities", "time_range", "sql_results", "documents", "calculations", "metric_definitions", "limitations", "clarification", "trace", "error"],
            "required_nonempty_fields": ["clarification"] if turn_status == "clarification_required" else ["answer"],
        }
        if turn_status == "clarification_required":
            turn["interface_expectation"]["expected_clarification"] = {"question": turn["gold_answer"], "missing_slots": ["required_context"]}

groups = {
    "eval_basic_nl2sql.jsonl": basic_sql,
    "eval_basic_rag.jsonl": basic_rag,
    "eval_special_clarification.jsonl": clarify,
    "eval_special_alias_robustness.jsonl": alias,
    "eval_special_multitable.jsonl": multi_sql,
    "eval_special_formula.jsonl": formula,
    "eval_special_multimodal.jsonl": multimodal,
    "eval_special_negative.jsonl": negative,
    "eval_cross_source.jsonl": cross,
    "eval_multidoc.jsonl": multidoc,
    "eval_multiturn.jsonl": multiturn,
}

LOCATION_REWRITES = {
    "Rock的消歧": "Rock 的消歧",
    "Metal的消歧": "Metal 的消歧",
    "Genre标准目录": "二 Genre 标准目录",
    "MediaType和商品范围": "三 MediaType 和商品范围",
    "Playlist的使用边界": "五 Playlist 的使用边界",
    "Rock商品选品": "四 Rock 商品选品",
    "Metal商品选品": "五 Metal 商品选品",
    "候选筛选顺序": "三 候选筛选顺序",
    "活跃客户层级": "三 活跃客户层级定义",
    "第三季度分层结果示例": "七 第三季度分层结果示例",
    "各层级维护动作": "五 各层级维护动作",
    "名单使用": "八 与支持负责人的关系",
    "D04 客户和支持负责人的关系": "D04 八 与支持负责人的关系",
    "D04 客户触达记录": "D04 八 与支持负责人的关系",
    "D04 常见工作问题": "D04 八 与支持负责人的关系",
    "活动内容": "一 项目背景",
    "D12 哪些问题目前没有数据": "D12 十 当前不支持的查询",
    "哪些问题目前没有数据": "十 当前不支持的查询",
    "D01 当前数据范围": "D12 十 当前不支持的查询",
}

def rewrite_evidence(value):
    if isinstance(value, str):
        for old, new in LOCATION_REWRITES.items():
            value = value.replace(old, new)
        value = value.replace("三 三 活跃客户层级定义定义", "三 活跃客户层级定义")
        value = value.replace("三 三 MediaType 和商品范围", "三 MediaType 和商品范围")
        value = value.replace("四 四 Rock 商品选品", "四 Rock 商品选品")
        value = value.replace("五 五 Metal 商品选品", "五 Metal 商品选品")
        return value
    if isinstance(value, list):
        return [rewrite_evidence(x) for x in value]
    if isinstance(value, dict):
        return {k: rewrite_evidence(v) for k, v in value.items()}
    return value

for records in groups.values():
    for record in records:
        record["evidence_locations"] = rewrite_evidence(record.get("evidence_locations", []))
        if record.get("expected_documents") == ["D01", "D12"] and record.get("capability") == "unsupported_profit":
            record["expected_documents"] = ["D12"]
            record["evidence_locations"] = [source("D12", "十 当前不支持的查询")]
        if "interface_expectation" in record:
            record["interface_expectation"]["source_assertions"] = record["evidence_locations"]

for filename, records in groups.items():
    with (EVAL / filename).open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

manifest = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "database": "data/chinook/Chinook.db",
    "source_directory": "data/knowledge",
    "database_sha256": hashlib.sha256(DB.read_bytes()).hexdigest(),
    "profile_fixture": "eval/agent/interface_profile_fixture.json",
    "files": {name: len(records) for name, records in groups.items()},
    "single_turn_total": sum(len(records) for name, records in groups.items() if name != "eval_multiturn.jsonl"),
    "multiturn_sessions": len(multiturn),
    "official_mapping": {
        "basic_nl2sql": "eval/agent/eval_basic_nl2sql.jsonl",
        "basic_rag": "eval/agent/eval_basic_rag.jsonl",
        "clarification": "eval/agent/eval_special_clarification.jsonl",
        "multitable": "eval/agent/eval_special_multitable.jsonl",
        "formula": "eval/agent/eval_special_formula.jsonl",
        "multimodal": "eval/agent/eval_special_multimodal.jsonl",
        "cross_source": "eval/agent/eval_cross_source.jsonl",
        "robustness_negative": "eval/agent/eval_special_negative.jsonl",
    },
}
(EVAL / "eval_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

profile_fixture = {
    "profile_id": PROFILE_ID,
    "mode": "hybrid",
    "description": "Chinook数字音乐商店经营分析评测配置",
    "database": "data/chinook/Chinook.db",
    "documents": "data/knowledge",
    "capabilities": ["sql", "rag", "cross_source", "clarification", "unsupported"],
    "notes": "评测用profile标识；实际部署时由应用配置映射到同名profile。"
}
(EVAL / "interface_profile_fixture.json").write_text(json.dumps(profile_fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

readme = f"""# 人工核查版评测集说明

这里汇总题库用途、简单用法和来源索引。点击下方题库名称即可阅读问题、参考答案和核查依据。

测试配置名为 `chinook-music`，定义见 [测试配置](../agent/interface_profile_fixture.json)。正式测试前，开发同学需要在应用中接入这个配置。

当前包含 {manifest['single_turn_total']} 道单轮题和 {manifest['multiturn_sessions']} 组多轮会话，机器文件清单见 [eval_manifest.json](../agent/eval_manifest.json)。

## 目录结构

- [agent/](../agent/)：评测程序读取的 JSONL 文件，包含问题、参考答案和评分依据。一行是一道单轮题，或一组多轮会话。
- 当前 `human/`：供人阅读的 Markdown 题库。
- [eval_validation_report.json](eval_validation_report.json)：题库校验记录，不是 Agent 实际答题的成绩。

## 简单使用方法

**人工核查：**打开下方题库，逐题看“问题—标准答案—来源—评分要点”。有疑问时打开对应业务文档；数值题可以进一步到同名 JSONL 文件查看完整的 SQL、参数和结果。

**手动测试 Agent：**复制题目中的自然语言问题到 Agent，保存它的回答，再对照参考答案检查数字、规则和证据。多轮题按顺序在同一会话提问，单轮题使用独立会话。

**程序批量测试：**按 manifest 加载 `agent/` 中的题库，提取 `interface_expectation.request`，发送至 `POST /api/v1/ask`。请求格式见 [接口契约](../../interface-contract.md)。标准答案、标准 SQL、预期文档和评分要点留在评测端，不随请求发送。然后保存实际响应，与题目的参考结果和评分要求比较。当前生成脚本负责准备题库，接口请求和评分需要由评测程序执行。

已知冲突题 `cross-009` 同时列出数据库和 D08 的不同数字，核查时要看回答有没有指出冲突。

## 常用字段

- `expected_tables`：预期使用的 Chinook 表；
- `expected_documents`：预期引用的 D01-D12 文档；
- `evidence_locations`：页码、标题或图片区域；
- `gold_sql` / `gold_result`：可执行 SQL 和实际结果；
- `gold_calculation`：目标差额、达成率、增长率等计算输入和公式；
- `gold_answer`：人工可读的参考答案；
- `gold_answer_requirements`：评分时必须出现的事实点。
- `interface_expectation`：与 `interface-contract.md` 对应的 profile、mode、预期 route/status、请求体和必需响应字段。

生成时间：{manifest['generated_at']}

"""

source_index = """## 来源索引

| 来源 | 主要覆盖题型 |
| --- | --- |
| Chinook.db：Invoice、InvoiceLine、Track、Album、Artist、Genre、MediaType、Customer、Employee、Playlist、PlaylistTrack | 基础 NL2SQL、多表关联、客户/品类/商品排名、媒体范围 |
| D01 经营指标说明 | 指标定义、时间范围、音频范围、公式 |
| D02 销售分析方法说明 | 分析周期、增长率、目标差额、异常解释 |
| D03 音乐分类与商品范围说明 | Genre、MediaType、Rock/Rock And Roll、Metal/Heavy Metal |
| D04 客户分层与运营规则 | A/B/C 阈值、沉睡/回流客户、维护动作 |
| D05 商品推荐与选品规则 | 商品池、候选优先级、Rock/Metal/Latin选品 |
| D06 2025Q3经营目标表 | 品类目标、月度计划、目标口径 |
| D07 2025Q2经营复盘 | 二季度实际表现和历史比较 |
| D08 2025Q3经营复盘 | 三季度实际表现、达成率和四季度承接 |
| D09 重点音乐品类运营手册 | Rock、Metal、Latin品类动作 |
| D10 音乐主题活动方案 | 活动时间、商品候选和执行安排 |
| D11 活动报名与选品条件海报 | 图片文字、报名截止、提交材料和区域证据 |
| D12 经营查询与数据使用FAQ | 数据边界、无证据问题和常见指标解释 |

每道题的 `evidence_locations` 是实际核查入口；数据库题的 `gold_sql` 和 `gold_result` 是可复算入口。
"""

human_names = {
    "eval_basic_nl2sql.jsonl": "基础 NL2SQL",
    "eval_basic_rag.jsonl": "基础文档问答",
    "eval_special_clarification.jsonl": "专项 澄清",
    "eval_special_alias_robustness.jsonl": "专项 别名与分类",
    "eval_special_multitable.jsonl": "专项 多表关联",
    "eval_special_formula.jsonl": "专项 公式计算",
    "eval_special_multimodal.jsonl": "专项 多模态",
    "eval_special_negative.jsonl": "专项 无证据处理",
    "eval_cross_source.jsonl": "跨源问答",
    "eval_multidoc.jsonl": "多文档问答",
    "eval_multiturn.jsonl": "多轮会话",
}

def human_sources(record):
    locations = record.get("evidence_locations", [])
    if not locations:
        return "无文档证据，依据数据库或接口状态判断。"
    parts = []
    for loc in locations:
        if isinstance(loc, dict):
            extra = []
            if loc.get("page") is not None:
                extra.append(f"第{loc['page']}页")
            if loc.get("image_region"):
                extra.append(f"图片区域 {loc['image_region']}")
            suffix = "，" + "，".join(extra) if extra else ""
            parts.append(f"{loc.get('document_id')}：{loc.get('location','')}{suffix}")
        else:
            parts.append(str(loc))
    return "；".join(parts)

purposes = {
    "eval_basic_nl2sql.jsonl": "自然语言查数；核对数值、时间和统计范围",
    "eval_basic_rag.jsonl": "文档问答；核对事实与原文依据",
    "eval_special_clarification.jsonl": "条件不完整；检查是否提出必要的补充问题",
    "eval_special_alias_robustness.jsonl": "业务别名与相近分类；检查名称和分类是否对应正确",
    "eval_special_multitable.jsonl": "多表关联；核对关联关系、去重和汇总结果",
    "eval_special_formula.jsonl": "公式计算；核对输入、公式、单位及结果",
    "eval_special_multimodal.jsonl": "PDF 表格与海报读取；对照表格位置或图片区域",
    "eval_special_negative.jsonl": "数据不足的问题；检查是否说明缺口",
    "eval_cross_source.jsonl": "数据库与业务文档联合分析；分别核对实际值和规则",
    "eval_multidoc.jsonl": "多份文档综合问答；核对各项结论的依据",
    "eval_multiturn.jsonl": "连续对话；在同一会话逐轮检查条件继承和修改",
}
catalog = ["| 题库 | 数量 | 作用和核查重点 |", "| --- | ---: | --- |"]
for filename, records in groups.items():
    title = human_names[filename]
    safe = filename.replace(".jsonl", ".md")
    lines = [f"# {title}", "", f"题库文件：`eval/agent/{filename}`", f"题量：{len(records)}", ""]
    for record in records:
        if filename == "eval_multiturn.jsonl":
            lines.extend([f"## {record['id']}", "", f"会话能力：{record.get('capability','')}", ""])
            for turn in record.get("turns", []):
                lines.extend([f"### 第 {turn['turn']} 轮", "", f"问题：{turn['question']}", "", f"参考答案：{turn.get('gold_answer','')}", ""])
            continue
        lines.extend([
            f"## {record['id']}", "",
            f"问题：{record['question']}", "",
            f"难度：{record.get('difficulty','')}　能力：{record.get('capability','')}", "",
            f"标准答案：{record.get('gold_answer','')}", "",
            f"来源：{human_sources(record)}", "",
            f"预期数据库表：{', '.join(record.get('expected_tables', [])) or '无'}", "",
            "评分要点：" + "；".join(record.get('gold_answer_requirements', [])), "",
        ])
        if record.get("gold_calculation"):
            lines.extend([f"计算记录：{json.dumps(record['gold_calculation'], ensure_ascii=False)}", ""])
    (HUMAN / safe).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    unit = "组" if filename == "eval_multiturn.jsonl" else "题"
    catalog.append(f"| [{title}]({safe}) | {len(records)} {unit} | {purposes[filename]} |")
readme += "\n\n## 题库总览\n\n" + "\n".join(catalog) + "\n\n" + source_index
(HUMAN / "README.md").write_text(readme, encoding="utf-8")

validation = {"files": {}, "errors": [], "sql_checks": 0, "global_duplicate_ids": [], "interface_errors": [], "result_mismatches": [], "known_conflicts": []}
global_ids = set()
for filename, records in groups.items():
    validation["files"][filename] = {"count": len(records), "duplicate_ids": []}
    seen = set()
    for record in records:
        if record["id"] in seen:
            validation["files"][filename]["duplicate_ids"].append(record["id"])
        seen.add(record["id"])
        if record["id"] in global_ids:
            validation["global_duplicate_ids"].append(record["id"])
        global_ids.add(record["id"])
        if record.get("gold_sql"):
            try:
                raw_rows = [dict(row) for row in conn.execute(record["gold_sql"], tuple(record.get("gold_params") or ())).fetchall()]
                validation["sql_checks"] += 1
                expected = record.get("gold_result")
                if isinstance(expected, dict) and len(raw_rows) == 1:
                    expected_subset = {k: v for k, v in expected.items() if k in raw_rows[0]}
                    actual_subset = {k: raw_rows[0].get(k) for k in expected_subset}
                    if actual_subset != expected_subset:
                        validation["result_mismatches"].append({"id": record["id"], "expected": expected_subset, "actual": actual_subset})
                elif isinstance(expected, list) and raw_rows != expected:
                    validation["result_mismatches"].append({"id": record["id"], "expected": expected, "actual": raw_rows})
            except Exception as exc:
                validation["errors"].append({"id": record["id"], "error": str(exc)})
        if filename != "eval_multiturn.jsonl" and not record.get("gold_answer"):
            validation["errors"].append({"id": record["id"], "error": "empty gold_answer"})
        if record.get("gold_sql") and "?" in record["gold_sql"] and not record.get("gold_params"):
            validation["errors"].append({"id": record["id"], "error": "parameterized gold_sql missing gold_params"})
        if record.get("known_conflicts"):
            validation["known_conflicts"].append({"id": record["id"], "conflicts": record["known_conflicts"]})
        ie = record.get("interface_expectation")
        if not ie:
            validation["interface_errors"].append({"id": record["id"], "error": "missing interface_expectation"})
        else:
            if ie.get("profile_id") != PROFILE_ID:
                validation["interface_errors"].append({"id": record["id"], "error": "profile_id mismatch"})
            if filename == "eval_multiturn.jsonl":
                for turn in record.get("turns", []):
                    tie = turn.get("interface_expectation")
                    if not tie or tie.get("profile_id") != PROFILE_ID:
                        validation["interface_errors"].append({"id": record["id"], "error": f"turn {turn.get('turn')} missing interface expectation"})
            elif ie.get("expected_route") not in {"sql", "rag", "cross_source", "clarification", "unsupported"}:
                validation["interface_errors"].append({"id": record["id"], "error": "invalid route"})

(HUMAN / "eval_validation_report.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
conn.close()
