import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const webRoot = fileURLToPath(new URL('../', import.meta.url));
async function filesIn(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  return (await Promise.all(entries.map((entry) => {
    const location = path.join(directory, entry.name);
    return entry.isDirectory() ? filesIn(location) : [location];
  }))).flat();
}
const files = [...await filesIn(path.join(webRoot, 'src')), ...await filesIn(path.join(webRoot, 'dist'))];
const forbidden = [
  /LLM_(?:API_KEY|BASE_URL|MODEL)/, /C_SQL_MODEL_/, /sk-[A-Za-z0-9_-]{24,}/, /VITE_.*(?:KEY|SECRET|TOKEN)/,
  /recorded-http|workspace-fixtures|evidence-fixtures|fixtures_3c3|step-3c[345](?:-http)?-responses\.json/,
];
for (const filename of files) {
  const content = await readFile(filename, 'utf8');
  if (forbidden.some((pattern) => pattern.test(content))) {
    // Print only the filename, never any matching secret value.
    throw new Error(`Browser configuration boundary violated: ${path.relative(webRoot, filename)}`);
  }
}
console.log(`Browser boundary checked: ${files.length} source/build files; no model settings, key-shaped values or test fixture imports.`);
