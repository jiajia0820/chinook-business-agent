// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils';
import App from '../src/App.vue';
import type { AskResponse } from '../src/contracts';
import { createAskClient, type AskClient, type AskClientResult } from '../src/api/client';
import { deferred, jsonResponse } from './recorded-http';
import { actualResponse, artificialCalculation } from './evidence-fixtures';
import { apiError, business, requestError } from './workspace-fixtures';

enableAutoUnmount(afterEach);
function responseResult(response: AskResponse): AskClientResult { return { kind: 'business', response, httpStatus: 200, requestId: response.request_id }; }
const app = (ask: AskClient['ask']) => mount(App, { props: { client: { ask } } });
type AppWrapper = ReturnType<typeof app>;
async function send(wrapper: AppWrapper, text: string) {
  await wrapper.get('[data-testid="question-input"]').setValue(text);
  await wrapper.get('[data-testid="ask-form"]').trigger('submit');
  await flushPromises();
}

describe('4C evidence integration, using explicit test transport only', () => {
  it('clears old SQL at send and shows only the new D12 document after reply', async () => {
    const pending = deferred<AskClientResult>();
    const first = actualResponse('客户数量是多少？');
    const second = actualResponse('销售额口径是什么？');
    second.session_id = first.session_id;
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(responseResult(first)).mockReturnValueOnce(pending.promise);
    const wrapper = app(ask);
    await send(wrapper, '客户数量是多少？');
    expect(wrapper.findAll('[data-testid="sql-evidence"]')).toHaveLength(1);
    const oldDetails = wrapper.get('[data-testid="sql-query"]').element as HTMLDetailsElement;
    oldDetails.open = true;
    await send(wrapper, '销售额口径是什么？');
    expect(wrapper.find('[data-testid="evidence-panel"]').exists()).toBe(false);
    pending.resolve(responseResult(second));
    await flushPromises();
    expect(wrapper.findAll('[data-testid="document-evidence"]')).toHaveLength(1);
    expect(wrapper.find('[data-testid="sql-evidence"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain(first.sql_results![0]!.query_id);
    expect(wrapper.get('[data-testid="evidence-panel"]').attributes('data-request-id')).toBe(second.request_id);
  });
  it.each([apiError(), requestError('timeout')])('does not attach old SQL/documents to a later $kind', async (error) => {
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(business()).mockResolvedValueOnce(error);
    const wrapper = app(ask);
    await send(wrapper, '客户数量是多少？');
    expect(wrapper.find('[data-testid="evidence-panel"]').exists()).toBe(true);
    await send(wrapper, '销售额口径是什么？');
    expect(wrapper.find('[data-testid="evidence-panel"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="sql-evidence"], [data-testid="document-evidence"]')).toHaveLength(0);
  });
  it('stopped old SQL cannot replace a later document or restore old request identity', async () => {
    const pending = deferred<AskClientResult>();
    const first = actualResponse('客户数量是多少？');
    const second = actualResponse('销售额口径是什么？');
    const ask = vi.fn<AskClient['ask']>().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(responseResult(second));
    const wrapper = app(ask);
    await send(wrapper, '客户数量是多少？');
    await wrapper.get('[data-testid="stop-waiting"]').trigger('click');
    await send(wrapper, '销售额口径是什么？');
    pending.resolve(responseResult(first));
    await flushPromises();
    expect(wrapper.get('[data-testid="evidence-panel"]').attributes('data-request-id')).toBe(second.request_id);
    expect(wrapper.find('[data-testid="sql-evidence"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="document-evidence"]').exists()).toBe(true);
  });
  it('new session clears calculation/source warnings and ignores late evidence', async () => {
    const pending = deferred<AskClientResult>();
    const artificial = artificialCalculation();
    const ask = vi.fn<AskClient['ask']>().mockResolvedValueOnce(responseResult(artificial)).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(business());
    const wrapper = app(ask);
    await send(wrapper, '测试人工计算展示');
    expect(wrapper.find('[data-testid="calculation-record"]').exists()).toBe(true);
    await send(wrapper, '等待中的测试问题');
    await wrapper.get('[data-testid="new-session"]').trigger('click');
    expect(wrapper.find('[data-testid="evidence-panel"]').exists()).toBe(false);
    await send(wrapper, '客户数量是多少？');
    expect(ask.mock.calls[2]?.[0].session_id).toBeUndefined();
    pending.resolve(responseResult(artificial));
    await flushPromises();
    expect(wrapper.find('[data-testid="calculation-record"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="fixture-warning"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="sql-evidence"]').exists()).toBe(true);
  });
  it('show_trace label uses sent options, not the checkbox edited after sending', async () => {
    const response = actualResponse('客户数量是多少？');
    response.trace = [];
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(responseResult(response));
    const wrapper = app(ask);
    await wrapper.get('[data-testid="show-trace"]').setValue(false);
    await send(wrapper, '客户数量是多少？');
    expect(wrapper.find('[data-testid="trace-not-requested"]').exists()).toBe(true);
    await wrapper.get('[data-testid="show-trace"]').setValue(true);
    expect(wrapper.find('[data-testid="trace-not-requested"]').exists()).toBe(true);
    await send(wrapper, '客户数量是多少？');
    expect(wrapper.find('[data-testid="trace-not-requested"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="trace-step"]').exists()).toBe(false);
  });
  it('error status can still show partial evidence without being promoted to answered', async () => {
    const error = business('error');
    error.response.documents = actualResponse('销售额口径是什么？').documents!;
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue(error);
    const wrapper = app(ask);
    await send(wrapper, '测试部分证据');
    expect(wrapper.get('.status-tag').attributes('data-status')).toBe('error');
    expect(wrapper.find('[data-testid="document-evidence"]').exists()).toBe(true);
    expect(wrapper.get('#result-heading').text()).toBe('业务处理出错');
  });
  it('real client/validator accepts actual response evidence through explicitly stubbed fetch', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      const requestId = new Headers(init!.headers).get('X-Request-ID')!;
      const response = actualResponse('默认音频范围是什么？');
      response.request_id = requestId;
      return jsonResponse(response, 200, requestId);
    });
    const wrapper = mount(App, { props: { client: createAskClient({ fetch: fetcher }) } });
    await send(wrapper, '默认音频范围是什么？');
    expect(wrapper.find('[data-testid="document-evidence"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="calculation-record"]').exists()).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
    // The progress stream is tried first; this stub answers plain JSON there, which the client accepts as-is.
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/ask/stream');
    expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toEqual({ question: '默认音频范围是什么？', profile_id: 'chinook-music', options: { show_trace: true, max_rows: 50, top_k: 5 } });
  });
});
