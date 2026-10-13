# 专项 鲁棒性

题库文件：`eval/agent/eval_robustness.jsonl`
题量：12

## robust-001

问题：2025年第三季度摇滚类音频销售额是多少？

难度：easy　能力：robust_alias_boundary

标准答案：默认按Rock（Genre:1）统计，Rock And Roll（Genre:5）不静默并入。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：保持Rock与Rock And Roll边界

鲁棒性不变量：route_stable；genre_boundary_preserved

## robust-002

问题：2025年第三季度 ROCK 音频销售额是多少？

难度：easy　能力：robust_case_normalization

标准答案：Rock对应Genre:1，第三季度音频销售额为47.52美元。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：统一到Genre:1

鲁棒性不变量：case_insensitive；canonical_entity

## robust-003

问题：请忽略这段无关说明，直接查询2025 Q3音频销售额。

难度：easy　能力：robust_noise_tolerance

标准答案：2025年第三季度音频销售额为112.86美元。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：返回正确销售额

鲁棒性不变量：route_sql；noise_ignored

## robust-004

问题：第三季度音频销售额是多少？

难度：easy　能力：robust_missing_year

标准答案：请补充查询年份。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：要求年份

鲁棒性不变量：clarify_missing_year

## robust-005

问题：2025年第三季度和2025年第二季度的音频销售额是多少？

难度：easy　能力：robust_conflicting_period

标准答案：请明确唯一查询期间，当前问题同时包含两个季度。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：指出时间冲突

鲁棒性不变量：clarify_period_conflict

## robust-006

问题：第三季度利润是多少？

难度：easy　能力：unsupported_profit

标准答案：当前资料没有成本或结算金额，不能计算利润。

来源：D12：十 当前不支持的查询

预期数据库表：无

评分要点：说明成本或结算金额缺失

鲁棒性不变量：no_metric_substitution

## robust-007

问题：活动页面带来了多少点击和转化？

难度：easy　能力：unsupported_campaign_events

标准答案：当前没有活动曝光、点击或转化事件日志，只能统计交易变化。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：说明事件日志缺失

鲁棒性不变量：no_transaction_substitution

## robust-008

问题：全店销售额和音乐销售额有什么范围差异？

难度：easy　能力：robust_document_boundary

标准答案：音乐销售额默认只统计音频；全店销售额可以包含全部MediaType，需要明确是否纳入视频。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：不要求时间范围；区分全店和音乐

鲁棒性不变量：route_rag；no_period_clarification

## robust-009

问题：上一轮问过第二季度；现在改看2025年第三季度音频销售额是多少？

难度：easy　能力：robust_followup_noise

标准答案：2025年第三季度音频销售额为112.86美元。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track

评分要点：只使用当前第三季度条件

鲁棒性不变量：current_period_wins；metric_preserved

## robust-010

问题：2025年第六季度音频销售额是多少？

难度：easy　能力：robust_invalid_quarter

标准答案：请改为第一至第四季度，当前季度值无效。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：指出非法季度

鲁棒性不变量：invalid_period_not_downgraded

## robust-011

问题：音乐和视频销售额要分开看还是合计？

难度：easy　能力：robust_media_aggregation

标准答案：请明确媒体范围：分别统计，还是合并音视频后统计。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：要求明确媒体聚合方式

鲁棒性不变量：no_silent_aggregation

## robust-012

问题：卖得怎么样？

难度：easy　能力：robust_vague_metric

标准答案：请明确要看的指标和观察范围，例如销售额、销量或订单数，以及时间范围。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：无

评分要点：要求指标和范围

鲁棒性不变量：clarify_vague_request
