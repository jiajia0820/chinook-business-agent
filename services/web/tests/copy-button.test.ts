// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import CopyButton from '../src/components/CopyButton.vue';

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn(writeText) } });
  return navigator.clipboard.writeText as ReturnType<typeof vi.fn>;
}

describe('CopyButton', () => {
  it('copies the exact text and confirms success', async () => {
    const writeText = stubClipboard(async () => undefined);
    const wrapper = mount(CopyButton, { props: { text: '查询结果：销售额 112.86 USD', label: '复制答案' } });
    expect(wrapper.get('button').attributes('aria-label')).toBe('复制答案');
    await wrapper.get('button').trigger('click');
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('查询结果：销售额 112.86 USD'));
    await vi.waitFor(() => expect(wrapper.get('button').attributes('title')).toBe('已复制'));
    expect(wrapper.get('button').attributes('data-state')).toBe('done');
  });
  it('surfaces clipboard failure instead of pretending success', async () => {
    stubClipboard(async () => { throw new Error('denied'); });
    const wrapper = mount(CopyButton, { props: { text: 'x', label: '复制 SQL' } });
    await wrapper.get('button').trigger('click');
    await vi.waitFor(() => expect(wrapper.get('button').attributes('data-state')).toBe('error'));
    expect(wrapper.text()).toContain('复制失败');
    expect(wrapper.get('button').attributes('title')).toContain('手动选择复制');
  });
  it('does not toggle a parent details when clicked inside a summary', async () => {
    stubClipboard(async () => undefined);
    const wrapper = mount({ template: '<details open><summary><CopyButton text="t" label="l" /></summary></details>' }, { props: {}, global: { components: { CopyButton } } });
    const details = wrapper.get('details').element as HTMLDetailsElement;
    await wrapper.get('button').trigger('click');
    expect(details.open).toBe(true);
  });
});
