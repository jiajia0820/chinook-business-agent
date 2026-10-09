<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
const props = defineProps<{ text: string; label: string }>();
const state = ref<'idle' | 'done' | 'error'>('idle');
let timer = 0;
async function copy() {
  try {
    await navigator.clipboard.writeText(props.text);
    state.value = 'done';
  } catch {
    state.value = 'error';
  }
  window.clearTimeout(timer);
  timer = window.setTimeout(() => { state.value = 'idle'; }, 1600);
}
onBeforeUnmount(() => window.clearTimeout(timer));
</script>

<template>
  <button type="button" class="icon-button" :data-state="state" :title="state === 'done' ? '已复制' : state === 'error' ? '复制失败，请手动选择复制' : label" :aria-label="label" @click.stop="copy">
    <svg v-if="state === 'done'" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
    <svg v-else viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
    <span v-if="state === 'error'">复制失败</span>
  </button>
</template>
