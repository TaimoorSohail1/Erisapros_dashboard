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
