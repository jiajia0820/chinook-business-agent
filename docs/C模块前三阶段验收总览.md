# C 模块前三阶段验收总览

> 2026-10-04 更新：用户已完成第一、二阶段 Chinook 本地检查并提交通过的专项测试输出；真实模型基础链路和 10 题评测已运行，但业务准确性未达标。下文 2026-10-03 的“真实模型未验证/人工验收全部待验收”为历史状态。当前完成情况与 B 调用方法见 [最新交接指南](C模块当前状态与B调用指南-2026-10-04.md)，结果分析见 [评测记录](C模块NL2SQL评测记录与开源调研需求-2026-10-04.md)。

2026-10-03。前三阶段独立实现与本地自动验证完成；人工验收全部待用户验收。按用户要求停在第三阶段。

| 阶段 | 指南 | 报告 | 最终自动结果 |
|---|---|---|---|
| 1 数据接入与Schema | [终端验收指南](C模块第1阶段终端验收指南.md) | [测试报告](C模块第1阶段测试报告.md) | 13通过、1真实AW未验证 |
| 2 安全SQL执行 | [终端验收指南](C模块第2阶段终端验收指南.md) | [测试报告](C模块第2阶段测试报告.md) | 13通过、1真实PG未验证 |
| 3 基础自然语言查数 | [终端验收指南](C模块第3阶段终端验收指南.md) | [测试报告](C模块第3阶段测试报告.md) | 15通过、1真实模型未验证 |

加上旧功能8项回归：共52项，49通过、0失败、3未验证。真实Chinook结构/查询与哈希未变已验证；PG跨Schema/复合关系及模型协议部分使用夹具/模拟，不能当真实服务成绩。

## 从这里开始

VS Code PowerShell：

```powershell
Set-Location -LiteralPath 'D:\南邮研究生\ICT比赛\附件2：第三届“中国电子杯”高校ICT产教融合创新大赛命题\chinook-business-agent'
.\.venv\Scripts\python.exe -m backend.sql_module profiles
.\.venv\Scripts\python.exe -m backend.sql_module check chinook
.\.venv\Scripts\python.exe -m backend.sql_module table chinook main.PlaylistTrack
.\.venv\Scripts\python.exe -m backend.sql_module query --request-file .\examples\c-sql-query.json
.\.venv\Scripts\python.exe -m backend.sql_module ask chinook --request-file .\examples\c-nl-query.json --demo-model
.\.venv\Scripts\python.exe -m unittest discover -s eval -p 'test*.py' -v
```

预期：Chinook可用、11表；PlaylistTrack复合主键正确；SQL客户数59；离线NL候选经实际库查询返回59并明确离线标记；自动测试49通过3未验证。精确每一步、异常与真实配置要求见上表指南。

## 验收界限

基础NL使用单候选；未配置真实模型时可显式离线演示，也可注入测试模型。没有自动修复、精准Schema Linking、复杂查询评测、澄清对话或B页面联调。HTTP适配已实现但真实模型未调用。演示指标未由A正式确认，语义等价尚需结果评测。

AdventureWorks暂不必下载来完成本地验收：实际接入需部署同伴的PostgreSQL实例、精确授权名称和本地安全连接配置。当前不搭建/更改数据库。旧接口及页面/Agent保留，本地interface-contract (1).md v0.3参考文件未纳入本次提交；仓库对外契约与新模块内部结构仍需团队对齐。

下一步先由用户逐项人工检查前三阶段；不自动进入第四阶段。团队交接见 [阶段交付与AB协作说明](C模块阶段交付与AB协作说明-2026-10-04.md)。
