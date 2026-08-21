import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

assert.match(
  source,
  /if \(option === "SCHEDULE_A"\) return "ShareFile Schedule A";/,
  "Schedule A form filter must use the ShareFile Schedule A label.",
);
assert.match(
  source,
  /if \(option === "FORM_5500"\) return "ShareFile Plan Worksheet";/,
  "Form 5500 form filter must use the ShareFile Plan Worksheet label.",
);

const expectedHeaderOrder = /<th>Field<\/th>\s*<th>Extracted<\/th>\s*<th>Current FTW<\/th>\s*<th>Proposed To Send<\/th>\s*<th>Status<\/th>/g;
assert.equal(
  [...source.matchAll(expectedHeaderOrder)].length,
  2,
  "Every review table must use the compact five-column comparison layout.",
);
assert.doesNotMatch(source, /<th>Decision<\/th>/, "Decision controls must be inline below Proposed To Send.");

assert.match(
  source,
  /<td>\{row\.extracted \|\|[\s\S]*?<\/td>\s*<td>\{row\.currentFtw \|\|/,
  "Review rows must render extracted values before current FTW values.",
);

console.log("Review table labels and column order passed.");

assert.match(
  source,
  /label="Action Required"/,
  "The primary review view must clearly group fields that require action.",
);
assert.match(
  source,
  /label="Will Update FTW"/,
  "The outgoing FT Williams changes must have a dedicated view.",
);
assert.doesNotMatch(
  source,
  /label="Ready \/ Same"/,
  "Same values must not occupy a primary review tab.",
);
assert.match(
  source,
  /function ReviewPrimaryActions/,
  "Primary workflow actions must be rendered contextually.",
);
assert.match(
  source,
  /<button className="button secondary" type="button" disabled=\{queryBusy\} onClick=\{onQuery\}>[\s\S]*?Query FTW Current/,
  "Query FTW Current must remain directly available after current FTW data has loaded.",
);
assert.match(
  source,
  /approvalReady=\{!isProcessing && !scheduleSelectionRequired\}/,
  "Approval must remain available with unresolved fields once processing and Schedule A selection are complete.",
);
assert.match(
  source,
  /hasBlockers=\{actionRequiredRows\.length > 0\}/,
  "The approval confirmation must warn whenever unresolved fields remain.",
);
assert.match(
  source,
  /function FilingGuidancePanel/,
  "The page must show one compact source-aware guidance message.",
);
assert.match(
  source,
  /function ScheduleASelectionStep/,
  "Multiple Schedule A candidates must be resolved in a visible workflow step.",
);
assert.match(
  source,
  /function FTWVerificationSummary/,
  "FT Williams send results must show verified and remaining field counts.",
);
assert.match(
  source,
  /if \(row\.extractedField\?\.status === "EDITED"\) return false;/,
  "A reviewer-confirmed field must immediately leave the Action Required count.",
);
assert.match(
  source,
  /if \(field\?\.status === "EDITED"\) return comparison\.changed && comparison\.update_included \? "WILL_UPDATE" : "SAME";/,
  "A reviewer-confirmed field must enter Will Update only when the FTW update contract includes it.",
);
assert.match(
  source,
  /Resolved · not sent to FTW/,
  "A confirmed read-only field must clearly state that it will not be sent to FT Williams.",
);
assert.match(
  source,
  /const needsDecisionRows = reviewRows\.filter\(\(row\) => row\.group === "NEEDS_DECISION" && isActionRequiredRow\(row\)\);/,
  "Approval summaries must not count reviewer-confirmed decisions as unresolved.",
);
assert.match(
  source,
  /\(filing\.jobs \|\| \[\]\)\.map/,
  "Older filing payloads without processing jobs must not crash the review page.",
);
assert.match(
  source,
  /\(filing\.audit_logs \|\| \[\]\)\.map/,
  "Older filing payloads without audit logs must not crash the review page.",
);
assert.match(
  source,
  /const latestJob = \(filing\.jobs \|\| \[\]\)\[0\];/,
  "Processing state must tolerate filing payloads without a jobs array.",
);
assert.match(
  source,
  /const displayStatus = \(filing\.status \|\| "UPLOADED"\)\.replaceAll\("_", " "\);/,
  "Processing state must tolerate older filing payloads without a status value.",
);
assert.match(
  source,
  /approvalReady=\{!isProcessing && !scheduleSelectionRequired\}/,
  "Approval must remain unavailable while processing is running or Schedule A selection is unresolved.",
);
assert.match(
  source,
  /title = "Document processing is in progress";/,
  "Processing guidance must take priority over approval-ready messaging.",
);
assert.doesNotMatch(
  source,
  /filing\.fields\.length|filing\.jobs\[0\]/,
  "Optional filing arrays must not be dereferenced without safe defaults.",
);
assert.match(
  source,
  /Technical details/,
  "Technical XML, logs, and fallback controls must be grouped in a hidden technical workspace.",
);

assert.match(
  source,
  /\{paginatedDisplayRows\.map\(\(row\) => \(/,
  "The filtered comparison must render inline with compact pagination.",
);
assert.match(
  source,
  /const reviewRowsPerPage = 8;/,
  "The main comparison must use the reference design's eight-row page size.",
);
assert.match(
  source,
  /onStepSelect=\{setActiveWorkflowStep\}/,
  "Workflow steps must open focused step details.",
);
assert.match(
  source,
  /function WorkflowDetailDialog/,
  "The filing workflow must provide focused, client-readable step dialogs.",
);
assert.match(
  source,
  /className={`workflow-status-pill workflow-status-pill-\$\{tone\}`}/,
  "Workflow dialogs must use the prototype's compact status pill instead of a full-width alert card.",
);
assert.match(
  source,
  /className="workflow-step-activity"/,
  "Workflow dialogs must explain progress through the prototype's activity list.",
);
assert.match(
  source,
  /className="workflow-schedule-select"/,
  "Schedule A selection must use the compact workflow selector shown in the prototype.",
);
assert.match(
  source,
  /className="workflow-dialog-footer-note"/,
  "Workflow dialogs must keep contextual state in the quiet footer area.",
);
assert.match(
  source,
  /function WorkflowReviewCenter/,
  "The Review workflow modal must provide one focused review center.",
);
assert.match(
  source,
  /className="workflow-review-panel workflow-user-review-panel"/,
  "User review status must appear as a polished panel inside the Review modal.",
);
assert.match(
  source,
  /<FTWVerificationSummary review=\{review\} onReview=\{[\s\S]*?\} compact \/>/,
  "FT Williams verification must be embedded inside the Review modal.",
);
assert.match(
  source,
  /Verification results will appear here automatically after data is sent to FT Williams\./,
  "The Review modal must explain where post-send FTW verification will appear.",
);
assert.match(
  source,
  /function TechnicalReviewDrawer/,
  "FTW matching, processing logs, and XML must live in a hidden technical drawer.",
);
assert.match(
  source,
  /Keep Extracted/,
  "The extracted-source decision must use clear source language.",
);
assert.match(
  source,
  /Keep Current/,
  "The current FTW decision must use clear source language.",
);
assert.match(
  source,
  /className="field-review-value-card field-review-value-source"/,
  "The field review modal must visually distinguish the extracted source value.",
);
assert.match(
  source,
  /className="field-review-value-card field-review-value-proposed"/,
  "The field review modal must give the proposed FTW value a focused editing surface.",
);
assert.match(
  source,
  /className="field-review-insight-card field-review-confidence-card"/,
  "The field review modal must present confidence as a compact insight card.",
);
assert.match(
  source,
  /className="field-review-action-group field-review-primary-actions"/,
  "The field review modal must keep the primary save action visually separate from supporting actions.",
);
assert.match(
  source,
  /approval-workflow-source/,
  "The compact workflow must identify ShareFile and the FTW comparison source.",
);
assert.match(
  source,
  /className="proposed-cell"[\s\S]*?className="decision-actions"/,
  "Source decisions must render inside the Proposed To Send cell.",
);
assert.doesNotMatch(
  source,
  /<section className="approval-focus-layout">/,
  "The review table must fill the page rather than sit below oversized summary sections.",
);

assert.match(
  styles,
  /\.approval-workspace-page \.approval-decision-table td\s*\{[^}]*font-size:\s*12px;/,
  "Comparison rows must remain readable at normal browser zoom.",
);
assert.match(
  styles,
  /\.approval-workspace-page \.approval-table-wrap\s*\{[^}]*flex:\s*1 1 auto;[^}]*height:\s*auto;[^}]*max-height:\s*none;[^}]*min-height:\s*0;/,
  "The comparison table must consume the actual remaining viewport height.",
);
assert.doesNotMatch(
  styles,
  /\.approval-workspace-page \.approval-table-wrap\s*\{[^}]*clamp\(/,
  "The comparison table must not use a hard-coded viewport clamp that cuts short screens and wastes tall screens.",
);
assert.match(
  styles,
  /\.approval-workspace-page \.approval-decision-table td small\s*\{[^}]*color:\s*#46566b;[^}]*font-size:\s*10px;/,
  "Secondary field text must remain legible with sufficient contrast.",
);
assert.match(
  source,
  /className="workflow-schedule-menu"[\s\S]*?role="listbox"/,
  "Schedule A selection must use a contained listbox instead of a native popup that can escape the dialog.",
);
assert.doesNotMatch(source, /<select id="workflow-schedule-candidate"/, "The workflow dialog must not use the overflowing native Schedule A selector.");
assert.match(
  styles,
  /\.workflow-schedule-menu\s*\{[^}]*max-height:\s*210px;[^}]*overflow-y:\s*auto;[^}]*width:\s*100%;/,
  "The Schedule A listbox must stay inside the modal and scroll internally.",
);
assert.doesNotMatch(styles, /\.workflow-dialog\s*\{[^}]*overflow:\s*visible;/, "Workflow dialogs must contain their controls and decoration.");
assert.match(
  styles,
  /\.workflow-review-panel > header strong\s*\{[^}]*font-size:\s*14px;/,
  "Review modal status cards must keep a clear visual hierarchy.",
);
assert.match(
  styles,
  /\.compact-table-search\s*\{[^}]*min-width:\s*180px;[^}]*overflow:\s*hidden;/,
  "The compact field search must keep its control inside the toolbar boundary.",
);
assert.match(
  styles,
  /\.compact-table-search input\s*\{[^}]*background:\s*transparent;[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
  "The compact field search input must not inherit the full-size search height.",
);

console.log("Guided filing review workflow passed.");
