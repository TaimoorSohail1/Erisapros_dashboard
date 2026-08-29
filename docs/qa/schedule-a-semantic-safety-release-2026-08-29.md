# Schedule A semantic safety release report

Date: 2026-08-29  
Branch: `codex/ftw-operator-ui`  
Implementation commit: `868cdbe`  
Production: deployed

## Outcome

The Schedule A semantic layer is now authoritative in production. EyeLevel/GroundX and local parsers still produce candidates, but a candidate that fails structural or semantic validation is sent to Review instead of being treated as an automatic FT Williams update.

This release improves **safety and correctness of decisions**. It does not claim that every layout now extracts every value. Ambiguous or unsupported layouts will produce review work rather than false automatic data.

## Root cause fixed

The semantic validation pipeline already existed, but production ran it in shadow mode. Its findings were recorded for QA and did not control proposed values. That allowed a structurally valid but semantically wrong value to continue into the review/update flow.

Production now runs with:

- `SCHEDULE_A_CANONICAL_VALIDATION_ENABLED=true`
- `SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED=false`

Both the API and ShareFile worker use the same authoritative settings.

## Implementation

1. Promoted section/table/row, evidence, type, date, broker, group, and compensation validation from shadow-only to authoritative production behavior.
2. Tightened Schedule A auto-matching:
   - at least one strong identifier is required (contract, carrier EIN, or NAIC);
   - carrier name alone cannot auto-select a record;
   - close scores stay pending for manual selection;
   - an exact contract can safely resolve a leading-zero normalization collision.
3. Preserved FT Williams sequence numbers in the complete Schedule A payload and used them during readback, so records with the same carrier identifiers are verified against the correct sequence.
4. Excluded internal sequence metadata from business-field comparisons.
5. Normalized punctuation-only address differences, including ZIP+4 hyphens, without hiding actual street/address changes.
6. Re-enabled the connected-user ShareFile manual sync control.

## Automated verification

| Gate | Result |
|---|---:|
| Full backend suite | 443 passed, 2 skipped |
| Semantic layer, extraction pipeline, golden corpus, production config | 48 passed |
| FT Williams review/update suite | 68 passed |
| ShareFile sync UI test | Passed |
| Review workflow UI tests | Passed |
| Dashboard UI tests | Passed |
| Field Rules UI test | Passed |
| TypeScript typecheck | Passed |
| Production frontend build | Passed |

The tests cover uncertain semantic values entering Review, aliases and published Field Rules, duplicate/cross-section values, broker rows, totals, strict matching, manual-match preservation, selected-record updates, complete-set preservation, readback verification, and address normalization.

## Production deployment

| Component | Evidence |
|---|---|
| CloudFormation stack | `erisapros-production` → `UPDATE_COMPLETE` |
| Backend CodeBuild | `erisapros-production-backend:121f047c-4f6a-4d76-8dde-bdbbc11706e6` → `SUCCEEDED` |
| API task definition | `erisapros-production-api:11`, rollout completed, 1/1 running |
| Worker task definition | `erisapros-production-sharefile-worker:6`, rollout completed, 1/1 running |
| CloudFront invalidation | `I1KG1FCTWMT85FK9UI2WJTJ1IZ` → completed |
| Live health | `/api/health` returned `status: ok` |
| Live frontend | Served bundle `index-BX6z8qDG.js` |
| Runtime flags | Authoritative `true`, shadow `false` in API and worker |
| Startup errors | No new `ERROR` log entries after deployment |

## Live browser smoke test

- ShareFile connector displayed `OAuth connected`.
- `Sync ShareFile` was enabled and accepted a manual scan.
- The app confirmed that background processing started.
- A live filing with four possible Schedule A records showed:
  - `FTW match: Pending`;
  - `FTW update: Locked`;
  - explicit `Select Schedule A` control;
  - no automatic best-score selection from weak/ambiguous evidence.
- Three extracted broker rows remained separate.
- The UI stated that unmatched existing FT Williams broker rows are preserved.

No live FT Williams write was submitted during this release smoke test. The selected filing correctly remained blocked until a reviewer chooses the Schedule A record, which is the intended safety behavior.

## 50-PDF corpus status

The earlier live 50-PDF run completed operationally but identified 30 semantic extraction defects. This release uses those defect patterns as validation cases and prevents rejected candidates from becoming automatic updates. Existing dashboard filings are not retroactively re-extracted by a deployment; they must be rerun or re-ingested to measure post-release extraction coverage.

Therefore:

- **Wrong/uncertain automatic values:** fixed by authoritative Review gating.
- **Unsafe Schedule A matching:** fixed by strong-identity and score-margin rules.
- **Unrelated Schedule A readback ambiguity:** fixed with FT Williams sequence-aware verification.
- **Every value from every possible future layout extracted automatically:** not claimed; uncertain values intentionally go to Review.

## Recommended next QA gate

Rerun the approved 50-PDF corpus through the deployed extraction path and compare each result to its approved expected values. Only reviewed, strongly matched filings should proceed to the FT Williams test account. For each write, capture before/after readback for the selected sequence and all preserved sequences. This is a measurement/release gate, not another client-specific parser change.
