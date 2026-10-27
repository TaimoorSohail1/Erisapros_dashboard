# Native Schedule A title follow-up

Base: merged PR #60, `646ea93c5d143cc4168a39eadba982020b9adad3`.

The first disposable-only dashboard refresh completed one case, then stopped
before the next extraction because content classification returned no type.
No FTWilliam update was sent. The read-only live provider/current-data run had
already completed all nine cases and verified returned values against separate
FTWilliam queries.

The native PDF had one readable page and the full standalone title
“Insurance Data for Schedule A (Form 5500)”. Only one of the classifier's
generic phrases matched. Substituting its canonical title alone reproduced the
distinction, ruling out a missing page or OCR failure.

The follow-up gives that explicit full tax-form title the same classification
weight as two generic markers. Existing worksheet priority remains unchanged.
No carrier, client, filename, policy number or value is encoded in the rule.

Four native-title cases failed before correction. Those cases and six negative
or worksheet cases pass afterward. Existing native/scanned classification
regressions also pass. Full Windows/Linux verification and real-document Linux
classification are required before this follow-up is released.

This changes intake classification only. It does not change extraction,
normalization, matching, FTWilliam serialization, credentials, published rules
or automatic-update controls. Real conflicts remain under review.
