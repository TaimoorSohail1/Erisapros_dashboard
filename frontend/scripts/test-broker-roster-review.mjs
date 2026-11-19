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


// Verified source rosters, with blank or complete addresses, require no approval.
for (const addressValues of [{address_line_1:'',city:'',state:'',zip_code:''}, {}]) {
  await render({...sourceProps,rows:[{...row,...addressValues}],roster:{...sourceProps.roster,mode:'REPLACE'}});
  assert.match(document.body.textContent,/Ready for automatic sending/);
  assert.equal(buttons().some(b=>b.textContent==='Approve'),false);
}
// Editor persistence is the confirmation: there is no second approval click.
await render({...sourceProps,onSaveReview:async(rows,review)=>{saves.push({rows,review});return true;}});
await click('Edit');await set(input('Commission'),'200');await set(input('Fees'),'-200');
await click('Save and continue');assert.equal(saves.length,1);
assert.equal(saves[0].rows[0].purpose,'COMMISSIONS AND FEES');
assert.equal(saves[0].review.expected_context,roster.review_context_sha256);
assert.equal(saves[0].review.complete_list_confirmed,true);
assert.equal(saves[0].review.accept_partial_addresses,false);
assert.equal(document.querySelector('form'),null);
assert.match(document.body.textContent,/Sending continues automatically/);
// Payment allocation and explicit supporting-only classification remain exact.
await render({onSaveReview:async(rows,review)=>{saves.push({rows,review});return true;}});
await click('Edit');await choose('INCLUDED');
await set(input('Commission'),'0');await set(input('Fees'),'240148.73');
await submit();assert.match(document.body.textContent,/must equal the original/);assert.equal(saves.length,1);
await set(input('Fees'),'240148.74');await submit();assert.equal(saves.length,2);
assert.equal(saves[1].review.disclosure_decisions[0].treatment,'INCLUDED');
assert.equal(saves[1].review.row_sources[0],'DISCLOSURE:mercer-proof');
await render({onSaveReview:async(rows,review)=>{saves.push({rows,review});return true;}});
await click('Edit');await choose('SUPPORTING_ONLY');
await set(document.querySelector('textarea'),'Confirmed instruction: no reportable Schedule A recipients');
await submit();assert.equal(saves.length,3);assert.equal(saves[2].rows.length,0);
assert.match(saves[2].review.empty_list_evidence,/no reportable/);
// Failed saves retain the editor, entered values, and source-bound context.
await render(sourceProps);await click('Edit');await set(input('Commission'),'201');await submit();
assert.equal(saves.length,4);assert.ok(document.querySelector('form'));
assert.equal(input('Commission').value,'201');assert.match(document.body.textContent,/edits are retained/);
await click('Cancel');
// Stale snapshots and destination locks cannot save or automatically send.
let blockedSaves=0,refreshes=0;
const changed={...sourceProps.roster,review_context_sha256:'c'.repeat(64)};
await render({...sourceProps,onRefreshReview:async()=>({schedule_a_broker_rows:[row],schedule_a_broker_roster:++refreshes===1?sourceProps.roster:changed}),onSaveReview:async()=>{blockedSaves++;return true;}});
await click('Edit');await submit();assert.equal(blockedSaves,0);assert.match(document.body.textContent,/data changed/);
let targetReviews=0;
const destinationReason='The policy number may refer to existing Schedule A #7. Select it or explicitly confirm a separate new Schedule A.';
await render({...sourceProps,roster:{...sourceProps.roster,destination_ready:false,destination_reason:destinationReason},onReviewDestination:()=>{targetReviews++;}});
await click('Approve');assert.equal(targetReviews,1);
// Source identity regressions below exercise the unchanged classification controls.
const identityBaseline=saves.length;
// Reproduce the Lincoln UI: valid payments must not hide source identity holds.
const identityHold={validator:'recipient_identity_ambiguity',status:'ERROR',reason:'Distinct printed addresses may be abbreviations of the same recipient. Preserve the physical rows and confirm aggregation before an update.'};
const heldRows=[{...row,name:'Mercer',address_line_1:'4565 PAYSPHERE CIR',commission_total:'4035.74',fee_total:'0',validation_results:[identityHold]},
  {...row,name:'Mercer',address_line_1:'4565 PAYSPHERE CIRCLE',commission_total:'0',fee_total:'1133.09',validation_results:[identityHold]}];
let heldSaves=0;
await render({...sourceProps,rows:heldRows,onSaveReview:async()=>{heldSaves++;return true;}});
assert.match(document.body.textContent,/Paused:/);
assert.match(document.body.textContent,/Confirm whether these entries belong to one broker or separate recipients/);
assert.doesNotMatch(document.body.textContent,/Ready for approval/);
assert.equal(button('Approve').disabled,false);
await click('Approve');assert.equal(heldSaves,0,'Do not submit a review the server must reject');
assert.match(document.querySelector('form').textContent,/Distinct printed addresses/);
await set(input('Commission'),'4035.75');await submit();assert.ok(document.querySelector('form'));await click('Cancel');
assert.equal(button('Approve').disabled,false,'Editing payment values still leaves identity review available');
assert.equal(heldSaves,0);
assert.doesNotMatch(document.body.textContent,/Changes ready\. Click Approve/);
await render({...sourceProps,rows:heldRows,roster:{...sourceProps.roster,mode:'REPLACE'}});
assert.doesNotMatch(document.body.textContent,/Broker list approved/,'An old roster mode cannot hide current source errors');
// Newly returned source holds must be visible before attempting a save.
await render({...sourceProps,onRefreshReview:async()=>({schedule_a_broker_rows:[heldRows[0]],schedule_a_broker_roster:sourceProps.roster}),onSaveReview:async()=>{heldSaves++;return true;}});
await click('Approve');assert.equal(heldSaves,0);assert.equal(button('Approve').disabled,false);
assert.match(document.body.textContent,/Confirm whether these entries belong to one broker or separate recipients/);
// Unknown future blocking validators retain their actual explanation.
await render({...sourceProps,rows:[{...row,validation_results:[{validator:'future_source_check',status:'ERROR',reason:'Source payment evidence is inconsistent.'}]}]});
assert.match(document.body.textContent,/Source payment evidence is inconsistent/);assert.equal(button('Approve').disabled,false);
await click('Approve');assert.match(document.querySelector('form').textContent,/Source payment evidence is inconsistent/);
// The four checks explicitly resolved by whole-list review must remain reviewable.
await render({...sourceProps,rows:[{...row,validation_results:['complete_source_broker_roster','payment_classification_ambiguity','business_rule_confirmation','source_section_ambiguity'].map(validator=>({validator,status:'ERROR',reason:'Needs full-list review'}))}]});
assert.equal(button('Approve').disabled,false);
// Saving identity is a separate persisted action; it never approves the roster.
const identityGroup={id:'identity-proof',name:'Mercer',source_indexes:[0,1],address_options:[
  {source_index:0,address:'4565 PAYSPHERE CIR, CHICAGO, IL, 60674'},
  {source_index:1,address:'4565 PAYSPHERE CIRCLE, CHICAGO, IL, 60674'}],
  commission_total:'4035.74',fee_total:'1133.09',choice:null,address_source_index:null};
const identityRoster={...sourceProps.roster,identity_groups:[identityGroup]};
// Older saved reviews have no grouping metadata. Refresh it through the read-only
// review endpoint and open the current server-generated group without reuploading.
await render({...sourceProps,rows:heldRows,onRefreshReview:async()=>({schedule_a_broker_rows:heldRows,schedule_a_broker_roster:{...identityRoster,review_context_sha256:'e'.repeat(64)}})});
await click('Confirm identity');assert.ok(document.querySelector('#broker-identity-editor'));
assert.equal(button('Approve').disabled,true);assert.equal(saves.length,identityBaseline);
let identitySaves=[];
const identityProps={...sourceProps,rows:heldRows,roster:identityRoster,onSaveIdentityReview:async request=>{
  identitySaves.push(request);return {schedule_a_broker_rows:heldRows,schedule_a_broker_roster:{...identityRoster,
    review_context_sha256:'d'.repeat(64),identity_groups:[{...identityGroup,choice:request.choice,address_source_index:request.address_source_index,confirmed_by:'reviewer'}]}};}};
await render(identityProps);await click('Confirm identity');
assert.ok(document.querySelector('#broker-identity-editor fieldset.broker-payment-treatment'),'Identity choices use the existing stacked accessible form layout');
assert.equal(button('Save confirmation').disabled,true);
await choose('SAME');
await set(input('Address to use'),'1');
assert.match(document.querySelector('#broker-identity-editor').textContent,/\$4,035\.74/);
assert.match(document.querySelector('#broker-identity-editor').textContent,/\$1,133\.09/);
assert.match(document.querySelector('#broker-identity-editor').textContent,/COMMISSIONS AND FEES/);
await act(async()=>document.querySelector('#broker-identity-editor input[type="checkbox"]').click());
await click('Save confirmation');assert.equal(identitySaves.length,1);
assert.equal(identitySaves[0].choice,'SAME');assert.equal(identitySaves[0].address_source_index,1);
assert.equal(identitySaves[0].verified,true);assert.equal(saves.length,4,'Saving identity does not approve the broker roster');
const savedIdentity={...identityRoster,review_context_sha256:'d'.repeat(64),identity_groups:[{...identityGroup,choice:'SAME',address_source_index:1,confirmed_by:'reviewer'}]};
await render({...identityProps,roster:savedIdentity},true);
assert.equal(button('Approve').disabled,false);
assert.match(document.body.textContent,/Identity confirmed: same broker/i);
await render({...identityProps,roster:savedIdentity});
assert.equal(button('Approve').disabled,false,'Saved identity remains usable after a reload');
await act(async()=>buttons().filter(b=>b.textContent==='Edit')[0].click());
await set(input('Commission'),'999');await submit();await click('Cancel');
assert.equal(button('Approve').disabled,false,'Changed reviewed values can reopen identity review');
await render({...identityProps,onSaveIdentityReview:async()=>null});
await click('Confirm identity');await choose('SEPARATE');
await act(async()=>document.querySelector('#broker-identity-editor input[type="checkbox"]').click());
await click('Save confirmation');assert.ok(document.querySelector('#broker-identity-editor'));
assert.match(document.body.textContent,/not saved/);assert.equal(button('Save confirmation').disabled,false);
await click('Cancel identity review');assert.equal(identitySaves.length,1);
await render(identityProps);await click('Confirm identity');await choose('SEPARATE');
await act(async()=>document.querySelector('#broker-identity-editor input[type="checkbox"]').click());
await click('Save confirmation');assert.equal(identitySaves.length,2);
assert.equal(identitySaves[1].choice,'SEPARATE');assert.equal(identitySaves[1].address_source_index,null);
const separateIdentity={...identityRoster,review_context_sha256:'d'.repeat(64),identity_groups:[{...identityGroup,choice:'SEPARATE',address_source_index:null,confirmed_by:'reviewer'}]};
let separateApprovals=0;
await render({...identityProps,roster:separateIdentity,onSaveReview:async()=>{separateApprovals++;return true;}},true);
assert.equal(button('Approve').disabled,false);await click('Approve');assert.equal(separateApprovals,1);
// A stale refresh cannot save an identity decision against a changed destination.
let identityRefreshes=0;
await render({...identityProps,onRefreshReview:async()=>({schedule_a_broker_rows:heldRows,schedule_a_broker_roster:++identityRefreshes===1?identityRoster:{...identityRoster,review_context_sha256:'e'.repeat(64)}})});
await click('Confirm identity');await choose('SEPARATE');
await act(async()=>document.querySelector('#broker-identity-editor input[type="checkbox"]').click());
await click('Save confirmation');assert.equal(identitySaves.length,2);assert.match(document.body.textContent,/data changed/);
// Optional broker addresses: no approval for verified rows; explicit as-is for partial data.
for (const addressValues of [{address_line_1:'',city:'',state:'',zip_code:''}, {}]) {
  await render({...sourceProps,rows:[{...row,...addressValues}],roster:{...sourceProps.roster,mode:'REPLACE'}});
  assert.match(document.body.textContent,/Ready for automatic sending/);
  assert.equal(buttons().some(b=>b.textContent==='Approve'),false);
}
let addressSaves=[];
const partialRow={...row,address_line_1:'',state:'',zip_code:'',organization_code:null};
const addressProps={...sourceProps,rows:[partialRow],onSaveReview:async(rows,review)=>{addressSaves.push({rows,review});return true;}};
await render(addressProps);
assert.match(document.body.textContent,/Address is incomplete/);
await click('Edit');
for (const label of ['Street','Address line 2','City','State','ZIP']) assert.equal(input(label).required,false);
assert.equal(input('Organization code').value,'3');
await click('Save and send as-is');
assert.equal(addressSaves.length,1);
assert.equal(addressSaves[0].review.accept_partial_addresses,true);
assert.equal(addressSaves[0].rows[0].city,'Boston');
assert.equal(addressSaves[0].rows[0].address_line_1,'');
assert.equal(document.querySelector('form'),null);
assert.match(document.body.textContent,/Sending continues automatically/);
// Correcting the incomplete address uses the same save, without a second approval.
await render(addressProps);await click('Edit');
for (const [label,value] of [['Street','123 MAIN ST'],['State','MA'],['ZIP','02110']]) await set(input(label),value);
await click('Save and continue');assert.equal(addressSaves.length,2);
assert.equal(addressSaves[1].review.accept_partial_addresses,false);
// A failed persistence keeps the partial address editor and entered values.
await render({...addressProps,onSaveReview:async()=>false});await click('Edit');await click('Save and send as-is');
assert.ok(document.querySelector('form'));assert.equal(input('City').value,'Boston');
assert.match(document.body.textContent,/edits are retained/);
if(process.env.BROKER_UI_REPLAY_FILE){
  const captured=JSON.parse(await readFile(process.env.BROKER_UI_REPLAY_FILE,'utf8'));
  await render({...sourceProps,rows:captured.source_rows});
  assert.match(document.body.textContent,/Paused: Confirm whether these entries belong to one broker or separate recipients/);
  assert.doesNotMatch(document.body.textContent,/Ready for approval/);
  assert.equal(button('Approve').disabled,false);
  assert.equal(document.querySelectorAll('tbody tr').length,captured.source_rows.length);
  if(process.env.BROKER_UI_EVIDENCE_FILE)await writeFile(process.env.BROKER_UI_EVIDENCE_FILE,document.documentElement.outerHTML,'utf8');
}
// Reconcile several missing recipients without submitting a knowingly incomplete list.
const reconciliationRow={...row,commission_total:'100',fee_total:'0',validation_results:[{
  validator:'broker_total_reconciliation',status:'ERROR',normalized_value:'source_totals:100:25',reason:'Printed source totals do not reconcile.'}]};
const reconciliationRoster={...sourceProps.roster,payment_reconciliation:{required:true,source:'PRINTED_SOURCE',expected_commission:'100.00',expected_fees:'25.00'}};
let reconciliationSaves=[];
await render({rows:[reconciliationRow],roster:reconciliationRoster,onSaveReview:async(rows,review)=>{reconciliationSaves.push({rows,review});return true;}});
assert.match(document.body.textContent,/Source fees.*\$25\.00/);
assert.match(document.body.textContent,/Remaining fees.*\$25\.00/);
await click('Edit');await submit();
assert.equal(reconciliationSaves.length,0);assert.equal(document.querySelector('form'),null);
assert.match(document.body.textContent,/staged in this review/);
await click('Add broker');await set(input('Broker name'),'First confirmed fee recipient');
await set(input('Commission'),'0');await set(input('Fees'),'10');await submit();
assert.equal(reconciliationSaves.length,0);assert.equal(document.querySelector('form'),null);
assert.match(document.body.textContent,/Remaining fees.*\$15\.00/);
await click('Add broker');await set(input('Broker name'),'Second confirmed fee recipient');
await set(input('Commission'),'0');await set(input('Fees'),'15');await submit();
assert.equal(reconciliationSaves.length,1);assert.equal(reconciliationSaves[0].rows.length,3);
assert.deepEqual(Array.from(reconciliationSaves[0].review.row_sources),['SOURCE:0','NEW','NEW']);
assert.match(document.body.textContent,/Sending continues automatically/);
await act(async()=>root.unmount());
console.log('Broker UI: automatic readiness, optional addresses, source holds, exact allocation, staged multi-row reconciliation, stale refresh and failed-save preservation passed.');
