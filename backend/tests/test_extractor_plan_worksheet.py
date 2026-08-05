import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extractor import parse_plan_worksheet_text


class PlanWorksheetExtractionTests(unittest.TestCase):
    def test_signer_name_is_not_used_as_plan_administrator_name_and_welfare_codes_are_extracted(self):
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


if __name__ == "__main__":
    unittest.main()
