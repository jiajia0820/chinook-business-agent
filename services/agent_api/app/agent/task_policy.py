"""Profile-scoped semantic policy. No eval fixtures or SQL execution here."""

from datetime import date, timedelta
import re
from functools import lru_cache
import json
from pathlib import Path

from .state import BusinessTask, ParsedTurn


POLICY_VERSION = "c-clarification-v3-20261011"

SLOT_SPECS = {
    "period": ("完整观察期间，例如2025Q3、2025年全年或明确起止日期", []),
    "year": ("目标/查询年度，请回复四位年份（例如2025年），不会自动补年度", ["2025年", "2024年"]),
    "quarter": ("完整季度或明确起止时间", ["第一季度", "第二季度", "第三季度", "第四季度"]),
    "metric": ("要查询的指标或回答目标", ["销售额", "销量", "订单数", "购买客户数", "目标达成情况"]),
    "comparison_period": ("本期及基期，例如2025Q3对比2025Q2", []),
    "data_scope": ("比较范围，例如全部音频或指定品类", ["全部音频", "仅Rock"]),
    "media_scope": ("媒体范围：音频、视频，还是分别/合并统计", ["音频", "视频", "分别统计", "合并音视频"]),
    "scope": ("目标比较的音频/品类范围", ["全部音频", "仅Rock"]),
    "genre_boundary": ("摇滚边界：仅Rock还是Rock与Rock And Roll合并", ["仅Rock", "Rock与Rock And Roll合并"]),
    "entity": ("具体对象，请回复TrackId 1或商品名称：具体名称（示例不是默认对象，名称定位能力仍需登记）", []),
    "classification_dimension": ("分类维度：Genre品类、MediaType媒体格式、所属Album或所在Playlist", ["Genre品类", "MediaType媒体格式", "所属Album", "所在Playlist"]),
    "observation_period": ("客户分层的观察季度，例如2025Q3", []),
    "playlist_id": ("具体播放列表编号，例如PlaylistId 1", []),
    "intent": ("明确业务问题或要查的指标/资料主题，例如2025Q3音频销售额是多少", []),
    "time_range": ("有效且明确的查询周期，例如2025Q3或2025年全年；不自动解释相对时间或非法季度", []),
    "context": ("没有可继承的任务，请提供完整问题", []),
    "condition_conflict": ("补充条件与已确认条件冲突；若要更正，请明确回复“改成……”", []),
}


def policy_clarify(task, missing, *, reused=False):
    from .clarification import build_clarification
    return build_clarification(task, missing, SLOT_SPECS, reused=reused)


GENERAL_CONTEXT_TERMS = (
    "公司", "企业", "业务", "项目", "系统", "数据库", "本地", "我们", "本项目",
    "实际", "销售", "销售额", "销量", "成交", "交易", "订单", "客户", "购买", "目标", "达标",
    "经营", "活动", "海报", "报名", "选品", "分层", "音乐", "音频", "视频", "媒体",
    "音乐类型", "Genre", "Rock", "Metal", "商品", "单曲", "曲目", "专辑", "艺术家",
    "播放列表", "TrackId", "CustomerId", "PlaylistId", "GenreId", "MediaType", "Chinook", "AdventureWorks",
    "数据表", "字段", "记录", "查询", "口径", "D01", "D02", "D03", "D04", "D05", "D06",
    "D07", "D08", "D09", "D10", "D11", "D12", "季度", "月份", "Q1", "Q2", "Q3", "Q4",
)

GENERAL_QUESTION_MARKERS = (
    "什么是", "是什么", "怎么理解", "如何理解", "有哪些", "哪一些", "举例", "介绍一下",
    "简单介绍", "通常包括", "一般包括", "常见的",
)


def looks_general_knowledge(question: str) -> bool:
    """Recognize low-risk general questions before business capability gates."""
    q = question.strip()
    if not q or any(term.casefold() in q.casefold() for term in GENERAL_CONTEXT_TERMS):
        return False
    return any(marker in q for marker in GENERAL_QUESTION_MARKERS)


@lru_cache(maxsize=1)
def metric_requirements():
    # Read-only server catalog, not eval data; required slots remain B-owned.
    path = Path(__file__).resolve().parents[4] / "profiles" / "chinook" / "metrics.json"
    return {item["metric_id"]: tuple(item["required_slots"]) for item in json.loads(path.read_text(encoding="utf-8"))["metrics"]}


def missing_conditions(task, profile):
    q, slots = task.original_question, task.slots
    if task.route == "rag":
        if task.intent == "classification_lookup":
            return (["entity"] if not slots.get("classification_entity") else []) + (["classification_dimension"] if not slots.get("classification_dimension") else [])
        # A general layering-rule explanation does not select a customer cohort.
        # Keep the period gate for a high-value-customer request, not a synonym
        # of an already approved rule question.
        if "高价值客户" in q:
            return ["observation_period"] if not (slots.get("year") and slots.get("quarter")) else []
        return []
    missing = []
    requirements = metric_requirements() if profile.sql_backend and profile.sql_backend.profile_id == "chinook" else {}
    period_required = any("start_date" in requirements.get(metric, ()) for metric in task.business_metric_ids) or "transaction_exists" in task.required_outputs
    if task.intent == "growth_rate":
        if not slots.get("comparison_period") or not slots["comparison_period"].get("quarter"):
            missing.append("comparison_period")
        prior = slots.get("comparison_period", {})
        if slots.get("year") and slots.get("quarter") and prior.get("year") and prior.get("quarter"):
            if (prior["year"], prior["quarter"]) >= (slots["year"], slots["quarter"]):
                missing.append("comparison_period")
        if not task.slot_provenance.get("metric", {}).get("confirmed"):
            missing.append("metric")
        if not slots.get("data_scope"):
            missing.append("data_scope")
    if task.intent in {"target_attainment", "target_difference"} and not task.slot_provenance.get("metric", {}).get("confirmed"):
        missing.append("metric")
    if period_required or task.needs_calculation:
        if not slots.get("year") and "comparison_period" not in missing:
            missing.append("year" if slots.get("quarter") or slots.get("month") else "period")
        elif slots.get("year") and not task.time_range and "comparison_period" not in missing:
            missing.append("period")
    if task.needs_calculation and task.intent != "growth_rate":
        if not slots.get("quarter"):
            missing.append("quarter")
        if not slots.get("scope"):
            missing.append("scope")
    if "摇滚相关" in q and not slots.get("genre_boundary_resolved"):
        missing.append("genre_boundary")
    if "卖得怎么样" in q and not task.business_metric_ids:
        missing.append("metric")
    for metric in task.business_metric_ids:
        for name in requirements.get(metric, ()):
            if name in {"start_date", "end_date"}:
                continue
            if name not in slots:
                missing.append(name)
    return list(dict.fromkeys(missing))


def typed_periods(question):
    # Entity identifiers and date-like poster values are not observation years.
    text = re.sub(r"(?i)(?:TrackId|CustomerId|GenreId|PlaylistId)\s*[:：=]?\s*\d+(?:\s*[-–至]\s*\d+)?", "", question)
    text = re.sub(r"(?i)(?<![A-Za-z0-9])D(?:0[1-9]|1[0-2])(?!\d)", " ", text)
    years = [int(x) for x in re.findall(r"(?<![A-Za-z\d])(\d{4})(?!\d)", text)]
    quarters = [int(x) if x.isdigit() else "一二三四".index(x) + 1
                for x in re.findall(r"(?:第?([一二三四1-4])季度|Q([1-4]))", text, re.I)
                for x in [x[0] or x[1]]]
    months = [int(x) for x in re.findall(r"(?<!\d)(\d{1,2})月", text)]
    slots = {}
    if years:
        slots["year"] = years[0]
    if quarters:
        slots["quarter"] = quarters[0]
    if months:
        slots["month"] = months[0]
    if len(quarters) == 2 and years:
        slots["comparison_period"] = {"year": years[-1], "quarter": quarters[-1]}
    elif len(quarters) == 2:
        slots["comparison_period"] = {"quarter": quarters[-1]}
    if quarters:
        slots["period_grain"] = "quarter"
    elif months:
        slots["period_grain"] = "month"
    elif years:
        slots["period_grain"] = "year"
    interval = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～)\s*(\d{4}-\d{2}-\d{2})", text)
    if interval:
        try:
            end = date.fromisoformat(interval[2])
            if not any(w in question for w in ("左闭右开", "不含结束")):
                end += timedelta(days=1)
            end_value = end.isoformat()
        except ValueError:
            end_value = interval[2]
        slots.update(start_date=interval[1], end_date=end_value, period_grain="range")
    return slots, years, quarters


def split_entities(question, profile):
    from .parsing import _entities
    positive, negative = [], []
    parts = re.split(r"(不包括|不包含|排除|除去)", question)
    for index, part in enumerate(parts):
        if part in {"不包括", "不包含", "排除", "除去"}:
            continue
        if index and parts[index - 1] in {"不包括", "不包含", "排除", "除去"}:
            # A negated span ends at a sentence/condition boundary.
            span, *rest = re.split(r"[，,。；;]", part, maxsplit=1)
            negative.extend(_entities(span, profile))
            if rest:
                positive.extend(_entities(rest[0], profile))
        else:
            positive.extend(_entities(part, profile))
    return positive, negative


def semantic_task(question, profile):
    from .parsing import _contains, _matched_metrics
    slots, years, quarters = typed_periods(question)
    entities, excluded = split_entities(question, profile)
    metrics = _matched_metrics(question, profile.question_rules)
    if looks_general_knowledge(question):
        return BusinessTask(
            original_question=question,
            normalized_question=question,
            intent="general_knowledge",
            route="general",
            retrieval_question=question,
            source_roles=["general_knowledge"],
        )
    metric_explicit = bool(metrics) or ("销售" in question and "增长" in question)
    q = question
    doc_terms = ("口径", "定义", "怎么计算", "如何计算", "规则", "手册", "方案", "海报", "复盘", "建议", "解读", "选品", "分层", "维护", "对应", "归类", "分类表", "Genre", "MediaType", "报名", "阈值", "一个分类", "分开", "并入", "范围差异", "范围怎么写", "核心结果", "包括哪些", "包含哪些", "按哪个分类", "媒体格式", "活动什么时候", "提交材料", "截止")
    document = _contains(q, doc_terms) or bool(re.search(r"(?<![A-Za-z0-9])D(?:0[1-9]|1[0-2])(?![A-Za-z0-9])", q, re.I))
    target = _contains(q, ("目标", "达标", "达成", "完成了多少"))
    compare = _contains(q, ("差多少", "差额", "距离", "达标", "达到目标", "和目标比", "是否都", "完成了多少", "完成了吗"))
    growth = _contains(q, ("增长", "环比", "同比"))
    plan_only = "计划" in q and not _contains(q, ("实际", "数据库", "查数"))
    ranking = _contains(q, ("最高", "排名", "Top", "前五", "前三"))
    query = bool(metrics) and not document or ranking or _contains(q, ("是否有成交", "购买Rock的客户", "购买 Rock 的客户"))
    if metrics and _contains(q, ("销售额是多少", "销量是多少", "订单数是多少", "客户数是多少", "实际销售额", "实际销量")) and not plan_only:
        query = True
    if document and (_contains(q, ("实际", "查数", "数据库", "最高", "哪些属于", "购买Rock的客户", "是否有成交", "增长了多少", "未达目标", "距离目标"))):
        query = True
    if target and compare and not plan_only:
        query = True
    if target and not compare:
        document = True
        if not ranking and not _contains(q, ("实际", "数据库", "查数", "销售额是多少", "销量是多少", "订单数是多少", "客户数是多少")):
            query = False  # A named target is a document value, not actual sales.
    if growth and not document:
        query = True
    report_only = ("复盘" in q or bool(re.search(r"D0[78]", q))) and not _contains(q, ("数据库", "查数", "核验", "核对数据库")) and not ("每月" in q and "实际" in q)
    premise_only = _contains(q, ("放在什么位置", "安排在哪里", "什么位置")) and _contains(q, ("活动", "方案")) and not _contains(q, ("核验", "实际", "差额", "差多少"))
    if report_only or premise_only:
        document, query = True, False
    if plan_only:
        document, query = True, False
    if document and not query:
        route, intent, calc = "rag", "document_rule", False
    elif query:
        route = "cross_source" if document or target or growth else "sql"
        intent = "target_difference" if target and _contains(q, ("差", "距离")) else "target_attainment" if target and compare else "growth_rate" if growth else "cross_source_query" if route == "cross_source" else "fact_query"
        calc = intent in {"target_difference", "target_attainment", "growth_rate"}
    else:
        route, intent, calc = "sql", "unknown", False
    if ranking and not metrics:
        metrics = ["sales_amount"]
    if (ranking or "各" in q) and _contains(q, ("品类", "音乐分类")):
        metrics = ["genre_sales"] if "订单数" not in q and "客户数" not in q else metrics
    elif ranking and _contains(q, ("商品", "单曲")):
        metrics = ["track_sales"]
    elif ranking and "艺术家" in q:
        metrics = ["artist_sales"]
    elif ranking and "专辑" in q and "商品" not in q:
        metrics = ["album_sales"]
    elif "每个播放列表" in q and "商品" in q:
        metrics, route, intent = ["playlist_track_count"], "sql", "fact_query"
    elif "客户" in q and _contains(q, ("购买音频金额", "购买金额")):
        metrics, route, intent = ["sales_amount"], "sql", "fact_query"
    if "各媒体类型" in q:
        metrics = list(dict.fromkeys(["media_type_sales", *metrics]))
    if calc and not metrics:
        metrics = ["sales_amount"]
    if query and "是否有成交" in q:
        metrics = ["track_sales"]
        metric_explicit = True
    if query and not metrics and "客户" in q:
        metrics = ["sales_amount"]
    if entities:
        slots["scope"] = slots["data_scope"] = ", ".join(e["label"] for e in entities)
    if _contains(q, ("全部音乐", "全部音频", "全音频")):
        entities = [e for e in entities if e["type"] != "genre"]
        slots["scope"] = slots["data_scope"] = "全部音频"
    elif "音频" in q and not entities:
        slots["scope"] = slots["data_scope"] = "全部音频"
    if "comparison_period" not in slots and slots.get("year") and slots.get("quarter"):
        year, quarter = slots["year"], slots["quarter"]
        if "环比" in q:
            slots["comparison_period"] = {"year": year if quarter > 1 else year - 1, "quarter": quarter - 1 if quarter > 1 else 4}
        elif "同比" in q:
            slots["comparison_period"] = {"year": year - 1, "quarter": quarter}
    slots["media_ids"] = list(profile.default_media_ids)
    if "视频" in q and "音频" not in q and "音乐" not in q:
        slots["media_ids"] = [3]
    if "各媒体类型" in q or "音频和视频" in q or "音乐和视频" in q:
        slots["media_ids"] = sorted(set(profile.default_media_ids) | {3})
    if _contains(q, ("仅", "不包括", "不包含", "排除")) or slots.get("scope") == "全部音频":
        slots["genre_boundary_resolved"] = True
    outputs = ["actual"] if query else ["document_facts"]
    if ranking:
        outputs.append("ranking")
    if calc:
        outputs = ["actual", "target", "attainment_rate", "verdict"] if target else ["current", "previous", "growth_rate"]
        if "差" in q or "距离" in q or "和目标比" in q:
            outputs.append("difference")
    if document and query:
        outputs.append("document_facts")
    if query and "每月" in q:
        outputs.append("monthly_actuals")
    if query and "是否有成交" in q:
        outputs.append("transaction_exists")
    if query and "属于A层" in q.replace(" ", ""):
        outputs.append("customer_tiers")
    if plan_only and "合计" in q:
        outputs.append("document_sum")
    if ranking and "客户" in q:
        outputs.append("customer_ranking")
    if "按支持负责人" in q:
        outputs.append("support_distribution")
    if ranking and "商品" in q and "专辑" in q:
        outputs.append("album_details")
    if ranking and "商品" in q and "艺术家" in q:
        outputs.append("artist_details")
    task = BusinessTask(original_question=q, normalized_question=q, intent=intent, route=route,
        slots=slots, entities=entities, excluded_entities=excluded, business_metric_ids=metrics,
        needs_calculation=calc, information_needs=(["sql"] if query else []) + (["rag"] if document or target or growth else []),
        required_outputs=outputs, retrieval_question=q,
        slot_provenance={key: {"source": "explicit", "confirmed": True} for key in slots if key != "media_ids"})
    task.slot_provenance["metric"] = {"source": "explicit" if metric_explicit else "unconfirmed_default", "confirmed": metric_explicit}
    from ..knowledge.facts import answer_obligations
    task.answer_obligations = answer_obligations(q)
    if "monthly_actuals" in outputs:
        task.slots["result_grain"] = "month"
        task.business_metric_ids = ["sales_amount"]
        if "计划" in q:
            task.route, task.intent, task.needs_calculation = "cross_source", "target_difference", True
            task.required_outputs += ["difference", "attainment_rate"]
    interval = re.search(r"TrackId\s*(\d+)\s*[-–至]\s*(\d+)", q, re.I)
    if interval:
        task.slots["requested_track_range"] = [int(interval[1]), int(interval[2])]
    task.source_roles = ["reported_actual"] if report_only else ["database_actual"] if query else ["document_rule"]
    if target:
        task.source_roles.append("target")
    if plan_only or "每月" in q and "计划" in q:
        task.source_roles.append("plan")
    if premise_only and "未达" in q:
        task.assumptions = [q.split("，")[0].split(",")[0]]
    from .clarification_reply import augment_task
    return augment_task(task, q)


def finalize_task(task, profile):
    from .parsing import _time_range, clarify
    missing = []
    if task.time_issues:
        return policy_clarify(task, ["time_range"])
    if task.slots.get("media_aggregation") == "separate":
        return ParsedTurn(decision="unsupported", task=task, reason="已记录分别统计音频与视频；当前未批准按这两组分别输出的查询粒度，未用合计值替代，也不再要求无用补充。", rules=["media_grouping_capability_gap"])
    try:
        if task.slots.get("period_grain") == "range":
            from ..contracts import TimeRange
            start = date.fromisoformat(task.slots["start_date"])
            end = date.fromisoformat(task.slots["end_date"])
            if end <= start:
                raise ValueError("invalid explicit interval")
            task.time_range = TimeRange(start=task.slots["start_date"], end=task.slots["end_date"], end_inclusive=False, label="明确日期区间")
        elif task.slots.get("month") and task.slots.get("year"):
            year, month = task.slots["year"], task.slots["month"]
            from ..contracts import TimeRange
            start = date(year, month, 1)
            end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            task.time_range = TimeRange(start=start.isoformat(), end=end.isoformat(), end_inclusive=False, label=f"{year}年{month}月")
        elif task.slots.get("period_grain") == "unspecified":
            task.time_range = None
        else:
            task.time_range = _time_range(task.slots)
        if task.time_range:
            task.slots.update(start_date=task.time_range.start, end_date=task.time_range.end)
    except (ValueError, TypeError):
        missing.append("time_range")
    if task.route == "general":
        return ParsedTurn(decision="ready", task=task, rules=[POLICY_VERSION, "general_knowledge"])
    if task.route == "sql" and task.intent == "unknown":
        if "卖得怎么样" in task.original_question:
            deferred = [] if task.time_range else ["year" if task.slots.get("quarter") else "period"]
            return policy_clarify(task, ["metric", *deferred])
        return policy_clarify(task, ["intent"])
    if missing:
        return policy_clarify(task, missing)
    positive = {e["id"] for e in task.entities if e["type"] == "genre"}
    negative = {e["id"] for e in task.excluded_entities if e["type"] == "genre"}
    if task.route != "rag" and negative and (not positive or positive & negative):
        return ParsedTurn(decision="unsupported", task=task, reason="当前查询契约未批准该排除集合；不会丢掉排除条件后执行。", rules=["exclusion_capability_gap"])
    coverage = []
    if task.route != "rag" and task.time_range and profile.data_start and profile.data_end:
        start, end = date.fromisoformat(task.time_range.start), date.fromisoformat(task.time_range.end)
        available_start, available_end = date.fromisoformat(profile.data_start), date.fromisoformat(profile.data_end) + timedelta(days=1)
        if end <= available_start or start >= available_end:
            return ParsedTurn(decision="unsupported", task=task, reason=f"查询周期不在已核验数据覆盖{profile.data_start}至{profile.data_end}内。", rules=["data_coverage_gate"])
        if start < available_start or end > available_end:
            coverage.append("partial_data_coverage")
    if task.route != "rag" and "数据库中" in task.original_question and not task.time_range:
        return ParsedTurn(decision="unsupported", task=task, reason="该全库交易任务需要无时间范围的指标契约；当前注册交易指标要求周期，未伪造日期执行。", rules=["full_database_capability_gap"])
    missing = missing_conditions(task, profile)
    if missing:
        return policy_clarify(task, missing)
    if task.intent == "classification_lookup":
        return ParsedTurn(decision="unsupported", task=task, reason="已确认商品对象和分类维度；当前未登记逐商品Genre/MediaType/Album/Playlist查询能力，不能用一般分类规则冒充该商品实际属性。", rules=["classification_capability_gap"])
    if task.route == "rag" and "高价值客户" in task.original_question and any(w in task.original_question for w in ("哪些", "谁", "名单")):
        return ParsedTurn(decision="unsupported", task=task, reason="已确认观察周期；当前仅批准指定品类购买集合的客户金额分层模板，未批准全体高价值客户名单查询，不能只返回规则冒充名单。", rules=["customer_list_capability_gap"])
    required = {"sql": ["sql"], "rag": ["rag"], "cross_source": ["sql", "rag"]}[task.route]
    if task.needs_calculation:
        required.append("calculate")
    if any(not profile.supports(cap) for cap in required):
        return ParsedTurn(decision="unsupported", task=task, reason="当前profile不支持完整任务。", rules=["capability_gate"])
    if "media_ids" in task.slots:
        task.slot_provenance.setdefault("media_ids", {"source": "profile_default", "confirmed": False})
    return ParsedTurn(decision="ready", task=task, rules=[POLICY_VERSION, *coverage])


def parse_c_v1(question, profile, memory):
    from .parsing import _contains
    q = question.strip()
    explicit_new = q.startswith(("新问题", "另外问", "重新查询"))
    if explicit_new:
        q = re.sub(r"^(新问题|另外问|重新查询)[:：\s]*", "", q)
        memory.pending = memory.last_task = memory.previous_view = None
        memory.pending_slots = []
        memory.pending_clarification = {}
        memory.active_task_id = None
    if q in {"取消", "取消当前问题", "重新开始", "清空上下文"}:
        return ParsedTurn(decision="cancelled", reason="已清空当前任务。", rules=["reset_context"])
    general_question = looks_general_knowledge(q)
    if not general_question:
        for metric, terms in profile.question_rules.unsupported_terms.items():
            if any(term.casefold() in q.casefold() for term in terms):
                return ParsedTurn(decision="unsupported", reason=f"当前数据不支持{metric}，不能用其他指标替代。", rules=["data_gap"])
        if any(term in q for term in ("点击", "转化")):
            return ParsedTurn(decision="unsupported", reason="没有页面事件/归因数据，无法计算点击或转化。", rules=["event_data_gap"])
        if "SupportRep" in q and "员工" in q and "销售额" in q:
            return ParsedTurn(decision="unsupported", reason="SupportRep仅表示客户维护责任，不能据此认定员工个人销售贡献。", rules=["invalid_attribution"])
    if (re.search(r"(?:音乐|音频).*(?:和|与|及).*视频.*(?:分开|分别).*还是.*(?:合计|合并)", q)
            and memory.pending is None):
        return policy_clarify(semantic_task(q, profile), ["media_scope"])
    if "高价值客户" in q:
        task = semantic_task(q, profile)
        task.intent, task.route, task.needs_calculation = "document_rule", "rag", False
    else:
        task = semantic_task(q, profile)
    if "track_attributes" in task.business_metric_ids:
        for key in ("year", "quarter", "month", "period_grain", "start_date", "end_date"):
            task.slots.pop(key, None)
        task.time_range = None
        task.intent, task.route, task.needs_calculation = "track_attributes", "sql", False
    if "客户" in q and _contains(q, ["购买", "买过"]) and _contains(q, ["A层", "A 层", "重点维护"]):
        task.intent, task.route, task.needs_calculation = "customer_segmentation", "cross_source", False
        task.business_metric_ids = ["sales_amount"]
        task.slots["business_workflow"] = "customer_segmentation"
    elif _contains(q, ["海报", "主页面"]) and _contains(q, ["成交", "卖出", "销售"]) and _contains(q, ["TrackId", "商品编号", "商品ID"]):
        from .entity_ids import track_selection
        try:
            selection = track_selection(q)
        except ValueError:
            return policy_clarify(task, ["filter_scope"])
        task.intent, task.route, task.needs_calculation = "poster_candidates", "cross_source", False
        task.business_metric_ids = ["sales_amount"]
        task.slots.update(business_workflow="poster_candidates", track_selection=selection)
    anchored = {"customer_segmentation", "poster_candidates", "cross_source_query", "target_attainment", "target_difference", "growth_rate"}
    if task.intent in anchored and task.slots.get("quarter") and not task.slots.get("year"):
        task.slots["year"] = 2025
        task.slots["year_assumed"] = 2025
    if task.slots.get("year") and task.slots.get("quarter") and not task.time_range:
        from .parsing import _time_range
        task.time_range = _time_range(task.slots)
        if task.time_range:
            task.slots["start_date"] = task.time_range.start
            task.slots["end_date"] = task.time_range.end
    # Keep approved exact document aliases without losing their canonical topic.
    for query in profile.question_rules.document_queries:
        if q.rstrip("？?") in {a.rstrip("？?") for a in query.aliases}:
            task.intent, task.route, task.needs_calculation = "document_rule", "rag", False
            task.information_needs = ["rag"]
            task.normalized_question = task.retrieval_question = query.canonical_question
            task.business_metric_ids = query.business_metric_ids
    # Frozen offline aliases still use their explicitly approved candidate.
    for query in profile.question_rules.offline_queries:
        if q.rstrip("？?") in {a.rstrip("？?") for a in query.aliases}:
            task.intent, task.route = query.intent, "sql"
            task.normalized_question = query.canonical_question
            task.business_metric_ids = query.business_metric_ids
            task.slots = {}
    from .turn_update import update_task
    from .clarification_reply import prepare_reply
    task, forced_action, early = prepare_reply(q, task, memory, profile, SLOT_SPECS)
    if early:
        return early
    task, reused, action = update_task(q, task, memory, profile, force_action=forced_action)
    if not reused and action in {"followup", "correct", "confirm"} and memory.pending is None and memory.last_task is None:
        if task.intent == "unknown" or q.startswith(("和目标比", "改成", "换成", "那")):
            return policy_clarify(None, ["context"])
    result = finalize_task(task, profile)
    result.reused_context = reused
    result.rules.append("turn:" + action)
    return result
