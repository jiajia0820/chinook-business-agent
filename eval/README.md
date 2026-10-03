# 评测集目录

评测集分为两层：

- `agent/`：给程序和 Agent 测试使用的机器题库。JSONL 中包含自然语言问题、标准答案、SQL、来源和接口期望字段；实际发送给 Agent 时只取题目请求字段。
- `human/`：给人阅读和核查使用的题库说明、Markdown 题目、标准答案、来源和校验报告。

正式题库的数量和文件映射见 [agent/eval_manifest.json](agent/eval_manifest.json)，可读说明见 [human/README.md](human/README.md)。
