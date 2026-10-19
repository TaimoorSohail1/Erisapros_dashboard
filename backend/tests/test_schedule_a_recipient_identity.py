import pytest
from app.models import NormalizedExtractionResult
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline

from app.services.extractor import extract_litera_lincoln_schedule_a_broker_rows, extract_litera_lincoln_schedule_a_fields


def lincoln_page(address_a, address_b, code="3"):
    return [(2, f"""SCHEDULE A REPORTING INFORMATION
Insurance fees and commissions paid to agents, brokers, and other persons:
EXAMPLE BENEFITS LLC $100.25 {code}
{address_a}
CHICAGO, IL 60674
Totals: $100.25 $0.00
EXAMPLE BENEFITS LLC $15.50 Broker Bonus {code}
{address_b}
CHICAGO, IL 60674
Totals: $0.00 $15.50
""")]


@pytest.mark.parametrize("address_a,address_b", [
    ("100 EXAMPLE ROAD", "200 EXAMPLE ROAD"),
    ("77 INVENTED CIR", "77 INVENTED CIRCLE"),
])
def test_distinct_printed_recipient_addresses_are_not_combined(address_a, address_b):
    rows = extract_litera_lincoln_schedule_a_broker_rows(lincoln_page(address_a, address_b))
    assert len(rows) == 2
    assert [r.address_line_1 for r in rows] == [address_a, address_b]
    assert rows[0].commission_total == "100.25"
    assert rows[1].fee_total == "15.50"


def test_payments_for_an_identical_printed_recipient_can_share_one_row():
    rows = extract_litera_lincoln_schedule_a_broker_rows(lincoln_page("100 EXAMPLE ROAD", "100 EXAMPLE ROAD"))
    assert len(rows) == 1
    assert rows[0].commission_total == "100.25" and rows[0].fee_total == "15.50"


@pytest.mark.parametrize("code", ["3", "6", "03"])
def test_lincoln_recipient_code_is_read_from_the_document(code):
    rows = extract_litera_lincoln_schedule_a_broker_rows(lincoln_page("100 EXAMPLE ROAD", "100 EXAMPLE ROAD", code))
    assert len(rows) == 1
    assert rows[0].organization_code == code
    assert rows[0].organization_code_defaulted is False


@pytest.mark.parametrize("authoritative", [False, True])
def test_possible_address_alias_requires_review_without_merging_source_rows(authoritative):
    rows = extract_litera_lincoln_schedule_a_broker_rows(lincoln_page("77 INVENTED CIR", "77 INVENTED CIRCLE"))
    result = apply_schedule_a_pipeline(
        NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=rows),
        authoritative=authoritative, shadow=True,
    )
    assert len(result.schedule_a_broker_rows) == 2
    assert all(r.decision == "REVIEW_REQUIRED" for r in result.schedule_a_broker_rows)
    assert all(any(v.validator == "recipient_identity_ambiguity" for v in r.validation_results)
               for r in result.schedule_a_broker_rows)


def test_lincoln_benefit_table_does_not_require_a_printed_cross_reference_to_part_iii():
    text = '''THE LINCOLN NATIONAL LIFE INSURANCE COMPANY
SCHEDULE A REPORTING INFORMATION
(a) Name of insurance carrier: The Lincoln National Life Insurance Company
(d) Contract or identification number: 00987654
Benefits & Contract Type Number of Persons Reporting Period From To
AD&D 53 01/01/2024 12/31/2024
Life 53 01/01/2024 12/31/2024
2. Insurance fee and commission information.
'''
    fields = {field.field_name.split('.')[0]: field.value
              for field in extract_litera_lincoln_schedule_a_fields([(2, text)])}
    assert fields['1e'] == '53'
    assert fields['1f'] == '01/01/2024'
    assert fields['1g'] == '12/31/2024'
