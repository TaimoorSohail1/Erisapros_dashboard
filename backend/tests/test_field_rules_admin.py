import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import ExtractedField, FieldRule, FieldRuleApplicability, FieldRuleStatus, Filing
from app.services.field_rule_admin import FieldRuleService
from app.services.mapping import map_extraction_to_rules
from app.models import NormalizedExtractionField
from app.auth import has_field_rule_admin_access
from app.config import Settings
from app.api.filings import re_evaluate_filing_rules
from app.services.schedule_a_classification import filter_schedule_a_fields_for_contract_type
from app.models import ScheduleAContractType


def run_async(coro):
    return asyncio.run(coro)


class FieldRuleAdminTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_draft_does_not_affect_agent_until_it_is_published(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            baseline = await service.list_rules()
            draft = await service.create_draft(
                FieldRule(
                    key="schedule_a_test_admin_rule",
                    label="Test admin field",
                    ftw_field="Test admin field",
                    xml_tag="TestAdminField",
                    priority="MEDIUM",
                    source="Schedule A",
                    form_section="Schedule A - Part I",
                    field_type="Dynamic",
                    existing_behavior="Review Only",
                    new_behavior="Add",
                    applicability=FieldRuleApplicability.BOTH,
                    aliases=["Test admin alias"],
                ),
                actor="admin@example.com",
                reason="Validate the rule lifecycle",
            )
            before_publish = await service.published_rules()
            published = await service.publish(
                draft.key,
                actor="admin@example.com",
                reason="Approved after preview",
            )
            after_publish = await service.published_rules()
            history = await service.history(draft.key)
            return baseline, draft, before_publish, published, after_publish, history

        baseline, draft, before_publish, published, after_publish, history = run_async(scenario())

        self.assertGreaterEqual(len(baseline), 62)
        self.assertEqual(draft.status, FieldRuleStatus.DRAFT)
        self.assertNotIn(draft.key, {rule.key for rule in before_publish})
        self.assertEqual(published.status, FieldRuleStatus.PUBLISHED)
        self.assertIn(published.key, {rule.key for rule in after_publish})
        self.assertEqual([record.status for record in history], [FieldRuleStatus.PUBLISHED, FieldRuleStatus.DRAFT])

    def test_mapping_can_use_the_published_rule_snapshot(self):
        rule = FieldRule(
            key="schedule_a_custom_alias",
            label="1z. Custom published field",
            ftw_field="1z. Custom published field",
            xml_tag="CustomPublishedField",
            priority="HIGH",
            source="Schedule A",
            form_section="Schedule A - Part I",
            field_type="Dynamic",
            aliases=["Client custom heading"],
        )

        result = map_extraction_to_rules(
            "filing-1",
            [NormalizedExtractionField(field_name="Client custom heading", value="ABC", confidence=0.98)],
            rules=[rule],
        )

        self.assertEqual(result["fields"][0].mapped_rule_key, rule.key)
        self.assertEqual(result["fields"][0].proposed_value, "ABC")

    def test_publishing_changes_the_agent_rule_set_version(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            before = await service.published_snapshot()
            draft = await service.create_draft(
                FieldRule(
                    key="schedule_a_versioned_rule",
                    label="Versioned rule",
                    ftw_field="Versioned rule",
                    xml_tag="VersionedRule",
                    priority="LOW",
                    source="Schedule A",
                    field_type="Dynamic",
                    aliases=["Version source"],
                ),
                actor="admin@example.com",
                reason="Test versioning",
            )
            await service.publish(draft.key, actor="admin@example.com", reason="Publish version")
            after = await service.published_snapshot()
            return before, after

        before, after = run_async(scenario())

        self.assertNotEqual(before.version, after.version)
        self.assertIn("schedule_a_versioned_rule", {rule.key for rule in after.rules})

    def test_admin_access_requires_an_allowed_email_or_cognito_group(self):
        settings = Settings(auth_enabled=True, field_rules_admin_emails="support@highlandtech.ai")

        self.assertTrue(has_field_rule_admin_access({"email": "support@highlandtech.ai"}, settings))
        self.assertTrue(has_field_rule_admin_access({"cognito:groups": ["Admins"]}, settings))
        self.assertFalse(has_field_rule_admin_access({"email": "reviewer@example.com"}, settings))

    def test_re_evaluate_rules_uses_stored_values_without_another_extraction(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(file_name="Stored.pdf", content_type="application/pdf", file_size=10, s3_key="stored.pdf")
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="Client custom heading",
                        normalized_field_name="client custom heading",
                        value="ABC",
                        proposed_value="ABC",
                        confidence=0.97,
                    )
                ]
            )
            service = FieldRuleService(repo)
            draft = await service.create_draft(
                FieldRule(
                    key="schedule_a_re_evaluate",
                    label="Re-evaluated field",
                    ftw_field="Re-evaluated field",
                    xml_tag="ReEvaluatedField",
                    priority="HIGH",
                    source="Schedule A",
                    field_type="Dynamic",
                    aliases=["Client custom heading"],
                ),
                actor="admin@example.com",
                reason="Add client alias",
            )
            await service.publish(draft.key, actor="admin@example.com", reason="Approved")
            response = await re_evaluate_filing_rules(filing.id, {"email": "admin@example.com"})
            jobs = await repo.list_extraction_jobs(filing.id)
            return response, await repo.list_fields(filing.id), jobs

        response, fields, jobs = run_async(scenario())

        self.assertEqual(response["status"], "re-evaluated")
        self.assertIn("schedule_a_re_evaluate", {field.mapped_rule_key for field in fields})
        self.assertEqual(jobs, [])

    def test_rule_applicability_controls_contract_specific_visibility(self):
        experience_rule = FieldRule(
            key="custom_experience_field",
            label="Custom experience field",
            ftw_field="Custom experience field",
            priority="HIGH",
            source="Schedule A",
            field_type="Dynamic",
            applicability=FieldRuleApplicability.EXPERIENCE,
        )
        field = ExtractedField(
            filing_id="filing-1",
            source_field_name=experience_rule.label,
            normalized_field_name="custom experience field",
            mapped_rule_key=experience_rule.key,
            proposed_value="100",
        )

        visible = filter_schedule_a_fields_for_contract_type(
            [field], ScheduleAContractType.NONEXPERIENCE_RATED, rules=[experience_rule]
        )

        self.assertEqual(visible, [])


if __name__ == "__main__":
    unittest.main()
