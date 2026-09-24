import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import ts from "typescript";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(scriptDir, "../src/ui/sharedPollingResource.ts");
const source = await readFile(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`;
const { createSharedPollingResource } = await import(moduleUrl);

let calls = 0;
let intervals = 0;
let cleared = 0;
let resolveLoad;
const resource = createSharedPollingResource({
  initialData: [],
  load: () => {
    calls += 1;
    return new Promise((resolve) => { resolveLoad = resolve; });
  },
  pollMs: 60_000,
  freshMs: 5_000,
  setIntervalFn: () => {
    intervals += 1;
    return 42;
  },
  clearIntervalFn: () => { cleared += 1; },
});

assert.equal(resource.getSnapshot().loading, true, "a new shared resource must expose initial loading before its first request completes");
assert.equal(resource.getSnapshot().updatedAt, 0, "a new shared resource must not look like a completed empty response");

const releaseFirst = resource.acquirePolling();
const releaseSecond = resource.acquirePolling();
const concurrentRefresh = resource.refresh();
assert.equal(calls, 1, "subscribers and concurrent refreshes must share one in-flight request");
assert.equal(intervals, 1, "multiple consumers must share one polling timer");
resolveLoad(["loaded"]);
await concurrentRefresh;
assert.deepEqual(resource.getSnapshot().data, ["loaded"]);
assert.equal(resource.getSnapshot().loading, false, "a completed first request must leave the initial loading state");

await resource.refresh();
assert.equal(calls, 1, "fresh cached data must prevent an immediate duplicate request");

releaseFirst();
assert.equal(cleared, 0, "timer must stay active while one consumer remains");
releaseSecond();
assert.equal(cleared, 1, "last consumer must stop the shared polling timer");

console.log("Shared polling resource deduplicates requests and timers.");

let raceCalls = 0;
const raceResolvers = [];
const raceResource = createSharedPollingResource({
  initialData: ["initial"],
  load: () => {
    raceCalls += 1;
    return new Promise((resolve) => raceResolvers.push(resolve));
  },
  pollMs: 60_000,
});

const staleRefresh = raceResource.refresh({ force: true });
const postMutationRefresh = raceResource.refresh({ force: true });
raceResolvers.shift()(["stale failure"]);
await staleRefresh;
await Promise.resolve();
assert.equal(raceCalls, 2, "a forced refresh during an older request must queue a new post-mutation load");
raceResolvers.shift()([]);
await postMutationRefresh;
assert.deepEqual(raceResource.getSnapshot().data, [], "the queued refresh must publish the latest post-mutation state");

console.log("Shared polling resource refreshes again after an in-flight stale request.");
