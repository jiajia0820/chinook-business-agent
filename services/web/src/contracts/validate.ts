import Ajv2020 from 'ajv/dist/2020.js';
import schema from './generated/validation-schema.json';
import type { ApiError, AskRequest, AskResponse, JsonValue } from './index';

const ajv = new Ajv2020({
  strict: true,
  allErrors: false,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
});
ajv.addSchema(schema);
const requestSchema = ajv.compile<AskRequest>({ $ref: `${schema.$id}#/$defs/AskRequest` });
const responseSchema = ajv.compile<AskResponse>({ $ref: `${schema.$id}#/$defs/AskResponse` });
const errorSchema = ajv.compile<ApiError>({ $ref: `${schema.$id}#/$defs/ApiError` });

// The exported JsonValue schema is {}. Enforce finite, acyclic JSON values
// without coercing, deleting fields, or accepting arbitrary JS objects.
export function isJsonValue(value: unknown): value is JsonValue {
  const ancestors = new WeakSet<object>();
  function visit(item: unknown, depth: number): boolean {
    if (depth > 128) return false;
    if (item === null || typeof item === 'boolean' || typeof item === 'string') return true;
    if (typeof item === 'number') return Number.isFinite(item);
    if (typeof item !== 'object' || ancestors.has(item)) return false;
    const prototype = Object.getPrototypeOf(item);
    if (!Array.isArray(item) && prototype !== Object.prototype && prototype !== null) return false;
    ancestors.add(item);
    const valid = Array.isArray(item)
      ? Array.from(item).every((child) => visit(child, depth + 1))
      : Object.values(item).every((child) => visit(child, depth + 1));
    ancestors.delete(item);
    return valid;
  }
  try { return visit(value, 0); } catch { return false; }
}

export function isAskRequest(value: unknown): value is AskRequest {
  if (!isJsonValue(value) || !requestSchema(value)) return false;
  return [value.question, value.profile_id, value.session_id, value.user_role]
    .every((item) => item === undefined || item === null || item.trim().length > 0);
}

export function isApiError(value: unknown): value is ApiError {
  return isJsonValue(value) && errorSchema(value);
}

export function isAskResponse(value: unknown): value is AskResponse {
  if (!isJsonValue(value) || !responseSchema(value)) return false;
  // Pydantic model_validator rules are not represented by JSON Schema.
  if (value.status === 'answered' && !value.answer) return false;
  if (value.status === 'clarification_required' && (value.route !== 'clarification' || value.clarification === null)) return false;
  if (value.status === 'unsupported' && value.route !== 'unsupported') return false;
  if ((value.status === 'error') !== (value.error !== null)) return false;
  for (const sql of value.sql_results ?? []) {
    if (sql.status === 'success' ? sql.error != null : sql.error == null) return false;
  }
  return true;
}
