"""Letter-layout recipients must come from each document, not a sample client."""
import pytest
from decimal import Decimal

from app.services.extractor import (
    extract_first_unum_broker_rows,
    extract_first_unum_schedule_a_fields,
)


@pytest.mark.parametrize("name,street,city,state,postal,commission,fees,extra", [
    ("SYNTHETIC ADVISORY (NY) LLC", "PO Box 2100", "Mineola", "NY", "11501", "7,654.32", ".00", ".00"),
    ("Different Advisory Inc", "42 Example Avenue", "Boston", "MA", "02110", "120.25", "10.50", ".00"),
])
@pytest.mark.parametrize("indent", ["", "    "])
def test_unum_reads_printed_recipient_identity_and_payment_columns(
    name, street, city, state, postal, commission, fees, extra, indent,
):
    pages = [(1, f"""INSURANCE DATA FOR SCHEDULE A (FORM 5500)
First Unum Life Insurance Company
TAX ID: 131898173 NAIC: 64297
CONTRACT NUMBER: 00234567
5. INSURANCE FEES AND COMMISSION INFORMATION:
NAME AND ADDRESS OF SALES ADDITIONAL
EACH SOLICITING AGENT COMMISSION FEES COMPENSATION
OR BROKER RECEIVING PAID PAID PAID
COMPENSATION:
------------------------------------------------
{name} {commission} {fees} {extra}
{street}
{city} {state} {postal}
------------------------------------------------
6. COVERAGE/BENEFITS PROVIDED: DBL
""")]
    if indent:
        pages = [(page, "\n".join(indent + line + " " for line in text.splitlines())) for page, text in pages]
    rows = extract_first_unum_broker_rows(pages)
    assert len(rows) == 1
    row = rows[0]
    assert (row.name, row.address_line_1, row.city, row.state, row.zip_code) == (
        name, street, city, state, postal,
    )
    assert row.commission_total == commission
    assert Decimal(row.fee_total) == Decimal(fees)
    assert row.organization_code_defaulted is True
    values = {f.field_name: f.value for f in extract_first_unum_schedule_a_fields(pages)}
    assert values["3a. Name of Agent/Broker/Person"] == name
    assert values["3b. Amount of Commissions"] == commission
    assert Decimal(values["3c. Amount of Fees"]) == Decimal(row.fee_total)
    assert values["1d. Contract/Policy Number"] == "00234567"
