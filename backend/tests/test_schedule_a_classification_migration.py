import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (
    DocumentType,
    ExtractedField,
    Filing,
    FormType,
    FTWilliamsReview,
    ScheduleAContractType,
)
from app.repositories import MemoryRepository
from app.services.schedule_a_classification_migration import reclassify_active_filings


LINE_9A = "schedule_a_part_iii_9a_premiums_1_amount_received"
LINE_10A = "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"


class ScheduleAClassificationMigrationTests(unittest.TestCase):
    def test_dry_run_reports_changes_without_mutating_the_filing(self):
        async def scenario():
            repo = MemoryRepository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="schedule-a/test.pdf",
                )
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="9a Premiums",
                        normalized_field_name="9a premiums",
                        mapped_rule_key=LINE_9A,
                        mapped_label="9a Premiums",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        value="120000",
                        proposed_value="120000",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="10a Premiums",
                        normalized_field_name="10a premiums",
                        mapped_rule_key=LINE_10A,
                        mapped_label="10a Premiums",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        value="120000",
                        proposed_value="120000",
                    ),
                ]
            )

            report = await reclassify_active_filings(repo, apply_changes=False)
            unchanged = await repo.get_filing(filing.id)
            unchanged_fields = await repo.list_fields(filing.id)
            return report, unchanged, unchanged_fields

        report, filing, fields = asyncio.run(scenario())

        self.assertEqual(report[0]["next_contract_type"], "EXPERIENCE_RATED")
        self.assertEqual(report[0]["derived_field_count"], 1)
        self.assertEqual(filing.schedule_a_contract_type.value, "UNKNOWN")
        self.assertEqual(next(field for field in fields if field.mapped_rule_key == LINE_10A).proposed_value, "120000")

    def test_apply_persists_the_decision_derived_value_and_audit(self):
        async def scenario():
            repo = MemoryRepository()
            filing = await repo.create_filing(
                Filing(file_name="Schedule A.pdf", content_type="application/pdf", file_size=100, s3_key="schedule-a/test.pdf")
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="9a Premiums",
                        normalized_field_name="9a premiums",
                        mapped_rule_key=LINE_9A,
                        mapped_label="9a Premiums",
                        form_type=FormType.SCHEDULE_A,
                        value="120000",
                        proposed_value="120000",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="10a Premiums",
                        normalized_field_name="10a premiums",
                        mapped_rule_key=LINE_10A,
                        mapped_label="10a Premiums",
                        form_type=FormType.SCHEDULE_A,
                        value="120000",
                        proposed_value="120000",
                    ),
                ]
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED,
                    schedule_a_contract_type_reason="Legacy stored review classification.",
                )
            )

            await reclassify_active_filings(repo, apply_changes=True)
            return (
                await repo.get_filing(filing.id),
                await repo.list_fields(filing.id),
                await repo.list_audit_logs(filing.id),
                await repo.get_ftwilliams_review(filing.id),
            )

        filing, fields, audit, review = asyncio.run(scenario())

        self.assertEqual(filing.schedule_a_contract_type.value, "EXPERIENCE_RATED")
        self.assertEqual(filing.status.value, "UPLOADED")
        self.assertEqual(next(field for field in fields if field.mapped_rule_key == LINE_10A).proposed_value, "0")
        self.assertEqual(audit[-1].event, "SCHEDULE_A_AUTO_CLASSIFICATION_MIGRATED")
        self.assertEqual(review.schedule_a_contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertEqual(review.schedule_a_contract_type_reason, filing.schedule_a_contract_type_reason)


if __name__ == "__main__":
    unittest.main()
