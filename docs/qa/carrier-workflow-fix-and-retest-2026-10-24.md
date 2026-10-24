# Nine carrier workflow fix and retest implementation

## Required final outcome

Implement the approved nine-layout plan, preserve genuine review safeguards, release only after independent technical and human approval, then retest all nine through live ShareFile and restore the approved disposable records. No real client filings may be test targets. Earlier results are baseline evidence, not proof of this candidate.

## Requirement ledger

- [ ] Recheck all nine PDF layouts, notes and six canonical worksheets against independent expectations.
- [x] Implement physical-folder worksheet association across poll and webhook paths, including PWS classification and file-version invalidation; local regressions pass.
- [x] Implement audited, stale-safe Keep Current, including rebuilt send validation; local regressions pass.
- [x] Capture sanitized actual selected dispatch payload and actual vendor read-back values; local regressions pass.
- [x] Correct completion captions using verified outcomes, including safe legacy-record display; frontend behavior tests pass.
- [x] Implement QA folder/source/target/parent/sibling write restrictions, including closed-run delayed events and task isolation; local regressions pass.
- [x] Reject unknown nested Schedule A fields, unrecognized subparts and duplicate subpart containers rather than omitting them from QA preservation comparison; valid broker containers remain supported.
- [ ] Pass Linux regressions and nine original extraction/mapping comparisons.
- [ ] Exercise independent same-layout changed-value and clean annotation-free QA fixtures.
- [ ] Pass wrong-target, ambiguity, deduplication, concurrency, unknown-outcome, auth/quota, rounding and blank/zero tests.
- [ ] Pass frontend build, behavior and visual checks.
- [ ] Independent technical review and human release approval of the exact candidate.
- [ ] Deploy immutable reviewed API/worker and matching frontend with recorded rollback artifacts.
- [ ] Fresh live UHC, Unum, Aetna, Cigna, Lincoln, VSP, Standard, MetLife and Hartford runs on one final release.
- [ ] Independent saved-value, broker, parent and sibling checks and complete original restoration after each case.
- [ ] Final independent audit of 39 Schedule As and six complete parent filings; close QA runs and restore device pause state.
- [ ] Deliver new workbook, readable report and credential-scanned private evidence bundle.

## Fixed safety decisions

The six disposable customer/plan pairs and nine 2025 sequences are recorded in the approved plan and the prior private upload manifest. Do not substitute destinations. The matcher must choose independently; a QA guard may reject but must not force a match. Retain existing automatic-update, routing, provider and published business-rule settings. No production mutation until candidate tests and review gates pass.

All changes are based on the merged PR 57 source tree, independently confirmed identical to tested head cf1761f. The mixed main checkout is not a release input. Existing untracked QA files in the attached release checkout are preserved and are not automatically staged.

## Baseline failures

The 24 October live test found VSP's unchanged PWS worksheet omitted after textual root ancestry differed between poll and webhook. Aetna/Lincoln Keep Current submitted an overlength compound display address as an edit. The dashboard used remaining update count for completed-update captions. Successful per-field evidence can echo the sent value rather than the literal saved value. Existing 199 passing source checks and six source-only observations remain unchanged as baseline controls.

## Candidate and review status

Tracked by issue #58. Implementation is a candidate only, not deployed. A full local backend run passed before the final send-boundary refinement; the affected review/API suite passed afterward. Full final Linux validation and all remaining gates above must still pass. No production code or flow control has changed.

The GitHub repository is public. Private PDFs, client IDs, live snapshots, source hashes tied to client documents, credentials, QA authorization manifests and test evidence must stay in private storage, not this repository or its issue/PR comments.

## First isolated Linux result

The first exact-image Linux run retained 1,366 passing tests, 37 failures and three skips. All frontend behavior checks and the build passed. Twelve failures exercised actual Windows registry, DPAPI, process/signature or DLL-loader interfaces; these tests must run on Windows, not be counted as Linux coverage. Twenty-five mocked ShareFile scan tests depended on a developer's local OAuth/discovery configuration. Their setup now supplies explicit synthetic settings and isolated storage; application defaults and production settings are unchanged. The affected Windows suite passes 79 tests with one existing fixture dependency skip. A fresh full Linux run and a final full Windows run remain required after this test-only correction. Failed evidence is preserved privately, not overwritten.

## Internal QA registry contract

Two optional server settings scope QA authorization: `FTW_QA_GUARD_FOLDER_IDS_JSON` lists exact registered source folder IDs, including closed-run tombstones; `FTW_QA_REGISTRY_S3_KEY` references a private object under `qa/control/` in the existing file bucket. Ordinary folders do not read that registry.

The registry has `schema_version: 1` and a `runs` array. A run contains `run_id`, `state` (`ACTIVE` or `CLOSED`), timezone-aware `expires_at`, `folder_ids`, and `cases`. Each case contains SHA256 values `source_sha256` and `worksheet_sha256`; an exact `target` with customer, plan, year and sequence; independently fetched `baseline_records` and `baseline_form_5500`; and `allowed_parent_values`, mapping each approved parent tag to its explicitly permitted test/restoration values.

The source and worksheet must be exact approved bytes in private storage. The normal matcher selects independently. Every selected update is bound to its original case digest, complete pre-send vendor snapshot and destination. The common transport rechecks active authorization before each write; closed, expired, missing or changed authorization fails closed. Preserve folder tombstones after cleanup. Do not remove a closed folder from the server list while delayed events can still arrive.

Sibling preservation compares vendor business fields and broker rows independently from the outbound serializer. Identifier digits and absent fields are not normalized into invented values. Whole-dollar tolerance applies to the separately approved read-back assessment, not to preserving sibling data.

The common guard is task-local so concurrent sends cannot overwrite each other's authorization. QA bindings survive configuration removal, and registered QA sources cannot invoke automatic Bring Forward. No public test-registration endpoint is introduced.

## Isolated evidence and additional boundary regression

The previous candidate, `6c3ea1b`, passed the complete Windows suite (1,404 tests, two skips), the pinned-image Linux suite (1,393 tests, 13 Windows-specific skips), all frontend behavior checks and the frontend build. All nine original source documents also completed fresh provider extraction with 199 passing comparisons and six verified source-only observations. These are extraction/mapping results, not live filing-update evidence. Source documents and provider artifacts remain private.

Local synthetic browser QA verified distinct completed-update, verified-no-change and legacy captions. The Keep Current interaction retains printed extraction evidence and sends an explicit no-write decision. This local authentication-disabled build is not a release artifact, and it made no vendor requests.

A subsequent negative test reproduced a QA boundary gap: leaf comparison silently omitted an unknown nested sibling field or an unrecognized subpart. The guard now rejects unexpected nesting, unrecognized subparts and duplicate subpart containers in every QA Schedule A payload. Existing valid broker subparts still compare independently. The focused boundary/review/evidence suite passes 30 tests and 14 subtests. The full Windows/Linux, exact-image and nine-layout gates must be rerun for this final revision; earlier passing evidence is not relabelled as final-revision verification.

The owner is arranging independent developer review. PR #59 remains open and unmerged, with no production deployment or filing writes authorized by test success alone. Review the latest revision, not only the earlier candidate.

## Changed-value source conflict found before release

The first changed-count provider canary returned the new visible count correctly, but the local/native path selected the older count. Investigation proved that the QA copy used a white rectangle and retained both differing native text layers at the same coordinates. That copy is not a clean changed-value fixture and is preserved as a negative input, not relabelled as a successful normal-layout test.

A synthetic end-to-end extraction test nevertheless reproduced a safety defect: the overlapping numeric source conflict could finish as automatic. A final-boundary check now preserves provider/local numeric alternatives and withholds automatic proposals when differing native digits substantially overlap on the same source page. It does not replace values, approve interpretations, hardcode document values or globally block ordinary provider/local corrections. Identical duplicate glyphs and corrections without overlapping source digits retain their existing behavior. Fresh clean-value fixtures and all final-revision regressions remain required.

## Final-source audit and clean scanned-layout canaries

Revision `ad35379` passed the complete Windows/Linux/frontend suites and all nine fresh original-PDF comparisons (199 passes and six verified source-only observations). It also passed the clean scanned-layout changed-count UHC canary. The next canary retained the changed Unum count correctly, but a local OCR continuation-word error caused the premium parser to propose a section digit instead of the actual paid-premium currency amount. The existing conflict validator withheld that proposal. The batch stopped without filing writes, and its failed evidence is preserved.

The Unum reader now bounds the complete paid-premium label to its own section, requires one unambiguous currency amount, and never takes the next section's unpaid premium. Tests use multiple different amounts and both clean/damaged continuation wording, plus missing/ambiguous amounts. This changes no classification approval or business rules.

The additional-field audit also found that zero-fee broker cleanup did not remove a separate, derived commission-heading summary in the fee-purpose scalar. Canonical resolution now omits only that inapplicable summary after the scalar and broker rows establish zero fees without fee components or a real purpose. Raw stages retain the observed value with an explicit not-applicable record. Missing fees, nonzero/offsetting fee components, other printed purposes and unresolved validation errors retain their existing behavior. The published fee-purpose rule remains optional and unchanged.

The focused parser/canonical regression run passes 144 tests and five subtests. Both fixes require final full-platform and pinned-image retesting before release. Earlier evidence is not counted as final-revision verification. The owner-arranged independent developer review must cover the latest PR #59 head.

## Benefit-level premium audit

Revision `da17cf9` passed the full Windows suite (1,422 tests, two skips) and the pinned Linux/frontend run. A separate independent component audit verified the Hartford premium and compensation components, including negative adjustments, and MetLife's benefit-level commission components. It found a source-evidence gap: the MetLife premium total was correct, but the four premium components were absent because the table reader did not accept the OCR subsection label `a_`.

The table reader now permits an underscore or period after subsection `a`, still requiring the full paid-premium label. It does not infer values, change business rules or replace the reported total. A component mismatch retains a review error. Regression tests reproduce the missing evidence, exercise different amounts, exclude unlabelled/other-section totals and prove the mismatch hold. All 53 source-observation/final-evidence tests pass. A replay of the captured real OCR pages independently recovers the four expected premium components and reconciles their sum. This seam replay is not a fresh-provider or whole-pipeline result; final-revision full-platform, exact-image and nine-layout retesting remain required.

The additional-field audit also identified `BASE COMMISSIONS` as another commission-category heading in the zero-fee scalar. The same narrow not-applicable rule now covers this wording only after independently established zero fees, empty fee components and no broker fee purpose. Commission components remain unchanged, and real fees continue to prevent omission. New tests reproduce the three heading variants before the fix and preserve a nonzero-fee control. This is not a business-rule change or permission to erase genuine fee purposes.

## Strict-purpose and changed-value follow-up

Head `2989225` passed all nine original fresh-provider runs (199 field/broker comparisons, six source-only checks), 40 independent component checks and full Windows/Linux/frontend tests. The clean scanned-layout variation passed UHC and Unum, including the previously failing paid premium. It stopped at Aetna: GroundX returned the correct carrier, but the labelled local reader rejected OCR `Co,` rather than `Co.` and retained an adjacent instruction sentence. The existing candidate-conflict hold prevented automatic acceptance. The reader now accepts that punctuation variation only in the labelled carrier cell and retains the actual label/value evidence. A carrier-name validator also rejects the instruction fragment even without a second candidate.

A stricter purpose audit found a retained Aetna OCR table border and two generic payment-category summaries presented as fee purposes. Complete labelled fee components now retain their actual purposes. A marked derived scalar can use one consistent explicit fee purpose; different purposes remain per recipient without an invented single scalar. Raw stages and the prior derived category are retained. Real scalar purposes, genuine candidate conflicts, uncertain rows and unreconciled components are not overwritten by this reconciliation. The later filing hook no longer replaces source purposes, creates missing purposes from payment amounts, copies monetary evidence into purpose evidence, or promotes a purpose on that basis. Two legacy tests that required such fabrication now assert source preservation; the frozen independent main comparator and oracle remain unchanged.

Negative testing additionally reproduced acceptance of mismatching recipient components and a one-cent section-total difference. Nonempty commission/fee component sets must now contain valid currency amounts and reconcile exactly with their own recipient total. Different benefit labels alone no longer bypass one Schedule A's aggregate check; the pre-existing separate-worksheet behavior requires explicit multiple worksheet summaries. A mismatch preserves values, holds the affected recipient and relevant amount/purpose fields for review, and does not rewrite other fields. Signed adjustments remain supported. Exact source comparisons remain separate from the already-approved whole-dollar vendor read-back representation.

Independent visual review confirms the nine current ShareFile-derived PDFs are already unannotated carrier documents. Their 31 pages contain no reviewer mapping notes; no note-removal edits were needed. These test sources are distinct from the older yellow-note examples.

Captured OCR/canonical/mapping replays retain 199 passing main comparisons and six source-only checks, and pass 40 component plus 20 stricter printed-purpose checks. They also recover the changed-count Aetna carrier correctly. These are boundary replays, not final-image fresh-provider evidence. All final-revision platform, image, fresh-provider and changed-value gates remain required. The owner-arranged independent review must cover the next pushed revision, not approve the preceding head as release-ready. No production or filing writes occurred in this isolated phase.

The final working-tree Windows suite passes 1,459 tests and 105 subtests with no skips. An older regression assumed different benefit labels were sufficient to bypass aggregate reconciliation; it now proves both the required review hold without worksheet boundaries and preservation of the explicit separate-worksheet case. The unchanged frozen oracle and stricter private audits pass against the latest recorded-boundary replay. A read-only production rule check confirms that all 55 effective published rules match the frozen QA snapshot in both versions and semantic content. No rule service bootstrap, migration or database write was invoked. Exact-image Linux/frontend and fresh-provider verification must still follow this revision; passing recorded replays do not replace those gates.

## Scanned Cigna primary-record follow-up

Head `914cc2c` passes full platform/frontend testing and nine fresh original documents: 199 main comparisons, six source-only observations, 40 component checks, 20 strict fee-purpose checks and 119 exact scalar rule/value checks. Eight additional values follow the unchanged published default and are not counted as printed source data. These results do not establish live filing updates.

The clean scanned changed-count batch passes the first three cases, then stops at Cigna. Raw structured output and page-bound X-Ray correctly identify the consolidated record, but local OCR damages the Roman-numeral heading. The primary reader misses that page; semantic processing selects a section digit and appendix identifiers, and no recipient row survives. Existing review holds prevent automatic acceptance. The remaining five cases are not submitted, and failed evidence is retained privately. Terminal QA compute and its exact temporary permissions are cleaned up; no production or filing operations occur.

The working fix recognizes Cigna-scoped Roman-numeral OCR variations and a complete contract/count/period row even when the first two identity cells are unreadable. It retains separately legible labelled identity cells. Missing primary identifiers may be recovered only from one actual page-bound X-Ray primary record with the same carrier, contract, count, dates and physical page. The queued/completed process identity, one output, filename and source URL must match the current document. Structured answers alone, traces, state appendices, multiple outputs and malformed/unbound metadata cannot clear the source-identity hold. Genuine primary-source disagreements remain candidates for review.

The single-recipient fallback now requires one visible name, one address and printed recipient amounts that reconcile with the primary totals. It accepts an OCR period between city and state, preserves identifier digits, supplies page-bound evidence, omits zero-only fee components and does not invent a fee purpose. Multiple addresses, inconsistent row amounts and unreadable positive-fee purposes cannot become an automatic aggregate allocation.

The focused public-pipeline/parser suite passes 154 tests, including wrong-document and genuine-conflict controls. Replaying the actual captured OCR/X-Ray boundary through the public pipeline recovers the primary header and recipient without provider or vendor calls. The changed PDF correctly retains employee-population review holds rather than inheriting the original hash-bound business approval. That replay is not fresh final-image evidence. Full final-source Windows/Linux/image, original-nine and changed-value testing remain required before review and promotion. No fixes are yet deployed.

The final working-source Windows suite passes 1,477 tests and 105 subtests with no skips. Exact final-image Linux/frontend and fresh original/changed-value batches remain pending; older candidate evidence is not relabelled as verification of this source revision.

## Scanned Hartford compensation follow-up

Head `62f64be` passed full Windows/Linux/frontend verification and all nine fresh originals, including the separate component, fee-purpose and scalar-mapping audits. The clean scanned changed-count batch submitted all nine documents. The first eight passed their bounded comparisons. Hartford's raw structured provider response omitted the changed count, although the final count and mapped value were correct. That raw-provider failure remains recorded; it is not reclassified as provider success.

Further source review found distinct compensation defects: page-level OCR lost the broker tables, flattened X-Ray columns interleaved categories, one identity contained a displaced organization-code digit, and a structured bonus was classified as commission. The repair decodes adjacent categories only when one exact component allocation reconciles, preserves signed adjustments and rejects ambiguous allocations. A uniquely matched derived structured row may be replaced by a reconciled source-table row; independently evidenced, duplicate or conflicting rows remain untouched. All original provider observations and replacement traces remain available. Count evidence now identifies the actual labelled benefit/count table rather than the cover letter.

The new scanned Hartford identity reader detects physical seven-column grids from the document, not fixed example coordinates. It requires matching identity/code headers and a following body row, then reads only the identity and organization-code cells. Two Tesseract segmentation observations must agree: block mode 6 versus identity mode 4 and short-code line mode 7. These are separate reads by the same OCR engine, not independent providers or guaranteed truth. Missing tools, incomplete grids, missing codes or disagreement produce no corroboration. Company-name digits and identifier leading zeros are preserved. Recovery is bound to one current completed provider document by process, filename and source URL. No business approval is created or inherited.

An offline Linux diagnostic on the unchanged scanned source confirms four matching identity/code observations. A public-pipeline replay of the actual recorded provider and cell-OCR boundaries recovers all four recipients, exact source payment categories and the changed count. That replay retains the changed source's classification review hold. It is not fresh-provider, final-image, live-update or restoration acceptance. Negative regressions cover current-document binding, malformed evidence, explicit source conflicts, mismatched/missing secondary reads, ambiguous component allocations and existing Lincoln name-conflict behavior.

Final-revision full-platform, exact-image, original-nine and changed-value checks remain required. The owner is arranging independent developer review of the latest pushed head; there is no merge or production promotion before that review and release sign-off. Production configuration, published rules, automatic-update behavior and real client filings remain unchanged in this phase.

The final Hartford working-source Windows run passes 1,509 tests and 105 subtests, with no skips. Its focused Hartford/source/conflict suite passes 53 tests. The exact-image Linux/frontend and fresh-provider gates are still pending for this new revision.
