"""C 模块：零第三方依赖的 Chinook 只读查询与受限规则 NL2SQL 基线。"""
from pathlib import Path
from contextlib import contextmanager
import argparse
import json
import re
import sqlite3
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent


def find_db():
    return ROOT.parent / 'data' / 'chinook' / 'Chinook.db'


class Chinook:
    def __init__(self, path=None):
        self.path = Path(path or find_db()).resolve()
        with self.connect() as c:
            self.tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.genres = [r[0] for r in c.execute('SELECT Name FROM Genre')]
            self.date_range = list(c.execute('SELECT MIN(InvoiceDate), MAX(InvoiceDate) FROM Invoice').fetchone())

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        try:
            c.execute('PRAGMA query_only=ON')
            yield c
        finally:
            c.close()

    def schema(self):
        with self.connect() as c:
            result = []
            for table in sorted(self.tables):
                quoted = '"' + table.replace('"', '""') + '"'
                result.append({'table': table,
                    'columns': [{'name': r[1], 'type': r[2], 'not_null': bool(r[3]), 'primary_key_order': r[5]} for r in c.execute('PRAGMA table_info(' + quoted + ')')],
                    'foreign_keys': [{'target_table': r[2], 'column': r[3], 'target_column': r[4]} for r in c.execute('PRAGMA foreign_key_list(' + quoted + ')')],
                    'row_count': c.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]})
        return {'database': 'Chinook', 'invoice_date_range': self.date_range, 'tables': result}

    def _execute(self, sql, parameters=None, max_rows=50, timeout_ms=5000):
        if not isinstance(sql, str) or not re.match(r'^\s*(SELECT|WITH)\b', sql, re.I):
            raise ValueError('仅允许 SELECT 或只读 WITH 查询')
        if type(max_rows) is not int or not 1 <= max_rows <= 200:
            raise ValueError('max_rows 必须为 1 至 200')
        reads = set()
        allowed_functions = {'sum', 'count', 'avg', 'min', 'max', 'round', 'coalesce', 'nullif', 'abs', 'lower', 'upper', 'length', 'substr', 'strftime', 'date', 'datetime', 'ifnull', 'total'}
        def authorize(action, arg1, arg2, database, trigger):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and (database == 'main' or (database is None and arg2 == '')) and arg1 in self.tables:
                reads.add((arg1, arg2))
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() in allowed_functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        start = time.perf_counter()
        with self.connect() as c:
            c.set_authorizer(authorize)
            c.set_progress_handler(lambda: int((time.perf_counter() - start) * 1000 > timeout_ms), 100)
            cursor = c.execute(sql, parameters or {})
            columns = [d[0] for d in cursor.description]
            if len(columns) != len(set(columns)):
                raise ValueError('结果列名重复，请用 AS 指定唯一别名')
            rows = cursor.fetchmany(max_rows + 1)
        return {'columns': columns, 'rows': [list(r) for r in rows[:max_rows]],
                'row_count': min(len(rows), max_rows), 'truncated': len(rows) > max_rows,
                'elapsed_ms': round((time.perf_counter() - start) * 1000, 3),
                'schema_linking': [{'table': t, 'column': f} for t, f in sorted(reads)]}

    def query(self, request):
        """SQL 工具公开入口，严格采用 interface-contract.md 第四节。"""
        started = time.perf_counter()
        data = request if isinstance(request, dict) else {}
        result = dict(query_id=data.get('query_id'), status='rejected',
                      sql=data.get('sql'), columns=[], rows=[], row_count=0,
                      truncated=False, execution_ms=0, error=None)
        def fail(code, message, status='rejected'):
            result.update(status=status, error=error_object(code, message),
                          execution_ms=round((time.perf_counter()-started)*1000, 3))
            return result
        if not isinstance(request, dict) or not isinstance(data.get('query_id'), str) or not data['query_id'].strip():
            return fail('INVALID_REQUEST', 'query_id 必须是非空字符串')
        sql = data.get('sql')
        if not isinstance(sql, str) or not sql.strip():
            return fail('INVALID_REQUEST', 'sql 必须是非空字符串')
        params = data.get('params', {})
        if not isinstance(params, dict) or any(not isinstance(k, str) or type(v) not in (str, int, float, type(None)) for k,v in params.items()):
            return fail('INVALID_REQUEST', 'params 必须是包含字符串键及标量值的对象')
        if not isinstance(data.get('purpose', ''), str):
            return fail('INVALID_REQUEST', 'purpose 必须是字符串')
        limit, timeout = data.get('max_rows', 50), data.get('timeout_ms', 5000)
        if type(limit) is not int or not 1 <= limit <= 200:
            return fail('INVALID_REQUEST', 'max_rows 必须为 1 至 200')
        if type(timeout) is not int or not 1 <= timeout <= 30000:
            return fail('INVALID_REQUEST', 'timeout_ms 必须为 1 至 30000')
        if not re.match(r'^\s*(SELECT|WITH)\b', sql, re.I):
            return fail('SQL_READ_ONLY_VIOLATION', '只允许单条 SELECT 或 WITH ... SELECT')
        try:
            raw = self._execute(sql, params, limit, timeout)
            result.update(status='success', columns=raw['columns'],
                          rows=[dict(zip(raw['columns'], row)) for row in raw['rows']],
                          row_count=raw['row_count'], truncated=raw['truncated'],
                          execution_ms=raw['elapsed_ms'])
            return result
        except ValueError as exc:
            return fail('INVALID_REQUEST', str(exc))
        except (sqlite3.Error, sqlite3.Warning) as exc:
            message = str(exc)
            if 'authorized' in message or 'one statement' in message:
                return fail('SQL_READ_ONLY_VIOLATION', '查询被拒绝：只允许单条受支持的只读查询')
            if isinstance(exc, sqlite3.ProgrammingError):
                return fail('INVALID_REQUEST', 'SQL 参数与占位符不匹配')
            return fail('SQL_EXECUTION_FAILED', '查询超时' if 'interrupted' in message else 'SQL 执行失败，请核对表、字段和语法', 'failed')

    def base_response(self, question):
        return {'version': '0.1', 'request_id': str(uuid.uuid4()), 'question': question,
                'status': 'unsupported', 'answer': '', 'sql': None, 'parameters': {},
                'result': None, 'schema_linking': [], 'metric_definition': None,
                'clarification': None, 'document_sources': [],
                'data_source': {'database': 'Chinook', 'invoice_date_range': self.date_range},
                'warnings': [], 'trace': [], 'generator': 'rule_baseline_v1'}

    def _ask_legacy(self, question, max_rows=50):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError('question 必须是 1 至 2000 字符的文本')
        r = self.base_response(question)
        q = question.strip()
        if any(w in q for w in ('增长率', '同比', '环比')):
            r.update(status='needs_clarification', clarification='请确认指标、当前期和对比期，例如：2025 年销售额相比 2024 年的增长率。当前规则基线暂不自动执行增长率计算。')
            return r
        if any(w in q for w in ('目标', '达标', '达成', '制度', '规定')):
            r.update(status='needs_document', answer='需要 B 模块检索经营目标或业务规则文档，确认年份、期间、指标和目标值后，再调用 SQL 查询工具。数据库自身不能证明目标是否达成。')
            return r
        params = {}
        sql = None
        definition = None
        # 故意限定完整句式，避免将未支持的过滤条件静默丢弃。
        simple = {
            '客户': ('Customer', '客户记录数'), '顾客': ('Customer', '客户记录数'),
            '员工': ('Employee', '员工记录数'), '艺术家': ('Artist', '艺术家记录数'),
            '专辑': ('Album', '专辑记录数'), '曲目': ('Track', '曲目记录数'),
            '歌曲': ('Track', '曲目记录数'), '音乐类型': ('Genre', '音乐类型记录数'),
            '订单': ('Invoice', '订单记录数'), '播放列表': ('Playlist', '播放列表记录数'),
            '媒体类型': ('MediaType', '媒体类型记录数')}
        m = re.fullmatch(r'(?:一共|总共|共有)?(客户|顾客|员工|艺术家|专辑|曲目|歌曲|音乐类型|订单|播放列表|媒体类型)(?:数量是多少|有多少(?:个|名|首|张|种)?|总数是多少)[？?]?', q)
        if m:
            table, definition = simple[m[1]]
            sql = f'SELECT COUNT(*) AS count FROM "{table}"'
        if q in ('有哪些音乐类型？', '有哪些音乐类型', '有哪些音乐类型?'):
            sql = 'SELECT GenreId, Name FROM Genre ORDER BY GenreId'
            definition = '音乐类型目录'
        sales = re.fullmatch(r'(全部年份|\d{4}\s*年)\s*(?:(第[一二三四1234]季度)\s*)?(.*?)\s*(销售额|销售金额|销量|订单数)(?:是多少|有多少)?[？?]?', q)
        rank = re.fullmatch(r'(全部年份|\d{4}\s*年)\s*按(国家|音乐类型|艺术家)的?销售额(?:排名)?前\s*(\d+)\s*名[？?]?', q)
        if sales or rank:
            m = sales or rank
            period = m[1].replace(' ', '')
            conditions = []
            if period != '全部年份':
                year = int(period[:4])
                if not 1 <= year <= 9998:
                    raise ValueError('年份超出支持范围')
                month, end_year, end_month = 1, year + 1, 1
                if sales and sales[2]:
                    quarter = '一二三四'.find(sales[2][1]) + 1 if sales[2][1] in '一二三四' else int(sales[2][1])
                    month = (quarter - 1) * 3 + 1
                    end_year, end_month = (year + 1, 1) if quarter == 4 else (year, month + 3)
                params.update(start=f'{year:04d}-{month:02d}-01', end=f'{end_year:04d}-{end_month:02d}-01')
                conditions += ['i.InvoiceDate >= :start', 'i.InvoiceDate < :end']
                if params['end'] <= self.date_range[0][:10] or params['start'] > self.date_range[1][:10]:
                    r['warnings'].append('请求期间与数据日期范围不相交；无记录不代表真实业务销售额为零。')
            elif sales and sales[2]:
                r.update(status='needs_clarification', clarification='季度查询请明确年份。')
                return r
            joins = 'Invoice i JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId'
            metric = sales[4] if sales else '销售额'
            expression = {'销售额': 'ROUND(SUM(il.UnitPrice * il.Quantity), 2)', '销售金额': 'ROUND(SUM(il.UnitPrice * il.Quantity), 2)', '销量': 'SUM(il.Quantity)', '订单数': 'COUNT(DISTINCT i.InvoiceId)'}[metric]
            definition = {'销售额': '成交行单价 × 数量求和；币种沿用原库，未额外假定人民币', '销售金额': '成交行单价 × 数量求和；币种沿用原库', '销量': '成交明细 Quantity 求和', '订单数': '符合条件的 InvoiceId 去重计数'}[metric]
            entity = sales[3].strip() if sales else ''
            music_scope = '音乐' in q or entity in ('摇滚', '爵士', '流行', '古典', '金属', '蓝调')
            if entity.endswith('音乐'):
                entity = entity[:-2].strip()
            aliases = {'摇滚': 'Rock', '爵士': 'Jazz', '流行': 'Pop', '古典': 'Classical', '金属': 'Metal', '蓝调': 'Blues'}
            entity = aliases.get(entity, entity)
            genre = next((g for g in self.genres if g.casefold() == entity.casefold()), None)
            if entity and not genre:
                r['answer'] = '未支持或无法唯一识别该筛选条件，请使用精确音乐类型名称。'
                return r
            dimension = rank[2] if rank else None
            if genre or music_scope or dimension in ('音乐类型', '艺术家'):
                joins += ' JOIN Track t ON t.TrackId = il.TrackId'
            if music_scope or genre:
                joins += ' JOIN MediaType mt ON mt.MediaTypeId = t.MediaTypeId'
                conditions.append("mt.Name IN ('MPEG audio file', 'Protected AAC audio file', 'Purchased AAC audio file', 'AAC audio file')")
                definition += '；仅统计音频媒体类型'
            if genre or dimension == '音乐类型':
                joins += ' JOIN Genre g ON g.GenreId = t.GenreId'
            if genre:
                conditions.append('g.Name = :genre')
                params['genre'] = genre
            if dimension == '艺术家':
                joins += ' JOIN Album a ON a.AlbumId = t.AlbumId JOIN Artist ar ON ar.ArtistId = a.ArtistId'
            where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
            if rank:
                top = int(rank[3])
                if not 1 <= top <= 100:
                    raise ValueError('排名数量须为 1 至 100')
                group = {'国家': 'i.BillingCountry', '音乐类型': 'g.GenreId, g.Name', '艺术家': 'ar.ArtistId, ar.Name'}[dimension]
                label = {'国家': 'i.BillingCountry', '音乐类型': 'g.Name', '艺术家': 'ar.Name'}[dimension]
                params['top'] = top
                sql = f'SELECT {label} AS dimension, {expression} AS value, COUNT(*) AS matched_lines FROM {joins}{where} GROUP BY {group} ORDER BY value DESC, {group} ASC LIMIT :top'
                definition += '；国家使用账单国家 BillingCountry；排名同值按维度键排序，固定取前 N 项。' if dimension == '国家' else '；同值按维度键排序，固定取前 N 项。'
            else:
                sql = f'SELECT {expression} AS value, COUNT(*) AS matched_lines FROM {joins}{where}'
        if sql is None:
            if re.fullmatch(r'(?:Rock\s*|摇滚)?(?:音乐)?(?:销售额|销量|订单数)(?:是多少|有多少)?[？?]?', q):
                r.update(status='needs_clarification', clarification='请指定年份或季度，也可明确选择全部年份。')
            else:
                r['answer'] = '当前为受限规则 NL2SQL 初版，该问法暂不支持。可使用 README 中的句式，或由上层模型生成 SQL 后调用 query。'
            return r
        r.update(sql=sql, parameters=params, metric_definition=definition)
        try:
            result = self._execute(sql, params, max_rows)
            r.update(status='ok', result=result, schema_linking=result['schema_linking'])
            if not result['rows'] or ('matched_lines' in result['columns'] and result['rows'][0][-1] == 0):
                r.update(status='no_data', answer='数据库中没有匹配记录，不能据此认定真实业务结果为零。')
            else:
                r['answer'] = '查询结果：' + json.dumps(result['rows'], ensure_ascii=False) + '。统计口径：' + definition
            r['trace'] = ['按已支持句式识别指标、期间与维度', '按固定外键路径构造参数化 SQL', '通过只读连接与授权器执行', '返回数据库结果与实际读取字段']
        except (ValueError, sqlite3.Error) as e:
            r.update(status='error', answer=str(e))
        return r

    def ask(self, request):
        """C 模块演示适配器；会话编排和 RAG 仍由 B 提供。"""
        if isinstance(request, str):
            request = {'question': request}
        data = request if isinstance(request, dict) else {}
        response = dict(request_id=str(uuid.uuid4()), session_id=data.get('session_id') or str(uuid.uuid4()),
                        status='error', answer='', normalized_question='', route='unsupported',
                        intent={'name': 'unknown', 'confidence': 0.0}, entities=[], time_range=None,
                        data_scope='all', sql_results=[], documents=[], calculations=[],
                        metric_definitions=[], trace=[], limitations=[], clarification=None, error=None)
        options = data.get('options', {})
        if (not isinstance(request, dict) or not isinstance(data.get('question'), str)
                or not data['question'].strip() or len(data['question']) > 2000
                or not isinstance(options, dict)
                or data.get('user_role', 'operator') not in ('operator', 'manager')
                or ('session_id' in data and (not isinstance(data['session_id'], str) or not data['session_id'].strip()))):
            response['error'] = error_object('INVALID_REQUEST', '请核对 question、session_id、user_role 和 options')
            return response
        if (type(options.get('max_rows',50)) is not int or not 1 <= options.get('max_rows',50) <= 200
                or type(options.get('top_k',5)) is not int or not 1 <= options.get('top_k',5) <= 10
                or type(options.get('show_trace',True)) is not bool):
            response['error'] = error_object('INVALID_REQUEST', 'options 类型或范围不正确')
            return response
        q = data['question']
        try:
            old = self._ask_legacy(q, options.get('max_rows',50))
        except (ValueError, sqlite3.Error):
            response['error'] = error_object('INVALID_REQUEST', '问题中的年份或排名范围不正确')
            return response
        response.update(answer=old['answer'], normalized_question=q.strip(), limitations=list(old['warnings']))
        response['limitations'].append('限定句式规则基线；无多轮记忆或文档检索。session_id 仅透传。')
        if old['status'] == 'needs_clarification':
            response.update(status='clarification_required', route='clarification', clarification={'question': old['clarification']})
        elif old['status'] == 'needs_document':
            response.update(status='insufficient_evidence', route='cross_source')
            response['limitations'].append('需要 Agent 补齐时间与范围并检索目标文档。')
        elif old['status'] == 'unsupported':
            response.update(status='insufficient_evidence', route='unsupported')
        elif old['status'] == 'error':
            response.update(error=error_object('SQL_EXECUTION_FAILED', '查询执行失败'))
        else:
            raw = old['result']
            query_id = 'sql-' + str(uuid.uuid4())
            response.update(status='answered' if old['status']=='ok' else 'insufficient_evidence', route='sql',
                            intent={'name':'metric_query', 'confidence':1.0})
            response['sql_results'] = [dict(query_id=query_id, purpose=q, sql=old['sql'],
                columns=raw['columns'], rows=[dict(zip(raw['columns'],row)) for row in raw['rows']],
                row_count=raw['row_count'], execution_ms=raw['elapsed_ms'],
                source={'type':'database','name':'Chinook.db','tables':sorted({f['table'] for f in raw['schema_linking']})})]
            # 参数值保留在可核验口径中，不更改 A 管理的 SQL 工具响应字段。
            response['metric_definitions'] = [old['metric_definition'], {'params':old['parameters']}]
            if raw['truncated']:
                response['limitations'].append('结果已按 max_rows 截断')
            params = old['parameters']
            if 'start' in params:
                response['time_range'] = dict(start=params['start'],end=params['end'],end_inclusive=False,label=f"{params['start']} 至 {params['end']}")
            response['data_scope'] = 'music' if 'JOIN MediaType' in old['sql'] else 'all'
            if options.get('show_trace',True):
                response['trace']=[dict(step=1,tool='sql.query',status='success',summary='执行只读 SQL',source_refs=[query_id],duration_ms=raw['elapsed_ms'])]
        return response


def error_object(code, message):
    return {'code':code, 'message':message, 'retryable':False, 'details':None}


def serve(db, port):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload, code=200):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/schema':
                self.reply(db.schema())
            elif self.path == '/health':
                self.reply({'status': 'ok', 'version': '0.1'})
            else:
                self.reply({'error': 'not_found'}, 404)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 32000:
                    raise ValueError('请求体需为 1 至 32000 字节')
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('请求体必须是 JSON 对象')
                if self.path in ('/ask', '/api/v1/ask'):
                    result = db.ask(data)
                    self.reply(result, 400 if result['error'] and result['error']['code']=='INVALID_REQUEST' else 200)
                elif self.path == '/query':
                    self.reply(db.query(data))
                else:
                    self.reply({'error': 'not_found'}, 404)
            except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
                self.reply({'status': 'error', 'error': error_object('INVALID_REQUEST', '请求体无效')}, 400)

    print(f'C 模块服务：http://127.0.0.1:{port}，按 Ctrl+C 停止', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('command', choices=['schema', 'ask', 'serve'])
    parser.add_argument('question', nargs='?')
    args = parser.parse_args()
    db = Chinook(args.db)
    if args.command == 'serve':
        serve(db, args.port)
    else:
        print(json.dumps(db.schema() if args.command == 'schema' else db.ask(args.question), ensure_ascii=False, indent=2))
