"""Finite business fact contracts, extracted only from retrieved source text."""
import re
import unicodedata


# Named document-role contracts keep multi-document answers tied to the
# source that owns each fact.  The mapping is serializable for traces and
# coverage reports; it is deliberately independent from retrieval ranking.
DOCUMENT_ROLES = {
    "target_review_activity": {"D06": "target", "D08": "actual_review", "D10": "operation"},
    "customer_category_action": {"D04": "segmentation", "D09": "category_operation"},
    "activity_material_crosscheck": {"D10": "schedule_and_version", "D11": "registration_material"},
    "metric_method_crosscheck": {"D01": "metric_definition", "D02": "analysis_method"},
}


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
    if all(word in q for word in ("目标", "复盘", "活动")) and any(doc in q for doc in ("D06", "D08", "D10")):
        roles.append("target_review_activity")
    if "客户" in q and "分层" in q and "Rock" in question and any(word in q for word in ("结合", "运营", "动作")):
        roles.append("customer_category_action")
    if "方案" in q and "海报" in q and any(word in q for word in ("核对", "共同")):
        roles.append("activity_material_crosscheck")
    if any(word in q for word in ("指标定义", "指标口径")) and any(word in q for word in ("比较方法", "应该和", "应该与")):
        roles.append("metric_method_crosscheck")
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


def _document_role_matches(role, doc_id, text):
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    if role == "target_review_activity":
        return {
            "D06": "Rock" in text and "销售额" in t and ("目标" in t or "季度目标" in t),
            "D08": "Rock" in text and "销售额" in t and any(word in t for word in ("实际", "复盘", "达成")),
            "D10": "Rock主页面" in t and any(word in t for word in ("Metal延伸", "延伸栏目")),
        }.get(doc_id, False)
    if role == "customer_category_action":
        return {
            "D04": all(word in t for word in ("阈值", "A", "B", "C")),
            "D09": all(word in t for word in ("Rock", "成交商品", "专辑", "专题陈列")),
        }.get(doc_id, False)
    if role == "activity_material_crosscheck":
        return {
            "D10": "2025年10月20日至2025年11月2日" in t and "版本" in t,
            "D11": "2025年10月13日" in t and all(word in t for word in ("商品清单", "选品理由", "页面文案")),
        }.get(doc_id, False)
    if role == "metric_method_crosscheck":
        return {
            "D01": "销售额" in t and any(word in t for word in ("UnitPrice", "指标", "口径")),
            "D02": any(word in t for word in ("比较", "完整月", "上月", "同比", "环比")),
        }.get(doc_id, False)
    return False


def coverage(task, chunks):
    results = []
    for role in task.answer_obligations:
        if role in DOCUMENT_ROLES:
            refs = [
                c.chunk_id for c in chunks
                if c.doc_id in DOCUMENT_ROLES[role] and _document_role_matches(role, c.doc_id, c.text)
            ]
            expected_docs = set(DOCUMENT_ROLES[role])
            covered_docs = {
                c.doc_id for c in chunks
                if c.doc_id in expected_docs and _document_role_matches(role, c.doc_id, c.text)
            }
            results.append({"role": role, "complete": covered_docs == expected_docs, "source_refs": refs})
        elif role in FACTS:
            refs = [c.chunk_id for c in chunks if c.doc_id == FACTS[role][0] and fact_matches(role, c.text)]
            results.append({"role": role, "complete": bool(refs), "source_refs": refs})
    return results
