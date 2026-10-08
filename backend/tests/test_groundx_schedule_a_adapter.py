from app.services.groundx_schedule_a_adapter import adapt_groundx_schedule_a_xray
from app.services.extractor import extract_fields_from_groundx_xray, select_best_schedule_a_fields
from app.models import DocumentType, FormType, NormalizedExtractionField, NormalizedExtractionResult
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_classification import classify_schedule_a_fields
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def test_reliance_xray_semantics_normalize_complete_nonexperience_schedule_a():
    payload = {
        "chunks": [
            {
                "pageNumbers": [1],
                "text": """Reliance Standard Life Insurance Company
EIN: 36-0883760
NAIC: 68381
ORG. NUMBER: 3
Policy Number: GL160111
Number of covered lives: Beginning: 351 Ending: 319
Policy Contract Year: 01/01/2025 to 12/31/2025""",
                "json": [{"contents": [{"text": "EIN: 36-0883760 NAIC: 68381 ORG. NUMBER: 3"}]}],
            },
            {
                "pageNumbers": [1],
                "text": "Total Premium: $10,878.32",
                "json": [
                    {
                        "record_type": "table_summary",
                        "company": "Reliance Standard Life Insurance Company",
                        "policy_number": "GL160111",
                        "policy_contract_year": "01/01/2025 to 12/31/2025",
                    },
                    {
                        "record_type": "table_total",
                        "row_label": "Total Premium",
                        "Total Premium": "$10,878.32",
                    },
                ],
            },
            {
                "pageNumbers": [1],
                "json": [
                    {
                        "record_type": "compensation",
                        "company_name": "Reliance Standard Life Insurance Company",
                        "payee_name": "Gallagher Benefit Services Inc",
                        "payee_address": "Mail Stop : 072103 P. O. Box 4135 Clinton, IA 52732",
                        "total_commission": "$1,522.94",
                        "total_administrative_and_other_fees": "$384.40",
                    }
                ],
            },
        ],
        "documentPages": [{"pageNumber": 1}],
    }

    result = adapt_groundx_schedule_a_xray(payload)
    fields = {field.field_name: field.value for field in result.fields}

    assert fields["1a. Name of Insurance Company"] == "Reliance Standard Life Insurance Company"
    assert fields["1b. Insurance Carrier EIN"] == "36-0883760"
    assert fields["1c. NAIC Code"] == "68381"
    assert fields["1d. Contract/Policy Number"] == "GL160111"
    assert fields["1e. Persons Covered (End of Policy Year)"] == "319"
    assert fields["1f. Policy Year Beginning Date"] == "01/01/2025"
    assert fields["1g. Policy Year Ending Date"] == "12/31/2025"
    assert fields["3b. Amount of Commissions"] == "1,522.94"
    assert fields["3c. Amount of Fees"] == "384.40"
    assert fields["3e. Organizational Code"] == "3"
    assert fields["10a. Total premiums or subscription charges paid to carrier"] == "10,878.32"

    assert len(result.broker_rows) == 1
    broker = result.broker_rows[0]
    assert broker.name == "Gallagher Benefit Services Inc"
    assert broker.address_line_1 == "Mail Stop : 072103"
    assert broker.address_line_2 == "P. O. Box 4135"
    assert broker.city == "Clinton"
    assert broker.state == "IA"
    assert broker.zip_code == "52732"
    assert broker.commission_total == "1,522.94"
    assert broker.fee_total == "384.40"
    assert broker.organization_code == "3"
    assert "PREMIUM_AMOUNT_PRESENT" in result.classification_signals


def test_adapter_reads_structured_json_embedded_in_suggested_text():
    payload = {
        "chunks": [
            {
                "pageNumbers": [2],
                "json": "",
                "suggestedText": (
                    '{"record_type":"compensation","payee_name":"Example Broker",'
                    '"total_commission":"$25.00","total_administrative_and_other_fees":"$5.00"}'
                    "\nThe following table contains compensation information."
                ),
            }
        ],
        "documentPages": [{"pageNumber": 2}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert [(row.name, row.commission_total, row.fee_total) for row in result.broker_rows] == [
        ("Example Broker", "25.00", "5.00")
    ]


def test_adapter_restores_full_numeric_policy_from_labeled_source_evidence():
    payload = {
        "chunks": [
            {
                "pageNumbers": [2],
                "text": (
                    "(a) Name of insurance carrier: The Lincoln National Life Insurance Company\n"
                    "(b) EIN: 35-0472300\n"
                    "(c) NAIC code: 65676\n"
                    "(d) Contract or identification number: 000010233868 00000"
                ),
                "json": [
                    {
                        "record_type": "policy",
                        "company_name": "The Lincoln National Life Insurance Company",
                        "policy_number": "000010233868",
                        "carrier_ein": "35-0472300",
                        "naic_code": "65676",
                    }
                ],
            }
        ],
        "documentPages": [{"pageNumber": 2}],
    }

    result = adapt_groundx_schedule_a_xray(payload)
    values = {field.field_name: field.value for field in result.fields}
    selected = select_best_schedule_a_fields(extract_fields_from_groundx_xray(payload))
    selected_values = {field.field_name: field.value for field in selected}

    assert values["1d. Contract/Policy Number"] == "00001023386800000"
    assert selected_values["1d. Contract/Policy Number"] == "00001023386800000"


def test_adapter_preserves_multiple_broker_rows():
    payload = {
        "chunks": [
            {
                "pageNumbers": [3],
                "json": [
                    {
                        "record_type": "compensation",
                        "payee_name": "Broker One",
                        "total_commission": "$100.00",
                        "total_administrative_and_other_fees": "$10.00",
                    },
                    {
                        "record_type": "compensation",
                        "payee_name": "Broker Two",
                        "total_commission": "$200.00",
                        "total_administrative_and_other_fees": "$20.00",
                    },
                ],
            }
        ],
        "documentPages": [{"pageNumber": 3}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert [row.name for row in result.broker_rows] == ["Broker One", "Broker Two"]
    assert [row.commission_total for row in result.broker_rows] == ["100.00", "200.00"]


def test_adapter_output_classifies_pomerene_as_nonexperience_rated():
    payload = {
        "chunks": [
            {
                "pageNumbers": [1],
                "json": [
                    {
                        "record_type": "table_total",
                        "row_label": "Total Premium",
                        "Total Premium": "$10,878.32",
                    }
                ],
            }
        ],
        "documentPages": [{"pageNumber": 1}],
    }

    result = adapt_groundx_schedule_a_xray(payload)
    mapped = map_extraction_to_rules(
        "pomerene-adapter-test",
        result.fields,
        form_type=FormType.SCHEDULE_A,
        source_document_type=DocumentType.SCHEDULE_A,
    )["fields"]

    classification = classify_schedule_a_fields(mapped, result.classification_signals)

    assert classification.contract_type.value == "NONEXPERIENCE_RATED"
    assert "premium amounts are present without claim amounts" in classification.reason


def test_adapter_does_not_treat_blank_claim_labels_as_claim_amounts():
    payload = {
        "chunks": [
            {
                "pageNumbers": [4],
                "text": (
                    "9 Experience-rated contracts: Claims paid Claim reserves\n"
                    "10 Nonexperience-rated contracts"
                ),
                "json": [
                    {
                        "record_type": "table_total",
                        "row_label": "Total Premium",
                        "total_premium": "$10,878.32",
                    }
                ],
            }
        ],
        "documentPages": [{"pageNumber": 4}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert "PREMIUM_AMOUNT_PRESENT" in result.classification_signals
    assert "CLAIM_AMOUNT_PRESENT" not in result.classification_signals


def test_adapter_uses_populated_structured_claim_amount_as_claim_evidence():
    payload = {
        "chunks": [
            {
                "pageNumbers": [4],
                "json": [
                    {
                        "record_type": "experience_rated_financials",
                        "total_premium": "$50,000.00",
                        "claims_paid": "$12,000.00",
                    }
                ],
            }
        ],
        "documentPages": [{"pageNumber": 4}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert "PREMIUM_AMOUNT_PRESENT" in result.classification_signals
    assert "CLAIM_AMOUNT_PRESENT" in result.classification_signals


def test_adapter_flags_multiple_policy_records_for_review():
    payload = {
        "chunks": [
            {
                "pageNumbers": [1],
                "json": [
                    {
                        "record_type": "policy",
                        "company_name": "Carrier One",
                        "policy_number": "POLICY-ONE",
                        "policy_contract_year": "01/01/2025 to 12/31/2025",
                    },
                    {
                        "record_type": "policy",
                        "company_name": "Carrier Two",
                        "policy_number": "POLICY-TWO",
                        "policy_contract_year": "01/01/2025 to 12/31/2025",
                    },
                ],
            }
        ],
        "documentPages": [{"pageNumber": 1}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert result.schedule_a_count == 2
    assert "MULTIPLE_SCHEDULE_A_RECORDS" in result.classification_signals


def test_adapter_does_not_count_cover_letter_and_see_above_references_as_multiple_records():
    payload = {
        "chunks": [
            {
                "pageNumbers": [1],
                "json": [
                    {
                        "record_type": "table_summary",
                        "company_name": "Sun Life Assurance Company of Canada",
                        "policy_number": "924948",
                        "policy_contract_year": "01/01/2025 to 12/31/2025",
                    },
                    {
                        "record_type": "table_cell",
                        "contract_or_identification_number": "SEE ABOVE # 62",
                    },
                ],
            }
        ],
        "documentPages": [{"pageNumber": 1}],
    }

    result = adapt_groundx_schedule_a_xray(payload)

    assert result.schedule_a_count == 1
    assert "MULTIPLE_SCHEDULE_A_RECORDS" not in result.classification_signals


def test_canonical_validation_blocks_multiple_xray_schedule_a_records():
    result = NormalizedExtractionResult(
        provider="GroundX X-Ray",
        fields=[
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="POLICY-ONE; POLICY-TWO",
                candidate_values=["POLICY-ONE", "POLICY-TWO"],
                confidence=0.99,
                page=1,
                source_text="Two policy records",
            )
        ],
        raw={"xray_adapter": {"schedule_a_count": 2}},
        classification_signals=["MULTIPLE_SCHEDULE_A_RECORDS"],
    )

    resolved = resolve_schedule_a_result(result)

    quality = resolved.raw["extraction_quality"]
    assert quality["decision"] == "REVIEW_REQUIRED"
    assert "multiple_schedule_a_records" in quality["cross_field_errors"]


def test_full_xray_selection_prefers_semantic_policy_number_over_layout_label():
    payload = {
        "chunks": [
            {
                "pageNumbers": [1],
                "text": "Policy Number: GL160111",
                "json": [
                    {
                        "record_type": "table_summary",
                        "company_name": "Reliance Standard Life Insurance Company",
                        "policy_number": "GL160111",
                        "policy_contract_year": "01/01/2025 to 12/31/2025",
                    }
                ],
                "suggestedText": "Policy Number Policy",
            }
        ],
        "documentPages": [{"pageNumber": 1}],
    }

    selected = select_best_schedule_a_fields(extract_fields_from_groundx_xray(payload))
    values = {field.field_name: field.value for field in selected}

    assert values["1d. Contract/Policy Number"] == "GL160111"


def test_field_selection_prefers_complete_numeric_policy_over_higher_confidence_prefix():
    selected = select_best_schedule_a_fields(
        [
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="000010233868",
                confidence=0.99,
                page=2,
                source_text="Structured value",
            ),
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="00001023386800000",
                confidence=0.97,
                page=2,
                source_text=(
                    "(d) Contract or identification number: "
                    "000010233868 00000"
                ),
            ),
        ]
    )

    assert selected[0].value == "00001023386800000"
