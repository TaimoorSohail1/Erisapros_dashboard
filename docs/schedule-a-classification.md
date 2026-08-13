# Automatic Schedule A rating classification

ERISAPros automatically selects the Schedule A rating type. Reviewers do not choose experience or nonexperience manually.

## Decision order

1. A meaningful line 9a amount selects **experience rated**.
2. Explicit `experience rated` wording selects **experience rated**.
3. Premium and claim amounts together select **experience rated**.
4. Explicit `nonexperience rated` wording selects **nonexperience rated**.
5. Premium amounts without claims select **nonexperience rated**.
6. Unclear evidence defaults to **nonexperience rated**.

Blank values, `N/A`, and numeric zero do not count as meaningful evidence. The selected type, reason, confidence, and evidence codes are stored on the filing and FT Williams review.

## Derived values

- Experience rated: line 10a is proposed as `0`.
- Nonexperience rated: lines 9a(4), 9b(3), and 9c(1)(H) are proposed as `0`.
- A premium amount that was extracted without a canonical mapping is promoted to line 10a for a nonexperience-rated filing.
- Original extracted values remain in `value`; automatic values are written to `proposed_value`, so reclassification can restore the source value safely.

## Existing filings

Dry-run the migration first:

```powershell
$env:PYTHONPATH='backend'
backend\.venv\Scripts\python backend\scripts\reclassify_schedule_a.py
```

Review the JSON report, then apply it explicitly:

```powershell
$env:PYTHONPATH='backend'
backend\.venv\Scripts\python backend\scripts\reclassify_schedule_a.py --apply
```

Apply mode records a `SCHEDULE_A_AUTO_CLASSIFICATION_MIGRATED` audit event for every processed filing. Every filing keeps its existing workflow status.
