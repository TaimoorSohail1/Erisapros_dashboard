# Global Schedule A extraction — implementation report

Date: 2026-08-29

## Outcome

The global semantic-validation layer is implemented and fully regression-tested. It is layout-independent: decisions are based on canonical Schedule A fields, source evidence, section meaning, candidate agreement, and financial reconciliation—not carrier or client names.

The new validator remains in shadow mode by default. It records the decision the new pipeline would make without changing reviewer proposals or FT Williams payloads. Authoritative mode remains disabled until a fresh evidence-bearing golden-corpus run passes.

## Implemented controls

- Structured GroundX/EyeLevel extraction generated dynamically from published Field Rules and aliases.
- One canonical Schedule A schema with repeating broker rows.
- One UI value per field while preserving all conflicting candidate values and source citations.
- Required page and source-text evidence for automatic field and broker-row decisions.
- Candidate conflict detection with semantic normalization for equivalent dates and amounts.
- Explicit section-context gates for experience-rated line 9 and nonexperience-rated line 10 values.
- Cross-section duplicate detection between 9a/10a and 9c/Part I compensation.
- Combined commission/fee ambiguity detection; the same unsupported amount cannot be automatic in both columns.
- Broker row name/noise validation, commission/fee column-evidence validation, and scalar-to-row reconciliation.
- Persons-covered validation against enrollment-tier and explicit covered-lives evidence.
- Placeholder contract identifiers such as `SEE ABOVE`, `ON FILE`, and `N/A` are Review-required.
- Multiple coverage/section candidates are preserved and routed to Review instead of silently collapsed.
- New Field Rules and aliases continue to change the extraction schema and validators without parser code changes.

## Automated verification

| Check | Result |
|---|---:|
| Focused canonical-pipeline tests | 21 passed |
| Structured workflow + canonical pipeline | 34 passed |
| Extraction/rules/golden targeted suite | 96 passed |
| Full backend suite | 437 passed, 2 skipped |
| Frontend TypeScript check | Passed |
| Diff whitespace check | Passed |

The only warning is the GroundX SDK's existing deprecation notice for its local YAML preparation helper.

## Saved 50-PDF EyeLevel snapshot replay

The saved raw EyeLevel outputs from the earlier 50-client run were normalized again through the new semantic validator.

| Result | Count |
|---|---:|
| Cases replayed | 50 |
| Automatic | 0 |
| Review-required | 50 |
| Combined commission/fee ambiguity detected | 5 |
| Broker commission total mismatch detected | 8 |
| Broker fee total mismatch detected | 15 |
| Cross-section 9c commission copies detected | 5 |
| Cross-section 9c fee copies detected | 5 |

Zero automatic decisions are expected for this historical snapshot because those stored EyeLevel results do not contain page/source evidence. This proves the new pipeline fails closed; it does not prove that the provider has corrected the extracted values.

## Release boundary

Before enabling authoritative validation:

1. Publish/run the updated EyeLevel workflow and capture fresh page/source evidence.
2. Have reviewers approve expected JSON for every private-corpus PDF.
3. Rerun all 50 PDFs and require exact values for clear fields and Review for every ambiguity.
4. Run deployed shadow comparison for accuracy, latency, and cost.
5. Test one selected Schedule A update in FT Williams and verify unrelated Schedule A records remain unchanged.
6. Enable `SCHEDULE_A_CANONICAL_VALIDATION_ENABLED` gradually with rollback support.

No deployment or live FT Williams write was performed in this implementation slice.
