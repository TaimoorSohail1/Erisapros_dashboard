import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const types = await readFile(new URL("../src/types.ts", import.meta.url), "utf8");
const qaHarness = await readFile(new URL("./qa-review.tsx", import.meta.url), "utf8");

assert.match(types, /export interface FTWilliamsUpdateReceipt/, "The API contract must type the verified FT Williams receipt.");
assert.match(types, /update_receipt\?: FTWilliamsUpdateReceipt \| null/, "A filing review must expose its persisted update receipt.");
assert.match(source, /function FTWUpdateSuccessNotice[\s\S]*?review\.update_receipt/, "The verified success surface must use the persisted receipt.");
assert.match(source, /New Schedule A created/, "The receipt must identify a newly created Schedule A.");
assert.match(source, /Existing Schedule A updated/, "The receipt must identify an updated Schedule A.");
assert.match(source, /Verified by FT Williams/, "The receipt must state that FT Williams read-back verified the update.");
assert.match(source, /Open in FT Williams/, "The receipt must link to the FT Williams destination.");
assert.match(source, /View verified PDF/, "The receipt must expose generated PDF evidence when available.");
assert.match(source, /const \[auditPdfBusy, setAuditPdfBusy\] = useState\(false\)/, "Opening verified evidence must have an explicit loading state.");
assert.match(source, /async function viewFtwAuditPdf[\s\S]*?setAuditPdfBusy\(true\)[\s\S]*?await openFTWilliamsAuditPDF\(id\)[\s\S]*?finally[\s\S]*?setAuditPdfBusy\(false\)/, "The verified PDF loader must cover the complete request lifecycle.");
assert.match(source, /disabled=\{auditPdfBusy\}[\s\S]*?InlineLoader label="Preparing verified PDF"/, "The verified PDF button must disable itself and show progress while opening evidence.");
assert.match(source, /<th>Sent<\/th>[\s\S]*?<th>FT Williams returned<\/th>[\s\S]*?<th>Status<\/th>/, "The receipt must show sent and returned field values with status.");
assert.match(source, /isVerifiedFTWilliamsUpdate\(review\)/, "The receipt must remain gated by verified read-back success.");
assert.match(source, /verifiedUpdateComplete \? \([\s\S]*?FT Williams verified[\s\S]*?: \([\s\S]*?Send to FT Williams/, "A completed filing must replace the send action with a verified state.");
assert.match(source, /key: "REVIEW"[\s\S]*?state: updateSent \? "done"/, "A verified update must show the review workflow step as complete.");
assert.match(styles, /\.ftw-update-receipt-details/, "The receipt field evidence must have a responsive details surface.");
assert.match(qaHarness, /scenario === "verified-update"/, "Visual QA must include a read-only verified update receipt scenario.");

console.log("Verified FT Williams update receipt contracts passed.");
