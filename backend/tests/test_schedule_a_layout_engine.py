import unittest

from app.services.schedule_a_layout_engine import (
    LayoutPage,
    LayoutWord,
    extract_layout_aware_schedule_a_fields_from_pages,
)


def word(text: str, x0: float, top: float, x1: float, bottom: float) -> LayoutWord:
    return LayoutWord(text=text, x0=x0, top=top, x1=x1, bottom=bottom)


class ScheduleALayoutEngineTests(unittest.TestCase):
    def test_reads_each_value_from_its_own_table_column(self):
        page = LayoutPage(
            number=2,
            width=612,
            height=792,
            words=[
                word("a.Name", 22, 210, 52, 219),
                word("of", 54, 210, 61, 219),
                word("Carrier(s)", 63, 210, 105, 219),
                word("b.EIN", 292, 210, 316, 219),
                word("c.NAIC", 350, 210, 376, 219),
                word("Code", 350, 220, 374, 229),
                word("Coverages", 385, 220, 430, 229),
                word("Anthem", 22, 245, 53, 254),
                word("Blue", 55, 245, 73, 254),
                word("Cross", 75, 245, 99, 254),
                word("Life", 101, 245, 116, 254),
                word("and", 118, 245, 133, 254),
                word("Health", 135, 245, 160, 254),
                word("Insurance", 162, 245, 201, 254),
                word("Company", 203, 245, 242, 254),
                word("(G0360)", 244, 245, 276, 254),
                word("95-4331852", 292, 245, 339, 254),
                word("62825", 350, 245, 375, 254),
                word("VISION", 385, 245, 416, 254),
            ],
        )

        fields = {
            field.field_name: field
            for field in extract_layout_aware_schedule_a_fields_from_pages([page])
        }

        self.assertEqual(fields["1a. Name of Insurance Company"].value, "Anthem Blue Cross Life and Health Insurance Company (G0360)")
        self.assertEqual(fields["1b. Insurance Carrier EIN"].value, "95-4331852")
        self.assertEqual(fields["1c. NAIC Code"].value, "62825")
        self.assertEqual(fields["1a. Name of Insurance Company"].evidence[0].bounding_box, (22.0, 245.0, 276.0, 254.0))
        self.assertEqual(fields["1a. Name of Insurance Company"].evidence[0].table_cell, (1, 0))

    def test_rejects_another_header_as_a_field_value(self):
        page = LayoutPage(
            number=1,
            width=612,
            height=792,
            words=[
                word("Name", 20, 100, 45, 110),
                word("of", 47, 100, 55, 110),
                word("Insurance", 57, 100, 95, 110),
                word("Company", 97, 100, 135, 110),
                word("EIN", 20, 120, 38, 130),
                word("(Insurance", 40, 120, 82, 130),
                word("Carrier)", 84, 120, 115, 130),
            ],
        )

        fields = extract_layout_aware_schedule_a_fields_from_pages([page])

        self.assertNotIn("1a. Name of Insurance Company", {field.field_name for field in fields})

    def test_same_line_label_value_is_supported(self):
        page = LayoutPage(
            number=1,
            width=612,
            height=792,
            words=[
                word("NAIC", 20, 100, 45, 110),
                word("Code:", 47, 100, 72, 110),
                word("61271", 95, 100, 125, 110),
            ],
        )

        fields = extract_layout_aware_schedule_a_fields_from_pages([page])

        self.assertEqual({field.field_name: field.value for field in fields}, {"1c. NAIC Code": "61271"})

    def test_generic_column_heading_is_not_accepted_as_a_carrier(self):
        page = LayoutPage(
            number=1,
            width=612,
            height=792,
            words=[
                word("Name", 20, 100, 45, 110),
                word("of", 47, 100, 55, 110),
                word("Carrier", 57, 100, 90, 110),
                word("INSURANCE", 20, 120, 70, 130),
                word("COMPANY", 72, 120, 115, 130),
            ],
        )

        fields = extract_layout_aware_schedule_a_fields_from_pages([page])

        self.assertNotIn("1a. Name of Insurance Company", {field.field_name for field in fields})

    def test_multiline_carrier_and_second_line_identifiers_stay_in_their_columns(self):
        page = LayoutPage(
            number=2,
            width=612,
            height=792,
            words=[
                word("Name", 20, 100, 45, 110),
                word("of", 47, 100, 55, 110),
                word("insurance", 57, 100, 95, 110),
                word("carrier", 97, 100, 130, 110),
                word("EIN", 250, 100, 270, 110),
                word("NAIC", 330, 100, 355, 110),
                word("code", 357, 100, 380, 110),
                word("Sun", 20, 120, 38, 130),
                word("Life", 40, 120, 58, 130),
                word("Assurance", 60, 120, 105, 130),
                word("Company", 107, 120, 150, 130),
                word("of", 152, 120, 160, 130),
                word("Canada", 20, 132, 52, 142),
                word("38-1082080", 250, 132, 300, 142),
                word("80802", 330, 132, 355, 142),
            ],
        )

        fields = {
            field.field_name: field.value
            for field in extract_layout_aware_schedule_a_fields_from_pages([page])
        }

        self.assertEqual(fields["1a. Name of Insurance Company"], "Sun Life Assurance Company of Canada")
        self.assertEqual(fields["1b. Insurance Carrier EIN"], "38-1082080")
        self.assertEqual(fields["1c. NAIC Code"], "80802")

    def test_multiple_carrier_rows_are_preserved_as_review_candidates(self):
        page = LayoutPage(
            number=2,
            width=612,
            height=792,
            words=[
                word("Name", 20, 100, 45, 110),
                word("of", 47, 100, 55, 110),
                word("Carrier", 57, 100, 90, 110),
                word("EIN", 250, 100, 270, 110),
                word("NAIC", 330, 100, 355, 110),
                word("First", 20, 120, 42, 130),
                word("Insurance", 44, 120, 82, 130),
                word("Company", 84, 120, 125, 130),
                word("11-1111111", 250, 120, 300, 130),
                word("11111", 330, 120, 355, 130),
                word("Second", 20, 138, 52, 148),
                word("Insurance", 54, 138, 92, 148),
                word("Company", 94, 138, 135, 148),
                word("22-2222222", 250, 138, 300, 148),
                word("22222", 330, 138, 355, 148),
            ],
        )

        fields = {
            field.field_name: field
            for field in extract_layout_aware_schedule_a_fields_from_pages([page])
        }

        carrier = fields["1a. Name of Insurance Company"]
        self.assertEqual(carrier.candidate_values, ["First Insurance Company", "Second Insurance Company"])
        self.assertEqual(carrier.decision, "REVIEW_REQUIRED")
        self.assertEqual(carrier.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
