import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/FieldRulesPage.tsx", import.meta.url), "utf8");

assert.doesNotMatch(
  source,
  /<option value="EXTRACTION_ONLY">Extraction-only field<\/option>/,
  "The client Add Field Rule flow must not offer extraction-only fields.",
);

assert.doesNotMatch(
  source,
  /<Field label="Rule type"/,
  "The client Add Field Rule flow must not show an unnecessary rule-type selector.",
);

assert.match(
  source,
  /mapping_mode: "FTW_MAPPED"/,
  "New client field rules must default to an FT Williams catalog mapping.",
);

assert.match(
  source,
  /label="FT Williams field"/,
  "The client flow must still allow selection from the FT Williams catalog.",
);

console.log("Field Rules client creation flow passed.");
