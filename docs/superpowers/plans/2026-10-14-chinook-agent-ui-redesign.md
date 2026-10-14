# Chinook Agent UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Vue 问答页重构为 C1 深色任务舱：左侧会话区、中间回答画布、右侧常驻证据舱，同时保持现有 API、数据语义和交互行为不变。

**Architecture:** 保留 `useAskWorkspace`、`AskResult` 和全部证据组件作为行为层；`App.vue` 负责三栏外壳和输入区，`EvidencePanel.vue` 增加本地页签状态并继续渲染全部已有证据，`style.css` 提供主题 token、三栏布局、状态样式和响应式规则。页签只负责定位/强调已有内容，不筛除响应字段。

**Tech Stack:** Vue 3.5、TypeScript、Vite、Vitest、Vue Test Utils、原生 CSS。

---

### Task 1: Add focused UI behavior tests before implementation

**Files:**
- Create: `services/web/tests/ui-redesign.test.ts`
- Read: `services/web/src/App.vue`
- Read: `services/web/src/components/evidence/EvidencePanel.vue`

- [ ] **Step 1: Write failing tests for the C1 shell and evidence tabs**

Create a jsdom test that mounts the existing `App` with the existing `AskClient` injection and verifies only user-visible UI behavior:

```ts
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils';
import App from '../src/App.vue';
import EvidencePanel from '../src/components/evidence/EvidencePanel.vue';
import type { AskClient } from '../src/api/client';
import { actualResponse } from './evidence-fixtures';

enableAutoUnmount(afterEach);

const app = (ask: AskClient['ask']) => mount(App, { props: { client: { ask } } });

describe('C1 UI shell', () => {
  it('renders the three-column shell while keeping the existing composer testids', () => {
    const wrapper = app(vi.fn());
    expect(wrapper.get('[data-testid="workspace-shell"]').classes()).toContain('workspace-shell');
    expect(wrapper.get('[data-testid="workspace-sidebar"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="workspace-main"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="workspace-evidence"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="question-input"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="new-session"]').exists()).toBe(true);
  });

  it('keeps the response and evidence in the same shell after an answer', async () => {
    const response = actualResponse('客户数量是多少？');
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue({ kind: 'business', httpStatus: 200, requestId: response.request_id, response });
    const wrapper = app(ask);
    await wrapper.get('[data-testid="question-input"]').setValue('客户数量是多少？');
    await wrapper.get('[data-testid="ask-form"]').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[data-testid="answer"]').text()).toContain('59');
    expect(wrapper.get('[data-testid="workspace-evidence"] [data-testid="evidence-panel"]').exists()).toBe(true);
  });
});

describe('C1 evidence dock navigation', () => {
  it('marks the selected evidence group and keeps all returned evidence mounted', async () => {
    const response = actualResponse('客户数量是多少？');
    response.documents = [{
      doc_id: 'D12', chunk_id: 'D12@test', title: '测试来源', doc_type: 'md',
      text: '测试来源片段', source_uri: 'knowledge/D12', section: '测试章节',
    }];
    const wrapper = mount(EvidencePanel, { props: { response, showTraceRequested: true } });
    expect(wrapper.get('[data-testid="evidence-tab-sql"]').attributes('aria-selected')).toBe('true');
    await wrapper.get('[data-testid="evidence-tab-sources"]').trigger('click');
    expect(wrapper.get('[data-testid="evidence-tab-sources"]').attributes('aria-selected')).toBe('true');
    expect(wrapper.get('[data-testid="evidence-tab-sql"]').attributes('aria-selected')).toBe('false');
    expect(wrapper.find('[data-testid="sql-evidence"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="document-evidence"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="metric-definitions"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="trace-evidence"]').exists()).toBe(true);
  });
});
```

- [ ] **Step 2: Run the focused tests and verify they fail for the missing UI hooks**

Run: `npm test -- --run services/web/tests/ui-redesign.test.ts` from `services/web`.

Expected: FAIL because `workspace-shell`, the three shell regions, and evidence tab buttons do not exist yet. Existing baseline tests should remain unaffected.

- [ ] **Step 3: Commit the red tests**

```powershell
git add services/web/tests/ui-redesign.test.ts
git commit -m "test: define C1 agent workspace UI behavior"
```

### Task 2: Add the C1 evidence dock navigation without changing evidence semantics

**Files:**
- Modify: `services/web/src/components/evidence/EvidencePanel.vue`
- Test: `services/web/tests/ui-redesign.test.ts`

- [ ] **Step 1: Add local selected-tab state and deterministic target scrolling**

Use `ref<'sql' | 'sources' | 'metrics' | 'trace'>('sql')` inside `EvidencePanel.vue`. Add a tablist with four buttons using the existing response only. On click, update the selected value and call `scrollIntoView` on the matching section when it exists. Do not conditionally remove `SqlEvidence`, `DocumentEvidence`, `MetricDefinitions`, `CalculationEvidence`, or `TraceEvidence`; all returned evidence must stay mounted so existing citations, tests, and accessibility remain stable.

Use stable attributes:

```html
<nav class="evidence-tabs" role="tablist" aria-label="证据分组">
  <button data-testid="evidence-tab-sql" role="tab" aria-controls="evidence-section-sql" :aria-selected="activeEvidence === 'sql'" @click="selectEvidence('sql')">SQL</button>
  <button data-testid="evidence-tab-sources" role="tab" aria-controls="evidence-section-sources" :aria-selected="activeEvidence === 'sources'" @click="selectEvidence('sources')">Sources</button>
  <button data-testid="evidence-tab-metrics" role="tab" aria-controls="evidence-section-metrics" :aria-selected="activeEvidence === 'metrics'" @click="selectEvidence('metrics')">Metrics</button>
  <button data-testid="evidence-tab-trace" role="tab" aria-controls="evidence-section-trace" :aria-selected="activeEvidence === 'trace'" @click="selectEvidence('trace')">Trace</button>
</nav>
```

Wrap the existing evidence groups with `id="evidence-section-*"` and `data-evidence-group` attributes. Keep the existing `data-testid` values on child components. When a group has no returned records, the existing precise empty state remains visible.

- [ ] **Step 2: Run focused tests**

Run: `npm test -- --run services/web/tests/ui-redesign.test.ts services/web/tests/evidence-components.test.ts`.

Expected: the C1 evidence navigation tests pass and all pre-existing evidence tests remain green.

- [ ] **Step 3: Commit the evidence dock behavior**

```powershell
git add services/web/src/components/evidence/EvidencePanel.vue services/web/tests/ui-redesign.test.ts
git commit -m "feat: add evidence dock navigation"
```

### Task 3: Recompose `App.vue` into the three-column shell

**Files:**
- Modify: `services/web/src/App.vue`
- Test: `services/web/tests/ui-redesign.test.ts`

- [ ] **Step 1: Preserve existing setup and handlers**

Keep the current `useAskWorkspace` destructuring, status label map, clock helper, and Ctrl+Enter handler unchanged. Do not add API calls or alter request payloads.

- [ ] **Step 2: Move existing controls into stable shell regions**

Create one root element with `data-testid="workspace-shell"` and three direct regions:

```html
<main class="workspace-shell" data-testid="workspace-shell">
  <aside class="workspace-sidebar" data-testid="workspace-sidebar" aria-label="会话导航">
    <!-- product mark, new-session button, profile, existing history list -->
  </aside>
  <section class="workspace-main" data-testid="workspace-main">
    <!-- existing composer, notices, last request, AskResult, retry area -->
  </section>
  <aside class="workspace-evidence" data-testid="workspace-evidence" aria-label="常驻证据">
    <!-- one keyed EvidencePanel receives the same business response -->
  </aside>
</main>
```

To keep the evidence dock populated without duplicating responses, render `AskResult` once in the central region with `showEvidence=false`, then render one keyed `EvidencePanel` in the right region from the same computed business response. `AskResult` emits the referenced evidence ID when an inline citation is clicked; `App.vue` tells the dock to select the matching tab, opens the drawer on narrow screens, and preserves the existing expand/highlight behavior. Do not clone response data or add a second evidence renderer. Keep all existing `data-testid` selectors for composer, response, retry, history, and controls.

Use a local `data-layout`/class structure rather than adding a router, store, or new feature module. Keep the current example buttons, request option controls, loading message, notice, last request block, empty state, and session history content.

- [ ] **Step 3: Run the full component test suite**

Run: `npm test` from `services/web`.

Expected: all existing interaction and evidence tests pass, including session continuity, loading cancellation, answer citations, and partial evidence states.

- [ ] **Step 4: Commit the shell markup**

```powershell
git add services/web/src/App.vue services/web/tests/ui-redesign.test.ts
git commit -m "feat: compose C1 agent workspace shell"
```

### Task 4: Implement the dark task-room design system and responsive layout

**Files:**
- Modify: `services/web/src/style.css`
- Read: `services/web/src/components/AskResult.vue`
- Read: `services/web/src/components/evidence/*.vue`

- [ ] **Step 1: Add semantic CSS tokens and base dark surfaces**

Define the C1 tokens from the approved spec: `--bg`, `--panel`, `--panel-raised`, `--line`, `--text`, `--muted`, `--primary`, `--success`, `--warning`, and `--danger`. Apply them to body, the workspace shell, form controls, buttons, result cards, evidence cards, status tags, tables, disclosures, code blocks, and history rows. Keep readable contrast and visible `:focus-visible` rings.

- [ ] **Step 2: Add three-column geometry and component styles**

Use CSS grid for desktop: a fixed compact sidebar, a flexible main column, and a fixed evidence column. Style the question composer as the primary input surface, the answer conclusion as the main result, the loading/trace states as an execution rail, and the evidence tabs/cards as the inspectable right dock. Reuse existing class names wherever possible so child evidence components need no data changes.

- [ ] **Step 3: Add responsive collapse rules**

At medium widths, allow the evidence column to collapse through CSS while leaving its content in the DOM. At narrow widths, stack the main content and evidence, reduce sidebar width to a compact rail, and preserve all controls and keyboard focus order. Do not add a separate mobile data flow.

- [ ] **Step 4: Run typecheck and focused tests**

Run: `npm run typecheck` and `npm test`.

Expected: exit code 0, no TypeScript errors, and all tests pass.

- [ ] **Step 5: Commit the visual system**

```powershell
git add services/web/src/style.css
git commit -m "feat: style agent workspace as dark task room"
```

### Task 5: Validate the rendered UI in the Browser

**Files:**
- No committed files; screenshots and temporary validation output stay outside the repository.

- [ ] **Step 1: Start or reuse the existing Vite dev server**

Use the existing `services/web` dev server at `http://127.0.0.1:5173` when healthy. If it is unavailable, run `npm run dev -- --host 127.0.0.1 --port 5173` from `services/web`.

- [ ] **Step 2: Verify the desktop target flow**

Flow under test: app loads → C1 three-column shell renders → click an example → submit a question through the injected/live API state → answer and evidence render → click Sources/Trace tab → selected tab changes and the evidence group is visible.

Use the Browser/IAB path first. Check page identity, meaningful DOM, no framework overlay, console errors, a desktop screenshot, and the evidence-tab interaction.

- [ ] **Step 3: Verify a narrow viewport**

Use the browser viewport capability or an equivalent Browser-supported viewport override to inspect a mobile-sized width. Confirm the page does not clip the composer, evidence controls remain reachable, and the focus order remains usable.

- [ ] **Step 4: Compare the implementation to the accepted concept**

Use `view_image` on the accepted C1 concept image and on the latest browser screenshot. Record any mismatch in container proportions, palette, typography, evidence visibility, or responsive behavior. Fix visual mismatches before handoff.

- [ ] **Step 5: Run the final verification command**

Run: `npm run verify` from `services/web`.

Expected: contracts check, typecheck, all Vitest tests, Vite build, and boundary checks all exit 0.

### Task 6: Final review and handoff

- [ ] **Step 1: Review the final diff**

Run: `git diff --stat HEAD~5..HEAD` and `git status --short`.

Confirm only the intended `services/web` UI files, tests, and the committed plan/spec changed; do not include `.superpowers` visual-session artifacts in the implementation commits.

- [ ] **Step 2: Report QA evidence**

Include the verified URL, viewport(s), Browser path, commands, test/build results, interaction path, remaining risk, and the final desktop/mobile screenshots.
