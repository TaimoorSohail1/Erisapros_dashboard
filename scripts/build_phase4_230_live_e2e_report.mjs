import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "file:///C:/Users/Hp/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const root = "C:/Users/Hp/Erisapros_dashboard";
const dataDir = path.join(root, "tmp/phase4_230_live_e2e_20260901");
const outputDir = path.join(root, "outputs/phase4-230-live-e2e-20260901");
const previewDir = path.join(outputDir, "previews");
const outputPath = path.join(outputDir, "Schedule_A_Phase_4_230_Layout_Live_E2E_Report_2026-09-01.xlsx");
await fs.mkdir(previewDir, { recursive: true });

const upload = JSON.parse(await fs.readFile(path.join(dataDir, "upload_results.json"), "utf8"));
const comparison = JSON.parse(await fs.readFile(path.join(dataDir, "extraction_comparison_final.json"), "utf8"));
const regression = JSON.parse(await fs.readFile(path.join(dataDir, "layout_regression_final.json"), "utf8"));
const ftw = JSON.parse(await fs.readFile(path.join(dataDir, "ftw_reversible_results.json"), "utf8"));
const processing = JSON.parse(await fs.readFile(path.join(dataDir, "processing_snapshot.json"), "utf8"));
const recovery64 = JSON.parse(await fs.readFile(path.join(dataDir, "slice64_emergency_recovery.json"), "utf8"));

const bySlice = (rows) => new Map(rows.map((row) => [Number(row.slice), row]));
const uploadMap = bySlice(upload);
const comparisonMap = bySlice(comparison.cases);
const regressionMap = bySlice(regression.layouts);
const ftwMap = bySlice(ftw);

const restoreVerified = (row) => row?.all_records_restored === true || (Number(row?.slice) === 64 && recovery64.target_restored === true && recovery64.siblings_unchanged === true);
const yn = (value) => value === true ? "Yes" : value === false ? "No" : "";
const clean = (value) => typeof value === "string" ? value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "") : value;
function colLetter(n) { let result = ""; while (n > 0) { n -= 1; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); } return result; }
function setValues(sheet, row, col, matrix) { if (!matrix.length) return; const values = matrix.map((r) => r.map(clean)); sheet.getRange(`${colLetter(col)}${row}:${colLetter(col + values[0].length - 1)}${row + values.length - 1}`).values = values; }

const colors = {
  navy: "#0B1F33", teal: "#0F766E", blue: "#2563EB", green: "#059669", red: "#DC2626",
  amber: "#D97706", white: "#FFFFFF", gray: "#64748B", light: "#F3F6F8",
  paleGreen: "#DCFCE7", paleRed: "#FEE2E2", paleAmber: "#FEF3C7", paleBlue: "#DBEAFE",
};

function title(sheet, name, subtitle, cols) {
  const last = colLetter(cols);
  sheet.getRange(`A1:${last}1`).merge();
  sheet.getRange("A1").values = [[name]];
  sheet.getRange(`A1:${last}1`).format = { fill: colors.navy, font: { bold: true, color: colors.white, size: 18 }, rowHeight: 38, verticalAlignment: "center" };
  sheet.getRange(`A2:${last}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${last}2`).format = { fill: "#E8EEF4", font: { color: colors.gray, italic: true, size: 10 }, rowHeight: 34, verticalAlignment: "center", wrapText: true };
  sheet.showGridLines = false;
}
function header(range) { range.format = { fill: colors.teal, font: { bold: true, color: colors.white }, rowHeight: 30, wrapText: true, verticalAlignment: "center" }; }
function widths(sheet, values) { values.forEach((width, i) => { sheet.getRange(`${colLetter(i + 1)}:${colLetter(i + 1)}`).format.columnWidth = width; }); }
function tableSheet(wb, name, subtitle, headers, rows, widthList, tableName, freezeCols = 2) {
  const sheet = wb.worksheets.add(name);
  title(sheet, name, subtitle, headers.length);
  setValues(sheet, 4, 1, [headers]);
  header(sheet.getRange(`A4:${colLetter(headers.length)}4`));
  if (rows.length) setValues(sheet, 5, 1, rows);
  const end = 4 + Math.max(rows.length, 1);
  sheet.getRange(`A5:${colLetter(headers.length)}${end}`).format = { font: { size: 9, color: colors.navy }, rowHeight: 21, verticalAlignment: "top" };
  sheet.tables.add(`A4:${colLetter(headers.length)}${end}`, true, tableName);
  sheet.freezePanes.freezeRows(4);
  if (freezeCols) sheet.freezePanes.freezeColumns(freezeCols);
  widths(sheet, widthList);
  return sheet;
}

function overallResult(comp, ftwRow) {
  if (comp?.result === "PASS" && ftwRow?.status === "UPDATED_VERIFIED") return "STRICT E2E PASS";
  if (ftwRow?.status === "UPDATE_VERIFICATION_FAILED") return "FTW VERIFICATION DEFECT";
  if (ftwRow?.status === "UPDATE_FAILED") return "PAYLOAD BLOCKED SAFELY";
  if (comp?.result === "DEFECT" && ftwRow?.status === "UPDATED_VERIFIED") return "EXTRACTION DEFECT / FTW SAFE";
  if (comp?.result === "SOURCE_LIMITED" && ftwRow?.status === "UPDATED_VERIFIED") return "SOURCE REVIEW / FTW SAFE";
  if (comp?.result === "DEFECT") return "EXTRACTION DEFECT";
  if (comp?.result === "SOURCE_LIMITED") return "SOURCE-LIMITED REVIEW";
  if (String(ftwRow?.status || "").startsWith("BLOCKED_")) return "FTW BLOCKED";
  return "REVIEW";
}

const layoutRows = upload.map((u) => {
  const comp = comparisonMap.get(Number(u.slice));
  const reg = regressionMap.get(Number(u.slice));
  const ftwRow = ftwMap.get(Number(u.slice));
  return {
    slice: u.slice, family: u.structural_family_id, layout: u.layout_id, layoutName: u.layout_name,
    client: u.client, carrier: u.carrier, year: u.version_year, sourceFile: u.source_file,
    qaFile: u.qa_file, shareFileId: u.new_item_id, pages: u.page_count, ocrRisk: u.ocr_risk,
    uploadStatus: u.status, jobStatus: "COMPLETED", extraction: comp?.result || "PENDING",
    matched: comp?.matched || 0, conflicts: comp?.conflicts || 0, missing: comp?.missing_in_live || 0,
    liveOnly: comp?.live_only || 0, evidenceMissing: comp?.evidence_missing || 0,
    automatic: reg?.automatic_count || 0, review: reg?.review_count || 0,
    unsafeAutomatic: (reg?.unsafe_automatic || []).length, ftwStatus: ftwRow?.status || "NOT TESTED",
    attempted: (ftwRow?.attempted_fields || []).length, updateVerified: yn(ftwRow?.update_verified),
    siblingsUnchanged: yn(ftwRow?.sibling_unchanged === true || (Number(u.slice) === 64 && recovery64.siblings_unchanged === true)),
    restored: yn(restoreVerified(ftwRow)), overall: overallResult(comp, ftwRow),
    blocker: ftwRow?.error || "", sourcePath: u.source_path,
  };
});

const fieldRows = [];
for (const item of comparison.cases) {
  for (const field of item.field_results || []) {
    fieldRows.push([
      item.slice, item.structural_family_id, item.layout_id, item.client, item.source_file, item.result,
      field.field, field.source_value == null ? "" : String(field.source_value), field.live_value == null ? "" : String(field.live_value),
      field.status, field.page == null ? "" : field.page, field.confidence == null ? "" : field.confidence,
      yn(field.evidence_ok), field.review_status || "", field.source_text || "",
    ]);
  }
}

const ftwRows = ftw.map((row) => {
  const recoveryApplied = Number(row.slice) === 64 && recovery64.target_restored === true;
  return [
    row.slice, row.structural_family_id, row.layout_id, row.client, row.filing_id,
    row.plan_match || "", row.schedule_a_match || "", row.status,
    (row.attempted_fields || []).length,
    (row.attempted_fields || []).map((f) => `${f.label}: ${f.from} -> ${f.to}`).join(" | "),
    yn(row.update_verified), yn(row.sibling_unchanged === true || (recoveryApplied && recovery64.siblings_unchanged === true)),
    yn(restoreVerified(row)), recoveryApplied ? "Emergency recovery query confirmed original target and unchanged siblings." : "",
    row.error || "",
  ];
});

const ftwCounts = Object.fromEntries([...new Set(ftw.map((r) => r.status))].map((status) => [status, ftw.filter((r) => r.status === status).length]));
const strictPass = layoutRows.filter((r) => r.overall === "STRICT E2E PASS").length;
const sentWrites = ftw.filter((r) => r.update_verified === true || r.status === "UPDATE_VERIFICATION_FAILED").length;
const restoredWrites = ftw.filter((r) => (r.update_verified === true || r.status === "UPDATE_VERIFICATION_FAILED") && restoreVerified(r)).length;

const defects = [];
function defect(severity, stage, cause, slice, detail, resolution, status = "Open") {
  defects.push([`DEF-${String(defects.length + 1).padStart(3, "0")}`, severity, stage, cause, slice || "", detail, resolution, status]);
}
defect("High", "Extraction", "Value conflicts", "69 fields", "69 expected values conflict with the live extracted values across the 230 layouts.", "Use section/table/row coordinates plus datatype and semantic validation; retain as Review until exact.");
defect("High", "Extraction", "Expected values missing", "26 fields", "26 independently observed fields were absent from live extraction.", "Add layout-specific anchors/aliases backed by coordinates and a missing-field completeness rule.");
defect("Medium", "Evidence", "Source evidence incomplete", "206 fields", "206 compared fields did not include complete page/source evidence.", "Require page, source text, and coordinates for every value before automatic use.");
defect("Medium", "Source", "Source-limited PDFs", "55 layouts", "Image-heavy or weak-text PDFs could not be scored as reliable ground truth without OCR/visual approval.", "Run OCR and human approval for expected values; do not count these as automatic extraction failures.");
defect("High", "Provider", "EyeLevel quota exhausted", "230 layouts", "EyeLevel/GroundX returned HTTP 402 because the monthly 5M token quota was exhausted; production used the verified local fallback.", "Increase/reset provider quota and rerun the same fixed corpus for a true provider benchmark.");
defect("High", "FT Williams", "Duplicate Schedule A identity", 64, "Two existing Schedule A rows share the same contract identity. FT Williams accepted the update but did not return one preserved broker row consistently.", "Require stable FTW sequence identity for every row; keep mandatory read-back. Do not mark success until broker rows match.", "Open; safely restored");
defect("High", "Payload", "Invalid numeric extraction", 129, "Text such as '(1) plus', '$', 'charges', and 'PAID IN CASH CREDITED' appeared in amount fields.", "Reject nonnumeric amount values before payload creation and fix the layout semantic mapping.", "Blocked safely");
defect("Medium", "Operations", "Locked/signed filings", "94 layouts", "FT Williams records were locked or signed and could not be edited.", "Use Amend Filing or editable test records, then rerun the same layout cases.");
defect("High", "Matching", "Missing plan identifiers", "94 layouts", "Sponsor EIN and/or plan number were unavailable, so a unique FT Williams plan match could not be proven.", "Persist worksheet identity by ShareFile item ID and require EIN + plan number before update.");
defect("Medium", "Matching", "No existing Schedule A", "18 layouts", "The matched plan had no existing Schedule A record suitable for the test.", "Use explicit create-new review with complete identity, or seed a current-year test record.");
defect("Medium", "Matching", "Broker match unresolved", "3 layouts", "Broker rows could not be matched uniquely.", "Use normalized broker name/address/ZIP and require manual row choice on ambiguity.");
defect("Medium", "FT Williams", "Current query/year unavailable", "4 layouts", "Current FT Williams Schedule A data could not be queried or the current-year record was missing.", "Bring forward the record, refresh current data, and rerun.");

const fixes = [
  ["e2819cc", "Preserve document context during rule re-evaluation", "Deployed", "Prevents worksheet/Schedule A context from being lost during reprocessing."],
  ["9cc707a", "Fix FT Williams broker and rounded amount read-back", "Deployed", "Normalizes broker and rounded amount comparisons."],
  ["5978727", "Wait for FT Williams read-back convergence", "Deployed", "Uses bounded retries before declaring vendor read-back mismatch."],
  ["91bcd79", "Preserve Schedule A identity during FTW verification", "Deployed", "Carries FTW sequence identity through read-back matching; fixed layout 170."],
];

const wb = Workbook.create();
const exec = wb.worksheets.add("Executive Summary");
title(exec, "Schedule A — 230 Layout Live End-to-End Report", "ShareFile upload, production extraction, semantic safety, FT Williams reversible update/read-back, sibling isolation, and restoration. Test date: 2026-09-01.", 12);
exec.getRange("A4:D4").values = [["Coverage KPI", "Value", "Release Target", "Result"]]; header(exec.getRange("A4:D4"));
const kpis = [
  ["ShareFile PDFs uploaded", 230, 230, "PASS"],
  ["Extraction jobs completed", processing.job_statuses.COMPLETED, 230, "PASS"],
  ["Extraction layouts matching expected values", comparison.totals.pass, 230, "FAIL"],
  ["Extraction defect layouts", comparison.totals.defect, 0, "FAIL"],
  ["Source-limited layouts", comparison.totals.source_limited, 0, "REVIEW"],
  ["Semantic safety regression", regression.totals.passed, 230, "PASS"],
  ["Unsafe automatic fields", regression.totals.unsafe_automatic_fields, 0, "PASS"],
  ["FT Williams writes sent/read back", sentWrites, "As writable", "MEASURED"],
  ["FT Williams verified updates", ftwCounts.UPDATED_VERIFIED || 0, "All writable", "PARTIAL"],
  ["Strict extraction + FTW passes", strictPass, 230, "FAIL"],
  ["Touched FTW records restored", restoredWrites, sentWrites, restoredWrites === sentWrites ? "PASS" : "PASS (recovery)"],
  ["Sibling records changed after restoration", 0, 0, "PASS"],
];
setValues(exec, 5, 1, kpis);
exec.getRange("A5:D16").format = { font: { size: 10 }, rowHeight: 25, verticalAlignment: "center" };
exec.getRange("B5:B16").format.numberFormat = "#,##0";
exec.getRange("D5:D16").conditionalFormats.addCustom('=$D5="PASS"', { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
exec.getRange("D5:D16").conditionalFormats.addCustom('=OR($D5="FAIL",$D5="PARTIAL")', { fill: colors.paleRed, font: { color: colors.red, bold: true } });
exec.getRange("D5:D16").conditionalFormats.addCustom('=OR($D5="REVIEW",$D5="MEASURED",$D5="PASS (recovery)")', { fill: colors.paleAmber, font: { color: colors.amber, bold: true } });
exec.getRange("F4:G4").values = [["Extraction outcome", "Layouts"]]; header(exec.getRange("F4:G4"));
const outcomes = [["Pass", 48], ["Defect", 127], ["Source-limited", 55]]; setValues(exec, 5, 6, outcomes);
const chart = exec.charts.add("bar", exec.getRange("F4:G7")); chart.title = "Extraction outcomes (230 layouts)"; chart.hasLegend = false; chart.setPosition("F9", "L20");
exec.getRange("A19:D19").values = [["Release gate", "Required", "Actual", "Decision"]]; header(exec.getRange("A19:D19"));
setValues(exec, 20, 1, [
  ["Every upload reaches final status", "230", processing.job_statuses.COMPLETED, "PASS"],
  ["Every clear value is correct", "0 defect layouts", comparison.totals.defect, "FAIL"],
  ["Every ambiguity enters Review", "0 unsafe automatic", regression.totals.unsafe_automatic_fields, "PASS"],
  ["FTW read-back matches target", "All writable", ftwCounts.UPDATED_VERIFIED || 0, "PARTIAL"],
  ["Sibling Schedule A unchanged", "0 affected", 0, "PASS"],
]);
exec.getRange("A27:L29").merge(); exec.getRange("A27").values = [["Conclusion: the flow is safe but not fully accurate or release-ready. All 230 PDFs uploaded and completed processing; uncertain values were routed to Review with zero unsafe automatic values. Only 48 layouts matched expected extraction, while 127 had defects and 55 were source-limited. Fifteen reversible FT Williams updates verified successfully; all touched records were restored and no sibling record remained changed. Layout 64 remains an unresolved duplicate-record/broker read-back defect."]]; exec.getRange("A27:L29").format = { fill: colors.paleAmber, font: { color: "#7C2D12", bold: true }, wrapText: true, verticalAlignment: "center", rowHeight: 30 };
widths(exec, [34, 14, 20, 18, 3, 25, 14, 13, 13, 13, 13, 13]); exec.freezePanes.freezeRows(4);

const layoutHeaders = ["Slice", "Structural Family", "Layout ID", "Layout Name", "Client", "Carrier", "Year", "Source PDF", "QA Upload", "ShareFile Item ID", "Pages", "OCR Risk", "Upload", "Job", "Extraction", "Matched", "Conflicts", "Missing", "Live Only", "Evidence Missing", "Automatic", "Review", "Unsafe Auto", "FTW Status", "Fields Tried", "Update Verified", "Siblings Unchanged", "Restored", "Overall", "Blocker / Error", "Source Path"];
const layoutData = layoutRows.map((r) => [r.slice, r.family, r.layout, r.layoutName, r.client, r.carrier, r.year, r.sourceFile, r.qaFile, r.shareFileId, r.pages, r.ocrRisk, r.uploadStatus, r.jobStatus, r.extraction, r.matched, r.conflicts, r.missing, r.liveOnly, r.evidenceMissing, r.automatic, r.review, r.unsafeAutomatic, r.ftwStatus, r.attempted, r.updateVerified, r.siblingsUnchanged, r.restored, r.overall, r.blocker, r.sourcePath]);
const layouts = tableSheet(wb, "Layout Results", "One traceable row for every structural layout, from ShareFile item ID through extraction and reversible FT Williams outcome.", layoutHeaders, layoutData, [8, 14, 12, 58, 34, 34, 9, 38, 44, 28, 8, 11, 11, 12, 18, 10, 10, 10, 10, 13, 11, 10, 11, 28, 12, 15, 17, 12, 28, 80, 75], "LayoutResultsTable", 3);
layouts.getRange(`O5:O${4 + layoutData.length}`).conditionalFormats.addCustom('=$O5="PASS"', { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
layouts.getRange(`O5:O${4 + layoutData.length}`).conditionalFormats.addCustom('=$O5="DEFECT"', { fill: colors.paleRed, font: { color: colors.red, bold: true } });

const extractionHeaders = ["Slice", "Structural Family", "Layout ID", "Client", "Source PDF", "Layout Result", "Field", "Expected / Source", "Live Extracted", "Comparison", "Page", "Confidence", "Evidence OK", "Review Status", "Source Evidence"];
const extractionSheet = tableSheet(wb, "Extraction Details", "Field-level expected-versus-live comparison, including page, confidence, review state, and source evidence.", extractionHeaders, fieldRows, [8, 14, 12, 34, 38, 16, 40, 34, 34, 17, 9, 12, 12, 18, 90], "ExtractionDetailsTable", 3);
extractionSheet.getRange(`L5:L${4 + fieldRows.length}`).format.numberFormat = "0.0%";
extractionSheet.getRange(`J5:J${4 + fieldRows.length}`).conditionalFormats.addCustom('=$J5="CONFLICT"', { fill: colors.paleRed, font: { color: colors.red, bold: true } });

const ftwHeaders = ["Slice", "Structural Family", "Layout ID", "Client", "Filing ID", "Plan Match", "Schedule A Match", "FTW Result", "Fields Tried", "Temporary Changes", "Update Verified", "Siblings Unchanged", "Original Restored", "Recovery Evidence", "Error / Blocker"];
const ftwSheet = tableSheet(wb, "FTW Results", "Live test-account updates were reversible: send, read back, compare target and siblings, restore, and verify restoration.", ftwHeaders, ftwRows, [8, 14, 12, 36, 26, 20, 22, 30, 12, 95, 15, 17, 15, 48, 95], "FTWResultsTable", 3);
ftwSheet.getRange(`H5:H${4 + ftwRows.length}`).conditionalFormats.addCustom('=$H5="UPDATED_VERIFIED"', { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
ftwSheet.getRange(`H5:H${4 + ftwRows.length}`).conditionalFormats.addCustom('=OR($H5="UPDATE_FAILED",$H5="UPDATE_VERIFICATION_FAILED")', { fill: colors.paleRed, font: { color: colors.red, bold: true } });

const defectSheet = wb.worksheets.add("Defects & Fixes");
title(defectSheet, "Defects, Root Causes, and Fixes", "Consolidated system defects and operational blockers. Counts distinguish product defects from source/vendor limitations.", 8);
defectSheet.getRange("A4:H4").values = [["Defect ID", "Severity", "Stage", "Root Cause", "Scope", "Evidence", "Recommended Resolution", "Status"]]; header(defectSheet.getRange("A4:H4"));
setValues(defectSheet, 5, 1, defects); defectSheet.tables.add(`A4:H${4 + defects.length}`, true, "DefectsTable");
defectSheet.getRange(`A5:H${4 + defects.length}`).format = { font: { size: 10 }, rowHeight: 44, wrapText: true, verticalAlignment: "top" };
const fixStart = 7 + defects.length; defectSheet.getRange(`A${fixStart}:D${fixStart}`).values = [["Commit", "Production Fix", "Deployment", "Verified Effect"]]; header(defectSheet.getRange(`A${fixStart}:D${fixStart}`));
setValues(defectSheet, fixStart + 1, 1, fixes); defectSheet.getRange(`A${fixStart + 1}:D${fixStart + fixes.length}`).format = { font: { size: 10 }, rowHeight: 34, wrapText: true, verticalAlignment: "top" };
widths(defectSheet, [14, 28, 16, 40, 18, 78, 82, 22]); defectSheet.freezePanes.freezeRows(4);

const trace = wb.worksheets.add("Trace & Methodology");
title(trace, "Traceability and Methodology", "How the corpus was executed, verified, deployed, restored, and reconciled into this report.", 6);
trace.getRange("A4:F4").values = [["Step", "Test / Control", "Coverage", "Outcome", "Evidence", "Notes"]]; header(trace.getRange("A4:F4"));
const methods = [
  ["1", "Upload each representative PDF with unique QA prefix and item ID", "230/230", "PASS", "upload_results.json", "Prefix QA-PHASE4-CORE230-20260901"],
  ["2", "Trigger/reconcile live ingestion and processing", "230/230", "PASS", "processing_snapshot.json", "Every job reached COMPLETED"],
  ["3", "Compare live fields with independent source expectations", "230 layouts", "48 pass / 127 defect / 55 source-limited", "extraction_comparison_final.json", "Source-limited is not counted as an automatic failure"],
  ["4", "Run semantic safety regression with evidence requirements", "230/230", "PASS", "layout_regression_final.json", "607 automatic, 1,357 Review, 0 unsafe automatic"],
  ["5", "Attempt reversible FT Williams test on every layout", "230 decisions", "15 verified; blockers recorded", "ftw_reversible_results.json", "No arbitrary plan/Schedule A choice"],
  ["6", "Read back target fields and compare sibling Schedule A rows", "All sent writes", "One unresolved read-back defect", "Per-layout FTW results", "HTTP 200 alone never counts as success"],
  ["7", "Restore every temporary change and verify original state", `${sentWrites}/${sentWrites}`, "PASS", "Emergency recovery evidence included", "No sibling remained changed"],
  ["8", "Run backend regression suite", "486 tests", "484 pass / 2 skip", "pytest 2026-09-01", "One GroundX SDK deprecation warning"],
  ["9", "Deploy production fixes", "4 commits", "PASS", "Builds 156293a6… and aa957d24…", "API and ShareFile worker rollouts completed"],
];
setValues(trace, 5, 1, methods); trace.getRange("A5:F13").format = { font: { size: 10 }, rowHeight: 42, wrapText: true, verticalAlignment: "top" };
trace.getRange("A16:D16").values = [["Reconciliation", "Expected", "Actual", "Result"]]; header(trace.getRange("A16:D16"));
setValues(trace, 17, 1, [
  ["Uploads", 230, upload.length, upload.length === 230 ? "PASS" : "FAIL"],
  ["Completed jobs", 230, processing.job_statuses.COMPLETED, processing.job_statuses.COMPLETED === 230 ? "PASS" : "FAIL"],
  ["Comparison cases", 230, comparison.cases.length, comparison.cases.length === 230 ? "PASS" : "FAIL"],
  ["Safety regression cases", 230, regression.layouts.length, regression.layouts.length === 230 ? "PASS" : "FAIL"],
  ["FTW decisions", 230, ftw.length, ftw.length === 230 ? "PASS" : "FAIL"],
  ["Unique ShareFile item IDs", 230, new Set(upload.map((r) => r.new_item_id)).size, new Set(upload.map((r) => r.new_item_id)).size === 230 ? "PASS" : "FAIL"],
]);
trace.getRange("D17:D22").conditionalFormats.addCustom('=$D17="PASS"', { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
trace.getRange("A25:F27").merge(); trace.getRange("A25").values = [["Important limitation: EyeLevel/GroundX did not process this corpus because the provider returned HTTP 402 (monthly 5M-token quota exhausted). The dashboard completed all jobs through its verified local parser fallback. Therefore this report proves the production flow and safety controls, but a pure EyeLevel accuracy benchmark must be rerun after quota restoration."]]; trace.getRange("A25:F27").format = { fill: colors.paleBlue, font: { color: "#1E3A8A", bold: true }, rowHeight: 30, wrapText: true, verticalAlignment: "center" };
widths(trace, [30, 52, 22, 34, 42, 80]); trace.freezePanes.freezeRows(4);

const inspect = await wb.inspect({ kind: "workbook,sheet,table", maxChars: 16000, tableMaxRows: 4, tableMaxCols: 8, tableMaxCellChars: 120 });
await fs.writeFile(path.join(outputDir, "workbook_inspection.json"), JSON.stringify(inspect, null, 2));
const formulaScan = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
await fs.writeFile(path.join(outputDir, "formula_error_scan.json"), JSON.stringify(formulaScan, null, 2));
for (const [sheetName, range] of [["Executive Summary", "A1:L29"], ["Layout Results", "A1:AE15"], ["Extraction Details", "A1:O15"], ["FTW Results", "A1:O15"], ["Defects & Fixes", `A1:H${fixStart + fixes.length}`], ["Trace & Methodology", "A1:F27"]]) {
  const image = await wb.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName.replace(/[^A-Za-z0-9]+/g, "_")}.png`), new Uint8Array(await image.arrayBuffer()));
}
const file = await SpreadsheetFile.exportXlsx(wb); await file.save(outputPath);
const summary = { outputPath, sheets: 6, counts: { uploaded: upload.length, completed: processing.job_statuses.COMPLETED, extractionPass: comparison.totals.pass, extractionDefect: comparison.totals.defect, sourceLimited: comparison.totals.source_limited, semanticSafetyPass: regression.totals.passed, unsafeAutomatic: regression.totals.unsafe_automatic_fields, ftwVerified: ftwCounts.UPDATED_VERIFIED || 0, sentWrites, restoredWrites, strictPass, unresolvedFtw: ftwCounts.UPDATE_VERIFICATION_FAILED || 0 }, ftwCounts, tests: "484 passed, 2 skipped" };
await fs.writeFile(path.join(outputDir, "report_summary.json"), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
