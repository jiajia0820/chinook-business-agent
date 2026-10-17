"""原问题义务：把用户这一轮真正要求的过滤、粒度和输出结构记下来。

这里只解析问题文本，绝不调用模型、SQL 或检索，因此可以在工具执行之前就把
"用户要求了什么"固定下来。模型候选或受控降级查询是否满足这些义务，由
backend 的覆盖检查判断；不确定时拒绝，而不是放宽。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from ..profiles.models import BusinessProfile

DEFAULT_PROFILE_ID = "chinook-music"

# 方法/口径类问题的措辞。命中且没有索要数据库实际值时，按文档规则问题处理，
# 不因未登记业务词或缺年份而反问。
METHOD_RULE_MARKERS = (
    "界线", "分三档", "分成三档", "分档", "档位", "截止", "最迟", "提交材料",
    "准备哪些", "除以", "怎么办", "怎么处理", "如何处理", "零订单", "合理吗",
    "应该怎么", "口径", "定义", "是不是", "算哪几种", "怎么挑", "如何挑",
)
# 明确要求本轮数据库实际值时，仍按事实问题走 SQL/跨源义务检查。
ACTUAL_VALUE_MARKERS = (
    "实际", "数据库", "查数", "核验", "核对数据库", "销售额是多少",
    "销量是多少", "订单数是多少", "客户数是多少", "增长了多少",
)

MONTH_GROUPING = re.compile(r"每月|每个月|按月|按月份|月度|逐月|各月|分月|月累计")
RUNNING_TOTAL = re.compile(r"累计|滚动|累加|截至当月|到当月")
# 分组要求必须是"分配性"的：各品类、按分类、每个类型才算要按品类分行。
# 裸词"品类/分类"会把"属于哪个音乐分类"这种属性提问误判成分组查询。
GENRE_GROUPING = re.compile(
    r"(?:各|各个|每个|每|按|逐|所有|全部|不同)\s*(?:品类|分类|类型|风格|音乐分类|音乐类型)"
    r"|(?:品类|分类|类型|风格)\s*(?:分别|各自)")
# "分别/各/每/逐"单独出现只说明要多个指标或多列，不等于要多行。真正的多行要求来自
# 月分组、排名条数、已识别的分组维度，或明确的清单/明细/排名/前 N 措辞。
MULTI_ROW = re.compile(r"列出|清单|明细|排名|排行|前\s*(?:[0-9]+|[一二两三四五六七八九十]+)")
RANK_MARKERS = re.compile(r"最高|最低|排名|排行|top", re.I)
LIMIT_PATTERN = re.compile(
    r"(?:top|前|最高的?|最低的?)\s*([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|名|种|类|款)?",
    re.I,
)
CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 残留文本里属于"义务措辞"而非"过滤值"的词，先消掉再判断是否有未解析条件。
OBLIGATION_PHRASES = (
    "累计到当月", "截至当月", "滚动累计", "累计", "滚动", "显示为", "也要",
    "其他", "以外", "之外", "最高", "最低", "品类", "类型", "媒介", "成交",
    "分别", "各个", "每个", "逐月", "月度", "补零", "排名", "排序", "合计",
    "小计", "没有", "需要", "应该", "可以", "是不是", "怎么办",
)
OBLIGATION_CHARS = "月个各每按到当为也要没的了和与或及之除前后名零一二三四五六七八九十百千万0123456789."

# 让 _unparsed_query_text 把已登记词的删除位置标出来，残渣才能按片段分别判断，
# 而不是拼成一整块无法解读的字符串（例如"商品叫哪哪种媒体标价美元"）。
_RESIDUE_SEPARATOR = "\u241f"
_RESIDUE_PUNCTUATION = re.compile(r"[\s，,。;；:：?？!！、()（）\[\]【】\"'“”‘’]+")
# 只有"取值样"的残渣才是未解析过滤条件：拉丁字母词、地名、带人物角色前缀的姓名。
# 其余残渣是问句措辞（"列出""升序排列""金额""订单去重"），把它们当过滤器会让正常
# 问题被误判成丢条件而拒答。
_VALUE_ASCII = re.compile(r"[A-Za-z]")
_VALUE_PLACE = re.compile(r"^[\u4e00-\u9fff]{0,5}(?:国|州|省|市|区|县|岛)$")
_VALUE_PERSON = re.compile(
    r"^(?:歌手|艺人|艺术家|演奏者|乐队|作曲家|客户|顾客|员工|负责人)(?P<name>[\u4e00-\u9fff]{2,4})$")
# 角色前缀后面接通用业务词时，那是"客户编号"这类字段说法，不是人名。
_PERSON_GENERIC = ("编号", "号码", "名称", "姓名", "金额", "总额", "数量", "总数", "消费",
                   "清单", "明细", "哪些", "哪个", "什么", "多少", "分布", "列表", "订单",
                   "商品", "专辑", "国家", "地区", "城市", "类型", "品类", "分类")


def _value_token(chunk: str) -> str:
    """从一段未登记文本里取出真正的过滤取值；取不出就返回空串。"""
    token = _RESIDUE_PUNCTUATION.sub("", "".join(char for char in chunk if char not in OBLIGATION_CHARS))
    if not token:
        return ""
    if _VALUE_ASCII.search(token):
        return token
    if _VALUE_PLACE.match(token):
        return token
    person = _VALUE_PERSON.match(token)
    if person and not person.group("name").startswith(_PERSON_GENERIC):
        return token
    return ""


@dataclass(frozen=True)
class QueryObligations:
    """一轮问题的不可丢失要求。全部字段都可 JSON 序列化。"""

    consumed_entities: tuple[str, ...] = ()
    unresolved_terms: tuple[str, ...] = ()
    required_grouping: tuple[str, ...] = ()
    requires_month_grouping: bool = False
    requires_running_total: bool = False
    requires_rank: bool = False
    requested_limit: int | None = None
    requires_multiple_rows: bool = False
    requires_transaction_period: bool = True

    @property
    def has_unresolved_condition(self) -> bool:
        return bool(self.unresolved_terms)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in payload.items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "QueryObligations":
        """旧 checkpoint 或空字典都退化成默认义务，不抛错。"""
        if not payload:
            return cls()
        known = {name for name in cls.__dataclass_fields__}
        values = {name: value for name, value in payload.items() if name in known}
        for name in ("consumed_entities", "unresolved_terms", "required_grouping"):
            if name in values:
                values[name] = tuple(values[name] or ())
        if "requested_limit" in values and values["requested_limit"] is not None:
            values["requested_limit"] = int(values["requested_limit"])
        return cls(**values)


def _profile(profile_id: str, profile: "BusinessProfile | None") -> "BusinessProfile | None":
    if profile is not None:
        return profile
    from ..profiles import ProfileRegistry

    try:
        return ProfileRegistry.defaults().get(profile_id)
    except KeyError:
        return None


def _requested_limit(question: str) -> int | None:
    match = LIMIT_PATTERN.search(question)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if len(token) == 1:
        return CN_NUMBERS.get(token)
    # 十五、二十之类的两位数中文写法。
    tens, _, units = token.partition("十")
    value = (CN_NUMBERS.get(tens, 1) if tens else 1) * 10
    return value + CN_NUMBERS.get(units, 0) if units else value


def _residue_terms(question: str, profile: "BusinessProfile | None") -> tuple[str, ...]:
    """已登记词表消不掉、且长得像过滤取值的词才是未解析条件；义务措辞不算。"""
    if profile is None:
        return ()
    from .parsing import _unparsed_query_text

    residue = _unparsed_query_text(question, profile, separator=_RESIDUE_SEPARATOR)
    terms = []
    for chunk in residue.split(_RESIDUE_SEPARATOR):
        for phrase in OBLIGATION_PHRASES:
            chunk = chunk.replace(phrase, "")
        token = _value_token(chunk)
        if token:
            terms.append(token)
    return tuple(dict.fromkeys(terms))


def _is_rule_question(question: str, task_type: str, route: str) -> bool:
    if task_type == "document_rule" or route == "rag":
        return True
    asks_actual_value = any(marker in question for marker in ACTUAL_VALUE_MARKERS)
    return not asks_actual_value and any(marker in question for marker in METHOD_RULE_MARKERS)


def extract_query_obligations(
    question: str,
    profile_id: str = DEFAULT_PROFILE_ID,
    *,
    profile: "BusinessProfile | None" = None,
) -> QueryObligations:
    """解析一轮问题的义务。纯函数：不触达模型、SQL、检索或评测标准答案。"""
    text = (question or "").strip()
    resolved = _profile(profile_id, profile)
    metrics: list[str] = []
    if resolved is not None:
        from .parsing import _matched_metrics

        try:
            metrics = list(_matched_metrics(text, resolved.question_rules))
        except ValueError:
            metrics = []

    from .route_policy import classify_question

    task_type, route, _outputs = classify_question(text, metrics)
    rule_question = _is_rule_question(text, task_type, route)

    consumed: tuple[str, ...] = ()
    if resolved is not None:
        from .parsing import _entities

        consumed = tuple(dict.fromkeys(entity["id"] for entity in _entities(text, resolved)))

    grouping: list[str] = []
    intent_dimension = None
    top_n = None
    try:
        from backend.sql_module.query_intent import parse_query_intent

        intent = parse_query_intent(text, metrics)
        intent_dimension = intent.get("dimension")
        top_n = intent.get("top_n")
    except (ValueError, KeyError, ImportError):
        intent = {}

    requires_month = bool(MONTH_GROUPING.search(text)) or intent_dimension == "month"
    if requires_month:
        grouping.append("month")
    if intent_dimension and intent_dimension not in grouping and intent_dimension != "support_rep":
        grouping.append(str(intent_dimension))
    elif GENRE_GROUPING.search(text) and "genre" not in grouping:
        grouping.append("genre")

    limit = top_n if isinstance(top_n, int) else _requested_limit(text)
    requires_rank = bool(RANK_MARKERS.search(text)) or limit is not None
    requires_running_total = bool(RUNNING_TOTAL.search(text))
    requires_multiple_rows = bool(
        requires_month or requires_rank or limit is not None
        or MULTI_ROW.search(text) or len(grouping) > 0
    )

    return QueryObligations(
        consumed_entities=consumed,
        unresolved_terms=() if rule_question else _residue_terms(text, resolved),
        required_grouping=tuple(grouping),
        requires_month_grouping=requires_month,
        requires_running_total=requires_running_total,
        requires_rank=requires_rank,
        requested_limit=limit,
        requires_multiple_rows=requires_multiple_rows,
        # Customer record counts read the Customer catalog and do not need an
        # invoice period. Keep the registered global-count path valid after a
        # clarification turn.
        requires_transaction_period=(not rule_question and set(metrics) != {"customer_count"}),
    )
