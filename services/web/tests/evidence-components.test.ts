// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { enableAutoUnmount, mount } from '@vue/test-utils';
import SqlEvidence from '../src/components/evidence/SqlEvidence.vue';
import DocumentEvidence from '../src/components/evidence/DocumentEvidence.vue';
import MetricDefinitions from '../src/components/evidence/MetricDefinitions.vue';
import CalculationEvidence from '../src/components/evidence/CalculationEvidence.vue';
import TraceEvidence from '../src/components/evidence/TraceEvidence.vue';
import EvidencePanel from '../src/components/evidence/EvidencePanel.vue';
import AskResult from '../src/components/AskResult.vue';
import { copySample, recordedBusiness } from './recorded-http';
import { actualResponse, artificialCalculation, documentChunk, recordedCalculations, sqlResult } from './evidence-fixtures';
import type { AskResponse, TraceStep } from '../src/contracts';

enableAutoUnmount(afterEach);
const panel = (response: AskResponse) => mount(EvidencePanel, { props: { response, showTraceRequested: null } });

describe('SQL evidence and dynamic rows', () => {
  it.each(['客户数量是多少？', '美国客户数量是多少？', '有哪些音乐类型？'])('preserves actual recorded query: %s', (question) => {
    const result = actualResponse(question).sql_results![0]!;
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.text()).toContain(result.query_id);
    expect(wrapper.text()).toContain(result.profile_id);
    expect(wrapper.text()).toContain(result.source.name);
    expect(wrapper.text()).toContain(`row_count=${result.row_count}`);
    expect(wrapper.findAll('thead th').map((cell) => cell.text())).toEqual(result.columns);
    expect(wrapper.findAll('tbody tr')).toHaveLength(result.rows!.length);
    expect(wrapper.get('[data-testid="candidate-sql"]').text()).toBe(result.sql);
    expect(wrapper.get('[data-testid="sql-legend"]').text()).toContain('关键字');
    expect(wrapper.findAll('[data-testid="sql-legend"] .chip')).toHaveLength(3);
    expect(wrapper.text()).toContain('不是逐字驱动 SQL');
    // The copy button is the only interactive element allowed inside an evidence card.
    expect(wrapper.findAll('input, textarea, a')).toHaveLength(0);
    expect(wrapper.findAll('button').map((button) => button.attributes('aria-label'))).toEqual(['复制 SQL']);
    expect(wrapper.findAll('.cell-origin')).toHaveLength(0);
  });
  it('marks the cells the answer quotes as raw origin', () => {
    const response = actualResponse('客户数量是多少？');
    const result = response.sql_results![0]!;
    const wrapper = mount(SqlEvidence, { props: { result, answerText: response.answer } });
    const marked = wrapper.findAll('tbody td.cell-origin');
    expect(marked.length).toBeGreaterThan(0);
    for (const cell of marked) expect(response.answer).toContain(cell.text());
    expect(wrapper.get('caption').text()).toContain('原始出处');
  });
  it('shows Genre limit as a returned subset, never total number of genres', () => {
    const result = actualResponse('有哪些音乐类型？', 1).sql_results![0]!;
    expect(result.truncated).toBe(true);
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.get('[data-testid="sql-truncated"]').text()).toContain('不代表完整总量');
    expect(wrapper.findAll('tbody tr')).toHaveLength(result.rows!.length);
    expect(wrapper.text()).not.toContain('总共 3 类');
  });
  it('distinguishes 0/false/null/empty/missing and nested objects', () => {
    const result = sqlResult();
    result.columns = ['zero', 'bool', 'nil', 'empty', 'absent', 'json'];
    result.rows = [{ zero: 0, bool: false, nil: null, empty: '', json: { x: [0, false, null] } }];
    const wrapper = mount(SqlEvidence, { props: { result } });
    const cells = wrapper.findAll('tbody td').map((cell) => cell.text());
    expect(cells.slice(0, 5)).toEqual(['0', 'false', 'null', '（空字符串）', '（缺失字段）']);
    expect(JSON.parse(cells[5]!)).toEqual({ x: [0, false, null] });
  });
  it('preserves duplicate column order, count mismatch and undeclared fields visibly', () => {
    const result = sqlResult();
    result.columns = ['a', 'a'];
    result.rows = [{ a: false, extra: 'unexpected' }];
    result.row_count = 9;
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.findAll('thead th').map((cell) => cell.text())).toEqual(['a', 'a']);
    expect(wrapper.get('[data-testid="sql-shape-notes"]').text()).toContain('重复');
    expect(wrapper.get('[data-testid="sql-shape-notes"]').text()).toContain('row_count=9');
    expect(wrapper.get('[data-testid="sql-raw-rows"]').text()).toContain('unexpected');
  });
  it('returns success empty rows without inventing an aggregate zero', () => {
    const result = sqlResult();
    result.rows = [];
    result.row_count = 0;
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.get('[data-testid="sql-empty"]').text()).toContain('不是经营指标值为 0');
    expect(wrapper.find('table').exists()).toBe(false);
  });
  it.each(['failed', 'rejected'] as const)('displays SQL %s independently of row count', (status) => {
    const result = sqlResult();
    result.status = status;
    result.rows = [];
    result.row_count = 0;
    result.error = { code: 'TEST_SQL_ERROR', message: '<img src=x>错误', retryable: false, details: { stack: 'PRIVATE_TEST_DETAIL' } };
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.get('[data-testid="sql-error"]').text()).toContain('TEST_SQL_ERROR');
    expect(wrapper.find('[data-testid="sql-empty"]').exists()).toBe(false);
    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('PRIVATE_TEST_DETAIL');
  });
  it.each(['omitted', 'null', 'empty'])('does not generate SQL/params when %s', (state) => {
    const result = sqlResult();
    if (state === 'omitted') { delete result.sql; delete result.params; }
    if (state === 'null') { result.sql = null; result.params = null; }
    if (state === 'empty') { result.sql = ''; result.params = {}; }
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.find('[data-testid="candidate-sql"]').exists()).toBe(false);
    expect(wrapper.text()).toContain(state === 'empty' ? '无参数（{}）' : '未提供参数');
  });
  it('shows nested params separately, without interpolating them into SQL', () => {
    const result = sqlResult();
    result.params = { filter: { codes: [1, null], active: false }, country: "US'; DROP TABLE x;--" };
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(JSON.parse(wrapper.get('[data-testid="sql-params"]').text())).toEqual(result.params);
    expect(wrapper.get('[data-testid="candidate-sql"]').text()).toBe(result.sql);
  });
  it('shows raw rows when columns are absent instead of guessing column names', () => {
    const result = sqlResult();
    delete result.columns;
    const wrapper = mount(SqlEvidence, { props: { result } });
    expect(wrapper.find('table').exists()).toBe(false);
    expect(wrapper.get('[data-testid="sql-raw-rows"]').text()).toContain('customer_count');
  });
});

describe('D12 documents, metric definitions and safe locators', () => {
  it.each(['销售额口径是什么？', '默认音频范围是什么？', '购买客户数是什么意思？'])('preserves actual D12 text/locator: %s', (question) => {
    const document = actualResponse(question).documents![0]!;
    const wrapper = mount(DocumentEvidence, { props: { document } });
    for (const text of [document.doc_id, document.title, document.chunk_id, document.retrieval_id!, document.section!, document.source_uri]) expect(wrapper.text()).toContain(text);
    expect(wrapper.get('[data-testid="document-text"] pre').text()).toBe(document.text);
    expect(wrapper.find('[data-testid="document-page"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="document-bbox"]').exists()).toBe(false);
    expect(wrapper.findAll('a, iframe, img')).toHaveLength(0);
    expect(wrapper.text()).toContain('不是结论可信度');
  });
  it('retains provided page/bbox and a zero retrieval score', () => {
    const document = documentChunk();
    document.page = 1;
    document.score = 0;
    document.bbox = [0, 0, 12.5, 32];
    const wrapper = mount(DocumentEvidence, { props: { document } });
    expect(wrapper.get('[data-testid="document-page"]').text()).toContain('1');
    expect(JSON.parse(wrapper.get('[data-testid="document-bbox"] pre').text())).toEqual(document.bbox);
    expect(wrapper.text()).toContain('0（不是结论可信度）');
  });
  it.each(['file:///private/test', 'javascript:alert(1)', 'https://example.invalid/x', 'fixture://test/policy'])('locator %s stays selectable text, never an active URL', (sourceUri) => {
    const document = documentChunk();
    document.source_uri = sourceUri;
    const wrapper = mount(DocumentEvidence, { props: { document } });
    expect(wrapper.get('[data-testid="source-uri"]').text()).toBe(sourceUri);
    expect(wrapper.find('a').exists()).toBe(false);
  });
  it('uses fallback labels for optional locator metadata without made-up values', () => {
    const document = documentChunk();
    delete document.page; delete document.bbox; delete document.section; delete document.retrieval_id; delete document.score;
    const wrapper = mount(DocumentEvidence, { props: { document } });
    expect(wrapper.text()).toContain('未提供章节');
    expect(wrapper.text()).toContain('未提供检索 ID');
    expect(wrapper.find('[data-testid="document-page"]').exists()).toBe(false);
  });
  it('preserves long document markup as text', () => {
    const document = documentChunk();
    document.title = '<svg onload=alert(1)>标题</svg>';
    document.text = '<script>alert(1)</script>\n' + '全文'.repeat(7000);
    const wrapper = mount(DocumentEvidence, { props: { document } });
    expect(wrapper.get('[data-testid="document-text"] pre').text()).toBe(document.text);
    expect(wrapper.findAll('script, svg')).toHaveLength(0);
  });
  it('retains the actual provisional Customer record definition and all source refs', () => {
    const metrics = copySample().metric_definitions!;
    const wrapper = mount(MetricDefinitions, { props: { metrics } });
    expect(wrapper.get('[data-testid="metric-definition"]').text()).toBe(metrics[0]!.definition);
    expect(wrapper.text()).toContain('不等于购买客户数');
    expect(wrapper.text()).toContain(metrics[0]!.unit);
    for (const ref of metrics[0]!.source_refs!) expect(wrapper.text()).toContain(ref);
  });
  it('keeps omitted/empty metric references and HTML definitions safe', () => {
    const metrics = copySample().metric_definitions!;
    metrics[0]!.definition = '<a href="javascript:x">定义</a>';
    delete metrics[0]!.source_refs;
    const wrapper = mount(MetricDefinitions, { props: { metrics } });
    expect(wrapper.text()).toContain(metrics[0]!.definition);
    expect(wrapper.text()).toContain('未提供口径来源引用');
    expect(wrapper.find('a').exists()).toBe(false);
  });
});

describe('calculation/trace display and current response identity', () => {
  it.each(recordedCalculations.map((response, index) => [index, response] as const))('renders recorded artificial calculation %i with explicit source warnings', (_index, response) => {
    const wrapper = panel(response);
    expect(wrapper.get('[data-testid="fixture-warning"]').text()).toContain('不代表 A/C 真实资料');
    expect(wrapper.get('[data-testid="calculation-fixture"]').text()).toContain('不代表真实经营结果');
    for (const calculation of response.calculations!) {
      expect(wrapper.text()).toContain(calculation.formula);
      expect(wrapper.text()).toContain(String(calculation.result));
      for (const input of calculation.inputs!) expect(wrapper.text()).toContain(input);
    }
  });
  it('does not execute formula expressions or round a provided result', () => {
    const calculations = [{ calculation_id: 'synthetic', formula: 'globalThis.EXPRESSION_EXECUTED = true', inputs: [], result: 1.23456789, unit: 'test' }];
    const wrapper = mount(CalculationEvidence, { props: { calculations, fixtureSources: false } });
    expect(wrapper.get('[data-testid="calculation-formula"]').text()).toBe(calculations[0]!.formula);
    expect(wrapper.get('[data-testid="calculation-value"]').text()).toContain('1.23456789');
    expect(Object.hasOwn(globalThis, 'EXPRESSION_EXECUTED')).toBe(false);
  });
  it('shows a legitimate zero calculation but never invents zero for empty calculations', () => {
    const wrapper = mount(CalculationEvidence, { props: { calculations: [{ calculation_id: 'test', formula: 'test', result: 0, unit: '%' }], fixtureSources: false } });
    expect(wrapper.get('[data-testid="calculation-value"]').text()).toContain('0 · 单位：%');
    const empty = mount(CalculationEvidence, { props: { calculations: [], fixtureSources: false } });
    expect(empty.get('[data-testid="calculation-empty"]').text()).toBe('计算记录：本轮无');
    expect(empty.get('[data-testid="calculation-empty"]').attributes('title')).toContain('未启用真实业务计算器');
    expect(empty.find('[data-testid="calculation-value"]').exists()).toBe(false);
  });
  it('keeps backend trace order/step numbers/source references instead of sorting or summing', () => {
    const steps: TraceStep[] = [{ step: 5, tool: 'rag.retrieve', status: 'failed', duration_ms: 0, source_refs: ['doc:test'] }, { step: 2, tool: 'sql.task', status: 'success', duration_ms: 1.5 }];
    const wrapper = mount(TraceEvidence, { props: { steps, showTraceRequested: true } });
    const items = wrapper.findAll('[data-testid="trace-step"]');
    expect(items[0]!.text()).toContain('步骤 5');
    expect(items[1]!.text()).toContain('步骤 2');
    expect(wrapper.text()).toContain('doc:test');
    expect(wrapper.text()).toContain('1.5 ms');
    expect(wrapper.text()).toContain('不是模型思维链');
    expect(wrapper.text()).not.toContain('总耗时');
  });
  it.each([false, true, null])('empty trace with requested=%s does not invent tools or timings', (requested) => {
    const wrapper = mount(TraceEvidence, { props: { steps: [], showTraceRequested: requested } });
    expect(wrapper.find('[data-testid="trace-not-requested"]').exists()).toBe(requested === false);
    expect(wrapper.get('[data-testid="trace-empty"]').text()).toContain('执行轨迹：本轮无');
    expect(wrapper.get('[data-testid="trace-empty"]').attributes('title')).toContain('不能据此推断工具未运行');
    expect(wrapper.find('[data-testid="trace-step"]').exists()).toBe(false);
  });
  it('shows actual parsed time/entities but does not fill an absent year', () => {
    const artificial = artificialCalculation();
    const wrapper = panel(artificial);
    expect(wrapper.get('[data-testid="parsed-conditions"]').text()).toContain(artificial.time_range!.label);
    expect(wrapper.text()).toContain('不包含结束时刻');
    expect(JSON.parse(wrapper.get('[data-testid="parsed-entities"]').text())).toEqual(artificial.entities);
    const real = panel(copySample());
    expect(real.get('[data-testid="parsed-conditions"]').text()).toContain('前端不补年份');
  });
  it('handles all absent optional evidence arrays and keeps empty states precise', () => {
    const response = copySample();
    delete response.sql_results; delete response.documents; delete response.calculations; delete response.metric_definitions; delete response.trace; delete response.entities;
    const wrapper = panel(response);
    expect(wrapper.findAll('[data-testid="sql-evidence"], [data-testid="document-evidence"], [data-testid="calculation-record"], [data-testid="trace-step"]')).toHaveLength(0);
    expect(wrapper.text()).toContain('文档片段：本轮无');
    expect(wrapper.text()).toContain('指标口径：本轮无');
    expect(wrapper.findAll('h4')).toHaveLength(0);
    expect(wrapper.find('[data-testid="fixture-warning"]').exists()).toBe(false);
  });
  it.each(recordedBusiness.map((record, index) => [index, record.body as AskResponse] as const))('all five status branches retain available evidence in response %i', (_index, response) => {
    const wrapper = panel(response);
    expect(wrapper.findAll('[data-testid="sql-evidence"]')).toHaveLength(response.sql_results?.length ?? 0);
    expect(wrapper.findAll('[data-testid="document-evidence"]')).toHaveLength(response.documents?.length ?? 0);
    expect(wrapper.findAll('[data-testid="calculation-record"]')).toHaveLength(response.calculations?.length ?? 0);
    expect(wrapper.get('[data-testid="evidence-panel"]').attributes('data-request-id')).toBe(response.request_id);
  });
  it('new request identity resets expansion state to the default open SQL, even for repeated evidence IDs', async () => {
    const first = copySample();
    const result = { kind: 'business' as const, httpStatus: 200 as const, requestId: first.request_id, response: first };
    const wrapper = mount(AskResult, { props: { result } });
    const oldDetails = wrapper.get('[data-testid="sql-query"]').element as HTMLDetailsElement;
    expect(oldDetails.open).toBe(true);
    oldDetails.open = false;
    const second = structuredClone(first);
    second.request_id = 'req-synthetic-next';
    await wrapper.setProps({ result: { ...result, requestId: second.request_id, response: second } });
    const nextDetails = wrapper.get('[data-testid="sql-query"]').element as HTMLDetailsElement;
    expect(nextDetails).not.toBe(oldDetails);
    expect(nextDetails.open).toBe(true);
  });
});
