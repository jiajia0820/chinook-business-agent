<script setup lang="ts">
import type { MetricDefinition } from '../../contracts';
const props = defineProps<{ metrics: MetricDefinition[]; availableDocIds?: string[] }>();
const emit = defineEmits<{ 'focus-ref': [refId: string] }>();
// chunk_id carries the short doc code as prefix: D12@hash:L43-L49:P6:…
const chunkFor = (reference: string) => (props.availableDocIds ?? []).find((id) => id === reference || id.startsWith(`${reference}@`));
</script>

<template>
  <section v-if="metrics.length" class="evidence-section" aria-label="指标口径" data-testid="metric-definitions">
    <h4>指标口径</h4>
    <article v-for="(metric, index) in metrics" :id="`ev-metric-${metric.metric_id}`" :key="`${metric.metric_id}-${index}`" class="evidence-card">
      <h5>{{ metric.name }} · {{ metric.metric_id }}</h5>
      <p class="answer-text" data-testid="metric-definition">{{ metric.definition }}</p>
      <p>单位：{{ metric.unit }}</p>
      <p>口径来源引用</p>
      <ul v-if="metric.source_refs?.length" class="source-refs"><li v-for="(reference, refIndex) in metric.source_refs" :key="refIndex"><button v-if="chunkFor(reference)" type="button" class="cite-link" :data-testid="`metric-ref-${reference}`" :title="`跳转到文档片段 ${reference}`" @click="emit('focus-ref', `ev-doc-${chunkFor(reference)}`)">{{ reference }}</button><span v-else class="ref-missing" :title="`本轮未返回片段 ${reference}，无法跳转`">{{ reference }}</span></li></ul>
      <p v-else class="helper">未提供口径来源引用。</p>
    </article>
  </section>
  <p v-else class="evidence-empty" title="本轮未返回指标定义，不代表已确认口径。">指标口径：本轮无</p>
</template>
