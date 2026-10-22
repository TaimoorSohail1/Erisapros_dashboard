"""Approved interpretations must not become global source-data overrides."""
import hashlib

import pytest

from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow, SourceEvidence
from app.services.schedule_a_confirmed_clarifications import (
    ConfirmedClarifications, VerifiedWorksheetCount, apply_confirmed_clarifications,
)
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline

PDF = b"independently verified test PDF"


def approval(*rules):
    return ConfirmedClarifications(
        source_sha256=hashlib.sha256(PDF).hexdigest(), rules=frozenset(rules),
        reference="Verified client response, Test Cases!D13:D21 / email 2026-10-21",
    )


def field(key, value, text=None):
    return NormalizedExtractionField(field_name=key, value=value, candidate_values=[value], confidence=.98,
        page=1, source_text=text or value,
        evidence=[SourceEvidence(provider="Printed source", page=1, source_text=text or value)])


def apply(result, *rules, pages=None):
    apply_confirmed_clarifications(result, pages or [(1, "Original printed source")], approval(*rules), PDF)
    return apply_schedule_a_pipeline(result, authoritative=True, shadow=False)


def test_approval_cannot_apply_to_different_pdf_or_unknown_rule():
    result = NormalizedExtractionResult(provider="Test", fields=[])
    with pytest.raises(ValueError, match="source"):
        apply_confirmed_clarifications(result, [], approval("padded_naic"), b"different")
    with pytest.raises(ValueError, match="Unknown"):
        approval("disable_all_validation")


def test_confirmed_naic_keeps_literal_evidence_and_changes_only_padded_format():
    result = NormalizedExtractionResult(provider="Test", fields=[field("1c. NAIC Code", "000-61069")])
    apply(result, "padded_naic")
    f = result.fields[0]
    assert f.value == "61069" and f.decision == "AUTOMATIC"
    assert any("000-61069" in (e.source_text or "") for e in f.evidence)
    assert result.raw["confirmed_clarifications"]["changes"][0]["before"] == "000-61069"


@pytest.mark.parametrize("literal", ["123-61069", "000-123456", "61069"])
def test_naic_approval_does_not_drop_arbitrary_identifier_digits(literal):
    result = NormalizedExtractionResult(provider="Test", fields=[field("1c. NAIC Code", literal)])
    apply_confirmed_clarifications(result, [], approval("padded_naic"), PDF)
    assert result.fields[0].value == literal


def test_confirmed_population_uses_extracted_source_count_not_sample_246():
    result = NormalizedExtractionResult(provider="Test", fields=[field(
        "1e. Persons Covered (End of Policy Year)", "389", "Employees: 389")])
    apply(result, "employees_as_covered")
    assert result.fields[0].value == "389" and result.fields[0].decision == "AUTOMATIC"


def test_nonexperience_approval_does_not_remove_unrelated_errors_or_accept_unsupported_money():
    result = NormalizedExtractionResult(provider="Test", fields=[field(
        "10a. Total premiums or subscription charges paid to carrier", "890.12", "Total payments: 890.12")])
    apply(result, "nonexperience_rated")
    assert result.fields[0].decision == "AUTOMATIC"
    bad = NormalizedExtractionResult(provider="Test", fields=[field(
        "10a. Total premiums or subscription charges paid to carrier", "9999", "Total payments: 890.12")])
    apply(bad, "nonexperience_rated")
    assert bad.fields[0].decision == "REVIEW_REQUIRED"


def test_approval_reference_date_is_not_numeric_source_evidence():
    result = NormalizedExtractionResult(provider="Test", fields=[field(
        "10a. Total premiums or subscription charges paid to carrier", "20261021", "Total payments: 890.12")])
    apply(result, "nonexperience_rated")
    assert result.fields[0].decision == "REVIEW_REQUIRED"


def test_missing_worksheet_is_explicitly_pending_not_an_invented_count():
    result = NormalizedExtractionResult(provider="Test", fields=[field("1e. Persons Covered (End of Policy Year)", "0")])
    apply(result, "count_from_6a2")
    assert result.fields[0].value == "0" and result.fields[0].decision == "REVIEW_REQUIRED"
    assert result.raw["confirmed_clarifications"]["remaining"][0]["field"] == "1e"


def test_merge_only_same_complete_recipient_preserves_money_and_different_addresses():
    def row(street, comm, fee):
        return ScheduleABrokerRow(name="EXAMPLE BENEFITS LLC", address_line_1=street,
            city="CHICAGO", state="IL", zip_code="60674", organization_code="3",
            commission_total=comm, fee_total=fee, source_page=1,
            evidence=[SourceEvidence(provider="Printed", page=1, source_text=street)])
    result = NormalizedExtractionResult(provider="Test", fields=[], schedule_a_broker_rows=[
        row("4565 PAYSPHERE CIR", "100.21", "0"), row("4565 PAYSPHERE CIRCLE", "0", "25.67"),
        row("999 OTHER ROAD", "10", "0")])
    apply_confirmed_clarifications(result, [], approval("merge_same_recipient"), PDF)
    assert len(result.schedule_a_broker_rows) == 2
    merged = result.schedule_a_broker_rows[0]
    assert merged.commission_total == "100.21" and merged.fee_total == "25.67"
    assert len(merged.evidence) >= 2


def test_contract_normalization_keeps_leading_zeros():
    result = NormalizedExtractionResult(provider="Test", fields=[field("1d. Contract/Policy Number", "000045678000")])
    apply(result, "trim_contract_trailing_zeros")
    assert result.fields[0].value == "000045678"


def test_life_adnd_approval_is_explicit_derived_benefit_not_printed_checkbox():
    result = NormalizedExtractionResult(provider="Test", fields=[], raw={"benefit_types": ["life"]})
    apply_confirmed_clarifications(result, [], approval("life_add_adnd"), PDF)
    assert result.raw["benefit_types"] == ["life", "other"]
    assert result.raw["confirmed_clarifications"]["benefit_other_text"] == "AD&D"


def test_no_approval_leaves_source_business_holds_unchanged():
    result = NormalizedExtractionResult(provider="Test", fields=[field("1c. NAIC Code", "000-61069")])
    before = result.model_dump()
    apply_confirmed_clarifications(result, [], None, PDF)
    assert result.model_dump() == before


def test_approved_maximum_can_recover_missing_scalar_from_independent_components():
    result = NormalizedExtractionResult(provider="Test", fields=[], raw={"source_review": {
        "premium_components": [
            {"persons_covered": "102", "page": 2, "source_text": "LIFE 102"},
            {"persons_covered": "89", "page": 2, "source_text": "WD-NST 89"},
        ], "benefit_types": ["life"],
    }})
    apply(result, "maximum_covered_count", "wd_temporary_disability")
    f = next(f for f in result.fields if f.field_name.startswith("1e."))
    assert f.value == "102" and f.decision == "AUTOMATIC"
    assert "temporary_disability" in result.raw["benefit_types"]
    assert not result.raw["confirmed_clarifications"]["remaining"]


def test_source_visual_benefits_are_not_lost_when_raw_summary_is_empty():
    result = NormalizedExtractionResult(provider="Test", fields=[], raw={
        "benefit_types": [], "source_review": {"benefit_types": ["dental", "indemnity"]}})
    apply_confirmed_clarifications(result, [], approval("no_footer_entry"), PDF)
    assert result.raw["benefit_types"] == ["dental", "indemnity"]


def test_combined_payment_approval_attaches_actual_printed_numeric_evidence():
    result = NormalizedExtractionResult(provider="Test", fields=[
        field("3b. Amount of Commissions", "1,234.56", "Generic carrier heading"),
        field("3c. Amount of Fees", "0", "Generic carrier heading"),
        field("3d. Purpose", "COMMISSIONS", "Generic carrier heading"),
    ], raw={"source_review": {"reported_values": [{"key": "combined_broker_payment",
        "value": "1234.56", "page": 1, "source_text": "Commissions/Fees paid for policy: $1,234.56"}]}},
    schedule_a_broker_rows=[ScheduleABrokerRow(name="EXAMPLE BENEFITS", commission_total="1234.56", fee_total="0")])
    apply(result, "combined_payment_commission")
    f = result.fields[0]
    assert f.value == "1234.56" and f.decision == "AUTOMATIC"
    assert any(e.page == 1 and "$1,234.56" in (e.source_text or "") for e in f.evidence)


@pytest.mark.parametrize("policy, expected", [("AB123", "347"), ("OTHER123", "0")])
def test_verified_worksheet_count_requires_matching_sponsor_policy_and_year(policy, expected):
    worksheet = VerifiedWorksheetCount(value="347", source_sha256="a" * 64,
        sponsor_name="Example Industries Inc", policy_number=policy,
        plan_year_begin="2025-01-01", plan_year_end="2025-12-31",
        source_text="6(a)(2) Total active participants on the last day of the plan year: 347",
        reference="Verified original ShareFile test worksheet")
    base = approval("count_from_6a2")
    confirmed = ConfirmedClarifications(source_sha256=base.source_sha256, rules=base.rules,
        reference=base.reference, worksheet=worksheet)
    result = NormalizedExtractionResult(provider="Test", fields=[field("1e. Persons Covered (End of Policy Year)", "0", "NUMBER OF PERSONS COVERED: 0"),
        field("1d. Contract/Policy Number", "AB123"), field("1f. Policy Year Beginning Date", "01/01/2025"),
        field("1g. Policy Year Ending Date", "12/31/2025")])
    apply_confirmed_clarifications(result, [(1, "Plan sponsor Example Industries\nContract AB123")], confirmed, PDF)
    result = apply_schedule_a_pipeline(result, authoritative=True, shadow=False)
    f = next(f for f in result.fields if f.field_name.startswith("1e."))
    assert f.value == expected
    assert f.decision == ("AUTOMATIC" if expected != "0" else "REVIEW_REQUIRED")


def test_extraction_checks_worksheet_identity_against_visible_overlay_not_empty_labels(monkeypatch):
    import asyncio
    import io
    from types import SimpleNamespace
    from pypdf import PdfWriter
    import app.services.extractor as module
    writer, stream = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    data = stream.getvalue()
    ws = VerifiedWorksheetCount(value="347", source_sha256="a" * 64,
        sponsor_name="Example Industries Inc", policy_number="AB123",
        plan_year_begin="2025-01-01", plan_year_end="2025-12-31",
        source_text="6(a)(2) Total active participants on the last day of the plan year: 347",
        reference="Verified original test worksheet")
    confirmed = ConfirmedClarifications(source_sha256=hashlib.sha256(data).hexdigest(),
        rules=frozenset({"count_from_6a2"}), reference="Client approval", worksheet=ws)
    result = NormalizedExtractionResult(provider="Test", fields=[field("1e. Persons Covered (End of Policy Year)", "0", "NUMBER OF PERSONS COVERED: 0"),
        field("1d. Contract/Policy Number", "AB123"), field("1f. Policy Year Beginning Date", "01/01/2025"),
        field("1g. Policy Year Ending Date", "12/31/2025")])
    async def extracted(self, *args):
        return result
    monkeypatch.setattr(module.ExtractionService, "_extract_schedule_a_unresolved", extracted)
    monkeypatch.setattr(module, "_semantic_page_texts", lambda *args: [(1, "PLAN SPONSOR:")])
    monkeypatch.setattr(module, "visible_native_text_pages", lambda *args: [(1, "PLAN SPONSOR: Example Industries\nContract AB123")])
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
        schedule_a_canonical_validation_enabled=True, schedule_a_canonical_validation_shadow_enabled=False))
    output = asyncio.run(module.ExtractionService([], confirmed_clarifications=confirmed).extract_schedule_a(data, "test.pdf"))
    assert next(f for f in output.fields if f.field_name.startswith("1e.")).value == "347"


@pytest.mark.parametrize("conflict", [False, True])
def test_nonexperience_approval_excludes_wrong_section_candidate_but_preserves_conflicts(conflict):
    result = NormalizedExtractionResult(provider="Test", fields=[
        field("9c(1)(A). Commissions", "3.43", "Broker table commission: $3.43"),
        field("3b. Amount of Commissions", "3.43", "Broker table commission: $3.43"),
    ], classification_signals=["EXPLICIT_EXPERIENCE_RATED"] if conflict else [])
    apply_confirmed_clarifications(result, [], approval("nonexperience_rated"), PDF)
    if conflict:
        assert any(f.field_name.startswith("9c") for f in result.fields)
        assert result.raw["confirmed_clarifications"]["remaining"]
    else:
        assert not any(f.field_name.startswith("9c") for f in result.fields)
        assert next(f for f in result.fields if f.field_name.startswith("3b")).value == "3.43"
        excluded = result.raw["confirmed_clarifications"]["excluded_fields"]
        assert excluded[0]["field"]["value"] == "3.43"
        assert excluded[0]["reason"]
