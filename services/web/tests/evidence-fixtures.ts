/** Test input only: actual prior responses plus explicitly synthetic edge cases. */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AskResponse, DocumentChunk, SqlQueryResponse } from '../src/contracts';
import { isAskResponse } from '../src/contracts/validate';
import { recordedBusiness, sample } from './recorded-http';

const records = (JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../../docs/examples/step-3c4-responses.json'), 'utf8')) as { records: { response?: unknown }[] }).records;
export const recordedCalculations: AskResponse[] = records.map((item) => item.response).filter(isAskResponse).filter((response) => (response.calculations?.length ?? 0) > 0);
if (!recordedCalculations.length) throw new Error('Missing explicitly artificial calculation records');
export function actualResponse(question: string, occurrence = 0): AskResponse {
  const record = recordedBusiness.filter((item) => item.case === question)[occurrence];
  if (!record || !isAskResponse(record.body)) throw new Error('Missing actual recorded response');
  return structuredClone(record.body);
}
export function sqlResult(): SqlQueryResponse { return structuredClone(sample.sql_results![0]!); }
export function documentChunk(): DocumentChunk { return actualResponse('销售额口径是什么？').documents![0]!; }
export function artificialCalculation(): AskResponse { return structuredClone(recordedCalculations[0]!); }
