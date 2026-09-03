import unittest

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import FieldRule, FieldRuleMappingMode, FormType, NormalizedExtractionField, NormalizedExtractionResult
from app.services.extractor import ExtractionService, parse_plan_worksheet_text


class PlanWorksheetExtractionTests(unittest.TestCase):
    def test_published_extraction_only_rule_can_capture_a_custom_worksheet_field(self):
        rule = FieldRule(
            key="custom_filing_signer_email",
            label="Filing Signer Email",
            ftw_field="",
            xml_tag=None,
            mapping_mode=FieldRuleMappingMode.EXTRACTION_ONLY,
            priority="MEDIUM",
            source="Form 5500",
            form_section="Form 5500 - Custom",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["E-mail address of filing signer"],
        )

        fields = parse_plan_worksheet_text(
            "E-mail address of filing signer: signer@example.com",
            rules=[rule],
        )

        by_name = {field.field_name: field.value for field in fields}
        self.assertEqual(by_name["Filing Signer Email"], "signer@example.com")

    def test_fixed_signer_label_is_not_exposed_as_plan_administrator(self):
        text = """
        Form 5500 Information:
        Plan sponsor name MIDWEST HOSE & SPECIALTY INC.
        Plan sponsor address 3312 S I 35 SERVICE ROAD OKLAHOMA CITY OK 73129
        Plan sponsor phone number (405) 670-6718
        EIN 73-1185740
        Business code 326100
        Plan number(s) 501
        Plan name(s) MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN
        Plan year begin / end 10-01-2024 09-30-2025
        Original ERISA plan effective date 10-01-2011
        Is the plan collectively bargained? No
        Individual signing as plan administrator CHRISTINE CATALDO
        E-mail address of filing signer ccataldo@midwesthose.com
        If the plan provides welfare benefits, enter the applicable codes from the List of Plan Characteristics Codes in the instructions:
        4A 4B 4D 4E 4F 4H 4Q
        Fully-Insured Benefits
        """

        fields = parse_plan_worksheet_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertNotIn("2a. Plan Administrator Name", by_name)
        self.assertEqual(by_name["8c. Welfare Benefit Features"], "4A 4B 4D 4E 4F 4H 4Q")

    def test_ohio_valley_layout_extracts_every_present_canonical_worksheet_value(self):
        text = """
        Form 5500 Information:
        Plan sponsor name OHIO VALLEY STAMPING & ASSEMBLIES INC.
        Plan sponsor address 515 NEWMAN ST MASFIELD OH 44902
        Plan sponsor phone number (419) 755-5464
        EIN 34-1655024
        Business code 336370
        Plan number(s) 501
        Plan name(s) OHIO VALLEY STAMPING & ASSEMBLIES INC. HARTFORD PLAN
        Plan year begin / end 01-01-2025 12-31-2025
        Original ERISA plan effective date 01-01-2024
        Is the plan collectively bargained? No
        Individual signing as plan administrator MICHAEL HAMILTON
        E-mail address of filing signer michael@hamiltonins.net
        5500 Contact / email address Angela Woouchuk awoochuk@enmanstamping.com
        Participant Counts:
        5 Total number of participants at the beginning of the plan year
        [used previous year's count from 6(d)] 137
        6(a)(1) Total number of active participants on the first day of the plan year
        [use previous year's count from 6(a)(2)] 137
        6(a)(2) Total number of active participants on the last day of the plan year 143
        6(b) Total number of retired or COBRA participants on benefits as of last day of the plan year 0
        6(c) Total number of retired or COBRA participants entitled to benefits as of last day of the plan year 0
        Benefits offered / Funding arrangement / Schedule A info:
        Fully-Insured Benefits
        LIFE; LTD; AD&D HARTFORD LIFE AND ACCIDENT 922520G 01-01-2025 12-31-2025
        Self-Funded Benefits
        Health
        """

        fields = parse_plan_worksheet_text(text)
        by_name = {field.field_name: field.value for field in fields}

        expected = {
            "1a. Plan Name": "OHIO VALLEY STAMPING & ASSEMBLIES INC. HARTFORD PLAN",
            "1b. Plan Number (PN)": "501",
            "1c. Plan Effective Date": "01-01-2024",
            "1d. Plan Sponsor Name": "OHIO VALLEY STAMPING & ASSEMBLIES INC.",
            "1e. Plan Sponsor EIN": "34-1655024",
            "1f. Plan Sponsor Address": "515 NEWMAN ST MASFIELD OH 44902",
            "1g. Business Code": "336370",
            "6. Plan Year Beginning Date": "01-01-2025",
            "7. Plan Year Ending Date": "12-31-2025",
            "9. Plan funding arrangement": "Insurance",
            "10a. Plan benefit arrangement": "Insurance",
            "10b. Schedules attached": "A",
            "11. Total participants at beginning of year": "137",
            "12. Total participants at end of year": "143",
            "13. Active participants at beginning": "137",
            "14. Active participants at end": "143",
            "15. Retired/separated participants receiving benefits": "0",
            "16. Other retired/separated participants entitled to benefits": "0",
        }
        self.assertEqual({name: by_name.get(name) for name in expected}, expected)


class PlanWorksheetFallbackTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.extractor.extract_pdf_text_pages", return_value=[])
    @patch(
        "app.services.extractor.get_settings",
        return_value=SimpleNamespace(groundx_api_key="test-key", groundx_bucket_id="test-bucket"),
    )
    async def test_scanned_pdf_uses_groundx_ocr_fallback_for_canonical_fields(self, _settings, _pages):
        service = ExtractionService()
        service._extract_with_groundx = AsyncMock(
            return_value=NormalizedExtractionResult(
                provider="GroundX Plan Worksheet OCR",
                fields=[
                    NormalizedExtractionField(
                        field_name="2a. Plan Administrator Name",
                        value="MICHAEL HAMILTON",
                        confidence=0.91,
                    )
                ],
            )
        )

        result = await service.extract_plan_worksheet(b"%PDF scanned", "Plan Worksheet.pdf")

        self.assertEqual(result.fields[0].field_name, "2a. Plan Administrator Name")
        self.assertEqual(result.fields[0].value, "MICHAEL HAMILTON")
        service._extract_with_groundx.assert_awaited_once_with(
            b"%PDF scanned",
            "Plan Worksheet.pdf",
            FormType.FORM_5500,
            "Plan Worksheet",
        )


if __name__ == "__main__":
    unittest.main()
