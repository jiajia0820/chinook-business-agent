<script setup lang="ts">
import type { DocumentChunk } from '../../contracts';
import { jsonText } from './format';
defineProps<{ document: DocumentChunk }>();
</script>

<template>
  <article class="evidence-card" data-testid="document-evidence" aria-label="文档证据">
    <h5>{{ document.title }}</h5>
    <p v-if="document.doc_type.toLowerCase() === 'fixture' || /^fixture:\/\//i.test(document.source_uri)" class="evidence-warning">人工测试文档来源，不是生产业务资料。</p>
    <dl class="evidence-meta">
      <div><dt>文档 ID / 类型</dt><dd>{{ document.doc_id }} · {{ document.doc_type }}</dd></div>
      <div><dt>章节</dt><dd>{{ document.section ?? '未提供章节' }}</dd></div>
      <div><dt>片段 ID</dt><dd>{{ document.chunk_id }}</dd></div>
      <div><dt>检索 ID</dt><dd>{{ document.retrieval_id ?? '未提供检索 ID' }}</dd></div>
      <div v-if="document.score != null"><dt>检索分数</dt><dd>{{ document.score }}（不是结论可信度）</dd></div>
      <div v-if="document.page != null" data-testid="document-page"><dt>页码</dt><dd>{{ document.page }}</dd></div>
      <div v-if="document.bbox != null" data-testid="document-bbox"><dt>坐标 bbox</dt><dd><pre>{{ jsonText(document.bbox) }}</pre></dd></div>
      <div><dt>来源定位</dt><dd class="source-location" data-testid="source-uri">{{ document.source_uri }}</dd></div>
    </dl>
    <p class="helper">来源定位仅为文本，不是下载/预览接口；未提供页码时不造页码。</p>
    <details data-testid="document-text"><summary>查看完整原文片段</summary><pre>{{ document.text }}</pre></details>
  </article>
</template>
