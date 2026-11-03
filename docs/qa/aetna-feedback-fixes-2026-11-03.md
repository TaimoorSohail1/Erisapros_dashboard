# Aetna feedback release candidate — 2026-11-03

Status: implementation and isolated verification; not deployed. Tracks the Aetna subset of issue #58.

## Requirements and implementation

1. Plan/sponsor names ignore only commas and periods (plus ordinary case/whitespace formatting). Word changes, colons and ampersand-versus-and differences remain meaningful. Preserve the existing spelling in both Form 5500 updates and Schedule A replacement payloads when equivalent.
2. Outgoing carrier/broker names use the first 70/35 characters respectively, rather than substituting abbreviations. The 2025 disposable FTWilliam form confirms those limits and PlanSponsorName's 70-character limit. Existing verified plan/sponsor limits apply at serialization. Original extracted values remain intact; raw XML validation still rejects oversized unsanitized payloads. Identifier, date, code and structured-address validation is not relaxed.
3. Proposed broker purpose is COMMISSIONS, FEES, or COMMISSIONS AND FEES according to nonzero signed category amounts; the approved zero/zero default remains COMMISSION. Preserve extraction evidence and source wording, monetary totals, unresolved holds, and untouched sibling/restore brokers. This change does not reclassify contingent commissions or invent missing payment components.
4. New Schedule A creation receives its exact queried Form 5500 parent and fills missing headers only. Explicit source headers take precedence. Carrier, policy and broker data are never copied from sibling schedules. Existing-record update paths do not inherit headers.

## Verification

Regression tests first reproduced each gap. Tests cover punctuation-only identity retention, real name changes beyond a truncation boundary, unchanged source values, signed compensation categories, unresolved broker holds, preservation of other recipients and restore snapshots, strict raw-XML length validation, and new-parent header inheritance through the service and serializer.

Existing tests asserting the superseded abbreviation/custom-purpose behavior have been updated to the client's new requirements; their amount, isolation and hold assertions remain.

Windows focused verification: 153 tests and 48 subtests passed before final review additions. Final Windows and Linux/image results are recorded separately in the private release evidence.

## Release and live verification gates

- Human review of this exact final diff is still required by the accepted delivery workflow before promotion.
- Linux tests and source/image hash verification must pass before deployment.
- Record the actual current API/worker revisions as rollback targets; change only image references when the reviewed release is promoted.
- Original Aetna example and Medical packet have different contracts. Verify the selected source against its disposable target; never substitute the Medical/Dental sequence merely because carrier or plan matches.
- Production upload, automatic-update, saved-value read-back, restoration and client demonstration are not yet performed. A successful recording diagnostic is not a workflow demonstration.

No frontend, public API, database schema, provider credentials, published rules, routing, automatic-update settings or production filings were changed during implementation.
