import unittest

from app.models import (
    FieldPriority,
    FieldRule,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
)
from app.services.extractor import merge_schedule_a_broker_rows
from app.services.schedule_a_semantic_layer import (
    SemanticDocument,
    enrich_schedule_a_result,
)
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


class ScheduleASemanticLayerTests(unittest.TestCase):
    def test_principal_breakdown_uses_total_not_employee_count(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    "ApproximateNumberof Total(e) 470\n"
                    "Persons CoveredatEnd Employees 273\n"
                    "ofPolicyYear Dependents 197",
                )
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="273",
                    confidence=0.98,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])

        self.assertEqual(enriched.fields[0].value, "470")
        self.assertIn("Total(e) 470", enriched.fields[0].source_text)

    def test_eyemed_table_uses_subscribers_and_dependents_total(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    """
Payments Received by carrier from plan or plan sponsor:
Name of Plan        Contract or ID #     subscribers covered at end of policy year:     subscribers and dependents covered at end of policy year:     EIN     NAIC     Amount
EXAMPLE PLAN        10545381001          104                                             171                                                        430949844 71870    $6,611.92
                                                         104                                             171                                         Total: $6,611.92
""",
                )
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="104",
                    confidence=0.98,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])

        field = enriched.fields[0]
        self.assertEqual(field.value, "171")
        self.assertEqual(field.page, 1)
        self.assertIn("subscribers and dependents", field.source_text.lower())
        self.assertEqual(field.candidate_values, ["171"])
        self.assertEqual(
            enriched.raw["semantic_resolution"]["corrections"][0]["reason"],
            "explicit_total_persons_covered",
        )
        resolved = resolve_schedule_a_result(enriched)
        self.assertEqual(resolved.fields[0].decision, "AUTOMATIC")

    def test_enrollment_fraction_uses_total_not_subscriber_component(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    3,
                    """
Part III Welfare Benefit Contract Information
Non Experience Rated Contracts:
Type   Benefit       10a. Premium       Approximate Enrollment (Subscribers/Members)
a/k    Health PPO    $3,170,109          175/205
""",
                )
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="175",
                    confidence=0.98,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])

        self.assertEqual(enriched.fields[0].value, "205")
        self.assertEqual(enriched.fields[0].page, 3)
        self.assertIn("175/205", enriched.fields[0].source_text)
        resolved = resolve_schedule_a_result(enriched)
        self.assertEqual(resolved.fields[0].decision, "AUTOMATIC")

    def test_explicit_total_adds_missing_persons_field_from_published_rule(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    3,
                    "Part III\n10a. Premium Approximate Enrollment (Subscribers/Members)\n"
                    "Health PPO $3,170,109 175/205",
                )
            ]
        )
        rule = FieldRule(
            key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
            label="1e. Persons Covered (End of Policy Year)",
            ftw_field="Persons Covered",
            priority=FieldPriority.HIGH,
            source="Schedule A",
            field_type="Integer",
            validators=["integer"],
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )

        self.assertEqual(len(enriched.fields), 1)
        self.assertEqual(enriched.fields[0].value, "205")
        self.assertEqual(enriched.fields[0].page, 3)

    def test_carrier_payment_total_adds_line_10a_with_section_evidence(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    "Payments Received by carrier from plan or plan sponsor:\n"
                    "Name of Plan Contract ID Subscribers Total Covered EIN NAIC Amount\n"
                    "EXAMPLE PLAN 10001 104 171 430949844 71870 $6,611.92\n"
                    "104 171 Total: $6,611.92",
                )
            ]
        )
        rule = FieldRule(
            key="schedule_a_part_iii_10a_total_premiums",
            label="10a. Total premiums or subscription charges paid to carrier",
            ftw_field="Total Premiums",
            priority=FieldPriority.HIGH,
            source="Schedule A",
            field_type="Currency",
            validators=["currency"],
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )

        field = enriched.fields[0]
        self.assertEqual(field.value, "6,611.92")
        self.assertEqual(field.page, 1)
        self.assertIn("Payments Received by carrier", field.source_text)
        resolved = resolve_schedule_a_result(enriched, rules=[rule])
        self.assertEqual(resolved.fields[0].decision, "AUTOMATIC")

    def test_benefit_table_adds_single_nonexperience_premium(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    3,
                    "Part III Welfare Benefit Contract Information\n"
                    "Non Experience Rated Contracts:\n"
                    "Type Benefit 10a. Premium Approximate Enrollment (Subscribers/Members)\n"
                    "a/k Health PPO $3,170,109 175/205",
                )
            ]
        )
        rule = FieldRule(
            key="schedule_a_part_iii_10a_total_premiums",
            label="10a. Total premiums or subscription charges paid to carrier",
            ftw_field="Total Premiums",
            priority=FieldPriority.HIGH,
            source="Schedule A",
            field_type="Currency",
            validators=["currency"],
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )

        premium = next(field for field in enriched.fields if field.field_name.startswith("10a."))
        self.assertEqual(premium.value, "3,170,109")
        self.assertEqual(premium.page, 3)

    def test_combined_commission_fee_column_forces_both_fields_to_review(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    "Insurance Fees and Commissions Paid to Agents and Brokers:\n"
                    "Agent or Broker                 Commissions/Fees\n"
                    "NFP CORPORATE SERVICES LLC      $613.34",
                )
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="3b. Amount of Commissions", value="613.34", confidence=0.98
                ),
                NormalizedExtractionField(
                    field_name="3c. Amount of Fees", value="613.34", confidence=0.98
                ),
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])
        resolved = resolve_schedule_a_result(enriched)

        self.assertIn(
            "combined_commission_fee_source",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        self.assertTrue(all(field.decision == "REVIEW_REQUIRED" for field in resolved.fields))

    def test_multiple_schedule_a_groups_are_not_collapsed(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    1,
                    """
PART I
(a) Name of Insurance Carrier: Altus Dental Insurance Company, Inc.
(d) Contract or Identification Number 1303_2
(e) Approximate Number of Persons Covered at End of Policy Year 2
Policy or Contract Year (f) From 01/01/2025 (g) To 12/31/2025
""",
                ),
                (
                    2,
                    """
PART I
(a) Name of Insurance Carrier: Altus Dental Insurance Company, Inc.
(d) Contract or Identification Number 1303_1
(e) Approximate Number of Persons Covered at End of Policy Year 158
Policy or Contract Year (f) From 01/01/2025 (g) To 12/31/2025
""",
                ),
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="158",
                    confidence=0.98,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])

        field = enriched.fields[0]
        self.assertEqual(field.value, "158")
        self.assertEqual(set(field.candidate_values), {"2", "158"})
        self.assertEqual(enriched.raw["semantic_resolution"]["group_count"], 2)
        self.assertEqual(enriched.raw["semantic_resolution"]["decision"], "REVIEW_REQUIRED")

        resolved = resolve_schedule_a_result(enriched)
        self.assertIn(
            "multiple_schedule_a_policy_groups",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        self.assertEqual(resolved.fields[0].decision, "REVIEW_REQUIRED")

    def test_dynamic_alias_attaches_page_level_source_evidence(self):
        rule = FieldRule(
            key="schedule_a_risk_pool_charge",
            label="Risk Pool Charge",
            ftw_field="Risk Pool Charge",
            priority=FieldPriority.MEDIUM,
            source="Schedule A",
            field_type="Currency",
            aliases=["Carrier Risk Assessment"],
            validators=["currency"],
        )
        document = SemanticDocument.from_page_texts(
            [(4, "Part III Nonexperience-Rated Contracts\nCarrier Risk Assessment: $123.45")]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[
                NormalizedExtractionField(
                    field_name="Risk Pool Charge",
                    value="123.45",
                    confidence=0.96,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[rule])

        field = enriched.fields[0]
        self.assertEqual(field.page, 4)
        self.assertIn("Carrier Risk Assessment", field.source_text)
        self.assertTrue(field.evidence)
        self.assertEqual(field.evidence[0].page, 4)

    def test_dynamic_alias_adds_missing_published_field(self):
        rule = FieldRule(
            key="schedule_a_risk_pool_charge",
            label="Risk Pool Charge",
            ftw_field="Risk Pool Charge",
            priority=FieldPriority.MEDIUM,
            source="Schedule A - Part III",
            field_type="Currency",
            aliases=["Carrier Risk Assessment"],
            validators=["currency"],
        )
        document = SemanticDocument.from_page_texts(
            [(4, "Part III Nonexperience-Rated Contracts\nCarrier Risk Assessment: $123.45")]
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )

        self.assertEqual(len(enriched.fields), 1)
        self.assertEqual(enriched.fields[0].field_name, "Risk Pool Charge")
        self.assertEqual(enriched.fields[0].value, "123.45")
        self.assertEqual(enriched.fields[0].page, 4)
        self.assertIn("Carrier Risk Assessment", enriched.fields[0].source_text)

    def test_conflicting_alias_values_are_created_for_review_not_guessed(self):
        rule = FieldRule(
            key="schedule_a_risk_pool_charge",
            label="Risk Pool Charge",
            ftw_field="Risk Pool Charge",
            priority=FieldPriority.MEDIUM,
            source="Schedule A - Part III",
            field_type="Currency",
            aliases=["Carrier Risk Assessment"],
            validators=["currency"],
        )
        document = SemanticDocument.from_page_texts(
            [
                (1, "Carrier Risk Assessment: $123.45"),
                (2, "Carrier Risk Assessment: $999.00"),
            ]
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )
        resolved = resolve_schedule_a_result(enriched, rules=[rule])

        self.assertEqual(set(enriched.fields[0].candidate_values), {"123.45", "999.00"})
        self.assertEqual(resolved.fields[0].decision, "REVIEW_REQUIRED")

    def test_placeholder_alias_value_is_not_extracted(self):
        rule = FieldRule(
            key="schedule_a_part_i_1e_persons_covered",
            label="1e. Persons Covered (End of Policy Year)",
            ftw_field="Persons Covered",
            priority=FieldPriority.HIGH,
            source="Schedule A - Part I",
            field_type="Dynamic",
            aliases=["Number of Persons Covered"],
        )
        document = SemanticDocument.from_page_texts(
            [(1, "Number of Persons Covered: To be provided by Plan Administrator")]
        )

        enriched = enrich_schedule_a_result(
            NormalizedExtractionResult(provider="EyeLevel", fields=[]),
            document,
            rules=[rule],
        )

        self.assertEqual(enriched.fields, [])

    def test_broker_evidence_requires_name_and_amount_in_same_region(self):
        document = SemanticDocument.from_page_texts(
            [
                (
                    2,
                    """
Insurance Fee and Commission information
Broker                         Commission Paid        Fees Paid
NFP CORPORATE SERVICES LLC     $1,713.62              $42.70
""",
                )
            ]
        )
        result = NormalizedExtractionResult(
            provider="EyeLevel",
            fields=[],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(
                    name="NFP CORPORATE SERVICES LLC",
                    commission_total="1,713.62",
                    fee_total="42.70",
                    confidence=0.97,
                )
            ],
        )

        enriched = enrich_schedule_a_result(result, document, rules=[])

        row = enriched.schedule_a_broker_rows[0]
        self.assertEqual(row.source_page, 2)
        self.assertTrue(row.evidence)
        self.assertIn("NFP CORPORATE SERVICES", row.evidence[0].source_text)

    def test_broker_merge_prefers_source_evidence_over_provider_confidence(self):
        provider_row = ScheduleABrokerRow(
            name="NFP CORPORATE SERVICES LLC",
            address_line_1="PO BOX 100",
            zip_code="10001",
            commission_total="100",
            fee_total="0",
            confidence=0.99,
        )
        source_row = ScheduleABrokerRow(
            name="NFP CORPORATE SERVICES LLC",
            address_line_1="PO BOX 100",
            zip_code="10001",
            commission_total="100",
            fee_total="0",
            confidence=0.92,
            source_page=2,
            evidence=[
                SourceEvidence(
                    provider="Local layout parser",
                    page=2,
                    source_text="NFP CORPORATE SERVICES LLC $100 $0",
                )
            ],
        )

        merged = merge_schedule_a_broker_rows([provider_row], [source_row])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_page, 2)
        self.assertEqual(merged[0].evidence[0].page, 2)

    def test_broker_merge_deduplicates_legal_suffix_split_into_address(self):
        provider_row = ScheduleABrokerRow(
            name="EMERSON ROGERS LLC",
            address_line_1="5200 N PALM AVE #114",
            city="FRESNO",
            state="CA",
            zip_code="93704",
            fee_rows=[
                ScheduleABrokerMoneyRow(
                    amount="30,270",
                    purpose="incentives, education, communication and training",
                )
            ],
            commission_total="0",
            fee_total="30270",
            confidence=0.98,
        )
        source_row = ScheduleABrokerRow(
            name="EMERSON ROGERS",
            address_line_1="LLC - 5200 N PALM AVE #114",
            city="FRESNO",
            state="CA",
            zip_code="93704",
            commission_total="0.00",
            fee_total="30,270.00",
            source_page=3,
            evidence=[
                SourceEvidence(
                    provider="Local layout parser",
                    page=3,
                    source_text="Broker EMERSON ROGERS LLC 5200 N PALM AVE #114 $30,270.00",
                )
            ],
        )

        merged = merge_schedule_a_broker_rows([provider_row], [source_row])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "EMERSON ROGERS LLC")
        self.assertEqual(merged[0].address_line_1, "5200 N PALM AVE #114")
        self.assertEqual(merged[0].organization_code, "3")
        self.assertEqual(merged[0].fee_rows[0].purpose, "incentives, education, communication and training")
        self.assertEqual(merged[0].source_page, 3)
        self.assertTrue(merged[0].evidence)

    def test_broker_merge_discards_parser_fragment_for_same_broker(self):
        complete_row = ScheduleABrokerRow(
            name="HUB INTERNATIONAL TEXAS INC",
            address_line_1="3221 COLLINSWORTH ST",
            city="FORT WORTH",
            state="TX",
            zip_code="76107-5739",
            commission_total="4616",
            fee_total="44",
            source_page=4,
            evidence=[
                SourceEvidence(
                    provider="GroundX structured extract",
                    page=4,
                    source_text="Name: HUB INTERNATIONAL TEXAS INC Address: 3221 COLLINSWORTH ST City: FORT WORTH ST: TX ZIP: 76107-5739",
                )
            ],
        )
        parser_fragment = ScheduleABrokerRow(
            name="HUB INTERNATIONAL TEXAS INC",
            city="FORT WORTH ST: TX ZIP: 76107-5739",
            fee_total="3",
            source_page=4,
            evidence=[
                SourceEvidence(
                    provider="Schedule A semantic layer",
                    page=4,
                    source_text="Name: HUB INTERNATIONAL TEXAS INC Address: 3221 COLLINSWORTH ST City: FORT WORTH ST: TX ZIP: 76107-5739",
                )
            ],
        )

        merged = merge_schedule_a_broker_rows([complete_row, parser_fragment], [])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].city, "FORT WORTH")
        self.assertEqual(merged[0].state, "TX")
        self.assertEqual(merged[0].zip_code, "76107-5739")
        self.assertEqual(merged[0].fee_total, "44")


if __name__ == "__main__":
    unittest.main()
