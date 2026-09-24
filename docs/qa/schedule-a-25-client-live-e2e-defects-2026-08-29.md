# Schedule A — 25-client live E2E defect register

Date: 2026-08-29
Scope: extraction, review actions, target matching, FT Williams updates, and sibling isolation

## Release blockers

### D1 — Wrong-section dates accepted as policy dates

Severity: **critical**
Seen in: ALL Erection, American Securities, Barry L. Price, Camino, Inland Valley Drug, Jane Street.

Preparation dates or nearby header dates were mapped as policy dates. Format validation passes because the values look like dates, but their semantic role is wrong.

Required fix: require page/region evidence tied to a policy-period label, enforce start-before-end, and reject preparation/print dates.

### D2 — Carrier labels and fragments accepted as carrier names

Severity: **critical**
Seen in: ALL Erection, American Securities, Barry L. Price, Camino, HMR, Inland Valley Drug, Jane Street.

Examples include `of America.`, `Guardian`, `SERVICE OR OTHER ORGANIZATION`, and `EIN (Insurance Carrier)`.

Required fix: reject label-only/header fragments; require a complete organization candidate supported by its carrier section and identifiers.

### D3 — Commission and fee column contamination

Severity: **critical**
Seen in: Community Legal Aid SoCal, Barry L. Price, Ideal Clamp, HMR, and other multi-row layouts.

Combined or neighboring values were duplicated, omitted, or assigned to the wrong compensation type.

Required fix: preserve table row and column identity, keep combined values ambiguous, and reconcile broker rows against source totals without inventing a split.

### D4 — Multiple contracts/coverages collapsed into one Schedule A

Severity: **critical**
Seen in: Camino, Kraft Power, Inland Valley Drug.

Required fix: segment carrier/contract/coverage groups before mapping. Emit one candidate Schedule A per unique carrier + EIN + NAIC + contract + policy period.

### D5 — Mixed-document page contamination

Severity: **high**
Seen in: Mastery Logistics package containing current Unum pages and unrelated historical Cigna pages.

Required fix: classify pages first, group pages by document identity and policy period, and prevent cross-group evidence from entering one result.

### D6 — Repeating broker rows incomplete or misaggregated

Severity: **high**
Seen in: Control Associates before manual correction, HMR, Ingersoll, Barry L. Price, Hyland.

Required fix: extract brokers as row objects with name, address, commissions, fees, purpose, organization code, page, and bounding box. Reconcile each row independently.

### D7 — No usable “Create new Schedule A” path when no safe match exists

Severity: **critical**
Reproduced with: Elyria Foundry / HCC Life.

The UI correctly rejected an unrelated Guardian candidate but offered only that candidate. The error text promises “select the correct existing record or create a new Schedule A,” yet the workflow modal provides no create-new action.

Required fix: add a reviewed create-new workflow that carries the selected source identity and broker rows, then performs readback verification. Never force the highest-score candidate when identity is different.

## Environment/data blockers

These are not extraction-code failures, but they prevent end-to-end updates:

- Missing current-year FT Williams filing / Bring Forward required: Brandeis, Community Legal Aid SoCal, Crest Discount Foods, Mastery Logistics.
- Locked/signed/accepted filing: BTIG, Kraft Power, ERH, Hyland, Ideal Clamp, Ingersoll, Inland Valley Drug.
- Missing sponsor EIN/plan number: Byrna, Housing Counseling Services.
- Multiple/invalid plan lookup: Affinity, Microbest, HMR.
- Unsafe target identity despite a numeric score: Camino, Elyria Foundry, ERH, Framestore, Inland Valley Drug.

## Confirmed non-defects

- Review gating worked: uncertain values did not auto-send.
- A manual edit changed exactly one Will Update row.
- Keep Extracted and Keep Current acted on the selected field only.
- The verified Control Associates update confirmed all 7 attempted fields.
- The unrelated Cigna Schedule A remained unchanged.
- FT Williams whole-dollar normalization of premium was handled correctly.

## Required acceptance gate

1. Every material value has page, bounding box, section, table, row, column, and source text evidence.
2. Policy dates cannot originate from preparation/header regions.
3. Carrier values cannot be labels, fragments, placeholders, or addresses.
4. Multi-contract documents emit separate Schedule A groups.
5. Broker rows preserve their individual compensation types.
6. Unsafe or unstable values always enter Review.
7. A no-match filing can create a new reviewed Schedule A without selecting an unrelated record.
8. Every FT Williams send is read back and compared.
9. Every non-target Schedule A remains unchanged.
10. The 25-PDF corpus and prior 50-PDF corpus pass as permanent semantic regression suites.
