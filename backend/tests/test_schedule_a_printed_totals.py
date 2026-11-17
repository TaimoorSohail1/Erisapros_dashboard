import hashlib
from unittest.mock import patch

import pytest

from app.models import ExtractedField, ExtractedFieldStatus, Filing, FormType, NormalizedExtractionField, NormalizedExtractionResult
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.extractor import extract_fields_from_pdf_text, extract_rules_driven_schedule_a_fields, parse_schedule_a_text
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


@pytest.mark.parametrize("count_separator", (" ", ""))
def test_covered_count_does_not_consume_digits_from_the_policy_date(count_separator):
    text = "(c) NAIC Code: See Attached Listing\n03/01/2025 02/28/2026"
    assert not any(field.field_name.startswith("1e.") for field in parse_schedule_a_text(text, 3))
    fields = parse_schedule_a_text(text.replace("\n03/", f"{count_separator}83    03/"), 3)
    assert [field.value for field in fields if field.field_name.startswith("1e.")] == ["83"]


def test_total_premiums_keep_the_printed_label_and_amount_as_evidence():
    text = "Schedule A carrier statement\n" + "Attachment information. " * 25
    text += "\nTotal premiums* or subscription charges paid to carrier:    $571,719.25\n"
    total = next(field for field in parse_schedule_a_text(text, 2) if field.field_name.startswith("10a."))
    result = resolve_schedule_a_result(NormalizedExtractionResult(provider="Native", fields=[total]))
    assert result.fields[0].decision == "AUTOMATIC"
    assert result.fields[0].value == "571,719.25"
    assert result.fields[0].page == 2
    assert "$571,719.25" in result.fields[0].source_text


def test_covered_alias_cannot_cross_the_next_column_heading():
    label = "Approximate number of persons covered at the end of policy or contract year:"
    text = label + " Policy or contract Year (f) From: 03/01/2025 (g) To: 02/28/2026"
    assert not any(field.field_name.startswith("1e.") for field in extract_rules_driven_schedule_a_fields([(3, text)]))
    fields = extract_rules_driven_schedule_a_fields([(3, text.replace(label, label + " 83"))])
    assert [field.value for field in fields if field.field_name.startswith("1e.")] == ["83"]


def test_native_pdf_uses_the_positioned_premium_label_when_plain_text_is_flattened(monkeypatch):
    label = "Total premiums* or subscription charges paid to carrier: $571,719.25"
    plain = "Attachment information. " * 25 + label
    monkeypatch.setattr("app.services.extractor.extract_pdf_text_pages", lambda _: [(2, plain)])
    monkeypatch.setattr("app.services.extractor.extract_pdf_layout_text_pages", lambda _: [(2, label)])
    monkeypatch.setattr("app.services.extractor.extract_layout_aware_schedule_a_fields", lambda _: [
        NormalizedExtractionField(field_name=FIELDS["WlfrTotChargesPaidAmt"][0], value="571,719.25", page=2, confidence=0.9),
    ])

    fields = extract_fields_from_pdf_text(b"fixture")
    result = resolve_schedule_a_result(NormalizedExtractionResult(provider="Native", fields=fields))
    premium = next(field for field in result.fields if field.field_name.startswith("10a."))
    assert (premium.decision, premium.page, premium.source_text) == ("AUTOMATIC", 2, label)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ("verified", "missing_evidence", "different_file", "existing_value", "broken_total", "other_conflict"))
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
                + ("; Automatic extraction requires a source page and source text that supports the extracted value."
                   if variant == "missing_evidence" and label.startswith("10a.") else "")
                + (" Multiple source candidates disagree for this field." if variant == "other_conflict" else ""))
        for label, value, key, reason in FIELDS.values()
    ])
    current = {"InsPrsnCoveredEoyCnt": "99"} if variant == "existing_value" else {}
    with patch("app.services.schedule_a_printed_totals.StorageService.load_pdf", return_value=data), \
            patch("app.services.extractor.extract_pdf_layout_text_pages", return_value=[(3, data.decode())]):
        refreshed = await refresh_verified_totals(repo, filing, fields, current)
    comparison = FTWilliamsReviewService()._comparison_fields(refreshed, {}, current)
    rows = {row.ftw_tag: row for row in comparison}
    assert rows["WlfrTotChargesPaidAmt"].update_included == (variant in {"verified", "missing_evidence", "existing_value", "broken_total"})
    assert rows["InsPrsnCoveredEoyCnt"].update_included == (variant in {"verified", "missing_evidence"})
