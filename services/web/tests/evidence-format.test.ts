import { describe, expect, it } from 'vitest';
import type { JsonValue } from '../src/contracts';
import { cellText, hasFixtureSources, jsonText, sqlShapeNotes } from '../src/components/evidence/format';
import { copySample } from './recorded-http';
import { recordedCalculations, sqlResult } from './evidence-fixtures';

describe('pure display formatting, not a calculator or semantic validator', () => {
  it.each([
    [0, '0'], [false, 'false'], [null, 'null'], ['', '（空字符串）'], ['Rock', 'Rock'], ['  ', '"  "'],
    [1.23456789, '1.23456789'], [-20, '-20'], [true, 'true'], ['<script>x</script>', '<script>x</script>'],
  ] satisfies [JsonValue, string][])('preserves scalar %j', (value, expected) => {
    expect(cellText({ value }, 'value')).toBe(expected);
  });
  it('does not mistake inherited or missing keys for JSON null', () => {
    expect(cellText({}, 'toString')).toBe('（缺失字段）');
    expect(cellText({}, 'value')).toBe('（缺失字段）');
    expect(cellText({ value: null }, 'value')).toBe('null');
  });
  it('preserves nested JSON and has no expression execution', () => {
    const value: JsonValue = { values: [0, false, null, { text: '1 + 1' }] };
    expect(JSON.parse(cellText({ value }, 'value'))).toEqual(value);
    expect(jsonText(undefined)).toBe('（未提供）');
    expect(jsonText('1 + 1')).toBe('"1 + 1"');
  });
  it('handles an own __proto__ field without object merging', () => {
    const row = JSON.parse('{"__proto__":{"polluted":true}}') as Record<string, JsonValue>;
    expect(cellText(row, '__proto__')).toContain('polluted');
    expect(Object.hasOwn({}, 'polluted')).toBe(false);
  });
  it('valid recorded row/column shapes have no invented warning', () => {
    expect(sqlShapeNotes(sqlResult())).toEqual([]);
  });
  it('reports missing arrays, not a false empty-query result', () => {
    const sql = sqlResult();
    delete sql.columns;
    delete sql.rows;
    expect(sqlShapeNotes(sql)).toEqual(['响应未提供列列表。', '响应未提供结果行。']);
  });
  it('reports count mismatch, duplicate columns, missing and additional keys without mutating', () => {
    const sql = sqlResult();
    sql.columns = ['a', 'a', 'missing'];
    sql.rows = [{ a: 0, extra: false }];
    sql.row_count = 9;
    const before = structuredClone(sql);
    const notes = sqlShapeNotes(sql);
    expect(notes).toHaveLength(4);
    expect(notes.join(' ')).toContain('row_count=9');
    expect(notes.join(' ')).toContain('完整原始行');
    expect(sql).toEqual(before);
  });
  it('keeps genuine sample SQL/D12 separate from artificial calculation sources', () => {
    expect(hasFixtureSources(copySample())).toBe(false);
    for (const record of recordedCalculations) expect(hasFixtureSources(record)).toBe(true);
  });
  it.each(['answer', 'limitations', 'document_type', 'source_uri', 'sql_type', 'metric_refs', 'calculation_refs', 'trace_refs'])('warns for an explicit artificial marker in %s', (location) => {
    const response = copySample();
    if (location === 'answer') response.answer = '人工测试来源';
    if (location === 'limitations') response.limitations = ['人工测试资料'];
    if (location === 'document_type' || location === 'source_uri') response.documents = [{ doc_id: 'test', chunk_id: 'test', title: 'test', text: 'test', doc_type: location === 'document_type' ? 'fixture' : 'markdown', source_uri: location === 'source_uri' ? 'fixture://test' : 'relative:test' }];
    if (location === 'sql_type') response.sql_results![0]!.source.type = 'fixture';
    if (location === 'metric_refs') response.metric_definitions![0]!.source_refs = ['fixture:metric'];
    if (location === 'calculation_refs') response.calculations = [{ calculation_id: 'test', formula: '1', result: 1, unit: '', inputs: ['fixture:value'] }];
    if (location === 'trace_refs') response.trace = [{ step: 1, tool: 'test', status: 'test', duration_ms: 0, source_refs: ['fixture:value'] }];
    expect(hasFixtureSources(response)).toBe(true);
  });
});
