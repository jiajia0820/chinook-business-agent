import type { AskResponse, JsonValue, SqlQueryResponse } from '../../contracts';

export function jsonText(value: JsonValue | undefined): string {
  return value === undefined ? '（未提供）' : JSON.stringify(value, null, 2);
}
export function cellText(row: Record<string, JsonValue>, column: string): string {
  if (!Object.hasOwn(row, column)) return '（缺失字段）';
  const value = row[column];
  if (value === '') return '（空字符串）';
  if (typeof value === 'string' && value.trim()) return value;
  return jsonText(value);
}
export function sqlShapeNotes(result: SqlQueryResponse): string[] {
  const notes: string[] = [];
  const columns = result.columns ?? [];
  const rows = result.rows ?? [];
  if (result.columns === undefined) notes.push('响应未提供列列表。');
  if (result.rows === undefined) notes.push('响应未提供结果行。');
  else if (result.row_count !== rows.length) notes.push(`row_count=${result.row_count}，实际收到 ${rows.length} 行；保留原值，不推断总量。`);
  const columnSet = new Set(columns);
  if (columnSet.size !== columns.length) notes.push('列名存在重复；按返回顺序保留。');
  if (rows.some((row) => columns.some((column) => !Object.hasOwn(row, column)))) notes.push('部分行缺少声明列；缺失字段不是 null 或 0。');
  if (rows.some((row) => Object.keys(row).some((key) => !columnSet.has(key)))) notes.push('部分行包含声明列之外的字段；请查看完整原始行。');
  return notes;
}
const fixtureText = (value: string) => /fixture(?::|\/\/)|人工(?:测试|来源|资料|证据|计算| SQL)/i.test(value);
export function hasFixtureSources(response: AskResponse): boolean {
  return fixtureText(response.answer ?? '') || (response.limitations ?? []).some(fixtureText)
    || (response.documents ?? []).some((doc) => doc.doc_type.toLowerCase() === 'fixture' || fixtureText(doc.source_uri))
    || (response.sql_results ?? []).some((sql) => [sql.query_id, sql.source.type, sql.source.name].some(fixtureText) || sql.source.type.toLowerCase() === 'fixture')
    || (response.metric_definitions ?? []).some((metric) => (metric.source_refs ?? []).some(fixtureText))
    || (response.calculations ?? []).some((calculation) => (calculation.inputs ?? []).some(fixtureText))
    || (response.trace ?? []).some((step) => (step.source_refs ?? []).some(fixtureText));
}
