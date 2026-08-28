import unittest
import xml.etree.ElementTree as ET

from app.models import ScheduleABrokerRow
from app.services.schedule_a_broker_matching import (
    match_schedule_a_brokers,
    resolved_schedule_a_broker_rows,
)
from app.services.xml_builder import build_schedule_a_records_update_xml, schedule_a_replacement_data_gaps


def extracted(name: str, address: str = "", zip_code: str = "", commission: str = "0") -> ScheduleABrokerRow:
    return ScheduleABrokerRow(
        name=name,
        address_line_1=address or None,
        zip_code=zip_code or None,
        commission_total=commission,
        organization_code="03",
    )


def current(index: int, name: str, address: str = "", zip_code: str = "", commission: str = "0") -> dict[str, str]:
    return {
        f"Name{index}": name,
        f"AddressLine1{index:02d}": address,
        f"ZipCode{index:02d}": zip_code,
        f"CommPdAmt{index:02d}": commission,
        f"Code{index:02d}": "03",
    }


class ScheduleABrokerMatchingTests(unittest.TestCase):
    def test_reordered_rows_match_by_identity_not_position(self):
        extracted_rows = [
            extracted("Alpha Broker", "1 Main St", "10001", "100"),
            extracted("Beta Broker", "2 Main St", "10002", "200"),
        ]
        current_rows = [
            current(1, "Beta Broker", "2 Main St", "10002", "20"),
            current(2, "Alpha Broker", "1 Main St", "10001", "10"),
        ]

        matches = match_schedule_a_brokers(extracted_rows, current_rows)

        self.assertTrue(all(match.resolved for match in matches))
        self.assertEqual([match.ftw_index for match in matches], [1, 0])
        aligned = resolved_schedule_a_broker_rows(extracted_rows, current_rows, matches)
        self.assertEqual(aligned[0].name, "Beta Broker")
        self.assertEqual(aligned[1].name, "Alpha Broker")

    def test_duplicate_names_are_disambiguated_by_address_and_zip(self):
        extracted_rows = [
            extracted("NFP CORPORATE SERVICES", "200 Park Ave", "10001", "100"),
            extracted("NFP CORPORATE SERVICES", "200 Park Ave", "10002", "200"),
        ]
        current_rows = [
            current(1, "NFP CORPORATE SERVICES", "200 Park Ave", "10002"),
            current(2, "NFP CORPORATE SERVICES", "200 Park Ave", "10001"),
        ]

        matches = match_schedule_a_brokers(extracted_rows, current_rows)

        self.assertEqual([match.ftw_index for match in matches], [1, 0])
        self.assertTrue(all(match.status == "AUTO_MATCHED" for match in matches))

    def test_dba_alias_with_unique_exact_address_keeps_current_broker_identity(self):
        extracted_rows = [
            extracted(
                "Nth Insurance Agency dba: Alliance 360 I",
                "10833 VALLEY VIEW STREET",
                commission="2340.80",
            )
        ]
        current_rows = [
            current(
                1,
                "NTH INSURANCE AGENCY INC.",
                "10833 VALLEY VIEW STREET",
                commission="3284",
            )
        ]

        matches = match_schedule_a_brokers(extracted_rows, current_rows)
        aligned = resolved_schedule_a_broker_rows(extracted_rows, current_rows, matches)

        self.assertTrue(matches[0].resolved)
        self.assertEqual(matches[0].ftw_index, 0)
        self.assertEqual(matches[0].reason, "Matched by a unique exact broker address.")
        self.assertEqual(aligned[0].name, "NTH INSURANCE AGENCY INC.")
        self.assertEqual(aligned[0].address_line_1, "10833 VALLEY VIEW STREET")
        self.assertEqual(aligned[0].commission_total, "2340.80")

    def test_ambiguous_duplicate_requires_confirmation(self):
        extracted_rows = [extracted("Same Broker", commission="100")]
        current_rows = [current(1, "Same Broker"), current(2, "Same Broker")]

        matches = match_schedule_a_brokers(extracted_rows, current_rows)

        self.assertEqual(matches[0].status, "NEEDS_CONFIRMATION")
        self.assertFalse(matches[0].resolved)
        self.assertEqual(matches[0].candidate_ftw_indexes, [0, 1])

    def test_confirmed_new_row_is_appended_and_unmatched_current_row_is_preserved(self):
        extracted_rows = [
            extracted("Existing Broker", "1 Main St", "10001", "100"),
            extracted("New Broker", "9 New St", "90009", "300"),
        ]
        current_rows = [
            current(1, "Existing Broker", "1 Main St", "10001", "10"),
            current(2, "Keep This Broker", "5 Keep St", "50005", "50"),
        ]
        decisions = {1: {"create_new": True}}

        matches = match_schedule_a_brokers(extracted_rows, current_rows, decisions=decisions)
        aligned = resolved_schedule_a_broker_rows(extracted_rows, current_rows, matches)

        self.assertTrue(all(match.resolved for match in matches))
        self.assertEqual(len(aligned), 3)
        self.assertEqual(aligned[0].name, "Existing Broker")
        self.assertIsNone(aligned[1])
        self.assertEqual(aligned[2].name, "New Broker")

    def test_same_ftw_row_cannot_be_confirmed_for_two_extracted_rows(self):
        extracted_rows = [extracted("First"), extracted("Second")]
        current_rows = [current(1, "Current")]

        with self.assertRaisesRegex(ValueError, "assigned more than once"):
            match_schedule_a_brokers(
                extracted_rows,
                current_rows,
                decisions={0: {"ftw_index": 0}, 1: {"ftw_index": 0}},
            )

    def test_full_replace_keeps_ft_order_and_preserves_unmatched_current_broker(self):
        extracted_rows = [
            extracted("Alpha Broker", "1 Main St", "10001", "100"),
            extracted("Beta Broker", "2 Main St", "10002", "200"),
        ]
        current_rows = [
            current(1, "Beta Broker", "2 Main St", "10002", "20"),
            current(2, "Keep Broker", "5 Keep St", "50005", "50"),
            current(3, "Alpha Broker", "1 Main St", "10001", "10"),
        ]
        records = [
            {
                "ftw_seq_no": "4",
                "query_results": {"InsCarrierName": "Carrier", "InsContractNum": "POLICY"},
                "query_subparts": {"Broker": current_rows},
            }
        ]

        matches = match_schedule_a_brokers(extracted_rows, current_rows)
        aligned = resolved_schedule_a_broker_rows(extracted_rows, current_rows, matches)
        xml = build_schedule_a_records_update_xml(
            records,
            "4",
            [],
            year="2025",
            ftw_customer_id="customer",
            ftw_plan_id="plan",
            schedule_a_broker_rows=aligned,
        )

        brokers = ET.fromstring(xml).findall(".//DOLSubPartData/Broker")
        self.assertEqual([broker.findtext("NameXX") for broker in brokers], ["Beta Broker", "Keep Broker", "Alpha Broker"])
        self.assertEqual([broker.findtext("CommPdAmtXX") for broker in brokers], ["200", "50", "100"])
        self.assertEqual(schedule_a_replacement_data_gaps(records, xml), [])


if __name__ == "__main__":
    unittest.main()
