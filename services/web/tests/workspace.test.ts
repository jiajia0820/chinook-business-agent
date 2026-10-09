import { afterEach, describe, expect, it, vi } from 'vitest';
import { effectScope } from 'vue';
import type { AskClient, AskClientResult, AskStage } from '../src/api/client';
import { CLARIFICATION_EXAMPLE, EXAMPLES, RESET_QUESTION, useAskWorkspace } from '../src/features/ask/use-ask-workspace';
import { deferred } from './recorded-http';
import { apiError, business, clarificationPair, requestError, resetResponse } from './workspace-fixtures';

const active: ReturnType<typeof useAskWorkspace>[] = [];
function workspace(client: AskClient) {
  const state = useAskWorkspace(client);
  active.push(state);
  return state;
}
afterEach(() => { active.splice(0).forEach((state) => state.dispose()); });

describe('question/session state without a runtime fixture fallback', () => {
  it('starts idle with contract defaults and never auto-fetches examples', () => {
    const ask = vi.fn<AskClient['ask']>();
    const state = workspace({ ask });
    expect(state.phase.value).toBe('idle');
    expect(state.sessionId.value).toBeNull();
    expect(state.options).toEqual({ show_trace: true, max_rows: 50, top_k: 5 });
    expect(state.canSubmit.value).toBe(false);
    expect(state.canResetTask.value).toBe(false);
    for (const example of [...EXAMPLES, CLARIFICATION_EXAMPLE]) state.fillExample(example);
    expect(state.question.value).toBe(CLARIFICATION_EXAMPLE);
    expect(ask).not.toHaveBeenCalled();
  });
  it.each([['empty', ''], ['spaces', '   '], ['controls', '\n\t'], ['overlong', '问'.repeat(4001)]])('blocks invalid text: %s', async (_case, text) => {
    const ask = vi.fn<AskClient['ask']>();
    const state = workspace({ ask });
    state.question.value = text;
    expect(state.validationMessage.value).not.toBeNull();
    expect(await state.submit()).toBe(false);
    expect(ask).not.toHaveBeenCalled();
  });
  it('counts Unicode code points rather than rejecting 4000 non-BMP characters', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business());
    const state = workspace({ ask });
    state.question.value = ` ${'😀'.repeat(4000)} `;
    expect(state.charCount.value).toBe(4000);
    expect(await state.submit()).toBe(true);
    expect(ask.mock.calls[0]?.[0].question.length).toBe(8000);
  });
  it.each([0, 201, 1.5, NaN])('blocks invalid max_rows %s', async (value) => {
    const ask = vi.fn<AskClient['ask']>();
    const state = workspace({ ask });
    state.question.value = EXAMPLES[0]!;
    state.options.max_rows = value;
    expect(await state.submit()).toBe(false);
    expect(state.optionsError.value).toContain('SQL');
    expect(ask).not.toHaveBeenCalled();
  });
  it.each([0, 11, 1.5, NaN])('blocks invalid top_k %s', async (value) => {
    const ask = vi.fn<AskClient['ask']>();
    const state = workspace({ ask });
    state.question.value = EXAMPLES[0]!;
    state.options.top_k = value;
    expect(await state.submit()).toBe(false);
    expect(ask).not.toHaveBeenCalled();
  });
  it('does not coerce invalid show_trace', async () => {
    const ask = vi.fn<AskClient['ask']>();
    const state = workspace({ ask });
    state.question.value = EXAMPLES[0]!;
    // Deliberately bypass TypeScript to exercise the UI boundary.
    Object.assign(state.options, { show_trace: 'true' });
    expect(await state.submit()).toBe(false);
    expect(ask).not.toHaveBeenCalled();
  });
  it('sends only trimmed public fields and immutable option snapshots', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const state = workspace({ ask });
    state.question.value = '  客户数量是多少？  ';
    const work = state.submit();
    expect(ask.mock.calls[0]?.[0]).toEqual({ question: '客户数量是多少？', profile_id: 'chinook-music', options: { show_trace: true, max_rows: 50, top_k: 5 } });
    state.options.max_rows = 3;
    expect(() => { state.lastRequest.value!.options!.max_rows = 2; }).toThrow(TypeError);
    expect(ask.mock.calls[0]?.[0].options?.max_rows).toBe(50);
    expect(state.lastRequest.value?.options?.max_rows).toBe(50);
    pending.resolve(business());
    await work;
  });
  it('adopts only a business session and sends it on a follow-up', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business('answered', 'session-valid'));
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    expect(state.sessionId.value).toBe('session-valid');
    state.fillExample(EXAMPLES[1]!);
    await state.submit();
    expect(ask.mock.calls[1]?.[0].session_id).toBe('session-valid');
  });
  it.each([apiError(), requestError()])('does not invent a session after $kind', async (error) => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(error);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    expect(state.sessionId.value).toBeNull();
    expect(state.canResetTask.value).toBe(false);
  });
  it('preserves a known session on API failure but clears the old answer at send', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business('answered', 'session-known')).mockReturnValueOnce(pending.promise);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    const work = state.submit();
    expect(state.result.value).toBeNull();
    expect(state.busy.value).toBe(true);
    pending.resolve(apiError());
    await work;
    expect(state.sessionId.value).toBe('session-known');
    expect(state.phase.value).toBe('settled');
  });
  it('uses the recorded missing-year → explicit year → unsupported sequence, never an auto-year', async () => {
    const [first, second] = clarificationPair();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    const state = workspace({ ask });
    state.fillExample(CLARIFICATION_EXAMPLE);
    await state.submit();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(state.question.value).toBe(CLARIFICATION_EXAMPLE);
    expect(state.canClarify.value).toBe(true);
    expect(await state.submitClarification('2025 年')).toBe(true);
    expect(ask.mock.calls[1]?.[0]).toMatchObject({ question: '2025 年', session_id: first.response.session_id });
    expect(Object.keys(ask.mock.calls[1]![0]).sort()).toEqual(['options', 'profile_id', 'question', 'session_id']);
    expect(state.result.value).toMatchObject({ kind: 'business', response: { status: 'unsupported' } });
    expect(state.canClarify.value).toBe(false);
  });
  it('blocks candidate submission before clarification or for invalid candidate text', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business('clarification_required'));
    const state = workspace({ ask });
    expect(await state.submitClarification('2025 年')).toBe(false);
    state.fillExample(CLARIFICATION_EXAMPLE);
    await state.submit();
    for (const text of ['', '  ', '问'.repeat(4001)]) expect(await state.submitClarification(text)).toBe(false);
    expect(ask).toHaveBeenCalledTimes(1);
  });
  it('blocks duplicate submit/reset/retry/example changes while waiting', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business()).mockReturnValueOnce(pending.promise);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    const work = state.submit();
    expect(await state.submit()).toBe(false);
    expect(await state.resetTask()).toBe(false);
    expect(await state.retry()).toBe(false);
    state.fillExample('不能覆盖');
    expect(state.question.value).toBe(EXAMPLES[0]);
    expect(ask).toHaveBeenCalledTimes(2);
    pending.resolve(business());
    await work;
  });
  it('stopping wait aborts and ignores late evidence but retains a known session', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business('answered', 'known')).mockReturnValueOnce(pending.promise);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    const work = state.submit();
    state.stopWaiting();
    expect(ask.mock.calls[1]?.[1]?.signal?.aborted).toBe(true);
    expect(state.notice.value).toContain('可能仍在执行');
    expect(state.sessionId.value).toBe('known');
    pending.resolve(business('answered', 'late'));
    expect(await work).toBe(false);
    expect(state.result.value).toBeNull();
    expect(state.sessionId.value).toBe('known');
  });
  it('new-session clears local state and does not restore a late old session', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(business('answered', 'new'));
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    const work = state.submit();
    state.newSession();
    expect(state.lastRequest.value).toBeNull();
    expect(state.question.value).toBe('');
    expect(state.notice.value).toContain('不会删除后端历史');
    state.fillExample(EXAMPLES[1]!);
    await state.submit();
    expect(ask.mock.calls[1]?.[0].session_id).toBeUndefined();
    pending.resolve(business('answered', 'old'));
    expect(await work).toBe(false);
    expect(state.sessionId.value).toBe('new');
    expect(state.result.value).toMatchObject({ kind: 'business', response: { session_id: 'new' } });
  });
  it.each([apiError(true), requestError('network'), requestError('timeout'), requestError('cancelled'), requestError('invalid_response')])('retries $kind only upon a manual action', async (error) => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(error).mockResolvedValueOnce(business());
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(state.canRetry.value).toBe(true);
    state.question.value = '未发送的新问题';
    state.options.max_rows = 2;
    await state.retry();
    expect(ask.mock.calls[1]?.[0]).toEqual(ask.mock.calls[0]?.[0]);
    expect(ask).toHaveBeenCalledTimes(2);
  });
  it.each([apiError(false), requestError('invalid_request'), business('unsupported'), business('insufficient_evidence'), business('answered')])('does not offer a retry for ineligible $kind', async (result) => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(result);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    expect(state.canRetry.value).toBe(false);
    expect(await state.retry()).toBe(false);
    expect(ask).toHaveBeenCalledTimes(1);
  });
  it('allows manual retry of a retryable business error on its valid returned session', async () => {
    const error = business('error', 'known');
    error.response.error!.retryable = true;
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(error);
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    await state.retry();
    expect(ask.mock.calls[1]?.[0].session_id).toBe('known');
  });
  it('sends cancel phrase only with a known idle session and waits for backend acknowledgment', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business('answered', 'known')).mockReturnValueOnce(pending.promise);
    const state = workspace({ ask });
    expect(await state.resetTask()).toBe(false);
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    const work = state.resetTask();
    expect(ask.mock.calls[1]?.[0]).toMatchObject({ question: RESET_QUESTION, session_id: 'known' });
    expect(state.question.value).toBe(EXAMPLES[0]);
    pending.resolve(resetResponse('known'));
    await work;
    expect(state.question.value).toBe('');
    expect(state.sessionId.value).toBe('known');
    expect(state.result.value).toMatchObject({ kind: 'business', response: { intent: { name: 'reset_context' } } });
  });
  it('failed cancel does not pretend that backend context was cleared', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business()).mockResolvedValueOnce(apiError(false));
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    await state.resetTask();
    expect(state.question.value).toBe(EXAMPLES[0]);
    expect(state.result.value?.kind).toBe('api_error');
  });
  it('unexpected exceptions yield fixed safe messages, not raw details', async () => {
    const ask = vi.fn<AskClient['ask']>().mockRejectedValue(new Error('PRIVATE_INTERNAL_PATH'));
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    await state.submit();
    expect(JSON.stringify(state.result.value)).not.toContain('PRIVATE_INTERNAL_PATH');
    expect(state.result.value?.kind).toBe('request_error');
    expect(state.busy.value).toBe(false);
  });
  it('scope disposal aborts waiting and prevents late/new work', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const scope = effectScope();
    const state = scope.run(() => workspace({ ask }))!;
    state.fillExample(EXAMPLES[0]!);
    const work = state.submit();
    scope.stop();
    expect(ask.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(state.canSubmit.value).toBe(false);
    pending.resolve(business());
    expect(await work).toBe(false);
    expect(await state.submit()).toBe(false);
    expect(state.result.value).toBeNull();
  });
  it('separate mounted workspaces do not share a session', async () => {
    const first = workspace({ ask: vi.fn<AskClient['ask']>().mockResolvedValue(business('answered', 'first')) });
    const second = workspace({ ask: vi.fn<AskClient['ask']>().mockResolvedValue(business('answered', 'second')) });
    first.fillExample(EXAMPLES[0]!);
    second.fillExample(EXAMPLES[0]!);
    await first.submit();
    expect(second.sessionId.value).toBeNull();
    await second.submit();
    expect(first.sessionId.value).toBe('first');
    expect(second.sessionId.value).toBe('second');
  });
});

describe('live stage progress for the in-flight question', () => {
  function streamingClient() {
    const pending = deferred<AskClientResult>();
    let emit: ((stage: AskStage) => void) | null = null;
    const askStream = vi.fn<NonNullable<AskClient['askStream']>>((_payload, _options, onStage) => {
      emit = onStage ?? null;
      return pending.promise;
    });
    const ask = vi.fn<AskClient['ask']>();
    return { client: { ask, askStream }, pending, ask, askStream, emit: () => emit };
  }
  it('records stages in order while waiting and exposes the latest one', async () => {
    const fixture = streamingClient();
    const state = workspace(fixture.client);
    state.fillExample(EXAMPLES[0]!);
    const work = state.submit();
    expect(state.stages.value).toEqual([]);
    expect(state.currentStage.value).toBeNull();
    fixture.emit()?.({ node: 'parse', label: '意图与时间解析' });
    fixture.emit()?.({ node: 'call_sql', label: 'SQL 生成与只读执行' });
    expect(state.stages.value.map((stage) => stage.node)).toEqual(['parse', 'call_sql']);
    expect(state.currentStage.value?.label).toBe('SQL 生成与只读执行');
    fixture.pending.resolve(business());
    expect(await work).toBe(true);
    // Settled: late progress from the same stream must not extend the trail.
    fixture.emit()?.({ node: 'compose', label: '答案与证据组装' });
    expect(state.stages.value).toHaveLength(2);
  });
  it('drops progress after stopping and clears the trail', async () => {
    const fixture = streamingClient();
    const state = workspace(fixture.client);
    state.fillExample(EXAMPLES[0]!);
    const work = state.submit();
    fixture.emit()?.({ node: 'parse', label: '意图与时间解析' });
    expect(state.stages.value).toHaveLength(1);
    state.stopWaiting();
    expect(state.stages.value).toEqual([]);
    fixture.emit()?.({ node: 'call_sql', label: 'SQL 生成与只读执行' });
    expect(state.stages.value).toEqual([]);
    fixture.pending.resolve(business());
    expect(await work).toBe(false);
    expect(state.phase.value).toBe('idle');
  });
  it('keeps a client without the progress stream working through the plain endpoint', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business());
    const state = workspace({ ask });
    state.fillExample(EXAMPLES[0]!);
    expect(await state.submit()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(state.stages.value).toEqual([]);
  });
});

describe('local session question history', () => {
  it('starts empty and records settled questions newest first', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business()).mockResolvedValueOnce(requestError());
    const state = workspace({ ask });
    expect(state.history.value).toEqual([]);
    state.question.value = EXAMPLES[0]!;
    expect(await state.submit()).toBe(true);
    state.question.value = EXAMPLES[1]!;
    expect(await state.submit()).toBe(true);
    expect(state.history.value.map((entry) => entry.status)).toEqual(['network', 'answered']);
    expect(state.history.value.map((entry) => entry.question)).toEqual([EXAMPLES[1], EXAMPLES[0]]);
    for (const entry of state.history.value) {
      expect(entry.summary.length).toBeGreaterThan(0);
      expect(entry.summary).not.toContain('\n');
      expect(entry.atMs).toBeGreaterThan(0);
    }
    expect(state.history.value[0]!.id).toBeGreaterThan(state.history.value[1]!.id);
  });
  it('summarizes clarification and API failures without inventing answers', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(resetResponse()).mockResolvedValueOnce(apiError());
    const state = workspace({ ask });
    state.question.value = EXAMPLES[0]!;
    await state.submit();
    state.question.value = EXAMPLES[1]!;
    await state.submit();
    expect(state.history.value[0]!.status).toBe('api_error');
    expect(state.history.value[0]!.summary).toContain('API 暂不可用');
    expect(state.history.value[1]!.status).toBe('clarification_required');
    expect(state.history.value[1]!.summary).toContain('清空当前任务');
  });
  it('does not record a question whose wait was stopped', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const state = workspace({ ask });
    state.question.value = EXAMPLES[0]!;
    const work = state.submit();
    state.stopWaiting();
    pending.resolve(business());
    expect(await work).toBe(false);
    await Promise.resolve();
    expect(state.history.value).toEqual([]);
  });
  it('clears with a new session and keeps only the newest 30 entries', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business());
    const state = workspace({ ask });
    for (let index = 0; index < 31; index += 1) {
      state.question.value = `${EXAMPLES[0]}${index}`;
      expect(await state.submit()).toBe(true);
    }
    expect(state.history.value).toHaveLength(30);
    expect(state.history.value[0]!.question).toBe(`${EXAMPLES[0]}30`);
    state.newSession();
    expect(state.history.value).toEqual([]);
  });
});
