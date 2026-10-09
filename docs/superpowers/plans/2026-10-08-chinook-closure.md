# Chinook 完整产品闭环 Implementation Plan

Goal: 在 Chinook 与 D01-D12 自建知识库上跑通离线可回归、live 可配置的 SQL、RAG、联合计算和网页展示闭环，并保留本地 Git 回退点。

Architecture: 保留 POST /api/v1/ask 契约。B 负责解析、路由、证据组装和响应，C 负责 profile/schema/metrics、NL2SQL、只读 SQL；知识库从单 D12 提升为带 manifest 校验的多文档 lexical index；cross_source 只允许 SQL 数值、文档目标/公式和受控计算器之间显式绑定。

## Task 0: 固定开发基线

Files: 本计划文件；必要时修改 .gitignore；测试命令为 .venv\Scripts\python.exe -m unittest discover -s services/agent_api/tests -q。

- [ ] 确认分支为 codex/chinook-complete，且项目虚拟环境能导入 SQLAlchemy/FastAPI。
- [ ] 提交计划：git add docs/superpowers/plans/2026-10-08-chinook-closure.md，然后 git commit -m "docs: add Chinook closure implementation plan"。

## Task 1: 快照 manifest 和多文档索引

Files: 新建 services/agent_api/app/knowledge/documents.py、index.py、scripts/verify_knowledge_snapshot.py、scripts/build_knowledge_index.py；修改 data/knowledge/source-manifest.json 和 markdown.py；测试 test_knowledge_snapshot.py、test_knowledge_index.py。

- [ ] 先写失败测试：manifest 必须含 D01-D12；每个文件 hash/size 可核验；Markdown 有章节行号；PDF/DOCX/PNG 返回类型和定位字段；损坏文件按 doc_id 报错。
- [ ] 用项目虚拟环境运行新测试，确认因旧实现而失败。
- [ ] 实现原始字节 hash 校验和统一 chunk 接口。Markdown 复用现有 chunker；DOCX 从 zip/XML 提取段落；PDF 使用可用解析器或 Poppler；PNG 在 OCR 不可用时返回 DOCUMENT_PROCESSING_FAILED，不伪造文本。
- [ ] 运行 scripts/verify_knowledge_snapshot.py 和 scripts/build_knowledge_index.py --check，确认 D01-D12 verified/indexed。
- [ ] 提交 feat: index and verify D01-D12 knowledge snapshot。

## Task 2: 统一 offline/live LLM 配置

Files: 修改 .env.example、core/config.py、core/model_config.py、backend/sql_module/generation.py、integrations/c_offline.py、services/backend_lifecycle.py；测试 test_llm_provider.py。

- [ ] 先写失败测试：统一读取 LLM_*；只拼一次 /chat/completions；Key 不进入异常/日志/响应；成功、4xx/5xx、超时、非法 JSON 有结构化错误；live 缺配置不回退 Demo；offline 继续 Demo。
- [ ] 实现 AGENT_MODEL_MODE=offline|live，HTTP JSON body 使用 model/messages/temperature=0/response_format，Key 只进请求头并脱敏异常。
- [ ] 让启动路径按模式选择模型；live 缺配置时 /health 可用、/ask 返回 PROFILE_UNAVAILABLE。
- [ ] 回归 C 离线测试并提交 feat: add explicit offline and live LLM modes。

## Task 3: Chinook metrics/profile 和自然语言 NL2SQL

Files: 修改 profiles/chinook、third_party/chinook_c/profiles/chinook、services/agent_api/app/profiles/configs/chinook-music.json、agent/parsing.py、adapters/c_sql.py；测试 test_chinook_metrics.py、test_live_nl2sql.py。

- [ ] 先写失败测试：12 个指标定义/单位/粒度/source_refs 完整；全 Chinook 业务表在 schema 可见；live 三条自然语言题进入 C；未知过滤条件不被删除。
- [ ] 注册 sales_amount、units_sold、order_count、purchasing_customers、average_order_value、genre_sales、track_sales、album_sales、artist_sales、media_type_sales、billing_country_sales、playlist_track_count。
- [ ] offline 继续固定别名；live 放行自然语言，保留已确认槽位和未知残余，交给 C；生成失败返回结构化错误。
- [ ] 回归并提交 feat: complete Chinook metric catalog and live NL2SQL routing。

## Task 4: D01-D12 全量 RAG

Files: 修改 integrations/sql_d12.py、knowledge/index.py、chinook profile、eval/agent 相关 JSONL；测试 test_all_documents_rag.py。

- [ ] 先写失败测试：D01-D12 各有题命中正确 doc_id 和章节/页码/bbox；无证据返回 insufficient_evidence；不把原文替换成固定答案。
- [ ] 把 SQL+D12 集成替换为 SQL+knowledge index，保留兼容类名；注册全量 rag.retrieve。
- [ ] 运行 index check 和 RAG 回归，提交 feat: enable D01-D12 retrieval in agent runtime。

## Task 5: 受控 cross_source 计算

Files: 修改 evidence/models.py、evidence/binding.py、tools/local_calculator.py、response/composer.py、integrations/sql_d12.py；测试 test_cross_source_calculation.py。

- [ ] 先写失败测试：目标达成、差额、增长率绑定 SQL actual/current/previous、文档 target/formula；目标缺失或周期不完整返回 insufficient_evidence 且不调用 calculator；成功响应含 SQL、documents、calculations、metric_definitions、trace。
- [ ] 实现 verified business evidence resolver，仅接受已检索 chunk 的数字 span、D06-D08 目标/复盘和 D01 公式；周期/scope 不匹配即拒绝。
- [ ] 注册真实 calculator，Composer 生成带来源 refs 和限制的中文答案。
- [ ] 回归并提交 feat: bind SQL documents and controlled calculations。

## Task 6: API、网页和最终验收

Files: 按契约需要修改 API lifecycle/config 和 web 文件；新建 docs/验收记录-2026-10-08.md；测试后端全量测试和 services/web/tests。

- [ ] 覆盖 SQL、RAG、clarification、unsupported、cross_source 五类响应及证据面板字段。
- [ ] 运行 .venv\Scripts\python.exe -m unittest discover -s services/agent_api/tests -q，要求 0 failures/errors。
- [ ] 运行 cd services/web 后 npm run verify，要求 contracts/typecheck/tests/build 全通过。
- [ ] 用 uv run uvicorn 和 /health 做 smoke test，验证四条问题路径。
- [ ] 写验收记录并提交 docs: record Chinook closure acceptance。
