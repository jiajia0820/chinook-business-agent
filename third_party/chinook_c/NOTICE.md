# C 首版隔离交付

来源：团队提供的 chinook-business-agent-main.zip，2026-10-05 核对。

本目录仅收录 19 个必要文件：backend/sql_module 与其 package 标记、profiles/chinook、样例库及库许可证、原始 requirements-c.txt。未收录旧 Chinook 入口、A 业务文档或 eval/gold 答案；未覆盖 B 工程。

这些文件逐字节从用户提供的包中提取，未改动 C 的源码、安全策略、profile 或数据库。artifact-manifest.json 保存原包 hash 和逐文件 hash；B 的路径/异步/来源适配位于 services/agent_api/app，不修改此目录的 C 文件。包内 profile 的相对路径布局原样保留，不依赖 tmp 审阅目录。

C 代码归团队交付，不将其重新声明为开源 pi 代码；包内未提供 C 整体开源许可证，公开发布时需要团队确认。data/chinook/LICENSE.md 原样保留 Chinook 的许可证；本库仍是团队比赛样例快照，不是企业生产数据。

原 requirements-c.txt 包含 PostgreSQL 的 psycopg；本阶段只装配 SQLite，不安装/验证 PostgreSQL 驱动。C 的 HTTPJSONModel 随原 generation.py 保留，但 B 工厂仅显式创建 DemoJSONModel，不使用环境变量或网络模型。
