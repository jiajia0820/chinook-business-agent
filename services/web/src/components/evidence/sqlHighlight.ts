export interface SqlToken { text: string; cls: string | null }

const KEYWORDS = new Set(['select', 'from', 'where', 'join', 'inner', 'left', 'right', 'full', 'outer', 'cross', 'on', 'and', 'or', 'not', 'in', 'is', 'null', 'as', 'group', 'by', 'order', 'limit', 'offset', 'distinct', 'having', 'union', 'all', 'case', 'when', 'then', 'else', 'end', 'between', 'like', 'escape', 'exists', 'with', 'values', 'insert', 'into', 'update', 'set', 'delete', 'create', 'table', 'view', 'primary', 'foreign', 'key', 'references', 'asc', 'desc', 'true', 'false']);
const FUNCTIONS = new Set(['sum', 'count', 'avg', 'min', 'max', 'round', 'abs', 'coalesce', 'ifnull', 'nullif', 'date', 'datetime', 'strftime', 'julianday', 'unixepoch', 'length', 'substr', 'trim', 'upper', 'lower', 'cast']);

// One pass: string literal (with '' escape), line comment, :param, number, word, then single fallback chars.
const TOKEN = /('(?:''|[^'])*')|(--[^\n]*)|(:[A-Za-z_]\w*)|(\d+(?:\.\d+)?)|([A-Za-z_]\w*)|([\s\S])/g;

/** Read-only display tokenizer: classifies SQL text, never evaluates or rewrites it. */
export function highlightSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  for (const match of sql.matchAll(TOKEN)) {
    const text = match[0] ?? '';
    const literal = match[1];
    const comment = match[2];
    const param = match[3];
    const number = match[4];
    const word = match[5];
    let cls: string | null = null;
    if (literal) cls = 'tok-str';
    else if (comment) cls = 'tok-comment';
    else if (param) cls = 'tok-param';
    else if (number) cls = 'tok-num';
    else if (word) {
      const lower = word.toLowerCase();
      if (KEYWORDS.has(lower)) cls = 'tok-kw';
      else if (FUNCTIONS.has(lower)) cls = 'tok-fn';
    }
    const last = tokens[tokens.length - 1];
    if (last && last.cls === cls) last.text += text;
    else tokens.push({ text, cls });
  }
  return tokens;
}
