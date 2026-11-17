from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
from app.services.extractor import extract_schedule_a_acroform_data
from app.services.schedule_a_benefit_table import reconcile_labeled_benefit_table
from app.services.schedule_a_broker_validation import attach_labelled_broker_payment_columns
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


TABLE = """Non Experience Rated Contracts
Part III Welfare Benefit Contract Information
                                           Approximate Enrollment
                                           (Subscribers/
8. Type  8. Benefit       10.a. Premium     Members)
a/k      Medical PPO      $144,001          130/358
a/j      Medical HMO      $21,702           25/35
* Some values are rounded.
"""


def resolve(fields, pages):
    return resolve_schedule_a_result(NormalizedExtractionResult(
        provider="test", fields=reconcile_labeled_benefit_table(fields, pages),
    )).fields


def test_labelled_component_rows_support_combined_premium_and_member_count():
    fields = [
        NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier",
                                  value="144,001", candidate_values=["165,703"]),
        NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)",
                                  value="358", candidate_values=["35"]),
    ]
    results = resolve(fields, [(2, TABLE)])
    assert [(f.value, f.page, f.decision) for f in results] == [
        ("165,703", 2, "AUTOMATIC"), ("358", 2, "AUTOMATIC"),
    ]


def test_unknown_candidate_or_incomplete_table_remains_in_review():
    field = NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier",
                                      value="144,001", candidate_values=["200,000"])
    assert resolve([field], [(2, TABLE)])[0].decision == "REVIEW_REQUIRED"
    broken = TABLE.replace("25/35", "unreadable")
    assert resolve([field.model_copy(update={"candidate_values": ["165,703"]})], [(2, broken)])[0].decision == "REVIEW_REQUIRED"


def test_filled_form_widget_proves_financial_section_and_parses_spaced_zip():
    fields, brokers = extract_schedule_a_acroform_data({
        "wlfrTotChargesPaidAmt": "13,213.90",
        "insBrokerName_01": "Example Broker",
        "insBrokerAddress_01": "28 Main Road\rRolling Meadows IL 60008 4036",
    })
    premium = next(field for field in resolve(fields, []) if field.field_name.startswith("10a."))
    assert (premium.value, premium.decision) == ("13,213.90", "AUTOMATIC")
    broker, = brokers
    assert (broker.address_line_1, broker.city, broker.state, broker.zip_code) == (
        "28 Main Road", "Rolling Meadows", "IL", "60008-4036",
    )


def test_labelled_broker_columns_repair_evidence_without_changing_payments():
    page = (2, "Broker     Sales and Base     Fees Paid *\n"
            "           Commission Paid\n"
            "Example Broker LLC\n28 Main Road\nBoston, MA 02110     $0.00    $35.50\n* Notes")
    row = ScheduleABrokerRow(name="Example Broker LLC", address_line_1="28 Main Road", city="Boston",
                            state="MA", zip_code="02110", commission_total="0", fee_total="35.50",
                            fee_source_text="Commission Paid", source_page=2)
    swapped = row.model_copy(update={"commission_total": "35.50", "fee_total": "0"})
    attach_labelled_broker_payment_columns([row, swapped], [page])
    assert row.fee_source_text and "Fees Paid" in row.fee_source_text
    assert swapped.fee_source_text == "Commission Paid"
