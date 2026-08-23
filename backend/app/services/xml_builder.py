from datetime import datetime
from html import escape
import re
import xml.etree.ElementTree as ET
from app.config import get_settings
from app.models import ExtractedField, FieldPriority, FormType
from app.services.ftwilliams_contract import normalize_ftw_update_value
from app.services.ftwilliams_tags import (
    SCHEDULE_A_CURRENT_TAGS_BY_RULE,
    SCHEDULE_A_TAGS_BY_RULE,
    resolve_ftw_current_value,
    resolve_ftw_tag,
    resolve_ftw_update_tag,
    values_meaningfully_different,
)


FTW_DATE_TAGS = {
    "PLAN_EFF_DATE",
    "FORM_PLAN_YEAR_BEGIN_DATE",
    "FORM_TAX_PRD",
    "InsPolicyFromDate",
    "InsPolicyToDate",
    "PlanYearBeginDate",
    "PlanYearEndDate",
}

FTW_INDICATOR_TAGS = {
    "FundingInsuranceInd",
    "FundingCdSection412Ind",
    "FundingTrustInd",
    "FundingGeneralAssetInd",
    "BenefitInsuranceInd",
    "BenefitCdSection412Ind",
    "BenefitTrustInd",
    "BenefitGeneralAssetInd",
}

FTW_ONE_TWO_BOOLEAN_TAGS = {
    "InsFailProvideInfoInd",
}

FORM_5500_INDICATOR_GROUPS = {
    "form_5500_part_ii_9_plan_funding_arrangement": [
        ("FundingInsuranceInd", ("insurance",)),
        ("FundingCdSection412Ind", ("412", "code section")),
        ("FundingTrustInd", ("trust",)),
        ("FundingGeneralAssetInd", ("general assets", "general asset", "sponsor")),
    ],
    "form_5500_part_ii_10a_plan_benefit_arrangement": [
        ("BenefitInsuranceInd", ("insurance",)),
        ("BenefitCdSection412Ind", ("412", "code section")),
        ("BenefitTrustInd", ("trust",)),
        ("BenefitGeneralAssetInd", ("general assets", "general asset", "sponsor")),
    ],
}

SCHEDULE_A_REPLACEMENT_CONTROL_TAGS = {
    "TransactionType",
    "EditCheck",
    "CustomerID",
    "PlanID",
    "FTWCustomerID",
    "FTWPlanID",
    "FTWSeqNo",
    "Year",
    "Type",
    "ErrorCode",
    "ErrorDesc",
    "StatusSuccess",
    "Broker",
    "DOLSubPartData",
}

SCHEDULE_A_BROKER_FIELD_BASES = (
    "ProvinceOrState",
    "AddressLine1",
    "AddressLine2",
    "FeesPdText",
    "CommPdAmt",
    "FeesPdAmt",
    "ForeignAddy",
    "PostalCode",
    "ZipCode",
    "Country",
    "State",
    "City",
    "Code",
    "Name",
)


def build_proposed_ftw_xml(fields: list[ExtractedField]) -> str:
    return build_ftw_update_xml(fields)


def build_ftw_update_xml(
    fields: list[ExtractedField],
    *,
    customer_id: str | None = None,
    plan_id: str | None = None,
    year: str | None = None,
    ftw_customer_id: str | None = None,
    ftw_plan_id: str | None = None,
    ftw_seq_no: str | None = None,
    form_5500_current_values: dict[str, str] | None = None,
    schedule_a_current_values: dict[str, str] | None = None,
    schedule_a_broker_rows: list | None = None,
    include_5500: bool = True,
    include_schedule_a: bool = True,
) -> str:
    settings = get_settings()
    key_id = settings.ftwlink_key_id or "[FTWLINK_KEY_ID]"
    filing_year = year or get_value(fields, "form_5500_part_i_7_plan_year_ending_date") or get_value(fields, "schedule_a_part_iv_4e_plan_year_ending_date") or "[Year]"
    data_batches: list[str] = []
    if include_5500:
        form_5500_xml = _document_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id=customer_id,
            plan_id=plan_id,
            year=filing_year,
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            current_values=form_5500_current_values,
            ftw_seq_no=None,
        )
        if form_5500_xml:
            data_batches.append(form_5500_xml)
    if include_schedule_a:
        schedule_a_xml = _document_xml(
            "DOLScheduleAData",
            fields,
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id=customer_id,
            plan_id=plan_id,
            year=filing_year,
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            current_values=schedule_a_current_values,
            ftw_seq_no=ftw_seq_no,
            preserve_current_values=bool(schedule_a_current_values),
            schedule_a_broker_rows=schedule_a_broker_rows,
        )
        if schedule_a_xml:
            data_batches.append(schedule_a_xml)
    if not data_batches:
        data_batches.append("    <!-- No approved FT Williams fields are available yet. -->")
    documents_xml = "\n".join(data_batches)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <KeyID>{escape(key_id)}</KeyID>
  <DataBatch>
{documents_xml}
  </DataBatch>
</ftwLink>'''


def build_single_document_update_xml(
    data_tag: str,
    fields: list[ExtractedField],
    form_type: FormType,
    *,
    transaction_type: str,
    customer_id: str | None = None,
    plan_id: str | None = None,
    year: str | None = None,
    ftw_customer_id: str | None = None,
    ftw_plan_id: str | None = None,
    ftw_seq_no: str | None = None,
    current_values: dict[str, str] | None = None,
    preserve_current_values: bool = False,
    schedule_a_broker_rows: list | None = None,
) -> str:
    settings = get_settings()
    key_id = settings.ftwlink_key_id or "[FTWLINK_KEY_ID]"
    filing_year = year or get_value(fields, "form_5500_part_i_7_plan_year_ending_date") or get_value(fields, "schedule_a_part_iv_4e_plan_year_ending_date") or "[Year]"
    document_xml = _document_xml(
        data_tag,
        fields,
        form_type,
        transaction_type=transaction_type,
        customer_id=customer_id,
        plan_id=plan_id,
        year=filing_year,
        ftw_customer_id=ftw_customer_id,
        ftw_plan_id=ftw_plan_id,
        current_values=current_values,
        ftw_seq_no=ftw_seq_no,
        preserve_current_values=preserve_current_values,
        schedule_a_broker_rows=schedule_a_broker_rows,
    )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <KeyID>{escape(key_id)}</KeyID>
  <DataBatch>
{document_xml or "    <!-- No approved FT Williams fields are available yet. -->"}
  </DataBatch>
</ftwLink>'''


def build_schedule_a_records_update_xml(
    records: list[dict],
    matched_ftw_seq_no: str | None,
    fields: list[ExtractedField],
    *,
    add_new_fields: list[ExtractedField] | None = None,
    new_schedule_desc: str | None = None,
    transaction_type: str = "2",
    customer_id: str | None = None,
    plan_id: str | None = None,
    year: str | None = None,
    ftw_customer_id: str | None = None,
    ftw_plan_id: str | None = None,
    schedule_a_broker_rows: list | None = None,
) -> str:
    settings = get_settings()
    key_id = settings.ftwlink_key_id or "[FTWLINK_KEY_ID]"
    filing_year = year or get_value(fields, "schedule_a_part_iv_4e_plan_year_ending_date") or get_value(fields, "form_5500_part_i_7_plan_year_ending_date") or "[Year]"
    matched_seq = str(matched_ftw_seq_no or "").strip()
    documents: list[str] = []
    for record in sorted(records, key=lambda item: _record_sort_key(item.get("ftw_seq_no"))):
        record_seq = str(record.get("ftw_seq_no") or "").strip()
        update_fields = fields if matched_seq and record_seq == matched_seq else []
        current_values = record.get("query_results") or {}
        if not isinstance(current_values, dict) or not current_values:
            continue
        document_xml = _schedule_a_record_document_xml(
            update_fields,
            current_values,
            transaction_type=transaction_type,
            customer_id=customer_id,
            plan_id=plan_id,
            year=filing_year,
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            schedule_a_broker_rows=schedule_a_broker_rows if update_fields else None,
            query_subparts=record.get("query_subparts") or {},
        )
        if document_xml:
            documents.append(document_xml)
    if add_new_fields:
        new_current_values = {"ScheduleDesc": new_schedule_desc or _schedule_desc_from_fields(add_new_fields, records)}
        document_xml = _schedule_a_record_document_xml(
            add_new_fields,
            new_current_values,
            transaction_type=transaction_type,
            customer_id=customer_id,
            plan_id=plan_id,
            year=filing_year,
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            schedule_a_broker_rows=schedule_a_broker_rows,
        )
        if document_xml and len(update_values_for_form(add_new_fields, FormType.SCHEDULE_A)) > 0:
            documents.append(document_xml)
    joined = "\n".join(documents) or "    <!-- No approved FT Williams Schedule A records are available yet. -->"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <KeyID>{escape(key_id)}</KeyID>
  <DataBatch>
{joined}
  </DataBatch>
</ftwLink>'''


def combine_ftw_update_xml(*documents: str | None) -> str:
    settings = get_settings()
    key_id = settings.ftwlink_key_id or "[FTWLINK_KEY_ID]"
    data_batches = [
        inner
        for document in documents
        if document and (inner := _data_batch_inner(document)).strip() and "No approved FT Williams" not in inner
    ]
    joined = "\n".join(data_batches) or "    <!-- No approved FT Williams fields are available yet. -->"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <KeyID>{escape(key_id)}</KeyID>
  <DataBatch>
{joined}
  </DataBatch>
</ftwLink>'''


def _document_xml(
    data_tag: str,
    fields: list[ExtractedField],
    form_type: FormType,
    *,
    transaction_type: str,
    customer_id: str | None,
    plan_id: str | None,
    year: str | None,
    ftw_customer_id: str | None,
    ftw_plan_id: str | None,
    current_values: dict[str, str] | None,
    ftw_seq_no: str | None,
    preserve_current_values: bool = False,
    schedule_a_broker_rows: list | None = None,
) -> str:
    if preserve_current_values and form_type == FormType.SCHEDULE_A and current_values:
        values = full_replace_values_for_schedule_a(fields, current_values)
    else:
        values = update_values_for_form(fields, form_type, current_values=current_values)
    broker_rows: list[dict[str, str]] = []
    if form_type == FormType.SCHEDULE_A:
        broker_overrides = {
            tag: value
            for tag, value in values.items()
            if _schedule_a_broker_tag_index(tag) is not None
        }
        for tag in broker_overrides:
            values.pop(tag, None)
        if schedule_a_broker_rows:
            broker_overrides.update(schedule_a_broker_update_values(schedule_a_broker_rows))
        broker_rows = schedule_a_broker_multipart_rows(
            current_values or {},
            overrides=broker_overrides,
        )
    if not values:
        if not broker_rows:
            return ""

    xml_lines = [
        f"      <TransactionType>{escape(transaction_type)}</TransactionType>",
        "      <EditCheck>0</EditCheck>",
        *_identifier_xml(customer_id, plan_id, ftw_customer_id, ftw_plan_id),
        f"      <Year>{escape(str(year or '[Year]'))}</Year>",
    ]
    xml_lines.extend(f"      <{tag}>{escape(value)}</{tag}>" for tag, value in sorted(values.items()))
    xml_lines.extend(_schedule_a_subpart_xml_lines(broker_rows))
    joined = "\n".join(xml_lines)
    return f"""    <{data_tag}>
{joined}
    </{data_tag}>"""


def _schedule_a_record_document_xml(
    fields: list[ExtractedField],
    current_values: dict[str, str],
    *,
    transaction_type: str,
    customer_id: str | None,
    plan_id: str | None,
    year: str | None,
    ftw_customer_id: str | None,
    ftw_plan_id: str | None,
    schedule_a_broker_rows: list | None = None,
    query_subparts: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    values = full_replace_values_for_schedule_a(fields, current_values, force_current_values=True)
    broker_overrides = {
        tag: value
        for tag, value in values.items()
        if _schedule_a_broker_tag_index(tag) is not None
    }
    for tag in broker_overrides:
        values.pop(tag, None)
    if schedule_a_broker_rows:
        broker_overrides.update(schedule_a_broker_update_values(schedule_a_broker_rows))
    broker_rows = schedule_a_broker_multipart_rows(
        current_values,
        query_subparts=query_subparts,
        overrides=broker_overrides,
    )
    if not values and not broker_rows:
        return ""
    xml_lines = [
        f"      <TransactionType>{escape(transaction_type)}</TransactionType>",
        "      <EditCheck>0</EditCheck>",
        *_identifier_xml(customer_id, plan_id, ftw_customer_id, ftw_plan_id),
        f"      <Year>{escape(str(year or '[Year]'))}</Year>",
    ]
    xml_lines.extend(f"      <{tag}>{escape(value)}</{tag}>" for tag, value in sorted(values.items()))
    xml_lines.extend(_schedule_a_subpart_xml_lines(broker_rows))
    joined = "\n".join(xml_lines)
    return f"""    <DOLScheduleAData>
{joined}
    </DOLScheduleAData>"""


def update_values_for_form(
    fields: list[ExtractedField],
    form_type: FormType,
    *,
    current_values: dict[str, str] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields:
        if field.form_type != form_type or field.priority == FieldPriority.IGNORE:
            continue
        indicator_values = _form_5500_indicator_group_values(field, current_values=current_values)
        if indicator_values is not None:
            if current_values is not None:
                current_value = resolve_ftw_current_value(field, current_values)
                proposed_value = _indicator_group_summary(field, indicator_values)
                if not values_meaningfully_different(current_value, proposed_value):
                    continue
            values.update(indicator_values)
            continue
        tag = resolve_ftw_update_tag(field)
        if not tag:
            continue
        proposed = normalize_ftw_update_value(form_type, tag, field.proposed_value)
        if not proposed:
            continue
        if current_values is not None:
            current_value = _normalize_ftw_xml_value(tag, resolve_ftw_current_value(field, current_values))
            if not values_meaningfully_different(current_value, proposed, tag=tag):
                continue
        values[tag] = proposed
    return {tag: str(value or "") for tag, value in values.items() if str(value or "").strip()}


def full_replace_values_for_schedule_a(
    fields: list[ExtractedField],
    current_values: dict[str, str],
    *,
    force_current_values: bool = False,
) -> dict[str, str]:
    use_canonical_carrier = _schedule_a_identity_matches_current(fields, current_values)
    overlay_fields = [
        field
        for field in fields
        if not (
            use_canonical_carrier
            and str(field.mapped_rule_key or "") == "schedule_a_part_i_1a_name_of_insurance_company"
        )
    ]
    changed_values = update_values_for_form(overlay_fields, FormType.SCHEDULE_A, current_values=current_values)
    if not changed_values and not force_current_values:
        return {}

    values = current_values_for_schedule_a_update(current_values)
    if force_current_values and not fields:
        return values
    proposed_values = update_values_for_form(overlay_fields, FormType.SCHEDULE_A)
    if use_canonical_carrier:
        canonical_carrier = str(
            current_values.get("InsCarrierName")
            or current_values.get("INS_CARRIER_NAME")
            or ""
        ).strip()
        if canonical_carrier:
            proposed_values["InsCarrierName"] = canonical_carrier
    values.update(proposed_values)
    return {tag: str(value or "") for tag, value in values.items() if str(value or "").strip()}


def _schedule_a_identity_matches_current(
    fields: list[ExtractedField],
    current_values: dict[str, str],
) -> bool:
    proposed_by_rule = {
        str(field.mapped_rule_key or ""): str(field.proposed_value or field.value or "").strip()
        for field in fields
        if field.form_type == FormType.SCHEDULE_A
    }
    identity_pairs = [
        (
            proposed_by_rule.get("schedule_a_part_i_1b_insurance_carrier_ein"),
            current_values.get("InsCarrierEIN") or current_values.get("INS_CARRIER_EIN"),
            lambda value: re.sub(r"\D", "", str(value or "")),
            True,
        ),
        (
            proposed_by_rule.get("schedule_a_part_i_1c_naic_code"),
            current_values.get("InsCarrierNAICCode") or current_values.get("INS_CARRIER_NAIC_CODE"),
            lambda value: re.sub(r"\D", "", str(value or "")).lstrip("0"),
            True,
        ),
        (
            proposed_by_rule.get("schedule_a_part_i_1d_contract_policy_number"),
            current_values.get("InsContractNum") or current_values.get("INS_CONTRACT_NUM"),
            lambda value: re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper().lstrip("0"),
            False,
        ),
    ]
    has_carrier_identifier_match = False
    for proposed, current, normalize, carrier_identifier in identity_pairs:
        if not proposed or not current:
            continue
        if normalize(proposed) != normalize(current):
            return False
        if carrier_identifier:
            has_carrier_identifier_match = True
    return has_carrier_identifier_match


def current_values_for_schedule_a_update(current_values: dict[str, str]) -> dict[str, str]:
    current_to_update_tags = {
        current_tag: SCHEDULE_A_TAGS_BY_RULE[rule_key]
        for rule_key, current_tag in SCHEDULE_A_CURRENT_TAGS_BY_RULE.items()
        if rule_key in SCHEDULE_A_TAGS_BY_RULE
    }
    values: dict[str, str] = {}
    for tag, current_value in current_values.items():
        update_tag = current_to_update_tags.get(tag, tag)
        if update_tag in SCHEDULE_A_REPLACEMENT_CONTROL_TAGS:
            continue
        if _schedule_a_broker_tag_index(update_tag) is not None:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", update_tag):
            raise ValueError(f"FT Williams returned an invalid Schedule A field name: {update_tag}")
        normalized = _normalize_ftw_xml_value(update_tag, current_value)
        if str(normalized or "").strip():
            values[update_tag] = normalized
    return {tag: str(value or "") for tag, value in values.items() if str(value or "").strip()}


def schedule_a_broker_update_values(rows: list) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, row in enumerate(rows or [], start=1):
        row_values = _schedule_a_broker_row_update_values(row, index)
        if row_values:
            values.update(row_values)
    return values


def schedule_a_broker_multipart_rows(
    current_values: dict[str, str],
    *,
    query_subparts: dict[str, list[dict[str, str]]] | None = None,
    overrides: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    source_rows = list((query_subparts or {}).get("Broker") or [])
    if not source_rows:
        source_rows = _broker_rows_from_flat_values(current_values)

    rows: list[dict[str, str]] = []
    for index, source in enumerate(source_rows, start=1):
        multipart: dict[str, str] = {}
        for tag, value in source.items():
            parsed = _schedule_a_broker_tag_index(tag)
            text = str(value or "").strip()
            if not parsed or not text:
                continue
            _, field_index = parsed
            if field_index != index:
                raise ValueError(
                    f"FT Williams broker field {tag} belongs to row {field_index}, not row {index}."
                )
            multipart[_schedule_a_broker_multipart_tag(tag)] = text
        rows.append(multipart)

    for tag, value in (overrides or {}).items():
        parsed = _schedule_a_broker_tag_index(tag)
        if not parsed:
            continue
        _, index = parsed
        while len(rows) < index:
            rows.append({})
        text = str(value or "").strip()
        multipart_tag = _schedule_a_broker_multipart_tag(tag)
        if text:
            rows[index - 1][multipart_tag] = text
        else:
            rows[index - 1].pop(multipart_tag, None)
    return [row for row in rows if row]


def schedule_a_replacement_data_gaps(records: list[dict], xml: str | None) -> list[str]:
    if not xml:
        return ["Schedule A replacement XML is missing"]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ["Schedule A replacement XML is malformed"]
    documents = root.findall(".//DOLScheduleAData")
    ordered_records = sorted(records or [], key=lambda item: _record_sort_key(item.get("ftw_seq_no")))
    gaps: list[str] = []
    if len(documents) < len(ordered_records):
        gaps.append(
            f"replacement contains {len(documents)} record(s) for {len(ordered_records)} current record(s)"
        )
    for record, document in zip(ordered_records, documents):
        sequence = str(record.get("ftw_seq_no") or "?").strip() or "?"
        current_values = record.get("query_results") or {}
        expected_fields = current_values_for_schedule_a_update(current_values)
        actual_fields = {
            child.tag
            for child in list(document)
            if child.tag != "DOLSubPartData" and str(child.text or "").strip()
        }
        for tag in sorted(set(expected_fields) - actual_fields):
            gaps.append(f"sequence {sequence} missing field {tag}")

        expected_brokers = schedule_a_broker_multipart_rows(
            current_values,
            query_subparts=record.get("query_subparts") or {},
        )
        actual_brokers = [
            {
                child.tag: str(child.text or "").strip()
                for child in list(broker)
                if str(child.text or "").strip()
            }
            for broker in document.findall("./DOLSubPartData/Broker")
        ]
        if len(actual_brokers) < len(expected_brokers):
            gaps.append(
                f"sequence {sequence} has {len(actual_brokers)} broker row(s) for {len(expected_brokers)} current row(s)"
            )
        for index, expected_broker in enumerate(expected_brokers):
            actual_broker = actual_brokers[index] if index < len(actual_brokers) else {}
            for tag in sorted(set(expected_broker) - set(actual_broker)):
                gaps.append(f"sequence {sequence} missing broker row {index + 1} field {tag}")
    return gaps


def _broker_rows_from_flat_values(current_values: dict[str, str]) -> list[dict[str, str]]:
    grouped: dict[int, dict[str, str]] = {}
    for tag, value in current_values.items():
        parsed = _schedule_a_broker_tag_index(tag)
        text = str(value or "").strip()
        if not parsed or not text:
            continue
        _, index = parsed
        grouped.setdefault(index, {})[tag] = text
    if not grouped:
        return []
    rows: list[dict[str, str]] = []
    for index in range(1, max(grouped) + 1):
        rows.append(grouped.get(index, {}))
    return rows


def _schedule_a_broker_tag_index(tag: object) -> tuple[str, int] | None:
    text = str(tag or "").strip()
    for base in SCHEDULE_A_BROKER_FIELD_BASES:
        if not text.startswith(base):
            continue
        suffix = text[len(base):]
        if not re.fullmatch(r"\d{1,2}", suffix):
            continue
        index = int(suffix)
        if index > 0:
            return base, index
    return None


def _schedule_a_broker_multipart_tag(tag: object) -> str:
    parsed = _schedule_a_broker_tag_index(tag)
    if not parsed:
        raise ValueError(f"Unsupported FT Williams Schedule A broker field: {tag}")
    base, _ = parsed
    return f"{base}XX"


def _schedule_a_subpart_xml_lines(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    lines = ["      <DOLSubPartData>"]
    for row in rows:
        lines.append("        <Broker>")
        lines.extend(
            f"          <{tag}>{escape(value)}</{tag}>"
            for tag, value in sorted(row.items())
            if str(value or "").strip()
        )
        lines.append("        </Broker>")
    lines.append("      </DOLSubPartData>")
    return lines


def _schedule_a_broker_row_update_values(row: object, index: int) -> dict[str, str]:
    name = _broker_row_attr(row, "name")
    commission = _broker_row_attr(row, "commission_total")
    fees = _broker_row_attr(row, "fee_total")
    code = _broker_row_attr(row, "organization_code")
    purpose = _broker_row_purpose(row, commission, fees)

    values = {
        f"Name{index}": name,
        f"CommPdAmt{index}": commission,
        f"FeesPdAmt{index}": fees,
        f"FeesPdText{index}": purpose,
        f"Code{index}": code,
    }
    normalized: dict[str, str] = {}
    for tag, value in values.items():
        text = normalize_ftw_update_value(FormType.SCHEDULE_A, tag, value)
        if str(text or "").strip():
            normalized[tag] = str(text)
    return normalized


def _broker_row_attr(row: object, key: str) -> str:
    if hasattr(row, key):
        return str(getattr(row, key) or "").strip()
    if isinstance(row, dict):
        return str(row.get(key) or "").strip()
    return ""


def _broker_row_purpose(row: object, commission: str, fees: str) -> str:
    commission_amount = _money_to_float(commission)
    fee_amount = _money_to_float(fees)
    if commission_amount > 0 and fee_amount > 0:
        return "COMMISSIONS AND FEES"
    if commission_amount > 0:
        return "COMMISSIONS"
    if fee_amount > 0:
        return "FEES"
    return _first_money_row_purpose(row, "commission_rows") or _first_money_row_purpose(row, "fee_rows")


def _first_money_row_purpose(row: object, key: str) -> str:
    rows = getattr(row, key, None)
    if rows is None and isinstance(row, dict):
        rows = row.get(key)
    for item in rows or []:
        purpose = getattr(item, "purpose", None)
        if purpose is None and isinstance(item, dict):
            purpose = item.get("purpose")
        text = str(purpose or "").strip()
        if text:
            return text.upper()
    return ""


def _money_to_float(value: object) -> float:
    text = str(value or "").strip()
    if not text or text.upper() in {"N/A", "NA", "NOT APPLICABLE"}:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", "."}:
        return 0.0
    try:
        amount = float(cleaned)
    except ValueError:
        return 0.0
    return -amount if negative else amount


def _record_sort_key(value: object) -> tuple[int, str]:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text), text
    return 10_000, text


def _schedule_desc_from_fields(fields: list[ExtractedField], records: list[dict]) -> str:
    existing = {
        str((record.get("query_results") or {}).get("ScheduleDesc") or "").strip().upper()
        for record in records
        if isinstance(record.get("query_results"), dict)
    }
    carrier = get_value(fields, "schedule_a_part_i_1a_name_of_insurance_company") or "SCHEDULE"
    base = re.sub(r"[^A-Z0-9]", "", str(carrier).upper())[:8] or "SCHEDULE"
    candidate = base[:8]
    if candidate not in existing:
        return candidate
    stem = base[:7] or "SCHEDUL"
    for index in range(1, 10):
        candidate = f"{stem}{index}"[:8]
        if candidate not in existing:
            return candidate
    return base[:6] + "99"


def _data_batch_inner(document: str) -> str:
    match = re.search(r"<DataBatch>\s*(.*?)\s*</DataBatch>", document, flags=re.DOTALL)
    return match.group(1) if match else ""


def _identifier_xml(
    customer_id: str | None,
    plan_id: str | None,
    ftw_customer_id: str | None,
    ftw_plan_id: str | None,
) -> list[str]:
    if ftw_customer_id and ftw_plan_id:
        return [
            f"      <FTWCustomerID>{escape(ftw_customer_id)}</FTWCustomerID>",
            f"      <FTWPlanID>{escape(ftw_plan_id)}</FTWPlanID>",
        ]
    if customer_id and plan_id:
        return [
            f"      <CustomerID>{escape(customer_id)}</CustomerID>",
            f"      <PlanID>{escape(plan_id)}</PlanID>",
        ]
    return ["      <!-- CustomerID/PlanID or FTWCustomerID/FTWPlanID required before sending to FT Williams. -->"]


def get_value(fields: list[ExtractedField], rule_key: str) -> str | None:
    for field in fields:
        if field.mapped_rule_key == rule_key:
            return field.proposed_value
    return None


def _normalize_ftw_xml_value(tag: str, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if tag in FTW_ONE_TWO_BOOLEAN_TAGS:
        normalized_text = text.lower()
        if normalized_text in {"1", "y", "yes", "true"}:
            return "1"
        if normalized_text in {"0", "2", "n", "no", "false"}:
            return "2"
    if tag in FTW_INDICATOR_TAGS:
        normalized_text = text.lower()
        if normalized_text in {"1", "y", "yes", "true", "insurance"}:
            return "1"
        if normalized_text in {"0", "n", "no", "false"}:
            return "0"
    if tag in FTW_DATE_TAGS:
        normalized = _normalize_ftw_date(text)
        if normalized:
            return normalized
    return text


def _form_5500_indicator_group_values(
    field: ExtractedField,
    *,
    current_values: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if field.form_type != FormType.FORM_5500:
        return None
    group = FORM_5500_INDICATOR_GROUPS.get(str(field.mapped_rule_key or ""))
    if not group:
        return None
    proposed = str(field.proposed_value or "").strip()
    if not proposed:
        return {}
    normalized = proposed.lower()
    normalized_parts = [
        part.strip()
        for part in normalized.replace(";", ",").split(",")
        if part.strip()
    ]
    values: dict[str, str] = {}
    for tag, needles in group:
        values[tag] = "1" if _indicator_selected(needles, normalized, normalized_parts) else "0"
    return values


def _indicator_selected(needles: tuple[str, ...], normalized: str, normalized_parts: list[str]) -> bool:
    if "insurance" in needles:
        return any(part == "insurance" for part in normalized_parts) or normalized == "insurance"
    if "trust" in needles:
        return any(part == "trust" for part in normalized_parts) or normalized == "trust"
    return any(needle in normalized for needle in needles)


def _indicator_group_summary(field: ExtractedField, values: dict[str, str]) -> str:
    group = FORM_5500_INDICATOR_GROUPS.get(str(field.mapped_rule_key or ""), [])
    labels = []
    for tag, needles in group:
        if values.get(tag) != "1":
            continue
        label = needles[0]
        if "general asset" in label or label == "sponsor":
            label = "General assets of the sponsor"
        elif label == "412":
            label = "Code section 412(e)(3) insurance contracts"
        elif label == "insurance":
            label = "Insurance"
        elif label == "trust":
            label = "Trust"
        labels.append(label)
    return ", ".join(labels)


def _normalize_ftw_date(value: str) -> str | None:
    for pattern in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return None
