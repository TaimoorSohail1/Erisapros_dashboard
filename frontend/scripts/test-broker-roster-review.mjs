import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import React,{act,useState,useRef,useEffect} from 'react';
import {JSDOM} from 'jsdom';
import ts from 'typescript';

const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
const {createRoot}=await import('react-dom/client');
const source=await readFile(new URL('../src/ui/BrokerRosterPanel.tsx',import.meta.url),'utf8');
const code=ts.transpileModule(source.replace(/^import .*$/gm,'').replace(/^export /gm,''),{
  compilerOptions:{module:ts.ModuleKind.None,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022}}).outputText;
const Panel=runInNewContext(code+'\nBrokerRosterPanel',{React,useState,useEffect,useRef,Intl,Number});
const root=createRoot(document.getElementById('root'));
let saves=[],key=0;
const roster={state:'NEEDS_REVIEW',mode:'PRESERVE',existing_count:1,replacement_count:0,
  review_context_sha256:'a'.repeat(64),reasons:['Source coverage needs confirmation'],evidence:[],
  other_disclosures:[{id:'mercer-proof',name:'Mercer Health & Benefits LLC',address:'4565 PAYSPHERE CIRCLE CHICAGO IL 60674',amount:'240148.74',page:5,source_text:'Original Schedule C disclosure'}]};
const props={busy:false,rows:[],roster,currentRows:[{NameXX:'Existing broker'}],
  onSaveReview:async(rows,review)=>{saves.push({rows,review});return false;}};
const buttons=()=>[...document.querySelectorAll('button')];
const button=text=>{const element=buttons().find(b=>b.textContent===text);assert.ok(element,text);return element;};
const click=async text=>{await act(async()=>button(text).click());};
const render=async(overrides={},same=false)=>{if(!same)key++;await act(async()=>root.render(React.createElement(Panel,{...props,...overrides,key})));};
const input=label=>{const e=document.querySelector(`[aria-label="${label}"]`);assert.ok(e,label);return e;};
const set=async(element,value)=>{
  const type=element.tagName==='SELECT'?'HTMLSelectElement':element.tagName==='TEXTAREA'?'HTMLTextAreaElement':'HTMLInputElement';
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window[type].prototype,'value').set.call(element,value);
    element.dispatchEvent(new dom.window.Event(element.tagName==='SELECT'?'change':'input',{bubbles:true}));});
  // Other UI suites may have initialized ReactDOM against another JSDOM.
  // In that case exercise this controlled input's public change callback.
  const propsKey=Object.keys(element).find(key=>key.startsWith('__reactProps'));
  if(element[propsKey]?.value!==value)await act(async()=>element[propsKey].onChange({target:{value}}));
};
const submit=async()=>{await act(async()=>document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true})));};
const choose=async value=>{const e=document.querySelector(`input[type="radio"][value="${value}"]`) ?? [...document.querySelectorAll('input[type="radio"]')][value==='INCLUDED'?0:1];await act(async()=>e.click());};
const row={name:'Source broker',address_line_1:'10 Main St',city:'Boston',state:'MA',zip_code:'02110',organization_code:'0',commission_total:'100',fee_total:'-100'};
const sourceProps={rows:[row],roster:{...roster,other_disclosures:[]}};

// Visible information and the two requested actions, without a second large form.
await render();
assert.match(document.body.textContent,/Mercer Health & Benefits LLC/);
assert.match(document.body.textContent,/\$240,148\.74/);
assert.match(document.body.textContent,/Paused:/);
assert.match(document.body.textContent,/Unclassified/);
assert.equal(buttons().filter(b=>b.textContent==='Edit').length,1);
assert.equal(buttons().filter(b=>b.textContent==='Approve').length,1);
assert.equal(button('Approve').disabled,true,'Unclassified payments cannot be approved');
assert.equal(document.querySelectorAll('form,textarea,select,details').length,0,'Main view contains only the row and short status');
await click('Edit');
assert.equal(document.activeElement.id,'broker-review-heading');
assert.match(document.body.textContent,/View evidence/);
assert.match(document.body.textContent,/Current brokers \(1\)/);
assert.equal(buttons().some(b=>b.textContent==='Add broker'),false);
assert.equal(document.querySelectorAll('textarea').length,0,'Unknown payments do not expose an empty-roster form');
await choose('INCLUDED');
assert.equal(input('Commission').value,'');assert.equal(input('Fees').value,'');
await click('Cancel');assert.equal(saves.length,0,'Opening, choosing and cancelling never save or authorize an update');
assert.match(document.body.textContent,/Unclassified/);

// Edit saves a local draft; Approve alone sends the complete, source-bound review.
await render(sourceProps);
assert.equal(button('Approve').disabled,false);
await click('Edit');assert.equal(input('Organization code').value,'0');
await set(input('Commission'),'200');await set(input('Fees'),'-200');
assert.equal(input('Purpose').value,'COMMISSIONS AND FEES');
await submit();assert.equal(saves.length,0,'Save changes is local only');
assert.equal(document.querySelector('form'),null);
assert.match(document.body.textContent,/\$200\.00/);
await click('Approve');assert.equal(saves.length,1);
assert.equal(saves[0].rows[0].commission_total,'200');
assert.equal(saves[0].rows[0].purpose,'COMMISSIONS AND FEES');
assert.equal(saves[0].review.complete_list_confirmed,true);
assert.equal(saves[0].review.expected_context,roster.review_context_sha256);
assert.match(saves[0].review.reason,/Reviewer approved the complete displayed/);
assert.doesNotMatch(saves[0].review.reason,/Client confirmed/,'Approval provenance does not invent a client instruction');
assert.match(document.body.textContent,/edits are retained/);
await click('Edit');assert.equal(input('Commission').value,'200');await click('Cancel');
await render({...sourceProps,roster:{...sourceProps.roster,review_context_sha256:'b'.repeat(64)}},true);
assert.equal(button('Approve').disabled,true,'A changed source or vendor snapshot invalidates approval');
assert.equal(saves.length,1);

// Disclosure allocation must equal its original amount, using exact cents.
await render();await click('Edit');await choose('INCLUDED');
await set(input('Commission'),'0');await set(input('Fees'),'240148.73');
for(const [label,value] of [['Street','4565 PAYSPHERE CIRCLE'],['City','CHICAGO'],['State','IL'],['ZIP','60674']])await set(input(label),value);
await submit();assert.match(document.body.textContent,/must equal the original/);assert.ok(document.querySelector('form'));
await set(input('Fees'),'240148.74');await submit();
assert.equal(button('Approve').disabled,false);
await click('Approve');assert.equal(saves.length,2);
assert.equal(saves[1].review.disclosure_decisions[0].treatment,'INCLUDED');
assert.equal(saves[1].review.row_sources[0],'DISCLOSURE:mercer-proof');
assert.equal(saves[1].rows[0].purpose,'FEES');

// Supporting-only does not silently authorize deletion of the current broker.
await render();await click('Edit');await choose('SUPPORTING_ONLY');
await submit();assert.equal(button('Approve').disabled,true);assert.equal(saves.length,2);
await click('Edit');await set(document.querySelector('textarea'),'Confirmed instruction: no reportable Schedule A recipients'.padEnd(2000,'x'));
await submit();await click('Approve');assert.equal(saves.length,3);
assert.equal(saves[2].rows.length,0);assert.match(saves[2].review.empty_list_evidence,/no reportable/);
assert.ok(saves[2].review.reason.length<=2000,'Approval provenance fits the existing backend reason limit');

// An exclusion is explicit; all remaining source rows are shown and approved together.
await render({rows:[row,{...row,name:'Excluded source broker'}],roster:{...roster,other_disclosures:[]}});
assert.equal(buttons().filter(b=>b.textContent==='Approve').length,1);
await act(async()=>buttons().filter(b=>b.textContent==='Edit')[1].click());
await act(async()=>document.querySelector('input[type="checkbox"]').click());await submit();await click('Approve');
assert.equal(saves.length,4);assert.equal(saves[3].rows.length,1);assert.equal(saves[3].review.excluded_source_indexes[0],1);

// Refresh must never approve unseen source/destination changes or open another row by old index.
const fresh={schedule_a_broker_rows:[{...row,name:'Refreshed broker'}],schedule_a_broker_roster:{...sourceProps.roster,review_context_sha256:'c'.repeat(64)}};
await render({...sourceProps,onRefreshReview:async()=>fresh});await click('Approve');
assert.equal(saves.length,4);assert.match(document.body.textContent,/Check the displayed list/);
await render({...sourceProps,onRefreshReview:async()=>fresh});await click('Edit');
assert.equal(document.querySelector('form'),null);assert.match(document.body.textContent,/Check the displayed rows/);

// Successful approval and an already verified list introduce no extra broker decision.
let resolveSave;const delayed=new Promise(resolve=>{resolveSave=resolve;});let attempts=0;
await render({...sourceProps,onSaveReview:async()=>{attempts++;return delayed;}});
await act(async()=>{button('Approve').click();button('Approve').click();});
assert.equal(attempts,1,'Double-click cannot submit duplicate approvals');
assert.equal(button('Please wait…').disabled,true);
await act(async()=>resolveSave(true));assert.equal(button('Approved').disabled,true);
assert.match(document.body.textContent,/other filing reviews are clear/);
await render({...sourceProps,roster:{...sourceProps.roster,mode:'REPLACE',replacement_count:1}});
assert.equal(button('Approved').disabled,true);
await render({rows:[{...row,commission_total:'100,901.12',fee_total:'(100,901.12)'}],roster:{...sourceProps.roster,mode:'REPLACE'}});
assert.match(document.body.textContent,/COMMISSIONS AND FEES/);assert.doesNotMatch(document.body.textContent,/NaN/);
await render({rows:[{...row,commission_total:'90071992547409.91',fee_total:'0'}],roster:{...sourceProps.roster,mode:'REPLACE'}});
assert.match(document.body.textContent,/\$90,071,992,547,409\.91/,'Displaying a large amount must not round away cents');
// A broker draft may be edited before selecting a target, but cannot be approved.
let targetReviews=0;
const destinationReason='The policy number may refer to existing Schedule A #7. Select it or explicitly confirm a separate new Schedule A.';
await render({...sourceProps,roster:{...sourceProps.roster,destination_ready:false,destination_reason:destinationReason},onReviewDestination:()=>{targetReviews++;}});
assert.match(document.body.textContent,/existing Schedule A #7/);
assert.equal(button('Approve').disabled,true);
await click('Review Schedule A');assert.equal(targetReviews,1);
await click('Edit');assert.ok(document.querySelector('form'));await click('Cancel');
// Destination readiness can change on refresh even if source/snapshot hashes did not.
await render({...sourceProps,onRefreshReview:async()=>({schedule_a_broker_rows:[row],schedule_a_broker_roster:{...sourceProps.roster,destination_ready:false,destination_reason:destinationReason}})});
await click('Approve');assert.equal(saves.length,4);assert.match(document.body.textContent,/existing Schedule A #7/);
assert.equal(button('Approve').disabled,true);
// A genuinely new, verified target uses the same approval path without a sequence.
await render({...sourceProps,roster:{...sourceProps.roster,destination_ready:true},onSaveReview:async()=>true});
await click('Approve');assert.equal(button('Approved').disabled,true);
// Reproduce the Lincoln UI: valid payments must not hide source identity holds.
const identityHold={validator:'recipient_identity_ambiguity',status:'ERROR',reason:'Distinct printed addresses may be abbreviations of the same recipient. Preserve the physical rows and confirm aggregation before an update.'};
const heldRows=[{...row,name:'Mercer',address_line_1:'4565 PAYSPHERE CIR',commission_total:'4035.74',fee_total:'0',validation_results:[identityHold]},
  {...row,name:'Mercer',address_line_1:'4565 PAYSPHERE CIRCLE',commission_total:'0',fee_total:'1133.09',validation_results:[identityHold]}];
let heldSaves=0;
await render({...sourceProps,rows:heldRows,onSaveReview:async()=>{heldSaves++;return true;}});
assert.match(document.body.textContent,/Paused:/);
assert.match(document.body.textContent,/Confirm whether these entries belong to one broker or separate recipients/);
assert.doesNotMatch(document.body.textContent,/Ready for approval/);
assert.equal(button('Approve').disabled,true);
await click('Approve');assert.equal(heldSaves,0,'Do not submit a review the server must reject');
await act(async()=>buttons().filter(b=>b.textContent==='Edit')[0].click());
assert.match(document.querySelector('form').textContent,/Distinct printed addresses/);
await set(input('Commission'),'4035.75');await submit();
assert.equal(button('Approve').disabled,true,'Editing payment values does not confirm recipient identity');
assert.doesNotMatch(document.body.textContent,/Changes ready\. Click Approve/);
await render({...sourceProps,rows:heldRows,roster:{...sourceProps.roster,mode:'REPLACE'}});
assert.doesNotMatch(document.body.textContent,/Broker list approved/,'An old roster mode cannot hide current source errors');
// Newly returned source holds must be visible before attempting a save.
await render({...sourceProps,onRefreshReview:async()=>({schedule_a_broker_rows:[heldRows[0]],schedule_a_broker_roster:sourceProps.roster}),onSaveReview:async()=>{heldSaves++;return true;}});
await click('Approve');assert.equal(heldSaves,0);assert.equal(button('Approve').disabled,true);
assert.match(document.body.textContent,/Confirm whether these entries belong to one broker or separate recipients/);
// Unknown future blocking validators retain their actual explanation.
await render({...sourceProps,rows:[{...row,validation_results:[{validator:'future_source_check',status:'ERROR',reason:'Source payment evidence is inconsistent.'}]}]});
assert.match(document.body.textContent,/Source payment evidence is inconsistent/);assert.equal(button('Approve').disabled,true);
// The four checks explicitly resolved by whole-list review must remain reviewable.
await render({...sourceProps,rows:[{...row,validation_results:['complete_source_broker_roster','payment_classification_ambiguity','business_rule_confirmation','source_section_ambiguity'].map(validator=>({validator,status:'ERROR',reason:'Needs full-list review'}))}]});
assert.equal(button('Approve').disabled,false);
if(process.env.BROKER_UI_REPLAY_FILE){
  const captured=JSON.parse(await readFile(process.env.BROKER_UI_REPLAY_FILE,'utf8'));
  await render({...sourceProps,rows:captured.source_rows});
  assert.match(document.body.textContent,/Paused: Confirm whether these entries belong to one broker or separate recipients/);
  assert.doesNotMatch(document.body.textContent,/Ready for approval/);
  assert.equal(button('Approve').disabled,true);
  assert.equal(document.querySelectorAll('tbody tr').length,captured.source_rows.length);
  if(process.env.BROKER_UI_EVIDENCE_FILE)await writeFile(process.env.BROKER_UI_EVIDENCE_FILE,document.documentElement.outerHTML,'utf8');
}
await act(async()=>root.unmount());
console.log('Simple broker UI: Edit/Approve, source-error visibility and approval gating, no Add broker, drafts/cancel, exact classification, complete-list approval, exclusions/empty evidence, stale refresh, failed save, duplicate prevention and verified-list compatibility passed.');
