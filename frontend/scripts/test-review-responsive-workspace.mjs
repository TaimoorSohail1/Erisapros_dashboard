import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
assert.match(source, /<details open className="review-workflow-disclosure">/, "Workflow must be expanded by default while remaining collapsible.");
assert.match(source, /className="review-issue-summary"[\s\S]*?Review status details/, "Issues need one compact summary with accessible full details.");
assert.match(source, /function ReviewStatusDrawer[\s\S]*?useDialogFocus[\s\S]*?aria-modal="true"/, "Full status details must remain available in a keyboard-accessible drawer.");
for (const label of ["Field", "Extracted", "Current FTW", "Proposed To Send", "Status"]) {
  assert.ok(source.includes(`data-label="${label}"`), `Mobile cards must identify ${label}.`);
}
assert.match(styles, /\.approval-workspace-page \.approval-table-wrap\s*\{[^}]*max-height:\s*none;[^}]*overflow:\s*visible;/, "Field rows must not have a nested vertical scroller.");
assert.match(styles, /\.approval-workspace-page \.schedule-a-broker-table-wrap\s*\{[^}]*max-height:\s*none;/, "Broker rows must not be height-constrained.");
assert.match(styles, /@media \(max-width: 640px\)[\s\S]*?content:\s*attr\(data-label\)/, "Small screens need labelled field cards.");
assert.match(styles, /\.approval-workspace-page \.approval-decision-table td:nth-child\(n\)[\s\S]*?width:\s*100%/, "Mobile cards must override desktop column-width selectors.");
assert.match(styles, /\.approval-workspace-page \.approval-filter-row\s*\{[^}]*flex:\s*0 0 auto;/, "Wrapped filters must grow rather than overlap field rows.");
assert.match(styles, /\.approval-workspace-page \.approval-count-tabs\s*\{[^}]*flex:\s*0 0 auto;/, "Wrapped review tabs must not create a second vertical scroller.");
assert.match(styles, /\.approval-workspace-page \.broker-edit-form > \*\s*\{[^}]*grid-column:\s*1 \/ -1;/, "Desktop editor column spans must not create narrow implicit mobile columns.");
assert.match(styles, /\.approval-workspace-page \.approval-stepper\s*\{[^}]*grid-template-columns:\s*repeat\(5, minmax\(0, 1fr\)\);/, "The five workflow stages must fill the desktop row without an empty approval column.");
assert.match(styles, /\.approval-workspace-page \.approval-stepper\s*\{[^}]*grid-template-columns:\s*repeat\(5, minmax\(118px, 1fr\)\);[^}]*min-width:\s*590px;/, "Small-screen workflow scrolling must reserve width for five stages, not six.");
assert.match(source, /className="skeleton-workflow card">\s*\{Array\.from\(\{ length: 5 \}/, "The loading workflow must also contain five stages.");
console.log("Responsive review workspace contracts passed.");
