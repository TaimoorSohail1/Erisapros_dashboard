# Retire the welfare-plan review question

The dashboard previously inferred **6. Plan is a welfare plan?** from worksheet phrases such as “welfare benefit plan.” This review-only question has no supported FT Williams update tag. Stored copies could appear as blocked or unmapped even when the remaining fields were ready.

The shared retirement mechanism now hides this question by rule key and by label, including legacy unmapped copies. It is excluded from published field rules, new extraction mapping, comparisons, coverage summaries, and all decision counters. Filing detail responses recalculate counters after filtering historical rows. The worksheet parser stops generating the question.

The actual welfare benefit feature codes remain supported. FT Williams tag mappings, XML generation, broker approval requirements, and transport configuration retain their existing behavior. Original extraction history remains available for audit.

## Acceptance checks

- Existing mapped and unmapped question rows disappear from review.
- The question contributes zero conflicts, blocked values, or coverage requirements.
- Other invalid values and genuine conflicts retain their counts.
- Welfare feature codes still extract normally.
- Existing Form 5500 and Schedule A payloads are preserved.

## Local verification

- Backend: **2,030 passed, 2 skipped**.
- Frontend production build and production-bundle smoke check passed.
- Review and dashboard UI regression checks passed.
- Production maintenance must refresh saved summaries and verify payload hashes on existing filings. The release report records those results separately after deployment.
