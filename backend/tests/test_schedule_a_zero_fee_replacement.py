"""Approved zero-without-purpose representation must not retain an old fee."""
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from app.models import ScheduleABrokerRow
from app.services.xml_builder import (
    build_schedule_a_records_update_xml,
    schedule_a_broker_multipart_rows,
    schedule_a_broker_update_values,
    schedule_a_replacement_data_gaps,
)
from app.services.ftwilliams_contract import FTWPayloadValidationError


def broker(fees="0.00", purpose=None):
    return ScheduleABrokerRow(
        name="SAMPLE ADVISORY LLC", organization_code="6",
        commission_total="125.25", fee_total=fees, purpose=purpose,
        decision="AUTOMATIC",
    )


@pytest.mark.parametrize("old_fee,old_purpose", [("0", ""), ("45.50", "OLD SERVICE")])
def test_reviewed_zero_fee_clears_old_amount_and_purpose_without_changing_source(old_fee, old_purpose):
    row = broker()
    existing = {"Broker": [{"Name1": row.name, "CommPdAmt1": "100",
                           "FeesPdAmt1": old_fee, "FeesPdText1": old_purpose, "Code1": "6"}]}
    values = schedule_a_broker_update_values([row])
    rows = schedule_a_broker_multipart_rows({}, query_subparts=existing, overrides=values)
    assert rows[0]["CommPdAmtXX"] == "125.25"
    assert "FeesPdAmtXX" not in rows[0]
    assert "FeesPdTextXX" not in rows[0]
    assert row.fee_total == "0.00"


@pytest.mark.parametrize("fee", ["25.50", "-25.50"])
def test_nonzero_fees_keep_exact_amount_and_purpose(fee):
    values = schedule_a_broker_update_values([broker(fee, "Annual service")])
    assert Decimal(values["FeesPdAmt1"]) == Decimal(fee)
    assert values["FeesPdText1"] == "ANNUAL SERVICE"


def test_unselected_and_restore_payloads_keep_vendor_zero_representation():
    records = [{"ftw_seq_no": "1", "query_results": {"InsCarrierName": "SAMPLE"},
                "query_subparts": {"Broker": [{"Name1": "SAMPLE ADVISORY LLC",
                    "Code1": "6", "CommPdAmt1": "125.25", "FeesPdAmt1": "0"}]}}]
    xml = build_schedule_a_records_update_xml(
        records=records, fields=[], matched_ftw_seq_no=None,
    )
    root = ET.fromstring(xml)
    assert root.findtext(".//Broker/FeesPdAmtXX") == "0"


@pytest.mark.parametrize("fee", ["unknown", "N/A", "not paid"])
def test_unknown_fee_is_not_treated_as_a_printed_zero(fee):
    with pytest.raises(FTWPayloadValidationError):
        schedule_a_broker_update_values([broker(fee)])


def test_preservation_guard_requires_explicit_selected_zero_fee_clear():
    record = {"ftw_seq_no": "1", "query_results": {"InsCarrierName": "SAMPLE"},
              "query_subparts": {"Broker": [{"Name1": "SAMPLE ADVISORY LLC",
                  "Code1": "6", "CommPdAmt1": "100", "FeesPdAmt1": "45.50",
                  "FeesPdText1": "OLD SERVICE"}]}}
    xml = build_schedule_a_records_update_xml([record], "1", [], schedule_a_broker_rows=[broker()])
    assert schedule_a_replacement_data_gaps([record], xml, matched_ftw_seq_no="1")
    assert not schedule_a_replacement_data_gaps(
        [record], xml, matched_ftw_seq_no="1", schedule_a_broker_rows=[broker()])
    # A restore or sibling is never granted this selected-record allowance.
    assert schedule_a_replacement_data_gaps([record], xml, schedule_a_broker_rows=[broker()])
