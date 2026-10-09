import type { ApiError, AskRequest, AskResponse } from '../contracts';
import { isApiError, isAskRequest, isAskResponse } from '../contracts/validate';

export type RequestFailure = 'invalid_request' | 'network' | 'timeout' | 'cancelled' | 'invalid_response';
export type AskClientResult =
  | { kind: 'business'; response: AskResponse; httpStatus: 200; requestId: string }
  | { kind: 'api_error'; error: ApiError; httpStatus: number; requestId: string }
  | { kind: 'request_error'; reason: RequestFailure; message: string; requestId: string; httpStatus?: number };

export interface AskCallOptions { signal?: AbortSignal; requestId?: string }
export interface AskClient {
  ask(payload: AskRequest, options?: AskCallOptions): Promise<AskClientResult>;
}
export interface AskClientSettings { fetch?: typeof fetch; timeoutMs?: number }

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

export function createAskClient(settings: AskClientSettings = {}): AskClient {
  const fetcher = settings.fetch ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = settings.timeoutMs ?? 190_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 240_000) {
    throw new Error('Invalid frontend request timeout.');
  }

  return {
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
          const headerId = response.headers.get('X-Request-ID');
          if (headerId) returnedId = headerId;
          const contentType = response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() ?? '';
          if (contentType !== 'application/json' && !/^application\/[a-z0-9.+-]+\+json$/.test(contentType)) {
            return failure('invalid_response', returnedId, httpStatus);
          }
          const body: unknown = await response.json();
          if (stopped) return failure(stopped, requestId, httpStatus);
          if (headerId && headerId !== requestId) return failure('invalid_response', requestId, httpStatus);
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
  };
}
