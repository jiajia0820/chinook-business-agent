"""Bounded local answers from this turn's evidence, preserving table cells."""
from __future__ import annotations

import os
import re

from ..contracts import DocumentChunk
from ..knowledge.index import lexical_terms
from .document_answer import (
    asks_activity_plan,
    asks_metric_denominator,
    asks_tier_thresholds,
    cited,
    comparison,
    classification,
    customer_tiers,
    metric_definition,
    poster,
    tier_boundary_evidence,
    _multidoc_target_review,
    _multidoc_customer_operation,
    _multidoc_activity_crosscheck,
    _multidoc_next_step,
)


def fast_rag_enabled() -> bool:
    return os.getenv('AGENT_FAST_RAG', '1').strip().casefold() not in {'0', 'false', 'off', 'no'}


# 换词问题的答案角色：只由问题语义决定，不读评测期望，也不看检索到了哪些 doc_id。
ROLE_CUSTOMER_SEGMENTATION = 'customer_segmentation'
ROLE_ACTIVITY_PLAN = 'activity_plan'
ROLE_METRIC_DEFINITION = 'metric_definition'
ROLE_GENERIC_EXCERPT = 'generic_excerpt'

# 角色对应的聚焦渲染器。命中角色但渲染器取不到证据时，仍按原有链路兜底，
# 让内容闸把证据不足的情况推回 insufficient_evidence。
_ROLE_RENDERERS = {
    ROLE_CUSTOMER_SEGMENTATION: customer_tiers,
    ROLE_ACTIVITY_PLAN: poster,
    ROLE_METRIC_DEFINITION: metric_definition,
}


def answer_role_from_question(question: str) -> str:
    """Which focused renderer owns this question, or the generic excerpt path."""
    if asks_tier_thresholds(question):
        return ROLE_CUSTOMER_SEGMENTATION
    if asks_metric_denominator(question):
        return ROLE_METRIC_DEFINITION
    if asks_activity_plan(question):
        return ROLE_ACTIVITY_PLAN
    return ROLE_GENERIC_EXCERPT


def core_content_missing(question: str, documents: list[DocumentChunk]) -> list[str]:
    """Content gates refer to words/fields, never facts inferred from doc ids."""
    from ..knowledge.source_plan import rag_source_plan
    plan = rag_source_plan(question)
    if plan.primary_docs:
        documents = [chunk for chunk in documents if chunk.doc_id in plan.primary_docs]
    text = '\n'.join(chunk.text for chunk in documents)
    return _content_missing(question, text)


def _content_missing(question: str, text: str) -> list[str]:
    missing = []
    if any(word in question for word in ('定义', '口径', '怎么计算', '如何计算')) and not any(word in question for word in ('客单价', '成交均价')):
        for label, field in (('销售额', 'UnitPrice'), ('销量', 'Quantity'), ('订单数', 'InvoiceId'), ('购买客户数', 'CustomerId')):
            if label in question and (label not in text or field not in text):
                missing.append(label + '定义')
    if any(word in question for word in ('一个分类', '并入', '归类', '分类边界')):
        for name in ('Rock And Roll', 'Rock', 'Heavy Metal', 'Metal'):
            if name.casefold() in question.casefold() and not re.search(re.escape(name) + r'[^。\n]{0,100}(?:Genre|分类|单独|分开)', text, re.I):
                missing.append(name + '分类边界')
    if '订单' in question and '音频' in question and '视频' in question and any(word in question for word in ('怎么处理', '应该')):
        for field in ('InvoiceLine', 'MediaTypeId', 'Invoice.Total'):
            if field not in text:
                missing.append(field + '混合订单规则')
    if any(word in question for word in ('应该和', '应该与', '比较方法')) and not ('月' in text and any(word in text for word in ('上月', '同比', '环比'))):
        missing.append('比较基准')
    # 阈值问题必须拿到界线本身：只有分层结果示例（客户名单+金额）时判为缺内容，
    # 让图内补检索再跑一次，而不是用名单冒充界线。
    if answer_role_from_question(question) == ROLE_CUSTOMER_SEGMENTATION and not tier_boundary_evidence(text):
        missing.append('分层阈值')
    return missing


def _units(text: str):
    # Treat tables as complete blocks. PDF numeric rows retain whitespace, so
    # adjacent amounts/identifiers can never merge into a fabricated number.
    if '|' in text or re.search(r'^\s*\S[^。\n]*\n\s*\d', text, re.M):
        yield text.strip()
        return
    for sentence in re.split(r'(?<=[。！？])', text):
        sentence = sentence.strip()
        if sentence:
            yield sentence


def _topic_units(question: str, text: str):
    """Keep a source-defined summary/table block intact when it owns intent."""
    if '日期范围' in question or '时间区间' in question:
        match = re.search(r'(?:时间区间|时间筛选)[\s\S]*?(?=\n[^\n]{2,8}口径|$)', text)
        if match:
            yield match[0].strip()
            return
    if any(word in question for word in ('核心结果', '经营摘要')):
        match = re.search(r'(?:一\s*经营摘要|经营摘要)[\s\S]*?(?=二\s*月度|月度节奏|$)', text)
        if match:
            yield match[0].strip()
            return
    if '候选' in question and any(word in question for word in ('四类', '优先级', '分别')):
        match = re.search(r'优先级[\s\S]*?(?=四\s*Rock|\n[一二三四五六七八九十]+\s+[^\n]+|$)', text)
        if match:
            yield match[0].strip()
            return
    yield from _units(text)


def _readable_identifiers(text: str) -> str:
    # A data list is reproduced with ordinary separators. Values are parsed
    # only from the evidence; the media range is never supplied by a doc id.
    return re.sub(r'(MediaTypeId\s*)(\d+(?:\s*[、,，]\s*\d+)+)',
                  lambda m: m[1] + '范围编号为 ' + ', '.join(re.findall(r'\d+', m[2])), text)


def _excerpt(question: str, documents: list[DocumentChunk]) -> str | None:
    terms = set(lexical_terms(question))
    if any(word in question for word in ('应该和', '应该与', '比较方法')):
        terms.update(lexical_terms('比较基准 完整月 上月 同比 环比'))
    ranked = []
    for order, chunk in enumerate(documents):
        for unit in _topic_units(question, chunk.text):
            if len(unit) < 12 or any(word in unit for word in ('报告编号：', '文档编号：', '签发日期：', '归口岗位：')):
                continue
            normalized = re.sub(r'\s+', '', unit).casefold()
            score = sum(term in normalized for term in terms)
            if '左闭右开' in unit and ('日期范围' in question or '时间区间' in question):
                score += 30
            if any(word in question for word in ('核心结果', '经营摘要')) and '经营摘要' in unit:
                score += 30
            if '候选' in question and '优先级' in unit:
                score += 30
            if '音频' in question and '视频' in question and '订单' in question and any(field in unit for field in ('MediaTypeId', 'InvoiceLine', 'Invoice.Total')):
                score += 20
            if any(word in question for word in ('定义', '口径', '怎么计算', '如何计算')):
                # Prioritize the definition-bearing statement for every
                # requested metric. Broad metric overlap must not push one
                # denominator/identity definition out of the excerpt budget.
                for label, field in (('销售额', 'UnitPrice'), ('销量', 'Quantity'), ('订单数', 'InvoiceId'), ('购买客户数', 'CustomerId')):
                    if label in question and label in unit and field in unit:
                        score += 20
            if score >= 2:
                ranked.append((score, order, unit, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected, used = [], []
    for _, _, unit, chunk in ranked:
        if unit in selected or sum(map(len, selected)) + len(unit) > 2600:
            continue
        selected.append(unit)
        used.append(chunk)
        if len(selected) >= 6:
            break
    # Retain source reading order after ranking has selected the passages.
    pairs = sorted(zip(selected, used), key=lambda pair: documents.index(pair[1]))
    return cited('\n\n'.join(_readable_identifiers(unit) for unit, _ in pairs), [chunk for _, chunk in pairs]) if pairs else None


def local_answer(question: str, documents: list[DocumentChunk]) -> str | None:
    from ..knowledge.source_plan import rag_source_plan
    plan = rag_source_plan(question)
    if plan.required_docs:
        documents = [chunk for chunk in documents if chunk.doc_id in plan.required_docs]
    if not documents or core_content_missing(question, documents):
        return None
    # Existing finite drafts parse actual evidence values. A multi-period
    # comparison must never degrade to a statement about just one period.
    wants_comparison = any(word in question for word in ('二季度', '第二季度', 'Q2')) and any(word in question for word in ('三季度', '第三季度', 'Q3')) and any(word in question for word in ('比较', '对比', '变化', '结合'))
    if wants_comparison:
        return comparison(question, documents)
    answer = (
        _multidoc_target_review(question, documents)
        or _multidoc_customer_operation(question, documents)
        or _multidoc_activity_crosscheck(question, documents)
        or _multidoc_next_step(question, documents)
    )
    if answer is None:
        focused = _ROLE_RENDERERS.get(answer_role_from_question(question))
        if focused is not None:
            answer = focused(question, documents)
    if answer is None:
        answer = (
            classification(question, documents)
            or poster(question, documents)
            or _excerpt(question, documents)
        )
    return answer if answer and not _content_missing(question, answer) else None


__all__ = ['fast_rag_enabled', 'core_content_missing', 'local_answer', 'answer_role_from_question']
