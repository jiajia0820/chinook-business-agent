# Chinook 初赛运行面收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将默认服务收敛为 Chinook Live 主链路，保留受控离线兜底，并让 AdventureWorks 完整保留但在产品中显示为不可用。

**Architecture:** 用一个 `AGENT_RUN_MODE` 作为正式运行模式来源，兼容旧的 Python 参数与环境变量；后端只装配 Chinook integration，同时把完整 profile catalog 中的 AdventureWorks 标记为 unavailable。前端沿用 profile catalog 展示状态，不增加模式切换。

**Tech Stack:** FastAPI、Pydantic、LangGraph、Vue 3、Vitest、Python unittest、Git。

---

### Task 1: 固定运行模式和默认 profile

**Files:**
- Modify: `services/agent_api/app/core/config.py`
- Modify: `services/agent_api/app/services/backend_lifecycle.py`
- Modify: `services/agent_api/app/api/routes/health.py`
- Modify: `.env.example`
- Test: `services/agent_api/tests/test_runtime_modes.py`

- [ ] **Step 1: Write failing tests** for `AGENT_RUN_MODE=live/demo`, Chinook-only startup selection, and health exposing the selected run mode.
- [ ] **Step 2: Run the focused tests and confirm they fail because `run_mode` and the Chinook-only catalog behavior do not exist.
- [ ] **Step 3: Add `Settings.run_mode`, parse `AGENT_RUN_MODE` with old `AGENT_MODEL_MODE` compatibility, and make default startup initialize only `chinook-music`.
- [ ] **Step 4: Run the focused tests and the existing health/config tests.
- [ ] **Step 5: Commit `feat: define chinook preliminary run modes`.

### Task 2: 保留完整 profile catalog，禁用 AdventureWorks 主路由

**Files:**
- Modify: `services/agent_api/app/services/profile_router.py`
- Modify: `services/agent_api/app/services/backend_lifecycle.py`
- Modify: `services/agent_api/tests/test_profile_router.py`
- Modify: `services/agent_api/tests/test_adventureworks_support.py`
- Test: `services/agent_api/tests/test_preliminary_profile_catalog.py`

- [ ] **Step 1: Write failing tests** asserting catalog contains Chinook ready and AdventureWorks unavailable, and AdventureWorks requests return `PROFILE_UNAVAILABLE` without creating an integration.
- [ ] **Step 2: Run focused tests and verify the current router returns profile-not-found or initializes both profiles.
- [ ] **Step 3: Add explicit unavailable profile handling and catalog status mapping while leaving AdventureWorks assets and independent factories intact.
- [ ] **Step 4: Run profile, lifecycle, and API tests.
- [ ] **Step 5: Commit `feat: keep adventureworks cataloged but unavailable`.

### Task 3: 前端展示不可用数据集

**Files:**
- Modify: `services/web/src/features/ask/profiles.ts`
- Modify: `services/web/src/App.vue`
- Modify: `services/web/tests/ui-redesign.test.ts`
- Modify: `services/web/tests/client.test.ts`

- [ ] **Step 1: Write failing tests** for AdventureWorks disabled state and its unavailable label.
- [ ] **Step 2: Run the focused Vitest tests and confirm the current default fixture marks AdventureWorks ready.
- [ ] **Step 3: Change only the local default profile status and rendering copy; keep server catalog as source of truth when available.
- [ ] **Step 4: Run `npm run verify` in `services/web`.
- [ ] **Step 5: Commit `feat: show adventureworks as unavailable in preliminary UI`.

### Task 4: 统一 Chinook 的执行模式传递

**Files:**
- Modify: `services/agent_api/app/integrations/c_offline.py`
- Modify: `services/agent_api/app/integrations/sql_d12.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/response/composer.py`
- Test: existing live/offline integration tests plus `services/agent_api/tests/test_runtime_modes.py`

- [ ] **Step 1: Add failing assertions that service startup passes one resolved run mode into the Chinook integration and that demo mode never constructs the HTTP model.
- [ ] **Step 2: Run focused tests and inspect the failing mode mismatch.
- [ ] **Step 3: Thread the resolved mode once; retain `model_mode` function parameters only as compatibility shims for direct tests.
- [ ] **Step 4: Run all integration, fast-path, clarification, and response tests.
- [ ] **Step 5: Commit `refactor: resolve chinook execution mode once`.

### Task 5: 清理主链路中的重复运行分支

**Files:**
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/agent/task_policy.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `services/agent_api/app/response/composer.py`
- Test: relevant existing Agent tests and a new route matrix test.

- [ ] **Step 1: Add route matrix tests covering SQL, RAG, cross-source, clarification, unsupported, and controlled fallback.
- [ ] **Step 2: Run them before cleanup to capture current behavior.
- [ ] **Step 3: Remove only unreachable legacy/fixture branches from the default Chinook path; leave compatibility entry points used by historical tests.
- [ ] **Step 4: Run the full backend suite and compare status/trace contracts.
- [ ] **Step 5: Commit `refactor: narrow chinook agent main path`.

### Task 6: 删除无引用运行包装层并完成回归

**Files:**
- Candidate review: `services/agent_api/app/agent/semantic_shadow.py`, unused wrappers found by `rg`, `frontend/.gitkeep`, `config/.gitkeep`
- Modify: `README.md`, `docs/环境说明与启动指南.md`
- Test: full backend and frontend suites

- [ ] **Step 1: Run import/reference scans and record every deletion candidate.
- [ ] **Step 2: Delete only candidates with no runtime, test, or script references; keep AdventureWorks and frozen Chinook C assets.
- [ ] **Step 3: Update mode/profile documentation and `.env.example`.
- [ ] **Step 4: Run backend unittest discovery, C regression checks, frontend `npm run verify`, and `git diff --check`.
- [ ] **Step 5: Commit `chore: finalize chinook preliminary cleanup`.
