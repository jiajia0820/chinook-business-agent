import { describe, expect, expectTypeOf, it } from 'vitest';
import type { AskRequest, AskResponse, AskStatus, JsonValue, Route, SqlQueryResponse } from '../src/contracts';
import { isApiError, isAskRequest, isAskResponse, isJsonValue } from '../src/contracts/validate';
import { copySample, recordedBusiness, recordedHttp, request } from './recorded-http';

describe('OpenAPI contract and recorded HTTP evidence', () => {
  it('preserves all 18 actual recorded HTTP responses and 15 business bodies', () => {
    expect(recordedHttp).toHaveLength(18);
    expect(recordedBusiness).toHaveLength(15);
    for (const item of recordedHttp) {
      if (item.http_status >= 400) expect(isApiError(item.body)).toBe(true);
      else if (item.case !== 'health_liveness') expect(isAskResponse(item.body)).toBe(true);
    }
  });
  it('covers the five statuses without making an HTTP success assumption', () => {
    const statuses = new Set(recordedBusiness.map((item) => (item.body as AskResponse).status));
    expect([...statuses].sort()).toEqual(['answered', 'clarification_required', 'error', 'insufficient_evidence', 'unsupported']);
  });
  it('keeps generated nullable, optional, dynamic JSON and enum types', () => {
    expectTypeOf<AskResponse['answer']>().toEqualTypeOf<string | null>();
    expectTypeOf<AskStatus>().toEqualTypeOf<'answered' | 'clarification_required' | 'insufficient_evidence' | 'unsupported' | 'error'>();
    expectTypeOf<Route>().toEqualTypeOf<'sql' | 'rag' | 'cross_source' | 'clarification' | 'unsupported'>();
    expectTypeOf<SqlQueryResponse['params']>().toEqualTypeOf<Record<string, JsonValue> | null | undefined>();
    const values: JsonValue = { number: 0, boolean: false, nil: null, array: ['x', { nested: [1] }] };
    expect(isJsonValue(values)).toBe(true);
    // @ts-expect-error JSON values do not include Date instances.
    const notJson: JsonValue = new Date();
    void notJson;
  });
  it('allows omitted default options without inventing required fields', () => {
    const minimal: AskRequest = request;
    expect(isAskRequest(minimal)).toBe(true);
    expect(isAskRequest({ ...minimal, options: {} })).toBe(true);
    expect(isAskRequest({ ...minimal, session_id: null, user_role: null })).toBe(true);
  });
  it.each([0, 201, 1.5, '50', true])('rejects invalid max_rows %s without coercion', (max_rows) => {
    expect(isAskRequest({ ...request, options: { max_rows } })).toBe(false);
  });
  it.each([0, 11, '5'])('rejects invalid top_k %s', (top_k) => {
    expect(isAskRequest({ ...request, options: { top_k } })).toBe(false);
  });
  it.each(['question', 'profile_id', 'session_id', 'user_role'])('rejects whitespace-only %s', (field) => {
    expect(isAskRequest({ ...request, [field]: '  ' })).toBe(false);
  });
  it('forbids undeclared request and nested options fields', () => {
    expect(isAskRequest({ ...request, sql: 'SELECT 1' })).toBe(false);
    expect(isAskRequest({ ...request, api_key: 'invented-unit-value' })).toBe(false);
    expect(isAskRequest({ ...request, options: { confirmed: true } })).toBe(false);
  });
  it.each([NaN, Infinity, undefined, 1n, () => 1, new Date()])('rejects non-JSON runtime values', (value) => {
    expect(isJsonValue({ value })).toBe(false);
  });
  it('rejects circular data and excessive nesting safely', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(isJsonValue(circular)).toBe(false);
    let deep: unknown = null;
    for (let i = 0; i < 130; i++) deep = [deep];
    expect(isJsonValue(deep)).toBe(false);
  });
  it('does not mutate input, apply defaults or discard unknown values', () => {
    const body = copySample();
    const before = JSON.stringify(body);
    expect(isAskResponse(body)).toBe(true);
    expect(JSON.stringify(body)).toBe(before);
    const input = { ...request, unknown: 1 };
    expect(isAskRequest(input)).toBe(false);
    expect(input.unknown).toBe(1);
  });
  it('rejects unknown status, missing required and extra response fields', () => {
    expect(isAskResponse({ ...copySample(), status: 'success' })).toBe(false);
    expect(isAskResponse({ ...copySample(), request_id: undefined })).toBe(false);
    expect(isAskResponse({ ...copySample(), internal_diagnostics: 'not public' })).toBe(false);
  });
  it('enforces business-state invariants absent from OpenAPI', () => {
    expect(isAskResponse({ ...copySample(), answer: '' })).toBe(false);
    expect(isAskResponse({ ...copySample(), status: 'error', error: null })).toBe(false);
    expect(isAskResponse({ ...copySample(), status: 'unsupported', route: 'sql' })).toBe(false);
    expect(isAskResponse({ ...copySample(), status: 'clarification_required', route: 'clarification', clarification: null })).toBe(false);
    expect(isAskResponse({ ...copySample(), error: { code: 'X', message: 'x', retryable: false } })).toBe(false);
  });
  it('enforces successful/failed SQL error state', () => {
    const body = copySample();
    const sql = body.sql_results?.[0];
    expect(sql).toBeDefined();
    expect(isAskResponse({ ...body, sql_results: [{ ...sql, status: 'failed', error: null }] })).toBe(false);
    expect(isAskResponse({ ...body, sql_results: [{ ...sql, error: { code: 'X', message: 'x', retryable: true } }] })).toBe(false);
  });
  it('accepts nullable locators, empty arrays and complex dynamic row cells', () => {
    const body = copySample();
    const sql = body.sql_results?.[0];
    expect(isAskResponse({ ...body, sql_results: [{ ...sql, columns: ['dynamic'], rows: [{ dynamic: { list: [false, null, 0] } }], row_count: 1, sql: null, params: null }], documents: [], calculations: [], trace: [] })).toBe(true);
  });
  it('validates top-level errors independently from business responses', () => {
    const apiError = { code: 'FUTURE_CODE', message: '服务不可用', retryable: true, details: { reason: 'UNIT' } };
    expect(isApiError(apiError)).toBe(true);
    expect(isAskResponse(apiError)).toBe(false);
    expect(isApiError({ ...apiError, retryable: 'true' })).toBe(false);
  });
});
