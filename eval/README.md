# 评测集目录

评测集分为两层：

- `agent/`：给程序和 Agent 测试使用的机器题库。JSONL 中包含自然语言问题、标准答案、SQL、来源和接口期望字段；实际发送给 Agent 时只取题目请求字段。
- `human/`：给人阅读和核查使用的题库说明、Markdown 题目、标准答案、来源和校验报告。

正式题库的数量和文件映射见 [agent/eval_manifest.json](agent/eval_manifest.json)，可读说明见 [human/README.md](human/README.md)。

## 题目书写约定

- 需要数据库事实的题目必须写全年份（例如「2025年第三季度」）。只有季度没有年份时，Agent 一律要求澄清，不会默认锚定某一年，这类题目会被判为意外澄清而失败。
- 只有 `agent/eval_special_clarification.jsonl` 与 `agent/eval_multiturn.jsonl` 中标记 `clarification_required` 的题目允许缺年份，它们考的正是澄清行为。
- 多轮会话只在第 1 轮写全年份，后续轮次靠会话记忆继承；不要每轮重复年份。
- 用户前提（如「Metal 未达目标」）不会被重新核验。要考跨源核验，题目必须显式提出核验诉求（如「距离目标差多少」）。
- `agent/` 与 `human/` 两份题面必须同步修改，生成器 `scripts/build_eval_set.py` 里的题面也要一起改。
