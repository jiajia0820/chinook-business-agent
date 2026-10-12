# AdventureWorks SQL 评测集

题库文件：`eval/adventureworks/agent/eval_basic_nl2sql.jsonl`
题量：25

标准 SQL 只保存在评测端，用于复算 `gold_result`，不会发送给 Agent。

## aw-basic-001

问题：2013年总销售额是多少？

难度：easy　能力：annual_sales_total

标准答案：2013年总销售额为43,622,479.05美元。

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-002

问题：2013年一共卖出多少件产品？

难度：easy　能力：annual_units_total

标准答案：2013年共售出131,788件产品。

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-003

问题：2013年有多少张销售订单？

难度：easy　能力：annual_order_count

标准答案：2013年有14,182张销售订单。

预期数据库表：Sales.SalesOrderHeader

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-004

问题：AdventureWorks中有多少个客户？

难度：easy　能力：customer_count

标准答案：AdventureWorks中共有19,820个客户记录。

预期数据库表：Sales.Customer

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-005

问题：2013年销售额和销量分别是多少？

难度：easy　能力：annual_sales_and_units

标准答案：2013年指标如下（金额单位：美元，销量单位：件）：
1. 销售额 43,622,479.05，销量 131,788

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-006

问题：2013年每个月的销售额是多少？

难度：medium　能力：monthly_sales

标准答案：2013年各月销售额如下（美元）：
1. 月份 2013-01，销售额 2,087,872.46
2. 月份 2013-02，销售额 2,316,922.15
3. 月份 2013-03，销售额 3,412,068.97
4. 月份 2013-04，销售额 2,532,265.91
5. 月份 2013-05，销售额 3,245,623.75
6. 月份 2013-06，销售额 5,081,069.13
7. 月份 2013-07，销售额 4,896,353.74
8. 月份 2013-08，销售额 3,333,964.07
9. 月份 2013-09，销售额 4,532,908.71
10. 月份 2013-10，销售额 4,795,813.29
11. 月份 2013-11，销售额 3,312,130.25
12. 月份 2013-12，销售额 4,075,486.63

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-007

问题：2013年销售额最高的5个产品是什么？

难度：medium　能力：top_product_sales

标准答案：2013年销售额最高的5个产品如下（美元）：
1. 产品ID 782，产品 Mountain-200 Black, 38，销售额 2,212,974.78
2. 产品ID 783，产品 Mountain-200 Black, 42，销售额 1,932,388.29
3. 产品ID 779，产品 Mountain-200 Silver, 38，销售额 1,815,673.09
4. 产品ID 784，产品 Mountain-200 Black, 46，销售额 1,666,660.02
5. 产品ID 781，产品 Mountain-200 Silver, 46，销售额 1,657,616.28

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail, Production.Product

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-008

问题：2013年销量最高的5个产品是什么？

难度：medium　能力：top_product_units

标准答案：2013年销量最高的5个产品如下（件）：
1. 产品ID 870，产品 Water Bottle - 30 oz.，销量 3,913
2. 产品ID 712，产品 AWC Logo Cap，销量 3,768
3. 产品ID 708，产品 Sport-100 Helmet, Black，销量 3,088
4. 产品ID 711，产品 Sport-100 Helmet, Blue，销量 3,088
5. 产品ID 707，产品 Sport-100 Helmet, Red，销量 2,940

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail, Production.Product

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-009

问题：2013年各产品类别的销售额是多少？

难度：hard　能力：category_sales

标准答案：2013年各产品类别销售额如下（美元）：
1. 类别ID 1，类别 Bikes，销售额 36,266,829.36
2. 类别ID 2，类别 Components，销售额 5,612,935.34
3. 类别ID 3，类别 Clothing，销售额 1,067,689.70
4. 类别ID 4，类别 Accessories，销售额 675,024.65

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail, Production.Product, Production.ProductSubcategory, Production.ProductCategory

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-010

问题：2013年销售额最高的5个产品子类别是什么？

难度：hard　能力：subcategory_sales

标准答案：2013年销售额最高的5个产品子类别如下（美元）：
1. 子类别ID 2，子类别 Road Bikes，销售额 14,819,266.93
2. 子类别ID 1，子类别 Mountain Bikes，销售额 13,030,874.71
3. 子类别ID 3，子类别 Touring Bikes，销售额 8,416,687.71
4. 子类别ID 12，子类别 Mountain Frames，销售额 2,110,502.32
5. 子类别ID 14，子类别 Road Frames，销售额 1,598,089.41

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail, Production.Product, Production.ProductSubcategory

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-011

问题：2013年购买金额最高的5位客户是谁？

难度：hard　能力：customer_sales_ranking

标准答案：2013年购买金额最高的5位客户如下（金额单位：美元）：
1. 客户ID 29,641，销售额 418,802.45，订单数 3
2. 客户ID 29,913，销售额 416,854.13，订单数 4
3. 客户ID 29,818，销售额 382,337.58，订单数 4
4. 客户ID 29,701，销售额 360,839.91，订单数 4
5. 客户ID 29,617，销售额 326,364.42，订单数 4

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-012

问题：2013年的平均订单金额是多少？

难度：medium　能力：average_order_value

标准答案：2013年平均每张订单金额为3,075.90美元。

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-013

问题：2013年线上订单和线下订单的销售额分别是多少？

难度：medium　能力：online_offline_sales

标准答案：2013年线上/线下订单统计如下（金额单位：美元）：
1. 线上订单标记 True，销售额 10,732,127.33，订单数 12,584
2. 线上订单标记 False，销售额 32,890,351.72，订单数 1,598

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-014

问题：2013年每个月的订单数是多少？

难度：medium　能力：monthly_order_count

标准答案：2013年各月订单数如下：
1. 月份 2013-01，订单数 400
2. 月份 2013-02，订单数 325
3. 月份 2013-03，订单数 441
4. 月份 2013-04，订单数 428
5. 月份 2013-05，订单数 428
6. 月份 2013-06，订单数 719
7. 月份 2013-07，订单数 1,740
8. 月份 2013-08，订单数 1,789
9. 月份 2013-09，订单数 1,791
10. 月份 2013-10，订单数 1,968
11. 月份 2013-11，订单数 2,103
12. 月份 2013-12，订单数 2,050

预期数据库表：Sales.SalesOrderHeader

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-015

问题：AdventureWorks订单数据覆盖的最早和最晚订单日期是什么？

难度：easy　能力：latest_order_date

标准答案：订单数据最早日期为2011-05-31，最晚日期为2014-06-30。

预期数据库表：Sales.SalesOrderHeader

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-016

问题：产品ID 709 的累计销售额是多少？

难度：medium　能力：product_sales_by_id

标准答案：产品ID 709 的累计销售额如下（美元）：
1. 产品ID 709，产品 Mountain Bike Socks, M，累计销售额 6,060.39

预期数据库表：Production.Product, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-017

问题：列表价超过1000美元且从未销售的产品有多少个？

难度：hard　能力：unsold_expensive_products

标准答案：列表价超过1000美元且从未销售的产品共有8个。

预期数据库表：Production.Product, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-018

问题：各产品类别分别有多少个产品？

难度：medium　能力：category_product_count

标准答案：各产品类别的产品数如下：
1. 类别ID 1，类别 Bikes，产品数 97
2. 类别ID 2，类别 Components，产品数 134
3. 类别ID 3，类别 Clothing，产品数 35
4. 类别ID 4，类别 Accessories，产品数 29

预期数据库表：Production.ProductCategory, Production.ProductSubcategory, Production.Product

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-019

问题：各订单状态分别有多少张订单？

难度：easy　能力：status_order_breakdown

标准答案：各订单状态的订单数如下：
1. 订单状态 5，订单数 31,465

预期数据库表：Sales.SalesOrderHeader

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-basic-020

问题：2011年至2013年每年的销售额是多少？

难度：medium　能力：yearly_sales_trend

标准答案：2011年至2013年各年销售额如下（美元）：
1. 年份 2,011，销售额 12,641,672.21
2. 年份 2,012，销售额 33,524,301.32
3. 年份 2,013，销售额 43,622,479.05

预期数据库表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：标准 SQL 可在真实 AdventureWorks PostgreSQL 数据库执行；时间范围、关联关系、聚合口径和排序与问题一致

## aw-special-021

问题：销售额是多少？

难度：easy　能力：clarification_missing_year

标准答案：请补充统计年份或日期范围；例如 2013 年或 2013 年第一季度。

预期数据库表：无

评分要点：不默认猜测年份；要求补充时间范围

## aw-special-022

问题：2013年利润是多少？

难度：easy　能力：unsupported_profit

标准答案：当前 AdventureWorks 接入范围没有经过确认的利润口径；不能用销售额代替利润，需要补充成本或利润定义。

预期数据库表：无

评分要点：明确利润口径缺失；不把销售额当利润

## aw-special-023

问题：现在还有多少库存？

难度：easy　能力：unsupported_inventory

标准答案：当前评测 profile 没有开放库存表或库存快照，不能回答当前库存数量。

预期数据库表：无

评分要点：指出库存数据缺口；不从销售数量推断库存

## aw-special-024

问题：2015年销售额是多少？

难度：medium　能力：out_of_range

标准答案：当前 AdventureWorks 样例订单数据覆盖 2011-05-31 至 2014-06-30，2015 年不在数据覆盖范围内。

预期数据库表：无

评分要点：识别数据覆盖范围；不返回空范围的伪精确数值

## aw-special-025

问题：2013年没有销售记录的产品有哪些？

难度：hard　能力：empty_result

标准答案：2013年没有销售记录的产品如下；若结果为空，表示该年份每个产品都有销售记录。
1. 产品ID 1，产品 Adjustable Race
2. 产品ID 2，产品 Bearing Ball
3. 产品ID 3，产品 BB Ball Bearing
4. 产品ID 4，产品 Headset Ball Bearings
5. 产品ID 316，产品 Blade
6. 产品ID 317，产品 LL Crankarm
7. 产品ID 318，产品 ML Crankarm
8. 产品ID 319，产品 HL Crankarm
9. 产品ID 320，产品 Chainring Bolts
10. 产品ID 321，产品 Chainring Nut
11. 产品ID 322，产品 Chainring
12. 产品ID 323，产品 Crown Race
13. 产品ID 324，产品 Chain Stays
14. 产品ID 325，产品 Decal 1
15. 产品ID 326，产品 Decal 2
16. 产品ID 327，产品 Down Tube
17. 产品ID 328，产品 Mountain End Caps
18. 产品ID 329，产品 Road End Caps
19. 产品ID 330，产品 Touring End Caps
20. 产品ID 331，产品 Fork End
21. 产品ID 332，产品 Freewheel
22. 产品ID 341，产品 Flat Washer 1
23. 产品ID 342，产品 Flat Washer 6
24. 产品ID 343，产品 Flat Washer 2
25. 产品ID 344，产品 Flat Washer 9
26. 产品ID 345，产品 Flat Washer 4
27. 产品ID 346，产品 Flat Washer 3
28. 产品ID 347，产品 Flat Washer 8
29. 产品ID 348，产品 Flat Washer 5
30. 产品ID 349，产品 Flat Washer 7
31. 产品ID 350，产品 Fork Crown
32. 产品ID 351，产品 Front Derailleur Cage
33. 产品ID 352，产品 Front Derailleur Linkage
34. 产品ID 355，产品 Guide Pulley
35. 产品ID 356，产品 LL Grip Tape
36. 产品ID 357，产品 ML Grip Tape
37. 产品ID 358，产品 HL Grip Tape
38. 产品ID 359，产品 Thin-Jam Hex Nut 9
39. 产品ID 360，产品 Thin-Jam Hex Nut 10
40. 产品ID 361，产品 Thin-Jam Hex Nut 1
41. 产品ID 362，产品 Thin-Jam Hex Nut 2
42. 产品ID 363，产品 Thin-Jam Hex Nut 15
43. 产品ID 364，产品 Thin-Jam Hex Nut 16
44. 产品ID 365，产品 Thin-Jam Hex Nut 5
45. 产品ID 366，产品 Thin-Jam Hex Nut 6
46. 产品ID 367，产品 Thin-Jam Hex Nut 3
47. 产品ID 368，产品 Thin-Jam Hex Nut 4
48. 产品ID 369，产品 Thin-Jam Hex Nut 13
49. 产品ID 370，产品 Thin-Jam Hex Nut 14
50. 产品ID 371，产品 Thin-Jam Hex Nut 7
51. 产品ID 372，产品 Thin-Jam Hex Nut 8
52. 产品ID 373，产品 Thin-Jam Hex Nut 12
53. 产品ID 374，产品 Thin-Jam Hex Nut 11
54. 产品ID 375，产品 Hex Nut 5
55. 产品ID 376，产品 Hex Nut 6
56. 产品ID 377，产品 Hex Nut 16
57. 产品ID 378，产品 Hex Nut 17
58. 产品ID 379，产品 Hex Nut 7
59. 产品ID 380，产品 Hex Nut 8
60. 产品ID 381，产品 Hex Nut 9
61. 产品ID 382，产品 Hex Nut 22
62. 产品ID 383，产品 Hex Nut 23
63. 产品ID 384，产品 Hex Nut 12
64. 产品ID 385，产品 Hex Nut 13
65. 产品ID 386，产品 Hex Nut 1
66. 产品ID 387，产品 Hex Nut 10
67. 产品ID 388，产品 Hex Nut 11
68. 产品ID 389，产品 Hex Nut 2
69. 产品ID 390，产品 Hex Nut 20
70. 产品ID 391，产品 Hex Nut 21
71. 产品ID 392，产品 Hex Nut 3
72. 产品ID 393，产品 Hex Nut 14
73. 产品ID 394，产品 Hex Nut 15
74. 产品ID 395，产品 Hex Nut 4
75. 产品ID 396，产品 Hex Nut 18
76. 产品ID 397，产品 Hex Nut 19
77. 产品ID 398，产品 Handlebar Tube
78. 产品ID 399，产品 Head Tube
79. 产品ID 400，产品 LL Hub
80. 产品ID 401，产品 HL Hub
81. 产品ID 402，产品 Keyed Washer
82. 产品ID 403，产品 External Lock Washer 3
83. 产品ID 404，产品 External Lock Washer 4
84. 产品ID 405，产品 External Lock Washer 9
85. 产品ID 406，产品 External Lock Washer 5
86. 产品ID 407，产品 External Lock Washer 7
87. 产品ID 408，产品 External Lock Washer 6
88. 产品ID 409，产品 External Lock Washer 1
89. 产品ID 410，产品 External Lock Washer 8
90. 产品ID 411，产品 External Lock Washer 2
91. 产品ID 412，产品 Internal Lock Washer 3
92. 产品ID 413，产品 Internal Lock Washer 4
93. 产品ID 414，产品 Internal Lock Washer 9
94. 产品ID 415，产品 Internal Lock Washer 5
95. 产品ID 416，产品 Internal Lock Washer 7
96. 产品ID 417，产品 Internal Lock Washer 6
97. 产品ID 418，产品 Internal Lock Washer 10
98. 产品ID 419，产品 Internal Lock Washer 1
99. 产品ID 420，产品 Internal Lock Washer 8
100. 产品ID 421，产品 Internal Lock Washer 2
101. 产品ID 422，产品 Thin-Jam Lock Nut 9
102. 产品ID 423，产品 Thin-Jam Lock Nut 10
103. 产品ID 424，产品 Thin-Jam Lock Nut 1
104. 产品ID 425，产品 Thin-Jam Lock Nut 2
105. 产品ID 426，产品 Thin-Jam Lock Nut 15
106. 产品ID 427，产品 Thin-Jam Lock Nut 16
107. 产品ID 428，产品 Thin-Jam Lock Nut 5
108. 产品ID 429，产品 Thin-Jam Lock Nut 6
109. 产品ID 430，产品 Thin-Jam Lock Nut 3
110. 产品ID 431，产品 Thin-Jam Lock Nut 4
111. 产品ID 432，产品 Thin-Jam Lock Nut 13
112. 产品ID 433，产品 Thin-Jam Lock Nut 14
113. 产品ID 434，产品 Thin-Jam Lock Nut 7
114. 产品ID 435，产品 Thin-Jam Lock Nut 8
115. 产品ID 436，产品 Thin-Jam Lock Nut 12
116. 产品ID 437，产品 Thin-Jam Lock Nut 11
117. 产品ID 438，产品 Lock Nut 5
118. 产品ID 439，产品 Lock Nut 6
119. 产品ID 440，产品 Lock Nut 16
120. 产品ID 441，产品 Lock Nut 17
121. 产品ID 442，产品 Lock Nut 7
122. 产品ID 443，产品 Lock Nut 8
123. 产品ID 444，产品 Lock Nut 9
124. 产品ID 445，产品 Lock Nut 22
125. 产品ID 446，产品 Lock Nut 23
126. 产品ID 447，产品 Lock Nut 12
127. 产品ID 448，产品 Lock Nut 13
128. 产品ID 449，产品 Lock Nut 1
129. 产品ID 450，产品 Lock Nut 10
130. 产品ID 451，产品 Lock Nut 11
131. 产品ID 452，产品 Lock Nut 2
132. 产品ID 453，产品 Lock Nut 20
133. 产品ID 454，产品 Lock Nut 21
134. 产品ID 455，产品 Lock Nut 3
135. 产品ID 456，产品 Lock Nut 14
136. 产品ID 457，产品 Lock Nut 15
137. 产品ID 458，产品 Lock Nut 4
138. 产品ID 459，产品 Lock Nut 19
139. 产品ID 460，产品 Lock Nut 18
140. 产品ID 461，产品 Lock Ring
141. 产品ID 462，产品 Lower Head Race
142. 产品ID 463，产品 Lock Washer 4
143. 产品ID 464，产品 Lock Washer 5
144. 产品ID 465，产品 Lock Washer 10
145. 产品ID 466，产品 Lock Washer 6
146. 产品ID 467，产品 Lock Washer 13
147. 产品ID 468，产品 Lock Washer 8
148. 产品ID 469，产品 Lock Washer 1
149. 产品ID 470，产品 Lock Washer 7
150. 产品ID 471，产品 Lock Washer 12
151. 产品ID 472，产品 Lock Washer 2
152. 产品ID 473，产品 Lock Washer 9
153. 产品ID 474，产品 Lock Washer 3
154. 产品ID 475，产品 Lock Washer 11
155. 产品ID 476，产品 Metal Angle
156. 产品ID 477，产品 Metal Bar 1
157. 产品ID 478，产品 Metal Bar 2
158. 产品ID 479，产品 Metal Plate 2
159. 产品ID 480，产品 Metal Plate 1
160. 产品ID 481，产品 Metal Plate 3
161. 产品ID 482，产品 Metal Sheet 2
162. 产品ID 483，产品 Metal Sheet 3
163. 产品ID 484，产品 Metal Sheet 7
164. 产品ID 485，产品 Metal Sheet 4
165. 产品ID 486，产品 Metal Sheet 5
166. 产品ID 487，产品 Metal Sheet 6
167. 产品ID 488，产品 Metal Sheet 1
168. 产品ID 489，产品 Metal Tread Plate
169. 产品ID 490，产品 LL Nipple
170. 产品ID 491，产品 HL Nipple
171. 产品ID 492，产品 Paint - Black
172. 产品ID 493，产品 Paint - Red
173. 产品ID 494，产品 Paint - Silver
174. 产品ID 495，产品 Paint - Blue
175. 产品ID 496，产品 Paint - Yellow
176. 产品ID 497，产品 Pinch Bolt
177. 产品ID 504，产品 Cup-Shaped Race
178. 产品ID 505，产品 Cone-Shaped Race
179. 产品ID 506，产品 Reflector
180. 产品ID 507，产品 LL Mountain Rim
181. 产品ID 508，产品 ML Mountain Rim
182. 产品ID 509，产品 HL Mountain Rim
183. 产品ID 510，产品 LL Road Rim
184. 产品ID 511，产品 ML Road Rim
185. 产品ID 512，产品 HL Road Rim
186. 产品ID 513，产品 Touring Rim
187. 产品ID 514，产品 LL Mountain Seat Assembly
188. 产品ID 515，产品 ML Mountain Seat Assembly
189. 产品ID 516，产品 HL Mountain Seat Assembly
190. 产品ID 517，产品 LL Road Seat Assembly
191. 产品ID 518，产品 ML Road Seat Assembly
192. 产品ID 519，产品 HL Road Seat Assembly
193. 产品ID 520，产品 LL Touring Seat Assembly
194. 产品ID 521，产品 ML Touring Seat Assembly
195. 产品ID 522，产品 HL Touring Seat Assembly
196. 产品ID 523，产品 LL Spindle/Axle
197. 产品ID 524，产品 HL Spindle/Axle
198. 产品ID 525，产品 LL Shell
199. 产品ID 526，产品 HL Shell
200. 产品ID 527，产品 Spokes
201. 产品ID 528，产品 Seat Lug
202. 产品ID 529，产品 Stem
203. 产品ID 530，产品 Seat Post
204. 产品ID 531，产品 Steerer
205. 产品ID 532，产品 Seat Stays
206. 产品ID 533，产品 Seat Tube
207. 产品ID 534，产品 Top Tube
208. 产品ID 535，产品 Tension Pulley
209. 产品ID 679，产品 Rear Derailleur Cage
210. 产品ID 680，产品 HL Road Frame - Black, 58
211. 产品ID 706，产品 HL Road Frame - Red, 58
212. 产品ID 709，产品 Mountain Bike Socks, M
213. 产品ID 710，产品 Mountain Bike Socks, L
214. 产品ID 720，产品 HL Road Frame - Red, 52
215. 产品ID 721，产品 HL Road Frame - Red, 56
216. 产品ID 724，产品 LL Road Frame - Black, 62
217. 产品ID 728，产品 LL Road Frame - Red, 58
218. 产品ID 731，产品 ML Road Frame - Red, 44
219. 产品ID 732，产品 ML Road Frame - Red, 48
220. 产品ID 733，产品 ML Road Frame - Red, 52
221. 产品ID 734，产品 ML Road Frame - Red, 58
222. 产品ID 735，产品 ML Road Frame - Red, 60
223. 产品ID 737，产品 LL Road Frame - Black, 48
224. 产品ID 740，产品 HL Mountain Frame - Silver, 44
225. 产品ID 741，产品 HL Mountain Frame - Silver, 48
226. 产品ID 744，产品 HL Mountain Frame - Black, 44
227. 产品ID 745，产品 HL Mountain Frame - Black, 48
228. 产品ID 746，产品 HL Mountain Frame - Black, 46
229. 产品ID 749，产品 Road-150 Red, 62
230. 产品ID 750，产品 Road-150 Red, 44
231. 产品ID 751，产品 Road-150 Red, 48
232. 产品ID 752，产品 Road-150 Red, 52
233. 产品ID 753，产品 Road-150 Red, 56
234. 产品ID 754，产品 Road-450 Red, 58
235. 产品ID 755，产品 Road-450 Red, 60
236. 产品ID 756，产品 Road-450 Red, 44
237. 产品ID 757，产品 Road-450 Red, 48
238. 产品ID 758，产品 Road-450 Red, 52
239. 产品ID 771，产品 Mountain-100 Silver, 38
240. 产品ID 772，产品 Mountain-100 Silver, 42
241. 产品ID 773，产品 Mountain-100 Silver, 44
242. 产品ID 774，产品 Mountain-100 Silver, 48
243. 产品ID 775，产品 Mountain-100 Black, 38
244. 产品ID 776，产品 Mountain-100 Black, 42
245. 产品ID 777，产品 Mountain-100 Black, 44
246. 产品ID 778，产品 Mountain-100 Black, 48
247. 产品ID 803，产品 ML Fork
248. 产品ID 805，产品 LL Headset
249. 产品ID 812，产品 ML Road Handlebars
250. 产品ID 818，产品 LL Road Front Wheel
251. 产品ID 821，产品 Touring Front Wheel
252. 产品ID 827，产品 ML Road Rear Wheel
253. 产品ID 829，产品 Touring Rear Wheel
254. 产品ID 830，产品 ML Mountain Frame - Black, 40
255. 产品ID 834，产品 ML Road Frame-W - Yellow, 42
256. 产品ID 837，产品 HL Road Frame - Black, 62
257. 产品ID 840，产品 HL Road Frame - Black, 52
258. 产品ID 842，产品 Touring-Panniers, Large
259. 产品ID 845，产品 Mountain Pump
260. 产品ID 846，产品 Taillights - Battery-Powered
261. 产品ID 847，产品 Headlights - Dual-Beam
262. 产品ID 848，产品 Headlights - Weatherproof
263. 产品ID 851，产品 Men's Sports Shorts, XL
264. 产品ID 901，产品 LL Touring Frame - Yellow, 54
265. 产品ID 912，产品 ML Road Seat/Saddle
266. 产品ID 995，产品 ML Bottom Bracket

预期数据库表：Production.Product, Sales.SalesOrderDetail, Sales.SalesOrderHeader

评分要点：使用左连接或NOT EXISTS正确识别空集合；如结果为空要明确说明

