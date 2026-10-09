// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { enableAutoUnmount, mount } from '@vue/test-utils';
import AskResult from '../src/components/AskResult.vue';
import type { AskResponse } from '../src/contracts';
import { recordedBusiness } from './recorded-http';
import { actualResponse } from './evidence-fixtures';
import { apiError, business, requestError } from './workspace-fixtures';

enableAutoUnmount(afterEach);
describe('retained 4B result branches alongside 4C evidence', () => {
  it.each(recordedBusiness.map((item, index) => [index, item.case, item.body as AskResponse] as const))('renders recorded response %i (%s) without altering its status', (_index, _case, response) => {
    const wrapper = mount(AskResult, { props: { result: { kind: 'business', response, requestId: response.request_id, httpStatus: 200 } } });
    expect(wrapper.get('.status-tag').attributes('data-status')).toBe(response.status);
    expect(wrapper.text()).toContain(response.route);
    expect(wrapper.text()).toContain(response.request_id);
    if (response.answer !== null) expect(wrapper.get('[data-testid="answer"]').text()).toBe(response.answer.trim());
    expect(wrapper.find('form').exists()).toBe(false);
    expect(wrapper.find('a').exists()).toBe(false);
    if (response.status !== 'answered') expect(wrapper.get('h2').text()).not.toBe('已回答');
  });
  it.each([422, 500, 503])('renders HTTP %i as API error, not a business answer', (httpStatus) => {
    const wrapper = mount(AskResult, { props: { result: apiError(true, httpStatus) } });
    expect(wrapper.get('h2').text()).toBe('API 请求出错');
    expect(wrapper.get('[data-testid="api-error"]').text()).toContain(`HTTP ${httpStatus}`);
    expect(wrapper.find('.status-tag').exists()).toBe(false);
    expect(wrapper.text()).toContain('req-test-api');
    expect(wrapper.text()).toContain('手动重试');
  });
  it.each(['invalid_request', 'network', 'timeout', 'cancelled', 'invalid_response'] as const)('renders %s separately', (reason) => {
    const wrapper = mount(AskResult, { props: { result: requestError(reason) } });
    expect(wrapper.get('h2').text()).toBe('未获得可用业务响应');
    expect(wrapper.get('[data-testid="request-error"]').text()).toContain(reason);
    expect(wrapper.find('[data-testid="answer"]').exists()).toBe(false);
  });
  it('shows optional HTTP status on a request failure', () => {
    const wrapper = mount(AskResult, { props: { result: { ...requestError('invalid_response'), httpStatus: 502 } } });
    expect(wrapper.text()).toContain('HTTP 502');
  });
  it('shows the full clarification and emits only the chosen text', async () => {
    const result = business('clarification_required');
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.get('[data-testid="clarification-question"]').text()).toBe(result.response.clarification!.question);
    for (const slot of result.response.clarification!.missing_slots ?? []) expect(wrapper.text()).toContain(`${slot.name}：${slot.description}`);
    expect(wrapper.emitted('clarification')).toBeUndefined();
    await wrapper.get('[data-testid="clarification-choice-0"]').trigger('click');
    expect(wrapper.emitted('clarification')).toEqual([[result.response.clarification!.options![0]]]);
  });
  it('disables clarification choices during the next request', async () => {
    const wrapper = mount(AskResult, { props: { result: business('clarification_required'), choicesDisabled: true } });
    expect(wrapper.get('[data-testid="clarification-choice-0"]').attributes('disabled')).toBeDefined();
    await wrapper.get('[data-testid="clarification-choice-0"]').trigger('click');
    expect(wrapper.emitted('clarification')).toBeUndefined();
  });
  it('accepts omitted optional arrays and a null answer without inventing numbers', () => {
    const result = business('insufficient_evidence');
    result.response.answer = null;
    delete result.response.limitations;
    delete result.response.documents;
    delete result.response.calculations;
    delete result.response.sql_results;
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.find('[data-testid="answer"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('证据不足');
    expect(wrapper.text()).not.toContain('0.00');
  });
  it('accepts empty clarification arrays', () => {
    const result = business('clarification_required');
    result.response.clarification!.missing_slots = [];
    result.response.clarification!.options = [];
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.find('.choice-list').exists()).toBe(false);
    expect(wrapper.text()).toContain(result.response.clarification!.question);
  });
  it('renders long text and markup only as text, with no scripts/images/download links', () => {
    const result = business();
    result.response.answer = '<img src=x onerror=alert(1)>\n<script>alert(2)</script>' + '长'.repeat(10_000);
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.get('[data-testid="answer"]').text()).toContain(result.response.answer);
    expect(wrapper.findAll('img, script, a')).toHaveLength(0);
    expect(wrapper.html()).toContain('&lt;img');
  });
  it('renders API messages safely but never displays details/stack content', () => {
    const result = apiError(false);
    result.error.message = '<svg onload=alert(1)>错误</svg>';
    result.error.details = { stack: 'PRIVATE_TEST_DETAIL' };
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.text()).toContain(result.error.message);
    expect(wrapper.find('svg').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('PRIVATE_TEST_DETAIL');
  });
});

describe('answer-to-evidence citation linking', () => {
  function answeredWithSql() {
    const response = actualResponse('客户数量是多少？');
    const queryId = response.sql_results![0]!.query_id;
    response.answer = `客户数量为 59。来源：SQL ${queryId}。`;
    return { result: { kind: 'business' as const, response, requestId: response.request_id, httpStatus: 200 as const }, queryId };
  }
  it('turns evidence IDs inside the answer into clickable refs without altering text', () => {
    const { result, queryId } = answeredWithSql();
    const wrapper = mount(AskResult, { props: { result } });
    const refs = wrapper.findAll('.cite-link');
    expect(refs).toHaveLength(1);
    expect(refs[0]!.text()).toBe(queryId);
    expect(wrapper.get('[data-testid="answer"]').text()).toBe(result.response.answer);
  });
  it('lists SQL/document/metric citations and focuses the target card on click', async () => {
    const { result, queryId } = answeredWithSql();
    const wrapper = mount(AskResult, { props: { result }, attachTo: document.body });
    const chips = wrapper.findAll('[data-testid="citation-bar"] .citation-chip');
    expect(chips.length).toBeGreaterThanOrEqual(2);
    expect(chips[0]!.text()).toContain(queryId);
    expect(chips.some((chip) => chip.attributes('data-kind') === 'metric')).toBe(true);
    const card = document.getElementById(`ev-sql-${queryId}`);
    expect(card).not.toBeNull();
    const details = card!.querySelector('details[data-testid="sql-query"]') as HTMLDetailsElement;
    details.open = false;
    await chips[0]!.trigger('click');
    expect(card!.classList.contains('evidence-highlight')).toBe(true);
    expect(details.open).toBe(true);
  });
  it('clicking an inline answer ref highlights the same SQL card', async () => {
    const { result, queryId } = answeredWithSql();
    const wrapper = mount(AskResult, { props: { result }, attachTo: document.body });
    await wrapper.get('.cite-link').trigger('click');
    expect(document.getElementById(`ev-sql-${queryId}`)!.classList.contains('evidence-highlight')).toBe(true);
  });
  it('aligns oversized evidence cards to their head and centers short ones', async () => {
    const { result, queryId } = answeredWithSql();
    const wrapper = mount(AskResult, { props: { result }, attachTo: document.body });
    const card = document.getElementById(`ev-sql-${queryId}`)!;
    const realRect = card.getBoundingClientRect.bind(card);
    const realScroll = Element.prototype.scrollIntoView;
    const alignments: Array<string | undefined> = [];
    Element.prototype.scrollIntoView = function (options?: boolean | ScrollIntoViewOptions) {
      alignments.push(typeof options === 'object' ? options?.block : undefined);
    };
    try {
      card.getBoundingClientRect = () => ({ height: window.innerHeight * 2 } as DOMRect);
      await wrapper.get('.cite-link').trigger('click');
      card.getBoundingClientRect = () => ({ height: 120 } as DOMRect);
      await wrapper.get('.cite-link').trigger('click');
      expect(alignments).toEqual(['start', 'center']);
    } finally {
      Element.prototype.scrollIntoView = realScroll;
      card.getBoundingClientRect = realRect;
    }
  });
  it('renders no citation bar when the response carries no evidence', () => {
    const result = business('insufficient_evidence');
    delete result.response.sql_results;
    delete result.response.documents;
    delete result.response.metric_definitions;
    const wrapper = mount(AskResult, { props: { result } });
    expect(wrapper.find('[data-testid="citation-bar"]').exists()).toBe(false);
    expect(wrapper.findAll('.cite-link')).toHaveLength(0);
  });
});
