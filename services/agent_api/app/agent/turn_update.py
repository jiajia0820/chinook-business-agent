"""Version-bound logical patches; no results or execution rights survive."""
import re
from typing import Literal

from pydantic import Field, JsonValue
from ..tools.models import InternalModel


class TaskPatch(InternalModel):
    task_id: str
    expected_version: int
    action: Literal["supplement", "confirm", "correct", "followup"]
    set_slots: dict[str, JsonValue] = Field(default_factory=dict)
    clear_slots: list[str] = Field(default_factory=list)
    replace_genres: bool = False


def classify_turn(question, candidate, memory):
    base = memory.pending or memory.last_task
    if question in {"取消", "取消当前问题", "重新开始", "清空上下文"}:
        return "cancel"
    if question.startswith(("新问题", "另外问", "重新查询")):
        return "new"
    if question.startswith(("改成", "换成", "更正", "改看", "改查")):
        return "correct"
    if question.startswith(("按", "仅", "不包括", "不包含", "排除")):
        return "supplement"
    stripped = re.sub(r"\d{4}年?|第?[一二三四1-4]季度|Q[1-4]|[\s，。?？呢]+", "", question, flags=re.I)
    if not stripped or question in {"是", "确认", "对", "全部音频", "全音频", "全年"}:
        return "confirm"
    if question.startswith(("那", "和目标比", "对比", "环比", "同比")) or question.endswith(("呢？", "呢")):
        return "followup"
    if base and candidate.intent == "growth_rate" and not candidate.slots.get("year") and not candidate.entities:
        return "followup"
    if base and any(w in question for w in ("刚才两期", "这两期", "前面两期")):
        return "followup"
    if memory.pending and (candidate.entities or candidate.slots.get("year") or candidate.slot_provenance.get("metric", {}).get("confirmed")) and len(question) < 35:
        return "supplement"
    if base and "分层" in base.original_question and "A层" in question and "维护" in question:
        return "followup"
    if base and any(w in base.original_question for w in ("报名", "活动")) and any(w in question for w in ("报名", "活动什么时候")):
        return "followup"
    return "new"


def canonical_question(task, topic):
    """Projection of validated fields, not a concatenated chat history."""
    s = task.slots
    year = f"{s['year']}年" if s.get("year") else ""
    period = year + (f"第{'一二三四'[s['quarter'] - 1]}季度" if s.get("quarter") else f"{s['month']}月" if s.get("month") else "全年" if s.get("period_grain") == "year" else "")
    if s.get("period_grain") == "range":
        period = f"{s.get('start_date')}至{s.get('end_date')}（左闭右开）"
    scope = s.get("scope", "、".join(e["label"] for e in task.entities))
    negative = "不包括" + "、".join(e["label"] for e in task.excluded_entities) if task.excluded_entities else ""
    comparison = s.get("comparison_period", {})
    prior = (f"对比{comparison.get('year', '')}年第{comparison['quarter']}季度" if comparison.get("quarter") else "")
    return "；".join(x for x in (period, scope, topic, prior, negative) if x)[:3800]


def update_task(question, candidate, memory, profile, *, force_action=None):
    action = force_action or classify_turn(question, candidate, memory)
    base = memory.pending or memory.last_task
    if action in {"new", "cancel"} or base is None:
        memory.previous_view = None
        return candidate, False, action
    task = base.model_copy(deep=True)
    values = dict(candidate.slots)
    if "视频" not in question and "音频" not in question and "media_aggregation" not in values:
        values.pop("media_ids", None)
    if values.get("period_grain") == "year" and not values.get("quarter") and not values.get("month"):
        if base.slots.get("quarter") or base.slots.get("month"):
            values.pop("period_grain", None)
        elif memory.pending and "全年" not in question:
            values["period_grain"] = "unspecified"
    clear = ["start_date", "end_date", "year_assumed"]
    if values.get("quarter"):
        clear += ["month"]
    if values.get("month"):
        clear += ["quarter"]
    if "全年" in question:
        values["period_grain"] = "year"
        clear += ["quarter", "month"]
    patch = TaskPatch(task_id=base.task_id, expected_version=base.task_version, action=action, set_slots=values,
                      clear_slots=clear, replace_genres=values.get("scope") == "全部音频")
    if patch.task_id != task.task_id or patch.expected_version != task.task_version:
        raise ValueError("stale business patch")
    changing_period = any(values.get(k) is not None and values[k] != base.slots.get(k) for k in ("year", "quarter", "month"))
    if changing_period and base.slots.get("quarter"):
        previous = base.model_copy(deep=True)
        previous.evidence_coverage, previous.subtask_plan = [], []
        memory.previous_view = previous
    for key in patch.clear_slots:
        task.slots.pop(key, None)
        task.slot_provenance.pop(key, None)
    for key in task.slots:
        task.slot_provenance[key] = {**base.slot_provenance.get(key, {}), "source": "inherited"}
    task.slots.update(patch.set_slots)
    for key in patch.set_slots:
        task.slot_provenance[key] = {"source": "explicit", "confirmed": True}
    if candidate.time_issues:
        task.time_issues = candidate.time_issues
    elif (values.get("quarter") or values.get("month") or values.get("start_date") and values.get("end_date")
          or "全年" in question):
        task.time_issues = []
    if candidate.entities:
        task.entities = candidate.entities
    if patch.replace_genres:
        task.entities = [e for e in task.entities if e["type"] != "genre"]
        task.excluded_entities = []
    if candidate.excluded_entities:
        task.excluded_entities = candidate.excluded_entities
    explicit_metric = candidate.slot_provenance.get("metric", {}).get("confirmed")
    if explicit_metric:
        task.business_metric_ids = candidate.business_metric_ids
        task.slot_provenance["metric"] = dict(candidate.slot_provenance["metric"])
    changing_goal = action == "followup" and candidate.intent != "unknown" or base.intent == "unknown" and explicit_metric
    topic = base.retrieval_question or base.original_question
    if changing_goal:
        task.intent, task.route, task.needs_calculation = candidate.intent, candidate.route, candidate.needs_calculation
        task.information_needs, task.required_outputs = candidate.information_needs, candidate.required_outputs
        task.source_roles, task.assumptions = candidate.source_roles, candidate.assumptions
        task.answer_obligations = candidate.answer_obligations
        topic = question
    if action == "followup" and any(w in question for w in ("下一步", "重点看", "维护", "建议")):
        task.intent, task.route, task.needs_calculation = "document_rule", "rag", False
        task.required_outputs, task.information_needs = ["document_facts"], ["rag"]
        topic = question
        task.answer_obligations = candidate.answer_obligations
    if (action == "followup" and "目标" in question and not candidate.needs_calculation
            and not any(w in question for w in ("比", "达标", "差", "达到"))):
        task.intent, task.route, task.needs_calculation = "document_rule", "rag", False
        task.required_outputs, task.information_needs = ["document_facts"], ["rag"]
        topic = question
    if task.intent == "growth_rate":
        prior = memory.previous_view
        if task.slots.get("year") and task.slots.get("quarter") and any(w in question for w in ("同比", "环比")):
            y, q = task.slots["year"], task.slots["quarter"]
            if "同比" in question:
                task.slots["comparison_period"] = {"year": y - 1, "quarter": q}
            elif "环比" in question:
                task.slots["comparison_period"] = {"year": y if q > 1 else y - 1, "quarter": q - 1 if q > 1 else 4}
        elif prior and prior.slots.get("quarter") and prior.slots.get("data_scope") == task.slots.get("data_scope"):
            task.slots["comparison_period"] = {k: prior.slots[k] for k in ("year", "quarter") if k in prior.slots}
        if task.slots.get("year") and task.slots.get("comparison_period") and not task.slots["comparison_period"].get("year"):
            task.slots["comparison_period"]["year"] = task.slots["year"]
    # Keep the shared IR as an incremental semantic view.  Legacy task fields
    # remain authoritative for execution, while the IR preserves confirmed
    # time/entity/metric/output context across short follow-ups.
    if getattr(candidate, "question_ir", None) is not None:
        from .query_rewrite import merge_question_ir
        task.question_ir = merge_question_ir(getattr(base, "question_ir", None), candidate.question_ir)
    topic = re.sub(r"\d{4}-\d{2}-\d{2}|\d{4}\s*年?|第?[一二三四1-4]季度|Q\d+|\d{1,2}月|对比\s*年第\d季度", "", topic, flags=re.I)
    if not task.time_issues:
        topic = re.sub(r"Q\d+|第?[五六七八九零0-9]+季度|今年|去年|本季度|上季度|本月|上月|最近", "", topic, flags=re.I)
    labels = {e["label"] for e in base.entities + base.excluded_entities + task.entities + task.excluded_entities}
    labels.update(alias.alias for alias in profile.aliases)
    for label in sorted(labels, key=len, reverse=True):
        topic = re.sub(re.escape(label), "", topic, flags=re.I)
    topic = re.sub(r"不包括\s*(?=[，；。?？]|$)|不包含\s*(?=[，；。?？]|$)", "", topic)
    task.original_question = task.normalized_question = task.retrieval_question = canonical_question(task, topic)
    task.time_range, task.subtask_plan, task.evidence_coverage = None, [], []
    task.task_version += 1
    return task, True, action
