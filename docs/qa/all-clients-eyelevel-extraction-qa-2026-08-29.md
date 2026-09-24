# All-client Schedule A / EyeLevel extraction QA report

Date: 2026-08-29
Environment: isolated EyeLevel/GroundX QA bucket 32806 (`Erisapros`)
Workflow: `ERISAPros Schedule A Structured QA v4 20260829`

## Executive result

- Source client folders reviewed: **86**.
- Usable Schedule A PDFs selected: **50 PDFs from 50 clients**.
- Submitted to EyeLevel: **50/50**.
- EyeLevel ingestion completed: **50/50**.
- Ingestion failures: **0**.
- Text-native PDFs: **39**; scanned/image PDFs requiring OCR: **11**.
- Source-verified extraction result: **23 pass**, **25 contain at least one confirmed extraction defect**, and **2 are source-limited**.
- The 25 affected documents are not all total failures: most core carrier/policy values were found, but one or more amounts, repeated sections, broker rows, or field classifications were wrong.
- No FT Williams records were changed during this extraction-only QA run.

## What was tested

For every staged PDF, the source layout was inspected and the EyeLevel structured result was compared for:

- carrier name, EIN, NAIC, contract/policy number, persons covered, and policy dates;
- premiums, commissions, fees, experience-rated fields, and nonexperience-rated fields;
- every detected broker/agent row, address, organization code, amount, and purpose;
- OCR handling for the 11 image-only files;
- dynamic Field Rule and alias plumbing through the automated regression suite.

## Client-by-client result

| # | Client | Result | Source-verified outcome |
|---:|---|---|---|
| 1 | Advocates for Human Potential, Inc. | PASS | Core fields, totals, and three broker rows agree with the Principal worksheet. |
| 2 | Affinity Group, Inc. | ISSUE | Used subscriber count `104` instead of total covered `171`; premium `6,611.92` was classified as compensation and line 10a was missed. |
| 3 | ALL Erection & Crane Rental Corp. | ISSUE | One combined VSP commissions/fees amount was duplicated into both 3b and 3c. |
| 4 | AlphaSights | PASS | Available carrier, dates, premium, and zero compensation values agree with the source. |
| 5 | American Securities LLC | ISSUE | Nonexperience premium/compensation values were also emitted as experience-rated 9-series values. |
| 6 | Barry L. Price Rehabilitation Center, Inc. | ISSUE | Core OCR values were found, but additional compensation `554.22` was omitted. |
| 7 | Brandeis University | ISSUE | One combined VSP amount `2,416.88` was duplicated into both commission and fee fields. |
| 8 | BTIG, LLC | ISSUE | Premium `62,791.32` was placed in commissions and broker payment `5,776.93` in fees; line 10a was missed. |
| 9 | Byrna Technologies, Inc. | ISSUE | Scalar totals are correct, but broker rows reuse total premium/compensation values in the wrong columns. |
| 10 | CDF Corporations | ISSUE | Scalar totals are correct, but one broker row duplicates/misassigns commissions and fees. |
| 11 | Control Associates, Inc. | ISSUE | Core values are correct; multiple compensation rows were omitted from the broker/fee result. |
| 12 | Elyria Foundry Company LLC | PASS | Carrier, policy, premium, commission, fee, and broker values agree with the source. |
| 13 | ERH, LLC (formerly JM Texas) | ISSUE | Extracted placeholder `SEE ABOVE #` instead of resolving the group policy number `975445` shown in the source package. |
| 14 | FGF LLC | PASS | Core fields, premium, totals, and the 37 compensation recipients were retained. |
| 15 | Framestore Inc. | PASS | Core fields, totals, and three broker rows agree with the Principal worksheet. |
| 16 | Fund for the Public Interest Inc. | SOURCE-LIMITED | Extraction follows the source, but the available PDF is a 2022–2023 filing and is not 2025 production truth. |
| 17 | HMR Veteran Services, Inc. | ISSUE | Fee `223,403.64` is misclassified and does not reconcile to source total commissions `263,834.92`; unsupported item 11 was also inferred. |
| 18 | Homes for the Homeless, Inc. | PASS | Core values, premium, commission, fee, and two brokers agree with the source. |
| 19 | Housing Counseling Services, Inc. | PASS | OCR correctly recovered core values, premium, totals, and four broker rows. |
| 20 | Hyde Group Inc. | ISSUE | Values from separate LIFE/LTD coverage sections were mixed into one Schedule A result. |
| 21 | Hyland Software, Inc. | ISSUE | Commission total is `100` low and supplemental compensation `2,816.67` was omitted. |
| 22 | Ideal Clamp Products, Inc. | PASS | Core, premium, broker, and explicitly reported zero experience fields agree with the source. |
| 23 | Ingersoll Cutting Tool Company Inc. | ISSUE | Used subscriber count `321` rather than total covered `658`, and missed premium `49,250.58`. |
| 24 | Jane Street Group, LLC | ISSUE | Broker/fee value `198.16` was incorrectly emitted as experience-rated field 9a(2). |
| 25 | JTB Americas LTD | PASS | Core values, premium, commission, fee, and two broker rows agree with the Cigna source. |
| 26 | Kraft Power Corporation | ISSUE | Schedule C/calendar totals contaminated Schedule A experience-rated premium and expense fields. |
| 27 | Mastery Logistics Systems, Inc. | ISSUE | OCR found core values but omitted additional compensation `2,248.78` and emitted unsupported experience values on a nonexperience report. |
| 28 | Microbest, Inc. | ISSUE | Benefit rows and compensation values contaminated 3d and several 9-series fields; total premium was copied into taxes. |
| 29 | Modera Wealth Management, LLC | ISSUE | Scalar totals are correct, but the `38` marketing-fee broker row was omitted. |
| 30 | New York Yankees Partnership | ISSUE | Core values are correct, but an unsupported zero 9a(2) experience field was added. |
| 31 | Ohio Valley Stamping & Assemblies Inc. | PASS | OCR correctly recovered carrier, policy, persons, premium, compensation, and broker data. |
| 32 | Oxford Biomedica (US) LLC | PASS | OCR correctly recovered the Delta Dental core, premium, commission, and broker values. |
| 33 | Path Robotics | PASS | OCR correctly recovered all tested core, premium, commission, fee, and broker values. |
| 34 | Peerless Clothing International | PASS | OCR correctly separated base commission from bonus/other compensation and retained both broker rows. |
| 35 | Preferred Pump & Equipment, L.P. | ISSUE | One combined VSP commissions/fees amount `2,170.33` was duplicated into both 3b and 3c. |
| 36 | R. H. White Companies, Inc. | ISSUE | Scalar totals are mostly present, but broker-row commission and fee columns are swapped/misassigned. |
| 37 | Red Thread | ISSUE | One combined VSP amount `2,910.14` was duplicated into both 3b and 3c. |
| 38 | Rhythm Pharmaceuticals, Inc. | PASS | Core values and aggregated commission/fee results agree with the source; a duplicate empty broker occurrence was ignored by validation. |
| 39 | Saint Elizabeth Community | PASS | Core, premium, totals, and broker values agree with the source. |
| 40 | Salesloft, Inc. | SOURCE-LIMITED | All values present in the source were extracted, but persons covered is explicitly absent and must come from census/billing data. |
| 41 | Socure, Inc. | PASS | Core, experience-rated amounts, premium, and zero compensation values agree with the source. |
| 42 | Special Service for Groups Inc. | PASS | Available Kaiser core, premium, commission, and broker values agree with the source. |
| 43 | The Advertising Council | PASS | Core, premium, compensation, and broker values agree with the source. |
| 44 | The Boston Home, Inc. | PASS | VSP core, premium, commission, zero fee, and broker values agree with the source. |
| 45 | The International Group | ISSUE | Extracted placeholder `SEE ABOVE #` rather than resolving the actual policy identifier from the source package. |
| 46 | Tilt Holdings, Inc. | ISSUE | One combined VSP amount `1,293.98` was duplicated into both 3b and 3c. |
| 47 | Titmouse, Inc. | PASS | Available Kaiser core, premium, commission, and broker values agree with the source. |
| 48 | Tower Health | PASS | Core values, premium, scalar totals, and four compensation rows reconcile with the source. |
| 49 | Worcester Community Action Council, Inc. | PASS | Core, premium, commission, zero fee, and broker values agree with the source. |
| 50 | WorldSprings Holdings LLC | PASS | Core values, totals, and broker data agree with the Principal worksheet. |

## Field Rule and alias verification

- Controlled automated regression result: **81 passed, 0 failed**.
- The suite includes adding a published Schedule A field, adding an alias without parser code changes, accepting alias keys in the structured result, rejecting unknown fields, and keeping extraction-only fields out of FT Williams updates.
- This corpus run used the isolated QA workflow. No temporary production rule or alias was left enabled.

## Overall conclusion

EyeLevel successfully ingested every PDF and handled both text and OCR documents, but extraction is **not yet globally reliable enough for automatic FT Williams updates**. The main weakness is semantic table/section interpretation, not OCR availability. Affected values must remain review-required until the defects in the separate issue report are fixed and the same 50-document corpus passes again.

## Evidence

- Upload manifest: `tmp/all_clients_schedule_a_qa_20260829/groundx_upload_manifest.json`
- Structured comparison: `tmp/all_clients_schedule_a_qa_20260829/groundx_extraction_comparison.json`
- Source-grounding evidence: `tmp/all_clients_schedule_a_qa_20260829/source_grounding.json`
- OCR contact sheets: `tmp/all_clients_schedule_a_qa_20260829/scan_pages`
- Confirmed defects: `docs/qa/all-clients-eyelevel-extraction-issues-2026-08-29.md`
