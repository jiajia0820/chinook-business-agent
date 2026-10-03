# 远程仓库上传清单

## 建议上传

### 非结构化业务成品

上传 `data/knowledge/` 顶层的 12 份最终业务文档：

- D01-D10
- D11 海报
- D12 FAQ

这些是 Agent 运行时真正需要读取的业务资料。

### 正式评测集

上传 `eval/agent/` 全部文件：

- 11 个 JSONL 题库；
- `eval_manifest.json`；
- `interface_profile_fixture.json`。

上传 `eval/human/` 作为团队核查资料：

- `README.md`；
- 各题库 Markdown 可读版；
- `eval_validation_report.json`；
- `P9_AUDIT_REPORT.md`。

### 配置和说明

应上传 `interface-contract.md`、`README.md`、`数据集与评测集方案.md`，以及与运行程序实际使用一致的 `config/` 配置文件。

当前 `config/` 只有 `document_catalog.json`；项目说明中提到的 schema、指标和别名配置文件目前并不存在。若后端依赖这些配置，需要在上传前补齐，不能只上传一个 document catalog 就认为 profile 配置完整。

## 处理后再上传

### `config/document_catalog.json`

当前文件不能直接上传。它仍有旧文件名和旧哈希，例如 D01 路径与当前 `data/knowledge/D01_经营指标说明.pdf` 不一致，运行 `validate_knowledge_dataset.py` 已经验证会失败。

上传前需要按当前 12 份成品重新生成：

- `source_uri`
- `content_sha256`
- 页数和图片定位信息
- 当前文档版本和来源状态

### `data/knowledge/processed/`

这里是给 RAG、OCR 和来源追踪使用的加工结果，不是新的业务文档。当前 `chunks.jsonl`、D06 资源清单和若干 locator 仍带有上一轮文档版本信息。

如果远程 Agent 启动时直接读取预处理结果，就应当先重新生成，再上传：

- `chunks.jsonl`
- 各 DOCX/PDF locator JSON
- D06 PDF 页面图片和 OCR 结果
- D11 图片定位文件
- 与当前成品对应的 document catalog

如果部署流程会在启动时重新解析文档，则可以不上传这些生成物，只保留可复现的加工脚本和原始成品。

## 不建议上传

- `data/knowledge/source/`：重写源稿和过程稿，不是 Agent 运行资料；
- `tmp/`：渲染图、备份文件、临时 PDF、接触表和中间脚本输出；
- `__pycache__/`、编辑器缓存和本地日志；
- `scripts/apply_*`、`scripts/remove_poster_version.py` 等一次性批注和修图脚本；
- `scripts/rewrite_revised_pack.py`、`scripts/clean_v2_sources.py` 等历史重写脚本，除非要保留完整创作过程；
- 已被移除的旧评测草稿；
- 发送给 Agent 的标准答案、SQL 和人工评分说明以外的临时测试副本。

## 推荐的远程仓库形态

```text
data/
  chinook/
  knowledge/
    D01-D12 final documents
    processed/  regenerated artifacts only, if runtime needs them
config/
  current profile and document catalog
eval/
  agent/
  human/
docs/
  interface contract and dataset documentation
```

上传前最后检查：从干净环境拉取仓库，确认 profile 能找到 Chinook 数据库、12 份文档和评测题库；再运行知识资料校验和评测集校验。
