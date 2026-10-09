"""Injectable JSON model protocol and optional server-configured HTTP client."""
import json
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .errors import ModuleError, unavailable
from .profiles import obj
from .validation import validate_params

OUTPUT_SCHEMA = {'type':'object','additionalProperties':False,'required':['sql','params'],
                 'properties':{'sql':{'type':'string'},'params':{'type':'object'}}}


def model_error(reason, message, retryable=False):
    return ModuleError('SQL_EXECUTION_FAILED',message,reason,retryable)


def decode_generated(payload):
    """Reject prose, fenced code, duplicate keys, extras and unsupported values."""
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise ValueError()
            result[k]=v
        return result
    try:
        if not isinstance(payload,str) or len(payload)>40000:
            raise ValueError()
        value=json.loads(payload,object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        obj(value,['sql','params'],['sql','params'],'model output')
        if not isinstance(value['sql'],str) or not value['sql'].strip() or len(value['sql'])>20000:
            raise ValueError()
        validate_params(value['params'])
        return value
    except (ValueError,TypeError,ModuleError):
        raise model_error('MODEL_OUTPUT_INVALID','模型未返回合法 sql/params JSON') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPJSONModel:
    """Chat-completions-compatible wire adapter. No provider account is created."""
    def __init__(self, endpoint, model, api_key, opener=None):
        self._endpoint, self._model, self._key=endpoint,model,api_key
        self._opener=opener or build_opener(NoRedirect())

    @classmethod
    def from_env(cls):
        from services.agent_api.app.core.model_config import ModelConfig, ModelConfigurationError
        try:
            config = ModelConfig.from_env()
        except ModelConfigurationError as exc:
            raise unavailable(exc.reason, '模型配置不可用；请设置 LLM_BASE_URL、LLM_MODEL 和 LLM_API_KEY') from None
        params = config.c_http_parameters()
        return cls(params["endpoint"], params["model"], params["api_key"])

    def generate_json(self, messages, output_schema, deadline):
        timeout=min(50,deadline-time.monotonic())
        if timeout<=0: raise model_error('MODEL_TIMEOUT','模型调用预算已耗尽')
        body=json.dumps(dict(model=self._model,messages=messages,temperature=0,
                             response_format={'type':'json_object'},max_tokens=2048)).encode('utf-8')
        request=Request(self._endpoint,data=body,headers={'Content-Type':'application/json',
                                                       'Authorization':'Bearer '+self._key},method='POST')
        try:
            with self._opener.open(request,timeout=timeout) as response:
                raw=response.read(200001)
                if len(raw)>200000: raise ValueError()
                result=json.loads(raw)
                content=result['choices'][0]['message']['content']
                if not isinstance(content,str): raise ValueError()
            if time.monotonic()>=deadline: raise model_error('MODEL_TIMEOUT','模型响应超过调用预算')
            return content
        except ModuleError: raise
        except HTTPError as exc:
            raise model_error('MODEL_HTTP_FAILED','模型服务返回 HTTP '+str(exc.code),exc.code==429 or exc.code>=500) from None
        except (TimeoutError,socket.timeout):
            raise model_error('MODEL_TIMEOUT','模型调用超时') from None
        except URLError:
            raise model_error('MODEL_NETWORK_FAILED','模型服务连接失败',True) from None
        except (ValueError,KeyError,IndexError,TypeError,UnicodeError):
            raise model_error('MODEL_RESPONSE_INVALID','模型服务响应格式错误') from None


class DemoJSONModel:
    """Fixed offline examples only; never presented as a real model or free-form NL2SQL."""
    is_demo=True
    def generate_json(self,messages,output_schema,deadline):
        context=json.loads(messages[-1]['content'])
        question=context['question'].strip().rstrip('？?')
        examples={
            '客户数量是多少':{'sql':'SELECT COUNT(*) AS customer_count FROM main.Customer','params':{}},
            '有哪些音乐类型':{'sql':'SELECT GenreId, Name FROM main.Genre ORDER BY GenreId','params':{}},
            '美国客户数量是多少':{'sql':'SELECT COUNT(*) AS customer_count FROM main.Customer WHERE Country=:country','params':{'country':'USA'}}
        }
        question_lower = question.casefold()
        if ('销售额' in question or '目标' in question or '增长' in question) and ('rock' in question_lower or '摇滚' in question):
            start, end = ('2025-07-01', '2025-10-01') if ('第三季度' in question or 'q3' in question_lower) else ('2025-04-01', '2025-07-01')
            if '增长' in question or '环比' in question:
                examples[question] = {
                    'sql': (
                        "SELECT 'current' AS period, COALESCE(SUM(il.UnitPrice * il.Quantity), 0) AS sales_amount "
                        "FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId "
                        "WHERE i.InvoiceDate >= :current_start AND i.InvoiceDate < :current_end AND t.GenreId=:genre_id AND t.MediaTypeId IN (1,2,4,5) "
                        "UNION ALL SELECT 'previous' AS period, COALESCE(SUM(il.UnitPrice * il.Quantity), 0) AS sales_amount "
                        "FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId "
                        "WHERE i.InvoiceDate >= :previous_start AND i.InvoiceDate < :previous_end AND t.GenreId=:genre_id AND t.MediaTypeId IN (1,2,4,5)"
                    ),
                    'params': {'current_start': start, 'current_end': end, 'previous_start': '2025-04-01', 'previous_end': '2025-07-01', 'genre_id': 1},
                }
            else:
                examples[question] = {
                'sql': (
                    'SELECT COALESCE(SUM(il.UnitPrice * il.Quantity), 0) AS sales_amount '
                    'FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId '
                    'JOIN main.Track t ON t.TrackId=il.TrackId '
                    'WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date '
                    'AND t.GenreId=:genre_id AND t.MediaTypeId IN (1,2,4,5)'
                ),
                    'params': {'start_date': start, 'end_date': end, 'genre_id': 1},
                }
        if all(term in question for term in ('销售额', '销量', '订单数', '购买客户数')):
            examples[question] = {
                'sql': (
                    'SELECT ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount, '
                    'COALESCE(SUM(il.Quantity), 0) AS units_sold, '
                    'COUNT(DISTINCT i.InvoiceId) AS order_count, '
                    'COUNT(DISTINCT i.CustomerId) AS purchasing_customers '
                    'FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId '
                    'JOIN main.Track t ON t.TrackId=il.TrackId '
                    'WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date '
                    'AND t.MediaTypeId IN (1,2,4,5)'
                ),
                'params': {'start_date': '2025-07-01', 'end_date': '2025-10-01'},
            }
        if question not in examples:
            raise ModuleError('UNSUPPORTED_CAPABILITY','离线演示仅支持文档列出的固定问法')
        return json.dumps(examples[question],ensure_ascii=False)
