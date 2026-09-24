import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from app.models import FieldRule, FormType
from app.services.extractor import ExtractionService, safe_error_summary
from app.services.field_rules import DEFAULT_FIELD_RULES, form_type_for_rule
from app.services.groundx_schedule_a_workflow import (
    build_schedule_a_extraction_mapping,
    normalize_groundx_schedule_a_extract,
    schedule_a_schema_version,
    schedule_a_workflow_yaml,
)
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def schedule_a_rules() -> list[FieldRule]:
    return [rule for rule in DEFAULT_FIELD_RULES if form_type_for_rule(rule) == FormType.SCHEDULE_A]


def test_schema_is_generated_from_schedule_a_rules_and_keeps_groups_small():
    rules = schedule_a_rules()

    mapping = build_schedule_a_extraction_mapping(rules)
    extraction_groups = {
        key: value
        for key, value in mapping.items()
        if key not in {"extraction_policy_version", "workflow"}
    }

    emitted_keys = {
        field_key
        for group_name, group in extraction_groups.items()
        if group_name not in {"broker_rows", "broker_totals"}
        for field_key in group["fields"]
    }
    scalar_rule_keys = {
        rule.key
        for rule in rules
        if rule.field_type != "Reference/Lookup"
        and not rule.label.lower().startswith(("3a.", "3b.", "3c.", "3d.", "3e."))
    }
    assert emitted_keys == scalar_rule_keys
    assert not any(rule.label.startswith("4") and rule.key in emitted_keys for rule in rules)
    assert not any(rule.label.startswith("3") and rule.key in emitted_keys for rule in rules)
    assert all(
        len(group["fields"]) <= 20
        for group_name, group in extraction_groups.items()
        if group_name not in {"broker_rows", "broker_totals"}
    )
    assert mapping["extraction_policy_version"] == "v1"
    assert mapping["workflow"]["custom_steps"]
    assert mapping["workflow"]["agent_chain"][0]["parallel"]
    assert {"name", "address_line_1", "city", "commission_total", "fee_total"} <= set(
        mapping["broker_rows"]["fields"]
    )


def test_new_alias_and_published_rule_change_schema_without_parser_code_change():
    rules = schedule_a_rules()
    target = next(rule for rule in rules if rule.key.endswith("1d_contract_policy_number"))
    changed = target.model_copy(
        deep=True,
        update={"aliases": [*target.aliases, "Vendor Contract Reference"], "version": target.version + 1},
    )
    custom_rules = [changed if rule.key == target.key else rule for rule in rules]

    mapping = build_schedule_a_extraction_mapping(custom_rules)
    identifiers = next(
        field["prompt"]["identifiers"]
        for group_name, group in mapping.items()
        if group_name not in {"extraction_policy_version", "workflow", "broker_rows"}
        for field_key, field in group["fields"].items()
        if field_key == target.key
    )

    assert "Vendor Contract Reference" in identifiers
    assert schedule_a_schema_version(custom_rules) != schedule_a_schema_version(rules)


def test_schema_prompts_keep_coverage_and_financial_lines_in_their_exact_semantic_context():
    rules = schedule_a_rules()
    mapping = build_schedule_a_extraction_mapping(rules)

    def instructions(suffix: str) -> str:
        target = next(rule for rule in rules if rule.key.endswith(suffix))
        return next(
            field["prompt"]["instructions"]
            for group_name, group in mapping.items()
            if group_name not in {"extraction_policy_version", "workflow", "broker_rows", "broker_totals"}
            for field_key, field in group["fields"].items()
            if field_key == target.key
        )

    assert "never calculate or sum" in instructions("1e_persons_covered_end_of_policy_year").lower()
    assert "exposure" in instructions("1e_persons_covered_end_of_policy_year").lower()
    assert "experience-rated" in instructions("9a_premiums_1_amount_received").lower()
    assert "do not copy line 10a" in instructions("9a_premiums_1_amount_received").lower()
    assert "nonexperience-rated" in instructions(
        "10a_total_premiums_or_subscription_charges_paid_to_carrier"
    ).lower()
    assert "multiple premium rows" in instructions(
        "10a_total_premiums_or_subscription_charges_paid_to_carrier"
    ).lower()

    experience_rule = next(rule for rule in rules if rule.key.endswith("9a_premiums_1_amount_received"))
    experience_identifiers = next(
        field["prompt"]["identifiers"]
        for group_name, group in mapping.items()
        if group_name not in {"extraction_policy_version", "workflow", "broker_rows", "broker_totals"}
        for field_key, field in group["fields"].items()
        if field_key == experience_rule.key
    )
    assert "Premiums" not in experience_identifiers
    assert "Total Premium" not in experience_identifiers


def test_generated_yaml_loads_with_groundx_extract_sdk():
    groundx = pytest.importorskip("groundx")
    yaml_text = schedule_a_workflow_yaml(schedule_a_rules())

    definition = groundx.GroundX(api_key="test").load_extraction_definition_from_yaml(
        yaml_text=yaml_text
    )

    assert "schedule_a_policy" in definition.extract
    assert "broker_rows" in definition.extract
    assert definition.custom_steps
    assert yaml.safe_load(yaml_text)["broker_rows"]["fields"]["name"]


def test_structured_extract_normalizes_canonical_fields_brokers_and_evidence():
    rules = schedule_a_rules()
    carrier_rule = next(rule for rule in rules if rule.key.endswith("1a_name_of_insurance_company"))
    contract_rule = next(rule for rule in rules if rule.key.endswith("1d_contract_policy_number"))
    payload = {
        "extract": {
            "schedule_a_policy": {
                carrier_rule.key: {
                    "value": "Principal Life Insurance Company",
                    "confidence": 0.98,
                    "page": 1,
                    "sourceText": "Name of Insurance Carrier Principal Life Insurance Company",
                },
                contract_rule.key: "1022824",
            },
            "broker_rows": {
                "rows": [
                    {
                        "name": "NFP CORPORATE SERVICES NY LLC",
                        "address_line_1": "265 FRANKLIN ST STE 1901",
                        "city": "BOSTON",
                        "state": "MA",
                        "zip_code": "02110-3173",
                        "organization_code": "3",
                        "commission_total": 13369,
                        "fee_total": 2913,
                        "purpose": "Service Fee",
                    },
                    {
                        "name": "MERCER HEALTH & BENEFITS LLC",
                        "commission_total": "9,407",
                        "fee_total": "2,014",
                    },
                ]
            },
        }
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    fields = {field.field_name: field for field in result.fields}
    assert fields[carrier_rule.label].value == "Principal Life Insurance Company"
    assert fields[carrier_rule.label].page == 1
    assert fields[carrier_rule.label].evidence[0].provider == "GroundX structured extract"
    assert fields[contract_rule.label].value == "1022824"
    assert [row.name for row in result.schedule_a_broker_rows] == [
        "NFP CORPORATE SERVICES NY LLC",
        "MERCER HEALTH & BENEFITS LLC",
    ]
    assert result.schedule_a_broker_rows[0].commission_total == "13369"
    assert result.schedule_a_broker_rows[0].fee_total == "2913"


def test_structured_extract_accepts_rule_alias_keys_but_does_not_guess_unknown_fields():
    rules = schedule_a_rules()
    contract_rule = next(rule for rule in rules if rule.key.endswith("1d_contract_policy_number"))
    alias = "Vendor Contract Reference"
    changed = contract_rule.model_copy(deep=True, update={"aliases": [*contract_rule.aliases, alias]})
    custom_rules = [changed if rule.key == contract_rule.key else rule for rule in rules]
    payload = {"policy": {alias: "ABC-123", "Nearby Number": "999"}}

    result = normalize_groundx_schedule_a_extract(payload, custom_rules)

    assert [(field.field_name, field.value) for field in result.fields] == [
        (contract_rule.label, "ABC-123")
    ]


def test_structured_extract_preserves_conflicting_candidates_for_review():
    rules = schedule_a_rules()
    persons_rule = next(
        rule for rule in rules if rule.key.endswith("1e_persons_covered_end_of_policy_year")
    )
    payload = {
        "first_coverage_table": {
            persons_rule.key: {
                "value": 104,
                "confidence": 0.98,
                "page": 1,
                "sourceText": "Subscribers 104",
            }
        },
        "second_coverage_table": {
            persons_rule.key: {
                "value": 171,
                "confidence": 0.97,
                "page": 2,
                "sourceText": "Total covered lives 171",
            }
        },
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    field = next(item for item in result.fields if item.field_name == persons_rule.label)
    assert field.value == "104"
    assert field.candidate_values == ["104", "171"]
    assert {evidence.page for evidence in field.evidence} == {1, 2}


def test_structured_extract_prefers_position_backed_value_over_higher_label_confidence():
    rules = schedule_a_rules()
    carrier_rule = next(rule for rule in rules if rule.key.endswith("1a_name_of_insurance_company"))
    payload = {
        "label_only": {
            carrier_rule.key: {
                "value": "EIN (Insurance Carrier)",
                "confidence": 0.99,
                "page": 1,
                "sourceText": "EIN (Insurance Carrier)",
            }
        },
        "positioned_value": {
            carrier_rule.key: {
                "value": "Federal Insurance Company",
                "confidence": 0.94,
                "page": 1,
                "rowIndex": 8,
                "columnIndex": 2,
                "sourceText": "Name of Insurance Company: Federal Insurance Company",
            }
        },
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    field = next(item for item in result.fields if item.field_name == carrier_rule.label)
    assert field.value == "Federal Insurance Company"
    assert field.candidate_values == ["Federal Insurance Company"]


def test_provider_value_is_not_used_as_fabricated_source_evidence():
    rules = schedule_a_rules()
    contract_rule = next(rule for rule in rules if rule.key.endswith("1d_contract_policy_number"))
    payload = {
        "schedule_a_policy": {
            contract_rule.key: {
                "value": "ABC-123",
                "confidence": 0.99,
                "page": 1,
            }
        }
    }

    normalized = normalize_groundx_schedule_a_extract(payload, rules)
    resolved = resolve_schedule_a_result(normalized, rules=rules)

    field = next(item for item in resolved.fields if item.field_name == contract_rule.label)
    self_evidence = field.evidence[0]
    assert self_evidence.source_text is None
    assert field.decision == "REVIEW_REQUIRED"
    assert any(
        item.validator == "source_evidence" and item.status == "ERROR"
        for item in field.validation_results
    )


def test_broker_normalization_preserves_page_and_column_source_evidence():
    rules = schedule_a_rules()
    payload = {
        "broker_rows": [
            {
                "name": {
                    "value": "Example Broker LLC",
                    "page": 2,
                    "rowIndex": 7,
                    "columnIndex": 1,
                    "sourceText": "Example Broker LLC",
                },
                "commission_total": {
                    "value": 200,
                    "page": 2,
                    "sourceText": "Commissions Paid $200",
                },
                "fee_total": {
                    "value": 100,
                    "page": 2,
                    "sourceText": "Fees Paid $100",
                },
            }
        ]
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    row = result.schedule_a_broker_rows[0]
    assert row.source_page == 2
    assert row.commission_source_text == "Commissions Paid $200"
    assert row.fee_source_text == "Fees Paid $100"
    assert any(evidence.page == 2 for evidence in row.evidence)
    assert any(evidence.table_cell == (7, 1) for evidence in row.evidence)


def test_structured_extract_normalizes_dates_rejects_guesses_and_consolidates_broker_payments():
    rules = schedule_a_rules()

    def rule(suffix: str) -> FieldRule:
        return next(item for item in rules if item.key.endswith(suffix))

    payload = {
        "schedule_a_policy": {
            rule("1e_persons_covered_end_of_policy_year").key: "Benefit Employee Dependent",
            rule("1f_policy_year_beginning_date").key: "January 2025",
            rule("1g_policy_year_ending_date").key: "December 2025",
        },
        "schedule_a_compensation": {
            rule("3b_amount_of_commissions").key: "$75",
        },
        "schedule_a_plan_header": {
            rule("4b_plan_number_pn").key: "FLX0966852",
        },
        "broker_totals": [{"commission_total": 120, "fee_total": 30}],
        "broker_rows": [
            {
                "name": "ALLIANCE 360 INSURANCE SOLUTIONS",
                "address_line_1": "10833 VALLEY VIEW ST",
                "zip_code": "90630",
                "organization_code": "3- Insurance Agent or Broker",
                "commission_total": 100,
                "fee_total": 0,
                "purpose": "Standard Commissions",
            },
            {
                "name": "ALLIANCE 360 INSURANCE SOLUTIONS",
                "address_line_1": "10833 VALLEY VIEW ST",
                "zip_code": "90630",
                "organization_code": "3- Insurance Agent or Broker",
                "commission_total": 50,
                "fee_total": 10,
                "purpose": "Bonus",
            },
            {
                "name": "CENTERSTONE INS & FIN SVC LLC",
                "address_line_1": "12404 PARK CENTRAL DRIVE",
                "zip_code": "75251",
                "organization_code": "3",
                "commission_total": 0,
                "fee_total": 25,
                "purpose": "Service Fee",
            },
        ],
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    fields = {field.field_name: field.value for field in result.fields}
    assert fields[rule("1f_policy_year_beginning_date").label] == "01/01/2025"
    assert fields[rule("1g_policy_year_ending_date").label] == "12/31/2025"
    assert rule("1e_persons_covered_end_of_policy_year").label not in fields
    assert rule("4b_plan_number_pn").label not in fields
    # Primary Part I totals remain authoritative. The validator can now detect
    # that the extracted row allocations do not reconcile with those totals.
    assert fields[rule("3b_amount_of_commissions").label] == "120"
    assert fields[rule("3c_amount_of_fees").label] == "30"
    assert len(result.schedule_a_broker_rows) == 2
    assert result.schedule_a_broker_rows[0].commission_total == "150"
    assert result.schedule_a_broker_rows[0].fee_total == "10"
    assert result.schedule_a_broker_rows[0].organization_code == "3"
    assert [row.amount for row in result.schedule_a_broker_rows[0].commission_rows] == ["100", "50"]


def test_structured_extract_drops_line_9_values_copied_from_nonexperience_and_broker_totals():
    rules = schedule_a_rules()

    def by_prefix(prefix: str) -> FieldRule:
        return next(item for item in rules if item.label.lower().startswith(prefix.lower()))

    line_9a = by_prefix("9a. Premiums")
    line_9c_commissions = by_prefix("9c(1)(A).")
    line_9c_fees = by_prefix("9c(1)(B).")
    line_10a = by_prefix("10a.")
    payload = {
        "schedule_a_financial": {
            line_9a.key: "$1,000",
            line_9c_commissions.key: "$100",
            line_9c_fees.key: "$20",
            line_10a.key: "$1,000",
        },
        "broker_totals": [{"commission_total": 100, "fee_total": 20}],
        "broker_rows": [
            {
                "name": "EXAMPLE BROKER LLC",
                "commission_total": 100,
                "fee_total": 20,
            }
        ],
    }

    result = normalize_groundx_schedule_a_extract(payload, rules)

    fields = {field.field_name: field.value for field in result.fields}
    assert fields[line_10a.label] == "$1,000"
    assert line_9a.label not in fields
    assert line_9c_commissions.label not in fields
    assert line_9c_fees.label not in fields


def test_groundx_structured_extract_404_is_a_safe_fallback():
    service = ExtractionService(field_rules=schedule_a_rules())
    client = AsyncMock()
    client.get.return_value = SimpleNamespace(status_code=404)
    service._find_groundx_document_refs = AsyncMock(return_value=[{"documentId": "doc-1"}])

    payloads = asyncio.run(
        service._fetch_groundx_structured_extracts(
            client,
            "https://api.groundx.ai/api/v1",
            {"X-API-Key": "test"},
            [{"documentId": "doc-1"}],
            "schedule-a.pdf",
        )
    )

    assert payloads == []


def test_groundx_extraction_prefers_structured_output_and_keeps_fallbacks_available():
    rules = schedule_a_rules()
    carrier_rule = next(rule for rule in rules if rule.key.endswith("1a_name_of_insurance_company"))
    service = ExtractionService(field_rules=rules)
    structured = {"schedule_a_policy": {carrier_rule.key: "Structured Carrier"}}
    settings = SimpleNamespace(
        groundx_api_key="test",
        groundx_bucket_id=123,
        groundx_api_base_url="https://api.groundx.ai/api/v1",
    )

    with (
        patch("app.services.extractor.get_settings", return_value=settings),
        patch.object(service, "_ingest_groundx_file", new=AsyncMock(return_value={"documentId": "doc-1"})),
        patch.object(service, "_fetch_groundx_structured_extracts", new=AsyncMock(return_value=[structured])),
        patch.object(service, "_fetch_groundx_xray_payloads", new=AsyncMock(return_value=[])),
        patch.object(service, "_search_groundx_with_field_schema", new=AsyncMock(return_value=None)),
        patch("app.services.extractor.extract_fields_from_pdf_text", return_value=[]),
        patch("app.services.extractor.extract_schedule_a_broker_rows_from_pdf_text", return_value=[]),
        patch("app.services.extractor.extract_schedule_a_worksheet_summaries_from_pdf_text", return_value=[]),
    ):
        result = asyncio.run(
            service._extract_with_groundx(
                b"not-a-real-pdf",
                "schedule-a.pdf",
                FormType.SCHEDULE_A,
                "Schedule A",
            )
        )

    assert result.provider.startswith("GroundX structured extract")
    assert [(field.field_name, field.value) for field in result.fields] == [
        (carrier_rule.label, "Structured Carrier")
    ]


def test_groundx_sdk_error_summary_exposes_status_and_body_instead_of_headers():
    class FakeApiError(Exception):
        status_code = 402
        body = {"message": "monthly token limit reached"}

        def __str__(self):
            return "headers: {'x-cache': 'Error from cloudfront'}"

    summary = safe_error_summary(FakeApiError())

    assert summary == "HTTP 402: {'message': 'monthly token limit reached'}"
    assert "headers" not in summary
