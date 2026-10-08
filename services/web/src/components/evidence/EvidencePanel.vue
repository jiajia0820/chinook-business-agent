<script setup lang="ts">
import { computed } from 'vue';
import type { AskResponse } from '../../contracts';
import SqlEvidence from './SqlEvidence.vue';
import DocumentEvidence from './DocumentEvidence.vue';
import MetricDefinitions from './MetricDefinitions.vue';
import CalculationEvidence from './CalculationEvidence.vue';
import TraceEvidence from './TraceEvidence.vue';
import { hasFixtureSources, jsonText } from './format';
const props = defineProps<{ response: AskResponse; showTraceRequested: boolean | null }>();
const fixtureSources = computed(() => hasFixtureSources(props.response));
</script>

<template>
  <section class="evidence-panel" aria-label="本轮证据与口径" data-testid="evidence-panel" :data-request-id="response.request_id">
    <h3>本轮证据与口径</h3>
    <p class="helper">仅展示本轮后端返回的资料；存在片段不等于结论成立，没有人工标记也不构成生产真实性认证。</p>
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
    <section class="evidence-section" aria-label="SQL 与查询结果">
      <h4>SQL 与查询结果</h4>
      <p v-if="!response.sql_results?.length" data-testid="sql-none">本轮未返回 SQL 结果，不代表业务数值为 0。</p>
      <SqlEvidence v-for="(sql, index) in response.sql_results" :key="`${response.request_id}-${sql.query_id}-${index}`" :result="sql" />
    </section>
    <section class="evidence-section" aria-label="文档片段">
      <h4>文档片段</h4>
      <p v-if="!response.documents?.length" data-testid="documents-none">本轮未返回文档片段，不代表资料不存在或已具备足够证据。</p>
      <DocumentEvidence v-for="(document, index) in response.documents" :key="`${response.request_id}-${document.chunk_id}-${index}`" :document="document" />
    </section>
    <MetricDefinitions :metrics="response.metric_definitions ?? []" />
    <CalculationEvidence :calculations="response.calculations ?? []" :fixture-sources="fixtureSources" />
    <TraceEvidence :steps="response.trace ?? []" :show-trace-requested="showTraceRequested" />
  </section>
</template>
