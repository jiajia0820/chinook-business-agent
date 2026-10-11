"""Curated table readers bind values to entity, period, unit and exact source span."""
from decimal import Decimal
import hashlib
import re

from ..evidence.binding import task_period, task_scope, comparison_period, result_hash
from ..evidence.models import CalculationEvidenceBundle, DocumentLocator, DocumentScalarEvidence, FormulaEvidence, SqlScalarEvidence


FUNCTIONS={'target_attainment':'attainment_rate','target_difference':'difference','growth_rate':'growth_rate',
    'average_order_value':'average_order_value','document_sum':'sum','period_difference':'difference'}


def locator(chunk,start=0,end=None):
    return DocumentLocator(chunk_id=chunk.chunk_id,doc_id=chunk.doc_id,version='V1.0',
        content_sha256=hashlib.sha256(chunk.text.encode()).hexdigest(),source_uri=chunk.source_uri,
        start=start,end=len(chunk.text) if end is None else end)


def quarterly_targets(chunks,scope,metric_id):
    if scope.country_ids or len(scope.genre_ids)>1:
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


def monthly_plan(chunks,scope):
    if scope.genre_ids or scope.country_ids:
        return []
    results=[]
    for chunk in chunks:
        if chunk.doc_id!='D06' or chunk.page!=2 or '月度分解' not in chunk.text:
            continue
        months=[int(v) for v in re.findall(r'(\d{1,2})月金额',chunk.text)]
        match=re.search(r'(?m)^全部音频\n(?P<a>\d+\.\d+)\n(?P<b>\d+\.\d+)\n(?P<c>\d+\.\d+)\n\d+\.\d+',chunk.text)
        if match and len(months)==3:
            results=[(month,Decimal(match[key]),chunk,match.start(key),match.end(key)) for month,key in zip(months,('a','b','c'))]
            break
    return results


def create_business_resolver(index):
    def resolve(task,sql,rag,calculation_id,context):
        function=FUNCTIONS.get(task.intent)
        if not task.needs_calculation or function is None or not rag or not rag.chunks:
            return None
        metric_id=task.business_metric_ids[0]
        metric=index.profile.metric(metric_id)
        period,scope=task_period(task),task_scope(task)
        chunks=rag.chunks
        formula_words={'difference':('差额','本期销售额 - 上期销售额'), 'attainment_rate':('达成率',),
            'growth_rate':('增长率','period_over_period_growth','环比'), 'average_order_value':('客单价',),'sum':('月度分解',)}[function]
        formula_chunk=next((c for c in chunks if any(word in c.text for word in formula_words)),None)
        if formula_chunk is None:
            return None
        operands={}
        def scalar(result,row,column,*,operand_metric=metric_id,operand_period=period,unit=metric.unit):
            return SqlScalarEvidence(metric_id=operand_metric,period=operand_period,scope=scope,unit=unit,
                value=Decimal(str(result.rows[row][column])).quantize(Decimal('0.000001')),confirmed=True,
                query_id=result.query_id,row_index=row,column=column,result_sha256=result_hash(result))
        if function=='sum':
            if metric_id!='sales_amount':
                return None  # This source table is a monetary plan, not a units plan.
            # The declared operating-table year/quarter must match the requested
            # period; no other quarter inherits this target table.
            title=index.documents['D06'].title
            match=re.search(r'(\d{4})Q([1-4])',title)
            if not match or (int(match[1]),int(match[2]))!=(task.slots.get('year'),task.slots.get('quarter')):
                return None
            for month,value,chunk,start,end in monthly_plan(chunks,scope):
                operands[f'month_{task.slots["year"]}_{month:02}']=DocumentScalarEvidence(metric_id=metric_id,period=period,scope=scope,
                    unit=metric.unit,value=value,confirmed=True,locator=locator(chunk,start,end))
            if len(operands)!=3:
                return None
        else:
            if not sql or not sql.sql_results:
                return None
            result=sql.sql_results[0]
            if function=='average_order_value':
                operands={'sales':scalar(result,0,'sales_amount',operand_metric='sales_amount',unit='USD'),
                    'orders':scalar(result,0,'order_count',operand_metric='order_count',unit='单')}
            elif function=='growth_rate' or task.intent=='period_difference':
                previous=sql.sql_results[1] if len(sql.sql_results)>1 else result
                previous_index=0 if previous is not result else 1
                current=scalar(result,0,metric_id)
                prior=scalar(previous,previous_index,metric_id,operand_period=comparison_period(task))
                operands={'current':current,'previous':prior} if function=='growth_rate' else {'actual':current,'target':prior}
            else:
                title=index.documents['D06'].title
                match=re.search(r'(\d{4})Q([1-4])',title)
                if not match or (int(match[1]),int(match[2]))!=(task.slots.get('year'),task.slots.get('quarter')):
                    return None
                target=quarterly_targets(chunks,scope,metric_id)
                if target is None:
                    return None
                chunk,value,start,end=target
                operands={'actual':scalar(result,0,metric_id),'target':DocumentScalarEvidence(metric_id=metric_id,period=period,
                    scope=scope,unit=metric.unit,value=value,confirmed=True,locator=locator(chunk,start,end))}
        formula=FormulaEvidence(function=function,metric_id=metric_id,period=period,scope=scope,input_unit=metric.unit,
            result_unit=metric.unit if function in {'difference','sum','average_order_value'} else '%',
            confirmed=True,locator=locator(formula_chunk))
        return CalculationEvidenceBundle(request_id=context.request_id,session_id=context.session_id,profile_id=context.profile_id,
            calculation_id=calculation_id,source_mode='verified_business',function=function,operands=operands,formula=formula)
    return resolve
