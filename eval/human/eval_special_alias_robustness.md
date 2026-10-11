# 专项 别名与分类

题库文件：`eval/agent/eval_special_alias_robustness.jsonl`
题量：15

## special-alias-001

问题：问题里的“摇滚”默认对应哪个Genre？

难度：easy　能力：alias_normalization

标准答案：默认对应Rock，Genre:1；不包含Rock And Roll（Genre:5）。

来源：D03：Rock 的消歧

预期数据库表：无

评分要点：标准化为Genre:1

## special-alias-002

问题：“摇滚乐”在分类表里通常对应什么？

难度：medium　能力：alias_normalization

标准答案：Rock And Roll，Genre:5；它与Rock（Genre:1）分开。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：标准化为Genre:5

## special-alias-003

问题：“金属类”默认按哪个分类统计？

难度：easy　能力：alias_normalization

标准答案：默认按Metal，Genre:3统计；Heavy Metal是Genre:13，需单独说明。

来源：D03：Metal 的消歧

预期数据库表：无

评分要点：标准化为Genre:3

## special-alias-004

问题：“重金属”是否应该并入Metal？

难度：easy　能力：alias_normalization

标准答案：不应默认并入。重金属对应Heavy Metal（Genre:13），Metal为Genre:3。

来源：D03：Metal 的消歧

预期数据库表：无

评分要点：区分Genre:3和Genre:13

## special-alias-005

问题：“音乐商品”默认包含哪些媒体格式？

难度：easy　能力：alias_normalization

标准答案：默认包含MediaTypeId 1、2、4、5，不包含视频MediaTypeId 3。

来源：D01：音乐范围，第1页；D03：三 MediaType 和商品范围

预期数据库表：无

评分要点：列出音频范围

## special-alias-006

问题：“全店销售额”和“音乐销售额”有什么范围差异？

难度：medium　能力：alias_normalization

标准答案：音乐销售额默认只统计音频；全店销售额可以包含全部MediaType，需明确是否纳入视频。

来源：D03：三 MediaType 和商品范围

预期数据库表：无

评分要点：区分全店与音乐

## special-alias-007

问题：用户把 Heavy Metal 写成“重金属”，系统应如何归类？

难度：medium　能力：typo_tolerance

标准答案：识别为Heavy Metal，Genre:13，不应套用Metal（Genre:3）的目标。

来源：D03：Metal 的消歧

预期数据库表：无

评分要点：容错并保持边界

## special-alias-008

问题：“拉丁音乐”对应哪个Genre？

难度：easy　能力：alias_normalization

标准答案：Latin，Genre:7。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：标准化为Genre:7


## special-alias-009

问题：Alternative这个分类在日报里通常对应哪个Genre？

难度：easy　能力：alias_normalization

标准答案：Alternative对应Genre:23；Alternative & Punk是Genre:4，两个分类分开维护。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：Alternative映射Genre:23；区分Alternative & Punk Genre:4

## special-alias-010

问题：电子/舞曲类商品在分类表里对应哪个Genre？

难度：easy　能力：alias_normalization

标准答案：电子/舞曲类对应Electronica/Dance，Genre:15。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：映射到Electronica/Dance；GenreId为15

## special-alias-011

问题：古典音乐商品通常按哪个Genre统计？

难度：easy　能力：alias_normalization

标准答案：古典音乐对应Classical，Genre:24。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：映射到Classical；GenreId为24

## special-alias-012

问题：季度目标里的Rock，要不要把Rock And Roll一起算进去？

难度：medium　能力：alias_normalization

标准答案：不要默认算进去。季度目标里的Rock只对应Genre:1，Rock And Roll是Genre:5；如果要做合并主题，需要单独登记合并范围。

来源：D03：Rock 的消歧

预期数据库表：无

评分要点：Rock目标只用Genre:1；Rock And Roll为Genre:5；说明合并需要单独登记

## special-alias-013

问题：日报里写“拉丁类”，和Latin的统计口径是同一个吗？

难度：easy　能力：alias_normalization

标准答案：是同一个标准分类：Latin对应Genre:7。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：拉丁类映射Latin；GenreId为7

## special-alias-014

问题：日报里写 Alternative & Punk，和 Alternative 是不是同一个分类？

难度：easy　能力：alias_normalization

标准答案：不是。Alternative & Punk 是 Genre:4，Alternative 是 Genre:23；默认分开统计，若要合并必须明确登记范围。

来源：D03：Alternative 的消歧

预期数据库表：无

评分要点：区分Alternative & Punk Genre:4；区分Alternative Genre:23；说明默认不合并

## special-alias-015

问题：日报里把 Jazz（爵士）列为一个分类时，Genre 编号应该填多少？

难度：easy　能力：alias_normalization

标准答案：标准分类名称是 Jazz，Genre:2。

来源：D03：二 Genre 标准目录

预期数据库表：无

评分要点：映射到Jazz；GenreId为2
