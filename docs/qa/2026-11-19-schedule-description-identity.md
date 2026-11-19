# Keep Schedule A creation bound to policy identity

The PR 136 live send check created one valid Lincoln policy and then exposed a
shared matching defect. A second pending automatic policy had generated the
same eight-character description. Refresh reconciled it to the first policy
by description alone. Contract and premium checks stopped the second update;
the first update and two other eligible filings passed vendor readback.

Descriptions may disambiguate already verified identities, but cannot establish
contract or carrier identity. A unique exact identity can reconcile an earlier
successful create even when its description differs. Equal identity matches
without a unique description remain unresolved. A pending, unsubmitted auto
proposal can regenerate its description after a collision; manual confirmations
and uncertain prior writes remain protected. Labels remain unique beyond nine
policies.

Regression coverage includes a sequential shared-carrier collision, wrong
contract/EIN, missing identity, identity ties, existing exact-policy retry,
manual and confirmed-new choices, unknown send outcomes and many descriptions.
The initial regression reproduced seven failures before the fix. Repeat the
full frozen-source and production-image gates, deploy, refresh the existing
inventory without uploads, then verify the blocked second policy through the
normal executor and vendor readback. Keep source and destination decisions,
broker readiness and actual completed sends separate in the evidence report.

The full-policy check also found that calculated values acquired source page/text
in the review process but persisted only the value/status/confidence. Later policy
evaluation read the old evidence and requested review again. Persist the value
and evidence atomically, including evidence-only legacy repairs, while retaining
the original extracted value. Compare the original field state before saving;
concurrent reviewer edits abort rather than being overwritten. Classification and
calculation must preserve explicit reviewer values. Four failing regressions and
an end-to-end classification/save/reload/policy regression cover this second bug.
