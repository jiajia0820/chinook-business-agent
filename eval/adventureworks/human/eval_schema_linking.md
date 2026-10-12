# eval_schema_linking

题量：8

标准 SQL 留在评测端，用于真实数据库复算 gold_result，不发送给 Agent。

## aw-schema-001

问题：2013年按下单日期统计的销售额是多少？

能力：order_date_linking

标准答案：按下单日期统计，2013年销售额为43,622,479.05美元。

预期表：Sales.SalesOrderHeader, Sales.SalesOrderDetail

评分要点：使用 SalesOrderHeader.OrderDate；不使用 DueDate、ShipDate 或 ModifiedDate

## aw-schema-002

问题：2013年按发货日期统计的订单数是多少？

能力：ship_date_linking

标准答案：按发货日期统计，2013年订单数为13,752张。

预期表：Sales.SalesOrderHeader

评分要点：使用 ShipDate；按 SalesOrderID 去重

## aw-schema-003

问题：2013年到期日期落在年内的订单数是多少？

能力：due_date_linking

标准答案：按到期日期统计，2013年订单数为13,514张。

预期表：Sales.SalesOrderHeader

评分要点：使用 DueDate；不能误用 OrderDate

## aw-schema-004

问题：各产品的累计销售额是多少？

能力：product_table_linking

标准答案：按 Production.Product.Name 展示产品销售额（前10个产品，美元）：
1. 产品ID 707，产品 Sport-100 Helmet, Red，销售额 157,772.39
2. 产品ID 708，产品 Sport-100 Helmet, Black，销售额 160,869.52
3. 产品ID 709，产品 Mountain Bike Socks, M，销售额 6,060.39
4. 产品ID 710，产品 Mountain Bike Socks, L，销售额 513.00
5. 产品ID 711，产品 Sport-100 Helmet, Blue，销售额 165,406.62
6. 产品ID 712，产品 AWC Logo Cap，销售额 51,229.45
7. 产品ID 713，产品 Long-Sleeve Logo Jersey, S，销售额 21,445.71
8. 产品ID 714，产品 Long-Sleeve Logo Jersey, M，销售额 115,249.21
9. 产品ID 715，产品 Long-Sleeve Logo Jersey, L，销售额 198,754.98
10. 产品ID 716，产品 Long-Sleeve Logo Jersey, XL，销售额 95,611.20

预期表：Production.Product, Sales.SalesOrderDetail

评分要点：产品名称来自 Production.Product；通过 ProductID 关联明细

## aw-schema-005

问题：2013年各产品类别的销售额是多少？

能力：category_path_linking

标准答案：2013年各产品类别销售额如下（美元）：
1. 类别ID 1，类别 Bikes，销售额 36,266,829.36
2. 类别ID 2，类别 Components，销售额 5,612,935.34
3. 类别ID 3，类别 Clothing，销售额 1,067,689.70
4. 类别ID 4，类别 Accessories，销售额 675,024.65

预期表：Sales.SalesOrderHeader, Sales.SalesOrderDetail, Production.Product, Production.ProductSubcategory, Production.ProductCategory

评分要点：使用 Product→ProductSubcategory→ProductCategory 路径；不直接把产品当类别

## aw-schema-006

问题：每个客户的订单数是多少？

能力：customer_header_linking

标准答案：前10位客户的订单数如下：
1. 客户ID 11,000，订单数 3
2. 客户ID 11,001，订单数 3
3. 客户ID 11,002，订单数 3
4. 客户ID 11,003，订单数 3
5. 客户ID 11,004，订单数 3
6. 客户ID 11,005，订单数 3
7. 客户ID 11,006，订单数 3
8. 客户ID 11,007，订单数 3
9. 客户ID 11,008，订单数 3
10. 客户ID 11,009，订单数 3

预期表：Sales.SalesOrderHeader

评分要点：使用 SalesOrderHeader.CustomerID；按订单去重

## aw-schema-007

问题：2013年订单修改时间在年内的订单数是多少？

能力：modified_date_linking

标准答案：按 ModifiedDate 统计，2013年修改过的订单数为13,752张。

预期表：Sales.SalesOrderHeader

评分要点：使用 ModifiedDate；不把修改日期解释为下单日期

## aw-schema-008

问题：每个产品类别有多少个产品？

能力：category_product_path

标准答案：各产品类别的产品数如下：
1. 类别ID 1，类别 Bikes，产品数 97
2. 类别ID 2，类别 Components，产品数 134
3. 类别ID 3，类别 Clothing，产品数 35
4. 类别ID 4，类别 Accessories，产品数 29

预期表：Production.ProductCategory, Production.ProductSubcategory, Production.Product

评分要点：正确经过子类别关联产品；按 ProductID 计数
