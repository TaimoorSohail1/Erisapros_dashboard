import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/pages/ShareFilePage.tsx", import.meta.url), "utf8");

assert.match(
  source,
  /disabled=\{syncing \|\| !status\.connected\}/,
  "ShareFile sync must be available to connected users and disabled only while unavailable or already running.",
);

assert.doesNotMatch(
  source,
  /<button className="button" disabled onClick=\{handleSync\}>/,
  "ShareFile sync must not be permanently disabled.",
);

console.log("ShareFile manual sync control passed.");
