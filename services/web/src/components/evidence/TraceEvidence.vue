<script setup lang="ts">
import type { TraceStep } from '../../contracts';
defineProps<{ steps: TraceStep[]; showTraceRequested: boolean | null }>();
</script>

<template>
  <section class="evidence-section" aria-label="执行轨迹" data-testid="trace-evidence">
    <h4>执行轨迹</h4>
    <p class="helper">仅展示后端工具步骤，不是模型思维链；步骤耗时不等于完整网络/端到端时间。</p>
    <p v-if="showTraceRequested === false" data-testid="trace-not-requested">本次发送未要求执行轨迹（show_trace=false）。</p>
    <p v-if="!steps.length" data-testid="trace-empty">本轮未返回执行轨迹；不能据此推断工具未运行。</p>
    <details v-else data-testid="trace-details">
      <summary>查看 {{ steps.length }} 条后端步骤（按返回顺序）</summary>
      <ol class="trace-list">
        <li v-for="(step, index) in steps" :key="`${step.step}-${index}`" data-testid="trace-step">
          <p>步骤 {{ step.step }} · {{ step.tool }} · 状态：{{ step.status }} · {{ step.duration_ms }} ms</p>
          <ul v-if="step.source_refs?.length" class="source-refs"><li v-for="(reference, refIndex) in step.source_refs" :key="refIndex">{{ reference }}</li></ul>
          <p v-else class="helper">未提供该步骤来源引用。</p>
        </li>
      </ol>
    </details>
  </section>
</template>
