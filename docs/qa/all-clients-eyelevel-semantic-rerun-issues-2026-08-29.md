# EyeLevel semantic rerun — confirmed extraction issues

Date: 2026-08-29
Scope: fresh EyeLevel rerun of 50 client Schedule A PDFs

## Summary

- **30 PDFs** have at least one source-confirmed extraction defect.
- **2 PDFs** are source-limited and cannot be completed safely from the provided document.
- **All 50** are correctly blocked in Review by the semantic safety layer.
- **No FT Williams data was changed.**

## Root causes

1. **Wrong table/section relationship** — a value exists in the PDF but is attached to the wrong Schedule A field.
2. **Combined-column guessing** — one “commissions/fees” value is duplicated into both 3b and 3c.
3. **Subscriber versus total covered confusion** — a component count is selected instead of the total persons covered.
4. **Cross-section contamination** — Schedule C, benefits, premium, or experience values leak into another Schedule A section.
5. **Repeated group collapse** — two carriers, contracts, or coverage sections are merged into one result.
6. **Broker-row loss or column shift** — repeated broker rows are omitted or their commission/fee cells are swapped.
7. **Placeholder handling** — `SEE ABOVE #` is emitted as a final value rather than resolved or reviewed.
8. **Provider nondeterminism** — the same PDF produces materially different values on a fresh run.
9. **Missing provider evidence** — EyeLevel returns values without page, coordinates, or supporting row text, so correctness cannot be proven automatically.

## Confirmed affected clients

| Client | Defect category | Confirmed problem |
|---|---|---|
| Affinity Group | Wrong total/section | `104` chosen instead of total covered `171`; premium mapping wrong. |
| ALL Erection & Crane Rental | Combined amount | One combined amount duplicated into commission and fee. |
| American Securities | Cross-section contamination | Nonexperience data emitted as 9-series experience values. |
| Barry L. Price Rehabilitation | Omission | Additional compensation `554.22` missing. |
| Brandeis University | Combined amount | `2,416.88` duplicated into 3b and 3c. |
| BTIG | Wrong field classification | Premium `62,791.32` and broker payment `5,776.93` assigned to wrong fields. |
| Byrna Technologies | Broker-column contamination | Premium/compensation totals reused inside broker rows. |
| CDF Corporations | Broker-column shift | Broker commission and fee values duplicated/misassigned. |
| Control Associates | Repeating-row omission | Multiple recipient rows missing. |
| ERH (formerly JM Texas) | Placeholder | `SEE ABOVE #` not resolved to `975445`. |
| FGF LLC | Nondeterministic omission | Source premium `660,267.00` disappeared in the fresh run. |
| HMR Veteran Services | Wrong section | `7,023.51` taken from premium context but emitted as fee. |
| Homes for the Homeless | Wrong total | Subscriber `175` selected instead of total covered `205`. |
| Hyde Group | Repeated group collapse | LIFE and LTD sections merged. |
| Hyland Software | Omission | Supplemental compensation `2,816.67` omitted. |
| Ideal Clamp Products | Nondeterministic wrong value | Source commission `9,415.06` changed to `0`. |
| Ingersoll Cutting Tool | Wrong total/field | `321` selected instead of `658`; premium `49,250.58` misclassified. |
| Jane Street Group | Cross-section contamination | `198.16` emitted as 9a(2). |
| Kraft Power | Cross-section contamination | Correct premium also copied into experience-rated fields. |
| Mastery Logistics | Wrong field classification | `2,248.78` placed in retention/experience field. |
| Microbest | Cross-section contamination | Benefit/compensation rows leak into purpose and 9-series fields. |
| Modera Wealth Management | Broker-row omission | `38` marketing-fee row missing. |
| New York Yankees | Unsupported inference | Extra zero experience field added without source proof. |
| Preferred Pump & Equipment | Combined amount | `2,170.33` duplicated into 3b and 3c. |
| R. H. White Companies | Broker-column shift | Commission and fee columns swapped/misassigned. |
| Red Thread | Combined amount | `2,910.14` duplicated into 3b and 3c. |
| The Boston Home | Combined amount regression | Combined `613.34` duplicated into separate commission and fee fields. |
| The International Group | Placeholder | `SEE ABOVE #` not resolved. |
| Tilt Holdings | Combined amount | `1,293.98` duplicated into 3b and 3c. |
| Worcester Community Action Council | Repeated group collapse | Two Schedule A groups (`2 / 1,870` and `158 / 87,650`) are collapsed; wrong group selected. |

## Source-limited clients

| Client | Limitation | Required handling |
|---|---|---|
| Fund for the Public Interest | Available PDF is for 2022–2023, not current 2025 truth. | Require a current source document or reviewer confirmation. |
| Salesloft | Persons covered is not present in the source. | Obtain census/billing data; never infer the count. |

## Required global fixes

1. Require every scalar and broker value to include page, bounding box/table cell, and supporting text.
2. Segment the document into carrier/contract/coverage groups before mapping fields.
3. Treat combined commission/fee columns as one ambiguous value unless the source explicitly separates them.
4. Reconcile total persons covered against subscriber/dependent component counts.
5. Reconcile broker rows to section totals without forcing a match when the source reports different compensation types.
6. Reject duplicate values copied across unrelated sections.
7. Resolve placeholders only from an explicitly linked source; otherwise send to Review.
8. Compare repeated provider runs and fail closed when the structured result changes materially.
9. Keep this 50-PDF corpus as a permanent regression suite, including exact expected groups and row-level evidence.

## Acceptance gate

Release automatic extraction only when:

- every clear value maps to the correct Schedule A group, row, and column;
- every ambiguous, combined, missing, or conflicting value enters Review;
- repeated runs of the same PDF are stable;
- all 50 corpus PDFs pass source-verified regression;
- new Field Rules and aliases pass without client-specific code;
- an FT Williams test updates only the selected Schedule A and preserves every unrelated Schedule A unchanged.
