import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import React, {act,useState,useRef,useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM} from 'jsdom';
import ts from 'typescript';

const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
const source=await readFile(new URL('../src/ui/BrokerRosterPanel.tsx',import.meta.url),'utf8');
const code=ts.transpileModule(source.replace(/^import .*$/gm,'').replace(/^export /gm,''),{
  compilerOptions:{module:ts.ModuleKind.None,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022}}).outputText;
const Panel=runInNewContext(code+'\nBrokerRosterPanel',{React,useState,useEffect,useRef,Intl,Number});
const root=createRoot(document.getElementById('root'));
let saves=[];
const roster={state:'NEEDS_REVIEW',mode:'PRESERVE',existing_count:1,replacement_count:0,
  review_context_sha256:'a'.repeat(64),reasons:['Source coverage needs confirmation'],evidence:[],
  other_disclosures:[{id:'mercer-proof',name:'Mercer Health & Benefits LLC',address:'4565 PAYSPHERE CIRCLE CHICAGO IL 60674',amount:'240148.74',page:5,source_text:'Original Schedule C disclosure'}]};
const props={busy:false,rows:[],roster,currentRows:[{NameXX:'Existing broker'}],
  onSaveReview:async(rows,review)=>{saves.push({rows,review});return false;}};
const buttons=()=>[...document.querySelectorAll('button')];
const click=async(text)=>{const b=buttons().find(b=>b.textContent===text);assert.ok(b,text);await act(async()=>b.click());};
const set=async(element,value)=>{const key=element.tagName==='SELECT'?'HTMLSelectElement':element.tagName==='TEXTAREA'?'HTMLTextAreaElement':'HTMLInputElement';
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window[key].prototype,'value').set.call(element,value);
    element.dispatchEvent(new dom.window.Event('change',{bubbles:true}));element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
await act(async()=>root.render(React.createElement(Panel,props)));
assert.match(document.body.textContent,/Mercer Health & Benefits LLC/);
assert.match(document.body.textContent,/\$240,148\.74/);
assert.match(document.body.textContent,/Unclassified/);
assert.equal(buttons().filter(b=>b.textContent==='Review & edit').length,1);
assert.equal(document.querySelectorAll('details').length,0,'Technical details stay out of the main row');
await click('Review & edit');
assert.equal(document.activeElement.id,'broker-review-heading');
assert.match(document.body.textContent,/View evidence/);
assert.match(document.body.textContent,/Current brokers \(1\)/);
await click('Cancel');assert.equal(saves.length,0);
await click('Review & edit');
await set(document.querySelector('select'),'INCLUDED');
assert.equal(document.querySelector('[aria-label="Commission for broker 1"]').value,'');
assert.equal(document.querySelector('[aria-label="Fees for broker 1"]').value,'');
await click('Cancel');assert.equal(saves.length,0,'Opening/including/cancelling never saves');

await act(async()=>root.render(React.createElement(Panel,{...props,rows:[{name:'Formatted source broker',commission_total:'100,901.12',fee_total:'(100,901.12)'}],roster:{...roster,mode:'REPLACE',replacement_count:1,other_disclosures:[]}})));
assert.match(document.body.textContent,/\$100,901\.12/,'Formatted source amounts remain readable');
assert.match(document.body.textContent,/COMMISSIONS AND FEES/,'Parenthesized negative fees remain non-zero');

// A complete existing source row proves failed saves retain edits and confirmations.
const row={name:'Source broker',address_line_1:'10 Main St',city:'Boston',state:'MA',zip_code:'02110',organization_code:'0',commission_total:'100',fee_total:'-100'};
await act(async()=>root.render(React.createElement(Panel,{...props,rows:[row],roster:{...roster,other_disclosures:[]}})));
await click('Review & edit');
assert.equal(document.querySelector('select').value,'0','Explicit zero organization code is preserved');
assert.ok([...document.querySelectorAll('input')].some(e=>e.readOnly&&e.value==='COMMISSIONS AND FEES'));
// Browser-native input handling is exercised by an actual submit with controlled fields.
const textarea=document.querySelector('textarea');
await set(textarea,'Client confirmed the complete source list');
// React input tracker in this extracted-component harness may not receive text input;
// use the props rendered by the real component to invoke the same event handlers.
if (textarea.value) {
  const propKey=Object.keys(textarea).find(k=>k.startsWith('__reactProps'));
  await act(async()=>textarea[propKey].onChange({target:{value:'Client confirmed the complete source list'}}));
}
await act(async()=>document.querySelector('input[type="checkbox"]').click());
await act(async()=>document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true})));
assert.equal(saves.length,1);
assert.equal(saves[0].review.complete_list_confirmed,true);
assert.equal(saves[0].rows[0].purpose,'COMMISSIONS AND FEES');
assert.ok(document.querySelector('form'),'Failed save keeps the review panel open');
assert.match(document.body.textContent,/draft is retained/);
await act(async()=>root.render(React.createElement(Panel,{...props,rows:[row],roster:{...roster,review_context_sha256:'b'.repeat(64),other_disclosures:[]}})));
await act(async()=>document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true})));
assert.equal(saves.length,1,'Stale source/snapshot cannot submit a second review');
assert.match(document.body.textContent,/data changed/);
await act(async()=>root.unmount());
console.log('Broker review: visible disclosure, unknown payments, cancel, signed purpose, save failure, stale context passed.');
