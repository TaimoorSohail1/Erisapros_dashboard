import unittest

from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
from app.services.extractor import merge_schedule_a_broker_rows
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
from app.services.xml_builder import schedule_a_broker_update_values


class ScheduleACustomerRulesTests(unittest.TestCase):
    def enrich(self, text, value="17"):
        return enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[NormalizedExtractionField(
                field_name="1e. Persons Covered (End of Policy Year)", value=value, confidence=0.98)]),
            SemanticDocument.from_page_texts([(1, text)]), rules=[])

    def test_all_blank_recipient_codes_default_without_broker_label(self):
        for blank in (None, "", " \t "):
            with self.subTest(blank=blank):
                row = ScheduleABrokerRow(name="Example Recipient", organization_code=blank,
                                         commission_total="12", source_page=1)
                merged = merge_schedule_a_broker_rows([row], [])
                self.assertEqual(merged[0].organization_code, "3")
                self.assertTrue(merged[0].organization_code_defaulted)
                self.assertEqual(row.organization_code, blank, "Do not mutate input snapshots")

    def test_review_normalizes_existing_and_edited_rows_but_not_explicit_codes(self):
        service = FTWilliamsReviewService()
        for code in (None, "", " ", "0", "03", "6", "ABC"):
            with self.subTest(code=code):
                result = service._normalized_schedule_a_broker_rows([
                    {"name": "Recipient", "organization_code": code}])[0]
                self.assertEqual(result.organization_code, "3" if not str(code or "").strip() else code)
                self.assertEqual(result.organization_code_defaulted, not bool(str(code or "").strip()))

    def test_raw_payload_defaults_blank_codes_and_preserves_zero(self):
        for code in (None, "", " ", "0", 0, "6"):
            with self.subTest(code=code):
                values = schedule_a_broker_update_values([{"name": "Recipient", "organization_code": code}])
                self.assertEqual(values["Code1"], "3" if code is None or str(code).strip() == "" else str(code))
        self.assertEqual(schedule_a_broker_update_values([None]), {})

    def test_scalar_code_default_records_rule_provenance_not_ocr_evidence(self):
        result = resolve_schedule_a_result(NormalizedExtractionResult(provider="EyeLevel", fields=[
            NormalizedExtractionField(field_name="3e. Organizational Code", value=" ", confidence=0)]))
        self.assertEqual(result.fields[0].value, "3")
        self.assertEqual(result.fields[0].evidence[0].provider, "Customer configured default")
        self.assertIn("Defaulted", result.fields[0].source_text)
        self.assertIsNone(result.fields[0].evidence[0].page)
        self.assertEqual(result.fields[0].decision, "AUTOMATIC", "A deterministic customer rule is not an OCR guess")

    def test_rule_provenance_does_not_bypass_other_fields_source_requirements(self):
        from app.models import SourceEvidence
        from app.services.schedule_a_customer_rules import DEFAULT_CODE_REASON

        result = resolve_schedule_a_result(NormalizedExtractionResult(provider="EyeLevel", fields=[
            NormalizedExtractionField(field_name="1e. Persons Covered", value="3", confidence=1,
                source_text=DEFAULT_CODE_REASON,
                evidence=[SourceEvidence(provider="Customer configured default", source_text=DEFAULT_CODE_REASON)])]))
        self.assertEqual(result.fields[0].decision, "REVIEW_REQUIRED")

    def test_wrapped_lives_column_selects_maximum_not_currency_or_ids(self):
        text = ("Policy Number  Type of Benefit  Premium Applied  Approximate # of Lives\n"
                "                                                 Covered\n"
                "803154G        ACC-VOL          $3,358.08         17\n"
                "803154G        ADD-BAS          $5,517.41         208\n"
                "803154G        LIFE-BTRM        $54,621.99        208\n"
                "803154G        LTD-ABIL         $56,199.46        195\n"
                "               Total           $161,956.30\n\nPage 2025")
        result = self.enrich(text)
        field = result.fields[0]
        self.assertEqual(field.value, "208")
        self.assertEqual(field.candidate_values, ["208"])
        self.assertIn("Highest", field.source_text)
        self.assertEqual(result.raw["semantic_resolution"]["corrections"][0]["reason"],
                         "highest_lives_covered_column")
        self.assertEqual(resolve_schedule_a_result(result).fields[0].decision, "AUTOMATIC")

    def test_customer_keyword_columns_ignore_adjacent_money(self):
        for label in ("Persons Covered", "Group Covered", "Lives", "Employee Count", "Subscribers/ Members"):
            with self.subTest(label=label):
                text = (f"Benefit  {label:<23}  Premium\n"
                        f"Life     {'1,208':<23}  $92,000.00\n"
                        f"Dental   {'1,100':<23}  $99,000.00\n\nPage 2025")
                result = self.enrich(text)
                self.assertEqual(result.fields[0].value, "1208")
                self.assertEqual(resolve_schedule_a_result(result).fields[0].decision, "AUTOMATIC")

    def test_different_contracts_do_not_get_one_global_maximum(self):
        result = self.enrich("Policy Number  Lives  Premium\n"
                             "POL-A          100    $900.00\n"
                             "POL-B          200    $800.00", value="100")
        self.assertEqual(result.fields[0].value, "100")
        self.assertEqual(result.raw["semantic_resolution"]["decision"], "REVIEW_REQUIRED")
        self.assertEqual(resolve_schedule_a_result(result).fields[0].decision, "REVIEW_REQUIRED")

    def test_no_count_is_not_invented_and_zero_is_valid(self):
        for cell in ("$900", "1.25", "01/01/2025", "N/A"):
            with self.subTest(cell=cell):
                result = self.enrich(f"Benefit  Lives  Premium\nLife     {cell}  $800", value="")
                self.assertEqual(result.fields[0].value, "")
        self.assertEqual(self.enrich("Benefit  Lives  Premium\nLife     0      $800").fields[0].value, "0")

    def test_unaligned_ocr_does_not_guess_from_nearby_money(self):
        result = self.enrich("Approximate # of Lives Covered\n803154G LIFE $56199.46 195", value="17")
        self.assertEqual(result.fields[0].value, "17")
        self.assertEqual(result.fields[0].decision, "REVIEW_REQUIRED")

    def test_unreadable_first_row_must_not_claim_readable_subset_maximum(self):
        result = self.enrich("Benefit  Lives  Premium\nLife     N/A    $800\nDental   50     $700", value="17")
        self.assertEqual(result.fields[0].value, "17")
        self.assertEqual(resolve_schedule_a_result(result).fields[0].decision, "REVIEW_REQUIRED")

    def test_fully_unreadable_column_requires_review_of_provider_count(self):
        result = self.enrich("Benefit  Lives  Premium\nLife     N/A    $800", value="17")
        self.assertEqual(result.fields[0].value, "17")
        self.assertEqual(result.fields[0].decision, "REVIEW_REQUIRED")
        self.assertEqual(result.raw["semantic_resolution"]["decision"], "REVIEW_REQUIRED")

    def test_same_verified_policy_continued_table_uses_maximum_across_pages(self):
        document = SemanticDocument.from_page_texts([
            (1, "Policy Number  Lives  Premium\nPOL-A          100    $900"),
            (2, "Policy Number  Lives  Premium\nPOL-A          208    $800")])
        result = enrich_schedule_a_result(NormalizedExtractionResult(provider="EyeLevel", fields=[
            NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)",
                                      value="100", confidence=0.98)]), document, rules=[])
        self.assertEqual(result.fields[0].value, "208")
        self.assertEqual(result.fields[0].page, 2)
        self.assertEqual(resolve_schedule_a_result(result).fields[0].decision, "AUTOMATIC")

    def test_new_lives_field_does_not_remove_provider_failure_review_hold(self):
        from app.services.extractor import DEFAULT_FIELD_RULES

        result = enrich_schedule_a_result(NormalizedExtractionResult(
            provider="Local fallback - manual review required", fields=[],
            raw={"manual_review_required": True, "fallback_reason": "HTTP 402"}),
            SemanticDocument.from_page_texts([(1, "Benefit  Lives  Premium\nLife     208    $800")]),
            rules=DEFAULT_FIELD_RULES)
        field = next(field for field in result.fields if field.field_name.startswith("1e."))
        self.assertEqual(field.value, "208", "Keep useful source-backed values visible")
        self.assertLessEqual(field.confidence, 0.5)
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertEqual(result.raw["semantic_resolution"]["decision"], "REVIEW_REQUIRED")
