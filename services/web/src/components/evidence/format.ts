import type { AskResponse, JsonValue, SqlQueryResponse } from '../../contracts';

export type CitationKind = 'sql' | 'document' | 'metric';
export interface CitationTarget { refId: string; token: string; label: string; kind: CitationKind }
export interface AnswerSegment { text: string; refId: string | null }

/** Evidence anchors for the current response: SQL queries, document chunks, metric definitions. */
export function citationTargets(response: AskResponse): CitationTarget[] {
  const targets: CitationTarget[] = [];
  for (const sql of response.sql_results ?? []) targets.push({ refId: `ev-sql-${sql.query_id}`, token: sql.query_id, label: `SQL · ${sql.query_id}`, kind: 'sql' });
  for (const document of response.documents ?? []) targets.push({ refId: `ev-doc-${document.chunk_id}`, token: document.chunk_id, label: `文档 · ${document.title}`, kind: 'document' });
  for (const metric of response.metric_definitions ?? []) targets.push({ refId: `ev-metric-${metric.metric_id}`, token: metric.metric_id, label: `口径 · ${metric.name}`, kind: 'metric' });
  return targets;
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const ANSWER_NOTE_LINE = /^来源：(真实|SQL)/;

/** Separate source-banner/provenance boilerplate lines from the substantive answer so the UI can lead with the conclusion. */
export function splitAnswer(answer: string): { conclusion: string; notes: string } {
  const conclusion: string[] = [];
  const notes: string[] = [];
  for (const line of answer.split('\n')) (ANSWER_NOTE_LINE.test(line.trim()) ? notes : conclusion).push(line);
  if (conclusion.some((line) => line.trim())) return { conclusion: conclusion.join('\n'), notes: notes.join('\n') };
  return { conclusion: answer, notes: '' };
}

/** Split the answer into plain text and clickable evidence-ID segments; text content is preserved verbatim. */
export function answerSegments(answer: string, targets: CitationTarget[]): AnswerSegment[] {
  const tokenMap = new Map(targets.filter((target) => target.kind !== 'metric' && target.token).map((target) => [target.token, target.refId]));
  if (!answer || tokenMap.size === 0) return [{ text: answer, refId: null }];
  const tokens = [...tokenMap.keys()].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(${tokens.map(escapeRegExp).join('|')})`, 'g');
  return answer.split(pattern).filter((part) => part !== '').map((part) => ({ text: part, refId: tokenMap.get(part) ?? null }));
}

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
