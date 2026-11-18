from app.models import NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_american_heritage import reconcile_american_heritage_report
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline


def payment_page(benefit, count, premium, commission, grand=None):
    def line(*values):
        widths = [21, 21, 22, 29, 18, 24, 23]
        return "".join(value.ljust(width) for value, width in zip(values, widths)) + "".join(values[7:])

    text = [
        "Plan/Contract Year:01-Apr-2025 to 31-Mar-2026",
        "Part I (a) Name of Insurance Carrier: AMERICAN HERITAGE LIFE",
        "SOME CITY IA 50000   INSURANCE COMPANY",
        "Part I (b) EIN: 59-0781901",
        "Part I (c) NAIC Code: 60534",
        "Includes the Following Accounts: 12345",
        line("Part I, Sec 1 (d)", "Part I, Sec 1 (e)", "Part III, Sec 10a", "Part I, Sec 3 Name and",
             "Agent Number", "Part I, Sec 2 Total", "Part I, Sec 2 (c)", "Part I, Sec 3 (d)"),
        f"{benefit} Account: 12345",
        line(benefit.split()[0], count, premium, "", "", commission, "$0.00"),
    ]
    if " " in benefit:
        text.append(benefit.split(" ", 1)[1])
    text.extend([
        line("", "", "", "EXAMPLE BENEFITS", "A123B", commission, "$0.00"),
        line("", "", "", "COMPANY LLC"),
        line("", "", "", "12 MAIN"),
        line("", "", "", "STREET"),
        line("", "", "", "SUITE 2"),
        line("", "", "", "ANYTOWN IA 50000"),
    ])
    if grand:
        text.append(line("Grand Total", "", grand[0], "", "", grand[1], "$0.00"))
    text.append("Disclaimers:")
    return "\n".join(text)


def test_reconciled_positioned_report_uses_printed_payments_and_preserves_coverage_ambiguity():
    pages = [
        (1, payment_page("Accident", "20", "$200.00", "$10.00")),
        (2, payment_page("Critical Illness", "15", "$300.00", "$14.25", ("$500.00", "$24.25"))),
    ]
    result = NormalizedExtractionResult(provider="stored provider", fields=[],
        schedule_a_broker_rows=[ScheduleABrokerRow(name="EXAMPLE", organization_code="A123B")])
    assert reconcile_american_heritage_report(result, pages)
    final = apply_schedule_a_pipeline(result, authoritative=True)
    broker, = final.schedule_a_broker_rows
    assert broker.name == "EXAMPLE BENEFITS COMPANY LLC"
    assert broker.address_line_1 == "12 MAIN STREET" and broker.address_line_2 == "SUITE 2"
    assert broker.organization_code == "3" and broker.organization_code_defaulted
    assert broker.commission_total == "24.25" and broker.fee_total == "0.00"
    assert broker.decision == "AUTOMATIC"
    fields = {field.field_name.split(".")[0]:field for field in final.fields}
    assert fields["10a"].value == "500.00" and fields["10a"].decision == "AUTOMATIC"
    assert fields["1g"].value == "03/31/2026" and fields["1g"].decision == "AUTOMATIC"
    assert not fields["1e"].value and fields["1e"].decision == "REVIEW_REQUIRED"
    assert fields["1e"].candidate_values == ["20", "15"]


def test_bad_payment_totals_never_replace_provider_rows():
    result = NormalizedExtractionResult(provider="stored provider", fields=[],
        schedule_a_broker_rows=[ScheduleABrokerRow(name="RETAINED RECIPIENT")])
    original = result.model_copy(deep=True)
    report = payment_page("Accident", "20", "$200.00", "$10.00", ("$200.00", "$99.00"))
    assert not reconcile_american_heritage_report(result, [(1, report)])
    assert result == original
