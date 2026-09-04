import json
import unittest
from pathlib import Path

from app.models import (
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerRow,
    SourceEvidence,
)
from app.services.extractor import (
    extract_position_aware_schedule_a_fields,
    extract_position_aware_schedule_a_broker_rows,
    merge_schedule_a_broker_rows,
    select_best_schedule_a_fields,
)
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


FIXTURE = Path(__file__).parent / "fixtures" / "schedule_a_verified_cases.json"


class VerifiedScheduleARegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_verified_support_statements_extract_expected_fields_and_brokers(self):
        for case in self.cases:
            with self.subTest(case=case["case_id"]):
                pages = [(item["page"], item["text"]) for item in case["pages"]]
                fields = {
                    field.field_name: field.value
                    for field in extract_position_aware_schedule_a_fields(pages)
                }
                for label, expected in case["expected_fields"].items():
                    self.assertEqual(fields.get(label), expected, label)

                rows = extract_position_aware_schedule_a_broker_rows(pages)
                self.assertEqual(len(rows), len(case["expected_brokers"]))
                for row, expected in zip(rows, case["expected_brokers"]):
                    self.assertEqual(row.name, expected["name"])
                    self.assertEqual(row.commission_total, expected["commission_total"])
                    self.assertEqual(row.fee_total, expected["fee_total"])
                    self.assertEqual(row.zip_code, expected["zip_code"])
                    self.assertTrue(row.evidence)
                    self.assertIsNotNone(row.evidence[0].table_cell)

                resolved = resolve_schedule_a_result(
                    NormalizedExtractionResult(
                        provider="verified regression fixture",
                        fields=list(
                            extract_position_aware_schedule_a_fields(pages)
                        ),
                        schedule_a_broker_rows=rows,
                    )
                )
                expected_names = set(case["expected_fields"])
                self.assertTrue(
                    all(
                        field.decision == "AUTOMATIC"
                        for field in resolved.fields
                        if field.field_name in expected_names
                    )
                )
                self.assertTrue(all(row.decision == "AUTOMATIC" for row in resolved.schedule_a_broker_rows))

    def test_valid_position_aware_candidate_beats_higher_confidence_invalid_date(self):
        selected = select_best_schedule_a_fields(
            [
                NormalizedExtractionField(
                    field_name="1f. Policy Year Beginning Date",
                    value="9906-02-15",
                    confidence=0.99,
                    page=1,
                    source_text="Contract Identification/Policy Number: 9906-02-15",
                ),
                NormalizedExtractionField(
                    field_name="1f. Policy Year Beginning Date",
                    value="04/01/2024",
                    confidence=0.94,
                    page=1,
                    source_text="Policy Period: 04/01/2024 - 04/01/2025",
                ),
            ]
        )

        self.assertEqual(selected[0].value, "04/01/2024")

    def test_positioned_broker_row_replaces_provider_amount_error_and_address_fragment(self):
        guardian = self.cases[0]
        pages = [(item["page"], item["text"]) for item in guardian["pages"]]
        positioned = extract_position_aware_schedule_a_broker_rows(pages)
        provider_rows = [
            ScheduleABrokerRow(
                name="PARK AVENUE SUITE 3202 NEW YORK NY",
                commission_total="10,166",
                source_page=2,
                evidence=[SourceEvidence(provider="GroundX structured extract", page=2)],
            ),
            ScheduleABrokerRow(
                name="NFP CORPORATE SERVICES NY LLC",
                commission_total="17,582.30",
                fee_total="0",
                source_page=2,
                evidence=[SourceEvidence(provider="GroundX structured extract", page=2)],
            ),
        ]

        merged = merge_schedule_a_broker_rows(provider_rows, positioned)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(merged[0].commission_total, "122,729.20")
        self.assertEqual(merged[0].fee_total, "17,582.30")

    def test_semantic_evidence_keeps_row_and_column_position(self):
        document = SemanticDocument.from_page_texts([(2, "        Policy Period: 01/01/2025 - 12/31/2025")])

        line = document.lines[0]
        self.assertEqual(line.column_start, 8)
        self.assertEqual(line.column_end, 46)

    def test_different_support_statement_contracts_are_separate_policy_groups(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    "Schedule A\nContract Identification/Policy Number: ABC-100\n"
                    "Policy Period: 01/01/2025 - 12/31/2025\n"
                    "Name of Insurance Company: First Insurance Company",
                ),
                (
                    2,
                    "Schedule A\nContract Identification/Policy Number: XYZ-200\n"
                    "Policy Period: 01/01/2025 - 12/31/2025\n"
                    "Name of Insurance Company: Second Insurance Company",
                ),
            ]
        )

        self.assertEqual([group.contract_number for group in document.groups], ["ABC-100", "XYZ-200"])

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(
                provider="test",
                fields=[
                    NormalizedExtractionField(
                        field_name="1a. Name of Insurance Company",
                        value="First Insurance Company",
                        confidence=0.99,
                        page=1,
                        source_text="Name of Insurance Company: First Insurance Company",
                    )
                ],
            ),
            document,
            rules=[],
        )

        self.assertEqual(enriched.raw["semantic_resolution"]["decision"], "REVIEW_REQUIRED")
        self.assertEqual(enriched.fields[0].decision, "REVIEW_REQUIRED")
        self.assertLessEqual(enriched.fields[0].confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
