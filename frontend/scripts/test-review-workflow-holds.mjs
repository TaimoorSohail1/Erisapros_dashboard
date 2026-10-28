import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { JSDOM } from "jsdom";
import * as icons from "lucide-react";
import ts from "typescript";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("review.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ["brokerRowValidationIssues", "AutomationWorkflowNotice", "PlanYearConflictPanel", "localAgentStatusLabel", "uniqueAutomationReasonLabels", "reviewBlockingFields"];
const declarations = [];
function collect(node) {
  if (ts.isFunctionDeclaration(node) && names.includes(node.name?.text)) declarations.push(node);
  else ts.forEachChild(node, collect);
}
collect(ast);
const runtime = ts.transpileModule(declarations.map((s) => s.getText(ast)).join("\n"), {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;
const context = {
  React, ...icons, planYearConflictRequired: true, actionRequiredRows: [], brokerActionRequiredCount: 1,
  setActiveTab() {}, setStatusFilter() {}, setPriorityFilter() {}, setSectionFilter() {},
  setFormFilter() {}, setContractTypeFilter() {},
};
const api = runInNewContext(`${runtime}\n({ ${names.join(",")} })`, context);
const row = {
  name: "TEST BROKER", organization_code: "3", zip_code: "50398", decision: "REVIEW_REQUIRED",
  validation_results: [{ validator: "source_zip_shape", status: "ERROR", reason: "Printed ZIP is incomplete." }],
};
assert.ok(api.brokerRowValidationIssues(row).includes("Printed ZIP is incomplete."), "Saved source holds must be visible even when the edited ZIP has valid shape.");
assert.equal(api.brokerRowValidationIssues(row, false).length, 0, "The draft must allow an explicit valid correction to be saved.");
assert.ok(api.brokerRowValidationIssues({ ...row, zip_code: "50398-900" }, false).length > 0);
assert.ok(api.brokerRowValidationIssues({ ...row, validation_results: [] }).length > 0, "An unexplained REVIEW_REQUIRED decision must not disappear.");
assert.equal(api.brokerRowValidationIssues({ ...row, decision: "UNASSESSED", validation_results: [] }).length, 0);

let actions = [];
const notice = renderToStaticMarkup(React.createElement(api.AutomationWorkflowNotice, {
  filing: { automation_status: "ACTION_NEEDED", automation_next_action: "RESOLVE_ISSUES", status: "FAILED" },
  decisionLabels: ["Plan year selection", "Broker: TEST BROKER"], onReview: () => actions.push("review"), localAgentStatus: null,
}));
assert.match(notice, /2 decisions need your attention/);
assert.match(notice, /Review decisions/);
assert.doesNotMatch(notice, /2 fields/);
const panel = renderToStaticMarkup(React.createElement(api.PlanYearConflictPanel, {
  conflict: { worksheet_begin: "01/01/2025", ftw_form_begin: "10/01/2025" },
  busy: false, onResolve: (value) => actions.push(value),
}));
assert.match(panel, /01\/01\/2025/);
assert.match(panel, /10\/01\/2025/);
assert.match(panel, /Use worksheet dates/);
assert.match(panel, /Keep FT Williams dates/);
assert.match(panel, /every attached Schedule A/);
assert.equal(actions.length, 0, "Displaying decisions must never select dates or send a filing.");

// Exercise the actual controls and navigation using a local DOM, without API calls.
const dom = new JSDOM("<div id='root'></div><section id='plan-year-conflict'></section><section id='schedule-a-broker-rows'></section><section id='filing-review-table'></section>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
context.window = { requestAnimationFrame: (callback) => callback() };
const destinations = [];
context.document = { getElementById: (id) => ({ scrollIntoView: () => destinations.push(id) }) };
api.reviewBlockingFields();
assert.equal(destinations.at(-1), "plan-year-conflict");
context.planYearConflictRequired = false;
api.reviewBlockingFields();
assert.equal(destinations.at(-1), "schedule-a-broker-rows");
context.actionRequiredRows = [{ label: "Premium" }];
api.reviewBlockingFields();
assert.equal(destinations.at(-1), "filing-review-table");
const root = createRoot(document.getElementById("root"));
const props = { conflict: { worksheet_begin: "01/01/2025", ftw_form_begin: "10/01/2025" }, onResolve: (value) => actions.push(value) };
await act(async () => root.render(React.createElement(api.PlanYearConflictPanel, { ...props, busy: true })));
await act(async () => document.querySelector("button").click());
assert.equal(actions.length, 0, "Busy controls must not resolve a decision.");
await act(async () => root.render(React.createElement(api.PlanYearConflictPanel, { ...props, busy: false })));
await act(async () => document.querySelectorAll("button")[1].click());
assert.deepEqual(actions, ["KEEP_FTW"], "Only the explicitly selected period should be submitted.");
await act(async () => root.unmount());
dom.window.close();

// Assert the real page places date controls outside the optional status drawer.
let inlineDatePanel = false;
function visit(node, inDrawer = false) {
  if (ts.isJsxElement(node)) {
    inDrawer ||= node.openingElement.tagName.getText(ast) === "ReviewStatusDrawer";
  }
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === "PlanYearConflictPanel") {
    assert.equal(inDrawer, false, "The pending date decision must not be hidden in a closed drawer.");
    inlineDatePanel = true;
  }
  ts.forEachChild(node, (child) => visit(child, inDrawer));
}
visit(ast);
assert.ok(inlineDatePanel);
assert.match(source, /onResolve=\{reviewBlockingFields\}/, "Resolve must use the same workflow-aware navigation as the main review action.");
console.log("Review workflow and source-hold regressions passed.");
