# 跨源问答

题库文件：`eval/agent/eval_cross_source.jsonl`
题量：10

## cross-001

问题：第三季度Rock销售额达到目标了吗？差多少？

难度：medium　能力：actual_vs_target

标准答案：Rock实际销售额47.52美元，目标50.00美元，差额-2.48美元，达成率95.04%，未达标。

来源：D06：附件1 目标明细，第1页；D08：三 目标达成情况，第2页

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：数据库实际值正确；目标取D06；说明D08结论

计算记录：{"actual": 47.52, "target": 50.0, "gap": "-2.48", "rate": "95.04%"}

## cross-002

问题：第三季度全音频销售额和销量是否都达标？

难度：medium　能力：actual_vs_target

标准答案：两项都未达标：销售额112.86/120.00美元，达成率94.05%；销量114/125件，达成率91.20%。

来源：D06：附件1 目标明细，第1页；D08：一 经营摘要，第1页

预期数据库表：Invoice, InvoiceLine, Track

评分要点：分别判断销售额和销量

## cross-003

问题：第三季度销售额最高的品类是什么，运营手册建议关注什么？

难度：medium　能力：genre_action

标准答案：Rock最高，销售额47.52美元。运营手册建议继续整理成交商品和专辑，关注专题陈列、订单集中和专辑覆盖。

来源：D08：三 目标达成情况，第2页；D09：Rock 品类运营

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：实际排名正确；动作来自D09

## cross-004

问题：第三季度购买Rock的客户，哪些属于A层重点维护？

难度：hard　能力：customer_tiering

标准答案：第三季度购买Rock的客户中，按D04当季音频销售额大于等于12.00美元的A层客户是CustomerId 10、31、48。Rock购买事实来自数据库，A层判断使用客户当季全部音频销售额，不是只看Rock金额。

来源：D04：三 活跃客户层级定义；D04：七 七 第三季度分层结果示例

预期数据库表：Invoice, InvoiceLine, Track, Customer

评分要点：列出CustomerId 10、31、48；客户事实来自数据库；层级阈值来自D04

## cross-005

问题：第三季度比第二季度增长了多少，D02建议如何解读？

难度：hard　能力：quarter_compare_action

标准答案：销售额增加3.96美元，增长约3.64%。D02强调增长比较和目标达成是两种判断，因此三季度虽有增长，仍需另看120.00美元目标是否完成。

来源：D07：一 经营摘要，第1页；D08：一 经营摘要，第1页；D02：增长率和特殊分母

预期数据库表：Invoice, InvoiceLine, Track

评分要点：跨期计算正确；引用D02解释

## cross-006

问题：海报要求的Rock主页面商品中，TrackId 1791-1799在第三季度是否有成交？

难度：hard　能力：poster_product_sales

标准答案：五个商品均有成交：TrackId 1791、1793、1795、1797、1799 的第三季度销售额均为0.99美元。候选范围来自D05，主页面条件来自D11。

来源：D11：报名条件，图片区域 eligibility；D05：四 Rock 商品选品，第2页

预期数据库表：Invoice, InvoiceLine, Track

评分要点：图片条件与数据库商品相接；列出五个TrackId；每个销售额为0.99美元

## cross-007

问题：第三季度Metal未达目标，活动方案把Metal放在什么位置？

难度：medium　能力：metal_gap_activity

标准答案：Metal实际销售额21.78美元，目标24.00美元，差额-2.22美元；D10把Metal安排为Rock主题活动的延伸栏目，不作为主页面主品类。

来源：D08：三 目标达成情况，第2页；D10：一 项目背景

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：目标比较正确；活动位置准确

## cross-008

问题：第三季度Latin距离目标多少，D09建议观察什么？

难度：medium　能力：latin_gap_action

标准答案：Latin实际15.84美元，目标18.00美元，差额-2.16美元，销量16件；D09建议继续观察商品组合和客户覆盖，先核对成交明细。

来源：D08：三 目标达成情况，第2页；D09：Latin

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：金额和动作均正确

## cross-009

问题：2025年第三季度每月全音频实际销售额与目标计划分别是多少？

难度：hard　能力：monthly_plan_actual

标准答案：计划为7月38.00、8月40.00、9月42.00美元；数据库按订单明细算出的实际为7月37.62、8月37.62、9月37.62美元。D08文档写8月36.63美元，这与数据库及112.86美元季度合计冲突，回答时必须标注冲突，不能静默选择一个数字。

来源：D06：附件2 月度检查安排，第2页；D08：二 月度节奏，第1页

预期数据库表：Invoice, InvoiceLine, Track

评分要点：给出数据库三个月实际值；给出D08冲突值；解释112.86与36.63不相加

计算记录：{"database_monthly_actual": {"2025-07": 37.62, "2025-08": 37.62, "2025-09": 37.62}, "document_D08_claim": {"2025-08": 36.63}, "conflict": "D08的8月数字与数据库不一致；D08数字与季度112.86也不相加"}

## cross-010

问题：D03规定Metal和Heavy Metal分开时，D05的Metal候选应使用哪个Genre？

难度：medium　能力：classification_selection

标准答案：D05的Metal候选使用Genre:3；Heavy Metal为Genre:13，不自动并入Metal主列表。

来源：D03：Metal 的消歧；D05：五 Metal 商品选品，第2页

预期数据库表：无

评分要点：两个文档边界一致
