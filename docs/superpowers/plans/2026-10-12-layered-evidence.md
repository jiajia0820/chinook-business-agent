# 分层证据与部分回答 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不放松业务数字核验的前提下，让文档证据不完整时能返回可核实的部分和明确缺口。

**Architecture:** 证据检查继续决定整轮是否形成完整业务结论；响应合成器在 `rag` 路由存在文档片段但状态为 `insufficient_evidence` 时，使用已有证据摘录器生成受限的部分回答，并把缺口保留在限制说明中。SQL、跨源和工作流不走这条部分回答路径。

**Tech Stack:** Python、Pydantic、现有文档摘录器、unittest。

---

### Task 1: 为不完整文档证据增加部分回答

**Files:**
- Modify: `services/agent_api/app/response/composer.py`
- Test: `services/agent_api/tests/test_layered_evidence.py`

- [ ] **Step 1: 写失败测试**：构造带一个合法文档片段的 `insufficient_evidence` RAG 上下文，要求答案包含可确认文句；构造空片段上下文，要求仍返回“证据不足”。
- [ ] **Step 2: 运行测试确认失败**：当前合成器只返回统一的证据不足提示。
- [ ] **Step 3: 实现受限部分回答**：仅当 `route=rag`、存在 chunks、不是业务工作流时，调用现有 `draft_answer`；若没有可用摘录则保持原提示；正文明确“已确认部分”，不新增数字、不做推理。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 本地提交**：`feat: preserve partial document evidence`。

### Task 2: 明确证据缺口的限制说明

**Files:**
- Modify: `services/agent_api/app/agent/graph.py`
- Extend: `services/agent_api/tests/test_layered_evidence.py`

- [ ] **Step 1: 写失败测试**：缺少必要文档或覆盖不完整时，limitations 中出现明确缺口说明。
- [ ] **Step 2: 实现缺口分类文本**：区分无片段、必要来源缺失、证据覆盖不完整，保持现有状态值。
- [ ] **Step 3: 运行相关证据和业务回归**。
- [ ] **Step 4: 本地提交**：`feat: explain evidence gaps by layer`。
