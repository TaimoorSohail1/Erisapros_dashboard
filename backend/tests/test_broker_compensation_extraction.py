"""The broker compensation table is where Schedule A items 3a-3d live.

Measured against the 29 filings in production, the broker name was captured on
45% of them and the amounts on barely 30% - even when the carrier statement
printed them plainly. The amounts are the part that matters: item 3b is
commissions, 3c is fees, and both are dynamic fields that get written to
FT Williams.

The layouts below are the shapes real carrier statements use, reproduced from
the documents that were failing.
"""
import unittest

from app.services.extractor import (
    extract_commission_fee_total_fields,
    extract_compensation_table_broker_rows,
    schedule_a_broker_compensation_fields,
)


def _fields_by_name(fields):
    return {field.field_name: field.value for field in fields}


# New York Life / Life Insurance Company of North America - "Annual Policy
# Information Report". The amounts sit under the address block.
NEW_YORK_LIFE_PAGE = """
Annual Policy Information Report
Name of Insurance Carrier
Life Insurance Company of North America
EIN 23-1503749
NAIC Code 65498
Contract/Policy Number FLX0966853
Total premiums paid to Insurance Company during the policy year: $ 38,623.58
See below for total commissions and fees paid by Insurance Company during the policy year.
Agent
Number
Name and Address of Each Recipient of
Fees and/or Commissions
Amount of
Commissions Paid
Amount of
Fees Paid
Purpose for
Which Paid
CGI-026821Nth Insurance Agency dba: Alliance 360 I
10833 VALLEY VIEW STREET
SUITE 550
CYPRESS CA 90630
$5,810.59 $ 0.00 Standard Commissions
$
$
"""

# Two recipients on one statement.
TWO_RECIPIENT_PAGE = """
Name and Address of Each Recipient of Fees and/or Commissions
Amount of Commissions Paid
Amount of Fees Paid
Purpose for Which Paid
AB-100123First Brokerage LLC
1 MAIN STREET
BOSTON MA 02110
$1,000.00 $ 250.00 Standard Commissions
CD-200456Second Agency Inc
2 OTHER STREET
BOSTON MA 02110
$500.50 $ 0.00 Override
"""

# Equitable - a totals line, with recipients listed separately below.
EQUITABLE_PAGE = """
Section 2: Insurance Fee and Commissions Information
(A) Commissions Paid (B) Fees Paid
Total (from below) 6590.57 5555.33
Section 3: Persons Receiving Commissions and Fees
(A) Name & Address of
Agents or Brokers to
whom Commissions
or Fees Paid
Base Plan # Covered Total Premiums
"""

COVERING_LETTER_ONLY = """
Dear Valued Customer:
The enclosed report provides information regarding your group insurance policy,
including total premiums paid as well as compensation paid to agents or brokers
in connection with your policy. This information may include an entry showing
other compensation received by your broker, in addition to commissions.
"""


class CompensationTableTests(unittest.TestCase):
    def test_reads_the_recipient_and_both_amounts(self):
        rows = extract_compensation_table_broker_rows([(1, NEW_YORK_LIFE_PAGE)])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # The agent number is printed hard against the name and must come off.
        self.assertEqual(row.name, "Nth Insurance Agency dba: Alliance 360 I")
        self.assertEqual(row.commission_total, "5,810.59")
        self.assertEqual(row.fee_total, "0.00")
        self.assertEqual(row.address_line_1, "10833 VALLEY VIEW STREET")
        self.assertEqual(row.source_page, 1)

    def test_produces_schedule_a_items_3a_to_3d(self):
        fields = _fields_by_name(
            schedule_a_broker_compensation_fields(
                extract_compensation_table_broker_rows([(1, NEW_YORK_LIFE_PAGE)])
            )
        )
        self.assertEqual(fields["3a. Name of Agent/Broker/Person"], "Nth Insurance Agency dba: Alliance 360 I")
        self.assertEqual(fields["3b. Amount of Commissions"], "5,810.59")
        self.assertEqual(fields["3d. Purpose"], "Standard Commissions")
        self.assertIn("3c. Amount of Fees", fields)

    def test_multiple_recipients_are_totalled_and_marked_for_review(self):
        rows = extract_compensation_table_broker_rows([(1, TWO_RECIPIENT_PAGE)])
        self.assertEqual(len(rows), 2)

        fields = schedule_a_broker_compensation_fields(rows)
        by_name = _fields_by_name(fields)
        self.assertEqual(by_name["3b. Amount of Commissions"], "1,500.50")
        self.assertEqual(by_name["3c. Amount of Fees"], "250")
        # A split across recipients is a judgement call - flag it for a human.
        commissions = next(f for f in fields if f.field_name.startswith("3b."))
        self.assertLess(commissions.confidence, 0.8)

    def test_a_covering_letter_about_commissions_is_not_a_table(self):
        self.assertEqual(extract_compensation_table_broker_rows([(1, COVERING_LETTER_ONLY)]), [])

    def test_column_headings_are_never_returned_as_a_broker_name(self):
        rows = extract_compensation_table_broker_rows([(1, EQUITABLE_PAGE)])
        names = [row.name for row in rows]
        self.assertNotIn("Base Plan # Covered Total Premiums", names)


class CommissionFeeTotalTests(unittest.TestCase):
    def test_reads_a_totals_line_when_there_is_no_named_row(self):
        fields = _fields_by_name(extract_commission_fee_total_fields([(1, EQUITABLE_PAGE)]))
        self.assertEqual(fields["3b. Amount of Commissions"], "6590.57")
        self.assertEqual(fields["3c. Amount of Fees"], "5555.33")

    def test_a_totals_line_without_commission_context_is_ignored(self):
        page = """
        Participant counts
        Total 120 118
        """
        self.assertEqual(extract_commission_fee_total_fields([(1, page)]), [])


if __name__ == "__main__":
    unittest.main()
