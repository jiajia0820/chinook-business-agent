"""Frozen stage2 business scorer. Evaluation expectations never enter runtime inputs."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

VERSION = 'stage2-policy-1.5'
BASIC_EXTENSION_IDS = {f'basic-nl2sql-{i:03}' for i in range(11,16)}
ALIASES = {
    'customer_count': ('customer_count', 'purchasing_customers'),
    'actual_sales': ('actual_sales', 'sales_amount'),
    'rock_sales': ('rock_sales', 'sales_amount'),
    'q3_sales': ('q3_sales', 'sales_amount'),
}

# Evaluation-only semantic assertions supplement the official prose requirements.
# Each inner tuple is an OR; every tuple in a case must be present in the answer.
CONCEPTS = {
    'basic-rag-001': [('UnitPrice', '单价'), ('Quantity', '数量'), ('InvoiceId', '订单'), ('CustomerId', '客户'), ('去重', '不同')],
    'basic-rag-002': [('左闭右开',)],
    'basic-rag-003': [('Rock And Roll',), ('分开', '不是', '不同')],
    'basic-rag-004': [('音频',), ('视频',)],
    'basic-rag-005': [('A',), ('B',), ('C',)],
    'basic-rag-006': [('主位',), ('组合', '延伸'), ('观察',), ('核对',)],
    'basic-rag-007': [('Rock',), ('Metal',), ('Latin',), ('全音频', '全部音频')],
    'basic-rag-008': [('增加', '增长'), ('订单',), ('客户',)],
    'basic-rag-009': [('销售额',), ('销量',), ('未', '没有')],
    'basic-rag-010': [('商品清单',), ('选品理由',), ('页面文案',), ('商品内容', '品类运营')],
    'multidoc-001': [('销售额',), ('订单',), ('客户',)],
    'multidoc-002': [('目标',), ('实际', '达成'), ('Rock',), ('Metal',), ('主',), ('附属', '补充')],
    'multidoc-003': [('分层', 'A/B/C', 'A、B、C'), ('专辑',), ('覆盖',)],
    'multidoc-004': [('Rock And Roll',), ('Heavy Metal',), ('分开', '单独', '不能', '不自动')],
    'multidoc-005': [('商品清单',), ('选品理由',), ('页面文案',), ('一致', '核对', '相同')],
    'special-multimodal-001': [('活动',), ('截止',)],
    'special-multimodal-002': [('商品内容', '品类运营'), ('商品清单',), ('选品理由',), ('页面文案',)],
    'special-multimodal-003': [('Metal',), ('销售额',), ('销量',)],
    'special-multimodal-004': [('销售额',), ('销量',)],
    'special-multimodal-005': [('Rock',), ('Metal',), ('主',), ('附属', '补充', '延伸'), ('专辑',)],
    'special-alias-001': [('Rock',), ('Rock And Roll',), ('不', '分开')],
    'special-alias-002': [('Rock And Roll',), ('Rock',), ('分开', '不同', '两个目录条目')],
    'special-alias-003': [('Metal',), ('Heavy Metal',), ('单独', '分开', '另有')],
    'special-alias-004': [('Heavy Metal',), ('Metal',), ('不', '分开')],
    'special-alias-005': [('音频',), ('视频',), ('不', '排除', '单独')],
    'special-alias-006': [('全店',), ('音频',), ('视频',)],
    'special-alias-007': [('Heavy Metal',), ('Metal',), ('不', '分开')],
    'special-alias-008': [('Latin',)],
    'cross-001': [('未', '没有'), ('D08',)],
    'cross-002': [('销售额',), ('销量',), ('未', '没有')],
    'cross-003': [('Rock',), ('专辑',), ('覆盖',)],
    'cross-004': [('全音频', '全部音频'), ('A',)],
    'cross-005': [('增长',), ('目标',), ('分开', '分别', '不同', '不能')],
    'cross-006': [('1791',), ('1793',), ('1795',), ('1797',), ('1799',)],
    'cross-007': [('Metal',), ('附属', '补充'), ('Rock',)],
    'cross-008': [('Latin',), ('组合',), ('客户',)],
    'cross-009': [('冲突', '不一致'), ('112.86',), ('36.63',), ('38',), ('40',), ('42',)],
    'cross-010': [('Metal',), ('Heavy Metal',), ('不', '分开')],
    'session-001-t3': [('Rock',), ('专辑',), ('覆盖',)],
    'session-004-t3': [('每月',), ('已购',), ('推荐',)],
    'session-005-t2': [('商品清单',), ('选品理由',), ('页面文案',)],
    'special-negative-001': [('成本',), ('利润',), ('没有', '缺少', '不支持')],
    'special-negative-002': [('播放',), ('没有', '缺少', '不支持')],
    'special-negative-003': [('库存',), ('没有', '缺少', '不支持')],
    'special-negative-004': [('点击', '转化'), ('事件', '日志', '没有')],
    'special-negative-005': [('SupportRep', '支持负责人'), ('维护',), ('销售',), ('不', '不能')],
}
COUNT_KEYS = {'units_sold', 'units', 'order_count', 'customer_count', 'purchasing_customers', 'track_count', 'mixed_order_count', 'orders', 'actual_units', 'target_units'}


def number_equal(want, got, *, integer=False):
    if isinstance(want, bool) or isinstance(got, bool):
        return type(want) is type(got) and want == got
    try:
        a, b = Decimal(str(want)), Decimal(str(got))
        return a.is_finite() and b.is_finite() and (a == b if integer else abs(a-b) <= Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return False


def rows(response):
    return [row for result in response.sql_results if str(result.status) == 'success' for row in result.rows]


def row_problems(want, got):
    missing = []
    for key, value in want.items():
        actual = next((got[name] for name in ALIASES.get(key, (key,)) if name in got), None)
        if actual is None:
            missing.append(f'缺少列 {key}')
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not number_equal(value, actual, integer=key in COUNT_KEYS or key.endswith('Id')):
                missing.append(f'{key}: 期望 {value}，实际 {actual}')
        elif str(value) != str(actual):
            missing.append(f'{key}: 期望 {value!r}，实际 {actual!r}')
    return missing


def compare_rows(case, response):
    gold = case.get('gold_result')
    if gold is None:
        return []
    expected = gold if isinstance(gold, list) else [gold]
    # Composite cross-source results are checked as answer/calculation facts.
    if not isinstance(gold, list) and case.get('interface_expectation', {}).get('expected_route') == 'cross_source':
        return facts_missing(gold, response)
    actual = rows(response)
    if len(expected) != len(actual):
        return [f'结果行数 {len(actual)}，期望 {len(expected)}']
    if case.get('id') in BASIC_EXTENSION_IDS:
        # New questions explicitly require month/ID/tie order and cent-correct
        # zeros. Historical 001-010 retain their existing scoring unchanged.
        problems = []
        for index, (want, got) in enumerate(zip(expected, actual), 1):
            problems.extend(f'第{index}行：{p}' for p in row_problems(want, got))
            for key in ('sales_amount', 'unit_price'):
                if key in want:
                    try:
                        value = Decimal(str(got.get(key)))
                        correct = not isinstance(got.get(key), bool) and value.is_finite() and abs(value-Decimal(str(want[key]))) <= Decimal('0.000001')
                    except (InvalidOperation, TypeError, ValueError):
                        correct = False
                    if not correct:
                        problems.append(f'第{index}行：{key}未达到标准金额精度，期望{want[key]}，实际{got.get(key)}')
        return list(dict.fromkeys(problems))
    ordered = bool(re.search(r'ORDER\s+BY', case.get('gold_sql') or '', re.I)) and bool(re.search(r'最高|排名|前\s*\d|Top|五个|五位|五张', case.get('question', ''), re.I))
    if ordered:
        # Equal measure values allow permutation within a tied rank only.
        measures = [key for key in expected[0] if key in {'sales_amount', 'order_count', 'customer_count'}]
        remaining = list(actual)
        for index, want in enumerate(expected):
            matches = [i for i, got in enumerate(remaining) if not row_problems(want, got)]
            if not matches:
                return [f'第{index+1}行不匹配：{want}']
            got = actual[index]
            if measures and any(not number_equal(want[k], got.get(k)) for k in measures):
                return ['排名未按业务指标排序']
            remaining.pop(matches[0])
        return []
    remaining = list(actual)
    for want in expected:
        index = next((i for i, got in enumerate(remaining) if not row_problems(want, got)), None)
        if index is None:
            return [f'缺少期望记录：{want}']
        remaining.pop(index)
    return []


def _fact_present(value, text, *, integer=False):
    if isinstance(value, str):
        # A formula's result is its RHS, never the first input number.
        value = value.rsplit('=', 1)[-1].strip()
        match = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*(%|件|美元|USD|单)?', value)
        if not match:
            return re.sub(r'\s+', '', value).casefold() in re.sub(r'\s+', '', text).casefold()
        numeric, unit = match.groups()
        if unit == '%':
            return any(number_equal(numeric, n) for n in re.findall(r'(?<![\d.])(-?\d+(?:\.\d+)?)\s*%', text))
        value = numeric
    # IDs/dates/source references cannot supply a business measure.
    text = re.sub(r'\bD\d+\b|\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?|(?:TrackId|Genre|MediaTypeId|CustomerId)\s*[:：]?\s*\d+', '', text, flags=re.I)
    return any(number_equal(value, n, integer=integer) for n in re.findall(r'(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])', text))


def facts_missing(gold, response, prefix=''):
    text = response.answer or ''
    # Structured calculations are only valid as facts with the correct units.
    text += '\n' + '\n'.join(f'{c.result} {c.unit}' for c in response.calculations)
    missing = []
    for key, value in gold.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            for binding, amount in value.items():
                # Nested month evidence must preserve the binding, not just a number.
                bound_rows = [r for r in rows(response) if r.get('month') == binding]
                if not any(number_equal(amount, n) for r in bound_rows for n in r.values() if isinstance(n, (int,float))) and not re.search(re.escape(binding) + r'[^\n]{0,80}' + re.escape(str(amount)), text):
                    missing.append(f'{path}.{binding}={amount}')
        elif isinstance(value, list):
            missing.extend(f'{path}={item}' for item in value if not _fact_present(item, text))
        elif key == 'conflict':
            if not any(word in text for word in ('冲突', '不一致')):
                missing.append('未解释来源冲突')
        elif not _fact_present(value, text, integer=key in COUNT_KEYS):
            missing.append(f'{path}={value}')
    return ['答案/计算缺少事实：' + '、'.join(missing)] if missing else []


def compare_answer(case, response):
    problems = []
    if case['id'] in BASIC_EXTENSION_IDS:
        expectation = case.get('interface_expectation', {})
        for field in expectation.get('required_response_fields', []):
            if not hasattr(response, field):
                problems.append(f'缺少响应字段 {field}')
        if str(response.route) != expectation.get('expected_route'):
            problems.append(f'route: 期望sql，实际{response.route}')
        for result in response.sql_results:
            if str(result.status) != 'success' or result.truncated:
                problems.append('SQL结果未成功完成或被截断')
        period = getattr(response, 'time_range', None)
        expected_period = None if case['id'].endswith('011') else (('2025-01-01','2026-01-01') if case['id'].endswith('012') else ('2025-07-01','2025-10-01'))
        if expected_period is None and period is not None:
            problems.append('商品编号属性查询不应被识别为年份期间')
        elif expected_period is not None and (period is None or (period.start, period.end) != expected_period or period.end_inclusive):
            problems.append(f'查询期间错误，期望左闭右开{expected_period}')
    text = response.answer or ''
    for alternatives in CONCEPTS.get(case['id'], []):
        if not any(term.casefold() in text.casefold() for term in alternatives):
            problems.append('答案缺少必要信息：' + '/'.join(alternatives))
    # Document and session prose gold contains factual values. SQL identity/metric
    # fields are already checked by rows; clarification prose is not an answer.
    gold = case.get('gold_answer') or ''
    if not case.get('gold_result') and not case.get('gold_calculation'):
        # Match complete dates/time literally (space and Chinese date delimiters equivalent).
        compact = re.sub(r'\s+', '', text)
        compact = re.sub(r'(\d{4})[.](\d{1,2})[.](\d{1,2})',lambda m:f'{m[1]}年{int(m[2])}月{int(m[3])}日',compact)
        for year, month, day in re.findall(r'(\d{4})年(\d{1,2})月(\d{1,2})日', gold):
            patterns = [f'{year}年{int(month)}月{int(day)}日', f'{year}-{int(month):02}-{int(day):02}']
            # A poster may print the year on its activity date and omit it on
            # the deadline. Require both the year and the complete month/day.
            inherited_year = year in compact and f'{int(month)}月{int(day)}日' in compact
            if not any(token in compact for token in patterns) and not inherited_year:
                problems.append(f'答案缺少完整日期 {year}-{int(month):02}-{int(day):02}')
        for month, day in re.findall(r'(?<!\d)(\d{1,2})月(\d{1,2})日', gold):
            if not re.search(fr'(?:{int(month)}月{int(day)}日|(?:\d{{4}}-)?{int(month):02}-{int(day):02}|{int(month)}\.{int(day)})', compact):
                problems.append(f'答案缺少月日 {month}月{day}日')
        for token in re.findall(r'\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}', gold):
            if token not in compact:
                problems.append(f'答案缺少日期/时间 {token}')
        clean_gold = re.sub(r'\bD\d+\b|\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?|\d{1,2}月(?:\d{1,2}日)?|\d{1,2}:\d{2}|Genre\s*[:：]\s*\d+', '', gold, flags=re.I)
        for numeric, percent in re.findall(r'(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)(\s*%)?(?![A-Za-z0-9_.])', clean_gold):
            if not _fact_present(numeric + ('%' if percent else ''), text):
                problems.append(f'答案缺少业务数值 {numeric}{"%" if percent else ""}')
        for entity, ident in re.findall(r'(Genre)\s*[:：]\s*(\d+)', gold):
            if not re.search(r'Genre\s*(?:Id)?\s*[:：]?\s*' + ident + r'(?!\d)', text, re.I):
                problems.append(f'答案缺少实体标识 {entity}:{ident}')
    return problems


def workflow_assertions(case,response):
    """Frozen evaluation-only checks for final decisions, not just SQL intermediates."""
    problems=[]
    if case['id']=='cross-004':
        expected=json.loads((Path(__file__).with_name('stage2_cross_assertions.json')).read_text(encoding='utf-8'))
        got={str(r.get('CustomerId')):r for r in rows(response)}
        for identity,value in expected['all_audio_amounts'].items():
            if identity not in got or not number_equal(value,got[identity].get('all_audio_sales_amount')):
                problems.append(f'客户 {identity} 缺少正确的当季全部音频金额 {value}')
        lines={int(m[1]):m[2] for m in re.finditer(r'^CustomerId\s+(\d+)[^\n]*?：([^\n]+)',response.answer or '',re.M)}
        if set(lines)!=set(expected['eligible_ids']):
            problems.append('最终A层名单不完整或含错误客户')
        for identity in expected['eligible_ids']:
            line=lines.get(identity,'')
            if not re.search(r'全部音频[^\n]*?'+re.escape(f"{expected['all_audio_amounts'][str(identity)]:.2f}")+r'\s*USD',line) or not re.search(r'(?:≥|>=)\s*12(?:\.00)?\s*USD',line) or '阈值' not in line or 'sql-cell:' not in line:
                problems.append(f'客户 {identity} 的判定缺少金额/单位/阈值或本轮SQL单元格绑定')
        rule='\n'.join(c.text for c in response.documents if c.doc_id=='D04')
        if not re.search(r'A\s*\|[^\n|]*\|\s*大于等于\s*12(?:\.00)?',rule):
            problems.append('缺少D04原始A层规则行')
        if not response.time_range or [response.time_range.start,response.time_range.end]!=expected['period']:
            problems.append('客户分层期间不正确')
    elif case['id']=='cross-006':
        for row in rows(response):
            identity=row.get('TrackId')
            match=re.search(r'^TrackId\s+'+str(identity)+r'，([^\n]+)',response.answer or '',re.M)
            if not match or not re.search(r'有成交[^\n]*0\.99\s*USD',match[1]) or not all(s in match[1] for s in ['sql-cell:','候选引用','主页面条件引用']):
                problems.append(f'商品 {identity} 缺少成交事实/金额及候选、海报引用绑定')
        if not response.time_range or (response.time_range.start,response.time_range.end)!=('2025-07-01','2025-10-01'):
            problems.append('海报候选成交期间不正确')
        if not any(c.doc_id=='D11' and c.bbox is not None for c in response.documents):
            problems.append('海报条件缺少图片区域定位')
    return problems


def interface_diagnostics(case, response):
    want = case.get('interface_expectation', {}).get('expected_route')
    return [f'route: 期望 {want}，实际 {response.route}'] if want and str(response.route) != want else []


def judge(case, response):
    expectation = case.get('interface_expectation', {})
    want_status = expectation.get('expected_status', 'answered')
    if str(response.status) != want_status:
        return [f'status: 期望 {want_status}，实际 {response.status}']
    problems = []
    for field in expectation.get('required_nonempty_fields', []):
        if getattr(response, field, None) in (None, '', [], {}):
            problems.append(f'必填字段 {field} 为空')
    if want_status == 'clarification_required':
        want = set((expectation.get('expected_clarification') or {}).get('missing_slots', []))
        got = {s.name for s in response.clarification.missing_slots} if response.clarification else set()
        # The official generic required_context means requesting concrete context
        # slots; explicit specialized slots otherwise remain a strict contract.
        if want == {'required_context'}:
            if not got:
                problems.append('未要求补充必要上下文')
        elif want != got:
            problems.append(f'澄清槽位：期望 {sorted(want)}，实际 {sorted(got)}')
        return problems
    if want_status != 'answered':
        if not response.limitations:
            problems.append('拒答缺少限制说明')
        problems.extend(compare_answer({**case, 'gold_answer': ''}, response))
        return problems
    if response.clarification or response.error:
        problems.append('回答含意外澄清或错误')
    problems.extend(compare_rows(case, response))
    if case.get('expected_tables') and not response.sql_results:
        problems.append('要求数据库事实，但未返回 SQL 来源')
    if case.get('gold_calculation'):
        problems.extend(facts_missing(case['gold_calculation'], response))
        arithmetic = any(key in case['gold_calculation'] for key in ('rate','gap','growth_rate','result','formula','sum','average_order_value','aov'))
        if arithmetic and not response.calculations:
            problems.append('公式题未返回结构化 calculations')
    problems.extend(compare_answer(case, response))
    problems.extend(workflow_assertions(case,response))
    actual_docs = {c.doc_id for c in response.documents}
    for doc in case.get('expected_documents') or []:
        if doc not in actual_docs:
            problems.append(f'缺少必要来源 {doc}')
    for assertion in expectation.get('source_assertions', []):
        chunks = [c for c in response.documents if c.doc_id == assertion['document_id']]
        if assertion.get('page') and not any(c.page == assertion['page'] for c in chunks):
            problems.append(f'来源页不匹配 {assertion["document_id"]}:{assertion["page"]}')
        if assertion.get('image_region') and not any(c.bbox is not None for c in chunks):
            problems.append(f'缺少图片区域定位 {assertion["document_id"]}')
    return list(dict.fromkeys(problems))
