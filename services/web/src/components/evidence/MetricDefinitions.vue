<script setup lang="ts">
import type { MetricDefinition } from '../../contracts';
defineProps<{ metrics: MetricDefinition[] }>();
</script>

<template>
  <section v-if="metrics.length" class="evidence-section" aria-label="指标口径" data-testid="metric-definitions">
    <h4>指标口径</h4>
    <article v-for="(metric, index) in metrics" :id="`ev-metric-${metric.metric_id}`" :key="`${metric.metric_id}-${index}`" class="evidence-card">
      <h5>{{ metric.name }} · {{ metric.metric_id }}</h5>
      <p class="answer-text" data-testid="metric-definition">{{ metric.definition }}</p>
      <p>单位：{{ metric.unit }}</p>
      <p>口径来源引用</p>
      <ul v-if="metric.source_refs?.length" class="source-refs"><li v-for="(reference, refIndex) in metric.source_refs" :key="refIndex">{{ reference }}</li></ul>
      <p v-else class="helper">未提供口径来源引用。</p>
    </article>
  </section>
  <p v-else class="evidence-empty" title="本轮未返回指标定义，不代表已确认口径。">指标口径：本轮无</p>
</template>
