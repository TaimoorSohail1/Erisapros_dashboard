import hashlib
from unittest.mock import patch

import pytest

from app.models import ExtractedField, ExtractedFieldStatus, Filing, FormType, NormalizedExtractionField, NormalizedExtractionResult
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
from app.services.schedule_a_printed_totals import attach_printed_total_evidence, refresh_verified_totals


SOURCE = """Approximate Number of     Total (e)        111

Persons Covered at End    Employees         92

of Policy Year            Dependents        19

Section 10: Non-Experience Rated Contracts

(a) Total Premiums Paid to Carrier         9,715
"""
FIELDS = {
    "WlfrTotChargesPaidAmt": (
        "10a. Total premiums or subscription charges paid to carrier", "9,715",
        "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier",
        "Automatic extraction requires explicit nonexperience-rated/line 10 source evidence.",
    ),
    "InsPrsnCoveredEoyCnt": (
        "1e. Persons Covered (End of Policy Year)", "111",
        "schedule_a_part_i_1e_persons_covered_end_of_policy_year",
        "Enrollment tiers or an explicit covered-lives total do not support this persons-covered value.",
    ),
}


def test_printed_totals_validate_without_erasing_a_competing_candidate():
    fields = [NormalizedExtractionField(field_name=label, value=value, confidence=0.99)
              for label, value, _, _ in FIELDS.values()]
    fields.append(fields[0].model_copy(update={"candidate_values": ["9,800"]}))
    attach_printed_total_evidence(fields, [(3, SOURCE)])
    result = resolve_schedule_a_result(NormalizedExtractionResult(provider="QA", fields=fields))
    assert [(field.value, field.page, field.decision) for field in result.fields] == [
        ("9,715", 3, "AUTOMATIC"), ("111", 3, "AUTOMATIC"), ("9,715", 3, "REVIEW_REQUIRED"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ("verified", "different_file", "existing_value", "broken_total", "other_conflict"))
async def test_refresh_legacy_totals_only_when_source_and_current_values_agree(variant):
    repo = MemoryRepository()
    data = SOURCE.encode()
    source_hash = hashlib.sha256(data).hexdigest()
    if variant == "broken_total":
        data = data.replace(b"19", b"18", 1)
        source_hash = hashlib.sha256(data).hexdigest()
    elif variant == "different_file":
        source_hash = "0" * 64
    filing = await repo.create_filing(Filing(file_name="qa.pdf", content_type="application/pdf", file_size=len(data),
        s3_key="qa.pdf", package_documents=[{"document_type": "SCHEDULE_A", "file_name": "qa.pdf", "s3_key": "qa.pdf", "source_sha256": source_hash}]))
    fields = await repo.add_fields([
        ExtractedField(filing_id=filing.id, source_field_name=label, normalized_field_name=label, mapped_rule_key=key,
            form_type=FormType.SCHEDULE_A, value=value, status=ExtractedFieldStatus.LOW_CONFIDENCE,
            status_reason="Extraction requires review; automatic proposal withheld. " + reason
                + (" Multiple source candidates disagree for this field." if variant == "other_conflict" else ""))
        for label, value, key, reason in FIELDS.values()
    ])
    current = {"InsPrsnCoveredEoyCnt": "99"} if variant == "existing_value" else {}
    with patch("app.services.schedule_a_printed_totals.StorageService.load_pdf", return_value=data), \
            patch("app.services.extractor.extract_pdf_layout_text_pages", return_value=[(3, data.decode())]):
        refreshed = await refresh_verified_totals(repo, filing, fields, current)
    comparison = FTWilliamsReviewService()._comparison_fields(refreshed, {}, current)
    rows = {row.ftw_tag: row for row in comparison}
    assert rows["WlfrTotChargesPaidAmt"].update_included == (variant in {"verified", "existing_value", "broken_total"})
    assert rows["InsPrsnCoveredEoyCnt"].update_included == (variant == "verified")
