<script setup lang="ts">
import { computed } from 'vue';
import type { AskClientResult } from '../api/client';
import type { AskOptions, AskStatus, Route } from '../contracts';
import CopyButton from './CopyButton.vue';
import EvidencePanel from './evidence/EvidencePanel.vue';
import { answerSegments, citationTargets, splitAnswer } from './evidence/format';

const props = withDefaults(defineProps<{ result: AskClientResult; choicesDisabled?: boolean; requestOptions?: AskOptions | null }>(), { choicesDisabled: false, requestOptions: null });
defineEmits<{ clarification: [text: string] }>();
const business = computed(() => props.result.kind === 'business' ? props.result.response : null);
const apiError = computed(() => props.result.kind === 'api_error' ? props.result : null);
const requestError = computed(() => props.result.kind === 'request_error' ? props.result : null);
const titles: Record<AskStatus, string> = {
  answered: '已回答', clarification_required: '需要补充条件',
  insufficient_evidence: '证据不足', unsupported: '当前能力不支持', error: '业务处理出错',
};
const routes: Record<Route, string> = { sql: '数据库问数', rag: '文档检索', cross_source: '跨源', clarification: '澄清', unsupported: '不支持' };
const citations = computed(() => business.value ? citationTargets(business.value) : []);
// The answer leads with the substantive conclusion; source-banner/provenance lines are demoted to small notes below it.
const answerSplit = computed(() => (business.value?.answer != null ? splitAnswer(business.value.answer) : null));
const conclusionSegments = computed(() => (answerSplit.value ? answerSegments(answerSplit.value.conclusion, citations.value) : []));
const noteSegments = computed(() => (answerSplit.value?.notes ? answerSegments(answerSplit.value.notes, citations.value) : []));
// Citation click: expand, scroll to and briefly highlight the referenced evidence card.
function focusEvidence(refId: string) {
  const element = document.getElementById(refId);
  if (!element) return;
  element.querySelectorAll('details').forEach((details) => { details.open = true; });
  // Cards taller than the viewport must keep their head (SQL text, metric definition) on screen, so align those to the top.
  const block = element.getBoundingClientRect().height > window.innerHeight * 0.8 ? 'start' : 'center';
  element.scrollIntoView?.({ behavior: 'smooth', block });
  element.classList.add('evidence-highlight');
  window.setTimeout(() => element.classList.remove('evidence-highlight'), 2400);
}
</script>

<template>
  <section class="result-card" data-testid="ask-result" aria-labelledby="result-heading">
    <template v-if="business">
      <div class="result-heading">
        <h2 id="result-heading">{{ titles[business.status] }}</h2>
        <span class="status-tag" :data-status="business.status">{{ business.status }}</span>
      </div>
      <div v-if="business.answer !== null" class="answer-block" data-testid="answer">
        <p class="answer-conclusion" data-testid="answer-conclusion"><CopyButton v-if="business.answer" class="answer-copy" :text="business.answer" label="复制答案" /><template v-for="(segment, index) in conclusionSegments" :key="index"><button v-if="segment.refId" type="button" class="cite-link" :data-testid="`answer-ref-${index}`" @click="focusEvidence(segment.refId)">{{ segment.text }}</button><template v-else>{{ segment.text }}</template></template></p>
        <p v-if="noteSegments.length" class="answer-notes" data-testid="answer-notes"><template v-for="(segment, index) in noteSegments" :key="`note-${index}`"><button v-if="segment.refId" type="button" class="cite-link" :data-testid="`answer-note-ref-${index}`" @click="focusEvidence(segment.refId)">{{ segment.text }}</button><template v-else>{{ segment.text }}</template></template></p>
      </div>
      <div v-if="citations.length" class="citation-bar" data-testid="citation-bar" aria-label="回答引用">
        <span class="citation-bar-label">回答引用</span>
        <button v-for="(target, index) in citations" :key="`${target.refId}-${index}`" type="button" class="citation-chip" :data-kind="target.kind" :data-testid="`citation-chip-${index}`" :title="target.label" @click="focusEvidence(target.refId)">{{ target.label }}</button>
      </div>
      <dl class="result-meta">
        <div><dt>业务路由</dt><dd>{{ routes[business.route] }} · {{ business.route }}</dd></div>
        <div><dt>请求 ID</dt><dd>{{ result.requestId }}</dd></div>
      </dl>
      <div v-if="business.status === 'error' && business.error" class="error-message" role="alert">
        <p>{{ business.error.code }}：{{ business.error.message }}</p>
        <p>{{ business.error.retryable ? '可手动重试，不会自动重发。' : '请依据提示修改问题或检查能力范围。' }}</p>
      </div>
      <section v-if="business.status === 'clarification_required' && business.clarification" class="clarification-card" aria-labelledby="clarification-heading">
        <h3 id="clarification-heading">请补充或确认</h3>
        <p class="answer-text" data-testid="clarification-question">{{ business.clarification.question }}</p>
        <ul v-if="business.clarification.missing_slots?.length">
          <li v-for="(slot, index) in business.clarification.missing_slots" :key="index">{{ slot.name }}：{{ slot.description }}</li>
        </ul>
        <div v-if="business.clarification.options?.length" class="choice-list">
          <button v-for="(option, index) in business.clarification.options" :key="index" type="button" :disabled="choicesDisabled" :data-testid="`clarification-choice-${index}`" @click="$emit('clarification', option)">{{ option }}</button>
        </div>
        <p class="helper">点击候选项会带当前会话发送补充请求；也可以在输入框手动补充。不会默认补年份。</p>
      </section>
      <EvidencePanel :key="business.request_id" :response="business" :show-trace-requested="requestOptions?.show_trace ?? null" @focus-ref="focusEvidence" />
    </template>
    <template v-else-if="apiError">
      <h2 id="result-heading">API 请求出错</h2>
      <div class="error-message" role="alert">
        <p data-testid="api-error">HTTP {{ apiError.httpStatus }} · {{ apiError.error.code }}</p>
        <p>{{ apiError.error.message }}</p>
        <p>{{ apiError.error.retryable ? '可手动重试，不会自动重发。' : '请修改请求或检查配置后再提交。' }}</p>
      </div>
      <p class="helper">请求 ID：{{ apiError.requestId }}</p>
    </template>
    <template v-else-if="requestError">
      <h2 id="result-heading">未获得可用业务响应</h2>
      <div class="error-message" role="alert">
        <p data-testid="request-error">{{ requestError.reason }}：{{ requestError.message }}</p>
        <p v-if="requestError.httpStatus !== undefined">HTTP {{ requestError.httpStatus }}</p>
      </div>
      <p class="helper">请求关联：{{ requestError.requestId }}</p>
    </template>
  </section>
</template>
