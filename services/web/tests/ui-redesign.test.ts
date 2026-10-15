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
    expect(wrapper.find('.example-library > .helper').exists()).toBe(false);
  });

  it('shows the default database configuration and disabled alternatives', () => {
    const wrapper = app(vi.fn());
    expect(wrapper.get('[data-testid="database-config-trigger"]').text()).toContain('Chinook + 非结构化');
    expect(wrapper.find('[data-testid="database-config-trigger"] .database-config-chevron').exists()).toBe(true);
    expect(wrapper.get('[data-testid="database-option-adventureworks"]').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[data-testid="database-option-ohr-bench"]').attributes('disabled')).toBeDefined();
  });

  it('offers categorized evaluation questions without auto-submitting', async () => {
    const ask = vi.fn<AskClient['ask']>();
    const wrapper = app(ask);
    expect(wrapper.find('[data-testid="more-questions-picker"]').exists()).toBe(true);
    expect(wrapper.findAll('[data-testid^="more-question-category-"]')).toHaveLength(12);
    await wrapper.get('[data-testid="more-questions-summary"]').trigger('click');
    expect(wrapper.find('[data-testid="more-questions-menu"]').exists()).toBe(true);
    await wrapper.get('[data-testid="more-question-category-0"]').trigger('click');
    const firstQuestion = wrapper.find('[data-testid="more-question-0-0"]');
    expect(firstQuestion.exists()).toBe(true);
    await firstQuestion.trigger('click');
    expect((wrapper.get('[data-testid="question-input"]').element as HTMLTextAreaElement).value).toContain('音频销售额');
    expect(ask).not.toHaveBeenCalled();
  });

  it('keeps the response and evidence in the same shell after an answer', async () => {
    const response = actualResponse('客户数量是多少？');
    const ask = vi.fn<AskClient['ask']>().mockResolvedValue({ kind: 'business', httpStatus: 200, requestId: response.request_id, response });
    const wrapper = app(ask);
    await wrapper.get('[data-testid="question-input"]').setValue('客户数量是多少？');
    await wrapper.get('[data-testid="ask-form"]').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[data-testid="answer"]').text()).toContain('59');
    const evidence = wrapper.find('[data-testid="workspace-evidence"] [data-testid="evidence-panel"]');
    expect(evidence.exists()).toBe(true);
    expect(evidence.attributes('aria-label')).toBe('Evidence');
    expect(wrapper.get('[data-testid="workspace-evidence"]').text()).not.toContain('回答依据与口径');
    expect(evidence.find('[data-testid="evidence-disclosure"]').exists()).toBe(false);
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
