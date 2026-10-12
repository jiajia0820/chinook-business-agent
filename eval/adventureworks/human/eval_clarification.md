# eval_clarification

题量：8

## aw-clarify-001

问题：销售额是多少？

参考行为：请补充统计年份或日期范围，例如 2013 年或 2013 年第一季度。

澄清槽位：year, time_range

## aw-clarify-002

问题：2013年表现怎么样？

参考行为：请明确要查询的指标，例如销售额、销量、订单数或客户数。

澄清槽位：metric

## aw-clarify-003

问题：2013年产品排名是什么？

参考行为：请明确排名指标，例如按销售额排名还是按销量排名。

澄清槽位：metric

## aw-clarify-004

问题：2013年客户情况是多少？

参考行为：请明确客户指标，例如客户记录数、购买客户数或客户购买金额排名。

澄清槽位：metric

## aw-clarify-005

问题：订单增长率是多少？

参考行为：请补充比较周期，例如 2013 年相对 2012 年，或具体两个季度。

澄清槽位：comparison_period

## aw-clarify-006

问题：2013年销售额最高的是谁？

参考行为：请明确‘谁’指产品、产品子类别、产品类别还是客户。

澄清槽位：entity_type

## aw-clarify-007

问题：2013年销售额是多少，按下单日期还是发货日期？

参考行为：请确认日期口径：按下单日期（OrderDate）还是发货日期（ShipDate）统计。

澄清槽位：date_field

## aw-clarify-008

问题：2013年销售额，按类别。

参考行为：请确认按产品类别（ProductCategory）还是产品子类别（ProductSubcategory）分组。

澄清槽位：entity_type
