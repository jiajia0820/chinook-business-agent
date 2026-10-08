/** Recorded 3C-5 responses: test input only, never a runtime API fallback. */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AskResponse } from '../src/contracts';
import { isAskResponse } from '../src/contracts/validate';

export interface RecordedHttp { case: string; http_status: number; request_id_header: string; body: unknown }
// Filesystem input, not a browser asset URL (also used by jsdom component tests).
const artifactPath = resolve(dirname(fileURLToPath(import.meta.url)), '../../../docs/examples/step-3c5-http-responses.json');
const artifact = JSON.parse(readFileSync(artifactPath, 'utf8')) as { records: Partial<RecordedHttp>[] };
export const recordedHttp = artifact.records.filter((item): item is RecordedHttp => typeof item.http_status === 'number');
export const recordedBusiness = recordedHttp.filter((item) => isAskResponse(item.body));
const first = recordedBusiness[0]?.body;
if (!isAskResponse(first)) throw new Error('Missing valid recorded SQL response.');
export const sample: AskResponse = first;
export const request = { question: '客户数量是多少？', profile_id: 'chinook-music' };
export function copySample(): AskResponse { return structuredClone(sample); }
export function jsonResponse(body: unknown, status = 200, headerId?: string): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...(headerId ? { 'X-Request-ID': headerId } : {}) },
  });
}
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}
