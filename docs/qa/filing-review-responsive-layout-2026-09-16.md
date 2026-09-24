# Filing review responsive layout — production release

Date: 16 September 2026

## Outcome and release status

Implemented, verified locally, and **deployed to production after explicit user approval** on 16 September 2026. The feature-delivery workflow's human-review release gate was cleared by that approval. No production filing data or FT Williams records were modified.

Preview: http://localhost:5174/scripts/qa-review.html (synthetic data, read-only fetch harness; requires the running local Vite server). Reopen this URL rather than refreshing the synthetic `/filings/layout-qa` route.

## Changes

- Workflow progress is collapsed initially and expandable using mouse or keyboard.
- One compact FT Williams status summary replaces the large query, editability, validation, and plan-year panels in the main workspace.
- Full messages and existing retry, plan selection, Bring Forward, refresh, validation-fix, and plan-year resolution actions remain in a focused status drawer.
- Main fields and brokers grow naturally; no fixed-height vertical table scrollers. Desktop field headers remain sticky.
- Tablet tables scroll horizontally without stretching the whole page.
- Phone tables become labelled, full-width cards. Wrapped filters, tabs, and pagination grow without clipping or overlapping fields.
- Broker editor becomes a single column on phones, without desktop implicit column spans. Field and workflow dialogs respect small viewport bounds.
- Automation, API requests, plan matching, validation rules, approval/send readiness, confirmation requirements, and update verification were not changed.

## Checks passed

Frontend: review UI, FT Williams failure diagnostics, responsive workspace contracts, dashboard layout/company grouping, shared polling performance, Field Rules client creation, ShareFile sync controls, Agent settings, TypeScript/production build, and built-app smoke rendering.

Backend regression: FT Williams review **115 tests passed**; FT Williams automation **55 tests passed**. These were local automated tests, not live FT Williams submissions.

Browser QA used the real FilingReviewPage and AppShell with synthetic responses. Screen widths checked: **320, 375, 390, 640, 768, 820, 1024, 1366, 1920 pixels**. For all widths, page scroll width stayed within viewport width; field/broker/filter scroll heights equalled their content heights, with no constrained vertical scrolling. Eight field rows remained in the first page; pagination showed four rows on page two.

Manual interactions verified: workflow expand/collapse; status drawer opening; Escape dismissal; validation Fix navigation; mobile broker edit/cancel; mobile field modal; query-failure detail and retry availability; locked filing detail and disabled Send control. Smallest-screen modal bounds: field modal left 12/right 293, status drawer left 0/right 305 within the 320px viewport (including scrollbar space).

Issues caught and fixed during QA: notice overflow; desktop nth-child column widths overriding mobile cards; fixed flex-basis tabs/filters; pagination overflow at 320px; broker purpose spanning implicit mobile grid columns; field modal width exceeding padded viewport.

An existing dashboard test expected a grid despite the pre-existing automation notice using flex. Updated that assertion to match the existing flexible layout; no dashboard behavior was changed.

## Limitations

- No real approval, Bring Forward, or Send was executed for this UI-only task.
- Chromium browser QA only; Safari/Firefox and physical device checks are not claimed.
- Screen-width checks simulate responsive/zoom-sized viewports; OS scaling and actual browser zoom were not separately tested.
- Existing 500kB JavaScript bundle-size warning remains; build succeeds.
- Local preview is not a remotely hosted staging environment. Authenticated production layout verification is recorded below; a live FT Williams submission was deliberately outside this UI-only release scope.

## Reviewed scope

Production files: frontend/src/pages/FilingReviewPage.tsx, frontend/src/styles.css.

Supporting files: frontend/package.json; review/dashboard layout tests; responsive contract test; local-only qa-review.html and qa-review.tsx. The fixture is outside public/src and is not included in the normal production build.

No backend source or Agent executable changed. Existing untracked user artifacts were preserved. `git diff --check` passed.

## Production deployment and rollback

Verified AWS stack: erisapros-production, eu-north-1.

Frontend bucket: erisapros-production-frontendbucket-cakiwvjsgauc. CloudFront distribution: E38OL183OOAS7A. Website: https://d3axcdlq9aydpw.cloudfront.net.

Published only assets/index-BZVn3aj8.css and assets/index-DosfMAtD.js first, with immutable one-year asset caching. Published index.html last using the prior ETag as a conditional-write guard, with no-cache/no-store entrypoint caching. Old assets and Agent downloads were retained; no delete operation was used.

CloudFront invalidation **I9JERI2XUGAJRADBQB8OCBB5F5**, created at **2026-09-16 07:24:15 UTC**, completed successfully. Paths: /, /index.html, /filings/*.

Prior index S3 version: **85s_TrGQn3PmIvA89594y0x0CeN9VUMJ**. Published index S3 version: **ZzwlZKJPEZtQsnbJEQ1mNQts4PZBifvB**. Local rollback copy: `output/qa/review-responsive-release-2026-09-16/previous-index.html`.

Rollback if required: restore the captured previous index.html version, then invalidate the same entrypoint paths. Retained old hashed assets allow frontend rollback without rebuilding or changing backend/Agent state. Rollback was not required.

## Read-only production verification

Authenticated live Lyons Group filing `/filings/6aaa41e1035e7531a1ddc9c9` loaded the exact published JS/CSS references after reload. Desktop **1366x768**, phone **375x812**, and smallest-screen **320x740** checks passed without page-wide horizontal overflow. Field and broker container heights equalled their content heights at every checked width. The 320px layout retained eight first-page field rows and four labelled broker cards.

Confirmed compact status summary, full query/validation messages in the status drawer, drawer dismissal by Escape and Close, and workflow collapse. The 320px drawer stayed within the available viewport (left 0/right 305, accounting for scrollbar space). Captured browser error logs were empty. Temporary viewport overrides were reset after testing.

This verifies the display change, not the resolution of the filing's existing missing-current-year Schedule A or missing broker organization codes. Those validation messages remain visible and sending remains safely gated. No Retry, Bring Forward, approval, Send, or record deletion was executed during production verification.
