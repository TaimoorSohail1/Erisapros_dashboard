import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const auditDir = "C:/Users/Hp/Erisapros_dashboard/tmp/phase1_layout_audit_20260831";
const outputDir = "C:/Users/Hp/Erisapros_dashboard/outputs/phase1-schedule-a-layout-audit-20260831";
const outputPath = path.join(outputDir, "Schedule_A_Layout_Audit_Phase_1_2026-08-31.xlsx");
const previewDir = path.join(outputDir, "previews");

await fs.mkdir(previewDir, { recursive: true });

const catalog = JSON.parse(await fs.readFile(path.join(auditDir, "final_catalog.json"), "utf8"));
const folders = JSON.parse(await fs.readFile(path.join(auditDir, "sharefile_folder_inventory.json"), "utf8"));
const visualOverrides = JSON.parse(await fs.readFile(path.join(auditDir, "visual_classification_overrides.json"), "utf8"));

const systemFolders = new Set([
  "ERISAPROS AUTO SCAN QA 2026-08-21 2705",
  "HighlandTech AI Test Folder",
]);
const clientFolders = folders.filter((name) => !systemFolders.has(name));
const records = catalog.records;
const layouts = catalog.layouts;
const pages = catalog.pages;
const summary = catalog.summary;

const included = records.filter((r) => r.include_status === "Included");
const excluded = records.filter((r) => r.include_status !== "Included");
const includedByClient = new Map();
for (const record of included) {
  const key = record.client || record.client_name || "Unknown";
  if (!includedByClient.has(key)) includedByClient.set(key, []);
  includedByClient.get(key).push(record);
}

const countBy = (items, keyFn) => {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item) || "Unknown";
    map.set(key, (map.get(key) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
};

const carrierCounts = countBy(included, (r) => r.carrier).slice(0, 10);
const templateFamilyCounts = countBy(included, (r) => r.template_family);
const structuralGroups = new Map();
for (const record of included) {
  const id = record.structural_family_id;
  if (!structuralGroups.has(id)) structuralGroups.set(id, []);
  structuralGroups.get(id).push(record);
}

const structuralRows = [...structuralGroups.entries()].map(([id, group]) => {
  const carriers = [...new Set(group.map((r) => r.carrier).filter(Boolean))];
  const families = [...new Set(group.map((r) => r.template_family).filter(Boolean))];
  const pageCounts = [...new Set(group.map((r) => r.page_count).filter((v) => v !== undefined && v !== null))];
  const exactVariants = [...new Set(group.map((r) => r.layout_id).filter(Boolean))];
  const clients = [...new Set(group.map((r) => r.client || r.client_name).filter(Boolean))];
  const years = [...new Set(group.map((r) => r.filing_year).filter(Boolean))].sort();
  return {
    id,
    carrier: carriers.join("; "),
    family: families.join("; "),
    pageCounts: pageCounts.join(", "),
    pdfCount: group.length,
    uniqueContent: new Set(group.map((r) => r.hash || r.item_id)).size,
    exactVariantCount: exactVariants.length,
    clientCount: clients.length,
    years: years.join(", "),
    highRisk: group.filter((r) => r.ocr_risk === "High").length,
    brokerRows: group.some((r) => Number(r.broker_row_markers || 0) > 0) ? "Yes" : "No",
    totals: group.some((r) => r.has_total_label) ? "Yes" : "No",
  };
}).sort((a, b) => b.pdfCount - a.pdfCount || a.id.localeCompare(b.id));

const regressionRows = layouts.map((layout) => {
  const selected = records.find((r) => r.item_id === layout.gold_standard_item_id);
  const exactGroup = included.filter((r) => r.layout_id === layout.layout_id);
  return {
    tier: "Extended",
    ...layout,
    sourcePath: selected?.path || selected?.folder_path || "",
    gold_standard_client: selected?.client || selected?.client_name || layout.gold_standard_client,
    client_count: new Set(exactGroup.map((r) => r.client || r.client_name).filter(Boolean)).size,
    ocrQuality: selected?.ocr_quality || "",
    ocrRisk: selected?.ocr_risk || "",
    pageCount: selected?.page_count ?? "",
  };
});
const seenStructural = new Set();
for (const row of regressionRows.sort((a, b) => a.structural_family_id.localeCompare(b.structural_family_id) || b.pdf_count - a.pdf_count)) {
  if (!seenStructural.has(row.structural_family_id)) {
    row.tier = "Core";
    seenStructural.add(row.structural_family_id);
  }
}
regressionRows.sort((a, b) => (a.tier === b.tier ? b.pdf_count - a.pdf_count : a.tier === "Core" ? -1 : 1));

const validation = [];
const addCheck = (check, expected, actual) => validation.push({
  check,
  expected,
  actual,
  status: String(expected) === String(actual) ? "PASS" : "FAIL",
});
addCheck("Top-level ShareFile folders accounted", 88, folders.length);
addCheck("Client folders after system exclusions", 86, clientFolders.length);
addCheck("Candidate record count", summary.source_candidate_count, records.length);
addCheck("Included + excluded equals candidates", records.length, included.length + excluded.length);
addCheck("Included Schedule A records", summary.included_schedule_a_count, included.length);
addCheck("Every included record has a layout ID", included.length, included.filter((r) => r.layout_id).length);
addCheck("Every included record has a complete layout name", included.length, included.filter((r) => r.layout_name && r.carrier && r.template_family && r.version_year && r.layout_variant).length);
addCheck("Every excluded record has a reason", excluded.length, excluded.filter((r) => r.exclusion_reason).length);
addCheck("Layout counts reconcile to included PDFs", included.length, layouts.reduce((n, l) => n + Number(l.pdf_count || 0), 0));
addCheck("Every exact layout has one gold standard", layouts.length, layouts.filter((l) => l.gold_standard_item_id).length);
addCheck("Page audit rows", summary.page_count, pages.length);
addCheck("Every page maps to a known candidate", pages.length, pages.filter((p) => records.some((r) => r.item_id === p.item_id)).length);
addCheck("No duplicate candidate item IDs", records.length, new Set(records.map((r) => r.item_id)).size);
addCheck("Manual visual exclusions retained", summary.manual_visual_exclusion_count, Object.keys(visualOverrides).length);

if (validation.some((v) => v.status !== "PASS")) {
  throw new Error(`Catalog validation failed: ${JSON.stringify(validation.filter((v) => v.status !== "PASS"))}`);
}

const wb = Workbook.create();
const colors = {
  navy: "#0B1F33",
  teal: "#14B8A6",
  tealDark: "#0F766E",
  amber: "#F59E0B",
  red: "#DC2626",
  green: "#059669",
  light: "#F3F6F8",
  paleTeal: "#DDF7F2",
  paleAmber: "#FFF3D6",
  paleRed: "#FDE8E8",
  white: "#FFFFFF",
  gray: "#64748B",
  border: "#D6DEE7",
};

function colLetter(n) {
  let s = "";
  while (n > 0) {
    n--;
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26);
  }
  return s;
}

function setValues(sheet, startRow, startCol, matrix) {
  if (!matrix.length || !matrix[0].length) return;
  const endRow = startRow + matrix.length - 1;
  const endCol = startCol + matrix[0].length - 1;
  sheet.getRange(`${colLetter(startCol)}${startRow}:${colLetter(endCol)}${endRow}`).values = matrix;
}

function styleTitle(sheet, title, subtitle, endCol) {
  const last = colLetter(endCol);
  sheet.getRange(`A1:${last}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${last}1`).format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 18 },
    rowHeight: 34,
    verticalAlignment: "center",
  };
  sheet.getRange(`A2:${last}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${last}2`).format = {
    fill: "#E8EEF4",
    font: { color: colors.gray, italic: true, size: 10 },
    rowHeight: 28,
    verticalAlignment: "center",
    wrapText: true,
  };
}

function styleHeader(range) {
  range.format = {
    fill: colors.tealDark,
    font: { bold: true, color: colors.white },
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 28,
    borders: { bottom: { style: "continuous", color: colors.border, weight: 1 } },
  };
}

function styleDataSheet(sheet, headerRow, endRow, endCol, tableName, freezeCols = 1) {
  const last = colLetter(endCol);
  styleHeader(sheet.getRange(`A${headerRow}:${last}${headerRow}`));
  sheet.getRange(`A${headerRow + 1}:${last}${endRow}`).format = {
    font: { size: 9, color: colors.navy },
    verticalAlignment: "top",
    wrapText: false,
  };
  sheet.getRange(`A${headerRow}:${last}${endRow}`).format.borders = {
    bottom: { style: "continuous", color: "#E5EAF0", weight: 1 },
  };
  sheet.tables.add(`A${headerRow}:${last}${endRow}`, true, tableName);
  sheet.freezePanes.freezeRows(headerRow);
  if (freezeCols) sheet.freezePanes.freezeColumns(freezeCols);
}

function setWidths(sheet, widths) {
  widths.forEach((width, i) => {
    sheet.getRange(`${colLetter(i + 1)}:${colLetter(i + 1)}`).format.columnWidth = width;
  });
}

function addStatusFormatting(sheet, rangeAddress) {
  const r = sheet.getRange(rangeAddress);
  r.conditionalFormats.addCustom(`=${rangeAddress.split(":")[0]}="PASS"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
  r.conditionalFormats.addCustom(`=${rangeAddress.split(":")[0]}="FAIL"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });
}

// Executive Summary
const exec = wb.worksheets.add("Executive Summary");
styleTitle(exec, "Schedule A Layout Audit — Phase 1", "ShareFile corpus inventory, structural classification, OCR risk profile, and regression-test recommendations. EyeLevel extraction and FT Williams updates are intentionally outside Phase 1.", 12);
exec.getRange("A4:D4").values = [["Coverage KPI", "Value", "Source / Formula", "Status"]];
styleHeader(exec.getRange("A4:D4"));
const kpis = [
  ["Top-level ShareFile folders", folders.length, "ShareFile folder inventory", "Complete"],
  ["Client folders", clientFolders.length, "Top-level folders less 2 QA folders", "Complete"],
  ["Candidate PDFs discovered", records.length, "Candidate manifest", "Complete"],
  ["Valid Schedule A PDFs", included.length, "Classified PDF inventory", "Complete"],
  ["Unique included PDF content", summary.unique_included_content_count, "Hash-based deduplication", "Complete"],
  ["Exact duplicate PDF rows", summary.exact_duplicate_pdf_count, "ShareFile hash groups", "Informational"],
  ["Pages audited", pages.length, "Page Audit sheet", "Complete"],
  ["Carrier/template families", summary.template_family_count, "Normalized carrier/template grouping", "Complete"],
  ["Broad structural families", summary.structural_family_count, "Carrier + template + page geometry", "Complete"],
  ["Exact layout variants", layouts.length, "Year/version + structure fingerprint", "Complete"],
  ["Clients with valid Schedule A", includedByClient.size, "Client Coverage sheet", "Complete"],
  ["Clients without indexed Schedule A", clientFolders.length - includedByClient.size, "Client Coverage sheet", "Follow-up"],
  ["High OCR-risk PDFs", summary.high_ocr_risk_count, "OCR & Risks sheet", "Review"],
  ["Unavailable source PDFs", summary.unavailable_count, "Download manifest", "Follow-up"],
  ["Internal QA-folder PDFs excluded", summary.system_test_folder_excluded_count, "System-source separation", "Expected"],
];
setValues(exec, 5, 1, kpis);
const invLast = 4 + records.length;
const clientLast = 4 + clientFolders.length;
const layoutLast = 4 + layouts.length;
const pageLast = 4 + pages.length;
exec.getRange("B5:B8").formulas = [
  [`=COUNTA('Client Coverage'!$A$5:$A$${clientLast})+2`],
  [`=COUNTA('Client Coverage'!$A$5:$A$${clientLast})`],
  [`=COUNTA('PDF Inventory'!$A$5:$A$${invLast})`],
  [`=COUNTIF('PDF Inventory'!$H$5:$H$${invLast},"Included")`],
];
exec.getRange("B11").formulas = [[`=COUNTA('Page Audit'!$A$5:$A$${pageLast})`]];
exec.getRange("B14:B18").formulas = [
  [`=COUNTA('Layout Catalog'!$A$5:$A$${layoutLast})`],
  [`=COUNTIF('Client Coverage'!$K$5:$K$${clientLast},"Classified")`],
  [`=COUNTIF('Client Coverage'!$K$5:$K$${clientLast},"No indexed candidate")`],
  [`=COUNTIFS('PDF Inventory'!$H$5:$H$${invLast},"Included",'PDF Inventory'!$U$5:$U$${invLast},"High")`],
  [`=COUNTIF('PDF Inventory'!$I$5:$I$${invLast},"Unavailable")`],
];
exec.getRange("B19").formulas = [[`=COUNTIF('PDF Inventory'!$B$5:$B$${invLast},"HighlandTech AI Test Folder")+COUNTIF('PDF Inventory'!$B$5:$B$${invLast},"ERISAPROS AUTO SCAN QA 2026-08-21 2705")`]];
exec.getRange(`A5:D${4 + kpis.length}`).format = { font: { size: 10 }, rowHeight: 22, verticalAlignment: "center" };
exec.getRange(`B5:B${4 + kpis.length}`).format.numberFormat = "#,##0";
exec.getRange(`D5:D${4 + kpis.length}`).conditionalFormats.addCustom(`=$D5="Complete"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
exec.getRange(`D5:D${4 + kpis.length}`).conditionalFormats.addCustom(`=$D5="Review"`, { fill: colors.paleAmber, font: { color: "#9A6700", bold: true } });
exec.getRange(`D5:D${4 + kpis.length}`).conditionalFormats.addCustom(`=$D5="Follow-up"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });

exec.getRange("F4:G4").values = [["Top carrier", "PDFs"]];
styleHeader(exec.getRange("F4:G4"));
setValues(exec, 5, 6, carrierCounts.map(([carrier, count]) => [carrier, count]));
exec.getRange("G5:G14").format.numberFormat = "#,##0";
const carrierChart = exec.charts.add("bar", exec.getRange("F4:G14"));
carrierChart.title = "Top 10 carriers by valid Schedule A PDFs";
carrierChart.hasLegend = false;
carrierChart.setPosition("I4", "L18");

exec.getRange("F16:H16").values = [["Classification level", "Count", "How to use it"]];
styleHeader(exec.getRange("F16:H16"));
setValues(exec, 17, 6, [
  ["Template family", summary.template_family_count, "Carrier/template lineage"],
  ["Structural family", summary.structural_family_count, "Core regression coverage"],
  ["Exact variant", layouts.length, "Detailed version/layout coverage"],
]);
exec.getRange("A22:L22").merge();
exec.getRange("A22").values = [["Interpretation: the corpus spans 2016–2026 and contains many carrier revisions. Use 230 broad structural families for the core test pack and all 600 exact variants for extended regression coverage."]];
exec.getRange("A22:L22").format = { fill: colors.paleAmber, font: { color: "#7A4D00", bold: true }, wrapText: true, rowHeight: 36, verticalAlignment: "center" };
exec.getRange("A24:L24").merge();
exec.getRange("A24").values = [["Audit basis: 7,310 pages were programmatically inspected for text, page geometry, labels, section anchors, tables, broker-row markers, totals, and OCR risk. First-page renders of 298 image-heavy PDFs were visually reviewed; five misfiled supporting documents were excluded."]];
exec.getRange("A24:L24").format = { fill: colors.light, font: { color: colors.navy }, wrapText: true, rowHeight: 44, verticalAlignment: "center" };
exec.freezePanes.freezeRows(4);
setWidths(exec, [30, 14, 36, 15, 3, 28, 12, 32, 16, 16, 16, 16]);

// Client Coverage
const client = wb.worksheets.add("Client Coverage");
styleTitle(client, "Client Coverage", "Every client folder is listed, including clients with no indexed Schedule A candidate.", 12);
const clientHeaders = ["Client", "Candidate PDFs", "Valid Schedule A", "Unique Content", "Excluded / Unavailable", "Template Families", "Structural Families", "Exact Variants", "High OCR Risk", "Years", "Coverage Status", "Source"];
setValues(client, 4, 1, [clientHeaders]);
const clientRows = clientFolders.sort().map((name) => {
  const all = records.filter((r) => (r.client || r.client_name) === name);
  const valid = all.filter((r) => r.include_status === "Included");
  return [
    name,
    all.length,
    valid.length,
    new Set(valid.map((r) => r.hash || r.item_id)).size,
    all.length - valid.length,
    new Set(valid.map((r) => r.template_family_id).filter(Boolean)).size,
    new Set(valid.map((r) => r.structural_family_id).filter(Boolean)).size,
    new Set(valid.map((r) => r.layout_id).filter(Boolean)).size,
    valid.filter((r) => r.ocr_risk === "High").length,
    [...new Set(valid.map((r) => r.filing_year).filter(Boolean))].sort().join(", "),
    valid.length ? "Classified" : all.length ? "Candidates excluded/unavailable" : "No indexed candidate",
    "ShareFile /home/shared",
  ];
});
setValues(client, 5, 1, clientRows);
styleDataSheet(client, 4, 4 + clientRows.length, clientHeaders.length, "ClientCoverageTable", 1);
client.getRange(`B5:I${4 + clientRows.length}`).format.numberFormat = "#,##0";
client.getRange(`K5:K${4 + clientRows.length}`).conditionalFormats.addCustom(`=$K5="Classified"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
client.getRange(`K5:K${4 + clientRows.length}`).conditionalFormats.addCustom(`=$K5="No indexed candidate"`, { fill: colors.paleAmber, font: { color: "#9A6700", bold: true } });
client.getRange(`K5:K${4 + clientRows.length}`).conditionalFormats.addCustom(`=$K5="Candidates excluded/unavailable"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });
setWidths(client, [42, 13, 15, 14, 19, 16, 17, 14, 14, 22, 28, 23]);

// Layout Catalog
const layout = wb.worksheets.add("Layout Catalog");
styleTitle(layout, "Exact Layout Catalog", "One row per exact structural variant, named Carrier – Template Family – Version/Year – Variant.", 18);
const layoutHeaders = ["Layout ID", "Layout Name", "Carrier", "Template Family", "Version / Year", "Variant", "Template Family ID", "Structural Family ID", "PDFs", "Clients", "Client Names", "Filing Years", "Mode Pages", "High OCR Risk", "Broker Rows", "Totals", "Gold-standard PDF", "Confidence"];
setValues(layout, 4, 1, [layoutHeaders]);
const layoutRows = layouts.map((l) => {
  const exactGroup = included.filter((r) => r.layout_id === l.layout_id);
  const exactClients = [...new Set(exactGroup.map((r) => r.client || r.client_name).filter(Boolean))].sort();
  return [
    l.layout_id, l.layout_name, l.carrier, l.template_family, l.version_year, l.variant,
    l.template_family_id, l.structural_family_id, l.pdf_count, exactClients.length,
    exactClients.join("; "), Array.isArray(l.filing_years) ? l.filing_years.join(", ") : l.filing_years,
    l.page_count_mode, l.ocr_high_risk_count, l.broker_rows_present ? "Yes" : "No", l.totals_present ? "Yes" : "No",
    l.gold_standard_file, l.classification_confidence,
  ];
});
setValues(layout, 5, 1, layoutRows);
styleDataSheet(layout, 4, 4 + layoutRows.length, layoutHeaders.length, "LayoutCatalogTable", 2);
layout.getRange(`I5:J${4 + layoutRows.length}`).format.numberFormat = "#,##0";
layout.getRange(`N5:N${4 + layoutRows.length}`).format.numberFormat = "#,##0";
setWidths(layout, [15, 58, 28, 30, 14, 25, 18, 18, 10, 10, 48, 18, 11, 13, 12, 10, 52, 14]);

// PDF Inventory
const inventory = wb.worksheets.add("PDF Inventory");
styleTitle(inventory, "Client / PDF Inventory", "All discovered Schedule A candidates are retained, including valid, excluded, unavailable, duplicate, and internal QA sources.", 28);
const invHeaders = ["Item ID", "Client / Folder", "File Name", "ShareFile Path", "Filing Year", "Version", "Bytes", "Include Status", "Inspection Status", "Exclusion Reason", "Carrier", "Template Family", "Version / Year", "Layout ID", "Layout Name", "Template Family ID", "Structural Family ID", "Pages", "OCR Quality", "OCR Score", "OCR Risk", "Sections", "Table Pages", "Broker Markers", "Totals Label", "Duplicate Group", "Exact Duplicate Count", "Source Role"];
setValues(inventory, 4, 1, [invHeaders]);
const invRows = records.map((r) => [
  r.item_id, r.client || r.client_name, r.file_name, r.path || r.folder_path, r.filing_year, r.version, r.bytes || r.file_size,
  r.include_status, r.inspection_status, r.exclusion_reason, r.carrier, r.template_family, r.version_year, r.layout_id, r.layout_name,
  r.template_family_id, r.structural_family_id, r.page_count, r.ocr_quality, r.ocr_quality_score, r.ocr_risk,
  [r.section_coverage ? "Coverage" : "", r.section_commissions_fees ? "Commissions/Fees" : ""].filter(Boolean).join("; "),
  r.table_pages, r.broker_row_markers, r.has_total_label ? "Yes" : "No", r.duplicate_group, r.exact_duplicate_count, r.source_role,
]);
setValues(inventory, 5, 1, invRows);
styleDataSheet(inventory, 4, 4 + invRows.length, invHeaders.length, "PDFInventoryTable", 3);
inventory.getRange(`G5:G${4 + invRows.length}`).format.numberFormat = "#,##0";
inventory.getRange(`R5:R${4 + invRows.length}`).format.numberFormat = "#,##0";
inventory.getRange(`T5:T${4 + invRows.length}`).format.numberFormat = "0.00";
inventory.getRange(`H5:H${4 + invRows.length}`).conditionalFormats.addCustom(`=$H5="Included"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
inventory.getRange(`U5:U${4 + invRows.length}`).conditionalFormats.addCustom(`=$U5="High"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });
setWidths(inventory, [22, 38, 56, 68, 12, 11, 14, 14, 18, 46, 28, 30, 14, 15, 58, 18, 18, 10, 14, 11, 12, 24, 12, 15, 12, 20, 16, 18]);

// Structure Matrix
const structure = wb.worksheets.add("Structure Matrix");
styleTitle(structure, "Structural Family Matrix", "Broad layout families used for core regression coverage; exact year/version variants remain in the Layout Catalog.", 13);
const strHeaders = ["Structural Family ID", "Carrier", "Template Family", "Page Counts", "PDFs", "Unique Content", "Exact Variants", "Clients", "Filing Years", "High OCR Risk", "Broker Rows", "Totals", "Recommended Tier"];
setValues(structure, 4, 1, [strHeaders]);
const strRows = structuralRows.map((r) => [r.id, r.carrier, r.family, r.pageCounts, r.pdfCount, r.uniqueContent, r.exactVariantCount, r.clientCount, r.years, r.highRisk, r.brokerRows, r.totals, "Core"]);
setValues(structure, 5, 1, strRows);
styleDataSheet(structure, 4, 4 + strRows.length, strHeaders.length, "StructureMatrixTable", 1);
structure.getRange(`E5:J${4 + strRows.length}`).format.numberFormat = "#,##0";
structure.getRange(`M5:M${4 + strRows.length}`).format = { fill: colors.paleTeal, font: { color: colors.green, bold: true } };
setWidths(structure, [20, 30, 34, 18, 10, 15, 14, 10, 24, 14, 12, 10, 16]);

// OCR & Risks
const risks = wb.worksheets.add("OCR & Risks");
styleTitle(risks, "OCR and Classification Risks", "Actionable exceptions: high OCR risk, unavailable PDFs, excluded non-Schedule A content, and manually verified misfiles.", 14);
const riskHeaders = ["Severity", "Client / Folder", "File Name", "Item ID", "Risk Type", "Detail", "OCR Quality", "OCR Score", "Pages", "Carrier", "Layout ID", "Include Status", "Recommended Action", "Source Path"];
setValues(risks, 4, 1, [riskHeaders]);
const riskRecords = records.filter((r) => r.ocr_risk === "High" || r.include_status !== "Included" || visualOverrides[r.item_id]);
const riskRows = riskRecords.map((r) => {
  let severity = "Medium";
  let riskType = "Excluded candidate";
  let action = "Review exclusion reason; no extraction test required unless reclassified.";
  if (r.status === "download_failed" || r.result === "failed" || r.include_status === "Unavailable") {
    severity = "High"; riskType = "Unavailable source"; action = "Recover/download source, then rerun classification.";
  } else if (visualOverrides[r.item_id]) {
    severity = "Low"; riskType = "Manual visual exclusion"; action = "Retain as a negative classification guard test.";
  } else if (r.ocr_risk === "High" && r.include_status === "Included") {
    severity = "High"; riskType = "Image-heavy / weak text layer"; action = "Use OCR path and verify against rendered source before extraction scoring.";
  }
  return [severity, r.client || r.client_name, r.file_name, r.item_id, riskType, visualOverrides[r.item_id] || r.exclusion_reason || "High OCR risk", r.ocr_quality, r.ocr_quality_score, r.page_count, r.carrier, r.layout_id, r.include_status, action, r.path || r.folder_path];
});
setValues(risks, 5, 1, riskRows);
styleDataSheet(risks, 4, 4 + riskRows.length, riskHeaders.length, "RisksTable", 3);
risks.getRange(`A5:A${4 + riskRows.length}`).conditionalFormats.addCustom(`=$A5="High"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });
risks.getRange(`A5:A${4 + riskRows.length}`).conditionalFormats.addCustom(`=$A5="Medium"`, { fill: colors.paleAmber, font: { color: "#9A6700", bold: true } });
risks.getRange(`H5:H${4 + riskRows.length}`).format.numberFormat = "0.00";
setWidths(risks, [12, 38, 56, 22, 28, 60, 16, 12, 10, 28, 15, 14, 58, 68]);

// Regression Set
const regression = wb.worksheets.add("Regression Set");
styleTitle(regression, "Recommended Regression Set", "Core = one gold-standard PDF per broad structural family. Extended = one gold-standard PDF per remaining exact variant.", 15);
const regHeaders = ["Tier", "Structural Family ID", "Layout ID", "Layout Name", "Carrier", "Template Family", "Version / Year", "Variant", "PDF Coverage", "Client Coverage", "Gold-standard Client", "Gold-standard PDF", "Pages", "OCR Quality", "Source Path"];
setValues(regression, 4, 1, [regHeaders]);
const regRows = regressionRows.map((r) => [r.tier, r.structural_family_id, r.layout_id, r.layout_name, r.carrier, r.template_family, r.version_year, r.variant, r.pdf_count, r.client_count, r.gold_standard_client, r.gold_standard_file, r.pageCount, r.ocrQuality, r.sourcePath]);
setValues(regression, 5, 1, regRows);
styleDataSheet(regression, 4, 4 + regRows.length, regHeaders.length, "RegressionSetTable", 2);
regression.getRange(`A5:A${4 + regRows.length}`).conditionalFormats.addCustom(`=$A5="Core"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
regression.getRange(`A5:A${4 + regRows.length}`).conditionalFormats.addCustom(`=$A5="Extended"`, { fill: "#E8EEF4", font: { color: colors.navy, bold: true } });
regression.getRange(`I5:J${4 + regRows.length}`).format.numberFormat = "#,##0";
setWidths(regression, [12, 20, 15, 58, 28, 30, 14, 24, 13, 14, 38, 56, 10, 16, 68]);

// Page Audit
const pageAudit = wb.worksheets.add("Page Audit");
styleTitle(pageAudit, "Page-level Audit", "Every processed page is represented with geometry, text density, vector/table cues, anchors, and broker-row markers.", 16);
const pageHeaders = ["Item ID", "Client", "File Name", "Page", "Width", "Height", "Rotation", "Text Chars", "Words", "Vector Lines", "Rectangles", "Anchor Count", "Anchors", "Table Markers", "Broker Row Markers", "Page Risk"];
setValues(pageAudit, 4, 1, [pageHeaders]);
const pageRows = pages.map((p) => [
  p.item_id, p.client, p.file_name, p.page, p.width, p.height, p.rotation, p.text_chars, p.word_count, p.vector_lines, p.rectangles, p.anchor_count,
  Array.isArray(p.anchors) ? p.anchors.join("; ") : p.anchors,
  Array.isArray(p.table_markers) ? p.table_markers.join("; ") : p.table_markers,
  Array.isArray(p.broker_row_markers) ? p.broker_row_markers.join("; ") : p.broker_row_markers,
  Number(p.text_chars || 0) < 40 ? "High OCR risk" : Number(p.text_chars || 0) < 200 ? "Review" : "Text-rich",
]);
setValues(pageAudit, 5, 1, pageRows);
styleDataSheet(pageAudit, 4, 4 + pageRows.length, pageHeaders.length, "PageAuditTable", 3);
pageAudit.getRange(`D5:L${4 + pageRows.length}`).format.numberFormat = "#,##0.00";
pageAudit.getRange(`P5:P${4 + pageRows.length}`).conditionalFormats.addCustom(`=$P5="High OCR risk"`, { fill: colors.paleRed, font: { color: colors.red, bold: true } });
setWidths(pageAudit, [22, 38, 56, 9, 11, 11, 10, 12, 10, 12, 12, 12, 48, 48, 48, 16]);

// Methodology & QA
const qa = wb.worksheets.add("Methodology & QA");
styleTitle(qa, "Methodology and Quality Assurance", "Definitions, classification hierarchy, scope boundaries, and reconciliation checks.", 6);
qa.getRange("A4:F4").values = [["Method step", "What was checked", "Evidence produced", "Scope", "Outcome", "Notes"]];
styleHeader(qa.getRange("A4:F4"));
const methodRows = [
  ["1. Folder inventory", "All top-level client and QA folders", "88-folder inventory", "ShareFile /home/shared", "Complete", "2 internal QA folders separated from 86 client folders."],
  ["2. Candidate discovery", "PDF path/name/content indicators, including nested folders", "1,599 candidate records", "All indexed ShareFile files", "Complete", "Candidates retained even when later excluded."],
  ["3. PDF retrieval", "Source bytes, zero-byte and download failures", "Download manifest", "All candidates", "Complete with exceptions", "19 unavailable sources are listed for follow-up."],
  ["4. Page inspection", "Text, coordinates, page geometry, labels, anchors, tables, broker markers, totals", "7,310 page rows", "All processed pages", "Complete", "Programmatic inspection on every processed page."],
  ["5. Visual QA", "Image-heavy first-page renders and misfile detection", "298 rendered previews; 5 manual exclusions", "Image-heavy subset", "Complete", "Visual review supplements, not replaces, page-level metrics."],
  ["6. Classification", "Carrier, template family, year/version, structural fingerprint", "91 template families, 230 structural families, 600 exact variants", "1,183 valid Schedule A PDFs", "Complete", "Names follow Carrier – Template Family – Version/Year – Variant."],
  ["7. Regression selection", "Representative completeness, OCR quality, variant coverage", "600 gold standards", "Every exact variant", "Complete", "230 Core + 370 Extended representatives."],
  ["Phase boundary", "EyeLevel extraction and FT Williams update", "Not run in this phase", "Outside Phase 1", "Not started", "Phase 2 should benchmark EyeLevel against approved expected values."],
];
setValues(qa, 5, 1, methodRows);
qa.getRange("A15:D15").values = [["Validation check", "Expected", "Actual", "Result"]];
styleHeader(qa.getRange("A15:D15"));
const qaStart = 16;
setValues(qa, qaStart, 1, validation.map((v) => [v.check, v.expected, v.actual, v.status]));
const qaEnd = qaStart + validation.length - 1;
qa.getRange(`C${qaStart}:C${qaStart + 10}`).formulas = [
  [`=COUNTA('Client Coverage'!$A$5:$A$${clientLast})+2`],
  [`=COUNTA('Client Coverage'!$A$5:$A$${clientLast})`],
  [`=COUNTA('PDF Inventory'!$A$5:$A$${invLast})`],
  [`=COUNTA('PDF Inventory'!$A$5:$A$${invLast})`],
  [`=COUNTIF('PDF Inventory'!$H$5:$H$${invLast},"Included")`],
  [`=COUNTIFS('PDF Inventory'!$H$5:$H$${invLast},"Included",'PDF Inventory'!$N$5:$N$${invLast},"<>")`],
  [`=COUNTIFS('PDF Inventory'!$H$5:$H$${invLast},"Included",'PDF Inventory'!$O$5:$O$${invLast},"<>")`],
  [`=COUNTIFS('PDF Inventory'!$H$5:$H$${invLast},"Excluded",'PDF Inventory'!$J$5:$J$${invLast},"<>")`],
  [`=SUM('Layout Catalog'!$I$5:$I$${layoutLast})`],
  [`=COUNTA('Layout Catalog'!$Q$5:$Q$${layoutLast})`],
  [`=COUNTA('Page Audit'!$A$5:$A$${pageLast})`],
];
qa.getRange(`D${qaStart}:D${qaEnd}`).formulas = validation.map((_, i) => [`=IF(B${qaStart + i}=C${qaStart + i},"PASS","FAIL")`]);
qa.getRange(`D${qaStart}:D${qaEnd}`).conditionalFormats.addCustom(`=$D${qaStart}="PASS"`, { fill: colors.paleTeal, font: { color: colors.green, bold: true } });
qa.getRange("A32:F32").merge();
qa.getRange("A32").values = [["Completion rule: every valid Schedule A is classified and traceable; exclusions have reasons; exact variants reconcile to the PDF inventory; and every variant has one recommended gold-standard representative."]];
qa.getRange("A32:F32").format = { fill: colors.navy, font: { color: colors.white, bold: true }, wrapText: true, rowHeight: 38, verticalAlignment: "center" };
qa.freezePanes.freezeRows(4);
setWidths(qa, [30, 44, 34, 28, 20, 60]);
qa.getRange("A5:F12").format = { font: { size: 10 }, wrapText: true, verticalAlignment: "top", rowHeight: 44 };
qa.getRange(`A${qaStart}:D${qaEnd}`).format = { font: { size: 10 }, rowHeight: 22, verticalAlignment: "center" };

// Workbook-wide table header and body readability.
for (const name of ["Client Coverage", "Layout Catalog", "PDF Inventory", "Structure Matrix", "OCR & Risks", "Regression Set", "Page Audit"]) {
  const sheet = wb.worksheets.getItem(name);
  sheet.getRange("1:2").format.wrapText = true;
}

const inspections = {};
for (const [sheetName, range] of [
  ["Executive Summary", "A1:L24"],
  ["Client Coverage", "A1:L15"],
  ["Layout Catalog", "A1:R12"],
  ["PDF Inventory", "A1:AB12"],
  ["Structure Matrix", "A1:M12"],
  ["OCR & Risks", "A1:N12"],
  ["Regression Set", "A1:O12"],
  ["Page Audit", "A1:P12"],
  ["Methodology & QA", "A1:F32"],
]) {
  inspections[sheetName] = await wb.inspect({ kind: "region", sheetId: sheetName, range, maxChars: 4000 });
}
await fs.writeFile(path.join(outputDir, "workbook_inspection.json"), JSON.stringify(inspections, null, 2));

const formulaScan = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
await fs.writeFile(path.join(outputDir, "formula_error_scan.json"), JSON.stringify(formulaScan, null, 2));

for (const [sheetName, range] of [
  ["Executive Summary", "A1:L24"],
  ["Client Coverage", "A1:L18"],
  ["Layout Catalog", "A1:R14"],
  ["PDF Inventory", "A1:AB14"],
  ["Structure Matrix", "A1:M14"],
  ["OCR & Risks", "A1:N14"],
  ["Regression Set", "A1:O14"],
  ["Page Audit", "A1:P14"],
  ["Methodology & QA", "A1:F32"],
]) {
  const preview = await wb.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
  const fileName = `${sheetName.replace(/[^A-Za-z0-9]+/g, "_")}.png`;
  await fs.writeFile(path.join(previewDir, fileName), new Uint8Array(await preview.arrayBuffer()));
}

const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);

const reportSummary = {
  outputPath,
  previewDir,
  validation,
  counts: {
    topLevelFolders: folders.length,
    clientFolders: clientFolders.length,
    candidatePdfs: records.length,
    validScheduleA: included.length,
    pagesAudited: pages.length,
    templateFamilies: summary.template_family_count,
    structuralFamilies: summary.structural_family_count,
    exactVariants: layouts.length,
    clientsWithValidScheduleA: includedByClient.size,
    clientsWithoutIndexedScheduleA: clientFolders.length - includedByClient.size,
    highOcrRisk: summary.high_ocr_risk_count,
    unavailable: summary.unavailable_count,
    goldStandards: regressionRows.length,
    coreRegression: regressionRows.filter((r) => r.tier === "Core").length,
    extendedRegression: regressionRows.filter((r) => r.tier === "Extended").length,
  },
};
await fs.writeFile(path.join(outputDir, "report_summary.json"), JSON.stringify(reportSummary, null, 2));
console.log(JSON.stringify(reportSummary, null, 2));
