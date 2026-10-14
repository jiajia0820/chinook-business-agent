"""Small, model-only answer contract for general knowledge questions."""

import json


MAX_GENERAL_ANSWER_CHARS = 4000


def general_model_messages(question: str) -> list[dict[str, str]]:
    instructions = (
        "你是通用知识问答助手。回答用户的一般知识问题，先给结论，再给必要的解释或例子。"
        "不要假装掌握本项目、公司业务或数据库中的事实；如果问题要求这些内容，应说明需要查项目或业务资料。"
        "不要声称已经联网或掌握最新信息。回答简洁，避免编造精确数字、链接和来源。"
        '只输出JSON：{\"answer\":\"面向用户的回答\"}。'
    )
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": question},
    ]


def validate_general_answer(payload: str) -> str:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("通用回答模型未返回合法 JSON") from None
    if not isinstance(value, dict) or set(value) != {"answer"}:
        raise ValueError("通用回答模型输出字段不符合约定")
    answer = value["answer"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("通用回答模型返回空答案")
    answer = answer.strip()
    if len(answer) > MAX_GENERAL_ANSWER_CHARS:
        raise ValueError("通用回答模型输出过长")
    return answer
