# 专项 无证据处理

题库文件：`eval/agent/eval_special_negative.jsonl`
题量：5

## special-negative-001

问题：第三季度利润是多少？

难度：easy　能力：unsupported_profit

标准答案：当前资料没有成本或利润数据，不能计算利润；需要补充成本或结算金额。

来源：D12：十 当前不支持的查询

预期数据库表：无

评分要点：明确数据缺口；不拿销售额代替利润

## special-negative-002

问题：哪个播放列表播放量最高？

难度：easy　能力：unsupported_playback

标准答案：当前只有Playlist收录关系，没有播放次数或收听日志，不能判断播放量。

来源：D03：五 Playlist 的使用边界；D12：十 当前不支持的查询

预期数据库表：无

评分要点：拒绝播放量推断

## special-negative-003

问题：Rock现在还剩多少库存？

难度：easy　能力：unsupported_inventory

标准答案：当前数据没有库存快照，无法回答库存数量。

来源：D12：十 当前不支持的查询

预期数据库表：无

评分要点：指出库存数据缺失

## special-negative-004

问题：活动页面带来了多少点击和转化？

难度：medium　能力：unsupported_campaign

标准答案：当前没有活动曝光、点击或转化事件数据，只能统计活动期间的交易变化。

来源：D10：一 项目背景；D12：十 当前不支持的查询

预期数据库表：无

评分要点：区分交易数据和活动事件数据

## special-negative-005

问题：能按SupportRep给员工做2025年销售额排名吗？

难度：easy　能力：unsupported_employee_sales

标准答案：不能。SupportRep表示客户维护责任，不等同于个人销售归属或提成贡献。

来源：D04：八 与支持负责人的关系

预期数据库表：无

评分要点：说明字段含义边界
