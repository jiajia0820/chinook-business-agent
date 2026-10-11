"""Shared, bounded query structure derived only from the current business question.

No evaluation IDs, gold SQL or expected numeric answers are used here.
"""
import re
import math
from .entity_ids import identifier_spans, catalog_ids

DIMENSIONS = {'genre', 'track', 'album', 'artist', 'media_type', 'billing_country', 'customer', 'support_rep', 'playlist', 'month'}
DIMENSION_METRICS = {'genre': 'genre_sales', 'track': 'track_sales', 'album': 'album_sales',
    'artist': 'artist_sales', 'media_type': 'media_type_sales', 'billing_country': 'billing_country_sales',
    'playlist': 'playlist_track_count'}
MEASURES = {'sales_amount', 'units_sold', 'order_count', 'purchasing_customers', 'average_order_value'}
CN_NUMBERS = dict(zip('一二三四五六七八九十', range(1, 11)))


def parse_query_intent(question, metric_ids=()):
    q = question.casefold()
    result = {}
    if identifier_spans(question) and re.search(r'标价|单价|媒体类型|叫什么|商品属性', q) and not re.search(r'销售额|销量|订单|成交|消费|卖出|海报|主页面', q):
        return {'operation': 'track_attributes', 'track_ids': catalog_ids(question)}
    # A named output column is not automatically the aggregation grain. For
    # example "五个商品，列出专辑和艺术家" remains a track ranking.
    candidates = [('billing_country', r'账单国家'), ('support_rep', r'支持负责人|supportrep'),
        ('playlist', r'每个播放列表|各播放列表'), ('customer', r'五位客户|\d+位客户|每位客户|各客户|哪些客户|客户有哪些|客户编号|客户.*(?:金额最高|销售额最高)'),
        ('track', r'(?:五|\d+)个商品|各商品|每个商品|trackid'),
        ('album', r'(?:五|\d+)张专辑|各专辑|每张专辑'), ('artist', r'(?:五|\d+)位艺术家|各艺术家|每位艺术家'),
        ('media_type', r'各媒体类型|每个媒体类型|按媒体类型'),
        ('genre', r'各音乐分类|各品类|每个品类|各分类|最高的品类|最高.*品类|品类.*最高'),
        ('month', r'每月|按月|月度实际')]
    for dimension, pattern in candidates:
        if re.search(pattern, q):
            result['dimension'] = dimension
            break
    if 'dimension' not in result:
        for dimension, metric in DIMENSION_METRICS.items():
            if metric in metric_ids:
                result['dimension'] = dimension
                break
    if identifier_spans(question):
        result['track_ids'] = catalog_ids(question)
        result['dimension'] = 'track'
    match = re.search(r'(?:top\s*|前\s*)(\d+)|最高(?:的)?\s*([一二三四五六七八九十]|\d+)\s*[个位张名]', q)
    if match:
        token = match.group(1) or match.group(2)
        number = CN_NUMBERS.get(token, int(token) if token.isdigit() else 0)
        if 1 <= number <= 200:
            result.update(top_n=number, order_by='sales_amount', order_desc=True)
    if '销售额最高' in q:
        result.update(order_by='sales_amount', order_desc=True)
        if result.get('dimension') == 'genre' and 'top_n' not in result:
            result['top_n'] = 1
    elif result.get('dimension') == 'support_rep':
        result.update(order_by='purchasing_customers', order_desc=True)
    if result.get('dimension') == 'customer':
        amount = re.search(r'(?:消费总额|消费金额|消费额|销售额|金额)\s*(不少于|不低于|至少|大于等于|>=|超过|高于|大于|>|不超过|至多|小于等于|<=|低于|小于|<|等于|=)\s*(\d+(?:\.\d{1,2})?)(?![\d.])\s*(?:美元|USD|美金)', question, re.I)
        if amount:
            operators = {'不少于':'gte','不低于':'gte','至少':'gte','大于等于':'gte','>=':'gte',
                '超过':'gt','高于':'gt','大于':'gt','>':'gt','不超过':'lte','至多':'lte','小于等于':'lte','<=':'lte',
                '低于':'lt','小于':'lt','<':'lt','等于':'eq','=':'eq'}
            result['amount_filter'] = {'operator': operators[amount[1]], 'value': float(amount[2])}
        comparison_words = re.findall(r'不少于|不低于|至少|大于等于|>=|超过|高于|大于|不超过|至多|小于等于|<=|低于|小于|等于|[><=]', question)
        if comparison_words and (not amount or len(comparison_words) != 1):
            raise ValueError('金额阈值需要一个明确的美元数值条件')
        if re.search(r'按\s*(?:消费总额|消费金额|消费额|销售额|金额)\s*降序', q):
            result.update(order_by='sales_amount', order_desc=True)
        elif re.search(r'按\s*(?:消费总额|消费金额|消费额|销售额|金额)\s*升序', q):
            result.update(order_by='sales_amount', order_desc=False)
    if result.get('dimension') == 'media_type' and re.search(r'零成交|没有成交.*(?:列出|显示)|无成交.*(?:列出|显示)', q):
        result['include_empty'] = True
    measures = [m for m in metric_ids if m in MEASURES]
    if result.get('dimension') in {'track', 'album', 'artist', 'billing_country', 'customer', 'month'} and not measures:
        measures = ['sales_amount']
    if result.get('dimension') == 'support_rep':
        measures = ['purchasing_customers']
    if result.get('dimension') == 'customer' and 'order_count' not in measures:
        measures.append('order_count')
    if measures:
        result['measures'] = measures
    if result.get('dimension') in {'track', 'album'}:
        include = []
        if '专辑' in q and result['dimension'] == 'track':
            include.append('album')
        if '艺术家' in q or result['dimension'] == 'album':
            include.append('artist')
        if include:
            result['include'] = include
    if '同时' in q and '音频' in q and '视频' in q and '订单' in q:
        result = {'operation': 'mixed_media_orders'}
    return result


def validate_intent(intent):
    from .business_queries import OPERATIONS, valid_operation
    if isinstance(intent, dict) and intent.get('operation') in OPERATIONS:
        return valid_operation(intent)
    if isinstance(intent, dict) and intent.get('operation') == 'track_attributes':
        ids = intent.get('track_ids')
        return set(intent) == {'operation','track_ids'} and isinstance(ids,list) and 1 <= len(ids) <= 200 and all(type(i) is int and i > 0 for i in ids) and len(ids) == len(set(ids))
    allowed = {'dimension', 'top_n', 'order_by', 'order_desc', 'measures', 'include', 'operation', 'amount_filter', 'include_empty', 'track_ids'}
    if not isinstance(intent, dict) or set(intent) - allowed:
        return False
    if 'dimension' in intent and intent['dimension'] not in DIMENSIONS:
        return False
    if 'track_ids' in intent:
        ids = intent['track_ids']
        if intent.get('dimension') != 'track' or not isinstance(ids,list) or not 1 <= len(ids) <= 200 or any(type(i) is not int or i < 1 for i in ids) or len(ids) != len(set(ids)):
            return False
    if 'top_n' in intent and (type(intent['top_n']) is not int or not 1 <= intent['top_n'] <= 200):
        return False
    if 'order_by' in intent and intent['order_by'] not in MEASURES:
        return False
    if 'order_desc' in intent and type(intent['order_desc']) is not bool:
        return False
    if 'include_empty' in intent and (type(intent['include_empty']) is not bool or intent.get('dimension') != 'media_type'):
        return False
    if 'amount_filter' in intent:
        rule = intent['amount_filter']
        if intent.get('dimension') != 'customer' or not isinstance(rule,dict) or set(rule) != {'operator','value'} or not isinstance(rule['operator'],str) or rule['operator'] not in {'gte','gt','lte','lt','eq'} or type(rule['value']) not in (int,float) or (type(rule['value']) is float and not math.isfinite(rule['value'])) or rule['value'] < 0:
            return False
    for key, values in [('measures', MEASURES), ('include', {'album', 'artist'})]:
        if key in intent and (not isinstance(intent[key], list) or any(not isinstance(v,str) or v not in values for v in intent[key]) or len(intent[key]) != len(set(intent[key]))):
            return False
    return 'operation' not in intent or intent['operation'] == 'mixed_media_orders'
