<script setup lang="ts">
import type { AskClient } from './api/client';
import AskResult from './components/AskResult.vue';
import { CLARIFICATION_EXAMPLE, EXAMPLES, useAskWorkspace } from './features/ask/use-ask-workspace';

// Explicit test dependency injection; main.ts always uses the real default client.
const props = defineProps<{ client?: AskClient }>();
const {
  question, options, sessionId, result, lastRequest, notice, busy,
  validationMessage, charCount, canSubmit, canRetry, canResetTask, canClarify,
  submit, fillExample, submitClarification, retry, resetTask, stopWaiting, newSession,
} = useAskWorkspace(props.client);
function shortcut(event: KeyboardEvent) {
  if (!event.isComposing) void submit();
}
</script>

<template>
  <main class="workspace">
    <header class="page-heading">
      <div><p class="eyebrow">CHINOOK · 完整产品闭环</p><h1>业务问答工作台</h1><p class="intro">提问、澄清与证据展示 · 实时 NL2SQL＋D01-D12</p></div>
      <button type="button" class="secondary" data-testid="new-session" @click="newSession">新会话</button>
    </header>
    <aside class="boundary" aria-label="当前能力范围">
      <strong>当前真实能力</strong>
      <p>真实 Chinook 样例库＋大模型 NL2SQL 已启用；支持销售额、销量、订单数和客户数等已登记指标，SQL 经只读安全校验后执行。</p>
      <p>D01-D12 知识检索与有证据绑定的目标、增长计算已启用；未登记指标或证据不足时会明确说明。</p>
    </aside>
    <section class="session-bar" aria-label="会话信息">
      <p>Profile：chinook-music</p>
      <p data-testid="session-id">当前会话：{{ sessionId ?? '尚未生成，首次有效业务响应后由后端返回' }}</p>
      <p class="helper">仅本 worker 内存恢复，重启/另一 worker 可能不能续问；会话 ID 不是权限凭证。</p>
    </section>
    <section class="composer" aria-labelledby="question-label">
      <form data-testid="ask-form" @submit.prevent="submit">
        <label id="question-label" for="question">问题或补充条件</label>
        <textarea id="question" v-model="question" data-testid="question-input" rows="4" :disabled="busy" placeholder="例如：客户数量是多少？" aria-describedby="question-hint question-validation" :aria-invalid="Boolean(question && validationMessage)" @keydown.ctrl.enter.prevent="shortcut"></textarea>
        <div class="input-hint"><span id="question-hint">最多 4000 个字符 · Ctrl＋Enter 提交</span><span data-testid="character-count">{{ charCount }} / 4000</span></div>
        <p id="question-validation" class="validation-message" data-testid="validation-message">{{ validationMessage ?? '请求格式可提交；结果仍以当前后端能力为准。' }}</p>
        <fieldset class="request-options" :disabled="busy">
          <legend>请求选项</legend>
          <label><input v-model="options.show_trace" data-testid="show-trace" type="checkbox" /> 返回执行轨迹</label>
          <label for="max-rows">SQL 行上限<input id="max-rows" v-model.number="options.max_rows" data-testid="max-rows" type="number" min="1" max="200" step="1" /></label>
          <label for="top-k">文档片段上限<input id="top-k" v-model.number="options.top_k" data-testid="top-k" type="number" min="1" max="10" step="1" /></label>
        </fieldset>
        <div class="action-row">
          <button type="submit" class="primary" data-testid="submit-question" :disabled="!canSubmit">{{ busy ? '正在等待…' : '发送问题' }}</button>
          <button v-if="busy" type="button" class="secondary" data-testid="stop-waiting" @click="stopWaiting">停止等待</button>
          <button type="button" class="secondary" data-testid="reset-task" :disabled="!canResetTask" @click="resetTask">取消当前问题</button>
        </div>
      </form>
      <p class="helper">停止等待不会强杀 SQL。取消当前问题仅发送后端任务重置，可能等待同会话的旧请求完成。</p>
      <div class="example-list" aria-label="已验证的示例问题">
        <button v-for="(example, index) in EXAMPLES" :key="example" type="button" :disabled="busy" :data-testid="`example-${index}`" @click="fillExample(example)">{{ example }}</button>
      </div>
      <button type="button" class="clarification-example" data-testid="clarification-example" :disabled="busy" @click="fillExample(CLARIFICATION_EXAMPLE)">澄清示例：{{ CLARIFICATION_EXAMPLE }}</button>
      <p class="helper">示例只填入问题，不自动发送。澄清示例补齐年份后仍可能 unsupported。</p>
    </section>
    <section class="response-area" aria-label="本轮响应" :aria-busy="busy" aria-live="polite">
      <p v-if="busy" class="loading-message" role="status" data-testid="loading-message">正在等待 API 响应；不会自动重试。</p>
      <p v-if="notice" class="notice" role="status" data-testid="workspace-notice">{{ notice }}</p>
      <div v-if="lastRequest" class="last-request" data-testid="last-request">
        <p>本次发送：{{ lastRequest.question }}</p>
        <p class="helper">发送时会话：{{ lastRequest.session_id ?? '未指定，由后端新建' }} · SQL 上限 {{ lastRequest.options?.max_rows }} · 文档上限 {{ lastRequest.options?.top_k }}</p>
      </div>
      <AskResult v-if="result" :result="result" :request-options="lastRequest?.options ?? null" :choices-disabled="!canClarify" @clarification="submitClarification" />
      <div v-if="canRetry" class="retry-area">
        <button type="button" class="secondary" data-testid="retry-request" @click="retry">手动重试上次请求</button>
        <p class="helper">将发送上次问题/选项并保留当前会话；后端可能已执行，重试不保证无副作用。</p>
      </div>
      <p v-if="!busy && !result && !notice" class="empty-state">还没有本轮结果。输入问题或选择示例后发送；不会自动补数据或答案。</p>
    </section>
  </main>
</template>
