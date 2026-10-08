import type { AskRequest } from '../contracts';
import type { AskClient, AskClientResult } from './client';

export type LatestAskResult =
  | { kind: 'current'; sequence: number; result: AskClientResult }
  | { kind: 'stale'; sequence: number };

// Request sequencing only. It owns no business session or UI state.
export function createLatestAskRunner(client: AskClient) {
  let sequence = 0;
  let controller: AbortController | null = null;
  return {
    async run(payload: AskRequest): Promise<LatestAskResult> {
      const current = ++sequence;
      controller?.abort();
      const thisController = new AbortController();
      controller = thisController;
      try {
        const result = await client.ask(payload, { signal: thisController.signal });
        // Stale results do not expose response evidence to the rendering layer.
        return current === sequence
          ? { kind: 'current', sequence: current, result }
          : { kind: 'stale', sequence: current };
      } finally {
        if (controller === thisController) controller = null;
      }
    },
    cancel() {
      ++sequence;
      controller?.abort();
      controller = null;
    },
  };
}
