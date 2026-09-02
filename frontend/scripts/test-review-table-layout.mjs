import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const api = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");

assert.match(
  api,
  /export async function resolveFTWilliamsPlanYearConflict[\s\S]*?\/ftw\/plan-year-resolution/,
  "The client must expose the explicit FT Williams plan-year resolution endpoint.",
);
assert.match(
  source,
  /function PlanYearConflictPanel[\s\S]*?Use worksheet dates[\s\S]*?Keep FT Williams dates/,
  "A plan-year conflict must show both coordinated reviewer choices.",
);
assert.match(
  source,
  /const planYearConflictRequired = Boolean\([\s\S]*?plan_year_conflict[\s\S]*?!ftwReview\?\.plan_year_resolution/,
  "An unresolved plan-year conflict must be tracked as an approval blocker.",
);
assert.match(
  source,
  /const approvalReady = [^;]*!planYearConflictRequired/,
  "Approval must remain locked until the plan-year conflict is resolved.",
);
assert.match(
  source,
  /ftwReadyToSend = Boolean\([\s\S]*?!planYearConflictRequired/,
  "FT Williams send readiness must remain locked until the plan-year conflict is resolved.",
);

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

const brokerRowsPosition = source.indexOf("<ScheduleABrokerRowsPanel");
const comparisonTablePosition = source.indexOf('<table className="approval-decision-table">');
const comparisonFooterPosition = source.indexOf('<div className="approval-preview-footer">');
assert.ok(brokerRowsPosition > comparisonTablePosition, "Schedule A broker rows must appear below the field comparison table.");
assert.ok(brokerRowsPosition > comparisonFooterPosition, "Schedule A broker rows must appear at the bottom of the comparison workspace.");

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
  /bringForwardRequired=\{bringForwardRequired\}[\s\S]*?onOpenBringForward=\{openFtwBringForward\}/,
  "The main filing actions must receive the Bring Forward state and action.",
);
assert.match(
  source,
  /bringForwardRequired \? \([\s\S]*?Open FTW Bring Forward/,
  "Bring Forward must be directly visible in the main filing actions when the current-year record is missing.",
);
assert.match(
  source,
  /const retryingFailedFtwUpdate = filing\?\.status === "FAILED" && \(ftwUpdateFailed \|\| ftwUpdateUnknown\) && ftwReadyToSend;[\s\S]*?const approvalReady = !isProcessing && !scheduleSelectionRequired && !retryingFailedFtwUpdate && !planYearConflictRequired;/,
  "Approval readiness must distinguish an active FTW retry from a failed filing that needs re-approval.",
);
assert.match(
  source,
  /\{!approved && approvalReady \? \(/,
  "A recovered failed filing must not be excluded from the approval action solely because its stored filing status is FAILED.",
);
assert.doesNotMatch(
  source,
  /disabled=\{busy \|\| !ftwCurrentLoaded\}/,
  "Approval must not be disabled by FTW query state; query safety belongs to the send action.",
);
assert.doesNotMatch(
  source,
  /title=\{!ftwCurrentLoaded \? "Query FTW Current before approving\." : undefined\}/,
  "Approval must not tell reviewers to query FTW when field review is already complete.",
);
assert.doesNotMatch(
  source,
  /function handleApproveClick\(\) \{[\s\S]*?if \(!ftwCurrentLoaded\)[\s\S]*?setShowApproveConfirm\(true\);/,
  "Opening approval confirmation must not be blocked by FTW query state.",
);
assert.match(
  source,
  /\{!approved && approvalReady \? \([\s\S]*?disabled=\{busy\}[\s\S]*?onClick=\{onApprove\}/,
  "The primary approval action must only be disabled while another review action is busy.",
);
assert.doesNotMatch(
  source,
  /!approved && !failed && approvalReady/,
  "The approval action must not permanently disappear for recoverable failed filings.",
);
assert.match(
  source,
  /<WorkflowDetailDialog[\s\S]*?approvalReady=\{approvalReady\}[\s\S]*?onApprove=\{\(\) => \{/,
  "The Approval workflow step must receive the same approval action and readiness used by the primary toolbar.",
);
assert.match(
  source,
  /step === "APPROVAL" && filing\.status !== "APPROVED" && approvalReady \? \([\s\S]*?disabled=\{busy\}[\s\S]*?onClick=\{onApprove\}[\s\S]*?Approve filing/,
  "The Approval workflow step must render a working approval action when confirmation is pending.",
);
assert.match(
  source,
  /hasBlockers=\{actionRequiredCount > 0\}/,
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
  /ftwScheduleMatch \? "Best match selected" : ftwScheduleNeedsDecision \? "Needs your decision"/,
  "The FTW Loaded step must distinguish a safe automatic match from a required reviewer decision.",
);
assert.match(
  source,
  /<small>FTW match<\/small><strong>\{filing\.ftw_review\?\.schedule_a_match \? "Matched" : "Pending"\}<\/strong>/,
  "The filing summary must report a Schedule A match only when a specific Schedule A selection exists.",
);
assert.doesNotMatch(
  source,
  /<small>FTW match<\/small>[\s\S]{0,160}customer_id/,
  "A customer or plan lookup must not be presented as a selected Schedule A match.",
);
assert.match(
  source,
  /\(filing\.ftw_review\?\.schedule_a_candidates \|\| \[\]\)\.length \|\| filing\.ftw_review\?\.bring_forward_required/,
  "A missing current Schedule A that requires Bring Forward must keep FTW Loaded in the decision state.",
);
assert.match(
  source,
  /label=\{scheduleMatchSelected \? "Best match selected" : scheduleDecisionRequired \? "Needs your decision"/,
  "The FTW Loaded dialog must repeat the Schedule A match decision clearly.",
);
assert.match(
  source,
  /const scheduleDecisionRequired = Boolean\(\s*\(review\?\.current_query_success \|\| ftwCurrentLoaded\)/,
  "An intentionally incomplete snapshot with unmatched candidates must still open as Needs your decision, not Processing.",
);
assert.match(
  source,
  /Selected Schedule A[\s\S]*?Match score \{selectedScheduleScore\}/,
  "The FTW Loaded dialog must show the selected Schedule A and its match score.",
);
assert.match(
  source,
  /const recommended = Boolean\(recommendedSequence && sequence === recommendedSequence\);/,
  "Only the backend-selected safe Schedule A may be labelled as recommended.",
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
  /mergeFieldDecisionReview\(current\.ftw_review, result\.ftw_review, fieldId\)/,
  "Saving one field must merge only that field's FT Williams comparison instead of replacing unrelated rows.",
);
assert.match(source, /Review only · not supported/, "Unsupported FTW fields must be labelled as review-only rather than resolved.");
assert.match(source, /Managed in broker rows/, "Structured multi-broker fields must not be labelled as unsupported.");
assert.match(
  source,
  /hideStructuredBrokerFields && isStructuredBrokerSummaryRule\(comparison\.rule_key\)/,
  "Structured broker rows must hide duplicate flat broker comparison fields.",
);
assert.match(
  source,
  /hideStructuredBrokerFields && isStructuredBrokerSummaryRule\(fieldRuleKey\(field\)\)/,
  "Structured broker rows must also hide duplicate extracted-only broker fields.",
);
assert.match(
  source,
  /buildReviewDecisionRows\(fields, filing\?\.ftw_review \|\| null, scheduleAContractType, false, Boolean\(scheduleABrokerRows\.length\)\)/,
  "The comparison workspace must enable broker-summary hiding whenever the structured broker table is present.",
);
assert.match(source, /Add as new/, "Unmatched extracted brokers must offer an explicit new-row decision.");
assert.match(source, /setFTWilliamsScheduleABrokerMatches/, "Broker match decisions must be saved through the FT Williams review API.");
assert.match(source, /Save broker row/, "Reviewers must be able to edit a broker row before sending it to FT Williams.");
assert.match(source, /Exclude this broker row from the FT Williams update/, "Reviewers must be able to exclude a duplicate broker row.");
assert.match(api, /updateFTWilliamsScheduleABrokerRows[\s\S]*?\/ftw\/schedule-a-broker-rows/, "Broker row edits must be validated and saved through the review API.");
assert.match(source, /scheduleABrokersReady/, "Unconfirmed broker matches must lock FT Williams sending.");
assert.match(
  source,
  /const FTW_ORGANIZATION_CODE_OPTIONS[\s\S]*?value: "3", label: "Insurance agent or broker"/,
  "Broker organization codes must come from a labelled FT Williams option list.",
);
assert.match(
  source,
  /Organization code[\s\S]*?<select required value=\{draft\.organization_code \|\| ""\}/,
  "The broker editor must use a required organization-code dropdown instead of free text.",
);
assert.doesNotMatch(
  source,
  /<label>Organization code<input/,
  "The broker editor must not allow arbitrary organization codes.",
);
assert.match(
  source,
  /Choose 3 for an insurance broker/,
  "The organization-code field must explain the common insurance-broker choice.",
);
assert.match(
  source,
  /Will update FT Williams:.*→/,
  "Edited fields must clearly show the current and proposed FT Williams values.",
);
assert.match(
  source,
  /function FTWilliamsSendConfirmationModal/,
  "FT Williams sends must show a final change-and-preservation confirmation.",
);
assert.match(
  source,
  /review\.update_verification_success === true/,
  "The interface must require explicit read-back success before showing an FT Williams update as complete.",
);
assert.match(
  source,
  /!verifiedUpdateComplete/,
  "A fully verified filing must not offer another no-op FT Williams send.",
);
assert.match(
  source,
  /FT Williams accepted the update/,
  "The interface must show vendor acceptance separately from read-back verification.",
);
assert.match(
  source,
  /Unchanged Schedule A records/,
  "The confirmation modal must keep unchanged Schedule A identities in a collapsed summary.",
);
assert.match(
  source,
  /Send and verify/,
  "The final FT Williams action must explain that read-back verification follows the send.",
);
assert.match(
  source,
  /onSend=\{requestFtwSend\}/,
  "Toolbar sends must open the confirmation step instead of writing immediately.",
);
assert.doesNotMatch(source, /Resolved · not sent to FTW/, "Unsupported FTW fields must not appear as successfully resolved updates.");
assert.match(
  source,
  /const ftwSendInFlightRef = useRef\(false\);/,
  "FT Williams sends need an immediate in-flight guard so rapid clicks cannot start duplicate requests.",
);
assert.match(
  source,
  /if \(!id \|\| ftwSendInFlightRef\.current\) return;[\s\S]*?ftwSendInFlightRef\.current = true;[\s\S]*?finally \{[\s\S]*?ftwSendInFlightRef\.current = false;/,
  "The duplicate-send guard must cover the complete FT Williams request lifecycle.",
);
assert.match(
  source,
  /const showFtwSendAction = !verifiedUpdateComplete && \([\s\S]*?filing\?\.status === "APPROVED" \|\| \(filing\?\.status === "FAILED" && \(ftwUpdateFailed \|\| ftwUpdateUnknown\)\)[\s\S]*?\);/,
  "Approved filings may expose the FT Williams send action, but verified updates must not be sent again.",
);
assert.match(
  source,
  /showFtwSendAction=\{showFtwSendAction\}[\s\S]*?onSend=\{requestFtwSend\}/,
  "The toolbar must receive approved-state send visibility and route sends through final confirmation.",
);
assert.match(
  source,
  /\{showFtwSendAction \? \([\s\S]*?Send to FT Williams/,
  "An approved filing must render the send button and let the backend enforce the final safety preflight.",
);
assert.match(
  source,
  /ftwReview\.current_query_complete !== false/,
  "Sending must remain locked when FT Williams returned only a partial current-data snapshot.",
);
assert.match(
  source,
  /review\?\.status === "UPDATE_UNKNOWN" \? "Verification required"/,
  "An ambiguous FT Williams response must be displayed as verification required.",
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
  /const status = filing\.status \|\| "UPLOADED";\s*const displayStatus = status\.replaceAll\("_", " "\);/,
  "Processing state must tolerate older filing payloads without a status value.",
);
assert.match(
  source,
  /isProcessing && !fields\.length \? \(\s*<ProcessingPanel filing=\{filing\} \/>\s*\) : filing\.status === "FAILED"[\s\S]*?<section className="approval-decision-table-shell approval-preview-shell"/,
  "The empty review workspace must be replaced by the dedicated progress experience while extraction is running.",
);
assert.match(
  source,
  /<section className="extraction-progress-shell card" role="status" aria-live="polite"/,
  "Extraction progress must be announced accessibly without interrupting the user.",
);
assert.match(
  source,
  /File received[\s\S]*Reading documents[\s\S]*Matching filing fields[\s\S]*Preparing review/,
  "The extraction experience must explain the full customer-facing processing journey.",
);
assert.match(
  source,
  /You can safely leave this page/,
  "The progress experience must reassure users that processing continues in the background.",
);
assert.match(
  source,
  /filing\.status === "FAILED" && !fields\.length \? \(\s*<ExtractionFailurePanel/,
  "A failed extraction without review fields must replace the empty workspace with a recovery experience.",
);
assert.match(
  source,
  /function ExtractionFailurePanel[\s\S]*?role="alert"[\s\S]*?onClick=\{onRetry\}[\s\S]*?Try extraction again/,
  "The extraction failure state must offer a clear retry action.",
);
assert.doesNotMatch(
  source,
  /This page refreshes automatically from MongoDB/,
  "Customer-facing progress copy must not expose internal database implementation details.",
);
assert.match(
  source,
  /approvalReady=\{approvalReady\}/,
  "Approval must remain unavailable while processing is running or Schedule A selection is unresolved.",
);
assert.match(
  source,
  /const scheduleSelectionRequired = scheduleCandidates\.length > 0 && !ftwReview\?\.schedule_a_match;/,
  "Even one unmatched Schedule A candidate must require an explicit reviewer selection.",
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
  /textValue\(candidate\.score\)/,
  "The compact Schedule A selector must display the backend match score.",
);
assert.match(
  source,
  /workflow-schedule-recommended[^\n]*Recommended/,
  "The strongest Schedule A candidate must be visibly marked as recommended.",
);
assert.match(
  source,
  /candidate\.has_current_data/,
  "The compact Schedule A selector must display whether current FTW data was loaded.",
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
  /\.workflow-schedule-menu\s*\{[^}]*max-height:\s*\d+px;[^}]*overflow-y:\s*auto;[^}]*width:\s*100%;/,
  "The Schedule A listbox must stay inside the modal and scroll internally.",
);
assert.doesNotMatch(styles, /\.workflow-dialog\s*\{[^}]*overflow:\s*visible;/, "Workflow dialogs must contain their controls and decoration.");
assert.match(
  styles,
  /\.extraction-progress-shell\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/,
  "The extraction progress screen must fill the available review viewport without creating page overflow.",
);
assert.match(
  styles,
  /\.extraction-progress-stages\s*\{[^}]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\);/,
  "Desktop extraction progress must present the processing stages as one readable journey.",
);
assert.match(
  styles,
  /@media \(max-width:\s*720px\)[\s\S]*?\.extraction-progress-stages\s*\{[^}]*grid-template-columns:\s*1fr;/,
  "Extraction stages must stack cleanly on small screens.",
);
assert.match(
  styles,
  /@media \(max-width:\s*720px\)[\s\S]*?\.approval-workspace-page \.approval-workspace\s*\{[^}]*min-width:\s*0;[^}]*width:\s*100%;/,
  "The mobile workflow scroller must not force the extraction screen wider than the viewport.",
);
assert.match(
  styles,
  /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.extraction-progress-spinner/,
  "Extraction animations must respect reduced-motion preferences.",
);
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
assert.match(
  source,
  /aria-label="Schedule A broker rows"[\s\S]*?role="region"[\s\S]*?tabIndex=\{0\}/,
  "The broker-row scroll region must be keyboard accessible.",
);
assert.match(
  styles,
  /\.schedule-a-broker-table-wrap\s*\{[^}]*max-height:\s*min\(420px, 50vh\);[^}]*overflow:\s*auto;/,
  "The broker table must use a contained scrolling region instead of growing the page indefinitely.",
);
assert.match(
  styles,
  /\.schedule-a-broker-table th\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;/,
  "The broker table header must remain visible while its rows scroll.",
);
assert.match(
  source,
  /validation_status === "INVALID"[\s\S]*?Invalid FT Williams format/,
  "Invalid FT Williams values must use a specific status instead of appearing unsupported.",
);
assert.match(
  source,
  /validation-blocker-banner[\s\S]*?Approval and sending stay locked/,
  "Blocking validation errors must be explained before approval or sending.",
);
assert.match(
  source,
  /placeholder=\{expectedFormat \|\| "Enter the FT Williams value"\}/,
  "Field editing must show the expected FT Williams format.",
);
assert.match(
  source,
  /Select organization code/,
  "Broker organization codes must use an explicit placeholder.",
);
assert.match(
  source,
  /brokerRowValidationIssues\(draft\)[\s\S]*?setShowDraftValidation\(true\)/,
  "Broker rows must be validated before save.",
);

console.log("Guided filing review workflow passed.");
