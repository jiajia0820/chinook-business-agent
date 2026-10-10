"""Server-selected, parameterized plans for document-dependent business steps.

These plans contain no document thresholds or candidate IDs. All execution still
passes through C's SQLExecutor and its read-only/schema/row-budget checks.
"""
from datetime import date

OPERATIONS = {'customer_cohort', 'document_candidates'}


def valid_operation(intent):
    op = intent.get('operation')
    if op not in OPERATIONS:
        return False
    allowed = {'operation', 'cohort_genre_id'} if op == 'customer_cohort' else {'operation', 'candidate_ids', 'genre_id'}
    if set(intent) != allowed:
        return False
    key = 'cohort_genre_id' if op == 'customer_cohort' else 'genre_id'
    if type(intent[key]) is not int or intent[key] < 1:
        return False
    if op == 'document_candidates':
        ids = intent['candidate_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(type(i) is not int or i < 1 for i in ids) or len(ids) != len(set(ids)):
            return False
    return True


def output_columns(intent):
    if intent.get('operation') == 'customer_cohort':
        return ['CustomerId', 'customer', 'sales_amount', 'order_count', 'all_audio_sales_amount']
    return ['TrackId', 'Name', 'sales_amount', 'order_count']


def build_query(slots):
    intent = slots.get('query_intent', {})
    if not valid_operation(intent):
        raise ValueError('invalid business operation')
    start, end = slots['start_date'], slots['end_date']
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise ValueError('invalid business period')
    media = slots['media_ids']
    if not media or any(type(i) is not int or i < 1 for i in media):
        raise ValueError('invalid media scope')
    params = {'start_date': start, 'end_date': end}
    def ids_clause(values, prefix):
        for index, value in enumerate(values):
            params[f'{prefix}_{index}'] = value
        return ','.join(f':{prefix}_{i}' for i in range(len(values)))
    media_sql = ids_clause(media, 'media')
    if intent['operation'] == 'customer_cohort':
        params['cohort_genre_id'] = intent['cohort_genre_id']
        # Aggregate all audio rows once. The genre condition is only membership
        # and an independently exposed intermediate measure, never a WHERE filter.
        sql = f'''SELECT c.CustomerId AS CustomerId,c.FirstName || ' ' || c.LastName AS customer,
ROUND(SUM(CASE WHEN t.GenreId=:cohort_genre_id THEN il.UnitPrice*il.Quantity ELSE 0 END),2) AS sales_amount,
COUNT(DISTINCT CASE WHEN t.GenreId=:cohort_genre_id THEN i.InvoiceId END) AS order_count,
ROUND(SUM(il.UnitPrice*il.Quantity),2) AS all_audio_sales_amount
FROM main.Customer c JOIN main.Invoice i ON i.CustomerId=c.CustomerId
JOIN main.InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId
WHERE i.InvoiceDate>=:start_date AND i.InvoiceDate<:end_date AND t.MediaTypeId IN ({media_sql})
GROUP BY c.CustomerId,c.FirstName,c.LastName
HAVING SUM(CASE WHEN t.GenreId=:cohort_genre_id THEN 1 ELSE 0 END)>0
ORDER BY sales_amount DESC,c.CustomerId'''
    else:
        params['genre_id'] = intent['genre_id']
        candidates_sql = ids_clause(intent['candidate_ids'], 'candidate')
        # Keep catalog candidates with no qualifying period facts. Date/media
        # constraints live inside the facts subquery so an outer WHERE cannot
        # turn missing facts into missing candidates.
        sql = f'''SELECT t.TrackId AS TrackId,t.Name AS Name,
COALESCE(f.sales_amount,0) AS sales_amount,COALESCE(f.order_count,0) AS order_count
FROM main.Track t LEFT JOIN (
SELECT il.TrackId,ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount,
COUNT(DISTINCT i.InvoiceId) AS order_count
FROM main.Invoice i JOIN main.InvoiceLine il ON il.InvoiceId=i.InvoiceId
JOIN main.Track ft ON ft.TrackId=il.TrackId
WHERE i.InvoiceDate>=:start_date AND i.InvoiceDate<:end_date AND ft.MediaTypeId IN ({media_sql})
GROUP BY il.TrackId) f ON f.TrackId=t.TrackId
WHERE t.TrackId IN ({candidates_sql}) AND t.GenreId=:genre_id AND t.MediaTypeId IN ({media_sql})
ORDER BY t.TrackId'''
    return {'sql': sql, 'params': params}
