// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils';
import App from '../src/App.vue';
import { createAskClient, type AskClient, type AskClientResult } from '../src/api/client';
import type { AskRequest } from '../src/contracts';
import { CLARIFICATION_EXAMPLE, EXAMPLES, RESET_QUESTION } from '../src/features/ask/use-ask-workspace';
import { deferred, jsonResponse } from './recorded-http';
import { apiError, business, clarificationPair, requestError, resetResponse } from './workspace-fixtures';

enableAutoUnmount(afterEach);
const selector = (name: string) => `[data-testid="${name}"]`;
const makeApp = (ask: AskClient['ask']) => mount(App, { props: { client: { ask } } });
type AppWrapper = ReturnType<typeof makeApp>;
async function send(wrapper: AppWrapper, question = EXAMPLES[0]!) {
  await wrapper.get(selector('question-input')).setValue(question);
  await wrapper.get(selector('ask-form')).trigger('submit');
  await flushPromises();
}

describe('4B page interactions in simulated DOM (not live browser E2E)', () => {
  it('mounts without requests and exposes the live SQL capabilities/defaults', () => {
    const ask = vi.fn<AskClient['ask']>();
    const wrapper = makeApp(ask);
    expect(wrapper.get('h1').text()).toBe('多模态数据驱动的可解释精准问数/问答智能体');
    expect(wrapper.get(selector('submit-question')).attributes('disabled')).toBeDefined();
    expect(wrapper.get(selector('reset-task')).attributes('disabled')).toBeDefined();
    expect(wrapper.findAll('.example-list button')).toHaveLength(EXAMPLES.length);
    expect((wrapper.get(selector('max-rows')).element as HTMLInputElement).value).toBe('50');
    expect((wrapper.get(selector('top-k')).element as HTMLInputElement).value).toBe('5');
    expect(ask).not.toHaveBeenCalled();
  });
  it.each(EXAMPLES.map((text, index) => [index, text] as const))('example %i fills only (%s)', async (index, text) => {
    const ask = vi.fn<AskClient['ask']>();
    const wrapper = makeApp(ask);
    await wrapper.get(selector(`example-${index}`)).trigger('click');
    expect((wrapper.get(selector('question-input')).element as HTMLTextAreaElement).value).toBe(text);
    expect(ask).not.toHaveBeenCalled();
  });
  it('clarification example fills only and explains the unsupported boundary', async () => {
    const ask = vi.fn<AskClient['ask']>();
    const wrapper = makeApp(ask);
    await wrapper.get(selector('clarification-example')).trigger('click');
    expect((wrapper.get(selector('question-input')).element as HTMLTextAreaElement).value).toBe(CLARIFICATION_EXAMPLE);
    expect(wrapper.text()).toContain('补齐年份后仍可能 unsupported');
    expect(ask).not.toHaveBeenCalled();
  });
  it('sends form options and renders answer/returned session; follow-up uses it', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business('answered', 'session-ui'));
    const wrapper = makeApp(ask);
    await wrapper.get(selector('max-rows')).setValue('3');
    await wrapper.get(selector('top-k')).setValue('2');
    await wrapper.get(selector('show-trace')).setValue(false);
    await send(wrapper, ' 客户数量是多少？ ');
    expect(ask.mock.calls[0]?.[0]).toEqual({ question: EXAMPLES[0], profile_id: 'chinook-music', options: { max_rows: 3, top_k: 2, show_trace: false } });
    expect(wrapper.get(selector('answer')).text()).toContain('59');
    expect(wrapper.get(selector('last-request')).text()).toContain('SQL 上限 3');
    await send(wrapper, EXAMPLES[1]);
    expect(ask.mock.calls[1]?.[0].session_id).toBe('session-ui');
  });
  it('keeps loading controls disabled and suppresses duplicate form submits', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const wrapper = makeApp(ask);
    await send(wrapper);
    expect(wrapper.find(selector('loading-message')).exists()).toBe(true);
    for (const name of ['question-input', 'submit-question', 'example-0', 'reset-task']) expect(wrapper.get(selector(name)).attributes('disabled')).toBeDefined();
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined();
    expect(wrapper.get('.response-area').attributes('aria-busy')).toBe('true');
    await wrapper.get(selector('ask-form')).trigger('submit');
    expect(ask).toHaveBeenCalledTimes(1);
    pending.resolve(business());
    await flushPromises();
    expect(wrapper.find(selector('loading-message')).exists()).toBe(false);
    expect(wrapper.get(selector('submit-question')).attributes('disabled')).toBeUndefined();
  });
  it('stopping wait removes loading, aborts and does not show late business data', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const wrapper = makeApp(ask);
    await send(wrapper);
    await wrapper.get(selector('stop-waiting')).trigger('click');
    expect(ask.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(wrapper.get(selector('workspace-notice')).text()).toContain('可能仍在执行');
    expect(wrapper.find(selector('loading-message')).exists()).toBe(false);
    pending.resolve(business());
    await flushPromises();
    expect(wrapper.find(selector('ask-result')).exists()).toBe(false);
    await send(wrapper, EXAMPLES[1]);
    expect(ask.mock.calls[1]?.[0].session_id).toBeUndefined();
  });
  it('new session while loading ignores old response and sends next question without old ID', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business('answered', 'old')).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(business('answered', 'new'));
    const wrapper = makeApp(ask);
    await send(wrapper);
    await send(wrapper);
    await wrapper.get(selector('new-session')).trigger('click');
    expect((wrapper.get(selector('question-input')).element as HTMLTextAreaElement).value).toBe('');
    expect(wrapper.find(selector('last-request')).exists()).toBe(false);
    await send(wrapper, EXAMPLES[1]);
    expect(ask.mock.calls[2]?.[0].session_id).toBeUndefined();
    pending.resolve(business('answered', 'old'));
    await flushPromises();
    await send(wrapper, EXAMPLES[2]);
    expect(ask.mock.calls[3]?.[0].session_id).toBe('new');
  });
  it('candidate click continues the same recorded session and truthfully displays unsupported', async () => {
    const [first, second] = clarificationPair();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    const wrapper = makeApp(ask);
    await send(wrapper, CLARIFICATION_EXAMPLE);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(wrapper.get('.status-tag').attributes('data-status')).toBe('clarification_required');
    await wrapper.get(selector('clarification-choice-0')).trigger('click');
    await flushPromises();
    expect(ask.mock.calls[1]?.[0]).toMatchObject({ question: '2025 年', session_id: first.response.session_id });
    expect(wrapper.get('.status-tag').attributes('data-status')).toBe('unsupported');
    expect(wrapper.get('#result-heading').text()).not.toBe('已回答');
  });
  it('manually typed clarification uses the same session', async () => {
    const [first, second] = clarificationPair();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    const wrapper = makeApp(ask);
    await send(wrapper, CLARIFICATION_EXAMPLE);
    await send(wrapper, '2025 年');
    expect(ask.mock.calls[1]?.[0].session_id).toBe(first.response.session_id);
  });
  it('cancel sends the backend phrase and only acknowledgment clears the input', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business('answered', 'known')).mockReturnValueOnce(pending.promise);
    const wrapper = makeApp(ask);
    await send(wrapper);
    await wrapper.get(selector('reset-task')).trigger('click');
    expect(ask.mock.calls[1]?.[0]).toMatchObject({ question: RESET_QUESTION, session_id: 'known' });
    expect((wrapper.get(selector('question-input')).element as HTMLTextAreaElement).value).toBe(EXAMPLES[0]);
    pending.resolve(resetResponse('known'));
    await flushPromises();
    expect((wrapper.get(selector('question-input')).element as HTMLTextAreaElement).value).toBe('');
    expect(wrapper.text()).toContain('已清空当前任务');
    await send(wrapper, EXAMPLES[1]);
    expect(ask.mock.calls[2]?.[0].session_id).toBe('known');
  });
  it.each([apiError(), requestError('network'), requestError('timeout'), requestError('invalid_response')])('manual retry recovers from $kind and never auto-repeats', async (error) => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(error).mockResolvedValueOnce(business());
    const wrapper = makeApp(ask);
    await send(wrapper);
    expect(wrapper.find(selector('answer')).exists()).toBe(false);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('重试不保证无副作用');
    await wrapper.get(selector('retry-request')).trigger('click');
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(2);
    expect(wrapper.get(selector('answer')).text()).toContain('59');
  });
  it('nonretryable API errors remain distinct and require request correction', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(apiError(false, 422));
    const wrapper = makeApp(ask);
    await send(wrapper);
    expect(wrapper.get(selector('api-error')).text()).toContain('HTTP 422');
    expect(wrapper.find(selector('retry-request')).exists()).toBe(false);
    expect(wrapper.get(selector('submit-question')).attributes('disabled')).toBeUndefined();
  });
  it('invalid text/options disable submission and recover after correction', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business());
    const wrapper = makeApp(ask);
    await wrapper.get(selector('question-input')).setValue('问'.repeat(4001));
    expect(wrapper.get(selector('submit-question')).attributes('disabled')).toBeDefined();
    await wrapper.get(selector('question-input')).setValue(EXAMPLES[0]!);
    await wrapper.get(selector('max-rows')).setValue('0');
    await wrapper.get(selector('ask-form')).trigger('submit');
    expect(ask).not.toHaveBeenCalled();
    await wrapper.get(selector('max-rows')).setValue('50');
    await wrapper.get(selector('top-k')).setValue('11');
    expect(wrapper.get(selector('submit-question')).attributes('disabled')).toBeDefined();
    await wrapper.get(selector('top-k')).setValue('5');
    await wrapper.get(selector('ask-form')).trigger('submit');
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
  });
  it('Ctrl+Enter submits but Chinese input composition does not', async () => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(business());
    const wrapper = makeApp(ask);
    await wrapper.get(selector('question-input')).setValue(EXAMPLES[0]!);
    await wrapper.get(selector('question-input')).trigger('keydown', { key: 'Enter', ctrlKey: true, isComposing: true });
    expect(ask).not.toHaveBeenCalled();
    await wrapper.get(selector('question-input')).trigger('keydown', { key: 'Enter', ctrlKey: true });
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
  });
  it('unmount aborts a pending wait', async () => {
    const pending = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValue(pending.promise);
    const wrapper = makeApp(ask);
    await send(wrapper);
    wrapper.unmount();
    expect(ask.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    pending.resolve(business());
    await flushPromises();
  });
  it('connects the real 4A client/validator to the page using an explicitly stubbed transport', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      const payload = JSON.parse(init!.body as string) as AskRequest;
      const requestId = new Headers(init?.headers).get('X-Request-ID')!;
      const result = business('answered', payload.session_id ?? 'transport-test-session');
      result.response.request_id = requestId;
      return jsonResponse(result.response, 200, requestId);
    });
    const wrapper = mount(App, { props: { client: createAskClient({ fetch: fetcher }) } });
    await send(wrapper);
    // The progress stream is tried first; this stub answers plain JSON there, which the client accepts as-is.
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/ask/stream');
    expect(wrapper.get(selector('answer')).text()).toContain('59');
    await send(wrapper, EXAMPLES[1]);
    expect(JSON.parse(fetcher.mock.calls[1]![1]!.body as string)).toMatchObject({ session_id: 'transport-test-session' });
  });
});

describe('4B live stage progress rendering', () => {
  it('shows the waiting placeholder, then the backend stage trail, then clears it', async () => {
    const pending = deferred<AskClientResult>();
    const askStream = vi.fn<NonNullable<AskClient['askStream']>>().mockReturnValue(pending.promise);
    const wrapper = mount(App, { props: { client: { ask: vi.fn(), askStream } } });
    await send(wrapper);
    expect(wrapper.get(selector('stage-idle')).text()).toContain('正在连接进度流');
    expect(wrapper.find('.stage-list').exists()).toBe(false);
    askStream.mock.calls[0]?.[2]?.({ node: 'parse', label: '意图与时间解析' });
    askStream.mock.calls[0]?.[2]?.({ node: 'call_sql', label: 'SQL 生成与只读执行' });
    await flushPromises();
    const items = wrapper.findAll('.stage-list li');
    expect(items).toHaveLength(2);
    expect(items[0]!.attributes('data-state')).toBe('done');
    expect(items[1]!.attributes('data-state')).toBe('running');
    expect(items[1]!.text()).toContain('SQL 生成与只读执行');
    expect(items[1]!.text()).toContain('call_sql');
    expect(wrapper.find(selector('loading-message')).exists()).toBe(true);
    pending.resolve(business('answered', 'session-stages'));
    await flushPromises();
    expect(wrapper.find(selector('stage-progress')).exists()).toBe(false);
    expect(wrapper.find(selector('answer')).exists()).toBe(true);
  });
});
