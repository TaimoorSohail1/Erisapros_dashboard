# Schedule A semantic layer implementation report

Date: 2026-08-29

## Outcome

The layout-independent semantic layer is implemented and integrated into the
Schedule A extraction path. EyeLevel/GroundX and the local document parsers now
act as candidate producers; the semantic layer decides whether each candidate
is supported by the source document before the canonical validation pipeline
can mark it automatic.

The feature remains fail-closed. It has **not** been enabled for unattended FT
Williams updates because the 50-document replay did not pass the release gate.

## Implemented behavior

- Builds a page/line semantic document with policy-group boundaries.
- Corrects explicit covered-person totals for Principal, EyeMed, and
  subscriber/member enrollment layouts.
- Resolves explicit carrier-payment and nonexperience-rated premium totals.
- Generates missing fields from published Schedule A Field Rules and aliases
  only when the source contains an explicit `label: value` or `label = value`.
- Rejects placeholders such as `None`, `N/A`, `To be provided`, and
  `Plan will provide`.
- Preserves every candidate and its page/source evidence.
- Marks conflicting alias values for Review rather than selecting one.
- Detects combined `Commissions/Fees` sources and prevents automatic assignment
  to both separate FT Williams fields.
- Keeps independently completed Schedule A policy groups separate.
- Prefers source-evidenced broker rows over unsupported provider confidence.
- Reconciles structured broker rows with commission and fee totals.
- Provides deterministic corpus and stored-provider replay commands.

## Test results

| Test | Result |
|---|---:|
| Backend suite | 451 passed, 2 skipped |
| Backend subtests | 37 passed |
| Frontend typecheck | passed |
| Stored EyeLevel/GroundX cases replayed | 50 |
| Source-bound corrections | 18 |
| Combined commission/fee ambiguities blocked | 3 |
| Multi-policy-group documents isolated | 1 |
| Automatic releases | 0 |
| Review-required releases | 50 |

The three combined compensation ambiguities were detected for Brandeis
University, Red Thread, and The Boston Home. The multi-group document was the
Worcester Community Action Council Altus Dental source.

## Release decision

**Not ready for production automatic updates.** The implementation prevents
unsupported values from reaching FT Williams, but the historical EyeLevel
payloads do not provide complete page/coordinate evidence for every extracted
field. Enabling authoritative mode now would improve safety, not prove complete
extraction accuracy.

## Remaining release gates

1. Approve exact expected JSON for all 50 source PDFs, including every broker
   row and intentionally blank field.
2. Rerun fresh EyeLevel extraction so every clear field carries page/coordinate
   evidence.
3. Reach zero false automatic values and route every ambiguity to Review.
4. Run shadow comparison on new, unseen layouts.
5. Enable the semantic layer gradually, then perform one isolated FT Williams
   update and verify unrelated Schedule A records are unchanged.

## Evidence artifacts

- `tmp/all_clients_schedule_a_qa_20260829/semantic_local_report_r6.json`
- `tmp/all_clients_schedule_a_qa_20260829/groundx_semantic_replay_r4.json`
- `backend/tests/test_schedule_a_semantic_layer.py`
