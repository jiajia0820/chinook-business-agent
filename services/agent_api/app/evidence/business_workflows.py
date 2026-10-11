"""Finite document -> query -> conclusion dependencies; no gold/runtime answers."""
from decimal import Decimal
import re

from backend.sql_module.business_queries import build_query, output_columns
from ..agent.entity_ids import selected
from ..tools.references import sql_cell_ref

WORKFLOWS={'customer_segmentation','poster_candidates'}


class WorkflowGap(ValueError):
    pass


def compact(text):
    return re.sub(r'\s+','',text)


def _unique(values, label):
    if not values:
        raise WorkflowGap('缺少'+label+'，不能形成完整结论。')
    identities={value[0] for value in values}
    if len(identities)!=1:
        raise WorkflowGap(label+'出现相互矛盾的口径，需核对来源。')
    return values[0]


def resolve_sources(task,rag,profile):
    if not task or task.intent not in WORKFLOWS or not task.time_range or not task.slots.get('quarter'):
        raise WorkflowGap('业务流程需要明确的完整季度。')
    if not rag or not rag.chunks:
        raise WorkflowGap('缺少本轮规则或候选资料。')
    genres=[int(e['id'].split(':')[1]) for e in task.entities if e.get('type')=='genre']
    if len(genres)!=1 or len(task.entities)!=1:
        raise WorkflowGap('需确认唯一品类；当前流程不支持叠加地区或其他人群条件。')
    genre=genres[0]
    slots={'start_date':task.time_range.start,'end_date':task.time_range.end,'media_ids':list(profile.default_media_ids)}
    if slots['media_ids']!=task.slots.get('media_ids'):
        raise WorkflowGap('音频范围与已确认配置不一致。')
    period=(task.slots['year'],task.slots['quarter'])
    chunks=rag.chunks
    if task.intent=='customer_segmentation':
        rules=[]; scopes=[]; periods=[]
        for chunk in chunks:
            if chunk.doc_id!='D04': continue
            if '层级 | 名称 | 当季音频销售额 美元' in chunk.text:
                match=re.search(r'^A\s*\|[^\n|]*\|\s*大于等于\s*(\d+(?:\.\d+)?)\s*\|',chunk.text,re.M)
                if match: rules.append((match[1],chunk))
            if '分层金额按客户' in chunk.text and '只取音频明细' in chunk.text and 'InvoiceLine.UnitPrice * InvoiceLine.Quantity' in chunk.text:
                scopes.append(('all_audio_sales_amount',chunk))
            text=compact(chunk.text)
            match=re.search(r'本规则V.*?自(\d{4})年(\d{1,2})月\d{1,2}日起适用于第?([一二三四1234])季度',text)
            if match:
                q=int(match[3]) if match[3].isdigit() else '一二三四'.index(match[3])+1
                periods.append(((int(match[1]),q),chunk))
        threshold,rule=_unique(rules,'D04 A层阈值及美元单位')
        _,scope=_unique(scopes,'D04 全部音频消费计算口径')
        applicable,period_chunk=_unique(periods,'D04 规则适用期间')
        if applicable!=period:
            raise WorkflowGap('D04 规则适用期间与请求季度不一致，不能套用分层。')
        slots['query_intent']={'operation':'customer_cohort','cohort_genre_id':genre}
        return {'intent':task.intent,'slots':slots,'threshold':threshold,'operator':'>=','unit':'USD',
            'metric':'all_audio_sales_amount','rule_ref':rule.chunk_id,'scope_ref':scope.chunk_id,
            'period_ref':period_chunk.chunk_id,'rule_uri':rule.source_uri,'scope_uri':scope.source_uri,
            'period':list(period)}
    main_rules=[]; candidates=[]
    label=next(e['label'] for e in task.entities if e.get('type')=='genre')
    for chunk in chunks:
        text=compact(chunk.text)
        if chunk.doc_id=='D11' and '音频商品' in text and chunk.bbox is not None:
            for value in re.findall(r'GENRE(\d+)/MAIN',text,re.I):
                main_rules.append((int(value),chunk))
        if chunk.doc_id=='D05':
            match=re.search(r'[一二三四五六七八九十]+\s*'+re.escape(label)+r'\s*商品选品\s*([\s\S]*?)(?=[一二三四五六七八九十]+\s*\w+\s*商品选品|$)',chunk.text)
            if not match: continue
            block=match[1]
            stamp=re.search(r'(\d{4})年第?([一二三四1234])季度',block)
            # Supported candidate paragraphs explicitly declare their observation
            # period. Unknown text never becomes an inferred candidate list.
            if not stamp: continue
            quarter=int(stamp[2]) if stamp[2].isdigit() else '一二三四'.index(stamp[2])+1
            if (int(stamp[1]),quarter)!=period: continue
            declared=re.findall(r'Genre\s*:\s*(\d+)',block)
            if str(genre) not in declared: continue
            ids=tuple(sorted(set(int(value) for value in re.findall(r'(\d+)\s*《',block))))
            if ids: candidates.append((ids,chunk))
    main,poster=_unique(main_rules,'D11 音频主页面品类条件及图片定位')
    ids,candidate=_unique(candidates,'D05 对应品类和季度的明确候选名单')
    if main!=genre:
        raise WorkflowGap('请求品类与 D11 主页面条件不一致，不能作为主页面候选。')
    selection=task.slots.get('track_selection')
    if not selection or not (selection['ids'] or selection['ranges']):
        raise WorkflowGap('缺少明确商品编号或范围。')
    matched=[value for value in ids if selected(value,selection)]
    slots['query_intent']={'operation':'document_candidates','candidate_ids':matched,'genre_id':genre}
    return {'intent':task.intent,'slots':slots,'candidate_ref':candidate.chunk_id,'poster_ref':poster.chunk_id,
        'candidate_uri':candidate.source_uri,'poster_uri':poster.source_uri,'document_candidates':list(ids),
        'selected_candidates':matched,'period':list(period)}


def amount(value):
    if type(value) not in (int,float,Decimal): raise WorkflowGap('查询金额不是数值。')
    result=Decimal(str(value))
    if not result.is_finite() or result<0: raise WorkflowGap('查询金额无效。')
    return result.quantize(Decimal('.01'))


def conclude(sources,sql):
    if sources['intent']=='poster_candidates' and not sources['selected_candidates']:
        return {'sources':sources,'decisions':[],'empty_reason':'用户范围与有证据的候选集合无交集。'}
    if not sql or sql.status!='success' or len(sql.sql_results)!=1:
        raise WorkflowGap('必要数据库查询未完成，不能把查询失败当成零成交。')
    result=sql.sql_results[0]
    expected=build_query(sources['slots'])
    if result.status!='success' or result.truncated:
        raise WorkflowGap('数据库查询失败或结果被截断，不能声称完整名单。')
    if result.sql!=expected['sql'] or result.params!=expected['params'] or result.columns!=output_columns(sources['slots']['query_intent']):
        raise WorkflowGap('数据库结果与本轮业务条件/完整消费口径不一致。')
    decisions=[];seen=set()
    for index,row in enumerate(result.rows):
        key='CustomerId' if sources['intent']=='customer_segmentation' else 'TrackId'
        identity=row.get(key)
        if type(identity) is not int or identity<1 or identity in seen:
            raise WorkflowGap('查询对象标识缺失或重复。')
        seen.add(identity)
        orders=row.get('order_count')
        if type(orders) is not int or orders<0: raise WorkflowGap('订单数未正确去重计数。')
        if sources['intent']=='customer_segmentation':
            full=amount(row.get('all_audio_sales_amount'));cohort=amount(row.get('sales_amount'))
            if orders<1 or full<cohort: raise WorkflowGap('人群购买事实与完整消费额不一致。')
            decisions.append({'CustomerId':identity,'customer':row['customer'],'amount':str(full),
                'eligible':full>=Decimal(sources['threshold']),'unit':'USD','rule_ref':sources['rule_ref'],
                'sql_ref':sql_cell_ref(result.query_id,index,'all_audio_sales_amount'),
                'cohort_ref':sql_cell_ref(result.query_id,index,'sales_amount')})
        else:
            sales=amount(row.get('sales_amount'))
            if orders==0 and sales!=0: raise WorkflowGap('成交订单数与成交金额不一致。')
            decisions.append({'TrackId':identity,'Name':row['Name'],'amount':str(sales),'sold':orders>0,
                'unit':'USD','sql_ref':sql_cell_ref(result.query_id,index,'sales_amount'),
                'candidate_ref':sources['candidate_ref'],'poster_ref':sources['poster_ref']})
    if sources['intent']=='poster_candidates' and seen!=set(sources['selected_candidates']):
        raise WorkflowGap('候选集合与数据库目录不一致，缺项不能当成零成交。')
    return {'sources':sources,'decisions':decisions,'query_id':result.query_id,
        'empty_reason':'查询成功，该季度没有符合购买条件的客户。' if not decisions else None}
