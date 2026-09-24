# Global Schedule A extraction pipeline — delivery plan

## 1. Goal

Build one provider-independent, layout-aware Schedule A extraction pipeline that:

- extracts canonical Schedule A fields and every broker row from different document layouts;
- uses published Field Rules and aliases without a code deployment;
- validates meaning and relationships instead of trusting plausible text;
- sends uncertain or conflicting values to Review rather than guessing;
- prevents incorrect or unrelated FT Williams updates; and
- continuously regression-tests the 62-document QA corpus.

## 2. Current baseline

The current service combines GroundX/EyeLevel output with a large deterministic parser in `extractor.py`, then selects values mainly by confidence. Field Rules already support published aliases and extraction-only fields, and the filing pipeline already routes missing or low-confidence data to Review.

QA baseline:

- 62 representative Schedule A documents assessed.
- 16/62 structurally produced all seven core fields.
- 20/62 produced at least one broker row.
- 14/62 produced no usable fields.
- Automated review/rule regression suite is green: 140 backend tests plus frontend review, Field Rules, and FT Williams diagnostic tests.

## 3. Scope

### In scope

- PDF, scanned PDF, DOCX-backed/mislabeled PDF, spreadsheet, CSV, and text input detection.
- Text, OCR, coordinates, reading order, table cells, and page evidence.
- Canonical Schedule A fields, benefit data, and structured multi-broker rows.
- Runtime aliases and newly published Field Rules.
- Typed validation, cross-field validation, confidence calibration, and conflict handling.
- Reviewer evidence and explicit extraction provenance.
- Golden-corpus regression testing and shadow-mode rollout.
- Final selected-Schedule-A isolation verification in the FT Williams test account.

### Out of scope

- Training a custom OCR model in the first release.
- Automatically publishing aliases learned from reviewer corrections.
- Client- or filename-specific production rules as the main solution.
- Updating multiple Schedule A records to satisfy one selected record.
- Sending data to FT Williams before the extraction release gates pass.

## 4. Architecture

```text
Document intake
  -> file signature/type detection
  -> page rendering and text/OCR adapters
  -> unified document model (words, boxes, lines, tables, pages)
  -> layout-region classification
  -> field and broker-row candidate generation
  -> Field Rule/alias mapping
  -> typed and cross-field validation
  -> evidence-based conflict resolution
  -> canonical Schedule A result
  -> confidence/review decision
  -> reviewer approval
  -> selected Schedule A FT Williams update
  -> post-update verification
```

### Core design rules

1. OCR/AI providers are adapters, not the business logic.
2. Every extracted value carries provider, page, source text, bounding box/cells, confidence, and validation results.
3. Confidence alone never resolves a semantic conflict.
4. Unknown layouts fail closed to Review.
5. Field Rules define mapping metadata; validators enforce data meaning.
6. Brokers remain structured rows and are never collapsed into one scalar value.
7. Carrier-specific knowledge may exist as reusable layout-family strategies, never client-specific patches.

## 5. Canonical data contracts

Extend the extraction model with:

- `DocumentPage`: page number, dimensions, text blocks, words, tables, OCR provider.
- `SourceEvidence`: document ID, page, text span, bounding box/cell coordinates, provider.
- `FieldCandidate`: canonical rule key, raw value, normalized value, evidence, extractor, confidence, validation results.
- `ValidationResult`: validator name, pass/fail/warn, reason, normalized value.
- `ResolvedField`: selected candidate or unresolved conflict, with decision reason.
- `BrokerCandidateRow`: name/address/commission/fee/purpose/org code with per-cell evidence.
- `ExtractionDecision`: automatic, review-required, or missing.

Maintain backward compatibility by translating `ResolvedField` and broker rows into the existing `NormalizedExtractionResult` until consumers are migrated.

## 6. Field Rules contract

Each published Field Rule must define or inherit:

- canonical key and label;
- aliases;
- form and section;
- scalar or repeating-row cardinality;
- value type: text, EIN, NAIC, contract ID, date, integer, currency, enum, address, or boolean;
- normalization policy;
- validator set;
- required/priority level;
- FT Williams mapping mode: mapped, reference-only, or extraction-only; and
- whether automatic updates are permitted.

Publishing a new alias or mapped field must update the extraction snapshot immediately. Invalid rules must be rejected before publication.

## 7. Validation policy

### Field validators

- EIN: `NN-NNNNNNN` and reject plan/employer EIN when carrier evidence is required.
- NAIC: 4–6 digits and located near carrier/NAIC context.
- Contract: permitted characters and not a date, heading, EIN, NAIC, or page number.
- Dates: normalized to `MM/DD/YYYY`, correct beginning/ending role, valid order, and plausible duration.
- Persons covered: positive whole number and not a currency/table total.
- Currency: decimal-safe normalization; preserve cents.
- Names/addresses: reject headings and narrative paragraphs.
- Organization code: allowed FT Williams values.

### Cross-field validators

- beginning date must not exceed ending date;
- policy dates must not silently become Form 5500 plan-year dates;
- carrier name, EIN, and NAIC must share the same evidence region/entity;
- broker totals must reconcile to Section 2 totals within an explicit tolerance;
- duplicate brokers must be detected without merging distinct addresses or payments;
- selected contract/carrier identity must be internally consistent; and
- required fields missing or contradictory force Review.

## 8. Delivery slices

### Slice 0 — Freeze the baseline and create the golden corpus

Deliverables:

- Securely catalog all 62 source files; do not place private documents in a public repository.
- Create reviewed expected JSON for core fields, all available fields, broker rows, and expected Review conditions.
- Tag each document by layout family and input type.
- Store hashes so fixture/source drift is detectable.
- Add a corpus runner and accuracy report.

Acceptance gate:

- Every fixture has an approved expected result or an explicit expected ambiguity.
- The existing parser baseline is reproducible.

### Slice 1 — Small end-to-end tracer bullet

Use three documents:

1. a standard structured Schedule A;
2. a carrier letter/scanned layout; and
3. a multi-broker table.

Implement the full path from file detection through evidence, mapping, validation, canonical output, and Review status.

Acceptance gate:

- Exact expected fields and broker rows for all three.
- Every value links to visible source evidence.
- Ambiguous values enter Review.

### Slice 2 — Document normalization layer

Deliverables:

- Detect actual file type by signature/MIME, not extension.
- Support native PDF text plus OCR for scanned pages.
- Produce consistent words, lines, coordinates, reading order, and tables.
- Add provider adapters and timeouts with a deterministic fallback state.
- Cache normalized page artifacts by document hash.

Acceptance gate:

- DOCX-backed/mislabeled PDFs and scanned PDFs are correctly routed.
- Provider failure is visible and never silently treated as trustworthy output.

### Slice 3 — Configuration-driven scalar extraction

Deliverables:

- Generate candidates from label/value proximity, aliases, table cells, and semantic context.
- Move generic extraction logic out of the `extractor.py` monolith into focused modules.
- Preserve existing specialized parsers as temporary candidate producers.
- Apply published Field Rule snapshots consistently to local and provider extraction.

Acceptance gate:

- A newly published test alias and new mapped field extract without deployment.
- Headings are not accepted as values.

### Slice 4 — Semantic resolution and confidence calibration

Deliverables:

- Run typed and cross-field validators.
- Replace highest-confidence-wins with evidence and validation scoring.
- Represent conflicts explicitly.
- Calibrate automatic/review thresholds using the golden corpus.

Acceptance gate:

- No known wrong value is automatically accepted in the 62-document corpus.
- Conflicts and weak evidence are routed to Review with a reason.

### Slice 5 — Structured broker extraction

Deliverables:

- Detect repeated broker blocks and table rows using spatial/table structure.
- Extract name, full address, commissions, fees, purpose, and organization code per row.
- Preserve multiple commission/fee subrows when present.
- Reconcile row totals with Schedule A totals.

Acceptance gate:

- Exact broker-row match on all approved golden fixtures.
- No first-row-only behavior and no accidental row merging.

### Slice 6 — Reviewer evidence and feedback

Deliverables:

- Display provider, source page, highlighted region/text, confidence, validators, and conflict reason.
- Keep manual edits separate from extracted evidence.
- Record reviewer choices as auditable feedback.
- Create an admin workflow to propose—but never automatically publish—new aliases/rules from repeated corrections.

Acceptance gate:

- Reviewer can explain every proposed value from the UI.
- Keep Extracted, Keep Current, and manual edit affect only the selected field/row.

### Slice 7 — Full-corpus hardening

Deliverables:

- Expand layout-family strategies until all 62 fixtures meet their expected result.
- Add mutation tests for confusing dates, headings, totals, and repeated labels.
- Add performance, concurrency, provider outage, and malformed-document tests.

Acceptance gate:

- 100% exact match on unambiguous golden values and broker rows.
- 100% of expected ambiguous cases route to Review.
- Existing review, rule, intake, XML, and FT Williams tests remain green.

### Slice 8 — Shadow rollout

Deliverables:

- Run old and new extraction pipelines side-by-side without changing reviewer proposals.
- Compare values, evidence, latency, cost, and Review rate.
- Monitor by layout family and provider.
- Provide an immediate feature-flag rollback.

Acceptance gate:

- No P0/P1 data-integrity defect.
- New pipeline has zero known false automatic approvals.
- Performance and provider costs remain within agreed budgets.

### Slice 9 — Controlled FT Williams isolation test

Deliverables:

- Select one complete single-broker and one multi-broker test filing.
- Approve only the intended fields/rows.
- Update only the selected Schedule A.
- Re-query FT Williams and compare selected and non-selected Schedule A records.

Acceptance gate:

- Selected record matches the approved payload.
- All other Schedule A business data remains unchanged.
- Edit-check failures are clearly reported; none are bypassed.

### Slice 10 — Gradual production release

Rollout sequence:

1. internal test clients;
2. 5% of eligible filings;
3. 25%;
4. 50%; and
5. 100% after stable monitoring windows.

Pause or rollback on any incorrect automatic value, selected-record isolation failure, material error-rate increase, or provider-cost spike.

## 9. Trackable work packages

1. Build secure golden-corpus manifest and expected-result schema.
2. Add corpus runner, metrics, and diff output.
3. Introduce unified document/page/evidence models.
4. Implement signature-based format routing and OCR adapters.
5. Extract generic candidate generation from the current monolith.
6. Extend Field Rules with type, validators, cardinality, and update policy.
7. Implement typed and cross-field validation.
8. Implement evidence-based conflict resolution and calibrated review decisions.
9. Build spatial multi-broker row extraction and reconciliation.
10. Add reviewer evidence/provenance UI and feedback capture.
11. Add shadow-mode comparison, metrics, alerts, and feature flags.
12. Run controlled FT Williams selected-record isolation QA.
13. Complete staged rollout and operational documentation.

Each package must include acceptance tests, failure-path tests, and a short human QA checklist.

## 10. Required metrics

- exact field precision and recall by canonical field;
- broker row and broker cell exact-match rate;
- false automatic-approval count;
- Review rate and reason distribution;
- provider failure/fallback rate;
- extraction latency and cost per document/page;
- layout-family failure rate;
- reviewer correction rate; and
- selected-Schedule-A isolation failures.

## 11. Release gates

The new pipeline may become authoritative only when:

1. all expected unambiguous values in the golden corpus match exactly;
2. every expected ambiguous/unsupported case enters Review;
3. every broker row and total reconciles correctly;
4. runtime alias/new-field tests pass without deployment-specific code;
5. provider outage and malformed-file tests fail safely;
6. existing automated regression suites remain green;
7. human QA approves source evidence and review controls;
8. shadow metrics show no false automatic approvals; and
9. FT Williams selected-record isolation is proven in the test account.

## 12. Rollback and safety

- Keep the existing pipeline behind a feature flag during migration.
- Persist both old and new extraction snapshots in shadow mode.
- Never send shadow results to FT Williams.
- Roll back by switching the authoritative pipeline flag; do not rewrite historical evidence.
- Preserve audit logs for provider, rule snapshot, validators, reviewer decisions, payload, and post-send verification.

## 13. Definition of done

The initiative is complete when a new Schedule A layout either:

- produces validated canonical fields and structured broker rows with traceable evidence; or
- safely enters Review with a clear reason.

It must never silently guess, publish a new alias automatically, update unrelated fields, or modify a non-selected Schedule A.
