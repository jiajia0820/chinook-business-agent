# AdventureWorks PostgreSQL 数据集

来源：比赛说明指定的 Docker 镜像 chriseaton/adventureworks:postgres。

本目录来自该镜像官方构建仓库在 2025-06-19 发布的 PostgreSQL 初始化压缩包，包含按执行顺序拆分的 SQL 文件：

- install-a.sql：扩展、Schema、表结构和约束
- install-b.sql：索引与视图
- install-c.sql：基础数据
- install-d.sql：其余业务数据

官方来源：

- 镜像：https://hub.docker.com/r/chriseaton/adventureworks
- 构建仓库：https://github.com/chriseaton/docker-adventureworks
- 发布包：https://github.com/chriseaton/docker-adventureworks/releases/download/2025-06-19/chriseaton-docker-adventureworks-postgres.zip

原始压缩包 SHA-256：$hash

导入 PostgreSQL 时按 install-a.sql、install-b.sql、install-c.sql、install-d.sql 的顺序执行；该版本面向 PostgreSQL 16，并包含 HumanResources、Person、Production、Purchasing、Sales 等 Schema。
