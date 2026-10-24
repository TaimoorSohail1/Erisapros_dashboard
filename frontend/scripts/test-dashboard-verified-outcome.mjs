import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const page = await readFile(new URL('../src/pages/DashboardPage.tsx', import.meta.url), 'utf8');
const source = page.slice(page.indexOf('function dashboardUpdateMode('), page.indexOf('function automationPriority('));
const runtime = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const [mode, state] = runInNewContext(runtime + '\n[dashboardUpdateMode,dashboardAutomationState]',
    {automationIsActive:status=>status==='PROCESSING'});
const verified = {automation_status:'COMPLETED', will_update_count:0, verified_update_count:5,
    verified_update_at:'2026-10-24T00:00:00Z', verified_receipt_id:'receipt', verified_update_action:'SCHEDULE_A_UPDATED'};
assert.equal(mode(verified).label, 'Automatically updated');
assert.match(state(verified).detail, /5 FT Williams fields updated/);
assert.doesNotMatch(state({...verified,automation_reasons:['No changes needed']}).detail, /No changes needed/);
const legacy = {automation_status:'COMPLETED',will_update_count:0};
assert.equal(mode(legacy).label,'Automatically completed');
assert.doesNotMatch(state(legacy).detail,/No FT Williams changes were needed/);
const noop = {...legacy,verified_update_count:0,verified_update_at:'2026-10-24T00:00:00Z',verified_update_action:'NO_CHANGE'};
assert.equal(mode(noop).label,'Automatically verified');
assert.match(state(noop).detail,/No FT Williams changes were needed/);
assert.doesNotMatch(state({...verified,verified_update_count:0}).detail,/No FT Williams changes were needed/);
console.log('Verified outcome dashboard behavior passed.');
