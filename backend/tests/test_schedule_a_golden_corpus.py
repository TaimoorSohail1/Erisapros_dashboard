import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_golden_corpus import GoldenScheduleACase, evaluate_golden_case, run_golden_corpus


class ScheduleAGoldenCorpusTests(unittest.TestCase):
    def test_precision_and_recall_distinguish_extra_from_missing_fields(self):
        case = GoldenScheduleACase(
            case_id="metrics",
            source_file="metrics.pdf",
            source_sha256="abc123",
            expected_fields={"Expected A": "A", "Expected B": "B"},
            expected_review=True,
        )
        result = NormalizedExtractionResult(
            provider="test",
            fields=[
                NormalizedExtractionField(field_name="Expected A", value="A", confidence=0.9),
                NormalizedExtractionField(field_name="Unexpected", value="X", confidence=0.9),
            ],
            raw={"extraction_quality": {"decision": "REVIEW_REQUIRED"}},
        )

        report = evaluate_golden_case(case, result)

        self.assertEqual(report.field_precision, 0.5)
        self.assertEqual(report.field_recall, 0.5)

    def test_exact_fields_and_multiple_brokers_pass_the_golden_case(self):
        case = GoldenScheduleACase(
            case_id="principal-multi-broker",
            source_file="principal.pdf",
            source_sha256="abc123",
            expected_fields={
                "1a. Name of Insurance Company": "Principal Life Insurance Company",
                "1e. Persons Covered (End of Policy Year)": "470",
            },
            expected_brokers=[
                {"name": "NFP Corporate Services NY LLC", "commission_total": "13369", "fee_total": "2913"},
                {"name": "Mercer Health & Benefits LLC", "commission_total": "9407", "fee_total": "2014"},
            ],
        )
        result = NormalizedExtractionResult(
            provider="canonical pipeline",
            fields=[
                NormalizedExtractionField(field_name=key, value=value, confidence=0.95)
                for key, value in case.expected_fields.items()
            ],
            schedule_a_broker_rows=[ScheduleABrokerRow(**row, confidence=0.95) for row in case.expected_brokers],
        )

        report = evaluate_golden_case(case, result)

        self.assertTrue(report.passed)
        self.assertEqual(report.field_precision, 1.0)
        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.broker_exact_match, 1.0)
        self.assertEqual(report.differences, [])

    def test_runner_verifies_source_hash_and_aggregates_manifest_results(self):
        source = b"synthetic schedule a"
        digest = hashlib.sha256(source).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.pdf").write_bytes(source)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "case_id": "sample",
                            "source_file": "sample.pdf",
                            "source_sha256": digest,
                            "expected_fields": {"1b. Insurance Carrier EIN": "42-0127290"},
                        }
                    ]
                ),
                encoding="utf-8",
            )

            async def extractor(file_bytes, file_name):
                self.assertEqual(file_bytes, source)
                self.assertEqual(file_name, "sample.pdf")
                return NormalizedExtractionResult(
                    provider="test",
                    fields=[
                        NormalizedExtractionField(
                            field_name="1b. Insurance Carrier EIN",
                            value="42-0127290",
                            confidence=0.95,
                        )
                    ],
                )

            report = asyncio.run(run_golden_corpus(manifest, root, extractor))

        self.assertTrue(report.passed)
        self.assertEqual(report.case_count, 1)
        self.assertEqual(report.passed_count, 1)
        self.assertEqual(report.failed_count, 0)


if __name__ == "__main__":
    unittest.main()
