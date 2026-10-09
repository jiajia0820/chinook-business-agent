"""Finite, inspectable grammar. Not a general natural-language SQL model."""

from datetime import date, timedelta
import re

from ..contracts import Clarification, MissingSlot, TimeRange
from ..profiles.models import BusinessProfile
from .state import BusinessTask, ParsedTurn, SessionMemory


SLOT_DESCRIPTIONS = {
    "year": "具体年份，例如 2025 年（不会默认补年份）",
    "quarter": "具体季度，例如第三季度",
    "scope": "目标范围：仅 Rock、某个品类，或全部音乐",
    "target_metric": "要比较的目标指标，例如销售额",
    "metric": "要查询的指标，例如销售额、客户记录数或购买客户数",
    "comparison_period": "比较周期，例如 2025 Q3 对比 2025 Q2",
    "data_scope": "比较的数据范围，例如 Rock 音频或全部音频",
    "genre_boundary": "摇滚边界：仅 Rock，还是明确列出的合并品类",
    "intent": "请明确查询指标或要解释的业务规则",
    "context": "当前没有可继承的任务，请补充完整问题",
    "condition_conflict": "补充条件与已确认条件冲突；请使用“改成……”明确更正",
    "time_range": "年份/季度必须有效；本阶段仅支持整年或完整季度",
    "filter_scope": "该限定条件尚不能可靠解析，请改为已支持的明确条件",
}


def clarify(task, missing, *, reused=False, rules=None, reason=None):
    return ParsedTurn(
        decision="clarification", task=task, reused_context=reused,
        reason=reason, rules=rules or [],
        clarification=Clarification(
            question="请补充或确认：" + "；".join(SLOT_DESCRIPTIONS.get(s, s) for s in missing),
            missing_slots=[MissingSlot(name=s, description=SLOT_DESCRIPTIONS.get(s, s)) for s in missing],
            options=["2025 年", "仅 Rock，按销售额", "取消当前问题"],
        ),
    )


def _contains(question, terms):
    return any(term.casefold() in question.casefold() for term in terms)


# 数据范围词会插在指标词中间（“多少张音频订单”“购买音频的客户”），
# 去掉后再匹配一次词典，避免把已登记的指标误判为需要澄清。
METRIC_SCAN_NOISE = ("音频", "音乐", "视频", "全部", "各", "的")


def _metric_scan_text(question):
    text = question
    for noise in METRIC_SCAN_NOISE:
        text = text.replace(noise, "")
    return text


def _matched_metrics(question, rules):
    """Return registered metric ids named by the question, in catalog order."""
    scan = _metric_scan_text(question)
    return [metric for metric, terms in rules.metric_terms.items()
            if _contains(question, terms) or _contains(scan, terms)]


def _periods(question):
    """Return explicitly stated periods only, never a wall-clock default."""
    quarter_pattern = r"(?:第?([一二三四1234])季度|Q([1-4]))"
    year_matches = re.findall(r"(?<!\d)(\d{4})(?:\s*年)?(?!\d)", question)
    quarters = [int(a or b) if (a or b).isdigit() else "一二三四".index(a) + 1
                for a, b in re.findall(quarter_pattern, question, flags=re.I)]
    years = [int(year) for year in year_matches]
    slots = {}
    if years:
        slots["year"] = years[0]
    if quarters:
        slots["quarter"] = quarters[0]
    if len(quarters) == 2 and years:
        slots["comparison_period"] = {
            "year": years[1] if len(years) > 1 else years[0], "quarter": quarters[1],
        }
    return slots, years, quarters


def _entities(question, profile):
    # Longest-first span matching prevents 摇滚乐/重金属/Rock And Roll
    # from also matching their shorter, differently identified aliases.
    candidates = []
    for alias in profile.aliases:
        for term in {alias.alias, alias.label}:
            pattern = re.escape(term)
            if term.isascii():
                pattern = r"(?<![A-Za-z])" + pattern + r"(?![A-Za-z])"
            for match in re.finditer(pattern, question, flags=re.I):
                candidates.append((match.start(), match.end(), alias))
    used, result = [], []
    for start, end, alias in sorted(candidates, key=lambda x: -(x[1] - x[0])):
        if any(start < other_end and end > other_start for other_start, other_end in used):
            continue
        used.append((start, end))
        item = {"type": alias.entity_type, "id": alias.entity_id, "label": alias.label}
        if item not in result:
            result.append(item)
    return result


def _time_range(slots):
    year = slots.get("year")
    if year is None:
        return None
    quarter = slots.get("quarter")
    if quarter:
        month = (quarter - 1) * 3 + 1
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if quarter == 4 else date(year, month + 3, 1)
        label = f"{year} Q{quarter}"
    else:
        start, end, label = date(year, 1, 1), date(year + 1, 1, 1), f"{year} 年"
    return TimeRange(start=start.isoformat(), end=end.isoformat(), end_inclusive=False, label=label)


def _unparsed_query_text(question, profile):
    """Conservative finite grammar: never silently drop an unknown SQL filter."""
    residue = question
    terms = [term for terms in profile.question_rules.metric_terms.values() for term in terms]
    terms += [term for terms in profile.question_rules.intent_terms.values() for term in terms]
    terms += [term for alias in profile.aliases for term in (alias.alias, alias.label)]
    terms += profile.question_rules.ambiguous_scope_terms + profile.question_rules.query_fillers
    for term in sorted(set(terms), key=len, reverse=True):
        residue = re.sub(re.escape(term), "", residue, flags=re.I)
    residue = re.sub(r"(?<!\d)\d{4}\s*年?|第?[一二三四1-4]季度|Q[1-4]|[\s，,。:：?？!！、]+", "", residue, flags=re.I)
    return residue


def parse_question(question: str, profile: BusinessProfile, memory: SessionMemory) -> ParsedTurn:
    q = question.strip()
    rules = profile.question_rules
    if q in {"取消", "取消当前问题", "重新开始", "清空上下文"}:
        return ParsedTurn(decision="cancelled", reason="已清空当前任务，请输入完整的新问题。", rules=["reset_context"])
    explicit_new = q.startswith(("新问题", "另外问", "重新查询"))
    if explicit_new:
        q = re.sub(r"^(新问题|另外问|重新查询)[:：\s]*", "", q)
    if not q:
        return clarify(None, ["intent"], rules=["empty_new_question"])
    for metric, terms in rules.unsupported_terms.items():
        if _contains(q, terms):
            return ParsedTurn(decision="unsupported", reason=f"当前数据与工具不支持指标 {metric}，不能用其他指标替代。", rules=[f"data_gap:{metric}"])

    # Whole-question document aliases are configured vocabulary, not a list
    # of gold answers. Unknown filters never get removed to match an alias.
    key = q.strip().rstrip("？?")
    for query in rules.document_queries:
        if key in {alias.strip().rstrip("？?") for alias in query.aliases}:
            task = BusinessTask(original_question=q, normalized_question=query.canonical_question,
                intent="document_rule", route="rag", business_metric_ids=query.business_metric_ids)
            if not profile.supports("rag"):
                return ParsedTurn(decision="unsupported", task=task, reason="当前 profile 不提供 RAG 能力。", rules=["capability_gate"])
            return ParsedTurn(decision="ready", task=task, rules=["exact_document_alias", "capability_gate"])

    # Exact approved whole-question aliases only: never strip filters or map
    # all free text to an offline canned query. New full questions reset pending.
    if profile.sql_backend and profile.sql_backend.model_mode == "offline":
        key = q.strip().rstrip("？?")
        for query in rules.offline_queries:
            if key in {alias.strip().rstrip("？?") for alias in query.aliases}:
                task = BusinessTask(original_question=q, normalized_question=query.canonical_question,
                    intent=query.intent, route="sql", entities=_entities(q, profile), business_metric_ids=query.business_metric_ids)
                if not profile.supports("sql"):
                    return ParsedTurn(decision="unsupported", task=task, reason="当前 profile 不提供 SQL 能力。", rules=["capability_gate"])
                return ParsedTurn(decision="ready", task=task, rules=["exact_offline_alias", "capability_gate"])

    explicit_slots, years, quarters = _periods(q)
    entities = _entities(q, profile)
    metric_ids = _matched_metrics(q, rules)
    hits = {name: _contains(q, terms) for name, terms in rules.intent_terms.items()}
    followup = q.startswith(("换成", "改成", "那", "和目标比", "仅", "按", "对比", "环比", "同比"))
    # Slot-only responses are safe supplements; arbitrary text is not silently ignored.
    stripped = re.sub(r"\d{4}\s*年?|第?[一二三四1234]季度|Q[1-4]|[\s，,。?？呢]+", "", q, flags=re.I)
    slot_only = not stripped
    supplement = slot_only or q.startswith(("仅", "按", "全部音乐", "全部音频", "音频", "改成", "换成"))
    base = None
    if not explicit_new:
        if memory.pending and (supplement or not any(hits.values()) and not metric_ids):
            base = memory.pending
        elif memory.last_task and (followup or slot_only):
            base = memory.last_task
    reused = base is not None
    if (followup or slot_only) and base is None and not entities and not metric_ids and not explicit_new:
        return clarify(None, ["context"], rules=["no_context"])

    if base:
        task = base.model_copy(deep=True)
        old_slots = dict(task.slots)
        conflicts = [key for key, value in explicit_slots.items() if key in old_slots and old_slots[key] != value]
        entity_conflict = entities and task.entities and entities != task.entities
        metric_conflict = metric_ids and task.business_metric_ids and metric_ids != task.business_metric_ids
        if memory.pending and (conflicts or entity_conflict or metric_conflict) and not q.startswith(("改成", "换成", "更正")):
            return clarify(task, ["condition_conflict"], reused=True, rules=["preserve_confirmed_slots"])
        task.slots.update(explicit_slots)
        if entities:
            task.entities = entities
            task.slots["scope"] = ", ".join(e["label"] for e in entities)
            task.slots["data_scope"] = task.slots["scope"]
        if metric_ids:
            task.business_metric_ids = metric_ids
    else:
        task = BusinessTask(original_question=q, normalized_question=q, intent="unknown", route="sql", slots=explicit_slots, entities=entities, business_metric_ids=metric_ids)
        if entities:
            task.slots["scope"] = ", ".join(e["label"] for e in entities)
            task.slots["data_scope"] = task.slots["scope"]
    if _contains(q, ["全部音乐", "全部音频"]):
        task.entities = []
        task.slots["scope"] = task.slots["data_scope"] = "全部音频"
    if _contains(q, ["仅", "合并", "全部"]) and (entities or task.slots.get("scope") == "全部音频"):
        task.slots["genre_boundary_resolved"] = True
    task.slots.setdefault("media_ids", list(profile.default_media_ids))
    if task.business_metric_ids:
        task.slots["target_metric"] = task.business_metric_ids[0]
    if hits.get("document_calculation"):
        task.intent, task.route, task.needs_calculation = "document_calculation", "rag", True
    elif hits.get("target_difference"):
        task.intent, task.route, task.needs_calculation = "target_difference", "cross_source", True
    elif hits.get("target_attainment"):
        task.intent, task.route, task.needs_calculation = "target_attainment", "cross_source", True
    elif hits.get("growth_rate"):
        task.intent, task.route, task.needs_calculation = "growth_rate", "sql", True
        if len(quarters) == 1 and "year" in task.slots:
            year, quarter = task.slots["year"], task.slots["quarter"]
            if "环比" in q:
                task.slots["comparison_period"] = {"year": year if quarter > 1 else year - 1, "quarter": quarter - 1 if quarter > 1 else 4}
            elif "同比" in q:
                task.slots["comparison_period"] = {"year": year - 1, "quarter": quarter}
    elif hits.get("document_rule") and not hits.get("cross_source"):
        task.intent, task.route, task.needs_calculation = "document_rule", "rag", False
    elif hits.get("cross_source"):
        task.intent, task.route = "cross_source_query", "cross_source"
    elif task.business_metric_ids and (not base or not supplement or task.intent == "unknown"):
        task.intent = "quarterly_sales" if "sales_amount" in task.business_metric_ids and "quarter" in task.slots else "fact_query"
        task.route, task.needs_calculation = "sql", False
    elif not base:
        task.intent = "unknown"

    if not task.business_metric_ids:
        task.business_metric_ids = list(rules.intent_default_metric_ids.get(task.intent, []))
        if task.business_metric_ids:
            task.slots["target_metric"] = task.business_metric_ids[0]
    # Defaults are declared in profile; no year or numeric target is inferred.
    if task.intent == "target_attainment" and not task.business_metric_ids:
        return clarify(task, ["target_metric"] + ([] if task.slots.get("year") else ["year"]), reused=reused, rules=["target_metric_not_inferred"])
    missing = []
    # 自由问法先交给多文档检索：检索不到证据时仍返回 insufficient_evidence，不会编造答案。
    # offline 模式保持严格，只回答配置过的固定问法，避免把离线演示当成自由检索。
    offline_backend = profile.sql_backend is not None and profile.sql_backend.model_mode == "offline"
    if task.intent == "unknown" and profile.supports("rag") and (
            not offline_backend or (not years and not quarters and not entities)):
        task.intent, task.route = "document_rule", "rag"
    if task.intent == "unknown" or hits.get("vague_metric") and not task.business_metric_ids:
        missing.append("metric" if hits.get("vague_metric") else "intent")
    if task.route != "rag":
        missing.extend(slot for slot in profile.required_slots_by_intent.get(task.intent, []) if slot not in task.slots)
        if "sales_amount" in task.business_metric_ids or "purchasing_customers" in task.business_metric_ids:
            if "year" not in task.slots:
                missing.append("year")
    if task.intent == "growth_rate" and not task.business_metric_ids:
        missing.append("metric")
    if task.intent == "growth_rate":
        missing.extend(slot for slot in ("year", "quarter") if slot not in task.slots)
    if _contains(q, rules.ambiguous_scope_terms) or base and _contains(base.original_question, rules.ambiguous_scope_terms) and not task.slots.get("genre_boundary_resolved"):
        missing.append("genre_boundary")
    # Do not answer a narrower day/month question using a whole-quarter query.
    if re.search(r"\d{1,2}月|\d{1,2}日|最近|今年|去年|本季度|本月", q):
        missing.append("time_range")
    if re.search(r"第?(?:[五六七八九]|[5-9])季度", q) and not re.search(r"第?[一二三四1-4]季度", q):
        missing.append("time_range")
    if len(years) > 1 or len(quarters) > 1:
        if task.intent != "growth_rate" or len(quarters) != 2 or len(years) > 2 or not any(term in q for term in ("对比", "相比")):
            missing.append("time_range")
    if task.intent != "unknown" and task.route != "rag" and profile.sql_backend.model_mode == "offline" and _unparsed_query_text(q, profile):
        missing.append("filter_scope")
    try:
        task.time_range = _time_range(task.slots)
        if task.time_range is not None:
            task.slots["start_date"] = task.time_range.start
            task.slots["end_date"] = task.time_range.end
    except (ValueError, TypeError):
        missing.append("time_range")
    if base and not explicit_slots and not entities and not metric_ids and not any(hits.values()) and not supplement:
        missing.append("intent")
    if missing:
        return clarify(task, list(dict.fromkeys(missing)), reused=reused, rules=["required_slots", "finite_grammar"])
    coverage_rules = []
    if task.route != "rag" and task.time_range and profile.data_start and profile.data_end:
        start, end = date.fromisoformat(task.time_range.start), date.fromisoformat(task.time_range.end)
        available_start = date.fromisoformat(profile.data_start)
        available_end = date.fromisoformat(profile.data_end) + timedelta(days=1)
        if end <= available_start or start >= available_end:
            return ParsedTurn(decision="unsupported", task=task, reason=f"查询周期不在当前样例数据覆盖范围 {profile.data_start} 至 {profile.data_end} 内。", reused_context=reused, rules=["data_coverage_gate"])
        if start < available_start or end > available_end:
            coverage_rules.append("partial_data_coverage")
    # Normalization is rebuilt from current conditions, not appended to an old
    # question containing obsolete years/quarters/entities.
    metrics = "、".join(profile.metric(m).name for m in task.business_metric_ids)
    task.normalized_question = "；".join(filter(None, [task.intent, metrics, str(task.slots.get("scope", "")), task.time_range.label if task.time_range else None, f"条件={task.slots}"]))
    if task.route == "rag" or not base:
        # Keep this turn's document wording, including any new unknown topic.
        # Explicit inherited metrics still travel as hints; old question text
        # must not conceal a changed rule question and retrieve old evidence.
        task.normalized_question = q if task.route == "rag" else task.original_question
    required = {"sql": ["sql"], "rag": ["rag"], "cross_source": ["sql", "rag"]}[task.route]
    if task.needs_calculation:
        required.append("calculate")
    if any(not profile.supports(cap) for cap in required):
        return ParsedTurn(decision="unsupported", task=task, reason="当前 profile 的 mode/capabilities 不支持该完整任务。", reused_context=reused, rules=["capability_gate"])
    return ParsedTurn(decision="ready", task=task, reused_context=reused, rules=["profile_vocabulary", "explicit_slots", "capability_gate", *coverage_rules])
