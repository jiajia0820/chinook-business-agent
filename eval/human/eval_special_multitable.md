# 专项 多表关联

题库文件：`eval/agent/eval_special_multitable.jsonl`
题量：8

## special-multitable-001

问题：2025年第三季度销售额最高的五个商品，要同时列出专辑和艺术家。

难度：medium　能力：track_album_artist_join

标准答案：按TrackId汇总并连接Album、Artist，返回商品、专辑、艺术家和销售额。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Album, Artist

评分要点：多表连接正确；保留商品身份字段

## special-multitable-002

问题：2025年第三季度音频销售额最高的五位艺术家是谁？

难度：medium　能力：artist_sales_join

标准答案：按Artist连接Album和Track后汇总音频订单明细，返回销售额前五名。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Album, Artist

评分要点：Artist连接路径正确；按销售额排序

## special-multitable-003

问题：2025年第三季度音频销售额最高的五张专辑是什么？

难度：medium　能力：album_sales_join

标准答案：按Album汇总其下Track的音频订单明细，返回专辑、艺术家和销售额。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Album, Artist

评分要点：按AlbumId聚合；列出艺术家

## special-multitable-004

问题：每个播放列表收录了多少个不同商品？

难度：medium　能力：playlist_track_join

标准答案：通过PlaylistTrack按PlaylistId统计去重后的TrackId数量；这只是收录数量，不是播放量。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Playlist, PlaylistTrack, Track

评分要点：TrackId去重；不声称播放量

## special-multitable-005

问题：2025年第三季度购买音频金额最高的五位客户是谁？

难度：medium　能力：customer_sales_join

标准答案：按CustomerId汇总第三季度音频明细，返回客户编号、客户姓名、销售额和订单数。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, Customer, InvoiceLine, Track

评分要点：Customer与Invoice连接正确；金额按明细计算

## special-multitable-006

问题：第三季度购买音频客户按支持负责人分布如何？

难度：hard　能力：support_rep_customer_join

标准答案：按SupportRepId统计其负责且在第三季度购买音频的去重客户数；这表示维护分布，不表示个人销售贡献。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Employee, Customer, Invoice, InvoiceLine, Track

评分要点：客户去重；不解释为员工销售排名

## special-multitable-007

问题：第三季度各音乐分类的订单数和购买客户数是多少？

难度：hard　能力：genre_customer_order_join

标准答案：按Genre汇总，订单数按InvoiceId去重，购买客户数按CustomerId去重；品类数不能直接相加得到总数。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, Genre

评分要点：两个去重口径正确

## special-multitable-008

问题：数据库中同时含音频和视频明细的订单有多少张？

难度：hard　能力：mixed_order_join

标准答案：有17张订单同时含音频和视频明细。计算音乐销售额时仍需按InvoiceLine筛选。

来源：无文档证据，依据数据库或接口状态判断。

预期数据库表：Invoice, InvoiceLine, Track, MediaType

评分要点：结果为17；说明混合订单处理
