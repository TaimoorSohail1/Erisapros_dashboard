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
const readyCode = source.slice(source.indexOf("  const scheduleABrokersReady = ("), source.indexOf("  const form5500SafetyReady ="));
const ready = (brokerRoster, scheduleABrokerRows = []) => runInNewContext(`${readyCode}\nscheduleABrokersReady`, {
  brokerRoster, scheduleABrokerRows, ftwReview: null, brokerValidationIssues: [],
});
assert.equal(ready(null), true, "Field-only/Form 5500 flows propose no broker replacement and remain available.");
assert.equal(ready({mode:"PRESERVE"}), false, "An empty but unverified new extraction still needs review.");
assert.equal(ready(null, [{name:"Unverified broker"}]), false, "Legacy row matches cannot authorize a roster.");
assert.equal(ready({mode:"REPLACE"}, [{name:"Verified broker"}]), true);
const purposeCode = source.slice(source.indexOf("function formatBrokerPurpose("), source.indexOf("function FTWilliamsComparisonPanel("));
const purpose = runInNewContext(`${ts.transpileModule(purposeCode, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText}\nformatBrokerPurpose`);
for (const [commission, fees, expected] of [["100","0","COMMISSIONS"],["0","-557","FEES"],["100","-100","COMMISSIONS AND FEES"],["0","0","COMMISSION"],[null,"0","Unclassified"]]) {
  assert.equal(purpose({commission_total: commission, fee_total: fees, purpose: "Incorrect printed label"}), expected);
}
await import('./test-broker-roster-review.mjs');
console.log("Verified replacement gating and signed purpose rules pass without individual row assignments.");
