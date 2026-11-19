# Broker addresses

Broker address fields are optional for every carrier.

- **No address provided:** the system sends the address blank. No address review or separate approval is needed.
- **Complete address:** the broker continues automatically when the other filing checks pass. Address line 2 is optional.
- **Partial address:** open **Edit**. Complete the address and choose **Save and continue**, or leave the missing fields blank and choose **Save and send as-is**.
- **Blank organization code:** the system uses **3 — Insurance agent or broker**. Existing valid codes are preserved.

Saving confirms the displayed broker list and payment classifications and resumes normal processing. A second approval is unnecessary after a successful save. The system still checks the selected destination, source information, recipient identity, and payments. Any remaining issue is shown in the review.

If the source or FT Williams data changes while editing, refresh and review the current list before saving. Failed saves retain your entered values.

The review also rechecks eligible stored PDFs without requiring a new upload. Explicit carrier name and EIN labels can recover missing identity fields before matching the existing Schedule A. A unique eligible match is selected automatically; tied matches still need a decision. A query is complete only when the required current FT Williams data has actually loaded.

Broker lists continue without approval when an independent read of the complete PDF agrees with the saved provider extraction, recipients, payment categories and section totals. Missing proof, conflicting amounts, partial addresses and incomplete destination queries remain visible for review. This verification uses the exact stored PDF hash and preserves manual edits.

For packages containing a plan worksheet with several carriers, broker-list verification uses the summaries belonging to the selected source PDF. Sibling carriers in the worksheet do not make that PDF a multi-contract document. Multiple contracts within the PDF itself still require separate verification.

### Stored source recovery

Opening review can recover missing carrier identity and repair recipient address components, signed payments and commission/fee column mapping from the existing source PDF. The PDF bytes must match the saved content hash. This does not upload the file again or submit a new EyeLevel request.

The complete source recipient list must be corroborated before automatic broker replacement. Verified row amounts can also correct stale section summaries. Where an older provider trace has no content hash, only supported, closed source grids that agree across separate source reads can establish new proof. Unknown or conflicting source layouts remain in review; they are not treated as an empty broker list.

An agent number, payee code, account number or contract number is not a broker organization code. Explicit valid organization codes are preserved; a blank organization-code cell defaults to 3. Manual corrections and saved partial-address decisions are retained.

Payment verification accounts for unassigned amounts as well as named recipients. Matching recipient rows and readable pages alone cannot replace a reported fee total with zero. A discrepancy stays in the normal reconciliation flow, where a reviewer can correct the allocation against the source. Automatic proofs issued under earlier checks are reverified; human confirmations are preserved. Identical repeated statement pages are counted once only after their drawing or rendered content is confirmed, while different pages remain separate.

If the FT Williams query loaded successfully but no eligible policy matches, choose the correct destination or confirm a new Schedule A. A record for a different contract is not overwritten automatically. A unique highest eligible match is selected automatically; equal highest scores require a decision. An incomplete or uneditable query must be refreshed before sending.

### Reconcile payment differences

The broker review shows source commission and fee totals, the amounts allocated
to recipients, and the amount remaining in each category. An address correction
does not resolve a missing payment allocation. If a source reports a fee without
naming its recipient, confirm that recipient from the source or client before
adding it. Do not assign it to the commission recipient automatically.

Use **Edit** or **Add broker** and **Stage correction** to build the complete
list when several entries need correction. Staged changes stay in the current
review only; leaving the filing discards them. They do not approve the list or
send an FT Williams update. Once both categories reconcile, **Save and continue**
(or **Save and send as-is** for a partial address) saves the complete list and
normal processing resumes when the other filing checks pass.

A successful complete FT Williams query can still require a policy-selection
decision. Such a decision is shown separately from a failed or incomplete query;
repeatedly refreshing an already loaded list does not resolve a different policy
number or a tied match.

Stored source recovery also checks category totals corroborated across complete,
hash-bound OCR reads. A zero fee total is accepted only when the printed totals
and every recipient payment reconcile; an unassigned payment remains in review.
Explicit payee/producer identifiers are not organization codes. The default of 3
applies after a complete source read confirms that identifier-role mapping error;
explicit organization codes and reviewer corrections are preserved.
