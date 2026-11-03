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
const calls = [];
const root = createRoot(document.getElementById("root"));
await act(async () => root.render(React.createElement(Panel, {
  busy: false, rows: [{ name: "Example Broker", commission_total: "10" }],
  matches: [{ extracted_index: 0, ftw_index: 0, resolved: true, status: "AUTO_MATCHED", reason: "Automatic" }],
  choices: [{ index: 0, label: "FT Williams row 1: Example Broker, 10 Main St" },
    { index: 1, label: "FT Williams row 2: Correct Broker, 20 Main St" }],
  onConfirm: (...args) => calls.push(args), onSaveRows: async () => true,
})));
const change = [...document.querySelectorAll("button")].find((button) => button.textContent === "Change match");
assert.ok(change, "An already matched broker must offer a correction control.");
await act(async () => change.click());
const select = document.querySelector('select[aria-label="FT Williams broker row"]');
assert.ok(select?.textContent.includes("Correct Broker, 20 Main St"), "Show destination identity, not only its row number.");
await act(async () => { select.value = "1"; select.dispatchEvent(new window.Event("change", { bubbles: true })); });
await act(async () => [...document.querySelectorAll("button")].find((button) => button.textContent === "Match row").click());
assert.deepEqual(calls, [[0, 1, false]], "Correction must send the exact selected source and target indexes.");
await act(async () => root.unmount());
console.log("Broker match correction renders and sends the selected destination.");
