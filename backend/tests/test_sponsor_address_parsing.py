"""Sponsor-address review and serialization regressions; no external services."""

from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ExtractedField, FieldPriority, FormType
from app.services.ftwilliams_contract import FTWPayloadValidationError
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.xml_builder import build_single_document_update_xml


class SponsorAddressParsingTests(unittest.TestCase):
    def field(self, value):
        return ExtractedField(
            filing_id="local-address-test",
            source_field_name="1f. Plan Sponsor Address",
            normalized_field_name="sponsor_address",
            mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
            mapped_label="1f. Plan Sponsor Address",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value=value,
            proposed_value=value,
        )

    def payload(self, field, current=None):
        xml = build_single_document_update_xml(
            "DOL5500Data", [field], FormType.FORM_5500,
            transaction_type="1", customer_id="local-test-customer",
            plan_id="local-test-plan", year="2025", current_values=current,
        )
        root = ET.fromstring(xml)
        return {
            node.tag: node.text for node in root.iter()
            if node.tag.startswith("SD")
        }, xml

    def test_office_prefix_is_losslessly_split_at_review_and_xml_boundaries(self):
        source = "WEWORK OFFICE 20-110, 625 WEST ADAMS STREET, CHICAGO,IL 60661"
        field = self.field(source)
        self.assertEqual(FTWilliamsReviewService._validated_field_value(field), source)
        actual, xml = self.payload(field)
        self.assertEqual(actual, {
            "SDAddressLine1": "625 WEST ADAMS STREET",
            "SDAddressLine2": "WEWORK OFFICE 20-110",
            "SDCity": "CHICAGO", "SDState": "IL", "SDZipCode": "60661",
        })
        self.assertEqual(field.value, source)
        self.assertEqual(field.proposed_value, source)
        self.assertNotIn("PlanYearBeginDate", xml)
        self.assertNotIn("PlanYearEndDate", xml)

    def test_same_split_with_current_locality_emits_only_changed_components(self):
        field = self.field("WEWORK OFFICE 20-110, 625 WEST ADAMS STREET, CHICAGO,IL 60661")
        actual, _ = self.payload(field, {
            "SDAddressLine1": "OLD STREET", "SDAddressLine2": "OLD OFFICE",
            "SDCity": "CHICAGO", "SDState": "IL", "SDZipCode": "60661",
        })
        self.assertEqual(actual, {
            "SDAddressLine1": "625 WEST ADAMS STREET",
            "SDAddressLine2": "WEWORK OFFICE 20-110",
        })

    def test_office_split_is_generic_and_preserves_identifier_zeros(self):
        field = self.field("EXAMPLE OFFICE 003-B, 42 OAK ROAD, BOSTON,MA 02129")
        actual, _ = self.payload(field)
        self.assertEqual(actual, {
            "SDAddressLine1": "42 OAK ROAD", "SDAddressLine2": "EXAMPLE OFFICE 003-B",
            "SDCity": "BOSTON", "SDState": "MA", "SDZipCode": "02129",
        })

    def test_comma_spacing_does_not_change_address_components(self):
        for locality in ("CHICAGO,IL", "CHICAGO, IL", "CHICAGO , IL", "CHICAGO IL"):
            with self.subTest(locality=locality):
                actual, _ = self.payload(self.field(f"625 WEST ADAMS STREET, {locality} 60661"))
                self.assertEqual(actual, {
                    "SDAddressLine1": "625 WEST ADAMS STREET", "SDCity": "CHICAGO",
                    "SDState": "IL", "SDZipCode": "60661",
                })

    def test_existing_suite_floor_po_box_and_simple_layouts_remain_correct(self):
        cases = [
            ("18 CHESTNUT ST. SUITE 500 WORCESTER MA 01608", "18 CHESTNUT ST.", "SUITE 500", "WORCESTER", "MA", "01608"),
            ("815 2ND AVENUE 9TH FLOOR NEW YORK NY 100174503", "815 2ND AVENUE", "9TH FLOOR", "NEW YORK", "NY", "10017-4503"),
            ("36 COOPER SQUARE 3RD FLOOR NEW YORK CITY NY 10003", "36 COOPER SQUARE", "3RD FLOOR", "NEW YORK CITY", "NY", "10003"),
            ("3625 DEL AMO AVENUE, SUITE 260 TORRANCE CA 90503", "3625 DEL AMO AVENUE", "SUITE 260", "TORRANCE", "CA", "90503"),
            ("PO BOX 42, BOSTON, MA 02129", "PO BOX 42", "", "BOSTON", "MA", "02129"),
            ("625 WEST ADAMS STREET, CHICAGO, IL 60661", "625 WEST ADAMS STREET", "", "CHICAGO", "IL", "60661"),
        ]
        for source, street, line2, city, state, zip_code in cases:
            with self.subTest(source=source):
                field = self.field(source)
                FTWilliamsReviewService._validated_field_value(field)
                actual, _ = self.payload(field)
                expected = {"SDAddressLine1": street, "SDCity": city, "SDState": state, "SDZipCode": zip_code}
                if line2:
                    expected["SDAddressLine2"] = line2
                self.assertEqual(actual, expected)

    def test_overlength_or_ambiguous_prefix_stays_blocked_without_truncation(self):
        for source in (
            "AN EXCESSIVELY LONG COWORKING OFFICE 20-110, 625 WEST ADAMS STREET, CHICAGO,IL 60661",
            "WEWORK OFFICE 20-110, 625 VERY VERY VERY LONG WEST ADAMS STREET, CHICAGO,IL 60661",
            "UNKNOWN DELIVERY DETAILS, 625 WEST ADAMS STREET, CHICAGO,IL 60661",
        ):
            with self.subTest(source=source):
                field = self.field(source)
                with self.assertRaises(FTWPayloadValidationError):
                    FTWilliamsReviewService._validated_field_value(field)
                with self.assertRaises(FTWPayloadValidationError):
                    self.payload(field)
                self.assertEqual(field.proposed_value, source)


if __name__ == "__main__":
    unittest.main()
