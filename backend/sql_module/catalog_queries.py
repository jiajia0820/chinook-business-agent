"""Parameterized catalog attributes and zero-preserving media summaries.

No evaluation cases, answers, fixed identifiers or customer thresholds.
"""

CATALOG_COLUMNS = ['TrackId', 'Name', 'genre', 'media_type', 'unit_price']


def catalog_query(intent):
    ids = intent['track_ids']
    params = {f'track_{n}': value for n, value in enumerate(ids)}
    bindings = ','.join(':' + name for name in params)
    return {'sql': 'SELECT t.TrackId AS TrackId,t.Name AS Name,g.Name AS genre,'
        'mt.Name AS media_type,t.UnitPrice AS unit_price FROM main.Track t '
        'JOIN main.Genre g ON g.GenreId=t.GenreId '
        'JOIN main.MediaType mt ON mt.MediaTypeId=t.MediaTypeId '
        f'WHERE t.TrackId IN ({bindings}) ORDER BY t.TrackId', 'params': params}


def zero_media_query(context, measures):
    slots = context['resolved_slots']
    expressions = {
        'sales_amount': 'ROUND(SUM(il.UnitPrice * il.Quantity),2)',
        'units_sold': 'SUM(il.Quantity)',
        'order_count': 'COUNT(DISTINCT i.InvoiceId)',
        'purchasing_customers': 'COUNT(DISTINCT i.CustomerId)',
    }
    if not measures or set(measures) - set(expressions) or not {'start_date', 'end_date'} <= set(slots):
        return None
    params = {key: slots[key] for key in ('start_date', 'end_date')}
    where = ['i.InvoiceDate >= :start_date', 'i.InvoiceDate < :end_date']
    for entity in context.get('entities', []):
        if entity.get('type') == 'genre' and entity.get('id', '').startswith('Genre:'):
            params['genre_id'] = int(entity['id'].split(':', 1)[1])
            where.append('t.GenreId = :genre_id')
        elif entity.get('type') == 'country' and entity.get('id', '').startswith('Country:'):
            params['billing_country'] = entity['id'].split(':', 1)[1]
            where.append('i.BillingCountry = :billing_country')
        else:
            return None
    facts = ','.join(expressions[m] + ' AS ' + m for m in measures)
    output = ','.join(f'COALESCE(f.{m},0) AS {m}' for m in measures)
    return {'sql': 'SELECT mt.MediaTypeId AS MediaTypeId,mt.Name AS Name,' + output +
        ' FROM main.MediaType mt LEFT JOIN (SELECT t.MediaTypeId,' + facts +
        ' FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId '
        'JOIN main.Track t ON t.TrackId=il.TrackId WHERE ' + ' AND '.join(where) +
        ' GROUP BY t.MediaTypeId) f ON f.MediaTypeId=mt.MediaTypeId ORDER BY mt.MediaTypeId',
        'params': params}
