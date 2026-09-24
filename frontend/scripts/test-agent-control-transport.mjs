import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Execute the actual exported request function, not a source-pattern assertion.
const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  .replace(/^import .*from ["'].*["'];?\r?\n/gm, '')
  .replace(/import\.meta\.env/g, '({})')
  .replace(/export /g, '');
const calls = [];
const fetchProbe = async (url, options) => {
  calls.push({url, options});
  const actualHeader = new Headers(options.headers).get('Content-Type');
  assert.equal(actualHeader, 'application/json', 'Pause/Resume JSON must not be sent as text/plain.');
  return {ok:true, json:async()=>({device_id:'synthetic/device', status:'PAUSING'})};
};
const getControl = new Function('getIdToken', 'fetch', 'Headers', compiled + '\nreturn setFTWLocalAgentPaused;');
const control = getControl(async()=> 'synthetic-token', fetchProbe, Headers);
for (const paused of [true, false]) {
  await control('synthetic/device', paused);
  const {url, options} = calls.at(-1);
  assert.ok(url.endsWith('/local-agent/devices/synthetic%2Fdevice/control'));
  assert.equal(options.method, 'POST');
  assert.deepEqual(JSON.parse(options.body), {paused});
  assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer synthetic-token');
}
console.log('Actual agent-control transport passed for Pause and Resume.');
