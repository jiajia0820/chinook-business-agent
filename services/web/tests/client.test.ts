import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AskRequest, AskResponse } from '../src/contracts';
import { createAskClient } from '../src/api/client';
import { copySample, deferred, jsonResponse, recordedBusiness, request } from './recorded-http';

function responding(body: unknown, status = 200, headerId?: string) {
  return vi.fn<typeof fetch>(async (_url, init) => {
    // Simulated transport echoes the request ID, like the actual backend.
    // Recorded-body tests below instead pass the original recorded ID.
    const echoedId = new Headers(init?.headers).get('X-Request-ID');
    const outgoing = status === 200 && body !== null && typeof body === 'object' && 'request_id' in body && headerId === undefined
      ? { ...body, request_id: echoedId }
      : body;
    return jsonResponse(outgoing, status, headerId);
  });
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('typed API client', () => {
  it.each(recordedBusiness.map((record, index) => ({ record, index })))('consumes recorded business body $index without reclassifying its status', async ({ record }) => {
    const body = record.body as AskResponse;
    const client = createAskClient({ fetch: responding(body, 200, record.request_id_header) });
    const result = await client.ask({ question: '客户端离线消费记录测试', profile_id: body.profile_id, session_id: body.session_id }, { requestId: body.request_id });
    expect(result.kind).toBe('business');
    if (result.kind === 'business') {
      expect(result.response).toEqual(body);
      expect(result.response.status).toBe(body.status);
      expect(result.requestId).toBe(body.request_id);
    }
  });
  it('sends one relative POST with only public fields and request ID', async () => {
    const fakeFetch = responding(copySample());
    const client = createAskClient({ fetch: fakeFetch });
    const payload: AskRequest = { question: ' 客户数量是多少？ ', profile_id: ' chinook-music ', session_id: null, user_role: null, options: { show_trace: false, max_rows: 2, top_k: 1 } };
    const before = structuredClone(payload);
    await client.ask(payload, { requestId: 'req-web-unit' });
    expect(fakeFetch).toHaveBeenCalledTimes(1);
    const [url, init] = fakeFetch.mock.calls[0]!;
    expect(url).toBe('/api/v1/ask');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
    expect(init?.redirect).toBe('error');
    expect(new Headers(init?.headers).get('X-Request-ID')).toBe('req-web-unit');
    expect(JSON.parse(init?.body as string)).toEqual({ ...payload, question: payload.question.trim(), profile_id: payload.profile_id.trim() });
    expect(payload).toEqual(before);
  });
  it('generates unique request IDs', async () => {
    const fakeFetch = responding(copySample());
    const client = createAskClient({ fetch: fakeFetch });
    await client.ask(request);
    await client.ask(request);
    const ids = fakeFetch.mock.calls.map(([, init]) => new Headers(init?.headers).get('X-Request-ID'));
    expect(ids[0]).toMatch(/^req-web-/);
    expect(ids[0]).not.toBe(ids[1]);
  });
  it.each([422, 503, 500])('keeps %s ApiError and request ID separate from business state', async (status) => {
    const error = { code: 'UNIT_ERROR', message: '已脱敏错误', retryable: status >= 500, details: null };
    const fakeFetch = responding(error, status, 'req-unit-error');
    const result = await createAskClient({ fetch: fakeFetch }).ask(request, { requestId: 'req-unit-error' });
    expect(result).toEqual({ kind: 'api_error', error, httpStatus: status, requestId: 'req-unit-error' });
    expect(fakeFetch).toHaveBeenCalledTimes(1);
  });
  it('falls back to the business body ID when the header is absent', async () => {
    const body = copySample();
    const result = await createAskClient({ fetch: responding(body) }).ask(request, { requestId: body.request_id });
    expect(result.requestId).toBe(body.request_id);
  });
  it('uses the sent request ID for a headerless ApiError', async () => {
    const result = await createAskClient({ fetch: responding({ code: 'X', message: 'x', retryable: true }, 503) }).ask(request, { requestId: 'req-sent' });
    expect(result.requestId).toBe('req-sent');
  });
  it.each([
    { ...request, sql: 'SELECT 1' },
    { ...request, api_key: 'invented-unit-value' },
    { ...request, options: { max_rows: 201 } },
    { ...request, question: '  ' },
    { ...request, session_id: '' },
  ])('rejects invalid/extra request fields before fetch', async (payload) => {
    const fakeFetch = responding(copySample());
    const result = await createAskClient({ fetch: fakeFetch }).ask(payload as AskRequest);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_request' });
    expect(fakeFetch).not.toHaveBeenCalled();
  });
  it('rejects an unsafe custom request ID without echoing it', async () => {
    const fakeFetch = responding(copySample());
    const result = await createAskClient({ fetch: fakeFetch }).ask(request, { requestId: 'UNIT-PRIVATE\nvalue' });
    expect(result).toMatchObject({ kind: 'request_error', requestId: 'request-not-sent' });
    expect(JSON.stringify(result)).not.toContain('PRIVATE');
    expect(fakeFetch).not.toHaveBeenCalled();
  });
  it('rejects header/body ID mismatch', async () => {
    const result = await createAskClient({ fetch: responding(copySample(), 200, 'req-other') }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response', httpStatus: 200 });
  });
  it('rejects another request ID even if header and body agree', async () => {
    const body = copySample();
    const result = await createAskClient({ fetch: responding(body, 200, body.request_id) }).ask(request, { requestId: 'req-current' });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response', requestId: 'req-current' });
  });
  it('rejects another request ID in a headerless business body', async () => {
    const body = copySample();
    const fakeFetch = vi.fn<typeof fetch>(async () => jsonResponse(body));
    const result = await createAskClient({ fetch: fakeFetch }).ask(request, { requestId: 'req-current' });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it('rejects mismatched request ID on an ApiError', async () => {
    const result = await createAskClient({ fetch: responding({ code: 'X', message: 'x', retryable: true }, 503, 'req-other') }).ask(request, { requestId: 'req-current' });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it.each([{ ...request, session_id: 'other-session' }, { ...request, profile_id: 'other-profile' }])('rejects mismatched session/profile evidence', async (payload) => {
    const result = await createAskClient({ fetch: responding(copySample()) }).ask(payload);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it.each([{}, null, [], { answer: 'unsupported body' }])('rejects malformed JSON shape %j', async (body) => {
    const result = await createAskClient({ fetch: responding(body) }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it('rejects HTML responses without exposing their contents', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => new Response('UNIT-PRIVATE html', { status: 503, headers: { 'Content-Type': 'text/html' } }));
    const result = await createAskClient({ fetch: fakeFetch }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response', httpStatus: 503 });
    expect(JSON.stringify(result)).not.toContain('PRIVATE');
  });
  it('rejects invalid JSON parsing and does not expose raw text', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => new Response('UNIT-PRIVATE invalid json', { headers: { 'Content-Type': 'application/json' } }));
    const result = await createAskClient({ fetch: fakeFetch }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
    expect(JSON.stringify(result)).not.toContain('PRIVATE');
  });
  it.each([201, 502])('rejects unexpected status/body pairing %s', async (status) => {
    const result = await createAskClient({ fetch: responding(copySample(), status) }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response', httpStatus: status });
  });
  it('does not accept a top-level ApiError as a 200 business answer', async () => {
    const result = await createAskClient({ fetch: responding({ code: 'X', message: 'x', retryable: false }) }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it('accepts JSON media types with charset and structured +json suffix', async () => {
    for (const mime of ['application/json; charset=utf-8', 'application/problem+json']) {
      const fakeFetch = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(copySample()), { headers: { 'Content-Type': mime } }));
      expect((await createAskClient({ fetch: fakeFetch }).ask(request, { requestId: copySample().request_id })).kind).toBe('business');
    }
  });
  it('does not automatically retry network failures or expose raw exceptions', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => { throw new Error('UNIT-PRIVATE credential-like error'); });
    const result = await createAskClient({ fetch: fakeFetch }).ask(request);
    expect(result).toMatchObject({ kind: 'request_error', reason: 'network' });
    expect(JSON.stringify(result)).not.toContain('PRIVATE');
    expect(fakeFetch).toHaveBeenCalledTimes(1);
  });
  it('pre-aborted requests do not fetch', async () => {
    const controller = new AbortController();
    controller.abort();
    const fakeFetch = responding(copySample());
    const result = await createAskClient({ fetch: fakeFetch }).ask(request, { signal: controller.signal });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'cancelled' });
    expect(fakeFetch).not.toHaveBeenCalled();
  });
  it('caller cancellation stops waiting even when fetch ignores abort', async () => {
    const pending = deferred<Response>();
    const fakeFetch = vi.fn<typeof fetch>(() => pending.promise);
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    const resultPromise = createAskClient({ fetch: fakeFetch }).ask(request, { signal: controller.signal });
    controller.abort();
    const result = await resultPromise;
    expect(result).toMatchObject({ kind: 'request_error', reason: 'cancelled' });
    expect(fakeFetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    pending.resolve(jsonResponse(copySample()));
    expect(result.kind).toBe('request_error');
  });
  it('bounds uncooperative fetch and cleans the timeout timer', async () => {
    vi.useFakeTimers();
    const fakeFetch = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));
    const pending = createAskClient({ fetch: fakeFetch, timeoutMs: 20 }).ask(request);
    await vi.advanceTimersByTimeAsync(20);
    expect(await pending).toMatchObject({ kind: 'request_error', reason: 'timeout' });
    expect(fakeFetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('also bounds a stalled response body reader', async () => {
    vi.useFakeTimers();
    const response = jsonResponse(copySample());
    vi.spyOn(response, 'json').mockImplementation(() => new Promise(() => {}));
    const fakeFetch = vi.fn<typeof fetch>(async () => response);
    const pending = createAskClient({ fetch: fakeFetch, timeoutMs: 20 }).ask(request);
    await vi.advanceTimersByTimeAsync(20);
    expect(await pending).toMatchObject({ kind: 'request_error', reason: 'timeout' });
    expect(vi.getTimerCount()).toBe(0);
  });
  it('cleans timers on ordinary completion', async () => {
    vi.useFakeTimers();
    await createAskClient({ fetch: responding(copySample()) }).ask(request);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each([0, -1, NaN, Infinity, 240_001])('rejects invalid timeout setting %s', (timeoutMs) => {
    expect(() => createAskClient({ timeoutMs })).toThrow('Invalid frontend request timeout.');
  });
});

function sseResponse(chunks: (string | Uint8Array)[], status = 200, headerId?: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream', ...(headerId ? { 'X-Request-ID': headerId } : {}) },
  });
}
const stageEvent = (node: string, label: string) => `event: stage\ndata: ${JSON.stringify({ node, label })}\n\n`;
const resultEvent = (body: unknown) => `event: result\ndata: ${JSON.stringify(body)}\n\n`;
// Mirrors the backend: the streamed body carries the request ID that was sent.
function streaming(body: AskResponse) {
  return vi.fn<typeof fetch>(async (_url, init) => {
    const id = new Headers(init?.headers).get('X-Request-ID')!;
    return sseResponse([
      stageEvent('parse', '意图与时间解析'),
      stageEvent('call_sql', 'SQL 生成与只读执行'),
      resultEvent({ ...body, request_id: id }),
    ], 200, id);
  });
}

describe('streaming progress endpoint', () => {
  it('posts to the stream route and reports stages in order before the business result', async () => {
    const fakeFetch = streaming(copySample());
    const onStage = vi.fn();
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-ok' }, onStage);
    expect(fakeFetch).toHaveBeenCalledTimes(1);
    expect(fakeFetch.mock.calls[0]?.[0]).toBe('/api/v1/ask/stream');
    expect(new Headers(fakeFetch.mock.calls[0]?.[1]?.headers).get('Accept')).toBe('text/event-stream');
    expect(onStage.mock.calls.map(([stage]) => stage)).toEqual([
      { node: 'parse', label: '意图与时间解析' },
      { node: 'call_sql', label: 'SQL 生成与只读执行' },
    ]);
    expect(result).toMatchObject({ kind: 'business', requestId: 'req-stream-ok' });
  });
  it('reassembles events split across chunks and multi-byte boundaries', async () => {
    const text = stageEvent('call_sql', 'SQL 生成与只读执行') + resultEvent({ ...copySample(), request_id: 'req-stream-split' });
    const encoder = new TextEncoder();
    const bytes = encoder.encode(text);
    // Cut one byte inside a Chinese character so decoding must stay stateful.
    const cut = encoder.encode(text.slice(0, text.indexOf('生成'))).length + 1;
    const fakeFetch = vi.fn<typeof fetch>(async () => sseResponse([bytes.slice(0, cut), bytes.slice(cut)], 200, 'req-stream-split'));
    const onStage = vi.fn();
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-split' }, onStage);
    expect(onStage).toHaveBeenCalledWith({ node: 'call_sql', label: 'SQL 生成与只读执行' });
    expect(result).toMatchObject({ kind: 'business', requestId: 'req-stream-split' });
  });
  it('falls back to the plain endpoint when the stream route is missing', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async (url, init) => {
      const id = new Headers(init?.headers).get('X-Request-ID')!;
      if (String(url).endsWith('/ask/stream')) return new Response(null, { status: 404 });
      return jsonResponse({ ...copySample(), request_id: id }, 200, id);
    });
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-404' });
    expect(fakeFetch.mock.calls.map(([url]) => url)).toEqual(['/api/v1/ask/stream', '/api/v1/ask']);
    expect(result).toMatchObject({ kind: 'business', requestId: 'req-stream-404' });
  });
  it('accepts a plain JSON body served on the stream route without a second request', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async (_url, init) => {
      const id = new Headers(init?.headers).get('X-Request-ID')!;
      return jsonResponse({ ...copySample(), request_id: id }, 200, id);
    });
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-json' });
    expect(fakeFetch).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ kind: 'business', requestId: 'req-stream-json' });
  });
  it('keeps a preflight ApiError served on the stream route', async () => {
    const error = { code: 'BACKEND_UNAVAILABLE', message: '已脱敏错误', retryable: true, details: null };
    const fakeFetch = vi.fn<typeof fetch>(async () => jsonResponse(error, 503, 'req-stream-503'));
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-503' });
    expect(result).toEqual({ kind: 'api_error', error, httpStatus: 503, requestId: 'req-stream-503' });
  });
  it('reports an interrupted stream as unusable without echoing its code', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => sseResponse([
      stageEvent('parse', '意图与时间解析'),
      'event: error\ndata: {"code":"STREAM_INTERRUPTED","message":"进度流中断；本轮结果不可用，请重新提问。","retryable":false}\n\n',
    ]));
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-err' });
    expect(result).toEqual({ kind: 'request_error', reason: 'invalid_response',
      message: '进度流中断；本轮结果不可用，请重新提问。', requestId: 'req-stream-err' });
    expect(JSON.stringify(result)).not.toContain('STREAM_INTERRUPTED');
  });
  it('rejects a stream that closes without a result event', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => sseResponse([stageEvent('parse', '意图与时间解析')]));
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-open' });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it('applies the same identity checks to a streamed business body', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async () => sseResponse([resultEvent({ ...copySample(), request_id: 'req-other' })]));
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-id' });
    expect(result).toMatchObject({ kind: 'request_error', reason: 'invalid_response' });
  });
  it('ignores malformed stage payloads instead of surfacing them', async () => {
    const fakeFetch = vi.fn<typeof fetch>(async (_url, init) => {
      const id = new Headers(init?.headers).get('X-Request-ID')!;
      return sseResponse([
        'event: stage\ndata: {"node":123}\n\n',
        'event: stage\ndata: UNIT-PRIVATE not-json\n\n',
        resultEvent({ ...copySample(), request_id: id }),
      ], 200, id);
    });
    const onStage = vi.fn();
    const result = await createAskClient({ fetch: fakeFetch }).askStream(request, { requestId: 'req-stream-junk' }, onStage);
    expect(onStage).not.toHaveBeenCalled();
    expect(result).toMatchObject({ kind: 'business' });
    expect(JSON.stringify(result)).not.toContain('PRIVATE');
  });
  it('rejects an invalid payload or unsafe request ID before fetching', async () => {
    const fakeFetch = streaming(copySample());
    const client = createAskClient({ fetch: fakeFetch });
    expect(await client.askStream({ ...request, question: '  ' })).toMatchObject({ kind: 'request_error', reason: 'invalid_request' });
    expect(await client.askStream(request, { requestId: 'UNIT-PRIVATE\nvalue' })).toMatchObject({ kind: 'request_error', requestId: 'request-not-sent' });
    expect(fakeFetch).not.toHaveBeenCalled();
  });
  it('stops waiting on caller cancellation while the stream stays open', async () => {
    const firstStage = deferred<void>();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode(stageEvent('parse', '意图与时间解析'))); },
    });
    const fakeFetch = vi.fn<typeof fetch>(async () => new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    const controller = new AbortController();
    const pending = createAskClient({ fetch: fakeFetch })
      .askStream(request, { requestId: 'req-stream-stop', signal: controller.signal }, () => firstStage.resolve());
    await firstStage.promise;
    controller.abort();
    expect(await pending).toMatchObject({ kind: 'request_error', reason: 'cancelled' });
    expect(fakeFetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });
  it('bounds an idle stream and cleans its timer', async () => {
    vi.useFakeTimers();
    const stream = new ReadableStream<Uint8Array>({ start() {} });
    const fakeFetch = vi.fn<typeof fetch>(async () => new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    const pending = createAskClient({ fetch: fakeFetch, timeoutMs: 20 }).askStream(request);
    await vi.advanceTimersByTimeAsync(20);
    expect(await pending).toMatchObject({ kind: 'request_error', reason: 'timeout' });
    expect(vi.getTimerCount()).toBe(0);
  });
});
