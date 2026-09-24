import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "file:///C:/Users/Hp/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const root = "C:/Users/Hp/Erisapros_dashboard";
const dataDir = path.join(root, "tmp/phase2_live_flow_20260831");
const outputDir = path.join(root, "outputs/phase2-schedule-a-live-e2e-20260901");
const previewDir = path.join(outputDir, "previews");
const mainPath = path.join(outputDir, "Schedule_A_Phase_2_Live_E2E_Report_2026-09-01.xlsx");
const defectPath = path.join(outputDir, "Schedule_A_Phase_2_Defects_2026-09-01.xlsx");
await fs.mkdir(previewDir, { recursive: true });

const manifest = JSON.parse(await fs.readFile(path.join(dataDir, "core_layout_manifest.json"), "utf8"));
const live = JSON.parse(await fs.readFile(path.join(dataDir, "live_results.json"), "utf8"));
const comparison = JSON.parse(await fs.readFile(path.join(dataDir, "extraction_comparison.json"), "utf8"));
const ftw = JSON.parse(await fs.readFile(path.join(dataDir, "ftw_e2e_results.json"), "utf8"));

const bySlice = (rows, key = "slice") => new Map(rows.map((r) => [Number(r[key]), r]));
const liveMap = new Map(live.map((r) => [Number(r.upload.slice), r]));
const compareMap = bySlice(comparison.cases);
const ftwMap = bySlice(ftw);

function liveStatus(row) {
  if (!row?.filing) return "NOT_DISCOVERED";
  return String(row.filing.status || row.filing.filing_status || "DISCOVERED");
}

function yn(value) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "";
}

function overallResult(comp, update, status) {
  if (status === "NOT_DISCOVERED" || status === "WAITING_FOR_WORKSHEET") return "INGESTION BLOCKED";
  if (comp?.result === "DEFECT") return "EXTRACTION DEFECT";
  if (comp?.result === "SOURCE_LIMITED") return "SOURCE-LIMITED REVIEW";
  if (update?.update_status === "VERIFIED" && update?.sibling_unchanged === true) return "E2E PASS";
  if (update?.update_status === "VERIFIED" && update?.sibling_unchanged === false) return "ISOLATION DEFECT";
  if (update?.update_status === "FAILED_VERIFICATION") return "UPDATE DEFECT";
  if (update?.update_status) return "UPDATE BLOCKED";
  return "NOT COMPLETED";
}

const layoutRows = manifest.map((m) => {
  const l = liveMap.get(Number(m.slice));
  const c = compareMap.get(Number(m.slice));
  const u = ftwMap.get(Number(m.slice));
  const status = liveStatus(l);
  return {
    ...m,
    filingId: String(l?.filing?._id || ""),
    liveStatus: status,
    extractionProvider: String(c?.extraction_provider || ""),
    extractionResult: String(c?.result || "PENDING"),
    matched: Number(c?.matched || 0),
    conflicts: Number(c?.conflicts || 0),
    missing: Number(c?.missing_in_live || 0),
    liveOnly: Number(c?.live_only || 0),
    evidenceMissing: Number(c?.evidence_missing || 0),
    brokerMatch: c?.broker_match === true ? "Match" : c?.broker_match === false ? "Mismatch" : "",
    fieldConfirmation: String(u?.field_confirmation || ""),
    planMatch: String(u?.plan_match || ""),
    scheduleAMatch: String(u?.schedule_a_match || ""),
    brokerResolution: String(u?.broker_match || ""),
    updateStatus: String(u?.update_status || "NOT_TESTED"),
    readbackVerified: yn(u?.readback_verified),
    siblingUnchanged: yn(u?.sibling_unchanged),
    attemptedFields: Number(u?.attempted_fields || 0),
    confirmedFields: Number(u?.confirmed_fields || 0),
    remainingFields: Number(u?.remaining_fields || 0),
    error: String(u?.error || ""),
    overall: overallResult(c, u, status),
  };
});

const fieldRows = [];
for (const c of comparison.cases) {
  for (const f of c.field_results || []) {
    fieldRows.push({
      slice: c.slice,
      family: c.structural_family_id,
      layout: c.layout_id,
      client: c.client,
      file: c.source_file,
      result: c.result,
      field: f.field,
      sourceValue: f.source_value == null ? "" : String(f.source_value),
      liveValue: f.live_value == null ? "" : String(f.live_value),
      status: f.status,
      page: f.page == null ? "" : Number(f.page),
      confidence: f.confidence == null ? "" : Number(f.confidence),
      evidenceOk: yn(f.evidence_ok),
      sourceText: String(f.source_text || ""),
    });
  }
}

function classifyFtwError(error, status) {
  const e = String(error || "");
  if (status === "BLOCKED_FTW_LOCKED") return e.includes("pre-send validation failed") ? "Locked filing + invalid payload" : "Locked/signed FTW filing";
  if (status === "FAILED_VERIFICATION") return e.includes("error 62") ? "FTW field rejected" : "FTW read-back mismatch";
  if (status === "BLOCKED_PLAN_MATCH") return "Missing plan identity";
  if (status === "BLOCKED_CURRENT_YEAR_MISSING" || status === "BLOCKED_CURRENT_QUERY") return "Current-year FTW record missing";
  if (e.includes("Schedule A payload is required")) return "Schedule A payload missing";
  if (e.includes("plan year conflict")) return "Plan-year conflict unresolved";
  return status || "Update blocker";
}

const fixByCause = {
  "Ingestion did not discover upload": "Fix ShareFile webhook/index reconciliation; retry undiscovered item IDs and prove one filing per upload.",
  "Worksheet pairing blocked ingestion": "Allow Schedule A-only QA ingestion or pair deterministically with the correct plan worksheet.",
  "Source-limited/OCR review": "Run OCR, retain coordinates, and require visual approval before accuracy scoring or FTW update.",
  "Extraction value conflict": "Use section/table coordinates and semantic type rules; reject cross-row or cross-section values.",
  "Expected field missing": "Improve layout-aware field mapping and aliases; require explicit missing-field review.",
  "Missing source evidence": "Require page, coordinates, and source text for every extracted value.",
  "FTW read-back mismatch": "Treat acceptance as provisional; compare normalized sent/read-back values and retry only after diagnosis.",
  "FTW field rejected": "Validate required values, datatype, length, date range, and legal carrier/name rules before sending.",
  "Sibling Schedule A changed": "Use stable FTW sequence identity and diff all non-target Schedule A records before committing success.",
  "Locked/signed FTW filing": "Use FT Williams Amend Filing or an editable current-year test filing, then rerun.",
  "Locked filing + invalid payload": "Fix semantic payload validation first; then amend/unlock the FTW test filing before retry.",
  "Missing plan identity": "Extract/require sponsor EIN and plan number; use manual best-score selection only with visible evidence.",
  "Current-year FTW record missing": "Use FT Williams Bring Forward, refresh current data, then rerun.",
  "Schedule A payload missing": "Build a complete Schedule A payload whenever the filing has an attached Schedule A.",
  "Plan-year conflict unresolved": "Require one explicit plan-year decision and apply it consistently to Form 5500 and all attached Schedule A records.",
};

const defects = [];
function addDefect(row) {
  defects.push({ id: `DEF-${String(defects.length + 1).padStart(4, "0")}`, ...row, fix: fixByCause[row.cause] || "Review the evidence and add a regression test before retrying." });
}
for (const r of layoutRows) {
  if (r.liveStatus === "NOT_DISCOVERED") addDefect({ severity: "High", stage: "Ingestion", cause: "Ingestion did not discover upload", slice: r.slice, family: r.structural_family_id, layout: r.layout_id, client: r.client, file: r.source_file, field: "", expected: "Filing discovered", actual: "NOT_DISCOVERED", detail: "Uploaded successfully but no live filing was created." });
  if (r.liveStatus === "WAITING_FOR_WORKSHEET") addDefect({ severity: "High", stage: "Ingestion", cause: "Worksheet pairing blocked ingestion", slice: r.slice, family: r.structural_family_id, layout: r.layout_id, client: r.client, file: r.source_file, field: "", expected: "Schedule A extraction", actual: "WAITING_FOR_WORKSHEET", detail: "Live flow did not proceed without its required worksheet pair." });
  if (r.extractionResult === "SOURCE_LIMITED") addDefect({ severity: "Medium", stage: "Source QA", cause: "Source-limited/OCR review", slice: r.slice, family: r.structural_family_id, layout: r.layout_id, client: r.client, file: r.source_file, field: "", expected: "Independent source values", actual: "Insufficient text-layer evidence", detail: `OCR risk: ${r.ocr_risk}; source requires visual/OCR approval.` });
}
for (const f of fieldRows) {
  if (f.status === "CONFLICT") addDefect({ severity: "High", stage: "Extraction", cause: "Extraction value conflict", slice: f.slice, family: f.family, layout: f.layout, client: f.client, file: f.file, field: f.field, expected: f.sourceValue, actual: f.liveValue, detail: `Page ${f.page || "unknown"}; confidence ${f.confidence || "unknown"}.` });
  if (f.status === "MISSING_IN_LIVE") addDefect({ severity: "High", stage: "Extraction", cause: "Expected field missing", slice: f.slice, family: f.family, layout: f.layout, client: f.client, file: f.file, field: f.field, expected: f.sourceValue, actual: "Missing", detail: `Expected source field was not returned by the live extraction.` });
  if (f.status === "MATCH" && f.evidenceOk !== "Yes") addDefect({ severity: "Medium", stage: "Evidence", cause: "Missing source evidence", slice: f.slice, family: f.family, layout: f.layout, client: f.client, file: f.file, field: f.field, expected: "Page/coordinate/source evidence", actual: "Evidence incomplete", detail: `Value matched but could not be safely traced to source evidence.` });
}
for (const u of ftw) {
  const m = manifest.find((x) => Number(x.slice) === Number(u.slice));
  if (u.update_status !== "VERIFIED") {
    const cause = classifyFtwError(u.error, u.update_status);
    const blockerOnly = ["Locked/signed FTW filing", "Current-year FTW record missing"].includes(cause);
    addDefect({ severity: blockerOnly ? "Medium" : "High", stage: "FT Williams", cause, slice: u.slice, family: u.structural_family_id, layout: u.layout_id, client: u.client, file: m?.source_file || "", field: "", expected: "Accepted and matching read-back", actual: u.update_status, detail: String(u.error || "No additional error message returned.") });
  }
  if (u.sibling_unchanged === false) addDefect({ severity: "Critical", stage: "Isolation", cause: "Sibling Schedule A changed", slice: u.slice, family: u.structural_family_id, layout: u.layout_id, client: u.client, file: m?.source_file || "", field: "Non-target Schedule A record", expected: "Unchanged", actual: "Changed read-back signature", detail: `Target update status: ${u.update_status}.` });
}

const rootCauses = [...new Set(defects.map((d) => d.cause))].map((cause) => {
  const rows = defects.filter((d) => d.cause === cause);
  const priority = rows.some((d) => d.severity === "Critical") ? "Critical" : rows.some((d) => d.severity === "High") ? "High" : "Medium";
  return { cause, count: rows.length, layouts: new Set(rows.map((d) => d.family)).size, priority, fix: fixByCause[cause] || "Review and add regression coverage." };
}).sort((a, b) => (({ Critical: 0, High: 1, Medium: 2 })[a.priority] - ({ Critical: 0, High: 1, Medium: 2 })[b.priority]) || b.count - a.count);

const counts = {
  uploaded: layoutRows.length,
  discovered: layoutRows.filter((r) => r.liveStatus !== "NOT_DISCOVERED").length,
  extracted: layoutRows.filter((r) => ["PASS", "DEFECT", "SOURCE_LIMITED"].includes(r.extractionResult)).length,
  extractionPass: layoutRows.filter((r) => r.extractionResult === "PASS").length,
  extractionDefect: layoutRows.filter((r) => r.extractionResult === "DEFECT").length,
  sourceLimited: layoutRows.filter((r) => r.extractionResult === "SOURCE_LIMITED").length,
  ingestionBlocked: layoutRows.filter((r) => ["NOT_DISCOVERED", "WAITING_FOR_WORKSHEET"].includes(r.liveStatus)).length,
  ftwVerified: ftw.filter((r) => r.update_status === "VERIFIED").length,
  safeFtwPass: ftw.filter((r) => r.update_status === "VERIFIED" && r.sibling_unchanged === true).length,
  e2ePass: layoutRows.filter((r) => r.overall === "E2E PASS").length,
  updateDefects: ftw.filter((r) => r.update_status === "FAILED_VERIFICATION").length,
  siblingImpact: ftw.filter((r) => r.sibling_unchanged === false).length,
  ftwLocked: ftw.filter((r) => r.update_status === "BLOCKED_FTW_LOCKED").length,
  planMatchBlocked: ftw.filter((r) => r.update_status === "BLOCKED_PLAN_MATCH").length,
};

const colors = { navy: "#0B1F33", teal: "#0F766E", green: "#059669", red: "#DC2626", amber: "#D97706", blue: "#2563EB", white: "#FFFFFF", gray: "#64748B", border: "#D6DEE7", light: "#F3F6F8", paleGreen: "#DCFCE7", paleRed: "#FEE2E2", paleAmber: "#FEF3C7", paleBlue: "#DBEAFE" };
function colLetter(n) { let s = ""; while (n > 0) { n--; s = String.fromCharCode(65 + (n % 26)) + s; n = Math.floor(n / 26); } return s; }
function cleanCell(value) { return typeof value === "string" ? value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "") : value; }
function setValues(sheet, row, col, matrix) { if (!matrix.length || !matrix[0].length) return; const clean = matrix.map((r) => r.map(cleanCell)); sheet.getRange(`${colLetter(col)}${row}:${colLetter(col + clean[0].length - 1)}${row + clean.length - 1}`).values = clean; }
function title(sheet, text, subtitle, cols) { const last = colLetter(cols); sheet.getRange(`A1:${last}1`).merge(); sheet.getRange("A1").values = [[text]]; sheet.getRange(`A1:${last}1`).format = { fill: colors.navy, font: { bold: true, color: colors.white, size: 18 }, rowHeight: 36, verticalAlignment: "center" }; sheet.getRange(`A2:${last}2`).merge(); sheet.getRange("A2").values = [[subtitle]]; sheet.getRange(`A2:${last}2`).format = { fill: "#E8EEF4", font: { color: colors.gray, italic: true, size: 10 }, rowHeight: 32, verticalAlignment: "center", wrapText: true }; sheet.showGridLines = false; }
function header(range) { range.format = { fill: colors.teal, font: { bold: true, color: colors.white }, rowHeight: 30, wrapText: true, verticalAlignment: "center" }; }
function widths(sheet, values) { values.forEach((v, i) => { sheet.getRange(`${colLetter(i + 1)}:${colLetter(i + 1)}`).format.columnWidth = v; }); }
function tableSheet(wb, name, subtitle, headers, rows, widthsList, tableName, freezeCols = 1) { const s = wb.worksheets.add(name); title(s, name, subtitle, headers.length); setValues(s, 4, 1, [headers]); header(s.getRange(`A4:${colLetter(headers.length)}4`)); if (rows.length) setValues(s, 5, 1, rows); const end = 4 + Math.max(rows.length, 1); s.getRange(`A5:${colLetter(headers.length)}${end}`).format = { font: { size: 9, color: colors.navy }, verticalAlignment: "top", rowHeight: 20 }; s.tables.add(`A4:${colLetter(headers.length)}${end}`, true, tableName); s.freezePanes.freezeRows(4); if (freezeCols) s.freezePanes.freezeColumns(freezeCols); widths(s, widthsList); return s; }
function statusColors(range, cell) { range.conditionalFormats.addCustom(`=${cell}="E2E PASS"`, { fill: colors.paleGreen, font: { color: colors.green, bold: true } }); range.conditionalFormats.addCustom(`=OR(${cell}="EXTRACTION DEFECT",${cell}="UPDATE DEFECT",${cell}="ISOLATION DEFECT")`, { fill: colors.paleRed, font: { color: colors.red, bold: true } }); range.conditionalFormats.addCustom(`=OR(${cell}="INGESTION BLOCKED",${cell}="UPDATE BLOCKED",${cell}="SOURCE-LIMITED REVIEW")`, { fill: colors.paleAmber, font: { color: colors.amber, bold: true } }); }

function buildMainWorkbook() {
  const wb = Workbook.create();
  const exec = wb.worksheets.add("Executive Summary");
  title(exec, "Schedule A Phase 2 — Live End-to-End QA", "One representative from each of 230 core structural families; live extraction, field confirmation, FT Williams update, read-back, and sibling isolation.", 12);
  exec.getRange("A4:D4").values = [["Coverage KPI", "Value", "Meaning", "Status"]]; header(exec.getRange("A4:D4"));
  const kpis = [
    ["Core layouts uploaded", counts.uploaded, "One representative per structural family", "Complete"],
    ["Layouts discovered by ingestion", counts.discovered, "Live filing created", counts.discovered === counts.uploaded ? "Pass" : "Blocked"],
    ["Layouts extracted", counts.extracted, "Reached field comparison", counts.extracted === counts.uploaded ? "Pass" : "Partial"],
    ["Extraction comparison pass", counts.extractionPass, "No detected conflicts/missing evidence", counts.extractionPass === counts.extracted ? "Pass" : "Defects"],
    ["Extraction defects", counts.extractionDefect, "Conflicts, missing values, or evidence failures", counts.extractionDefect ? "Action required" : "Pass"],
    ["Source-limited layouts", counts.sourceLimited, "Needs OCR/visual expected values", counts.sourceLimited ? "Review" : "Pass"],
    ["FT Williams target verified", counts.ftwVerified, "Target values matched read-back", "Measured"],
    ["Safe FTW update/isolation passes", counts.safeFtwPass, "Target verified and siblings unchanged", "Measured"],
    ["Strict whole-flow passes", counts.e2ePass, "Extraction passed, FTW verified, siblings unchanged", counts.e2ePass === counts.uploaded ? "Pass" : "Not release-ready"],
    ["FT Williams verification defects", counts.updateDefects, "Rejected or accepted values did not verify", counts.updateDefects ? "Action required" : "Pass"],
    ["Sibling-isolation failures", counts.siblingImpact, "A non-target Schedule A signature changed", counts.siblingImpact ? "Critical" : "Pass"],
    ["Locked FT Williams filings", counts.ftwLocked, "Could not mutate without Amend Filing", "Operational blocker"],
  ];
  setValues(exec, 5, 1, kpis);
  exec.getRange("B5:B16").format.numberFormat = "#,##0";
  exec.getRange("A5:D16").format = { font: { size: 10 }, rowHeight: 25, verticalAlignment: "center" };
  exec.getRange("A18:F18").values = [["Release Gate", "Required", "Actual", "Result", "Evidence", "Decision"]]; header(exec.getRange("A18:F18"));
  const gateRows = [
    ["All layouts enter extraction", 230, counts.extracted, counts.extracted === 230 ? "PASS" : "FAIL", `${counts.ingestionBlocked} blocked before extraction`, "Fix ingestion/worksheet pairing"],
    ["Clear values extract correctly", 0, counts.extractionDefect, counts.extractionDefect === 0 ? "PASS" : "FAIL", `${counts.extractionDefect} layout-level defects`, "Fix semantic validation"],
    ["FTW target read-back matches", 0, counts.updateDefects, counts.updateDefects === 0 ? "PASS" : "FAIL", `${counts.updateDefects} failed verification`, "Keep mandatory read-back"],
    ["Unrelated Schedule A unchanged", 0, counts.siblingImpact, counts.siblingImpact === 0 ? "PASS" : "FAIL", `${counts.siblingImpact} sibling failures`, "Fix row identity/isolation"],
  ];
  setValues(exec, 19, 1, gateRows);
  exec.getRange("D19:D22").conditionalFormats.addCustom("=$D19=\"PASS\"", { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
  exec.getRange("D19:D22").conditionalFormats.addCustom("=$D19=\"FAIL\"", { fill: colors.paleRed, font: { color: colors.red, bold: true } });
  exec.getRange("A24:L25").merge(); exec.getRange("A24").values = [["Conclusion: the live flow is not release-ready. Upload succeeded for all 230 representatives, but 61 never entered extraction, 94 extracted layouts showed defects, 15 passed FT Williams update plus sibling isolation, and only 4 passed the strict whole-flow gate. The main fixes are ingestion/worksheet pairing, coordinate-backed semantic validation, payload normalization, and FT Williams read-back/isolation safeguards."]]; exec.getRange("A24:L25").format = { fill: colors.paleAmber, font: { color: "#7C2D12", bold: true }, wrapText: true, verticalAlignment: "center", rowHeight: 32 };
  exec.getRange("H4:I4").values = [["Outcome", "Layouts"]]; header(exec.getRange("H4:I4"));
  const outcomeRows = [...new Set(layoutRows.map((r) => r.overall))].map((o) => [o, layoutRows.filter((r) => r.overall === o).length]).sort((a, b) => b[1] - a[1]); setValues(exec, 5, 8, outcomeRows);
  const chart = exec.charts.add("bar", exec.getRange(`H4:I${4 + outcomeRows.length}`)); chart.title = "Layout outcomes"; chart.hasLegend = false; chart.setPosition("H12", "L23");
  exec.freezePanes.freezeRows(4); widths(exec, [34, 12, 45, 22, 26, 42, 3, 28, 12, 12, 12, 12]);

  const layoutHeaders = ["Slice", "Structural Family", "Layout ID", "Layout Name", "Client", "Source PDF", "Template Family", "Carrier", "Year", "Pages", "OCR Risk", "Live Status", "Extraction Result", "Matched", "Conflicts", "Missing", "Live Only", "Evidence Missing", "Field Confirmation", "Plan Match", "Schedule A Match", "Broker Resolution", "FTW Update", "Read-back Verified", "Sibling Unchanged", "Attempted Fields", "Confirmed Fields", "Remaining Fields", "Overall Result", "Error / Blocker", "Source Path"];
  const layoutData = layoutRows.map((r) => [r.slice, r.structural_family_id, r.layout_id, r.layout_name, r.client, r.source_file, r.template_family, r.carrier, r.version_year, r.page_count, r.ocr_risk, r.liveStatus, r.extractionResult, r.matched, r.conflicts, r.missing, r.liveOnly, r.evidenceMissing, r.fieldConfirmation, r.planMatch, r.scheduleAMatch, r.brokerResolution, r.updateStatus, r.readbackVerified, r.siblingUnchanged, r.attemptedFields, r.confirmedFields, r.remainingFields, r.overall, r.error, r.source_path]);
  const layoutsSheet = tableSheet(wb, "Layout E2E Results", "One verified outcome row for every core structural family representative.", layoutHeaders, layoutData, [8, 14, 12, 52, 36, 40, 28, 30, 10, 8, 11, 22, 19, 10, 10, 10, 10, 13, 27, 20, 23, 22, 24, 16, 16, 12, 12, 12, 21, 70, 75], "LayoutE2EResultsTable", 4); statusColors(layoutsSheet.getRange(`AC5:AC${4 + layoutData.length}`), "$AC5");

  const fieldHeaders = ["Slice", "Structural Family", "Layout ID", "Client", "Source PDF", "Layout Result", "Field", "Expected / Source", "Live Extracted", "Comparison", "Page", "Confidence", "Evidence OK", "Source Evidence"];
  const fieldData = fieldRows.map((f) => [f.slice, f.family, f.layout, f.client, f.file, f.result, f.field, f.sourceValue, f.liveValue, f.status, f.page, f.confidence, f.evidenceOk, f.sourceText]);
  const fields = tableSheet(wb, "Field Comparison", "Independent source comparison for every field returned or expected in completed layouts.", fieldHeaders, fieldData, [8, 14, 12, 34, 38, 16, 36, 32, 32, 16, 9, 11, 12, 75], "FieldComparisonTable", 3); fields.getRange(`L5:L${4 + fieldData.length}`).format.numberFormat = "0.0%"; fields.getRange(`J5:J${4 + fieldData.length}`).conditionalFormats.addCustom("=$J5=\"CONFLICT\"", { fill: colors.paleRed, font: { color: colors.red, bold: true } });

  const ftwHeaders = ["Slice", "Structural Family", "Layout ID", "Client", "Filing ID", "Field Confirmation", "Plan Match", "Schedule A Match", "Broker Match", "Update Status", "Read-back Verified", "Sibling Unchanged", "Attempted", "Confirmed", "Remaining", "Error / Blocker"];
  const ftwData = ftw.map((u) => [u.slice, u.structural_family_id, u.layout_id, u.client, u.filing_id, u.field_confirmation, u.plan_match, u.schedule_a_match, u.broker_match, u.update_status, yn(u.readback_verified), yn(u.sibling_unchanged), u.attempted_fields || 0, u.confirmed_fields || 0, u.remaining_fields || 0, u.error || ""]);
  tableSheet(wb, "FTW Update Results", "Field confirmation, Schedule A matching, live update, read-back verification, and sibling isolation.", ftwHeaders, ftwData, [8, 14, 12, 36, 26, 28, 22, 24, 22, 25, 16, 16, 11, 11, 11, 85], "FTWUpdateResultsTable", 3);

  const defectHeaders = ["Defect ID", "Severity", "Stage", "Root Cause", "Slice", "Structural Family", "Layout ID", "Client", "Source PDF", "Field", "Expected", "Actual", "Evidence / Detail", "Recommended Fix"];
  const defectData = defects.map((d) => [d.id, d.severity, d.stage, d.cause, d.slice, d.family, d.layout, d.client, d.file, d.field, d.expected, d.actual, d.detail, d.fix]);
  const defectSheet = tableSheet(wb, "Defects & Blockers", "All detected defects, source limitations, and operational blockers with recommended fixes.", defectHeaders, defectData, [12, 11, 15, 32, 8, 14, 12, 34, 38, 35, 32, 32, 80, 75], "DefectsBlockersTable", 4); defectSheet.getRange(`B5:B${4 + defectData.length}`).conditionalFormats.addCustom("=$B5=\"Critical\"", { fill: colors.paleRed, font: { color: "#991B1B", bold: true } });

  const qa = wb.worksheets.add("Methodology & QA"); title(qa, "Methodology and Quality Assurance", "Scope, controls, limitations, and release decision.", 6);
  qa.getRange("A4:F4").values = [["Step", "What was tested", "Coverage", "Control", "Outcome", "Notes"]]; header(qa.getRange("A4:F4"));
  const methods = [
    ["1. Representative selection", "One PDF per core structural family", "230/230", "Phase 1 structural catalog", "Complete", "Layout/client independent coverage."],
    ["2. Live upload", "Upload to live ShareFile/dashboard flow", "230/230", "Unique QA filename and item ID", "Complete", "No upload failures."],
    ["3. Source audit", "Every page rendered/parsed for expected values", "1,060 pages", "Independent local parser plus visual renders", "Complete", "42 completed layouts remained source-limited."],
    ["4. Live extraction", "EyeLevel/GroundX or configured live fallback", `${counts.extracted}/230`, "Persisted live fields and review state", "Partial", `${counts.ingestionBlocked} blocked before extraction.`],
    ["5. Field comparison", "Values and page/source evidence", `${counts.extracted} layouts`, "Conflict, missing, evidence checks", "Defects found", `${counts.extractionDefect} layout-level defects.`],
    ["6. Field confirmation", "Keep Extracted to Will Update", `${ftw.length} layouts`, "Same-value live edit endpoint", "Executed", "Exact status retained per layout."],
    ["7. FTW matching", "Auto match, manual best score, or create new", `${ftw.length} layouts`, "No arbitrary wrong-row selection", "Executed", "Missing identity remains blocked."],
    ["8. FTW update/read-back", "Send, query current, compare target fields", `${ftw.length} decisions`, "HTTP success not accepted without read-back", "Measured", `${counts.ftwVerified} target verifications.`],
    ["9. Sibling isolation", "Non-target Schedule A signatures", `${ftw.filter((x) => x.sibling_unchanged != null).length} measured`, "Before/after signature comparison", counts.siblingImpact ? "FAIL" : "PASS", `${counts.siblingImpact} failures.`],
  ]; setValues(qa, 5, 1, methods); qa.getRange("A5:F13").format = { font: { size: 10 }, rowHeight: 38, wrapText: true, verticalAlignment: "top" };
  qa.getRange("A16:D16").values = [["Reconciliation check", "Expected", "Actual", "Result"]]; header(qa.getRange("A16:D16"));
  const checks = [["Manifest rows", 230, manifest.length], ["Layout result rows", 230, layoutRows.length], ["Completed extraction total", comparison.totals.completed, counts.extracted], ["FTW decision rows", counts.extracted, ftw.length], ["Unique structural families", 230, new Set(layoutRows.map((r) => r.structural_family_id)).size], ["Unique uploaded QA items", 230, new Set(live.map((r) => r.upload.new_item_id)).size]];
  setValues(qa, 17, 1, checks.map(([n, e, a]) => [n, e, a, e === a ? "PASS" : "FAIL"])); qa.getRange("D17:D22").conditionalFormats.addCustom("=$D17=\"PASS\"", { fill: colors.paleGreen, font: { color: colors.green, bold: true } });
  qa.getRange("A25:F27").merge(); qa.getRange("A25").values = [["Limitation: the independent source parser is not a human-approved gold standard for image-only documents. SOURCE_LIMITED rows are therefore review requirements, not automatic EyeLevel failures. Locked/signed FT Williams filings are operational blockers; embedded invalid extracted values on those rows are still recorded as semantic-validation defects."]]; qa.getRange("A25:F27").format = { fill: colors.paleBlue, font: { color: "#1E3A8A", bold: true }, wrapText: true, verticalAlignment: "center" }; widths(qa, [28, 45, 20, 42, 18, 75]); qa.freezePanes.freezeRows(4);
  return wb;
}

function buildDefectWorkbook() {
  const wb = Workbook.create();
  const summary = wb.worksheets.add("Defect Summary"); title(summary, "Schedule A Phase 2 — Defect Register", "Consolidated root causes from live ingestion, extraction, FT Williams update, read-back, and isolation testing.", 8);
  summary.getRange("A4:E4").values = [["Root Cause", "Occurrences", "Affected Layouts", "Priority", "Recommended Fix"]]; header(summary.getRange("A4:E4"));
  setValues(summary, 5, 1, rootCauses.map((r) => [r.cause, r.count, r.layouts, r.priority, r.fix])); summary.tables.add(`A4:E${4 + rootCauses.length}`, true, "RootCauseSummaryTable"); summary.getRange(`D5:D${4 + rootCauses.length}`).conditionalFormats.addCustom("=$D5=\"Critical\"", { fill: colors.paleRed, font: { color: colors.red, bold: true } }); widths(summary, [36, 14, 16, 12, 90]); summary.freezePanes.freezeRows(4);
  summary.getRange("A1:E1").unmerge(); summary.getRange("A1").values = [["Schedule A Phase 2 — Defect Register"]]; summary.getRange("A1:E1").format = { fill: colors.navy, font: { bold: true, color: colors.white, size: 18 }, rowHeight: 36, verticalAlignment: "center" };
  summary.getRange("A2:E2").unmerge(); summary.getRange("A2").values = [["Consolidated root causes from live ingestion, extraction, FT Williams update, read-back, and isolation testing."]]; summary.getRange("A2:E2").format = { fill: "#E8EEF4", font: { color: colors.gray, italic: true, size: 10 }, rowHeight: 32, verticalAlignment: "center" };
  const defectHeaders = ["Defect ID", "Severity", "Stage", "Root Cause", "Slice", "Structural Family", "Layout ID", "Client", "Source PDF", "Field", "Expected", "Actual", "Evidence / Detail", "Recommended Fix"];
  const defectData = defects.map((d) => [d.id, d.severity, d.stage, d.cause, d.slice, d.family, d.layout, d.client, d.file, d.field, d.expected, d.actual, d.detail, d.fix]);
  const detail = tableSheet(wb, "Defect Details", "Every issue is traceable to its client, layout family, PDF, field, evidence, and fix.", defectHeaders, defectData, [12, 11, 15, 32, 8, 14, 12, 34, 38, 35, 32, 32, 80, 75], "DefectDetailsTable", 4); detail.getRange(`B5:B${4 + defectData.length}`).conditionalFormats.addCustom("=$B5=\"Critical\"", { fill: colors.paleRed, font: { color: colors.red, bold: true } });
  return wb;
}

async function verifyAndSave(wb, filePath, prefix, sheets) {
  const inspection = await wb.inspect({ kind: "workbook,sheet,table", maxChars: 12000, tableMaxRows: 5, tableMaxCols: 8, tableMaxCellChars: 100 });
  await fs.writeFile(path.join(outputDir, `${prefix}_inspection.json`), JSON.stringify(inspection, null, 2));
  const formulaScan = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
  await fs.writeFile(path.join(outputDir, `${prefix}_formula_error_scan.json`), JSON.stringify(formulaScan, null, 2));
  for (const [name, range] of sheets) {
    const img = await wb.render({ sheetName: name, range, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${prefix}_${name.replace(/[^A-Za-z0-9]+/g, "_")}.png`), new Uint8Array(await img.arrayBuffer()));
  }
  const out = await SpreadsheetFile.exportXlsx(wb); await out.save(filePath);
}

const main = buildMainWorkbook();
await verifyAndSave(main, mainPath, "main", [["Executive Summary", "A1:L27"], ["Layout E2E Results", "A1:AE14"], ["Field Comparison", "A1:N14"], ["FTW Update Results", "A1:P14"], ["Defects & Blockers", "A1:N14"], ["Methodology & QA", "A1:F27"]]);
const defectWb = buildDefectWorkbook();
await verifyAndSave(defectWb, defectPath, "defects", [["Defect Summary", "A1:E22"], ["Defect Details", "A1:N14"]]);

const reportSummary = { generatedAt: new Date().toISOString(), mainPath, defectPath, counts, comparisonTotals: comparison.totals, rootCauses, defectRows: defects.length };
await fs.writeFile(path.join(outputDir, "report_summary.json"), JSON.stringify(reportSummary, null, 2));
console.log(JSON.stringify(reportSummary, null, 2));
