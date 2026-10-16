<script setup lang="ts">
import { computed, ref } from 'vue';
import type { AskClient } from './api/client';
import AskResult from './components/AskResult.vue';
import EvidencePanel from './components/evidence/EvidencePanel.vue';
import { useAskWorkspace } from './features/ask/use-ask-workspace';
import { questionGroupsForProfile } from './features/ask/evaluation-questions';
import { PROFILE_EXAMPLES } from './features/ask/profiles';

// Explicit test dependency injection; main.ts always uses the real default client.
const props = defineProps<{ client?: AskClient }>();
const {
  question, options, result, lastRequest, notice, busy,
  validationMessage, charCount, canSubmit, canRetry, canResetTask, canClarify,
  submit, fillExample, submitClarification, retry, resetTask, stopWaiting, newSession,
  history, sessionId,
  profiles, selectedProfileId, selectedProfile, profileLoading, profileError, selectProfile,
} = useAskWorkspace(props.client);
const evidencePanel = ref<InstanceType<typeof EvidencePanel> | null>(null);
const sidebarOpen = ref(false);
const evidenceOpen = ref(false);
const business = computed(() => result.value?.kind === 'business' ? result.value.response : null);
const profileExamples = computed(() => PROFILE_EXAMPLES[selectedProfileId.value] ?? PROFILE_EXAMPLES['chinook-music']!);
const evaluationQuestionGroups = computed(() => questionGroupsForProfile(selectedProfileId.value));
const statusLabels: Record<string, string> = {
  answered: '已回答', clarification_required: '需要补充条件', insufficient_evidence: '证据不足',
  unsupported: '当前能力不支持', error: '业务处理出错', api_error: '接口错误',
  invalid_request: '请求不合法', network: '连接失败', timeout: '等待超时',
  cancelled: '已停止等待', invalid_response: '响应格式异常',
};
const clock = (ms: number) => new Date(ms).toLocaleTimeString('zh-CN', { hour12: false });
function shortcut(event: KeyboardEvent) {
  if (!event.isComposing) void submit();
}
function focusEvidence(refId: string) {
  evidencePanel.value?.reveal(refId);
  evidenceOpen.value = true;
  const element = document.getElementById(refId);
  if (!element) return;
  let parent = element.parentElement;
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
    parent = parent.parentElement;
  }
  element.querySelectorAll('details').forEach((details) => { details.open = true; });
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  element.scrollIntoView?.({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
  element.setAttribute('tabindex', '-1');
  element.focus({ preventScroll: true });
}
</script>

<template>
  <main class="workspace-shell" :class="{ 'sidebar-open': sidebarOpen, 'evidence-open': evidenceOpen }" data-testid="workspace-shell" @keydown.esc="sidebarOpen = false; evidenceOpen = false">
    <aside id="workspace-sidebar" class="workspace-sidebar" data-testid="workspace-sidebar" aria-label="会话导航">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true"></span>
        <div class="brand-copy">
          <div class="brand-title-row">
            <strong>{{ selectedProfile?.display_name?.split(' + ')[0] ?? '数据集' }}</strong>
            <details class="database-config">
              <summary data-testid="database-config-trigger" aria-label="选择数据库配置">
                <span>{{ selectedProfile?.display_name ?? '选择数据集' }}</span><span class="database-config-chevron" aria-hidden="true">▾</span>
              </summary>
              <div class="database-config-menu" role="menu" aria-label="数据库配置选项">
                <div v-if="profileLoading" class="database-config-current" role="status">正在读取数据集…</div>
                <template v-else>
                  <button
                    v-for="profile in profiles"
                    :key="profile.profile_id"
                    type="button"
                    :data-testid="`database-option-${profile.profile_id}`"
                    role="menuitem"
                    :aria-current="profile.profile_id === selectedProfileId ? 'true' : undefined"
                    :disabled="profile.status !== 'ready' || busy"
                    :title="profile.status === 'ready' ? `切换到 ${profile.display_name}` : '当前数据集不可用'"
                    @click="selectProfile(profile.profile_id)"
                  >
                    {{ profile.display_name }}<span>{{ profile.profile_id === selectedProfileId ? '当前' : profile.status === 'ready' ? '可用' : '不可用' }}</span>
                  </button>
                </template>
              </div>
            </details>
          </div>
          <span>Business Agent</span>
        </div>
      </div>
      <button type="button" class="new-session" data-testid="new-session" @click="newSession(); sidebarOpen = false"><span aria-hidden="true">＋</span> 新会话</button>
      <div class="workspace-link" aria-current="page"><span class="workspace-symbol" aria-hidden="true"></span>分析工作台<span class="active-dot" aria-hidden="true"></span></div>
      <section v-if="history.length" class="session-history" aria-label="本次会话问答记录" data-testid="session-history">
        <h2>本次会话问答记录 <span>{{ history.length }}</span></h2>
        <ol class="history-list">
          <li v-for="entry in history" :key="entry.id" :data-status="entry.status" :data-testid="`history-${entry.id}`">
            <button type="button" class="history-question" :disabled="busy" :title="`重新填入：${entry.question}`" @click="fillExample(entry.question); sidebarOpen = false">{{ entry.question }}</button>
            <span class="history-time">{{ clock(entry.atMs) }}</span>
            <span class="status-tag" :data-status="entry.status">{{ statusLabels[entry.status] ?? entry.status }}</span>
            <span class="history-summary" :title="entry.summary">{{ entry.summary }}</span>
          </li>
        </ol>
        <p class="helper">仅存于本标签页内存，刷新或新会话即清空；点击问题可重新填入输入框。</p>
      </section>
      <div v-else class="history-placeholder"><span>会话记录</span><p>你的分析问题将在这里保留。</p></div>
      <section class="session-bar" aria-label="会话信息"><span class="section-caption">当前数据配置</span><p>{{ selectedProfile?.profile_id ?? '未选择' }}</p><span class="profile-note">{{ selectedProfile?.capabilities.includes('rag') ? 'SQL 数据库 · 业务知识库' : 'SQL 数据库' }}</span><p v-if="profileError" class="helper">{{ profileError }}</p><p v-if="sessionId" class="session-id" :title="sessionId">会话：{{ sessionId }}</p></section>
    </aside>
    <section class="workspace-main" data-testid="workspace-main">
      <header class="page-heading">
        <button type="button" class="mobile-toggle" aria-controls="workspace-sidebar" :aria-expanded="sidebarOpen" aria-label="展开或收起会话导航" @click="sidebarOpen = !sidebarOpen">会话</button>
        <div class="heading-copy"><span class="page-label">分析工作台</span><h1>多模态数据驱动的可解释精准问数/问答智能体</h1></div>
        <div class="run-state" :data-status="busy ? 'loading' : business?.status ?? 'idle'"><span class="state-dot" aria-hidden="true"></span>{{ busy ? '正在执行' : business ? statusLabels[business.status] : '等待提问' }}</div>
        <button type="button" class="evidence-toggle" aria-controls="workspace-evidence" :aria-expanded="evidenceOpen" @click="evidenceOpen = !evidenceOpen">证据</button>
      </header>
      <div class="task-body">
        <section class="response-area" aria-label="本轮响应" :aria-busy="busy" aria-live="polite">
          <div v-if="lastRequest" class="last-request" data-testid="last-request"><span class="section-caption">本轮问题</span><p>本次发送：{{ lastRequest.question }}</p><p class="helper">发送时会话：{{ lastRequest.session_id ?? '未指定，由后端新建' }} · SQL 上限 {{ lastRequest.options?.max_rows }} · 文档上限 {{ lastRequest.options?.top_k }}</p></div>
          <div v-if="busy" class="loading-message" role="status" data-testid="loading-message"><span class="run-spinner" aria-hidden="true"></span><div><strong>Agent 正在处理你的问题</strong><p>正在等待 API 响应；不会自动重试。</p></div></div>
          <p v-if="notice" class="notice" role="status" data-testid="workspace-notice">{{ notice }}</p>
          <AskResult v-if="result" :result="result" :request-options="lastRequest?.options ?? null" :choices-disabled="!canClarify" :show-evidence="false" @evidence-focus="focusEvidence" @clarification="submitClarification" />
          <div v-if="canRetry" class="retry-area"><button type="button" class="secondary" data-testid="retry-request" @click="retry">手动重试上次请求</button><p class="helper">将发送上次问题/选项并保留当前会话；后端可能已执行，重试不保证无副作用。</p></div>
          <div v-if="!busy && !result && !notice" class="empty-state"><span class="agent-glyph prompt-terminal-glyph" data-testid="agent-empty-icon" aria-hidden="true"><svg viewBox="0 0 80 80" fill="none"><rect x="13" y="16" width="54" height="44" rx="10" fill="#17243a" stroke="#6e9cff" stroke-width="1.8"/><path d="m27 32 8 6-8 6M41 45h13" stroke="#7ae2b0" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="24" r="2" fill="#efbd72"/><circle cx="31" cy="24" r="2" fill="#6e9cff"/></svg></span><h2>从一个问题，开始分析</h2><p>提问、澄清与证据展示 · 实时 NL2SQL</p><span>还没有本轮结果。输入问题或选择示例后发送；不会自动补数据或答案。</span></div>
        </section>
        <details class="example-library" :open="!lastRequest">
          <summary>试试这些问题 <span>点击填入，按需发送</span></summary>
          <div class="example-list" aria-label="已验证的示例问题"><button v-for="(example, index) in profileExamples.examples" :key="example" type="button" :disabled="busy" :data-testid="`example-${index}`" @click="fillExample(example)">{{ example }}<span aria-hidden="true">↗</span></button></div>
          <button type="button" class="clarification-example" data-testid="clarification-example" :disabled="busy" @click="fillExample(profileExamples.clarification)">澄清示例：{{ profileExamples.clarification }}</button>
        </details>
        <details class="more-questions-picker" data-testid="more-questions-picker">
          <summary data-testid="more-questions-summary"><span>更多问题</span><span>按能力分类选择测评题</span></summary>
          <div class="more-questions-menu" data-testid="more-questions-menu">
            <details v-for="(group, groupIndex) in evaluationQuestionGroups" :key="group.label" class="more-question-category">
              <summary :data-testid="`more-question-category-${groupIndex}`"><span>{{ group.label }}</span><span>{{ group.questions.length }} 题</span></summary>
              <div class="more-question-list">
                <button v-for="(questionText, questionIndex) in group.questions" :key="`${group.label}-${questionIndex}`" type="button" :disabled="busy" :data-testid="`more-question-${groupIndex}-${questionIndex}`" @click="fillExample(questionText)">{{ questionText }}</button>
              </div>
            </details>
          </div>
        </details>
      </div>
      <section class="composer" aria-labelledby="question-label">
      <form data-testid="ask-form" @submit.prevent="submit">
        <label id="question-label" for="question">{{ lastRequest ? '继续提问或补充条件' : '问题或补充条件' }}</label>
        <div class="question-box">
          <textarea id="question" v-model="question" data-testid="question-input" rows="2" :disabled="busy" placeholder="向 Agent 提问，例如：客户数量是多少？" aria-describedby="question-hint" :aria-invalid="Boolean(question && validationMessage)" @keydown.ctrl.enter.prevent="shortcut"></textarea>
          <div class="question-box-footer">
            <div class="input-hint"><span id="question-hint">最多 4000 个字符 · Ctrl＋Enter 提交</span><span data-testid="character-count">{{ charCount }} / 4000</span></div>
            <div class="action-row">
              <button type="submit" class="primary" data-testid="submit-question" :disabled="!canSubmit">{{ busy ? '正在等待…' : '发送问题' }}<span v-if="!busy" aria-hidden="true">↑</span></button>
              <button v-if="busy" type="button" class="secondary" data-testid="stop-waiting" @click="stopWaiting">停止等待</button>
              <button type="button" class="secondary" data-testid="reset-task" :disabled="!canResetTask" @click="resetTask">取消当前问题</button>
            </div>
          </div>
        </div>
        <fieldset class="request-options" :disabled="busy">
          <legend>请求选项</legend>
          <label><input v-model="options.show_trace" data-testid="show-trace" type="checkbox" /> 返回执行轨迹</label>
          <label for="max-rows">SQL 行上限<input id="max-rows" v-model.number="options.max_rows" data-testid="max-rows" type="number" min="1" max="200" step="1" /></label>
          <label for="top-k">文档片段上限<input id="top-k" v-model.number="options.top_k" data-testid="top-k" type="number" min="1" max="10" step="1" /></label>
        </fieldset>
      </form>
      <p v-if="question && validationMessage" class="validation-message" role="status">{{ validationMessage }}</p>
      </section>
    </section>
    <aside id="workspace-evidence" class="workspace-evidence" data-testid="workspace-evidence" aria-label="常驻证据">
      <EvidencePanel v-if="business" :key="business.request_id" ref="evidencePanel" :response="business" :show-trace-requested="lastRequest?.options?.show_trace ?? null" :show-documents="selectedProfile?.capabilities.includes('rag') ?? false" @focus-ref="focusEvidence">
        <template #close><button type="button" class="evidence-close" aria-label="收起证据区" @click="evidenceOpen = false">关闭</button></template>
      </EvidencePanel>
      <div v-else class="evidence-placeholder">
        <header class="evidence-toolbar">
          <div class="evidence-toolbar-heading"><h3>Evidence</h3><button type="button" class="evidence-close" aria-label="收起证据区" @click="evidenceOpen = false">关闭</button></div>
          <div class="empty-evidence-tabs" aria-hidden="true"><span>SQL</span><span>Sources</span><span>Metrics</span><span>Trace</span></div>
        </header>
        <div class="evidence-placeholder-body"><span class="evidence-glyph prompt-terminal-glyph" data-testid="evidence-empty-icon" aria-hidden="true"><svg viewBox="0 0 80 80" fill="none"><rect x="13" y="16" width="54" height="44" rx="10" fill="#17243a" stroke="#6e9cff" stroke-width="1.8"/><path d="m27 32 8 6-8 6M41 45h13" stroke="#7ae2b0" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="24" r="2" fill="#efbd72"/><circle cx="31" cy="24" r="2" fill="#6e9cff"/></svg></span><h3>{{ busy ? '等待本轮结果' : '每个结论，都有依据' }}</h3><p>提问后，在这里查看 SQL、引用原文、指标口径和执行记录。</p><span>证据随本轮响应更新</span></div>
      </div>
    </aside>
    <button v-if="sidebarOpen || evidenceOpen" class="drawer-backdrop" type="button" aria-label="关闭展开区域" @click="sidebarOpen = false; evidenceOpen = false"></button>
  </main>
</template>
