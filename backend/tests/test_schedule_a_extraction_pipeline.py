import unittest

from app.models import FieldRule, NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline, resolve_schedule_a_result


class ScheduleAExtractionPipelineTests(unittest.TestCase):
    def test_missing_required_header_fields_force_review(self):
        resolved = resolve_schedule_a_result(
            NormalizedExtractionResult(provider="empty OCR result", fields=[])
        )

        quality = resolved.raw["extraction_quality"]
        self.assertEqual(quality["decision"], "REVIEW_REQUIRED")
        self.assertEqual(
            quality["missing_required_fields"],
            [
                "1a. Name of Insurance Company",
                "1b. Insurance Carrier EIN",
                "1c. NAIC Code",
                "1d. Contract/Policy Number",
                "1e. Persons Covered (End of Policy Year)",
                "1f. Policy Year Beginning Date",
                "1g. Policy Year Ending Date",
            ],
        )

    def test_invalid_core_values_are_never_treated_as_trusted_extraction(self):
        result = NormalizedExtractionResult(
            provider="test provider",
            fields=[
                NormalizedExtractionField(
                    field_name="1b. Insurance Carrier EIN",
                    value="Carrier EIN",
                    confidence=0.99,
                    page=1,
                    source_text="Carrier EIN",
                ),
                NormalizedExtractionField(
                    field_name="1c. NAIC Code",
                    value="NAIC Code",
                    confidence=0.99,
                    page=1,
                    source_text="NAIC Code",
                ),
                NormalizedExtractionField(
                    field_name="1d. Contract/Policy Number",
                    value="01/01/2025",
                    confidence=0.99,
                    page=1,
                    source_text="Contract/Policy Number 01/01/2025",
                ),
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="$12,000.00",
                    confidence=0.99,
                    page=1,
                    source_text="Total premium $12,000.00",
                ),
                NormalizedExtractionField(
                    field_name="1f. Policy Year Beginning Date",
                    value="12/31/2025",
                    confidence=0.99,
                    page=1,
                ),
                NormalizedExtractionField(
                    field_name="1g. Policy Year Ending Date",
                    value="01/01/2025",
                    confidence=0.99,
                    page=1,
                ),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertEqual(resolved.raw["extraction_quality"]["decision"], "REVIEW_REQUIRED")
        self.assertGreaterEqual(resolved.raw["extraction_quality"]["error_count"], 6)
        self.assertTrue(all(field.confidence <= 0.5 for field in resolved.fields))
        self.assertTrue(all(field.validation_results for field in resolved.fields))

    def test_valid_core_values_keep_their_confidence_and_source_evidence(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(field_name="1a. Name of Insurance Company", value="Principal Life Insurance Company", confidence=0.94, page=1, source_text="Carrier Principal Life Insurance Company"),
                NormalizedExtractionField(field_name="1b. Insurance Carrier EIN", value="42-0127290", confidence=0.94, page=1, source_text="EIN 42-0127290"),
                NormalizedExtractionField(field_name="1c. NAIC Code", value="61271", confidence=0.94, page=1, source_text="NAIC 61271"),
                NormalizedExtractionField(field_name="1d. Contract/Policy Number", value="1022824", confidence=0.94, page=1, source_text="Contract # 1022824"),
                NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)", value="470", confidence=0.94, page=1, source_text="Total 470"),
                NormalizedExtractionField(field_name="1f. Policy Year Beginning Date", value="01/01/2025", confidence=0.94, page=1, source_text="From January 1, 2025"),
                NormalizedExtractionField(field_name="1g. Policy Year Ending Date", value="12/31/2025", confidence=0.94, page=1, source_text="To December 31, 2025"),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertEqual(resolved.raw["extraction_quality"]["decision"], "AUTOMATIC")
        self.assertEqual(resolved.raw["extraction_quality"]["error_count"], 0)
        self.assertTrue(all(field.decision == "AUTOMATIC" for field in resolved.fields))
        self.assertTrue(all(field.confidence == 0.94 for field in resolved.fields))
        self.assertTrue(all(field.evidence[0].provider == "layout OCR" for field in resolved.fields))

    def test_placeholder_contract_identifier_is_never_automatic(self):
        result = NormalizedExtractionResult(
            provider="carrier report",
            fields=[
                NormalizedExtractionField(
                    field_name="1d. Contract/Policy Number",
                    value="SEE ABOVE",
                    confidence=0.99,
                    page=2,
                    source_text="Contract or policy number: SEE ABOVE",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertLessEqual(field.confidence, 0.5)
        self.assertTrue(
            any(
                item.validator == "contract_identifier" and item.status == "ERROR"
                for item in field.validation_results
            )
        )

    def test_conflicting_candidates_for_one_field_are_never_automatic(self):
        result = NormalizedExtractionResult(
            provider="structured extraction",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="104",
                    candidate_values=["104", "171"],
                    confidence=0.99,
                    page=1,
                    source_text="Subscribers 104; subscribers plus dependents 171",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(
                item.validator == "candidate_conflict" and item.status == "ERROR"
                for item in field.validation_results
            )
        )

    def test_equivalent_numeric_candidates_do_not_create_a_false_conflict(self):
        result = NormalizedExtractionResult(
            provider="combined extraction",
            fields=[
                NormalizedExtractionField(
                    field_name="3b. Amount of Commissions",
                    value="5,494.00",
                    candidate_values=["5494", "$5,494.00"],
                    confidence=0.98,
                    page=2,
                    source_text="Part I line 2 total commissions $5,494.00",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        conflict = next(
            item for item in field.validation_results if item.validator == "candidate_conflict"
        )
        self.assertEqual(conflict.status, "PASS")
        self.assertEqual(field.decision, "AUTOMATIC")

    def test_persons_covered_from_enrollment_tiers_requires_review(self):
        result = NormalizedExtractionResult(
            provider="structured extraction",
            fields=[
                NormalizedExtractionField(
                    field_name="1e. Persons Covered (End of Policy Year)",
                    value="104",
                    confidence=0.99,
                    page=1,
                    source_text="Subscribers 104; dependents 67; total covered lives 171",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(
                item.validator == "persons_covered_semantics" and item.status == "ERROR"
                for item in field.validation_results
            )
        )

    def test_valid_value_without_page_evidence_is_review_required(self):
        result = NormalizedExtractionResult(
            provider="structured extraction",
            fields=[
                NormalizedExtractionField(
                    field_name="1b. Insurance Carrier EIN",
                    value="42-0127290",
                    confidence=0.99,
                    source_text="EIN 42-0127290",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(
                item.validator == "source_evidence" and item.status == "ERROR"
                for item in field.validation_results
            )
        )

    def test_page_and_source_text_are_sufficient_field_evidence(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="1b. Insurance Carrier EIN",
                    value="42-0127290",
                    confidence=0.94,
                    page=1,
                    source_text="Insurance Carrier EIN 42-0127290",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        evidence_result = next(
            item for item in field.validation_results if item.validator == "source_evidence"
        )
        self.assertEqual(evidence_result.status, "PASS")

    def test_source_text_must_actually_support_the_extracted_value(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="1b. Insurance Carrier EIN",
                    value="42-0127290",
                    confidence=0.98,
                    page=1,
                    source_text="Insurance Carrier EIN 99-9999999",
                )
            ],
        )

        field = resolve_schedule_a_result(result).fields[0]

        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        evidence_result = next(
            item for item in field.validation_results if item.validator == "source_evidence"
        )
        self.assertEqual(evidence_result.status, "ERROR")

    def test_naic_rejects_a_policy_year_mapped_from_the_same_table_row(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="1c. NAIC Code",
                    value="2025",
                    confidence=0.98,
                    page=3,
                    source_text="NAIC Code: See Attached Listing 610 01/01/2025 12/31/2025",
                )
            ],
        )

        field = resolve_schedule_a_result(result).fields[0]

        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(any(item.validator == "naic" and item.status == "ERROR" for item in field.validation_results))

    def test_company_name_rejects_narrative_sentence_fragment(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="1a. Name of Insurance Company",
                    value="during this period is $7,816.51.",
                    confidence=0.98,
                    page=1,
                    source_text="The total premium paid to Companion Life Insurance Company during this period is $7,816.51.",
                )
            ],
        )

        field = resolve_schedule_a_result(result).fields[0]

        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(any(item.validator == "carrier_name" and item.status == "ERROR" for item in field.validation_results))

    def test_contract_identifier_rejects_a_table_column_heading(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="1d. Contract/Policy Number",
                    value="Type",
                    confidence=0.98,
                    page=1,
                    source_text="Group Number Type of Coverage Gross Premium Number of Lives",
                )
            ],
        )

        field = resolve_schedule_a_result(result).fields[0]

        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(item.validator == "contract_identifier" and item.status == "ERROR" for item in field.validation_results)
        )

    def test_broker_totals_that_do_not_reconcile_require_review(self):
        result = NormalizedExtractionResult(
            provider="table OCR",
            fields=[
                NormalizedExtractionField(field_name="3b. Amount of Commissions", value="100.00", confidence=0.95),
                NormalizedExtractionField(field_name="3c. Amount of Fees", value="20.00", confidence=0.95),
            ],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(name="Broker One", commission_total="40.00", fee_total="10.00", confidence=0.95, source_page=2),
                ScheduleABrokerRow(name="Broker Two", commission_total="50.00", fee_total="10.00", confidence=0.95),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertEqual(resolved.raw["extraction_quality"]["decision"], "REVIEW_REQUIRED")
        self.assertIn("broker_commission_total", resolved.raw["extraction_quality"]["cross_field_errors"])
        self.assertTrue(all(row.decision == "REVIEW_REQUIRED" for row in resolved.schedule_a_broker_rows))
        self.assertTrue(all(row.confidence <= 0.5 for row in resolved.schedule_a_broker_rows))
        self.assertEqual(resolved.schedule_a_broker_rows[0].evidence[0].page, 2)
        commission_field = next(
            field for field in resolved.fields if field.field_name.startswith("3b.")
        )
        self.assertEqual(commission_field.decision, "REVIEW_REQUIRED")
        self.assertLessEqual(commission_field.confidence, 0.5)
        self.assertTrue(
            any(
                item.validator == "broker_total_reconciliation" and item.status == "ERROR"
                for item in commission_field.validation_results
            )
        )

    def test_table_noise_is_not_accepted_as_a_broker_row(self):
        result = NormalizedExtractionResult(
            provider="document table parser",
            fields=[],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(
                    name="July",
                    commission_total="2,",
                    fee_total="2026",
                    confidence=0.96,
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        row = resolved.schedule_a_broker_rows[0]
        self.assertEqual(row.decision, "REVIEW_REQUIRED")
        self.assertLessEqual(row.confidence, 0.5)
        self.assertTrue(
            any(item.validator == "broker_name_semantics" and item.status == "ERROR" for item in row.validation_results)
        )
        self.assertIn("broker_row_semantics", resolved.raw["extraction_quality"]["cross_field_errors"])

    def test_broker_row_without_page_evidence_requires_review(self):
        result = NormalizedExtractionResult(
            provider="structured extraction",
            fields=[],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(
                    name="NFP Corporate Services NY LLC",
                    commission_total="100",
                    fee_total="0",
                    confidence=0.98,
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        row = resolved.schedule_a_broker_rows[0]
        self.assertEqual(row.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(
                item.validator == "source_evidence" and item.status == "ERROR"
                for item in row.validation_results
            )
        )
        self.assertIn(
            "broker_source_evidence",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )

    def test_broker_amounts_with_crossed_column_evidence_require_review(self):
        result = NormalizedExtractionResult(
            provider="table extraction",
            fields=[],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(
                    name="Example Broker LLC",
                    commission_total="200",
                    fee_total="100",
                    commission_source_text="Fees Paid $200",
                    fee_source_text="Commissions Paid $100",
                    source_page=2,
                    confidence=0.98,
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        row = resolved.schedule_a_broker_rows[0]
        self.assertEqual(row.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(
                item.validator == "broker_column_semantics" and item.status == "ERROR"
                for item in row.validation_results
            )
        )
        self.assertIn(
            "broker_column_semantics",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )

    def test_combined_compensation_amount_is_not_duplicated_as_commission_and_fee(self):
        result = NormalizedExtractionResult(
            provider="structured OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="3b. Amount of Commissions",
                    value="2,170.33",
                    confidence=0.97,
                ),
                NormalizedExtractionField(
                    field_name="3c. Amount of Fees",
                    value="2,170.33",
                    confidence=0.97,
                ),
            ],
            schedule_a_broker_rows=[
                ScheduleABrokerRow(
                    name="HUB International Texas, Inc.",
                    commission_total="2,170.33",
                    fee_total="0",
                    confidence=0.97,
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertIn(
            "ambiguous_compensation_split",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        compensation_fields = [
            field
            for field in resolved.fields
            if field.field_name.startswith(("3b.", "3c."))
        ]
        self.assertTrue(all(field.decision == "REVIEW_REQUIRED" for field in compensation_fields))
        self.assertTrue(all(field.confidence <= 0.5 for field in compensation_fields))

    def test_values_copied_from_nonexperience_and_compensation_sections_require_review(self):
        result = NormalizedExtractionResult(
            provider="structured extraction",
            fields=[
                NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value="1,000", confidence=0.98),
                NormalizedExtractionField(field_name="3b. Amount of Commissions", value="100", confidence=0.98),
                NormalizedExtractionField(field_name="3c. Amount of Fees", value="20", confidence=0.98),
                NormalizedExtractionField(field_name="9a. Premiums: (1) Amount Received", value="1,000", confidence=0.98),
                NormalizedExtractionField(field_name="9c(1)(A). Commissions", value="100", confidence=0.98),
                NormalizedExtractionField(field_name="9c(1)(B). Administrative service or other fees", value="20", confidence=0.98),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertIn(
            "cross_section_duplicate:9a",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        self.assertIn(
            "cross_section_duplicate:9c_commissions",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        self.assertIn(
            "cross_section_duplicate:9c_fees",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )
        experience_fields = [
            field for field in resolved.fields if field.field_name.lower().startswith("9")
        ]
        self.assertTrue(all(field.decision == "REVIEW_REQUIRED" for field in experience_fields))

    def test_explicit_experience_section_evidence_allows_equal_values(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value="1,000", confidence=0.95),
                NormalizedExtractionField(
                    field_name="9a. Premiums: (1) Amount Received",
                    value="1,000",
                    confidence=0.95,
                    page=2,
                    source_text="Part II Experience-rated contracts line 9a(1) Amount Received $1,000",
                ),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertNotIn(
            "cross_section_duplicate:9a",
            resolved.raw["extraction_quality"]["cross_field_errors"],
        )

    def test_financial_value_without_matching_section_context_requires_review(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="9c(1)(A). Commissions",
                    value="100",
                    confidence=0.98,
                    page=2,
                    source_text="Commissions paid $100",
                ),
                NormalizedExtractionField(
                    field_name="10a. Total premiums or subscription charges paid to carrier",
                    value="1,000",
                    confidence=0.98,
                    page=2,
                    source_text="Total premium $1,000",
                ),
            ],
        )

        resolved = resolve_schedule_a_result(result)

        self.assertTrue(all(field.decision == "REVIEW_REQUIRED" for field in resolved.fields))
        self.assertTrue(
            all(
                any(
                    item.validator == "section_context" and item.status == "ERROR"
                    for item in field.validation_results
                )
                for field in resolved.fields
            )
        )

    def test_explicit_financial_section_context_is_automatic(self):
        result = NormalizedExtractionResult(
            provider="layout OCR",
            fields=[
                NormalizedExtractionField(
                    field_name="10a. Total premiums or subscription charges paid to carrier",
                    value="1,000",
                    confidence=0.98,
                    page=2,
                    source_text="Part III line 10a nonexperience-rated total premiums $1,000",
                )
            ],
        )

        resolved = resolve_schedule_a_result(result)

        field = resolved.fields[0]
        self.assertEqual(field.decision, "AUTOMATIC")
        self.assertTrue(
            any(
                item.validator == "section_context" and item.status == "PASS"
                for item in field.validation_results
            )
        )

    def test_published_rule_validator_applies_to_a_new_field_without_pipeline_code(self):
        rule = FieldRule(
            key="schedule_a_custom_risk_charge",
            label="Risk Pool Charge",
            ftw_field="Risk Pool Charge",
            priority="MEDIUM",
            source="Schedule A",
            field_type="Currency",
            aliases=["Carrier Risk Pool Charge"],
        )
        result = NormalizedExtractionResult(
            provider="rules-driven parser",
            fields=[
                NormalizedExtractionField(
                    field_name="Risk Pool Charge",
                    value="not an amount",
                    confidence=0.98,
                )
            ],
        )

        resolved = resolve_schedule_a_result(result, rules=[rule])

        field = resolved.fields[0]
        self.assertEqual(field.decision, "REVIEW_REQUIRED")
        self.assertTrue(
            any(item.validator == "currency" and item.status == "ERROR" for item in field.validation_results)
        )

    def test_required_published_rule_is_part_of_the_review_gate(self):
        rule = FieldRule(
            key="schedule_a_required_category",
            label="Required Carrier Category",
            ftw_field="",
            mapping_mode="EXTRACTION_ONLY",
            priority="HIGH",
            source="Schedule A",
            field_type="Text",
            aliases=["Carrier Category"],
            required=True,
        )

        resolved = resolve_schedule_a_result(
            NormalizedExtractionResult(provider="rules-driven parser", fields=[]),
            rules=[rule],
        )

        self.assertIn(
            "Required Carrier Category",
            resolved.raw["extraction_quality"]["missing_required_fields"],
        )

        present = resolve_schedule_a_result(
            NormalizedExtractionResult(
                provider="rules-driven parser",
                fields=[
                    NormalizedExtractionField(
                        field_name="Required Carrier Category",
                        value="Medical",
                        confidence=0.95,
                    )
                ],
            ),
            rules=[rule],
        )
        self.assertNotIn(
            "Required Carrier Category",
            present.raw["extraction_quality"]["missing_required_fields"],
        )

    def test_shadow_mode_records_the_new_decision_without_changing_current_values(self):
        result = NormalizedExtractionResult(
            provider="legacy extractor",
            fields=[
                NormalizedExtractionField(
                    field_name="1b. Insurance Carrier EIN",
                    value="Carrier EIN",
                    confidence=0.99,
                )
            ],
            raw={"legacy": True},
        )

        shadowed = apply_schedule_a_pipeline(result, authoritative=False, shadow=True)

        self.assertEqual(shadowed.fields[0].confidence, 0.99)
        self.assertEqual(shadowed.fields[0].decision, "UNASSESSED")
        self.assertEqual(shadowed.raw["canonical_shadow_quality"]["decision"], "REVIEW_REQUIRED")
        self.assertTrue(shadowed.raw["legacy"])


if __name__ == "__main__":
    unittest.main()
