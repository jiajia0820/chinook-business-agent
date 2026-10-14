"""Dependency-aware questions; deferred blockers never become execution defaults."""
from ..contracts import Clarification, MissingSlot
from .state import ParsedTurn

PUBLIC_SLOTS = {"classification_dimension": "entity_dimension", "genre_boundary": "genre_scope"}

# A vague request leaves the measure and its observation scope equally undefined.
# Asking only the primary slot would force a second round-trip and invite a guess.
JOINT_SLOT_GROUPS = ({"metric", "scope"},)


def build_clarification(task, missing, specs, *, reused=False, note=None):
    blockers = list(dict.fromkeys(missing))
    if "period" in blockers:
        blockers = [s for s in blockers if s not in {"year", "quarter", "time_range"}]
    # Resolve a choice that defines the meaning of the remaining task first.
    # A hidden period/entity blocker remains in the plan and is rechecked later.
    priorities = ("condition_conflict", "classification_dimension", "genre_boundary", "metric", "intent")
    joint = next((group for group in JOINT_SLOT_GROUPS if group <= set(blockers)), None)
    if joint:
        asked = [name for name in blockers if name in joint]
    else:
        primary = next((s for s in priorities if s in blockers), None)
        asked = [primary] if primary else blockers[:3]
    descriptions = []
    options = []
    for name in asked:
        description, choices = specs.get(name, (name, []))
        if name == "metric" and task and task.intent in {"target_attainment", "target_difference"}:
            description, choices = "要核验的目标指标", ["销售额", "销量", "销售额和销量"]
        if name in {"period", "observation_period"} and task and task.slots.get("year"):
            description = f"已确认{task.slots['year']}年，请明确季度、月份或全年" if name == "period" else f"已确认{task.slots['year']}年，请明确观察季度"
            choices = [f"{task.slots['year']}年第三季度"] if name == "observation_period" else [f"{task.slots['year']}年第三季度", f"{task.slots['year']}年全年"]
        descriptions.append(description)
        options.extend(choices)
    text = "请补充或确认：" + "；".join(descriptions)
    if note:
        text = note + "\n" + text
    return ParsedTurn(decision="clarification", task=task, reused_context=reused,
        blocking_slots=blockers, rules=["dependency_clarification"],
        clarification=Clarification(question=text,
            missing_slots=[MissingSlot(name=PUBLIC_SLOTS.get(name, name), description=description)
                           for name, description in zip(asked, descriptions)],
            options=list(dict.fromkeys([*options, "取消当前问题"]))))
