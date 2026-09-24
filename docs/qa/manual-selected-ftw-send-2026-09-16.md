# Manual selected-field FT Williams sending

Release status: **live and verified on 2026-09-17 (Asia/Karachi)**. The user reviewed the local preview, requested the spacing follow-up, and authorized release. See `manual-selected-ftw-send-release-2026-09-17.md` for production verification and rollback details.

## Scope

- Removed Approve Filing controls, confirmation dialogs, and the approval workflow stage. The five stages are Intake, Extraction, FTW loaded, Review, and FTW update.
- Send to FT Williams is always shown. Only an active interaction temporarily disables it to prevent duplicate requests; unrelated review/action items are not a UI readiness gate.
- The confirmation lists available changed fields and lets the client deselect each one. Selected IDs are required explicitly by the API; omitted selection does not send the old whole-filing preview.
- Prepared broker changes are separately opt-in. Unchecked preserves existing FTW broker rows. Existing row exclusion/editing handles individual broker choices before opting in to the prepared bundle.
- Public approve/unapprove endpoints return HTTP 410 without changing historical records. Legacy internal approval handling remains for the existing automatic workflow, which was not requested to change.
- A manual send does not dispatch an additional automatic send. Scheduled automation and its existing settings remain independent.
- Partial manual success keeps the filing in Needs Review, preserves approval history, records an FTW_UPDATE event, and does not hide other unresolved fields.

## Retained write protections

Fresh target queries precede writes. Selected IDs must belong to the filing, selected values must be eligible and valid, and the FTW plan/year/Schedule A target must be correctly matched and writable. An enabled button does not override a locked/missing target, disabled vendor-writing configuration, invalid selected value, or a rating-specific mapping constraint.

Schedule A uses the vendor's replace-style transaction: payloads carry current untouched values, current broker rows when unselected, and sibling Schedule A records. Complete-snapshot checks, read-back verification, ambiguity handling, and restoration remain enabled. Form 5500-only selections do not send a Schedule A replacement.

## Verification

- Backend full suite: **708 passed, 2 skipped**, 49.36 seconds. One existing GroundX SDK deprecation warning.
- Eight selected-send tests cover public-service writes without approval, unrelated invalid values/brokers and classification mismatch, selected-field read-back, sibling/broker preservation, empty/foreign/invalid selection errors, locked targets, retired approval routes, and no automatic follow-up write.
- Frontend production build (TypeScript plus Vite): passed. Existing >500 kB bundle advisory remains.
- Review UI contracts, responsive workspace checks, FTW diagnostics checks: passed.
- Dashboard responsive/grouping checks: passed.
- Built production bundle render smoke test: passed.

## Browser QA

The local-only synthetic harness uses the real review page components with two changed Form 5500 fields, thirteen review items, and unresolved broker values. It intercepts sends locally and never calls FT Williams.

- Desktop: Send is available despite review items; no approval stage/button. Confirmation checkboxes and persistent Send/Cancel footer remain visible on a short screen.
- Deselecting sponsor sends only field-0 and include_broker_updates=false. The synthetic error displays that exact request, proving the UI-to-request boundary without a remote write.
- Phone 375 x 812: labelled field cards, working checkbox selection, scrollable modal body, accessible footer.
- Tablet 768 x 1024: dialog width 729 pixels and document width 753 pixels; no page horizontal overflow. The wider comparison table has its own scroll area.
- Browser console error check: empty.
- Browser testing found compressed grid tracks overlaying a checkbox on short screens. Content-height tracks, a non-sticky modal table header, and selection-first ordering fixed it; regression contracts now cover those rules.

Preview: http://localhost:5174/scripts/qa-review.html?scenario=selected

### Workflow spacing follow-up — 2026-09-17

Removed the obsolete sixth column from desktop and small-screen workflow grids and changed loading placeholders to five stages. Desktop browser measurement confirms five equal tracks and the last card's right edge exactly matches the grid's right edge locally. At 375 x 812, five 118-pixel tracks use a 590-pixel horizontally scrollable strip, with no page-width overflow. No console errors. Review UI contracts, production build, and bundle smoke test passed. This is a presentation-only follow-up; no sending or automation logic changed. Included in the authorized live release.

## Review areas and release checklist

Review backend/app/api/filings.py, backend/app/models.py, backend/app/services/ftwilliams_review.py and backend/tests/test_ftwilliams_selected_send.py for the partial-write boundary. Review frontend/src/pages/FilingReviewPage.tsx, frontend/src/api.ts, frontend/src/styles.css and frontend/scripts/test-review-table-layout.mjs for selection UX and removed approval controls. Preserve unrelated pre-existing dirty-worktree changes, including shipped dashboard grouping fixes.

1. Human reviews the preview and write-boundary changes (or knowingly waives this gate).
2. Deploy API and frontend together, retaining the last production image/assets for rollback; do not change the worker or automatic-send settings.
3. Confirm live assets and API revision, retired approval endpoints, and manual selected-send error contracts without modifying real client FTW data.
4. A real vendor-write smoke test requires an agreed test filing, target, and selected values. No real production FTW writes were made during this feature's local QA.
