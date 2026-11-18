import hashlib
from unittest.mock import patch

import pytest

from app.models import (AuditLog, ExtractedField, ExtractedFieldStatus, ExtractionJob, ExtractionJobStatus,
    Filing, FormType, NormalizedExtractionField, NormalizedExtractionResult, RawExtraction)
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.extractor import extract_fields_from_pdf_text, extract_rules_driven_schedule_a_fields, parse_schedule_a_text
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
from app.services.schedule_a_printed_totals import attach_printed_total_evidence, read_printed_totals, refresh_verified_totals


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


@pytest.mark.parametrize("variant", ("one_block", "sample", "different_candidate", "second_policy", "reversed_counts"))
def test_year_end_enrollment_uses_covered_members_instead_of_subscribers(variant):
    label = FIELDS["InsPrsnCoveredEoyCnt"][0]
    heading = "Approximate number of persons covered at end of policy or contract year:"
    body = f"\u2022 {heading}\n\n  \u25e6 Subscribers: 999\n\n  \u25e6 Members: 1,700\n\nFunding Type: Fully Insured"
    pages = [(1, f"Schedule A information\n{body}")]
    candidates = ["999", "1,700"]
    if variant == "sample":
        pages = [(1, pages[0][1].replace(heading, "Enrollment statistics:"))]
    elif variant == "different_candidate":
        candidates.append("2200")
    elif variant == "second_policy":
        pages.append((2, f"{heading} 2000"))
    elif variant == "reversed_counts":
        pages = [(1, pages[0][1].replace("1,700", "700"))]
    fields = [NormalizedExtractionField(field_name=label, value="999", candidate_values=candidates)]
    attach_printed_total_evidence(fields, pages)
    result = resolve_schedule_a_result(NormalizedExtractionResult(provider="Native", fields=fields))
    (covered,) = result.fields
    if variant == "one_block":
        assert (covered.value, covered.page, covered.decision) == ("1700", 1, "AUTOMATIC")
        assert covered.candidate_values == ["1700"]
        assert "Members: 1,700" in covered.source_text
    else:
        assert (covered.value, covered.decision) == ("999", "REVIEW_REQUIRED")


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


@pytest.mark.parametrize("section,expected", (("Experience - Rated Contracts", "AUTOMATIC"),
    ("Experience - Rated Contracts\nNon experience-Rated Contracts", "REVIEW_REQUIRED")))
def test_claims_attach_their_own_experience_rated_section_heading(section, expected):
    label = "9b(1). Benefit Charges (1) Claims paid"
    line = "Benefit Charges\n    (i) Claims Paid    $3,624,642.47"
    field = NormalizedExtractionField(field_name=label, value="3,624,642.47", page=1, source_text=line, confidence=.99)
    attach_printed_total_evidence([field], [(1, f"{section}\n\n{line}")])
    (resolved,) = resolve_schedule_a_result(NormalizedExtractionResult(provider="Native", fields=[field])).fields
    assert resolved.decision == expected


@pytest.mark.parametrize("layout", ("valid", "no_section", "later_section", "ambiguous", "invalid_precision"))
def test_applied_premium_requires_one_amount_in_the_printed_welfare_section(layout):
    heading = "Part III: Welfare Benefit Contract Information"
    label = "Premium applied by Example Health Plan, Inc. during your plan’s contract year: $1,815,868.99"
    page = f"{heading}\n\n{label}\n\nPart IV: Provision of Information"
    if layout == "no_section":
        page = label
    elif layout == "later_section":
        page = f"{heading}\n\nPart IV: Provision of Information\n{label}"
    elif layout == "ambiguous":
        page = page.replace(label, f"{label}\n{label.replace('868.99', '999.00')}")
    elif layout == "invalid_precision":
        page = page.replace("868.99", "868.999")

    result = read_printed_totals([(5, page)])

    if layout == "valid":
        amount, evidence = result["10a"]
        assert str(amount) == "1815868.99"
        assert evidence.page == 5
        assert evidence.source_text == f"{heading}\n{label}"
    else:
        assert "10a" not in result


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


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ("verified", "other_provider_count", "legitimate_local_count", "wrong_hash", "new_job", "different_ftw_count"))
async def test_existing_alias_conflict_is_cleared_only_with_a_bound_trace_and_fresh_source(variant):
    repo = MemoryRepository()
    data = b"stored coverage row"
    source_hash = hashlib.sha256(data).hexdigest()
    name = "schedule.pdf"
    filing = await repo.create_filing(Filing(file_name=name, content_type="application/pdf", file_size=len(data),
        s3_key=name, package_documents=[{"document_type": "SCHEDULE_A", "file_name": name, "s3_key": name, "source_sha256": source_hash}]))
    job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id, status=ExtractionJobStatus.COMPLETED))
    if variant == "new_job":
        await repo.create_extraction_job(ExtractionJob(filing_id=filing.id))
    await repo.add_audit(AuditLog(filing_id=filing.id, event="GROUNDX_SUBMISSION_CHECKPOINT", message="Stored source",
        details={"job_id": job.id, "status": "ACCEPTED", "process_id": "stored-process",
            "binding": {"file_name": name, "form_type": "SCHEDULE_A", "source_sha256": "0" * 64 if variant == "wrong_hash" else source_hash}}))
    count_label, _, rule, _ = FIELDS["InsPrsnCoveredEoyCnt"]
    begin_label, end_label = "1f. Policy Year Beginning Date", "1g. Policy Year Ending Date"
    text = "Approximate number of persons covered: 83   From: 03/01/2025   To: 02/28/2026"
    fresh = [NormalizedExtractionField(field_name=label, value=value, confidence=.98, page=3, source_text=text)
        for label, value in ((count_label, "83"), (begin_label, "03/01/2025"), (end_label, "02/28/2026"))]
    old_count = ["83", "64" if variant == "legitimate_local_count" else "03"]
    old_end = ["02/28/2026", "03/01/2025"]
    stages = [{"stage": stage, "fields": [
        {"field_name": count_label, "value": "83", "candidate_values": counts},
        {"field_name": end_label, "value": "02/28/2026", "candidate_values": ends},
    ]} for stage, counts, ends in (
        ("structured_provider_normalized", ["83", "99"] if variant == "other_provider_count" else ["83"], ["02/28/2026"]),
        ("local_parser_candidates", old_count, old_end), ("final_normalized", old_count, old_end),
    )]
    await repo.add_raw_extraction(RawExtraction(filing_id=filing.id, job_id=job.id, provider="GroundX",
        raw={"file_name": name, "document_type": "SCHEDULE_A", "raw": {"extraction_stages": stages}}))
    fields = await repo.add_fields([ExtractedField(filing_id=filing.id, source_field_name=label,
        normalized_field_name=label, mapped_rule_key=key, form_type=FormType.SCHEDULE_A, value=value,
        status=ExtractedFieldStatus.LOW_CONFIDENCE,
        status_reason="Extraction requires review; automatic proposal withheld. Multiple source candidates disagree for this field.")
        for label, value, key in ((count_label, "83", rule), (end_label, "02/28/2026", "schedule_a_part_i_1g_policy_year_ending_date"))])
    current = {"InsPolicyToDate": "02/28/2026"}
    if variant == "different_ftw_count":
        current["InsPrsnCoveredEoyCnt"] = "15"
    with patch("app.services.schedule_a_printed_totals.StorageService.load_pdf", return_value=data), \
            patch("app.services.extractor.extract_pdf_layout_text_pages", return_value=[(3, text)]), \
            patch("app.services.extractor.extract_fields_from_document_text", return_value=fresh):
        refreshed = await refresh_verified_totals(repo, filing, fields, current)
    rows = {row.ftw_tag: row for row in FTWilliamsReviewService()._comparison_fields(refreshed, {}, current)}
    assert rows["InsPrsnCoveredEoyCnt"].decision == ("WILL_UPDATE" if variant == "verified" else "BLOCKED")
    assert rows["InsPolicyToDate"].decision == ("BLOCKED" if variant in {"wrong_hash", "new_job"} else "NO_CHANGE")
