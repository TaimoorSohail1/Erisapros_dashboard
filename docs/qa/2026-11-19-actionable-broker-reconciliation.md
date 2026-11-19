# Resolve the remaining broker-review workflow gaps

The previous 183-file audit had 33 broker-ready records and 150 holds. That is a
broker-readiness count, not 150 independently established client-data conflicts.
This follow-up must distinguish application defects from missing source facts
and destination decisions before making a completion claim.

## Acceptance and delivery plan

1. Reproduce the existing Guardian save failure with the stored PDF and captured
   review. Its printed commission is 3,048.62 and fees are 914.72; the visible
   recipient list allocates only the commission. Do not invent a fee recipient.
2. Fix query completeness independently of record selection. A successful full
   Schedule A query remains complete when no eligible policy matches or the top
   eligible scores tie. Transport failures and missing required parent data
   remain incomplete. The selection still requires an eligible unique winner.
3. Make payment reconciliation actionable globally: display verified expected,
   allocated and remaining category totals, retain multi-row edits, permit
   staging more than one recipient correction, and submit only a reconciled,
   source/destination-bound complete list. Saving an unresolved allocation must
   not claim sending succeeded or assign an unnamed fee automatically.
4. Classify every previous hold against source evidence and current destination
   responses. Repair confirmed shared parsing/mapping defects; document source
   ambiguities, missing recipient facts, contract decisions and missing years.
   Keep manual corrections and partial-address choices.
5. Run failing regressions before fixes, full backend/frontend checks and the
   exact production-image Linux gate. Review and merge the PR, then deploy the
   tested API/worker image and matching frontend assets with rollback recorded.
6. Refresh existing filings through the deployed workflow without uploads or
   provider submissions. Test the actual reconciliation/save path with a
   disposable source/destination case, actual vendor readback and restoration.
   Publish each filing's classification and observed outcome. Report browser
   automation limitations explicitly rather than treating component checks as
   browser screenshots.

## Initial confirmed defects

- A query's completeness currently depends on a selected matching record.
  Thirty-one prior destination holds have successful queries but no selection.
  Refreshing alone cannot resolve that selection decision.
- The broker editor submits the whole list immediately and the error omits the
  exact category difference. A user cannot clearly stage several missing rows
  before the total check succeeds.

The screenshot's fee discrepancy is real source data, while its unclear
resolution and save behavior are workflow defects. Removing the reconciliation
check would overwrite valid FT Williams data with an incomplete payment list.

Further regression testing reproduced a save-loop defect: the original
source-evidence or organization-role warning survived valid reviewer corrections.
The follow-up adds an explicit, source/destination-bound reviewer verification
for source-read gaps and requires unchanged financial reconciliation. It preserves
the original warning as reviewed evidence, never an automatic extraction proof.
