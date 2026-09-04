# Schedule A global position extraction verification

Date: 2026-09-05  
Scope: local implementation and QA only  
Deployment: **not deployed**

## Outcome

A carrier-neutral geometry layer now reads real PDF word bounding boxes before field mapping. It groups words into page rows, detects field-label anchors and column boundaries, reads same-row or below-label values, rejects headings as values, preserves page/bounding-box/table-cell evidence, and routes conflicting carrier rows to Review.

The original FT Williams query, matching, update, and read-back code was not changed.

## Exact defects covered

- Flattened table columns can no longer turn `b.EIN Code Coverages` into the insurance-company value.
- Generic headings such as `INSURANCE COMPANY`, `SERVICE OR OTHER ORGANIZATION`, and `EIN (Insurance Carrier)` are rejected as values.
- Wrapped carrier names are reconstructed across adjacent lines in the same column.
- Distinct carriers in one table are retained as candidates and marked `REVIEW_REQUIRED` instead of silently selecting one.
- Every field produced by the geometry layer carries page, bounding box, and row/column evidence.

## 20-document replay

| # | Client / source | Local result | Geometry fields | Brokers | Notes |
|---:|---|---|---:|---:|---|
| 1 | Advocates for Human Potential — Kaiser | OCR required | 0 | 0 | Image-only PDF; zero searchable text |
| 2 | Affinity Group — Fidelity Security Life | Passed | 1 | 1 | Existing fallback retained where geometry had no safe candidate |
| 3 | ALL Erection — Guardian Dental | Passed | 4 | 1 | Carrier fragment corrected to full Guardian name |
| 4 | American Securities — Guardian | Passed | 2 | 2 | Uncertain values remain in Review |
| 5 | Barry L. Price — Unum | Passed | 6 | 0 | Header rejected; full Unum carrier selected |
| 6 | Brandeis University — Zurich | Passed | 5 | 0 | Coordinate-backed core identifiers |
| 7 | BTIG — MetLife | Passed | 4 | 1 | Generic `INSURANCE COMPANY` heading rejected |
| 8 | Byrna Technologies — Equitable | Passed | 5 | 0 | Three-line carrier name reconstructed |
| 9 | Camino Health Center — Anthem | Passed | 5 | 1 | Exact column-mixing bug fixed; multiple carriers route to Review |
| 10 | Community Legal Aid — MetLife | Passed | 4 | 2 | Generic carrier heading rejected |
| 11 | Control Associates — Guardian | Passed | 2 | 2 | Existing broker flow preserved |
| 12 | Crest Discount Foods — Lincoln | OCR required | 0 | 0 | Image-only PDF; zero searchable text |
| 13 | Kraft Power — Standard | Passed | 6 | 3 | Existing detailed extraction retained |
| 14 | Elyria Foundry — Tokio/HCC | Passed | 1 | 0 | No unsafe guess for unavailable local fields |
| 15 | ERH — MetLife | Passed | 4 | 5 | Generic carrier heading rejected |
| 16 | Framestore — United Behavioral Health | Passed | 3 | 0 | Existing fallback retained; uncertain fields stay in Review |
| 17 | Mastery Logistics | OCR required | 0 | 0 | Image-only PDF; zero searchable text |
| 18 | Microbest — UnitedHealthcare | Passed | 8 | 0 | Full core geometry coverage |
| 19 | HMR Veteran Services — Sun Life | Passed | 4 | 0 | Header replaced by full wrapped carrier name and EIN |
| 20 | Housing Counseling Services — Lincoln | OCR required | 0 | 0 | Image-only PDF; zero searchable text |

### Replay totals

- Documents tested: **20**
- Searchable-text documents passing structural safety: **16/16**
- Image-only documents correctly requiring OCR: **4/4**
- Coordinate-backed fields produced: **64**
- Headings selected as values: **0**
- Unsafe automatic fields: **0**
- Geometry fields missing position evidence: **0**
- Unexpected exceptions: **0**

Raw machine-readable result: `tmp/schedule_a_geometry_qa_20260905/results.json`

## Automated regression results

- Focused extraction suite: **84 passed**, plus **2 subtests passed**
- Complete backend suite: **538 passed, 2 skipped**, plus **39 subtests passed**
- Frontend production build: passed
- FT Williams review/failure-diagnostics checks: passed
- Dashboard, Field Rules, and ShareFile UI checks: passed

## Deployment gate

This change is intentionally not deployed. Before release, the four image-only PDFs must be replayed through the normal GroundX OCR path and the 20-document values must receive final human source comparison. The current result proves the new geometry layer is safe and fixes the reproduced column-mixing defect; it does not claim that every field in every PDF is now fully accurate.
