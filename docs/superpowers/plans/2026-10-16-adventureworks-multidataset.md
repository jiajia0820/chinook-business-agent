# AdventureWorks 多数据集选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让同一个 API 和网页工作台同时支持 Chinook 与 AdventureWorks，并允许用户在页面切换数据集后分别提问。

**Architecture:** 后端为每个启用的 public profile 创建独立 integration 和 GraphAskService，再由 profile router 按请求中的 `profile_id` 分发；这样 Chinook 的 SQL+RAG 组合与 AdventureWorks 的 SQL-only 组合保持各自证据策略。前端通过 profile catalog 初始化可选数据集，当前 profile 进入每次 ask 请求，切换时清空会话上下文、历史、结果和证据。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、LangGraph、Vue 3、TypeScript、Vitest、unittest。

---

### Task 1: 后端 profile 路由与多 integration 生命周期

**Files:**
- Create: `services/agent_api/app/services/profile_router.py`
- Modify: `services/agent_api/app/services/backend_lifecycle.py`
- Modify: `services/agent_api/app/core/config.py`
- Modify: `services/agent_api/app/integrations/sql_d12.py` only if shared factory typing requires a narrow compatibility change
- Test: `services/agent_api/tests/test_profile_router.py`
- Test: `services/agent_api/tests/test_adventureworks_support.py`

- [ ] **Step 1: Write failing tests** for accepting `("chinook-music", "adventureworks")`, routing each request to the matching service, rejecting unknown profiles with the existing structured response, and closing every integration.
- [ ] **Step 2: Run the focused tests** with `uv run python -m unittest services.agent_api.tests.test_profile_router services.agent_api.tests.test_adventureworks_support -v`; expected failure is the missing router/multi-profile validation.
- [ ] **Step 3: Implement the smallest router** with a profile-to-service map, profile metadata access, `ask`, and deterministic `aclose` aggregation; change settings validation from exactly one supported profile to one or more unique supported profiles.
- [ ] **Step 4: Change `create_default_backend`** to build Chinook through `create_canonical_knowledge_integration` and AdventureWorks through `create_canonical_sql_integration`, then return a composite backend exposing the selected registry and router service.
- [ ] **Step 5: Run the focused tests** and the existing lifecycle tests; expected result is all focused tests passing.
- [ ] **Step 6: Commit** with `git add services/agent_api/app/core/config.py services/agent_api/app/services/backend_lifecycle.py services/agent_api/app/services/profile_router.py services/agent_api/tests/test_profile_router.py services/agent_api/tests/test_adventureworks_support.py && git commit -m "feat: route requests across enabled profiles"`.

### Task 2: Profile discovery API

**Files:**
- Create: `services/agent_api/app/api/routes/profiles.py`
- Modify: `services/agent_api/app/main.py`
- Modify: `services/agent_api/app/contracts/models.py`
- Test: `services/agent_api/tests/test_profiles_route.py`

- [ ] **Step 1: Write failing tests** for `GET /api/v1/profiles` returning selected profiles with `profile_id`, display name, capabilities, and readiness, and for a single-profile deployment returning only that profile.
- [ ] **Step 2: Run the focused route tests** and confirm failure because the route/model do not exist.
- [ ] **Step 3: Add strict response models** and expose the catalog from application state populated by the composite backend; return stable ordering matching `AGENT_ENABLED_PROFILES`.
- [ ] **Step 4: Run route tests** plus existing API contract tests.
- [ ] **Step 5: Commit** with `git add services/agent_api/app/api/routes/profiles.py services/agent_api/app/main.py services/agent_api/app/contracts/models.py services/agent_api/tests/test_profiles_route.py && git commit -m "feat: expose enabled profile catalog"`.

### Task 3: Frontend profile state and request propagation

**Files:**
- Modify: `services/web/src/api/client.ts`
- Modify: `services/web/src/features/ask/use-ask-workspace.ts`
- Create: `services/web/src/features/ask/profiles.ts`
- Modify: `services/web/src/App.vue`
- Test: `services/web/tests/client.test.ts`
- Test: `services/web/tests/ui-redesign.test.ts`

- [ ] **Step 1: Write failing tests** for loading the profile catalog, rendering both profiles, sending the selected `profile_id`, and resetting session/history/results when the selection changes.
- [ ] **Step 2: Run the focused Vitest tests** with `npm test -- --run tests/client.test.ts tests/ui-redesign.test.ts`; expected failure is the current hard-coded profile and disabled AdventureWorks option.
- [ ] **Step 3: Implement a small profile client** for `GET /api/v1/profiles`, inject the selected profile into `useAskWorkspace`, and expose `selectProfile` plus profile-specific display metadata.
- [ ] **Step 4: Replace the disabled database menu** with enabled profile buttons, keep the selected option marked as current, and show loading/unavailable states from the catalog.
- [ ] **Step 5: Make profile switching call the same reset boundary as a new session**, close the menu, and prevent stale requests from updating the new profile state.
- [ ] **Step 6: Run focused Vitest tests** and verify the request body contains the expected profile ID for both choices.
- [ ] **Step 7: Commit** with `git add services/web/src/api/client.ts services/web/src/features/ask/use-ask-workspace.ts services/web/src/features/ask/profiles.ts services/web/src/App.vue services/web/tests/client.test.ts services/web/tests/ui-redesign.test.ts && git commit -m "feat: add UI profile selector"`.

### Task 4: Dataset-specific examples and capability display

**Files:**
- Modify: `services/web/src/features/ask/evaluation-questions.ts`
- Modify: `services/web/src/App.vue`
- Modify: `services/web/src/components/evidence/EvidencePanel.vue` only where capability gating is required
- Test: `services/web/tests/ui-redesign.test.ts`

- [ ] **Step 1: Write failing tests** proving Chinook examples contain knowledge-base questions, AdventureWorks examples contain product/order questions, and AdventureWorks does not render a document evidence section for SQL-only responses.
- [ ] **Step 2: Run focused UI tests** and confirm the current shared examples/always-mounted evidence behavior fails the new expectations.
- [ ] **Step 3: Add profile-keyed example groups** and render the selected group; keep examples as fill-only actions with no auto-submit.
- [ ] **Step 4: Gate document-oriented helper text and tabs by returned evidence/capability** without changing the response contract.
- [ ] **Step 5: Run frontend verification** with `npm run verify`.
- [ ] **Step 6: Commit** with `git add services/web/src/features/ask/evaluation-questions.ts services/web/src/App.vue services/web/src/components/evidence/EvidencePanel.vue services/web/tests/ui-redesign.test.ts && git commit -m "feat: tailor workspace to selected dataset"`.

### Task 5: Full regression and environment documentation

**Files:**
- Modify: `.env.example`
- Modify: `docs/环境说明与启动指南.md`
- Modify: `README.md`
- Test: `services/agent_api/tests/` existing suite
- Test: `services/web/tests/` existing suite

- [ ] **Step 1: Add the multi-profile environment example** and document `AGENT_ENABLED_PROFILES=chinook-music,adventureworks` plus the AdventureWorks PostgreSQL URL requirement.
- [ ] **Step 2: Run backend regression** with `uv run python -m unittest discover -s services/agent_api/tests -t .`.
- [ ] **Step 3: Run frontend regression** with `npm run verify` from `services/web`.
- [ ] **Step 4: Inspect `git diff --check` and `git status --short`**, then commit documentation and any test-only corrections.
- [ ] **Step 5: Record the final commit list and remaining runtime prerequisite**: AdventureWorks requires a reachable PostgreSQL database and `ADVENTUREWORKS_DATABASE_URL`; without it, the profile should surface as unavailable while Chinook remains usable if the composite startup policy supports partial readiness.

