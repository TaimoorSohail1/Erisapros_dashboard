import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const failuresPage = await readFile(new URL("../src/pages/FTWilliamsFailuresPage.tsx", import.meta.url), "utf8");
const notifications = await readFile(new URL("../src/ui/FTWilliamsNotifications.tsx", import.meta.url), "utf8");
const diagnostics = await readFile(new URL("../src/ui/FTWilliamsDiagnostic.tsx", import.meta.url), "utf8");
const filingReview = await readFile(new URL("../src/pages/FilingReviewPage.tsx", import.meta.url), "utf8");

assert.match(failuresPage, /useFTWilliamsFailures\(\)/, "The full failure page must use the shared failure resource.");
assert.doesNotMatch(failuresPage, /listFTWilliamsFailureQueue/, "The full failure page must not maintain a separate stale queue.");
assert.match(notifications, /refreshFTWilliamsFailures\(\)/, "Opening the drawer must fetch fresh failures.");
assert.match(notifications, /failures\.slice\(0, 3\)/, "The drawer must remain a three-item preview.");
assert.match(failuresPage, /dismissFTWilliamsFailure/, "Operators must be able to dismiss an acknowledged active failure.");
assert.match(diagnostics, /operation\.outcome_code/, "Technical details must show the normalized FT outcome.");
assert.match(diagnostics, /operation\.request_id/, "Technical details must show the vendor request identifier when available.");
assert.match(diagnostics, /operation\.response_excerpt/, "Technical details must expose the masked response excerpt.");
assert.match(filingReview, /active_failure_client_error/, "The filing page must prefer the persistent active failure details.");
assert.match(filingReview, /refreshFTWilliamsFailures/, "A send attempt must refresh the shared failure queue.");

console.log("FT Williams failure diagnostics workflow passed.");
