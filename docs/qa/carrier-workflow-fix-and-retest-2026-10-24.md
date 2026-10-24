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

The owner is arranging independent developer review. PR #59 remains a draft, with no merge, production deployment or filing writes authorized by test success alone. Review the latest revision, not only the earlier candidate.

## Changed-value source conflict found before release

The first changed-count provider canary returned the new visible count correctly, but the local/native path selected the older count. Investigation proved that the QA copy used a white rectangle and retained both differing native text layers at the same coordinates. That copy is not a clean changed-value fixture and is preserved as a negative input, not relabelled as a successful normal-layout test.

A synthetic end-to-end extraction test nevertheless reproduced a safety defect: the overlapping numeric source conflict could finish as automatic. A final-boundary check now preserves provider/local numeric alternatives and withholds automatic proposals when differing native digits substantially overlap on the same source page. It does not replace values, approve interpretations, hardcode document values or globally block ordinary provider/local corrections. Identical duplicate glyphs and corrections without overlapping source digits retain their existing behavior. Fresh clean-value fixtures and all final-revision regressions remain required.
