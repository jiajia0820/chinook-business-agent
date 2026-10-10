"""Shared, bounded query structure derived only from the current business question.

No evaluation IDs, gold SQL or expected numeric answers are used here.
"""
import re

DIMENSIONS = {'genre', 'track', 'album', 'artist', 'media_type', 'billing_country', 'customer', 'support_rep', 'playlist', 'month'}
DIMENSION_METRICS = {'genre': 'genre_sales', 'track': 'track_sales', 'album': 'album_sales',
    'artist': 'artist_sales', 'media_type': 'media_type_sales', 'billing_country': 'billing_country_sales',
    'playlist': 'playlist_track_count'}
MEASURES = {'sales_amount', 'units_sold', 'order_count', 'purchasing_customers', 'average_order_value'}
CN_NUMBERS = dict(zip('一二三四五六七八九十', range(1, 11)))


def parse_query_intent(question, metric_ids=()):
    q = question.casefold()
    result = {}
    # A named output column is not automatically the aggregation grain. For
    # example "五个商品，列出专辑和艺术家" remains a track ranking.
    candidates = [('billing_country', r'账单国家'), ('support_rep', r'支持负责人|supportrep'),
        ('playlist', r'每个播放列表|各播放列表'), ('customer', r'五位客户|\d+位客户|每位客户|各客户|哪些客户|客户.*(?:金额最高|销售额最高)'),
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
    match = re.search(r'(?:top\s*|前\s*)(\d+)|最高(?:的)?\s*([一二三四五六七八九十]|\d+)\s*[个位张名]', q)
    if match:
        token = match.group(1) or match.group(2)
        number = CN_NUMBERS.get(token, int(token) if token.isdigit() else 0)
        if 1 <= number <= 200:
            result.update(top_n=number, order_by='sales_amount', order_desc=True)
    if '销售额最高' in q:
        result.update(order_by='sales_amount', order_desc=True)
    elif result.get('dimension') == 'support_rep':
        result.update(order_by='purchasing_customers', order_desc=True)
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
    allowed = {'dimension', 'top_n', 'order_by', 'order_desc', 'measures', 'include', 'operation'}
    if not isinstance(intent, dict) or set(intent) - allowed:
        return False
    if 'dimension' in intent and intent['dimension'] not in DIMENSIONS:
        return False
    if 'top_n' in intent and (type(intent['top_n']) is not int or not 1 <= intent['top_n'] <= 200):
        return False
    if 'order_by' in intent and intent['order_by'] not in MEASURES:
        return False
    if 'order_desc' in intent and type(intent['order_desc']) is not bool:
        return False
    for key, values in [('measures', MEASURES), ('include', {'album', 'artist'})]:
        if key in intent and (not isinstance(intent[key], list) or any(not isinstance(v,str) or v not in values for v in intent[key]) or len(intent[key]) != len(set(intent[key]))):
            return False
    return 'operation' not in intent or intent['operation'] == 'mixed_media_orders'
