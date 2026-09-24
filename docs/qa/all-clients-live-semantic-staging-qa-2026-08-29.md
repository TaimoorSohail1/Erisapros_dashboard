# All-client live EyeLevel semantic QA

Date: 2026-08-29
Environment: live ERISAPros web application, isolated EyeLevel/GroundX QA bucket 32806
Corpus: 50 Schedule A PDFs from 50 client test folders

## Executive result

- Deployed backend commits: `c37f838`, `b49aee6`, and `846eb7b`.
- Live browser uploads submitted: **50 / 50**.
- EyeLevel jobs completed: **50 / 50** after one successful retry.
- Post-deployment synchronous timeout failures: **0**.
- FT Williams writes performed by this QA run: **0**.
- Source-verified semantic result: **18 source-correct but Review-required, 30 with confirmed extraction defects, and 2 source-limited**.
- Release decision: **do not enable automatic FT Williams updates from EyeLevel output yet**.

The asynchronous job change fixed the platform failure: documents taking longer than CloudFront's synchronous request limit now finish through polling. It did **not** make every extracted value correct. EyeLevel still reports full field matches for values taken from the wrong row, column, or section.

## Deployment verification

| Item | Result |
|---|---|
| Backend CodeBuild | `erisapros-production-backend:6e2f782f-c262-49c7-8f95-c709fbbd57ac` succeeded |
| Backend branch override | `codex/ftw-operator-ui` explicitly supplied; the project default pointed to an older branch |
| ECS API and ShareFile worker | Steady state reached |
| Frontend | Uploaded to the production frontend bucket |
| CloudFront | Distribution `E38OL183OOAS7A`, invalidation `I1AAUG0QD4LAPGLGF0FNWC3U4Y` completed |
| Backend regression | 454 passed, 2 skipped |
| Frontend | TypeScript check and production build passed |

## Live run behavior

All 50 PDFs were selected in the live browser and sent through the deployed async EyeLevel QA path. Normal completion time was about 105–160 seconds. Path Robotics initially returned `The login session is invalid or expired`; an immediate retry completed in 122 seconds with `14/14 matched`.

The operational flow is now stable, but the label `matched` only means an extracted result was mapped to a Field Rule. It does not prove that the value is semantically correct.

## Client-by-client verdict

| # | Client | Live job | Source-verified verdict |
|---:|---|---|---|
| 1 | Advocates for Human Potential, Inc. | Completed | Source-correct; Review required |
| 2 | Affinity Group, Inc. | Completed | Defect: subscriber count used instead of total covered |
| 3 | ALL Erection & Crane Rental Corp. | Completed | Defect: combined amount duplicated into commissions and fees |
| 4 | AlphaSights | Completed | Source-correct; Review required |
| 5 | American Securities LLC | Completed | Defect: nonexperience values copied into experience fields |
| 6 | Barry L. Price Rehabilitation Center, Inc. | Completed | Defect: additional compensation omitted |
| 7 | Brandeis University | Completed | Defect: combined amount duplicated |
| 8 | BTIG, LLC | Completed | Defect: premium and compensation mapped to wrong fields |
| 9 | Byrna Technologies, Inc. | Completed | Defect: broker columns contaminated by totals |
| 10 | CDF Corporations | Completed | Defect: broker commission/fee values misassigned |
| 11 | Control Associates, Inc. | Completed | Defect: repeating broker rows omitted |
| 12 | Elyria Foundry Company LLC | Completed | Source-correct; Review required |
| 13 | ERH, LLC (formerly JM Texas) | Completed | Defect: unresolved placeholder returned as policy number |
| 14 | FGF LLC | Completed | Defect: source premium omitted nondeterministically |
| 15 | Framestore Inc. | Completed | Source-correct; Review required |
| 16 | Fund for the Public Interest Inc. | Completed | Source-limited: supplied document is not current 2025 truth |
| 17 | HMR Veteran Services, Inc. | Completed | Defect: premium-context value emitted as a fee |
| 18 | Homes for the Homeless, Inc. | Completed | Defect: subscriber count used instead of total covered |
| 19 | Housing Counseling Services, Inc. | Completed | Source-correct; Review required |
| 20 | Hyde Group Inc. | Completed | Defect: LIFE and LTD groups collapsed |
| 21 | Hyland Software, Inc. | Completed | Defect: supplemental compensation omitted |
| 22 | Ideal Clamp Products, Inc. | Completed | Defect: source commission changed to zero |
| 23 | Ingersoll Cutting Tool Company Inc. | Completed | Defect: wrong persons total and premium classification |
| 24 | Jane Street Group, LLC | Completed | Defect: broker value leaked into an experience field |
| 25 | JTB Americas LTD | Completed | Source-correct; Review required |
| 26 | Kraft Power Corporation | Completed | Defect: premium copied into experience fields |
| 27 | Mastery Logistics Systems, Inc. | Completed | Defect: compensation placed in a retention field |
| 28 | Microbest, Inc. | Completed | Defect: benefit/compensation rows contaminated other fields |
| 29 | Modera Wealth Management, LLC | Completed | Defect: broker marketing-fee row omitted |
| 30 | New York Yankees Partnership | Completed | Defect: carrier fragment and unrelated dates returned |
| 31 | Ohio Valley Stamping & Assemblies Inc. | Completed | Source-correct; Review required |
| 32 | Oxford Biomedica (US) LLC | Completed | Source-correct; Review required |
| 33 | Path Robotics | Completed after one retry | Source-correct; Review required |
| 34 | Peerless Clothing International | Completed | Source-correct; Review required |
| 35 | Preferred Pump & Equipment, L.P. | Completed | Defect: `2,170.33` duplicated into commissions and fees |
| 36 | R. H. White Companies, Inc. | Completed | Defect: broker commission/fee columns misassigned |
| 37 | Red Thread | Completed | Defect: carrier text contaminated and combined amount duplicated |
| 38 | Rhythm Pharmaceuticals, Inc. | Completed | Defect in live rerun: header text returned as carrier and dates reversed/unrelated |
| 39 | Saint Elizabeth Community | Completed | Source-correct; Review required |
| 40 | Salesloft, Inc. | Completed | Source-limited: persons covered absent from source |
| 41 | Socure, Inc. | Completed | Defect in live rerun: invalid broker, policy, persons-covered, and tax values |
| 42 | Special Service for Groups Inc. | Completed | Defect in live rerun: preparation date used for both policy dates |
| 43 | The Advertising Council | Completed | Defect in live rerun: legal prose returned as a tax amount |
| 44 | The Boston Home, Inc. | Completed | Defect: combined amount duplicated and carrier text contaminated |
| 45 | The International Group | Completed | Defect: header text used as carrier and `SEE` used as policy number |
| 46 | Tilt Holdings, Inc. | Completed | Defect: combined amount duplicated and carrier text contaminated |
| 47 | Titmouse, Inc. | Completed | Defect in live rerun: preparation date used for both policy dates |
| 48 | Tower Health | Completed | Defect in live rerun: prose used as an amount and commissions duplicated into fees |
| 49 | Worcester Community Action Council, Inc. | Completed | Defect: two Schedule A groups collapsed |
| 50 | WorldSprings Holdings LLC | Completed | Source-correct; Review required |

## Field Rule and alias verification

- Dynamic extraction-schema generation from published Field Rules and aliases passed the focused automated suite.
- New-field, alias, unknown-field rejection, and extraction-only protections passed.
- The current full backend regression passed with **454 passed, 2 skipped**; frontend type checking and build passed.
- This corpus run did not publish a new live Field Rule, so deployed production rule configuration was not changed merely for QA.

## Final assessment

The **upload and long-running extraction flow is fixed and live**. The **global semantic extraction problem is not resolved**. Automatic updates must remain blocked until the system rejects wrong-section values, combined commission/fee guesses, contaminated text, unresolved placeholders, and unstable repeat-run results.

Detailed release-blocking defects and required fixes are in `all-clients-live-semantic-staging-issues-2026-08-29.md`.
