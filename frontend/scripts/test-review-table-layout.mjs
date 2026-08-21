import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");

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

const expectedHeaderOrder = /<th>Field<\/th>\s*<th>Extracted<\/th>\s*<th>Current FTW<\/th>/g;
assert.equal(
  [...source.matchAll(expectedHeaderOrder)].length,
  3,
  "Every review table must show Extracted before Current FTW.",
);

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
  /Advanced details/,
  "Technical XML, logs, and fallback controls must be grouped under Advanced details.",
);

console.log("Guided filing review workflow passed.");
