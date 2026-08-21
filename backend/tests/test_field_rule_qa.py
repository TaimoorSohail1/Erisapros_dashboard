import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (
    DocumentType,
    FieldRule,
    FieldRuleMappingMode,
    NormalizedExtractionField,
    NormalizedExtractionResult,
)
import app.repositories as repositories
from app.config import Settings
from app.services.field_rule_admin import FieldRuleService
from app.services.field_rule_qa import run_field_rule_qa


class FakeExtractor:
    async def extract_document(self, file_bytes, file_name, document_type):
        del file_bytes, file_name, document_type
        return NormalizedExtractionResult(
            provider="Controlled QA extractor",
            fields=[
                NormalizedExtractionField(
                    field_name="Policy Category",
                    value="Medical",
                    confidence=0.97,
                    source_text="Carrier Policy Category: Medical",
                )
            ],
        )


class SlowExtractor:
    async def extract_document(self, file_bytes, file_name, document_type):
        del file_bytes, file_name, document_type
        await asyncio.sleep(0.1)
        return NormalizedExtractionResult(provider="Too late", fields=[])


class FieldRuleQATests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_document_qa_reports_the_alias_and_never_sends_extraction_only_fields(self):
        rule = FieldRule(
            key="custom_policy_category",
            label="Policy Category",
            ftw_field="",
            xml_tag=None,
            mapping_mode=FieldRuleMappingMode.EXTRACTION_ONLY,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Custom",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["Carrier Policy Category"],
        )

        result = asyncio.run(
            run_field_rule_qa(
                b"synthetic content",
                "synthetic.txt",
                DocumentType.SCHEDULE_A,
                [rule],
                extractor=FakeExtractor(),
                rule_set_version="qa-version",
            )
        )

        self.assertEqual(result["provider"], "Controlled QA extractor")
        self.assertEqual(result["rule_set_version"], "qa-version")
        self.assertEqual(result["summary"], {"extracted": 1, "matched": 1, "unmatched": 0, "extraction_only": 1})
        self.assertEqual(result["fields"][0]["matched_alias"], "Carrier Policy Category")
        self.assertEqual(result["fields"][0]["mapped_rule_key"], rule.key)
        self.assertFalse(result["fields"][0]["will_send_to_ftw"])
        self.assertIsNone(result["fields"][0]["ftw_tag"])

    def test_catalog_field_alias_publish_and_document_upload_tracer(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            published = next(
                rule
                for rule in await service.published_rules()
                if rule.key == "schedule_a_part_i_1c_naic_code"
            )
            draft = await service.create_draft(
                published.model_copy(
                    update={"aliases": [*published.aliases, "QA Carrier Registry Number"]}
                ),
                actor="qa@example.com",
                reason="Controlled catalog-backed alias tracer",
            )
            await service.publish(
                draft.key,
                actor="qa@example.com",
                reason="Alias matched the controlled Schedule A sample",
            )
            snapshot = await service.published_snapshot()
            settings = Settings(
                groundx_api_key=None,
                groundx_bucket_id=None,
                eyelevel_api_key=None,
                eyelevel_extract_url=None,
            )
            with patch("app.services.extractor.get_settings", return_value=settings):
                result = await run_field_rule_qa(
                    b"QA Carrier Registry Number: 98765\n",
                    "catalog-field-tracer.txt",
                    DocumentType.SCHEDULE_A,
                    snapshot.rules,
                    rule_set_version=snapshot.version,
                )
            return draft, snapshot, result

        draft, snapshot, result = asyncio.run(scenario())

        self.assertEqual(draft.status.value, "DRAFT")
        self.assertTrue(snapshot.version)
        self.assertEqual(result["provider"], "Local document parser")
        self.assertEqual(result["summary"], {"extracted": 1, "matched": 1, "unmatched": 0, "extraction_only": 0})
        self.assertEqual(result["fields"][0]["matched_alias"], "QA Carrier Registry Number")
        self.assertEqual(result["fields"][0]["mapped_rule_key"], "schedule_a_part_i_1c_naic_code")
        self.assertEqual(result["fields"][0]["value"], "98765")
        self.assertEqual(result["fields"][0]["ftw_tag"], "InsCarrierNAICCode")
        self.assertTrue(result["fields"][0]["will_send_to_ftw"])

    def test_discovered_catalog_field_is_comparison_only_in_qa(self):
        class DiscoveredFieldExtractor:
            async def extract_document(self, file_bytes, file_name, document_type):
                del file_bytes, file_name, document_type
                return NormalizedExtractionResult(
                    provider="Controlled QA extractor",
                    fields=[
                        NormalizedExtractionField(
                            field_name="Carrier explanation for missing information",
                            value="Carrier records were incomplete",
                            confidence=0.96,
                            source_text="Carrier explanation for missing information: Carrier records were incomplete",
                        )
                    ],
                )

        rule = FieldRule(
            key="ftw_discovered_schedule_a_ins_fail_provide_info_text",
            label="Reason information was not provided",
            ftw_field="Insurance Carrier Missing Information Explanation",
            xml_tag="InsFailProvideInfoText",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Discovered FT Williams Fields",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["Carrier explanation for missing information"],
        )

        result = asyncio.run(
            run_field_rule_qa(
                b"synthetic content",
                "discovered-field.txt",
                DocumentType.SCHEDULE_A,
                [rule],
                extractor=DiscoveredFieldExtractor(),
                rule_set_version="qa-version",
            )
        )

        self.assertEqual(result["summary"], {"extracted": 1, "matched": 1, "unmatched": 0, "extraction_only": 0})
        self.assertEqual(result["fields"][0]["mapped_rule_key"], rule.key)
        self.assertEqual(result["fields"][0]["matched_alias"], "Carrier explanation for missing information")
        self.assertEqual(result["fields"][0]["ftw_field"], rule.ftw_field)
        self.assertIsNone(result["fields"][0]["ftw_tag"])
        self.assertFalse(result["fields"][0]["will_send_to_ftw"])

    def test_schedule_a_qa_falls_back_before_the_browser_request_times_out(self):
        rule = FieldRule(
            key="schedule_a_part_i_1c_naic_code",
            label="1c. NAIC Code",
            ftw_field="1c. NAIC Code",
            xml_tag="InsCarrierNAICCode",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Part I",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["QA Carrier Registry Number"],
        )

        result = asyncio.run(
            run_field_rule_qa(
                b"QA Carrier Registry Number: 98765\n",
                "carrier-sample.txt",
                DocumentType.SCHEDULE_A,
                [rule],
                extractor=SlowExtractor(),
                rule_set_version="qa-version",
                qa_timeout_seconds=0.01,
            )
        )

        self.assertIn("GroundX QA timeout", result["provider"])
        self.assertEqual(result["summary"]["matched"], 1)
        self.assertEqual(result["fields"][0]["mapped_rule_key"], rule.key)
        self.assertEqual(result["fields"][0]["value"], "98765")


if __name__ == "__main__":
    unittest.main()
