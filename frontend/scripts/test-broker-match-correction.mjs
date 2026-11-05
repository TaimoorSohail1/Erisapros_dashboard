import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import React, { act, useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import ts from "typescript";

const choiceSource = await readFile(new URL("../src/brokerMatchChoices.ts", import.meta.url), "utf8");
const choiceRuntime = ts.transpileModule(choiceSource.replace(/^import.*$/gm, "").replace(/^export /gm, ""), {
  compilerOptions: { module: ts.ModuleKind.None, target: ts.ScriptTarget.ES2022 },
}).outputText;
const choicesFor = runInNewContext(`${choiceRuntime}\nbrokerMatchChoices`);
const choiceResult = (rows) => JSON.parse(JSON.stringify(choicesFor({ ftw_seq_no: "1", schedule_a_records: [
  { ftw_seq_no: "1", query_subparts: { Broker: rows } },
  { ftw_seq_no: "2", query_subparts: { Broker: [{ NameXX: "Unrelated sibling" }] } },
]})));
assert.deepEqual(choiceResult([{ Name1: "First" }, { CommPdAmt01: "10" }, { Name2: "Second" }, { CommPdAmt02: "20" }]),
  [{ index: 0, label: "FT Williams row 1: First" }, { index: 1, label: "FT Williams row 2: Second" }]);
assert.deepEqual(choiceResult([{ Name1: "First" }, { Name1: "Second" }]),
  [{ index: 0, label: "FT Williams row 1: First" }, { index: 1, label: "FT Williams row 2: Second" }]);
assert.deepEqual(choiceResult([{ Name1: "First" }, { ForeignAddy01: "0" }, { Name2: "Second" }, { ForeignAddy02: "0" }]),
  [{ index: 0, label: "FT Williams row 1: First" }, { index: 1, label: "FT Williams row 2: Second" }]);

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const parts = source.slice(source.indexOf("function ScheduleABrokerRowsPanel("), source.indexOf("function formatBrokerAddress("));
const runtime = ts.transpileModule(parts, { compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 } }).outputText;
const dom = new JSDOM("<div id='root'></div>", { url: "https://example.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.requestAnimationFrame = () => 1;
window.cancelAnimationFrame = () => {};
const empty = () => null;
const Panel = runInNewContext(`${runtime}\nScheduleABrokerRowsPanel`, {
  React, Fragment: React.Fragment, useState, useEffect, useRef, window,
  PanelHeading: empty, ListChecks: empty, Edit3: empty, Ban: empty, AlertTriangle: empty,
  brokerRowValidationIssues: () => [], organizationCodeLabel: () => "3", formatBrokerAddress: () => "10 Main St",
  formatBrokerPurpose: () => "COMMISSIONS",
});
const purposeCode = source.slice(source.indexOf("function formatBrokerPurpose("), source.indexOf("function FTWilliamsComparisonPanel("));
const purpose = runInNewContext(`${ts.transpileModule(purposeCode, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText}\nformatBrokerPurpose`);
for (const [commission, fees, expected] of [["100","0","COMMISSIONS"],["0","-557","FEES"],["100","-100","COMMISSIONS AND FEES"],["0","0","COMMISSION"],[null,"0","Unclassified"]]) {
  assert.equal(purpose({commission_total: commission, fee_total: fees, purpose: "Incorrect printed label"}), expected);
}
const root = createRoot(document.getElementById("root"));
const base = {busy:false, onSaveRows:async()=>true,
  currentRows:[{NameXX:"Old broker", AddressLine1XX:"99 OLD ST",CommPdAmtXX:"200",FeesPdAmtXX:"0",FeesPdTextXX:"COMMISSIONS"}],
  rows:[{name:"Current broker",commission_total:"10",fee_total:"0"}],
  roster:{state:"VERIFIED",mode:"REPLACE",existing_count:2,replacement_count:1,reasons:[],evidence:[],other_disclosures:[]}};
await act(async () => root.render(React.createElement(Panel, base)));
assert.ok(document.body.textContent.includes("Will replace 2 existing brokers with 1 verified broker."));
assert.ok(document.body.textContent.includes("Old broker, 99 OLD ST"));
assert.ok(document.body.textContent.includes("Commissions: 200"));
assert.ok(!document.body.textContent.includes("Matched to row"));
assert.ok(![...document.querySelectorAll("button")].some(b=>/Change match|Match row|Add as new/.test(b.textContent)));
assert.ok([...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="Edit"), "Keep source correction controls");
await act(async () => root.render(React.createElement(Panel,{...base,rows:[],roster:{...base.roster,state:"NEEDS_REVIEW",mode:"PRESERVE",reasons:["Blank Schedule A table"],other_disclosures:[{name:"Example Advisory",page:5,amount:"240148.74",payment_category:"UNCLASSIFIED_DIRECT_COMPENSATION"}]}})));
assert.ok(document.body.textContent.includes("Existing FTWilliam brokers will be preserved"));
assert.ok(document.body.textContent.includes("$240148.74 (page 5)"));
assert.ok(document.body.textContent.includes("not a verified Schedule A commission or fee"));
await act(async () => root.unmount());
console.log("Verified replacement and ambiguous-disclosure previews pass without individual row assignments.");
