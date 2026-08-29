# EyeLevel structured Schedule A extraction — final implementation report

Date: 2026-08-29

## Outcome

The fresh EyeLevel account and isolated QA bucket were tested successfully. All 10 accessible PDFs reached `complete` status with zero ingestion errors, and their structured results were compared with the source PDFs and the deterministic local parser.

The extraction implementation is complete for the accessible corpus and all backend/frontend regressions pass. It remains fail-closed: a required value is sent to Review when the document does not explicitly provide it.

This run did not deploy the code, attach the workflow to the application bucket, or send any update to FT Williams.

## EyeLevel live verification

| Item | Result |
|---|---|
| Isolated QA bucket | `32806` (`Erisapros`) |
| Workflow | `51d4302f-da9a-4124-ae81-5a448eedebdb` |
| Workflow name | `ERISAPros Schedule A Structured QA v4 20260829` |
| Schema version | `433ec2561bc4` |
| PDFs submitted | 10 |
| Unique PDFs | 9 |
| EyeLevel documents completed | 10 |
| EyeLevel processing errors | 0 |
| Existing application bucket changed | No |
| FT Williams data changed | No |

The duplicate VOLLIFE PDFs produced semantically equivalent values after normalization.

## Per-document QA result

| PDF | Decision | Key verified result |
|---|---|---|
| `1. LIFE.pdf` | Review | Contract `FLX0966852`, dates and premium correct; source directs the filer to census data for persons covered. |
| `2.VOLLIFE (1).pdf` | Review | Contract `FLX0966853`, dates, premium, and broker correct; persons covered is not explicit. |
| `2.VOLLIFE.pdf` | Review | Exact duplicate; normalized result matches the other VOLLIFE copy. |
| `3.ADD.pdf` | Review | Contract `OK 0968358`, dates, premium, and broker correct; persons covered is not explicit. |
| `4. VOLADD.pdf` | Review | Contract `OK 0968359`, dates, premium, and broker correct; persons covered is not explicit. |
| `5. STD.pdf` | Review | Contract `LK 0751856`, dates, premium, and broker correct; persons covered is not explicit. |
| `6. NYD.pdf` | Review | Contract `NYD0075359`, dates, premium, and broker correct; persons covered is not explicit. |
| `7. 1.25-12.25 JTB USA Inc HMSA.pdf` | Automatic | Group `012763`, persons `23`, dates, and total premium `$211,487.10` verified from labeled table columns. |
| `8. Medical Schedule A Cigna.pdf` | Review | Header, premium, two primary brokers, commissions, and fees correct; primary Schedule A persons field is blank. |
| `9.DENTAL.pdf` | Automatic | Contract `0656053`, persons `235`, premium, totals, and exactly two primary broker rows are correct. |

Review is the expected result for eight files because their source documents do not explicitly supply Schedule A item 1e. The system no longer guesses a count from exposure, enrollment, or appendix tables.

## Defects found and fixed

- Reference/lookup Form 5500 values were being treated as extractable Schedule A fields. They are now excluded from the EyeLevel contract.
- Scalar broker fields 3a–3e could conflict with repeating broker rows. Broker data now has one repeating-row source of truth and a derived summary.
- Cigna appendix allocation rows could be mistaken for primary brokers. The Dental packet now yields two primary brokers instead of 22 appendix rows.
- Line 10 totals could be copied into line 9 experience-rated fields. Those false duplicates are removed.
- Persons covered could be inferred from unrelated counts. It is now accepted only from explicit evidence or a recognized labeled table.
- HMSA `Group # 012763` and `Sub Group 001` were incorrectly joined as `12763 1`. The fallback now preserves the actual contract/group identifier `012763`.
- Broker payment rows for the same identity are consolidated, while separate brokers remain separate.
- New dynamic/static Field Rules and aliases participate in workflow generation; reference/lookup rules remain intentionally excluded.

## Automated verification

| Check | Result |
|---|---:|
| Full backend suite | 421 passed, 2 skipped |
| Frontend TypeScript check | Passed |
| Review UI and FT Williams diagnostics | Passed |
| Field Rules client-creation flow | Passed |
| Production frontend build | Passed |

One non-blocking warning comes from GroundX SDK 4.0.1's deprecated local workflow-compilation helper. Runtime provisioning already submits the authored YAML directly, as the SDK recommends.

## Scope and release status

The accessible QA corpus contains 10 files, including one exact duplicate, rather than the historical 62-document corpus. This report therefore proves the nine unique layouts currently available—not every possible carrier layout.

Code status: implemented and regression-safe for the tested corpus.

Production status: not deployed and not enabled authoritatively.

Recommended release gate:

1. Add the remaining approved source PDFs and reviewer-approved expected JSON to the golden corpus.
2. Run the same scalar, broker-row, duplicate, and ambiguity checks.
3. Deploy first in shadow mode and compare live outputs without sending FT Williams updates.
4. Enable authoritative extraction only after the expanded corpus passes.

## Primary implementation files

- `backend/app/services/groundx_schedule_a_workflow.py`
- `backend/app/services/extractor.py`
- `backend/app/services/schedule_a_extraction_pipeline.py`
- `backend/app/services/schedule_a_golden_corpus.py`
- `backend/scripts/manage_groundx_schedule_a_workflow.py`
- `backend/scripts/analyze_schedule_a_corpus.py`
- `backend/tests/test_groundx_schedule_a_workflow.py`
- `backend/tests/test_schedule_a_extraction_pipeline.py`
- `backend/tests/test_schedule_a_golden_corpus.py`
- `backend/tests/test_extractor_schedule_a.py`
