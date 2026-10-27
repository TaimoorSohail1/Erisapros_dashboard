# Bounded native content recognition

## Confirmed issue and scope

The ShareFile classifier consumes the first two native PDF pages, but its reader
previously parsed every page and every page's annotation geometry first. An
isolated, source-hash-verified 131-page scan input peaked at 1,486,912 KiB RSS in
the tested Linux image. Limiting the experiment to the same two consumed pages
peaked at 147,868 KiB and produced identical consumed text. This establishes an
excessive-memory contributor; it does not prove every worker restart has the same
cause.

The correction adds an optional internal page limit to the native reader and
mapping-note view, and passes two only from content recognition. Full extraction
keeps the unlimited default. Native page order, annotation exclusion, scanned
OCR fallback, classification thresholds and worksheet priority are preserved.
No account, routing, published rule, FTWilliam or automatic-update setting changes.

## Regression evidence

- The first regression failed before the fix because recognition visited a third
  page's geometry; it passes with the two-page preview.
- Preview and full-reader cache entries do not contaminate each other's scope.
- Full extraction still reads continuation pages and preserves identifier digits.
- Both modes exclude mapping instructions while retaining printed source values.
- A title beyond the consumed preview does not change classification behavior.
- Focused native/scanned/title/ShareFile/mapping-note suites: 64 passed.

Full Windows/Linux suites, exact-image memory replay and nine-layout content and
extraction replays must be recorded in the PR before promotion. Worker runtime
observation and disposable-only update/read-back/restoration remain separate
release-verification requirements. Retain all earlier failed evidence.
