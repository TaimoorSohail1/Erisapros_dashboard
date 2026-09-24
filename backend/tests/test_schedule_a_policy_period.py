import unittest

from app.models import NormalizedExtractionField, NormalizedExtractionResult
from app.services.extractor import extract_rules_driven_schedule_a_fields
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


class PolicyPeriodRegressionTests(unittest.TestCase):
    def test_month_period_not_letter_date(self):
        text = "March 12, 2026\nCertification March 12, 2026\nContract Year from 01/2025 - 12/2025"
        fields = extract_rules_driven_schedule_a_fields([(3, text)], rules=DEFAULT_FIELD_RULES)
        values = {f.field_name: f.value for f in fields}
        self.assertEqual(values["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(values["1g. Policy Year Ending Date"], "12/31/2025")

    def test_unrelated_dates_do_not_become_policy_dates(self):
        fields = extract_rules_driven_schedule_a_fields(
            [(1, "Letter March 12, 2026\nCertified March 12, 2026")], rules=DEFAULT_FIELD_RULES)
        self.assertFalse(any(f.field_name.startswith(("1f.", "1g.")) for f in fields))

    def test_wrong_provider_dates_corrected_and_month_precision_reviewed(self):
        result = NormalizedExtractionResult(provider="Captured provider", fields=[
            NormalizedExtractionField(field_name=label, value="03/12/2026", confidence=0.98)
            for label in ("1f. Policy Year Beginning Date", "1g. Policy Year Ending Date")])
        document = SemanticDocument.from_page_texts([(3, "Contract Year from 01/2025 - 12/2025")])
        result = resolve_schedule_a_result(enrich_schedule_a_result(result, document, rules=DEFAULT_FIELD_RULES))
        dates = [f for f in result.fields if f.field_name.startswith(("1f.", "1g."))]
        self.assertEqual([f.value for f in dates], ["01/01/2025", "12/31/2025"])
        self.assertTrue(all(f.decision == "REVIEW_REQUIRED" for f in result.fields
                            if f.field_name.startswith(("1f.", "1g."))))
        self.assertTrue(all("01/2025" in f.source_text for f in dates))

    def test_exact_contract_period_preserved(self):
        text = "Letter March 12, 2026\nContract Year from 02/17/2024 - 02/16/2025"
        fields = extract_rules_driven_schedule_a_fields([(2, text)], rules=DEFAULT_FIELD_RULES)
        values = {f.field_name: f.value for f in fields}
        self.assertEqual(values["1f. Policy Year Beginning Date"], "02/17/2024")
        self.assertEqual(values["1g. Policy Year Ending Date"], "02/16/2025")

    def test_conflicting_contract_periods_force_review(self):
        result = NormalizedExtractionResult(provider="Provider", fields=[
            NormalizedExtractionField(field_name="1f. Policy Year Beginning Date", value="01/01/2025", confidence=0.98),
            NormalizedExtractionField(field_name="1g. Policy Year Ending Date", value="12/31/2025", confidence=0.98)])
        text = "Contract Year from 01/01/2025 - 12/31/2025\nContract Year from 02/01/2025 - 01/31/2026"
        result = resolve_schedule_a_result(enrich_schedule_a_result(result, SemanticDocument.from_page_texts([(1, text)]), rules=[]))
        self.assertTrue(all(f.decision == "REVIEW_REQUIRED" for f in result.fields
                            if f.field_name.startswith(("1f.", "1g."))))

    def test_invalid_month_is_not_accepted_or_crashed(self):
        fields = extract_rules_driven_schedule_a_fields([(1, "Contract Year from 13/2025 - 14/2025")], rules=DEFAULT_FIELD_RULES)
        self.assertFalse(any(f.field_name.startswith(("1f.", "1g.")) for f in fields))
