import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import (
    ExtractedField,
    FieldRule,
    FieldRuleApplicability,
    FieldRuleMappingMode,
    FieldRuleStatus,
    Filing,
    FTWilliamsComparisonField,
    FTWilliamsReview,
)
from app.services.field_rule_admin import FieldRuleService, FieldRuleValidationError
from app.services.ftw_field_catalog import field_catalog_entry
from app.services.ftwilliams_tags import resolve_ftw_current_value
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
            approved = next(rule for rule in baseline if rule.key == "schedule_a_part_i_1a_name_of_insurance_company")
            draft = await service.create_draft(
                approved.model_copy(update={"aliases": [*approved.aliases, "Test admin alias"]}),
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
        before_rule = next(rule for rule in before_publish if rule.key == draft.key)
        self.assertNotIn("Test admin alias", before_rule.aliases)
        self.assertEqual(published.status, FieldRuleStatus.PUBLISHED)
        after_rule = next(rule for rule in after_publish if rule.key == published.key)
        self.assertIn("Test admin alias", after_rule.aliases)
        self.assertEqual([record.status for record in history[:2]], [FieldRuleStatus.PUBLISHED, FieldRuleStatus.DRAFT])

    def test_update_capable_rule_must_use_an_approved_ftw_mapping(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            await service.create_draft(
                FieldRule(
                    key="schedule_a_unapproved_client_field",
                    label="Unapproved client field",
                    ftw_field="Unapproved client field",
                    xml_tag="ClientInventedTag",
                    priority="MEDIUM",
                    source="Schedule A",
                    form_section="Schedule A - Part I",
                    field_type="Dynamic",
                    existing_behavior="Update",
                    new_behavior="Add",
                    aliases=["Client supplied heading"],
                ),
                actor="admin@example.com",
                reason="Must not publish arbitrary FTW mappings",
            )

        with self.assertRaisesRegex(FieldRuleValidationError, "approved FT Williams field"):
            run_async(scenario())

    def test_plan_worksheet_catalog_aliases_are_fixed(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            approved = next(
                rule
                for rule in await service.published_rules()
                if rule.key == "form_5500_part_i_2a_plan_administrator_name"
            )
            await service.create_draft(
                approved.model_copy(update={"aliases": [*approved.aliases, "Client-specific administrator"]}),
                actor="admin@example.com",
                reason="Attempt to alter a fixed Plan Worksheet label",
            )

        with self.assertRaisesRegex(FieldRuleValidationError, "Plan Worksheet labels are fixed"):
            run_async(scenario())

    def test_extraction_only_fields_are_limited_to_schedule_a(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            await service.create_draft(
                FieldRule(
                    key="custom_form_5500_field",
                    label="Custom worksheet field",
                    ftw_field="",
                    mapping_mode=FieldRuleMappingMode.EXTRACTION_ONLY,
                    priority="MEDIUM",
                    source="Form 5500",
                    form_section="Form 5500 - Custom",
                    field_type="Dynamic",
                    existing_behavior="Review Only",
                    new_behavior="Keep FTW",
                    aliases=["Custom worksheet field"],
                ),
                actor="admin@example.com",
                reason="Attempt to add an unnecessary worksheet field",
            )

        with self.assertRaisesRegex(FieldRuleValidationError, "Plan Worksheet uses the protected field catalog"):
            run_async(scenario())

    def test_an_extraction_only_field_can_be_published_without_an_ftw_tag(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            draft = await service.create_draft(
                FieldRule(
                    key="custom_schedule_a_policy_category",
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
                ),
                actor="admin@example.com",
                reason="Capture a client field without sending it to FT Williams",
            )
            return await service.publish(
                draft.key,
                actor="admin@example.com",
                reason="Extraction-only field verified",
            )

        published = run_async(scenario())

        self.assertEqual(published.mapping_mode, FieldRuleMappingMode.EXTRACTION_ONLY)
        self.assertIsNone(published.xml_tag)
        self.assertIsNone(FieldRuleService.approved_update_tag(published.key))

    def test_retired_discovered_ftw_fields_are_hidden_and_disabled(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            retired_key = "ftw_discovered_schedule_a_ins_fail_provide_info_text"
            published = await repositories.get_repository().save_field_rule_version(
                FieldRule(
                    key=retired_key,
                    label="Reason information was not provided",
                    ftw_field="Insurance Carrier Missing Information Explanation",
                    xml_tag="InsFailProvideInfoText",
                    mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
                    priority="MEDIUM",
                    source="Schedule A",
                    form_section="Schedule A - Discovered FTW fields",
                    field_type="Dynamic",
                    existing_behavior="Review Only",
                    new_behavior="Keep FTW",
                    aliases=["Carrier explanation for missing information"],
                    status=FieldRuleStatus.PUBLISHED,
                    version=1,
                ),
            )
            listed = await service.list_rules()
            active = await service.published_rules()
            history = await service.history(retired_key)
            return published, listed, active, history

        published, listed, active, history = run_async(scenario())

        self.assertEqual(published.status, FieldRuleStatus.PUBLISHED)
        self.assertNotIn(published.key, {rule.key for rule in listed})
        self.assertNotIn(published.key, {rule.key for rule in active})
        self.assertEqual(history[0].status, FieldRuleStatus.DISABLED)

    def test_read_only_capability_overrides_stale_update_behavior(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            await service.ensure_seeded()
            existing = next(
                rule
                for rule in await repositories.get_repository().list_field_rule_versions()
                if rule.key == "form_5500_part_i_2a_plan_administrator_name"
            )
            await repositories.get_repository().save_field_rule_version(
                existing.model_copy(
                    update={
                        "id": None,
                        "version": existing.version + 1,
                        "status": FieldRuleStatus.PUBLISHED,
                        "existing_behavior": "Update",
                        "new_behavior": "Add",
                    }
                )
            )
            return next(
                rule
                for rule in await service.list_rules()
                if rule.key == "form_5500_part_i_2a_plan_administrator_name"
            )

        rule = run_async(scenario())

        self.assertEqual(rule.existing_behavior, "Review Only")
        self.assertEqual(rule.new_behavior, "Keep FTW")
        self.assertIsNone(FieldRuleService.approved_update_tag(rule.key))

    def test_discovered_form_5500_field_keeps_its_fixed_catalog_label(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            entry = field_catalog_entry("ftw_discovered_form_5500_admin_address_line_1")
            self.assertIsNotNone(entry)
            await service.create_draft(
                FieldRule(
                    key=entry.key,
                    label="Client-specific administrator address",
                    ftw_field=entry.label,
                    xml_tag=entry.current_tag,
                    mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
                    priority="LOW",
                    source="Form 5500",
                    form_section=entry.form_section,
                    field_type="Dynamic",
                    existing_behavior="Review Only",
                    new_behavior="Keep FTW",
                    aliases=["Client-specific administrator address"],
                    applicability=FieldRuleApplicability.FORM_5500,
                ),
                actor="admin@example.com",
                reason="Attempt to alter a fixed Plan Worksheet field",
            )

        with self.assertRaisesRegex(FieldRuleValidationError, "Plan Worksheet labels are fixed"):
            run_async(scenario())

    def test_publish_requires_a_meaningful_audit_reason(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            approved = next(
                rule
                for rule in await service.published_rules()
                if rule.key == "schedule_a_part_i_1b_insurance_carrier_ein"
            )
            draft = await service.create_draft(
                approved.model_copy(update={"aliases": [*approved.aliases, "Carrier registry EIN"]}),
                actor="admin@example.com",
                reason="Add a verified carrier alias",
            )
            await service.publish(draft.key, actor="admin@example.com", reason="   ")

        with self.assertRaisesRegex(FieldRuleValidationError, "change reason"):
            run_async(scenario())

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

    def test_extraction_only_mapping_is_described_as_review_data_not_an_ftw_field(self):
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

        result = map_extraction_to_rules(
            "filing-1",
            [NormalizedExtractionField(field_name="Carrier Policy Category", value="Medical", confidence=0.98)],
            rules=[rule],
        )

        field = result["fields"][0]
        self.assertEqual(field.status_reason, "Matched to extraction-only field rule; never sent to FT Williams.")
        self.assertIsNone(field.ftw_field)

    def test_publishing_changes_the_agent_rule_set_version(self):
        async def scenario():
            service = FieldRuleService(repositories.get_repository())
            before = await service.published_snapshot()
            approved = next(
                rule
                for rule in await service.published_rules()
                if rule.key == "schedule_a_part_i_1b_insurance_carrier_ein"
            )
            draft = await service.create_draft(
                approved.model_copy(update={"aliases": [*approved.aliases, "Version source"]}),
                actor="admin@example.com",
                reason="Test versioning",
            )
            await service.publish(draft.key, actor="admin@example.com", reason="Publish version")
            after = await service.published_snapshot()
            return before, after

        before, after = run_async(scenario())

        self.assertNotEqual(before.version, after.version)
        self.assertIn("schedule_a_part_i_1b_insurance_carrier_ein", {rule.key for rule in after.rules})

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
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    fields=[FTWilliamsComparisonField(label="Stale comparison field")],
                )
            )
            service = FieldRuleService(repo)
            approved = next(
                rule
                for rule in await service.published_rules()
                if rule.key == "schedule_a_part_i_1a_name_of_insurance_company"
            )
            draft = await service.create_draft(
                approved.model_copy(update={"aliases": [*approved.aliases, "Client custom heading"]}),
                actor="admin@example.com",
                reason="Add client alias",
            )
            await service.publish(draft.key, actor="admin@example.com", reason="Approved")
            response = await re_evaluate_filing_rules(filing.id, {"email": "admin@example.com"})
            jobs = await repo.list_extraction_jobs(filing.id)
            review = await repo.get_ftwilliams_review(filing.id)
            return response, await repo.list_fields(filing.id), jobs, review

        response, fields, jobs, review = run_async(scenario())

        self.assertEqual(response["status"], "re-evaluated")
        self.assertIn("schedule_a_part_i_1a_name_of_insurance_company", {field.mapped_rule_key for field in fields})
        self.assertEqual(jobs, [])
        self.assertNotIn("Stale comparison field", {field.label for field in review.fields})
        self.assertGreater(response["field_count"], 0)

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
