import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const page = await readFile(new URL("../src/pages/DashboardPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

assert.match(
  page,
  /groupFilingsByCompany\(filteredFilings\)/,
  "Filtered filings should be grouped by company before pagination.",
);
assert.match(
  page,
  /if \(normalizedName && clientName !== "Client pending"\) return `name-\$\{normalizedName\}`;\s+if \(normalizedEin\) return `ein-\$\{normalizedEin\}`;/,
  "Company names should be the primary group identity so stale or missing EINs do not split one client.",
);
assert.match(
  page,
  /sessionStorage\.setItem\(DASHBOARD_EXPANDED_GROUPS_KEY/,
  "Expanded company groups should be remembered for the browser session.",
);
assert.match(
  page,
  /aria-expanded=\{expanded\}/,
  "Company rows should expose their expanded state to assistive technology.",
);
assert.match(
  page,
  /Expand all/,
  "Dashboard should provide an Expand all control.",
);
assert.match(
  page,
  /Collapse all/,
  "Dashboard should provide a Collapse all control.",
);
assert.match(
  styles,
  /\.dashboard-company-row/,
  "Company rows should have dedicated visual styling.",
);
assert.match(
  styles,
  /\.dashboard-filing-child-row/,
  "Expanded filing rows should be visually nested under their company.",
);
assert.match(
  styles,
  /\.dashboard-company-toggle:focus-visible/,
  "Company toggles should have a visible keyboard focus state.",
);
assert.match(
  styles,
  /\.shell:has\(\.dashboard-ops\)\s*\{[^}]*height:\s*100dvh;[^}]*overflow:\s*hidden;/,
  "Desktop dashboard layout should use the shell's real viewport row instead of a fixed offset.",
);
assert.match(
  styles,
  /\.dashboard-ops\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
  "Dashboard content should fill the remaining shell height without overflowing it.",
);
assert.doesNotMatch(
  styles,
  /--dashboard-shell-offset|height:\s*calc\(100dvh - var\(--dashboard-shell-offset\)\)/,
  "Dashboard height must not depend on a duplicated hard-coded shell offset.",
);

console.log("Dashboard company grouping checks passed.");
