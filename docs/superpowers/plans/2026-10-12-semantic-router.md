# 模糊问题语义路由 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在规则无法判断时增加一次低权限模型路由，并保持明确业务问题的快速路径和原有安全校验。

**Architecture:** 解析节点之后增加语义路由节点。只有原始解析结果为 clarification 且存在路由模型时才调用它；模型返回受限 JSON 建议，程序将建议合并回任务后重新进入现有校验节点。没有模型或模型失败时保持原结果。

**Tech Stack:** Python、Pydantic、LangGraph、现有 HTTP JSON 模型适配器、unittest。

---

### Task 1: 建立受限路由建议模型

**Files:**
- Create: `services/agent_api/app/agent/semantic_router.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/services/graph_ask_service.py`
- Test: `services/agent_api/tests/test_semantic_router.py`

- [ ] **Step 1: 写失败测试**：合法 general 建议可解析；非法 route、低置信度和额外字段被拒绝。
- [ ] **Step 2: 运行测试确认失败**：模块尚不存在。
- [ ] **Step 3: 实现 JSON 提示、解析和建议应用**：只允许 `general/sql/rag/cross_source/clarification`，置信度至少 0.8 才应用；general 只能生成 general_knowledge 任务。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 本地提交**：`feat: add constrained semantic route proposal`。

### Task 2: 把模型接入模糊路由节点

**Files:**
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/integrations/sql_d12.py`
- Extend: `services/agent_api/tests/test_semantic_router.py`

- [ ] **Step 1: 写失败测试**：明确销售额查询不调用模型；规则无法判断的问题在 fake model 建议 general 时得到 answered/general；模型失败时仍返回 clarification。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现单次短超时调用**：只在 clarification 分支调用；路由建议应用后回到现有 validate；不授予任何工具权限。
- [ ] **Step 4: 运行新增测试和路由回归**。
- [ ] **Step 5: 本地提交**：`feat: use model fallback for ambiguous routing`。

### Task 3: 完成回归验证

**Files:**
- Verify: `services/agent_api/tests/`
- Verify: `services/web/`

- [ ] **Step 1: 运行语义路由、通用回答、业务策略和契约测试**。
- [ ] **Step 2: 运行前端契约、类型和测试命令**。
- [ ] **Step 3: 记录任何原有环境失败，不把它们混入本阶段成功结论**。
