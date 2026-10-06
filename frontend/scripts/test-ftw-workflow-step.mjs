import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = await readFile(new URL("../src/ftwWorkflowStep.ts", import.meta.url), "utf8");
const runtime = ts.transpileModule(source.replace(/^export /gm, ""), {
  compilerOptions: { module: ts.ModuleKind.None, target: ts.ScriptTarget.ES2022 },
}).outputText;
const workflowStep = runInNewContext(`${runtime}\nftwWorkflowStep`);

const result = (input) => JSON.parse(JSON.stringify(workflowStep(input)));

assert.deepEqual(
  result({ automationCompleted: true, currentQuerySucceeded: true, needsDecisionCount: 0, verifiedUpdate: false }),
  { detail: "No changes needed", state: "done" },
  "A completed no-change filing must not remain in Needs review.",
);
assert.deepEqual(
  result({ automationCompleted: true, currentQuerySucceeded: true, needsDecisionCount: 0, verifiedUpdate: true }),
  { detail: "Verified", state: "done" },
  "A verified FT Williams write must remain complete.",
);
assert.deepEqual(
  result({ automationCompleted: false, currentQuerySucceeded: true, needsDecisionCount: 0, verifiedUpdate: false }),
  { detail: "Send selected changes", state: "active" },
  "An unfinished filing must still require review or sending.",
);
assert.deepEqual(
  result({ automationCompleted: true, currentQuerySucceeded: true, needsDecisionCount: 1, verifiedUpdate: false }),
  { detail: "Send selected changes", state: "active" },
  "A completed flag must not hide an outstanding decision.",
);
assert.deepEqual(
  result({ automationCompleted: true, currentQuerySucceeded: false, needsDecisionCount: 0, verifiedUpdate: false }),
  { detail: "Send selected changes", state: "active" },
  "A stale completed flag must not hide a missing FT Williams read-back.",
);

console.log("FT Williams workflow-step state checks passed.");
