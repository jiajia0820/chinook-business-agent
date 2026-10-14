"""Curated table readers bind values to entity, period, unit and exact source span."""
from decimal import Decimal
import hashlib
import re

from ..evidence.models import DocumentLocator
from ..evidence.cross_source_bundle import build_cross_source_bundle
from ..evidence.formulas import FormulaRegistry


FUNCTIONS={'target_attainment':'attainment_rate','target_difference':'difference','growth_rate':'growth_rate',
    'average_order_value':'average_order_value','document_sum':'sum','period_difference':'difference'}


def locator(chunk,start=0,end=None):
    return DocumentLocator(chunk_id=chunk.chunk_id,doc_id=chunk.doc_id,version='V1.0',
        content_sha256=hashlib.sha256(chunk.text.encode()).hexdigest(),source_uri=chunk.source_uri,
        start=start,end=len(chunk.text) if end is None else end)


def quarterly_targets(chunks,scope,metric_id):
    if metric_id not in {'sales_amount', 'units_sold'} or scope.country_ids or len(scope.genre_ids)>1:
        return None
    if getattr(scope, 'media_ids', [1, 2, 4, 5]) != [1, 2, 4, 5]:
        return None
    identity=scope.genre_ids[0] if scope.genre_ids else '全部音频分类'
    pattern=r'(?m)^([^\n]+)\n(?P<identity>Genre:\d+|全部音频分类)\n(?P<sales>\d+(?:\.\d+)?)\n(?P<units>\d+)\s*$'
    for chunk in chunks:
        if chunk.doc_id!='D06' or chunk.page!=1:
            continue
        for match in re.finditer(pattern,chunk.text):
            if match['identity']==identity:
                column='units' if metric_id=='units_sold' else 'sales'
                return chunk,Decimal(match[column]),match.start(column),match.end(column)
    return None


def monthly_plan(chunks,scope,metric_id='sales_amount'):
    """Read one named D06 monthly table without substituting money for units."""
    if metric_id not in {'sales_amount', 'units_sold'} or scope.country_ids or len(scope.genre_ids)>1:
        return []
    if getattr(scope, 'media_ids', [1, 2, 4, 5]) != [1, 2, 4, 5]:
        return []
    names = {'Genre:1': 'Rock', 'Genre:3': 'Metal', 'Genre:7': 'Latin'}
    identity = names.get(scope.genre_ids[0]) if scope.genre_ids else '全部音频'
    if identity is None:
        return []
    column = '金额' if metric_id == 'sales_amount' else '销量'
    number = r'\d+(?:\.\d{1,6})?' if metric_id == 'sales_amount' else r'\d+'
    for chunk in chunks:
        if chunk.doc_id!='D06' or chunk.page!=2:
            continue
        headers = list(re.finditer(rf'(?m)^(\d{{1,2}})月{column}[^\n]*$', chunk.text))
        months = [int(header[1]) for header in headers]
        if len(months) != 3 or months != list(range(months[0], months[0] + 3)) or months[0] not in {1, 4, 7, 10}:
            continue
        units = {'', '美元', 'USD'} if metric_id == 'sales_amount' else {'', '件'}
        if any(header[0].split(column, 1)[1].strip() not in units for header in headers):
            continue
        table_start = headers[-1].end()
        next_table = re.search(r'(?m)^统计范围\s*$', chunk.text[table_start:])
        table_end = table_start + next_table.start() if next_table else len(chunk.text)
        pattern = rf'(?m)^{re.escape(identity)}\n(?P<a>{number})\n(?P<b>{number})\n(?P<c>{number})\n{number}\s*$'
        match = re.search(pattern, chunk.text[table_start:table_end])
        if match:
            return [(month, Decimal(match[key]), chunk, table_start + match.start(key), table_start + match.end(key))
                for month, key in zip(months, ('a', 'b', 'c'))]
    return []


def target_period_matches(chunk,task):
    """A month uses its real calendar quarter; no synthetic task slot is needed."""
    stamp = re.search(r'(\d{4})Q([1-4])', chunk.title)
    month = task.slots.get('month')
    quarter = (month - 1) // 3 + 1 if month is not None else task.slots.get('quarter')
    return bool(stamp and (int(stamp[1]), int(stamp[2])) == (task.slots.get('year'), quarter))


def create_business_resolver(index):
    try:
        formula_registry = FormulaRegistry.from_profile("chinook")
    except (FileNotFoundError, ValueError):
        formula_registry = None

    def resolve(task,sql,rag,calculation_id,context):
        function=FUNCTIONS.get(task.intent)
        if not task.needs_calculation or function is None or not rag or not rag.chunks:
            return None
        if len(task.business_metric_ids) != 1 or index.profile.metric(task.business_metric_ids[0]) is None or formula_registry is None:
            return None
        try:
            bundle = build_cross_source_bundle(task, sql, rag,
                calculation_id=calculation_id, request_id=context.request_id,
                session_id=context.session_id, profile_id=context.profile_id,
                formula_registry=formula_registry)
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        required = {'actual', 'target'} if function in {'difference', 'attainment_rate'} else {'current', 'previous'} if function == 'growth_rate' else {'sales', 'orders'} if function == 'average_order_value' else set(bundle.operands)
        return bundle if required <= set(bundle.operands) and (function != 'sum' or len(bundle.operands) == 3) else None
    return resolve
