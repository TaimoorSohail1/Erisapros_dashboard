# All-client EyeLevel semantic extraction rerun

Date: 2026-08-29
Environment: isolated EyeLevel/GroundX QA bucket 32806 (`Erisapros`)
Corpus: 50 Schedule A PDFs from 50 client test folders
Run prefix: `QA50_SEMANTIC_20260829_R2__`

## Executive result

- Freshly uploaded to EyeLevel: **50/50**.
- EyeLevel ingestion completed: **50/50**; ingestion failures: **0**.
- Source-correct core extraction: **18 PDFs**.
- Confirmed extraction defects: **30 PDFs**.
- Source-limited: **2 PDFs**.
- Automatic decisions: **0**.
- Safely routed to Review: **50/50**.
- FT Williams writes performed: **0**.

The new semantic validation layer is working as a safety gate: unsupported, conflicting, or unproven values are no longer allowed to update FT Williams automatically. EyeLevel extraction accuracy itself is **not yet globally correct**. Thirty PDFs still contain at least one confirmed semantic extraction defect.

## Fresh-run measurements

| Measurement | Result |
|---|---:|
| EyeLevel scalar fields returned | 656 |
| EyeLevel broker rows returned | 114 |
| Fields agreeing with the independent parser | 307 |
| Conflicting fields | 99 |
| EyeLevel-only fields requiring proof/review | 250 |
| Parser-only fields missed by EyeLevel | 78 |
| Scalar values directly string-grounded in source text | 382 / 656 |
| Broker values directly string-grounded in source text | 459 / 839 |
| Image/OCR PDFs requiring visual source review | 11 |

String grounding only proves that text occurs somewhere in the PDF. It does not prove that it came from the correct Schedule A section, table row, or column. The semantic validator therefore correctly keeps these results in Review.

## Client-by-client result

| # | Client | Result | Fresh source-verified outcome |
|---:|---|---|---|
| 1 | Advocates for Human Potential, Inc. | SOURCE-CORRECT / REVIEW | Clear core values and three broker rows agree; provider broker evidence lacks page/row coordinates, so automatic use is blocked. |
| 2 | Affinity Group, Inc. | ISSUE | Uses subscriber count `104` instead of total covered `171`; premium/compensation section mapping remains wrong. |
| 3 | ALL Erection & Crane Rental Corp. | ISSUE | Combined commissions/fees amount is duplicated into separate commission and fee fields. |
| 4 | AlphaSights | SOURCE-CORRECT / REVIEW | Fresh premium `161,667` is source-supported; unproven fields remain gated. |
| 5 | American Securities LLC | ISSUE | Nonexperience values are also emitted into experience-rated 9-series fields. |
| 6 | Barry L. Price Rehabilitation Center, Inc. | ISSUE | Additional compensation `554.22` is omitted. |
| 7 | Brandeis University | ISSUE | Combined amount `2,416.88` is duplicated into both commissions and fees. |
| 8 | BTIG, LLC | ISSUE | Premium `62,791.32` is classified as commission and `5,776.93` as fee; line 10a is missed. |
| 9 | Byrna Technologies, Inc. | ISSUE | Broker columns reuse premium/compensation totals in the wrong row columns. |
| 10 | CDF Corporations | ISSUE | Broker-row commission and fee values are duplicated/misassigned. |
| 11 | Control Associates, Inc. | ISSUE | Multiple compensation recipient rows are omitted. |
| 12 | Elyria Foundry Company LLC | SOURCE-CORRECT / REVIEW | Clear carrier, policy, premium, totals, and broker values agree with source. |
| 13 | ERH, LLC (formerly JM Texas) | ISSUE | Placeholder `SEE ABOVE #` is returned instead of resolving policy number `975445`. |
| 14 | FGF LLC | ISSUE | Fresh run dropped source premium `660,267.00` that the earlier run found. |
| 15 | Framestore Inc. | SOURCE-CORRECT / REVIEW | Clear core values, totals, and broker rows agree with source. |
| 16 | Fund for the Public Interest Inc. | SOURCE-LIMITED | Available source is a 2022–2023 document and cannot prove 2025 production truth. |
| 17 | HMR Veteran Services, Inc. | ISSUE | `7,023.51` is taken from premium context but classified as a fee; totals do not reconcile. |
| 18 | Homes for the Homeless, Inc. | ISSUE | Fresh run selects subscriber count `175` instead of total persons covered `205`. |
| 19 | Housing Counseling Services, Inc. | SOURCE-CORRECT / REVIEW | OCR recovers clear core values, premium, totals, and four broker rows. |
| 20 | Hyde Group Inc. | ISSUE | LIFE and LTD coverage sections are mixed into one Schedule A result. |
| 21 | Hyland Software, Inc. | ISSUE | Supplemental compensation `2,816.67` is omitted from the total. |
| 22 | Ideal Clamp Products, Inc. | ISSUE | Fresh run changes source commission `9,415.06` to `0`. |
| 23 | Ingersoll Cutting Tool Company Inc. | ISSUE | Uses subscriber count `321` instead of total `658`; premium `49,250.58` is misclassified. |
| 24 | Jane Street Group, LLC | ISSUE | Broker/fee value `198.16` is incorrectly emitted as experience-rated field 9a(2). |
| 25 | JTB Americas LTD | SOURCE-CORRECT / REVIEW | Clear core, premium, commission, fee, and broker values agree with source. |
| 26 | Kraft Power Corporation | ISSUE | Premium `1,569,138.68` is correct at 10a but also contaminates experience-rated fields. |
| 27 | Mastery Logistics Systems, Inc. | ISSUE | Additional compensation `2,248.78` is placed in the wrong retention/experience field. |
| 28 | Microbest, Inc. | ISSUE | Benefit and compensation rows contaminate purpose and multiple 9-series fields. |
| 29 | Modera Wealth Management, LLC | ISSUE | The `38` marketing-fee broker row is omitted. |
| 30 | New York Yankees Partnership | ISSUE | Unsupported experience-rated zero field is added. |
| 31 | Ohio Valley Stamping & Assemblies Inc. | SOURCE-CORRECT / REVIEW | Clear core values remain source-supported; new experience fields lack sufficient automatic evidence and are gated. |
| 32 | Oxford Biomedica (US) LLC | SOURCE-CORRECT / REVIEW | OCR-recovered core, premium, commission, and broker values agree with source. |
| 33 | Path Robotics | SOURCE-CORRECT / REVIEW | Clear core and broker values remain source-supported; newly inferred experience fields stay in Review. |
| 34 | Peerless Clothing International | SOURCE-CORRECT / REVIEW | Base commission and other compensation remain separated and source-supported. |
| 35 | Preferred Pump & Equipment, L.P. | ISSUE | Combined amount `2,170.33` is duplicated into both commissions and fees. |
| 36 | R. H. White Companies, Inc. | ISSUE | Broker-row commission and fee columns remain swapped/misassigned. |
| 37 | Red Thread | ISSUE | Combined amount `2,910.14` is duplicated into both commissions and fees. |
| 38 | Rhythm Pharmaceuticals, Inc. | SOURCE-CORRECT / REVIEW | Clear core and aggregated commission/fee values agree; duplicate empty occurrence is rejected. |
| 39 | Saint Elizabeth Community | SOURCE-CORRECT / REVIEW | Clear core, premium, totals, and broker values agree with source. |
| 40 | Salesloft, Inc. | SOURCE-LIMITED | Persons covered is absent from the source and must come from census/billing data. |
| 41 | Socure, Inc. | SOURCE-CORRECT / REVIEW | Clear core, experience amounts, premium, and zero compensation values agree. |
| 42 | Special Service for Groups Inc. | SOURCE-CORRECT / REVIEW | Available Kaiser core, premium, commission, and broker values agree. |
| 43 | The Advertising Council | SOURCE-CORRECT / REVIEW | Clear core, premium, compensation, and broker values agree. |
| 44 | The Boston Home, Inc. | ISSUE | Combined `613.34` commissions/fees amount is copied into both separate fields. |
| 45 | The International Group | ISSUE | Placeholder `SEE ABOVE #` is returned instead of the actual policy identifier. |
| 46 | Tilt Holdings, Inc. | ISSUE | Combined amount `1,293.98` is duplicated into both commissions and fees. |
| 47 | Titmouse, Inc. | SOURCE-CORRECT / REVIEW | Available Kaiser core, premium, commission, and broker values agree. |
| 48 | Tower Health | SOURCE-CORRECT / REVIEW | Clear core, premium, scalar totals, and compensation rows reconcile. |
| 49 | Worcester Community Action Council, Inc. | ISSUE | Two separate Schedule A sections are collapsed; fresh output chooses the wrong contract/premium group. |
| 50 | WorldSprings Holdings LLC | SOURCE-CORRECT / REVIEW | Clear core, premium, commission, zero fee, and broker values agree. |

## Field Rule and alias verification

- Focused backend Field Rule and alias suite: **72 passed**.
- Frontend Field Rule creation and alias flow: **passed**.
- Full backend regression: **437 passed, 2 skipped**.
- Frontend TypeScript check: **passed**.
- A newly published field/alias can enter the extraction schema without client-specific parser code, while unknown fields are rejected and extraction-only fields remain excluded from FT Williams updates.

## Release decision

**Do not enable automatic FT Williams updates from EyeLevel extraction yet.**

The safe behavior is correct: every fresh document is Review-required and unrelated Schedule A records were not touched. The remaining provider problem is semantic interpretation of document groups, combined amount columns, totals versus subscriber counts, and repeated broker rows. The confirmed cases are recorded in the separate issue report.

## Evidence

- Fresh upload manifest: `tmp/all_clients_schedule_a_qa_20260829/groundx_upload_manifest_semantic_r2.json`
- Fresh semantic comparison: `tmp/all_clients_schedule_a_qa_20260829/groundx_extraction_comparison_semantic_r2.json`
- Fresh source grounding: `tmp/all_clients_schedule_a_qa_20260829/source_grounding_semantic_r2.json`
- Detailed defects: `docs/qa/all-clients-eyelevel-semantic-rerun-issues-2026-08-29.md`
