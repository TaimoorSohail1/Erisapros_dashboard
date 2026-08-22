import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

assert.match(
  styles,
  /\.shell:has\(\.dashboard-ops\)\s*\{[^}]*height:\s*100dvh;[^}]*overflow:\s*hidden;/s,
  "Dashboard shell should use the real viewport height.",
);
assert.match(
  styles,
  /\.dashboard-ops\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/s,
  "Dashboard should fill the shell's remaining viewport row.",
);
assert.match(
  styles,
  /\.dashboard-workbench\s*\{[^}]*flex:\s*1 1 auto[^}]*min-height:\s*0/s,
  "Dashboard workbench should consume the remaining viewport height without forcing overflow.",
);
assert.match(
  styles,
  /\.dashboard-ops \.dashboard-table-panel\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0/s,
  "Filings panel should fill the workbench instead of using a fixed height cap.",
);
assert.match(
  styles,
  /\.dashboard-ops \.dashboard-kpi-grid\s*\{[^}]*flex:\s*0 0 auto/s,
  "KPI strip should not collapse when the viewport is short.",
);
assert.match(
  styles,
  /\.dashboard-v3\.dashboard-ops \.dashboard-filings-table\s*\{[^}]*min-width:\s*1120px[^}]*table-layout:\s*fixed/s,
  "Dense dashboard table should override the legacy wide-table layout.",
);
assert.match(
  styles,
  /\.dashboard-filings-table th\s*\{[^}]*position:\s*sticky[^}]*top:\s*0/s,
  "Column headers should remain visible while the filing list scrolls.",
);
assert.match(
  styles,
  /@media \(max-height:\s*800px\) and \(min-width:\s*981px\)/,
  "Short laptop screens should receive an explicit dense layout treatment.",
);
assert.doesNotMatch(
  styles,
  /\.dashboard-ops \.dashboard-table-panel\s*\{[^}]*height:\s*clamp\([^}]*560px/s,
  "The dashboard must not retain the old 560px table height cap.",
);

console.log("Dashboard responsive layout checks passed.");
