# 专项 别名与分类

题库文件：`eval/agent/eval_special_alias_robustness.jsonl`
题量：8

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
