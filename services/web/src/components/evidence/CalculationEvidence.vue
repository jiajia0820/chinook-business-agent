<script setup lang="ts">
import type { Calculation } from '../../contracts';
defineProps<{ calculations: Calculation[]; fixtureSources: boolean }>();
</script>

<template>
  <section class="evidence-section" aria-label="计算记录" data-testid="calculation-evidence">
    <h4>计算记录</h4>
    <p v-if="!calculations.length" data-testid="calculation-empty">本轮未返回计算记录；当前默认离线实例未启用真实业务计算器。</p>
    <p v-else class="helper">仅展示后端返回的公式、结果和引用；前端不执行或重新计算公式，也不自行确认业务适用性。</p>
    <p v-if="calculations.length && fixtureSources" class="evidence-warning" data-testid="calculation-fixture">人工来源计算测试，不代表真实经营结果。</p>
    <article v-for="(calculation, index) in calculations" :key="`${calculation.calculation_id}-${index}`" class="evidence-card" data-testid="calculation-record">
      <h5>{{ calculation.calculation_id }}</h5>
      <p>公式（仅文本）</p><pre data-testid="calculation-formula">{{ calculation.formula }}</pre>
      <p class="calculation-value" data-testid="calculation-value">后端返回结果：{{ calculation.result }} · 单位：{{ calculation.unit }}</p>
      <p>输入/来源引用（不是前端计算参数）</p>
      <ul v-if="calculation.inputs?.length" class="source-refs"><li v-for="(reference, refIndex) in calculation.inputs" :key="refIndex">{{ reference }}</li></ul>
      <p v-else class="helper">未提供输入引用。</p>
    </article>
  </section>
</template>
