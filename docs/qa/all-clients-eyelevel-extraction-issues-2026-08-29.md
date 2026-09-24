# Confirmed EyeLevel Schedule A extraction issues

Date: 2026-08-29
Scope: 50-client isolated EyeLevel/GroundX corpus run

This file contains confirmed extraction defects only. Missing values that are genuinely absent from a source PDF are not counted as EyeLevel defects.

## Root-cause summary

| Root cause | Affected examples | Required correction |
|---|---|---|
| Combined commission/fee amount duplicated | ALL Erection, Brandeis, Preferred Pump, Red Thread, Tilt | Represent a source combined amount once, mark its type ambiguous, and require reviewer classification instead of copying it into both 3b and 3c. |
| Premium or total payment classified as broker compensation | Affinity, BTIG, Byrna, CDF | Anchor values to table headers/row labels and validate that broker rows reconcile to line 2 totals, never to premium totals. |
| Cross-section contamination | American Securities, Hyde Group, Jane Street, Kraft Power, Mastery Logistics, Microbest, New York Yankees | Segment pages into Schedule A sections before mapping; prohibit values from Schedule C, benefit detail, or nonexperience sections from entering 9-series fields. |
| Repeated coverage sections collapsed | Hyde Group | Preserve coverage/contract groups, then choose or aggregate only according to explicit Schedule A rules. |
| Additional compensation rows omitted | Barry L. Price, Control Associates, Hyland, Mastery Logistics, Modera | Extract every compensation row and reconcile broker-row sums to scalar commission/fee totals. |
| Persons covered uses subscribers rather than total covered | Affinity, Ingersoll | Prefer the explicit total subscribers-plus-dependents/persons-covered value; route ambiguity to review. |
| Placeholder policy number accepted | ERH, The International Group | Resolve `SEE ABOVE #` from the cover letter/header when unambiguous; otherwise require review rather than publishing the placeholder. |
| Broker amount columns misassigned | Byrna, CDF, R. H. White | Use column coordinates/header associations and enforce per-row and overall reconciliation checks. |
| Arithmetic/reconciliation failure not blocked | HMR Veteran, Hyland | Reject or review any result whose detailed rows do not sum to the reported totals. |

## Confirmed client defects

| Client | Severity | Confirmed defect |
|---|---|---|
| Affinity Group, Inc. | High | Wrong persons-covered basis; premium misclassified as compensation; line 10a missing. |
| ALL Erection & Crane Rental Corp. | High | One combined amount duplicated into 3b and 3c. |
| American Securities LLC | High | Nonexperience values duplicated into experience-rated fields. |
| Barry L. Price Rehabilitation Center, Inc. | Medium | Additional compensation `554.22` omitted. |
| Brandeis University | High | Combined `2,416.88` duplicated into commissions and fees. |
| BTIG, LLC | Critical | Premium and broker compensation placed in the wrong fields; premium missing. |
| Byrna Technologies, Inc. | High | Broker rows contain total premium/compensation in wrong columns. |
| CDF Corporations | High | Broker commission/fee values duplicated or assigned to the wrong payee/column. |
| Control Associates, Inc. | Medium | Multiple compensation rows omitted. |
| ERH, LLC | Medium | Placeholder contract value accepted although policy `975445` is present in the package. |
| HMR Veteran Services, Inc. | Critical | Fee value is wrong, total does not reconcile, and unsupported item 11 was inferred. |
| Hyde Group Inc | Critical | LIFE and LTD sections were mixed into one record. |
| Hyland Software, Inc. | High | Commission is `100` low; supplemental compensation `2,816.67` omitted. |
| Ingersoll Cutting Tool Company Inc. | High | Persons covered wrong and premium omitted. |
| Jane Street Group, LLC | Medium | Broker-related `198.16` emitted as experience field 9a(2). |
| Kraft Power Corporation | Critical | Schedule C/calendar totals contaminated Schedule A experience fields. |
| Mastery Logistics Systems, Inc. | Critical | Additional compensation omitted and unsupported experience values created. |
| Microbest, Inc. | Critical | Benefit/compensation values contaminated 3d and 9-series fields; premium copied into taxes. |
| Modera Wealth Management, LLC | Medium | `38` marketing-fee broker row omitted despite correct scalar fee total. |
| New York Yankees Partnership | Low | Unsupported zero experience field was added. |
| Preferred Pump & Equipment, L.P. | High | Combined `2,170.33` duplicated into 3b and 3c. |
| R. H. White Companies, Inc. | High | Broker-row commission/fee columns misassigned. |
| Red Thread | High | Combined `2,910.14` duplicated into 3b and 3c. |
| The International Group | Medium | Placeholder contract value accepted instead of resolving the package policy identifier. |
| Tilt Holdings, Inc. | High | Combined `1,293.98` duplicated into 3b and 3c. |

## Acceptance criteria for the combined fix

1. Re-run the same 50 source PDFs with zero ingestion failures.
2. No value may cross from Schedule C, benefit detail, or nonexperience sections into Schedule A 9-series fields without matching section evidence.
3. Broker rows must reconcile to scalar 3b/3c totals; mismatches must be review-required.
4. Combined/ambiguous compensation amounts must appear once and require reviewer classification.
5. Repeated coverage/contract sections must remain isolated.
6. Persons-covered logic must distinguish subscribers from total covered lives.
7. New published Field Rules and aliases must continue working without client-specific parser changes.
8. Only after all confirmed defects pass should automatic FT Williams updates be considered.
