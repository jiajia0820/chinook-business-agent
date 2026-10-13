"""Finite business fact contracts, extracted only from retrieved source text."""
import re
import unicodedata


FACTS = {
    "customer_thresholds": ("D04", "当季音频销售额 层级 大于等于 至 以下"),
    "customer_maintenance": ("D04", "A 重点维护 联络频率 主要动作 每月至少一次"),
    "registration_materials": ("D11", "报名材料 商品清单 选品理由 文案"),
    "registration_deadline": ("D11", "报名截止 时间"),
    "activity_period": ("D10", "拟执行周期 活动日期"),
    "poster_activity_period": ("D11", "活动周期"),
    "activity_position": ("D10", "Metal 延伸选择 相关专辑 页面文字"),
    "candidate_tracks": ("D05", "候选 TrackId 已成交 商品"),
    "primary_page_rule": ("D11", "主页面 Rock 音频 商品"),
    "customer_upgrade": ("D04", "升级条件 客户支持岗确认 观察对象"),
    "monthly_plan": ("D06", "附件2 月度分解 7月金额 8月金额 9月金额 全部音频"),
    "target_formula": ("D06", "销售额差额 销售额达成率 实际销售额 目标"),
    "reported_monthly_actual": ("D08", "月度节奏 7月 8月 9月 音频销售额"),
    "monthly_bundle": ("D06", "月度分解 7月金额 8月金额 9月金额 全部音频 销售额差额 销售额达成率"),
}


def answer_obligations(question):
    q = question.replace(" ", "")
    roles = []
    if any(w in q for w in ("客户如何分层", "客户怎么分层", "客户分层", "属于A层")) or ("客户" in q and any(w in q for w in ("A、B、C", "各层级阈值"))):
        roles.append("customer_thresholds")
    if "维护" in q and any(w in q for w in ("A层", "客户", "分层")):
        roles.append("customer_maintenance")
    if any(w in q for w in ("报名要交", "提交材料", "报名材料", "报名需要")):
        roles.append("registration_materials")
    if "报名" in q and "截止" in q:
        roles.append("registration_deadline")
    if any(w in q for w in ("活动什么时候举行", "活动周期", "活动时间")):
        roles += ["activity_period", "poster_activity_period"]
    if "Metal" in question and any(w in q for w in ("位置", "安排在哪", "放在哪")):
        roles.append("activity_position")
    if "选品" in q and any(w in q for w in ("成交", "主页面", "候选")) and not re.search(r'A[、/,]B[、/,]C[、/,]D', q, re.I):
        roles.append("candidate_tracks")
    if "主页面" in q:
        roles.append("primary_page_rule")
    if "属于A层" in q:
        roles.append("customer_upgrade")
    if "每月" in q and "计划" in q and any(w in q for w in ("实际", "查数", "数据库")):
        roles += ["monthly_plan", "target_formula"]
        roles.append("reported_monthly_actual")
    return list(dict.fromkeys(roles))


def fact_matches(role, text):
    if role == "monthly_bundle":
        return fact_matches("monthly_plan", text) or fact_matches("target_formula", text)
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    if role == "customer_thresholds":
        return all(w in t for w in ("大于等于", "以下", "音频销售额")) and bool(re.search(r"A\|.*B\|.*C\|", t))
    if role == "customer_maintenance":
        return all(w in t for w in ("联络频率", "主要动作", "每月至少一次", "定向推荐"))
    if role == "registration_materials":
        return all(w in t for w in ("商品清单", "选品理由", "文案"))
    if role == "registration_deadline":
        return "截止" in t and bool(re.search(r"\d{1,2}[.月/-]\d{1,2}.*\d{1,2}[:：]\d{2}", t))
    if role in {"activity_period", "poster_activity_period"}:
        return ("周期" in t or "活动时间" in t) and bool(re.search(r"\d{1,2}[.月/-]\d{1,2}.*(?:至|[-—~～]).*\d{1,2}[.月/-]\d{1,2}", t))
    if role == "activity_position":
        return "Metal" in text and any(w in t for w in ("延伸选择", "相关专辑", "延伸栏目"))
    if role == "candidate_tracks":
        return bool(re.search(r"TrackId\s*\d+", text, re.I)) and ("候选" in text or "成交" in text)
    if role == "primary_page_rule":
        return "Rock" in text and "音频" in text and "主页面" in text
    if role == "customer_upgrade":
        return "升级条件" in t and "客户支持岗确认" in t
    if role == "monthly_plan":
        return all(w in t for w in ("月度分解", "7月金额", "8月金额", "9月金额"))
    if role == "target_formula":
        return "销售额差额" in t and "销售额达成率" in t
    if role == "reported_monthly_actual":
        return "月度节奏" in t and "8月销售额" in t
    return False


def coverage(task, chunks):
    return [{"role": role, "complete": bool(refs := [c.chunk_id for c in chunks
                if c.doc_id == FACTS[role][0] and fact_matches(role, c.text)]), "source_refs": refs}
            for role in task.answer_obligations if role in FACTS]
