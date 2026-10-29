# Missing Schedule A defaults - 2026-10-29

The owner authorized these defaults and deployment, with live testing to be done
by the owner. Do not replay the nine carrier PDFs or send FTWilliam filing updates
as part of this release.

## Rules

- An absent persons-covered field defaults to 0 in an identified Schedule A.
- Missing commission and fee amounts on an identified broker default to 0.
- Missing broker purpose defaults to Commission, including when fees are zero.
- Retain supplied values, including supplied purposes and negative amounts.
  Do not replace N/A, unreadable values, conflicting candidates, review holds or
  payment components with guessed zeros. Components without a total still need
  a verified total. Defaults retain policy provenance instead of claiming OCR proof.
- A default count that differs from a nonblank current FTWilliam count still
  requires the established conflict decision.

The new purpose policy supersedes the earlier zero-fee blank-purpose wire policy.
Known non-purpose headings retain their original source trace and use the new
default; supplied recipient purposes remain intact.

## Boundaries

Defaults reach source normalization, review proposals and selected broker XML.
Named retained brokers on the selected record can receive missing-value defaults
when broker updates are included. No empty broker identities are invented.

Unselected schedules, restoration payloads and field-only sends retain their
existing boundaries. Read-back never supplies defaults to make an update appear
successful. Explicit zero rows are observable during verification.

No public API, database schema, authentication, matching, routing, published rule
version, production credentials or automatic-update controls are changed. No
existing filings are migrated or modified by release tooling. The frontend needs
no code or asset change.

## Validation and handoff

The focused regressions cover absent/present values, duplicate source candidates,
default provenance, mapping to zero, nonblank-current conflicts, repeated
normalization, invalid/component-only money, held rows, selected/sibling/restore
boundaries, supplied purposes with zero fees and honest read-back comparison.
Adjacent tests for the superseded blank-purpose policy now assert explicit zero
and Commission or the supplied purpose.

Owner live checks after deployment: process an approved test case with missing
values, confirm the proposed defaults, and verify the saved values. Confirm that
provided values remain intact and unresolved data still requires review. No
live filing-write test is performed by the release agent.
