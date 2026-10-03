# 专项 澄清

题库文件：`eval/agent/eval_special_clarification.jsonl`
题量：8

## special-clarify-001

问题：销售增长率是多少？

难度：easy　能力：missing_period

标准答案：请先说明比较哪两个完整周期，以及统计全部音频还是某个品类。

来源：D01：环比，第1页

预期数据库表：无

评分要点：不猜比较期；要求指标范围

## special-clarify-002

问题：第三季度Rock卖得怎么样？

难度：easy　能力：missing_metric

标准答案：请明确想看销售额、销量、订单数、购买客户数，还是目标达成情况。

来源：D02：分析需求的接收和确认

预期数据库表：无

评分要点：澄清指标

## special-clarify-003

问题：第三季度销售额是多少？

难度：easy　能力：missing_year

标准答案：请确认年份；如果按当前经营资料，应指定2025年第三季度。

来源：D01：时间区间，第1页

预期数据库表：无

评分要点：澄清年份

## special-clarify-004

问题：音乐和视频销售额要分开看还是合计？

难度：easy　能力：ambiguous_media

标准答案：请确认需要分开列示音频与视频，还是统计全店合计。

来源：D03：三 MediaType 和商品范围

预期数据库表：无

评分要点：澄清合计方式

## special-clarify-005

问题：某商品属于什么类型？

难度：easy　能力：ambiguous_type

标准答案：请明确想查Genre、MediaType、所属Album，还是所在Playlist。

来源：D03：商品、专辑和艺术家范围

预期数据库表：无

评分要点：澄清类型维度

## special-clarify-006

问题：哪些客户是高价值客户？

难度：medium　能力：tier_period

标准答案：请指定观察周期，或确认按2025年第三季度客户分层规则判断。

来源：D04：分层计算口径

预期数据库表：无

评分要点：澄清周期

## special-clarify-007

问题：销售额达到目标了吗？

难度：medium　能力：target_scope

标准答案：请明确季度、品类或全音频范围；如果是第三季度，还需确认看销售额目标还是销售额和销量两个目标。

来源：D06：附件1 目标明细，第1页

预期数据库表：无

评分要点：澄清周期、范围、目标指标

## special-clarify-008

问题：摇滚相关销售额是多少？

难度：medium　能力：genre_scope

标准答案：请确认是否把Rock And Roll（Genre:5）并入Rock（Genre:1）。默认Rock和Rock And Roll分开统计。

来源：D03：Rock 的消歧

预期数据库表：无

评分要点：澄清分类边界
