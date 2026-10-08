import { describe, expect, it, vi } from 'vitest';
import type { AskClient, AskClientResult } from '../src/api/client';
import { createLatestAskRunner } from '../src/api/latest-request';
import { copySample, deferred, request } from './recorded-http';

function result(): AskClientResult {
  const response = copySample();
  return { kind: 'business', response, requestId: response.request_id, httpStatus: 200 };
}
describe('latest request boundary, not a session/UI store', () => {
  it('marks an old late response stale without exposing its evidence', async () => {
    const old = deferred<AskClientResult>();
    const newest = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValueOnce(old.promise).mockReturnValueOnce(newest.promise);
    const runner = createLatestAskRunner({ ask });
    const first = runner.run(request);
    const second = runner.run({ ...request, question: '销售额口径是什么？' });
    expect(ask.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    newest.resolve(result());
    expect(await second).toMatchObject({ kind: 'current', sequence: 2 });
    old.resolve(result());
    expect(await first).toEqual({ kind: 'stale', sequence: 1 });
  });
  it('explicit cancellation invalidates a pending response', async () => {
    const old = deferred<AskClientResult>();
    const ask = vi.fn<AskClient['ask']>().mockReturnValueOnce(old.promise);
    const runner = createLatestAskRunner({ ask });
    const pending = runner.run(request);
    runner.cancel();
    expect(ask.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    old.resolve(result());
    expect(await pending).toEqual({ kind: 'stale', sequence: 1 });
  });
  it('preserves an API/request error as the current result', async () => {
    const error: AskClientResult = { kind: 'request_error', reason: 'network', message: '连接失败', requestId: 'req-unit' };
    const runner = createLatestAskRunner({ ask: async () => error });
    expect(await runner.run(request)).toEqual({ kind: 'current', sequence: 1, result: error });
  });
  it('does not share sequence state across independent runners', async () => {
    const client: AskClient = { ask: async () => result() };
    expect((await createLatestAskRunner(client).run(request)).sequence).toBe(1);
    expect((await createLatestAskRunner(client).run(request)).sequence).toBe(1);
  });
  it('can run again after cancellation and does not abort a completed call', async () => {
    const ask = vi.fn<AskClient['ask']>(async () => result());
    const runner = createLatestAskRunner({ ask });
    await runner.run(request);
    const firstSignal = ask.mock.calls[0]?.[1]?.signal;
    runner.cancel();
    expect(firstSignal?.aborted).toBe(false);
    expect(await runner.run(request)).toMatchObject({ kind: 'current', sequence: 3 });
  });
});
