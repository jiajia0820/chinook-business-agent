import { computed, getCurrentScope, onScopeDispose, reactive, ref, shallowRef } from 'vue';
import type { AskOptions, AskRequest } from '../../contracts';
import { createAskClient, type AskClient, type AskClientResult, type AskStage } from '../../api/client';
import { createLatestAskRunner } from '../../api/latest-request';

export const PROFILE_ID = 'chinook-music';
export const RESET_QUESTION = '取消当前问题';
export const EXAMPLES = [
  '客户数量是多少？', '美国客户数量是多少？', '有哪些音乐类型？',
  '2025年第三季度音频销售额是多少？', '销售额口径是什么？',
];
export const CLARIFICATION_EXAMPLE = '第三季度 Rock 达标了吗？';

/** One settled question in this browser tab; newest first, never sent to the backend. */
export interface HistoryEntry {
  id: number;
  question: string;
  status: string;
  summary: string;
  atMs: number;
}
const HISTORY_LIMIT = 30;

// Source banners are boilerplate; the timeline wants the first substantive line.
function firstLine(text: string | null | undefined): string {
  return (text ?? '').split('\n').map((line) => line.trim())
    .find((line) => line.length > 0 && !line.startsWith('来源：')) ?? '';
}
function summarizeResult(returned: AskClientResult): { status: string; summary: string } {
  if (returned.kind === 'business') {
    const response = returned.response;
    const summary = firstLine(response.clarification?.question) || firstLine(response.answer)
      || firstLine(response.error?.message) || '（本轮没有文本结论）';
    return { status: response.status, summary };
  }
  if (returned.kind === 'api_error') return { status: 'api_error', summary: firstLine(returned.error.message) };
  return { status: returned.reason, summary: firstLine(returned.message) };
}

function textError(text: string): string | null {
  if (!text.trim()) return '请输入问题或补充条件。';
  if (Array.from(text.trim()).length > 4000) return '本阶段问题最多支持 4000 个字符，请缩短后发送。';
  return null;
}

export function useAskWorkspace(client: AskClient = createAskClient()) {
  const runner = createLatestAskRunner(client);
  const question = ref('');
  const options = reactive({ show_trace: true, max_rows: 50, top_k: 5 });
  const sessionId = ref<string | null>(null);
  const phase = ref<'idle' | 'loading' | 'settled'>('idle');
  const result = shallowRef<AskClientResult | null>(null);
  const lastRequest = shallowRef<AskRequest | null>(null);
  const notice = ref<string | null>(null);
  // Live backend node progress for the in-flight question only; never persisted.
  const stages = ref<AskStage[]>([]);
  const history = ref<HistoryEntry[]>([]);
  let historySeq = 0;
  let revision = 0;
  const disposed = ref(false);

  const optionsError = computed(() => {
    if (!Number.isInteger(options.max_rows) || options.max_rows < 1 || options.max_rows > 200) return 'SQL 行上限必须是 1～200 的整数。';
    if (!Number.isInteger(options.top_k) || options.top_k < 1 || options.top_k > 10) return '文档片段上限必须是 1～10 的整数。';
    if (typeof options.show_trace !== 'boolean') return '执行轨迹选项必须为开或关。';
    return null;
  });
  const validationMessage = computed(() => textError(question.value) ?? optionsError.value);
  const busy = computed(() => phase.value === 'loading');
  const canSubmit = computed(() => !disposed.value && !busy.value && validationMessage.value === null);
  const canResetTask = computed(() => !disposed.value && !busy.value && sessionId.value !== null && optionsError.value === null);
  const canClarify = computed(() => canResetTask.value && result.value?.kind === 'business' && result.value.response.status === 'clarification_required');
  const canRetry = computed(() => {
    if (disposed.value || busy.value || !lastRequest.value || !result.value) return false;
    if (result.value.kind === 'api_error') return result.value.error.retryable;
    if (result.value.kind === 'request_error') return result.value.reason !== 'invalid_request';
    return result.value.response.status === 'error' && result.value.response.error?.retryable === true;
  });

  function payload(text: string, requestOptions: AskOptions = { ...options }): AskRequest {
    return {
      question: text.trim(), profile_id: PROFILE_ID,
      ...(sessionId.value === null ? {} : { session_id: sessionId.value }),
      options: { ...requestOptions },
    };
  }
  async function execute(request: AskRequest): Promise<boolean> {
    if (disposed.value || busy.value) return false;
    const current = ++revision;
    phase.value = 'loading';
    result.value = null;
    notice.value = null;
    stages.value = [];
    lastRequest.value = structuredClone(request);
    try {
      const returned = await runner.run(request, (stage) => {
        // Progress from a superseded, stopped or already settled request must not reach the UI.
        if (disposed.value || current !== revision || phase.value !== 'loading') return;
        stages.value = [...stages.value, stage];
      });
      if (disposed.value || current !== revision || returned.kind === 'stale') return false;
      result.value = returned.result;
      const settled = summarizeResult(returned.result);
      history.value = [{ id: ++historySeq, question: request.question, status: settled.status, summary: settled.summary, atMs: Date.now() }, ...history.value].slice(0, HISTORY_LIMIT);
      if (returned.result.kind === 'business') {
        sessionId.value = returned.result.response.session_id;
        if (returned.result.response.intent.name === 'reset_context') question.value = '';
      }
      phase.value = 'settled';
      return true;
    } catch {
      if (disposed.value || current !== revision) return false;
      // Defensive handling for an unexpected client failure; no raw exception.
      result.value = { kind: 'request_error', reason: 'network',
        message: '前端请求未正常完成；未获得可用响应，请检查后手动再试。', requestId: 'request-id-unavailable' };
      phase.value = 'settled';
      return true;
    }
  }
  async function submit() {
    if (!canSubmit.value) return false;
    return execute(payload(question.value));
  }
  function fillExample(text: string) {
    if (disposed.value || busy.value) return;
    question.value = text;
    notice.value = null;
  }
  async function submitClarification(text: string) {
    if (!canClarify.value || textError(text)) return false;
    question.value = text;
    return execute(payload(text));
  }
  async function retry() {
    if (!canRetry.value || !lastRequest.value) return false;
    const saved = lastRequest.value;
    question.value = saved.question;
    // Preserve the last sent question/options, adopting only the valid current
    // backend session. No automatic repeat or invented confirmed/binding fields.
    return execute(payload(saved.question, saved.options));
  }
  async function resetTask() {
    if (!canResetTask.value) return false;
    return execute(payload(RESET_QUESTION));
  }
  function stopWaiting() {
    if (!busy.value || disposed.value) return;
    ++revision;
    runner.cancel();
    phase.value = 'idle';
    result.value = null;
    stages.value = [];
    notice.value = '已停止前端等待；后端任务或 SQL 可能仍在执行，迟到响应不会显示。';
  }
  function newSession() {
    if (disposed.value) return;
    ++revision;
    runner.cancel();
    sessionId.value = null;
    question.value = '';
    result.value = null;
    lastRequest.value = null;
    history.value = [];
    phase.value = 'idle';
    stages.value = [];
    notice.value = '已清空本地会话入口；下次不带旧会话 ID，这不会删除后端历史。';
  }
  function dispose() {
    if (disposed.value) return;
    disposed.value = true;
    ++revision;
    runner.cancel();
    phase.value = 'idle';
    result.value = null;
    stages.value = [];
  }
  if (getCurrentScope()) onScopeDispose(dispose);

  return {
    question, options, optionsError, validationMessage, busy, canSubmit, canRetry, canResetTask, canClarify,
    charCount: computed(() => Array.from(question.value.trim()).length),
    sessionId: computed(() => sessionId.value), phase: computed(() => phase.value),
    result: computed(() => result.value), notice: computed(() => notice.value),
    stages: computed(() => stages.value),
    currentStage: computed(() => stages.value[stages.value.length - 1] ?? null),
    history: computed(() => history.value),
    lastRequest: computed(() => {
      if (!lastRequest.value) return null;
      const snapshot = structuredClone(lastRequest.value);
      if (snapshot.options) Object.freeze(snapshot.options);
      return Object.freeze(snapshot);
    }),
    submit, fillExample, submitClarification, retry, resetTask, stopWaiting, newSession, dispose,
  };
}
