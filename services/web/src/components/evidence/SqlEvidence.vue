<script setup lang="ts">
import { computed } from 'vue';
import type { SqlQueryResponse } from '../../contracts';
import CopyButton from '../CopyButton.vue';
import { cellText, jsonText, sqlRowsToCsv, sqlShapeNotes } from './format';
import { highlightSql } from './sqlHighlight';
const props = defineProps<{ result: SqlQueryResponse; answerText?: string | null }>();
const notes = computed(() => sqlShapeNotes(props.result));
const sqlTokens = computed(() => highlightSql(props.result.sql ?? ''));
// Cells whose value the answer actually quotes: the raw origin of the answer number.
const originCells = computed(() => {
  const answer = props.answerText ?? '';
  const cells = new Set<string>();
  const rows = props.result.rows;
  const columns = props.result.columns;
  if (!answer || !rows?.length || !columns?.length) return cells;
  const answerNumbers = new Set(answer.match(/-?\d+(?:\.\d+)?/g) ?? []);
  for (const row of rows) {
    for (const column of columns) {
      const text = cellText(row, column).trim();
      if (!text) continue;
      if (/^-?\d+(?:\.\d+)?$/.test(text)) {
        if (/^(19|20)\d{2}$/.test(text)) continue; // year labels are context, not the answer value
        if (answerNumbers.has(text)) cells.add(text);
      } else if (text.length >= 2 && answer.includes(text)) {
        cells.add(text);
      }
    }
  }
  return cells;
});
const statuses = { success: '查询成功', failed: '查询失败', rejected: '查询被拒绝' };
// Excel on Windows only detects UTF-8 CSV reliably with a BOM, so the export prepends one.
function exportCsv() {
  const columns = props.result.columns ?? [];
  const rows = props.result.rows ?? [];
  if (!columns.length || !rows.length) return;
  const blob = new Blob(['\uFEFF' + sqlRowsToCsv(columns, rows)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `sql-result-${props.result.query_id}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <article class="evidence-card" :id="`ev-sql-${result.query_id}`" data-testid="sql-evidence" aria-label="SQL 证据">
    <h5>SQL · {{ result.query_id }}</h5>
    <p class="evidence-status" :data-sql-status="result.status">{{ statuses[result.status] }} · {{ result.status }}</p>
    <dl class="evidence-meta">
      <div><dt>Profile</dt><dd>{{ result.profile_id }}</dd></div>
      <div><dt>数据源</dt><dd>{{ result.source.name }} · {{ result.source.type }}</dd></div>
      <div><dt>来源表</dt><dd><ul v-if="result.source.tables?.length"><li v-for="(table, index) in result.source.tables" :key="index">{{ table }}</li></ul><span v-else>未提供来源表列表</span></dd></div>
      <div><dt>执行耗时</dt><dd>{{ result.execution_ms }} ms（后端返回值）</dd></div>
      <div><dt>返回行数</dt><dd>row_count={{ result.row_count }} · 实际收到 {{ result.rows === undefined ? '未提供' : result.rows.length }} 行</dd></div>
      <div><dt>是否截断</dt><dd>{{ result.truncated ? '是 · true' : '否 · false' }}</dd></div>
    </dl>
    <p v-if="result.truncated" class="evidence-warning" data-testid="sql-truncated">结果已截断：仅展示返回子集，不代表完整总量。</p>
    <div v-if="result.status !== 'success'" class="error-message" role="alert" data-testid="sql-error">
      <p>{{ result.error?.code }}：{{ result.error?.message }}</p>
      <p>查询未成功，不能把空行解释成业务数值为 0。</p>
    </div>
    <details data-testid="sql-query" open>
      <summary class="sql-summary"><span>候选 SQL 与参数</span><CopyButton v-if="result.sql != null && result.sql.trim()" :text="result.sql" label="复制 SQL" /></summary>
      <p class="helper">当前默认离线实例中的 SQL 字段为 C 已接受候选；执行前会校验和规范化，不是逐字驱动 SQL。这里只读展示，未提供时不生成语句，也不提供执行入口。</p>
      <pre v-if="result.sql != null && result.sql.trim()" class="sql-code" data-testid="candidate-sql"><span v-for="(token, index) in sqlTokens" :key="index" :class="token.cls ?? undefined">{{ token.text }}</span></pre>
      <p v-if="result.sql != null && result.sql.trim()" class="sql-legend" data-testid="sql-legend"><span><i class="chip chip-kw"></i>关键字</span><span><i class="chip chip-fn"></i>函数</span><span><i class="chip chip-param"></i>绑定参数</span><span class="legend-plain">字符串/数字仅加粗变色，表名列名保持原色</span></p>
      <p v-else>未提供候选 SQL 语句。</p>
      <p>参数（独立于候选语句）</p>
      <p v-if="result.params == null">未提供参数。</p>
      <p v-else-if="Object.keys(result.params).length === 0">无参数（{}）。</p>
      <pre v-else data-testid="sql-params">{{ jsonText(result.params) }}</pre>
    </details>
    <ul v-if="notes.length" class="evidence-warning" data-testid="sql-shape-notes"><li v-for="(note, index) in notes" :key="index">{{ note }}</li></ul>
    <div v-if="result.columns?.length && result.rows?.length" class="table-toolbar">
      <button type="button" class="secondary" data-testid="export-csv" aria-label="导出 CSV" @click="exportCsv">导出 CSV</button>
      <span class="helper">UTF-8 含 BOM，Excel 可直接打开</span>
    </div>
    <div v-if="result.columns?.length && result.rows?.length" class="table-scroll" role="region" aria-label="SQL 查询结果表" tabindex="0">
      <table data-testid="sql-table">
        <caption>返回行明细 · {{ result.query_id }}（不是业务总量汇总）<span v-if="originCells.size" class="caption-origin">；黄底单元格即答案数值的原始出处</span></caption>
        <thead><tr><th v-for="(column, index) in result.columns" :key="index" scope="col">{{ column }}</th></tr></thead>
        <tbody><tr v-for="(row, rowIndex) in result.rows" :key="rowIndex"><td v-for="(column, columnIndex) in result.columns" :key="columnIndex" :class="{ 'cell-origin': originCells.has(cellText(row, column).trim()) }">{{ cellText(row, column) }}</td></tr></tbody>
      </table>
    </div>
    <p v-else-if="result.status === 'success' && result.rows?.length === 0" data-testid="sql-empty">查询成功，本次返回 0 行；这不是经营指标值为 0 的结论。</p>
    <p v-else-if="result.rows?.length">有结果行但未提供可用列，请查看完整原始行。</p>
    <details v-if="result.rows?.length" data-testid="sql-raw-rows"><summary>查看完整原始行（含额外字段）</summary><pre>{{ jsonText(result.rows) }}</pre></details>
  </article>
</template>
