"""Conservative semantic checks on SQL AST, before the read-only executor.

Read-only safety and business semantics are independent. Unsupported equivalent
forms are repaired with a disclosed registered plan rather than guessed valid.
"""
import sqlglot
from sqlglot import exp
from datetime import date


def covers_context(candidate, context, *, media_applies=True):
    try:
        tree = sqlglot.parse_one(candidate['sql'], read='sqlite')
    except (sqlglot.errors.SqlglotError, KeyError, TypeError):
        return False
    params = candidate.get('params', {})
    slots = context.get('resolved_slots', {})
    intent = slots.get('query_intent', {})
    if slots.get('comparison_period'):
        tags = [item.this.this for select in tree.find_all(exp.Select) for item in select.expressions
            if isinstance(item,exp.Alias) and item.alias=='period' and isinstance(item.this,exp.Literal)]
        if sorted(tags) != ['current','previous']:
            return False

    def value(node):
        if isinstance(node, exp.Placeholder):
            return params.get(node.name)
        if isinstance(node, exp.Literal):
            return node.this
        return None

    for select in tree.find_all(exp.Select):
        aliases = {table.alias_or_name.casefold(): table.name.casefold() for table in select.find_all(exp.Table)}
        if 'invoiceline' not in aliases.values():
            continue
        start_date, end_date = slots.get('start_date'), slots.get('end_date')
        prior = slots.get('comparison_period')
        period_tag = next((value(item.this) for item in select.expressions if isinstance(item, exp.Alias) and item.alias == 'period'), None)
        if prior and period_tag == 'previous':
            year, quarter = prior.get('year'), prior.get('quarter')
            if type(year) is not int or type(quarter) is not int or not 1 <= quarter <= 4:
                return False
            start_date = date(year, (quarter-1)*3+1, 1).isoformat()
            end_date = date(year+1, 1, 1).isoformat() if quarter == 4 else date(year, quarter*3+1, 1).isoformat()
        joins = select.args.get('joins') or []
        # Null-preserving joins can retain unqualified fact rows. The controlled
        # sales plan deliberately includes only matching transactions.
        if any(join.args.get('side') or join.args.get('kind') in {'CROSS', 'OUTER'} for join in joins):
            return False
        where = select.args.get('where')
        predicates = [where.this] if where else []
        predicates.extend(join.args['on'] for join in joins if join.args.get('on') is not None)
        if any(predicate.find(exp.Or) for predicate in predicates):
            return False

        def column(node, table, name):
            return isinstance(node, exp.Column) and node.name.casefold() == name.casefold() and (not node.table or aliases.get(node.table.casefold()) == table.casefold())

        def binary(kind, table, name, expected):
            return any(column(node.this, table, name) and str(value(node.expression)) == str(expected)
                for predicate in predicates for node in predicate.find_all(kind))

        if start_date and not binary(exp.GTE, 'invoice', 'InvoiceDate', start_date):
            return False
        if end_date and not binary(exp.LT, 'invoice', 'InvoiceDate', end_date):
            return False

        def membership(table, name, expected):
            expected = {str(item) for item in expected}
            return any(column(node.this, table, name) and {str(value(item)) for item in node.expressions} == expected
                for predicate in predicates for node in predicate.find_all(exp.In)) or len(expected) == 1 and binary(exp.EQ, table, name, next(iter(expected)))

        if media_applies and slots.get('media_ids') and not membership('track', 'MediaTypeId', slots['media_ids']):
            return False
        genres = [e['id'].split(':', 1)[1] for e in context.get('entities', []) if e.get('type') == 'genre' and e.get('id', '').startswith('Genre:')]
        if genres and not membership('track', 'GenreId', genres):
            return False
        countries = [e['id'].split(':', 1)[1] for e in context.get('entities', []) if e.get('type') == 'country' and e.get('id', '').startswith('Country:')]
        if countries and not membership('invoice', 'BillingCountry', countries):
            return False
        if intent.get('top_n'):
            limit = select.args.get('limit')
            if limit is None or str(value(limit.expression)) != str(intent['top_n']):
                return False
            order = select.args.get('order')
            if order is None or not order.expressions:
                return False
            first = order.expressions[0]
            if not first.args.get('desc') or not isinstance(first.this, exp.Column) or first.this.name != intent.get('order_by', 'sales_amount'):
                return False
        dimension = intent.get('dimension')
        dimensions = {'genre': ('genre', 'GenreId'), 'track': ('track', 'TrackId'),
            'album': ('album', 'AlbumId'), 'artist': ('artist', 'ArtistId'),
            'media_type': ('mediatype', 'MediaTypeId'), 'billing_country': ('invoice', 'BillingCountry'),
            'customer': ('customer', 'CustomerId'), 'support_rep': ('employee', 'EmployeeId')}
        if dimension in dimensions:
            table, name = dimensions[dimension]
            group = select.args.get('group')
            if group is None or not any(column(node, table, name) for node in group.expressions):
                return False
        elif dimension == 'month':
            group=select.args.get('group')
            def month_expression(node):
                return isinstance(node,exp.TimeToStr) and value(node.args.get('format'))=='%Y-%m' and any(
                    column(item,'invoice','InvoiceDate') for item in node.find_all(exp.Column))
            if group is None or not any(month_expression(item) for item in group.expressions) or not any(
                    isinstance(item,exp.Alias) and item.alias=='month' and month_expression(item.this) for item in select.expressions):
                return False
    return True
