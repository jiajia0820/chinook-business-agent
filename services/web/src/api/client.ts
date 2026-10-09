import type { ApiError, AskRequest, AskResponse } from '../contracts';
import { isApiError, isAskRequest, isAskResponse } from '../contracts/validate';

export type RequestFailure = 'invalid_request' | 'network' | 'timeout' | 'cancelled' | 'invalid_response';
export type AskClientResult =
  | { kind: 'business'; response: AskResponse; httpStatus: 200; requestId: string }
  | { kind: 'api_error'; error: ApiError; httpStatus: number; requestId: string }
  | { kind: 'request_error'; reason: RequestFailure; message: string; requestId: string; httpStatus?: number };

export interface AskCallOptions { signal?: AbortSignal; requestId?: string }
/** One backend graph node reported while a question is still being processed. */
export interface AskStage { node: string; label: string }
export interface AskClient {
  ask(payload: AskRequest, options?: AskCallOptions): Promise<AskClientResult>;
  // Optional so a double that only knows the plain endpoint still satisfies the contract.
  askStream?(payload: AskRequest, options?: AskCallOptions, onStage?: (stage: AskStage) => void): Promise<AskClientResult>;
}
/** The real transport always supports node-level progress; injected doubles may not. */
export interface StreamingAskClient extends AskClient {
  askStream(payload: AskRequest, options?: AskCallOptions, onStage?: (stage: AskStage) => void): Promise<AskClientResult>;
}
export interface AskClientSettings { fetch?: typeof fetch; timeoutMs?: number }

interface ResponseMeta {
  httpStatus: number;
  requestId: string;
  headerId: string | null;
  stopped: 'timeout' | 'cancelled' | null;
}

const messages: Record<RequestFailure, string> = {
  invalid_request: '请求内容不符合接口契约，未发送。',
  network: '无法连接问答 API，请检查本机服务和网络连接。',
  timeout: '前端等待超时；这不表示后端 SQL 已停止。',
  cancelled: '已停止本次前端等待；这不表示后端任务已取消。',
  invalid_response: 'API 响应格式异常，未作为业务答案使用。',
};
function failure(reason: RequestFailure, requestId: string, httpStatus?: number): AskClientResult {
  return { kind: 'request_error', reason, message: messages[reason], requestId,
    ...(httpStatus === undefined ? {} : { httpStatus }) };
}
function normalize(payload: AskRequest): AskRequest {
  // Explicit public whitelist, even when JS callers bypass TypeScript.
  return {
    question: payload.question.trim(),
    profile_id: payload.profile_id.trim(),
    ...(payload.session_id === undefined ? {} : { session_id: payload.session_id?.trim() ?? null }),
    ...(payload.user_role === undefined ? {} : { user_role: payload.user_role?.trim() ?? null }),
    ...(payload.options === undefined ? {} : { options: { ...payload.options } }),
  };
}
function isJsonContentType(contentType: string): boolean {
  return contentType === 'application/json' || /^application\/[a-z0-9.+-]+\+json$/.test(contentType);
}
function safeJsonParse(text: string): unknown {
  try { return JSON.parse(text); } catch { return null; }
}
interface SseEvent { name: string; data: string }
// Minimal SSE framing: the backend only emits `event`/`data` fields, so comments and ids are ignored.
function takeSseEvents(buffer: string): { events: SseEvent[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = blocks.pop() ?? '';
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let name = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue;
      const separator = line.indexOf(':');
      const field = separator === -1 ? line : line.slice(0, separator);
      let value = separator === -1 ? '' : line.slice(separator + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') name = value;
      else if (field === 'data') data.push(value);
    }
    if (data.length > 0) events.push({ name, data: data.join('\n') });
  }
  return { events, rest };
}
function parseStageEvent(data: string): AskStage | null {
  const parsed: unknown = safeJsonParse(data);
  if (typeof parsed !== 'object' || parsed === null) return null;
  const { node, label } = parsed as { node?: unknown; label?: unknown };
  if (typeof node !== 'string' || typeof label !== 'string') return null;
  if (node.length === 0 || node.length > 64 || label.length === 0 || label.length > 120) return null;
  return { node, label };
}

export function createAskClient(settings: AskClientSettings = {}): StreamingAskClient {
  const fetcher = settings.fetch ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = settings.timeoutMs ?? 190_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 240_000) {
    throw new Error('Invalid frontend request timeout.');
  }

  // Shared by both endpoints, so a streamed result is validated exactly like a plain one.
  function classifyJson(body: unknown, payload: AskRequest, meta: ResponseMeta): AskClientResult {
    const { httpStatus, requestId, headerId, stopped } = meta;
    const returnedId = headerId ?? requestId;
    if (stopped) return failure(stopped, requestId, httpStatus);
    if (headerId !== null && headerId !== requestId) return failure('invalid_response', requestId, httpStatus);
    if (httpStatus === 200 && isAskResponse(body)) {
      if (body.request_id !== requestId) return failure('invalid_response', requestId, httpStatus);
      if (body.profile_id !== payload.profile_id.trim() ||
          (payload.session_id != null && body.session_id !== payload.session_id.trim())) {
        return failure('invalid_response', returnedId, httpStatus);
      }
      return { kind: 'business', response: body, httpStatus: 200, requestId: headerId ?? body.request_id };
    }
    if (httpStatus >= 400 && httpStatus <= 599 && isApiError(body)) {
      return { kind: 'api_error', error: body, httpStatus, requestId: returnedId };
    }
    return failure('invalid_response', returnedId, httpStatus);
  }

  const client: StreamingAskClient = {
    async ask(payload, options = {}) {
      const requestId = options.requestId ?? `req-web-${crypto.randomUUID()}`;
      if (!/^[A-Za-z0-9._:-]{1,128}$/.test(requestId) || !isAskRequest(payload)) {
        return failure('invalid_request', 'request-not-sent');
      }
      if (options.signal?.aborted) return failure('cancelled', requestId);
      const controller = new AbortController();
      let stopped: 'timeout' | 'cancelled' | null = null;
      let stopWaiting: (result: AskClientResult) => void = () => {};
      const stoppedResult = new Promise<AskClientResult>((resolve) => { stopWaiting = resolve; });
      function stop(reason: 'timeout' | 'cancelled') {
        if (stopped !== null) return;
        stopped = reason;
        stopWaiting(failure(reason, requestId));
        controller.abort();
      }
      const onCancel = () => stop('cancelled');
      options.signal?.addEventListener('abort', onCancel, { once: true });
      const timer = setTimeout(() => stop('timeout'), timeoutMs);

      async function exchange(): Promise<AskClientResult> {
        let httpStatus: number | undefined;
        let returnedId = requestId;
        let responseReceived = false;
        try {
          const response = await fetcher('/api/v1/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestId },
            credentials: 'same-origin',
            cache: 'no-store',
            redirect: 'error',
            body: JSON.stringify(normalize(payload)),
            signal: controller.signal,
          });
          responseReceived = true;
          httpStatus = response.status;
          const headerId = response.headers.get('X-Request-ID') || null;
          if (headerId) returnedId = headerId;
          const contentType = response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() ?? '';
          if (!isJsonContentType(contentType)) return failure('invalid_response', returnedId, httpStatus);
          return classifyJson(await response.json(), payload, { httpStatus, requestId, headerId, stopped });
        } catch {
          // Never expose the raw exception or an invalid server response body.
          return failure(stopped ?? (responseReceived ? 'invalid_response' : 'network'), returnedId, httpStatus);
        }
      }
      try {
        // Also bounds an uncooperative fetch/body reader; no automatic retry.
        return await Promise.race([exchange(), stoppedResult]);
      } finally {
        clearTimeout(timer);
        options.signal?.removeEventListener('abort', onCancel);
      }
    },
    async askStream(payload, options = {}, onStage) {
      const requestId = options.requestId ?? `req-web-${crypto.randomUUID()}`;
      if (!/^[A-Za-z0-9._:-]{1,128}$/.test(requestId) || !isAskRequest(payload)) {
        return failure('invalid_request', 'request-not-sent');
      }
      if (options.signal?.aborted) return failure('cancelled', requestId);
      const controller = new AbortController();
      let stopped: 'timeout' | 'cancelled' | null = null;
      let stopWaiting: (result: AskClientResult) => void = () => {};
      const stoppedResult = new Promise<AskClientResult>((resolve) => { stopWaiting = resolve; });
      function stop(reason: 'timeout' | 'cancelled') {
        if (stopped !== null) return;
        stopped = reason;
        stopWaiting(failure(reason, requestId));
        controller.abort();
      }
      const onCancel = () => stop('cancelled');
      options.signal?.addEventListener('abort', onCancel, { once: true });
      let timer = setTimeout(() => stop('timeout'), timeoutMs);
      // A stage event proves the connection is alive, so the bound here is idle-based, not total.
      function touch() {
        clearTimeout(timer);
        timer = setTimeout(() => stop('timeout'), timeoutMs);
      }

      async function stream(): Promise<AskClientResult> {
        let response: Response;
        try {
          response = await fetcher('/api/v1/ask/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-Request-ID': requestId },
            credentials: 'same-origin',
            cache: 'no-store',
            redirect: 'error',
            body: JSON.stringify(normalize(payload)),
            signal: controller.signal,
          });
        } catch {
          return failure(stopped ?? 'network', requestId);
        }
        const httpStatus = response.status;
        const headerId = response.headers.get('X-Request-ID') || null;
        const returnedId = headerId ?? requestId;
        const contentType = response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() ?? '';
        // Without the stream route the backend started no work, so the plain endpoint stays a safe fallback.
        if (stopped === null && (httpStatus === 404 || httpStatus === 405)) {
          return client.ask(payload, { ...options, requestId });
        }
        if (contentType !== 'text/event-stream') {
          if (!isJsonContentType(contentType)) return failure('invalid_response', returnedId, httpStatus);
          try {
            return classifyJson(await response.json(), payload, { httpStatus, requestId, headerId, stopped });
          } catch {
            return failure(stopped ?? 'invalid_response', returnedId, httpStatus);
          }
        }
        if (!response.body) return failure('invalid_response', returnedId, httpStatus);
        try {
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let outcome: AskClientResult | null = null;
          while (outcome === null && stopped === null) {
            const chunk = await reader.read();
            if (chunk.done) break;
            touch();
            buffer += decoder.decode(chunk.value, { stream: true });
            const framed = takeSseEvents(buffer);
            buffer = framed.rest;
            for (const event of framed.events) {
              if (event.name === 'stage') {
                const stage = parseStageEvent(event.data);
                if (stage) onStage?.(stage);
                continue;
              }
              const data: unknown = safeJsonParse(event.data);
              if (event.name === 'result') {
                outcome = classifyJson(data, payload, { httpStatus, requestId, headerId, stopped });
              } else if (event.name === 'error' && isApiError(data)) {
                // Transport already answered 200, so an interrupted stream is an unusable response, not an HTTP error.
                outcome = { kind: 'request_error', reason: 'invalid_response', message: data.message, requestId: returnedId };
              }
              if (outcome !== null) break;
            }
          }
          await reader.cancel().catch(() => {});
          if (stopped !== null) return failure(stopped, requestId, httpStatus);
          return outcome ?? failure('invalid_response', returnedId, httpStatus);
        } catch {
          return failure(stopped ?? 'invalid_response', returnedId, httpStatus);
        }
      }
      try {
        return await Promise.race([stream(), stoppedResult]);
      } finally {
        clearTimeout(timer);
        options.signal?.removeEventListener('abort', onCancel);
      }
    },
  };
  return client;
}
