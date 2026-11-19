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
