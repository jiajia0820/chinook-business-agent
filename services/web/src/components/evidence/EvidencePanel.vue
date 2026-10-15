<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import type { AskResponse } from '../../contracts';
import SqlEvidence from './SqlEvidence.vue';
import DocumentEvidence from './DocumentEvidence.vue';
import MetricDefinitions from './MetricDefinitions.vue';
import CalculationEvidence from './CalculationEvidence.vue';
import TraceEvidence from './TraceEvidence.vue';
import { hasFixtureSources, jsonText } from './format';
const props = defineProps<{ response: AskResponse; showTraceRequested: boolean | null }>();
const emit = defineEmits<{ 'focus-ref': [refId: string] }>();
const fixtureSources = computed(() => hasFixtureSources(props.response));
const docIds = computed(() => (props.response.documents ?? []).map((document) => document.chunk_id));
type EvidenceGroup = 'sql' | 'sources' | 'metrics' | 'trace';
const activeEvidence = ref<EvidenceGroup>(props.response.sql_results?.length ? 'sql' : props.response.documents?.length ? 'sources' : props.response.metric_definitions?.length || props.response.calculations?.length ? 'metrics' : 'trace');
const tabs = [
  { id: 'sql' as const, label: 'SQL' }, { id: 'sources' as const, label: 'Sources' },
  { id: 'metrics' as const, label: 'Metrics' }, { id: 'trace' as const, label: 'Trace' },
];
const tablist = ref<HTMLElement | null>(null);
function selectEvidence(group: EvidenceGroup) {
  activeEvidence.value = group;
}
async function moveTab(event: KeyboardEvent) {
  const current = tabs.findIndex((tab) => tab.id === activeEvidence.value);
  const next = event.key === 'ArrowRight' ? (current + 1) % tabs.length
    : event.key === 'ArrowLeft' ? (current + tabs.length - 1) % tabs.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
  if (next === null) return;
  event.preventDefault();
  selectEvidence(tabs[next]!.id);
  await nextTick();
  tablist.value?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus();
}
function reveal(refId: string) {
  selectEvidence(refId.startsWith('ev-doc-') ? 'sources' : refId.startsWith('ev-metric-') ? 'metrics' : 'sql');
}
defineExpose({ reveal });
</script>

<template>
  <section class="evidence-panel" aria-label="Evidence" data-testid="evidence-panel" :data-request-id="response.request_id">
    <div class="evidence-disclosure">
    <header class="evidence-toolbar">
      <div class="evidence-toolbar-heading"><h3>Evidence</h3><slot name="close" /></div>
      <p class="helper">查看引用原文、数据和计算记录，核对回答依据。</p>
      <nav ref="tablist" class="evidence-tabs" role="tablist" aria-label="证据分组" @keydown="moveTab">
        <button v-for="tab in tabs" :id="`evidence-tab-${tab.id}`" :key="tab.id" type="button" role="tab" :data-testid="`evidence-tab-${tab.id}`" :aria-controls="`evidence-section-${tab.id}`" :aria-selected="activeEvidence === tab.id" :tabindex="activeEvidence === tab.id ? 0 : -1" :class="{ active: activeEvidence === tab.id }" @click="selectEvidence(tab.id)">{{ tab.label }}</button>
      </nav>
    </header>
    <p v-if="fixtureSources" class="evidence-warning" role="note" data-testid="fixture-warning">检测到人工测试来源标记：仅用于验证展示/计算流程，不代表 A/C 真实资料或生产经营结论。</p>
    <details class="conditions" data-testid="parsed-conditions">
      <summary>查看本轮解析条件</summary>
      <dl class="evidence-meta">
        <div><dt>意图</dt><dd>{{ response.intent.name }}</dd></div>
        <div v-if="response.intent.confidence != null"><dt>解析置信度</dt><dd>{{ response.intent.confidence }}（后端值，不是业务结论可信度）</dd></div>
      </dl>
      <template v-if="response.time_range">
        <p>{{ response.time_range.label }} · {{ response.time_range.start }} → {{ response.time_range.end }}</p>
        <p>结束边界：{{ response.time_range.end_inclusive ? '包含结束时刻' : '不包含结束时刻' }}</p>
      </template>
      <p v-else>未返回时间范围；前端不补年份。</p>
      <pre v-if="response.entities?.length" data-testid="parsed-entities">{{ jsonText(response.entities) }}</pre>
      <p v-else>本轮未返回实体条件。</p>
    </details>
    <section v-show="activeEvidence === 'sql'" id="evidence-section-sql" class="evidence-group" role="tabpanel" aria-labelledby="evidence-tab-sql" tabindex="0">
      <section v-if="response.sql_results?.length" class="evidence-section">
        <h4>SQL 与查询结果</h4>
        <SqlEvidence v-for="(sql, index) in response.sql_results" :key="`${response.request_id}-${sql.query_id}-${index}`" :result="sql" :answer-text="response.answer" />
      </section>
      <p v-else class="evidence-empty" data-testid="sql-none" title="本轮未返回 SQL 结果，不代表业务数值为 0。">SQL 与查询结果：本轮无</p>
    </section>
    <section v-show="activeEvidence === 'sources'" id="evidence-section-sources" class="evidence-group" role="tabpanel" aria-labelledby="evidence-tab-sources" tabindex="0">
      <section v-if="response.documents?.length" class="evidence-section">
        <h4>文档片段</h4>
        <details data-testid="documents-disclosure"><summary>查看 {{ response.documents.length }} 段引用原文</summary>
        <DocumentEvidence v-for="(document, index) in response.documents" :key="`${response.request_id}-${document.chunk_id}-${index}`" :document="document" />
        </details>
      </section>
      <p v-else class="evidence-empty" data-testid="documents-none" title="本轮未返回文档片段，不代表资料不存在或已具备足够证据。">文档片段：本轮无</p>
    </section>
    <section v-show="activeEvidence === 'metrics'" id="evidence-section-metrics" class="evidence-group" role="tabpanel" aria-labelledby="evidence-tab-metrics" tabindex="0">
      <MetricDefinitions :metrics="response.metric_definitions ?? []" :available-doc-ids="docIds" @focus-ref="emit('focus-ref', $event)" />
      <CalculationEvidence :calculations="response.calculations ?? []" :fixture-sources="fixtureSources" />
    </section>
    <section v-show="activeEvidence === 'trace'" id="evidence-section-trace" class="evidence-group" role="tabpanel" aria-labelledby="evidence-tab-trace" tabindex="0">
      <TraceEvidence :steps="response.trace ?? []" :show-trace-requested="showTraceRequested" />
    </section>
    </div>
  </section>
</template>
