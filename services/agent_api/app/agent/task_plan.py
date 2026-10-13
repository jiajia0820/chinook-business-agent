"""Finite plans derived from approved document roles, never evaluation gold."""

import re


DOCUMENT_ROLES = {
    "D01": ("指标定义", "增长率", "差额", "达成率"),
    "D02": ("分析方法", "解读"),
    "D03": ("Genre", "分类", "类型", "边界", "重金属", "归类", "摇滚乐", "摇滚", "音乐商品", "媒体格式"),
    "D04": ("客户分层", "分层", "A层", "A、B、C层", "高价值", "维护", "支持负责人"),
    "D05": ("选品", "候选", "商品推荐"),
    "D06": ("目标", "达标", "计划", "达成率"),
    "D07": ("二季度", "第二季度", "Q2"),
    "D08": ("三季度", "第三季度", "Q3"),
    "D09": ("品类运营", "运营手册", "重点看", "运营动作", "观察什么"),
    "D10": ("活动方案", "活动时间", "活动什么时候举行"),
    "D11": ("海报", "报名", "主页面", "活动周期", "活动时间", "活动什么时候举行"),
    "D12": ("默认", "音乐范围", "全店销售额", "购买客户数与登记"),
}


def document_needs(task):
    q = task.retrieval_question or task.original_question
    explicit = re.findall(r"(?<![A-Za-z0-9])D(?:0[1-9]|1[0-2])(?![A-Za-z0-9])", q, re.I)
    docs = list(dict.fromkeys(x.upper() for x in explicit))
    if docs and any(w in q for w in ("仅依据", "只依据", "仅使用", "只使用")):
        return docs
    # SourcePlan owns the authoritative role-to-document mapping.  Explicit
    # document references remain caller-owned so a question such as
    # “D06、D08 和 D10 如何衔接” does not silently grow an extra source.
    if not docs and task.route in {"rag", "cross_source"}:
        from ..knowledge.source_plan import rag_source_plan
        docs.extend(rag_source_plan(q).required_docs)
    from ..knowledge.facts import FACTS
    docs += [FACTS[role][0] for role in task.answer_obligations if role in FACTS]
    if task.intent == "customer_segmentation":
        docs += ["D04"]
    elif task.intent == "poster_candidates":
        docs += ["D05", "D11"]
    for doc, words in DOCUMENT_ROLES.items():
        if any(word.casefold() in q.casefold() for word in words):
            # A quarter by itself is not a requirement to quote a recap.
            if doc in {"D07", "D08"} and not any(word in q for word in ("复盘", "核心结果")):
                continue
            if doc not in docs:
                docs.append(doc)
    if task.needs_calculation:
        primary = "D01" if task.intent == "growth_rate" else "D06"
        docs = [primary, *[d for d in docs if d != primary]]
        if primary == "D06":
            docs = [d for d in docs if d != "D01"]  # D06 itself defines the target formulas.
        # Cross-source calculations also need the period review source.  D06
        # supplies the approved target/formula, while D08 is the authoritative
        # Q3 review evidence used by the answer and conflict checks.  Keep it
        # as a retrieval requirement without turning document text into SQL.
        if task.route == "cross_source":
            if task.intent == "growth_rate":
                docs.extend(doc for doc in ("D07", "D08") if doc not in docs)
            elif task.intent in {"target_attainment", "target_difference", "document_calculation"}:
                review_doc = {2: "D07", 3: "D08"}.get(task.slots.get("quarter"))
                if review_doc and review_doc not in docs:
                    docs.append(review_doc)
    if not docs and task.route in {"rag", "cross_source"}:
        return [None]  # Known document request without a catalog cue: bounded ordinary retrieval.
    return list(dict.fromkeys(docs))


def make_plan(task):
    docs = document_needs(task) if task.route != "sql" else []
    doc_first = task.route == "cross_source" and any(d in docs for d in ("D04", "D05", "D11")) and any(w in task.original_question for w in ("属于", "主页面商品", "要求的", "选品"))
    from .cross_templates import BusinessSubtask
    retrieval = [{**BusinessSubtask(task_id=f"document-{index}", kind="rag", fact_role="business_document", grain="document_fact").model_dump(mode="json"), "doc_id": doc, "role": "business_document"} for index, doc in enumerate(docs)]
    sql = [{"task_id": "structured-facts", "kind": "sql", "depends_on": [r["task_id"] for r in retrieval] if doc_first else [], "scope": dict(task.slots)}] if task.route in {"sql", "cross_source"} else []
    if sql:
        sql[0] = {**BusinessSubtask(task_id="structured-facts", kind="sql", fact_role="database_actual", grain=task.slots.get("result_grain", "period_scope"),
            period={k: task.slots[k] for k in ("year", "quarter", "month") if k in task.slots}, scope=dict(task.slots),
            depends_on=sql[0]["depends_on"], required_fields=list(task.business_metric_ids)).model_dump(mode="json")}
    if not doc_first and sql:
        for request in retrieval:
            request["depends_on"] = ["structured-facts"]
    return [*retrieval, *sql] if doc_first else [*sql, *retrieval]


def retrieval_requests(task, top_k):
    return [(doc, query, count) for doc, query, count, _ in retrieval_specs(task, top_k)]


def retrieval_specs(task, top_k):
    from ..knowledge.facts import FACTS
    docs = document_needs(task)
    if not docs:
        return []
    from ..knowledge.source_plan import rag_source_plan
    source_plan = rag_source_plan(task.retrieval_question or task.original_question)
    # Whole-question top_k budget. Prefer primary target/formula with two
    # passages while reserving one per other required source.
    roles = [(FACTS[role][0], role) for role in task.answer_obligations if role in FACTS and FACTS[role][0] in docs]
    if ("D06", "monthly_plan") in roles and ("D06", "target_formula") in roles:
        roles = [("D06", "monthly_bundle"), *[(doc, role) for doc, role in roles if role not in {"monthly_plan", "target_formula"}]]
    sources = [*roles, *((doc, None) for doc in docs if doc not in {d for d, _ in roles})]
    if task.intent == "customer_segmentation" and not any(doc == "D04" and role is None for doc, role in sources):
        sources.append(("D04", None))
    allocation = [0] * len(sources)
    for index in range(min(top_k, len(sources))):
        allocation[index] = 1
    for _ in range(max(0, top_k - len(sources))):
        allocation[0] += 1
    if task.intent == "poster_candidates" and len(sources) >= 2 and top_k >= 2:
        allocation = [max(1, top_k - 2), 2, *allocation[2:]]
    entities = " ".join(e.get("label", "") for e in task.entities)
    queries = []
    for (doc, role), count in zip(sources, allocation):
        if not count:
            continue
        topic = task.retrieval_question or task.normalized_question
        if doc:
            topic = " ".join(DOCUMENT_ROLES[doc]) + " " + entities + " " + topic
            if doc == "D04" and task.intent == "customer_segmentation" and role is None:
                topic = "本规则V 适用于 2025年 第三季度 分层金额按客户 全部音频"
            if doc == "D01" and task.intent == "growth_rate":
                topic = "增长率 period_over_period_growth 本期 基期 " + entities
        if role:
            topic = FACTS[role][1] + " " + entities
        elif source_plan.query_terms:
            topic = " ".join(source_plan.query_terms) + " " + topic
        queries.append((doc, topic[:3900], count, role))
    return queries


def retrieval_task_from_sql(task, sql, profile):
    """Bind a dependent genre lookup to an approved identity from this turn.

    This changes only the retrieval view, not the parent scope, SQL rows or
    references. Unknown/mismatched identities must not become document hints.
    """
    if task.entities or "genre_sales" not in task.business_metric_ids or "ranking" not in task.required_outputs:
        return task
    if sql is None or sql.status != "success" or sql.profile_id != profile.profile_id:
        return task
    if len(sql.sql_results) != 1 or len(sql.sql_results[0].rows) != 1:
        return task  # This seam is a single top genre, not arbitrary row fusion.
    result = sql.sql_results[0]
    row = result.rows[0]
    identity, label = row.get("GenreId"), row.get("genre", row.get("Name"))
    if (identity is not None and type(identity) is not int) or not isinstance(label, str):
        return task
    aliases = [alias for alias in profile.aliases if alias.entity_type == "genre"
               and alias.label.casefold() == label.casefold()]
    if len({alias.entity_id for alias in aliases}) != 1:
        return task
    approved = aliases[0]
    if identity is not None and approved.entity_id != f"Genre:{identity}":
        return task
    from ..tools.references import sql_cell_ref
    linked = task.model_copy(deep=True)
    linked.entities = [{"type": "genre", "id": approved.entity_id, "label": approved.label}]
    # entities are query hints; keep the original information needs/catalog
    # selection unchanged rather than adding extra sources due to the label.
    linked.slot_provenance["retrieval_entity"] = {
        "source": "sql_result", "confirmed": True,
        "source_ref": sql_cell_ref(result.query_id, 0, "GenreId" if identity is not None else "genre" if "genre" in row else "Name"),
    }
    return linked


def retry_query(task, doc_id):
    """One focused repair; preserve all business conditions in filters/query."""
    topic = DOCUMENT_ROLES[doc_id][0]
    period = " ".join(str(task.slots[name]) for name in ("year", "quarter", "month") if name in task.slots)
    scope = str(task.slots.get("scope", ""))
    entities = " ".join(e.get("label", "") for e in task.entities)
    exclusions = "不包括" + "、".join(e.get("label", "") for e in task.excluded_entities) if task.excluded_entities else ""
    return " ".join((topic, period, scope, entities, exclusions)).strip()
