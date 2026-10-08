/** Test-only recorded/synthetic responses. Not imported by src or production. */
import type { AskResponse, AskStatus } from '../src/contracts';
import type { AskClientResult, RequestFailure } from '../src/api/client';
import { recordedBusiness, sample } from './recorded-http';

export function business(status: AskStatus = 'answered', sessionId?: string): Extract<AskClientResult, { kind: 'business' }> {
  const record = recordedBusiness.find((item) => (item.body as AskResponse).status === status);
  if (!record) throw new Error(`No recorded ${status} response`);
  const response = structuredClone(record.body as AskResponse);
  if (sessionId !== undefined) response.session_id = sessionId;
  return { kind: 'business', httpStatus: 200, requestId: response.request_id, response };
}
export function clarificationPair() {
  const first = recordedBusiness.find((item) => item.case === 'clarification_missing_year');
  const second = recordedBusiness.find((item) => item.case === 'clarification_resume_true_capability_boundary');
  if (!first || !second) throw new Error('Missing actual clarification records');
  return [first, second].map((record) => {
    const response = structuredClone(record.body as AskResponse);
    return { kind: 'business' as const, httpStatus: 200 as const, requestId: response.request_id, response };
  }) as [Extract<AskClientResult, { kind: 'business' }>, Extract<AskClientResult, { kind: 'business' }>];
}
export function apiError(retryable = true, httpStatus = 503): Extract<AskClientResult, { kind: 'api_error' }> {
  return { kind: 'api_error', httpStatus, requestId: 'req-test-api',
    error: { code: 'BACKEND_UNAVAILABLE', message: '测试：API 暂不可用', retryable, details: null } };
}
export function requestError(reason: RequestFailure = 'network'): Extract<AskClientResult, { kind: 'request_error' }> {
  return { kind: 'request_error', reason, requestId: 'req-test-transport', message: '测试：未获得有效响应' };
}
export function resetResponse(sessionId = sample.session_id) {
  const result = business('clarification_required', sessionId);
  result.response.intent.name = 'reset_context';
  result.response.clarification = { question: '测试：已清空当前任务，请输入完整的新问题', missing_slots: [], options: [] };
  return result;
}
