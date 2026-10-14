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
    expect(wrapper.find('[data-testid="workspace-sidebar"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="workspace-main"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="workspace-evidence"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="question-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="new-session"]').exists()).toBe(true);
  });

  it('keeps the response and evidence in the same shell after an answer', async () => {
    const response = actualResponse('客户数量是多少？');
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue({ kind: 'business', httpStatus: 200, requestId: response.request_id, response });
    const wrapper = app(ask);
    await wrapper.get('[data-testid="question-input"]').setValue('客户数量是多少？');
    await wrapper.get('[data-testid="ask-form"]').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[data-testid="answer"]').text()).toContain('59');
    expect(wrapper.find('[data-testid="workspace-evidence"] [data-testid="evidence-panel"]').exists()).toBe(true);
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
