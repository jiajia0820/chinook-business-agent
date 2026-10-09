# 专项 多模态

题库文件：`eval/agent/eval_special_multimodal.jsonl`
题量：5

## special-multimodal-001

问题：海报上的活动周期和报名截止时间是什么？

难度：easy　能力：poster_ocr

标准答案：活动周期为2025年10月20日至11月2日，报名截止为10月13日18:00。

来源：D11：活动主题和报名时间，图片区域 hero

预期数据库表：无

评分要点：读取图片文字；给出图片区域

## special-multimodal-002

问题：海报写的报名对象和提交材料是什么？

难度：easy　能力：poster_ocr

标准答案：商品内容/品类运营可以报名；提交商品清单、选品理由和一段页面文案。

来源：D11：报名对象与截止信息，图片区域 chips；D11：提交材料，图片区域 materials

预期数据库表：无

评分要点：对象和材料完整；给出图片区域

## special-multimodal-003

问题：目标表中Metal的销售额目标和销量目标是多少？

难度：easy　能力：pdf_table_reading

标准答案：Metal（Genre:3）销售额目标24.00美元，销量目标25件。

来源：D06：附件1 目标明细，第1页

预期数据库表：无

评分要点：正确读取PDF表格

## special-multimodal-004

问题：目标表中2025年9月全音频销售额和销量计划是多少？

难度：medium　能力：pdf_table_reading

标准答案：9月全音频销售额计划42.00美元，销量计划44件。

来源：D06：附件2 月度检查安排，第2页

预期数据库表：无

评分要点：正确读取表格行列

## special-multimodal-005

问题：海报主页面和延伸栏目分别是什么？

难度：medium　能力：poster_ocr

标准答案：Rock是主页面，Metal是延伸栏目；海报还展示经典专辑内容。

来源：D11：报名条件，图片区域 eligibility

预期数据库表：无

评分要点：识别图片卡片信息
