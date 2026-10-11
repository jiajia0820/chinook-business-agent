"""Interpret only a bound pending reply; never grant tool or evidence rights."""
import re
from datetime import date

from .clarification import build_clarification

DIMENSIONS = {"genre": "Genre", "品类": "Genre", "mediatype": "MediaType", "媒体格式": "MediaType",
              "媒体类型": "MediaType", "album": "Album", "所属专辑": "Album", "专辑": "Album",
              "playlist": "Playlist", "播放列表": "Playlist"}


def augment_task(task, question):
    named_entity = re.fullmatch(r"(?:商品名称|商品名|单曲名称|单曲名)\s*[:：=]\s*(.{1,200})", question.strip())
    classification = not named_entity and (bool(re.search(r"(?:属于什么类型|是什么类型|是哪种类型|属于哪(?:个类别|种类型|个类型|类|种)|分类维度)", question)) or (
        bool(re.search(r"TrackId\s*[:：=]?\s*\d+", question, re.I)) and
        not task.business_metric_ids and any(word in question.casefold() for word in DIMENSIONS)))
    if classification:
        task.intent, task.route, task.needs_calculation = "classification_lookup", "rag", False
        task.information_needs, task.required_outputs = ["rag"], ["classification_lookup"]
        task.business_metric_ids = []
    dimensions = {value for word, value in DIMENSIONS.items() if word in question.casefold()} if not named_entity else set()
    if len(dimensions) == 1:
        task.slots["classification_dimension"] = dimensions.pop()
    identity = re.search(r"\bTrackId\s*[:：=]?\s*(\d+)(?!\d|\s*[-–至]\s*\d)", question, re.I)
    if identity and not named_entity:
        task.slots["classification_entity"] = "TrackId:" + identity[1]
    if named_entity:
        name = named_entity[1].strip(" \"'“”。？?")
        if name:
            task.slots["classification_entity"] = "TrackName:" + name
            # A literal title, including years/date words, is not a query period.
            for key in ("year", "quarter", "month", "period_grain", "start_date", "end_date", "comparison_period"):
                task.slots.pop(key, None)
                task.slot_provenance.pop(key, None)
    for key in ("classification_dimension", "classification_entity"):
        if key in task.slots:
            task.slot_provenance[key] = {"source": "explicit", "confirmed": True}
    # Document definitions may mention relative dates without requesting a query.
    # Query dates, however, must never fall back from an invalid quarter to a year.
    if not named_entity and (task.route != "rag" or "高价值客户" in question):
        if re.search(r"Q(?:0|[5-9]|\d{2,})(?!\d)|第?(?:[五六七八九零0]|[5-9]|\d{2,})季度", question, re.I):
            task.time_issues.append("invalid_quarter")
        if task.slots.get("month") is not None and not 1 <= task.slots["month"] <= 12:
            task.time_issues.append("invalid_month")
        if task.slots.get("period_grain") == "range":
            try:
                if date.fromisoformat(task.slots["end_date"]) <= date.fromisoformat(task.slots["start_date"]):
                    raise ValueError("reversed interval")
            except (ValueError, KeyError):
                task.time_issues.append("invalid_interval")
        if re.search(r"今年|去年|本季度|上季度|本月|上月|最近", question):
            task.time_issues.append("relative_period")
    task.time_issues = list(dict.fromkeys(task.time_issues))
    return task


def prepare_reply(question, candidate, memory, profile, specs):
    """Return (possibly augmented candidate, forced action, early response)."""
    base = memory.pending
    if base is None:
        return candidate, None, None
    meta = memory.pending_clarification
    if meta and (meta.get("task_id") != base.task_id or meta.get("task_version") != base.task_version):
        return candidate, None, build_clarification(base, ["context"], specs, reused=True,
            note="待办确认已过期，未应用本次补充；请重新发送完整问题。")
    requested = set(memory.pending_slots)
    text = question.strip().rstrip("。？?")
    action = None
    if "entity_dimension" in requested or "entity" in requested:
        if (candidate.slots.get("classification_dimension") or candidate.slots.get("classification_entity")
                or "entity" in requested and re.match(r"(?:商品名称|商品名|单曲名称|单曲名)\s*[:：=]", text)):
            action = "supplement"
    if "metric" in requested and text in {"目标达成情况", "目标达成", "看目标达成情况"}:
        candidate.intent, candidate.route, candidate.needs_calculation = "target_attainment", "cross_source", True
        candidate.business_metric_ids = []
        candidate.slot_provenance["metric"] = {"source": "unconfirmed", "confirmed": False}
        candidate.information_needs = ["sql", "rag"]
        candidate.source_roles = ["database_actual", "target"]
        candidate.required_outputs = ["actual", "target", "attainment_rate", "verdict"]
        action = "followup"
    if "genre_scope" in requested and candidate.entities and not candidate.business_metric_ids:
        candidate.slots["genre_boundary_resolved"] = True
        action = "supplement"
    if "media_scope" in requested:
        media = None
        if text in {"分别统计", "分开", "分开统计", "分别列示", "分开列示"}:
            candidate.slots["media_aggregation"] = "separate"
            media = sorted(set(profile.default_media_ids) | {3})
        elif text in {"合计", "合并", "合并音视频", "全店合计", "音频和视频合计"}:
            candidate.slots["media_aggregation"] = "aggregate"
            media = sorted(set(profile.default_media_ids) | {3})
        elif text in {"音频", "仅音频"}:
            candidate.slots["media_aggregation"] = "aggregate"
            media = list(profile.default_media_ids)
        elif text in {"视频", "仅视频"}:
            candidate.slots["media_aggregation"] = "aggregate"
            media = [3]
        if media:
            candidate.slots["media_ids"] = media
            candidate.slots["scope"] = candidate.slots["data_scope"] = "全店音视频" if len(media) == 5 else "视频" if media == [3] else "全部音频"
            candidate.intent, candidate.route, candidate.needs_calculation = "fact_query", "sql", False
            candidate.information_needs, candidate.required_outputs = ["sql"], ["actual"]
            action = "followup"
    correction = question.startswith(("改成", "换成", "更正", "改看", "改查"))
    from .turn_update import classify_turn
    if (action is None and classify_turn(question, candidate, memory) == "new"
            and text not in {"是", "确认", "对", "好的", "随便", "你决定", "都可以", "不知道"}):
        return candidate, None, None
    if not correction:
        conflicts = []
        for key in ("year", "quarter", "month", "scope", "classification_dimension", "classification_entity"):
            if key not in candidate.slots or key not in base.slots or candidate.slots[key] == base.slots[key]:
                continue
            allowed = key == "scope" and requested & {"genre_scope", "media_scope"} or key == "classification_dimension" and "entity_dimension" in requested or key == "classification_entity" and "entity" in requested
            if not allowed and base.slot_provenance.get(key, {}).get("confirmed"):
                conflicts.append(key)
        if candidate.slot_provenance.get("metric", {}).get("confirmed") and base.slot_provenance.get("metric", {}).get("confirmed") and candidate.business_metric_ids != base.business_metric_ids and "metric" not in requested:
            conflicts.append("metric")
        if conflicts:
            return candidate, None, build_clarification(base, ["condition_conflict"], specs, reused=True,
                note="已确认条件未被覆盖；冲突项：" + "、".join(conflicts) + "。")
    if text in {"是", "确认", "对", "好的", "随便", "你决定", "都可以", "不知道"}:
        blockers = meta.get("blocking_slots") or memory.pending_slots
        # Old public names are interpreted only in this pending-question context.
        blockers = [{"genre_scope": "genre_boundary", "entity_dimension": "classification_dimension"}.get(s, s) for s in blockers]
        return candidate, None, build_clarification(base, blockers, specs, reused=True,
            note="尚未收到具体选择或条件，未自动填写；请直接选择选项或输入明确值。")
    return candidate, action, None
