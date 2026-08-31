"""Canonical validation and resolution for Schedule A extraction results.

Extractor adapters may propose values, but this module decides whether those
values are safe enough to use automatically.  It deliberately consumes the
existing public ``NormalizedExtractionResult`` so providers and specialized
parsers can migrate incrementally.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models import (
    ExtractionValidationResult,
    FieldRule,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerRow,
    SourceEvidence,
)


REVIEW_CONFIDENCE_CEILING = 0.5
REQUIRED_SCHEDULE_A_HEADER_FIELDS = (
    ("1a.", "1a. Name of Insurance Company"),
    ("1b.", "1b. Insurance Carrier EIN"),
    ("1c.", "1c. NAIC Code"),
    ("1d.", "1d. Contract/Policy Number"),
    ("1e.", "1e. Persons Covered (End of Policy Year)"),
    ("1f.", "1f. Policy Year Beginning Date"),
    ("1g.", "1g. Policy Year Ending Date"),
)
_BROKER_NOISE_NAMES = {
    "agent",
    "broker",
    "name",
    "recipient",
    "service provider",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}


def apply_schedule_a_pipeline(
    result: NormalizedExtractionResult,
    *,
    authoritative: bool,
    shadow: bool = True,
    rules: list[FieldRule] | None = None,
) -> NormalizedExtractionResult:
    """Run canonical resolution authoritatively or as a no-risk shadow diff."""
    if authoritative:
        return resolve_schedule_a_result(result, rules=rules)
    if not shadow:
        return result
    candidate = resolve_schedule_a_result(result.model_copy(deep=True), rules=rules)
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    raw["canonical_shadow_quality"] = candidate.raw["extraction_quality"]
    result.raw = raw
    return result


def resolve_schedule_a_result(
    result: NormalizedExtractionResult,
    *,
    rules: list[FieldRule] | None = None,
) -> NormalizedExtractionResult:
    """Attach evidence, validate values, and fail closed on contradictions."""
    error_fields: set[str] = set()
    for field in result.fields:
        if not field.evidence and (field.page or field.source_text):
            field.evidence.append(
                SourceEvidence(
                    provider=result.provider,
                    page=field.page,
                    source_text=field.source_text,
                )
            )
        validations = _validate_field(field, rules=rules)
        validations.append(_validate_candidate_consistency(field))
        persons_semantics = _validate_persons_covered_semantics(field)
        if persons_semantics is not None:
            validations.append(persons_semantics)
        section_context = _validate_financial_section_context(field)
        if section_context is not None:
            validations.append(section_context)
        validations.append(_validate_source_evidence(field))
        field.validation_results = validations
        if any(item.status == "ERROR" for item in validations):
            field.confidence = min(float(field.confidence or 0), REVIEW_CONFIDENCE_CEILING)
            field.decision = "REVIEW_REQUIRED"
            error_fields.add(field.field_name)
        else:
            field.decision = "AUTOMATIC" if float(field.confidence or 0) >= 0.8 else "REVIEW_REQUIRED"

    _validate_date_order(result.fields, error_fields)
    cross_field_errors = _validate_cross_section_duplicates(result.fields)
    cross_field_errors.extend(_validate_broker_rows(result, result.schedule_a_broker_rows))
    semantic_resolution = (
        result.raw.get("semantic_resolution", {})
        if isinstance(result.raw, dict)
        else {}
    )
    if int(semantic_resolution.get("group_count") or 0) > 1:
        cross_field_errors.append("multiple_schedule_a_policy_groups")
        _mark_fields_review(
            result.fields,
            ("",),
            validator="policy_group_isolation",
            reason=(
                "The source contains multiple independently completed Schedule A policy groups. "
                "A reviewer must select a group before scalar fields can be used."
            ),
        )
    semantic_ambiguities = semantic_resolution.get("ambiguities", [])
    if isinstance(semantic_ambiguities, list) and any(
        isinstance(item, dict) and item.get("type") == "combined_commission_fee_source"
        for item in semantic_ambiguities
    ):
        cross_field_errors.append("combined_commission_fee_source")
        _mark_fields_review(
            result.fields,
            ("3b.", "3c."),
            validator="combined_commission_fee_source",
            reason=(
                "The source reports one combined commissions/fees amount, so it cannot "
                "be assigned automatically to both separate FT Williams fields."
            ),
        )
    present_names = {field.field_name.strip().lower() for field in result.fields if str(field.value or "").strip()}
    normalized_present_names = {
        re.sub(r"[^a-z0-9]+", " ", name).strip()
        for name in present_names
    }
    missing_required_fields = [
        label
        for prefix, label in REQUIRED_SCHEDULE_A_HEADER_FIELDS
        if not any(name.startswith(prefix) for name in present_names)
    ]
    for rule in rules or []:
        normalized_label = re.sub(r"[^a-z0-9]+", " ", rule.label.lower()).strip()
        if rule.required and normalized_label not in normalized_present_names and rule.label not in missing_required_fields:
            missing_required_fields.append(rule.label)

    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    review_fields = sorted(
        field.field_name for field in result.fields if field.decision == "REVIEW_REQUIRED"
    )
    raw["extraction_quality"] = {
        "decision": "REVIEW_REQUIRED" if review_fields or cross_field_errors or missing_required_fields else "AUTOMATIC",
        "error_count": len(error_fields) + len(cross_field_errors) + len(missing_required_fields),
        "error_fields": sorted(error_fields),
        "missing_required_fields": missing_required_fields,
        "cross_field_errors": cross_field_errors,
        "review_fields": review_fields,
        "provider": result.provider,
    }
    result.raw = raw
    return result


def _validate_field(
    field: NormalizedExtractionField,
    *,
    rules: list[FieldRule] | None = None,
) -> list[ExtractionValidationResult]:
    name = field.field_name.strip().lower()
    value = str(field.value or "").strip()
    configured = _configured_rule(field.field_name, rules)
    configured_validators = [str(item).strip().lower() for item in (configured.validators if configured else []) if str(item).strip()]
    if configured and not configured_validators:
        inferred = _validator_from_field_type(configured.field_type)
        configured_validators = [inferred] if inferred else []
    if configured_validators:
        validations = [_run_named_validator(validator_name, value) for validator_name in configured_validators]
        if configured and not configured.automatic_update_allowed:
            validations.append(
                ExtractionValidationResult(
                    validator="automatic_update_policy",
                    status="ERROR",
                    reason="This field rule requires reviewer confirmation before update.",
                )
            )
        return validations
    validator = None
    if name.startswith("1a."):
        validator = _validate_carrier_name
    elif name.startswith("1b."):
        validator = _validate_ein
    elif name.startswith("1c."):
        validator = _validate_naic
    elif name.startswith("1d."):
        validator = _validate_contract
    elif name.startswith("1e."):
        validator = _validate_persons
    elif name.startswith(("1f.", "1g.")):
        validator = _validate_date
    if validator is None:
        return [ExtractionValidationResult(validator="presence", status="PASS" if value else "ERROR", reason="Value is present." if value else "Value is blank.")]
    return [validator(value)]


def _validate_source_evidence(
    field: NormalizedExtractionField,
) -> ExtractionValidationResult:
    pages = [field.page, *(item.page for item in field.evidence)]
    source_texts = [field.source_text, *(item.source_text for item in field.evidence)]
    has_page = any(isinstance(page, int) and page > 0 for page in pages)
    has_source_text = any(bool(str(text or "").strip()) for text in source_texts)
    supports_value = any(
        _source_text_supports_value(field.value, str(text or ""))
        for text in source_texts
        if str(text or "").strip()
    )
    valid = has_page and has_source_text and supports_value
    return ExtractionValidationResult(
        validator="source_evidence",
        status="PASS" if valid else "ERROR",
        reason=(
            "Field has page-level source evidence containing the extracted value."
            if valid
            else "Automatic extraction requires a source page and source text that supports the extracted value."
        ),
    )


def _source_text_supports_value(value: str, source_text: str) -> bool:
    value_text = re.sub(r"\s+", " ", str(value or "")).strip()
    source = re.sub(r"\s+", " ", str(source_text or "")).strip()
    if not value_text or not source:
        return False

    value_digits = re.sub(r"\D", "", value_text)
    source_digits = re.sub(r"\D", "", source)
    if len(value_digits) >= 4 and value_digits in source_digits:
        return True

    normalized_value_date = _normalized_date(value_text)
    if normalized_value_date:
        for candidate in re.findall(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", source):
            if _normalized_date(candidate) == normalized_value_date:
                return True
        for candidate in re.findall(
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+\d{1,2},?\s+\d{4}\b",
            source,
            flags=re.IGNORECASE,
        ):
            for pattern in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
                try:
                    if datetime.strptime(candidate, pattern).strftime("%m/%d/%Y") == normalized_value_date:
                        return True
                except ValueError:
                    continue

    value_key = re.sub(r"[^a-z0-9]+", " ", value_text.casefold()).strip()
    source_key = re.sub(r"[^a-z0-9]+", " ", source.casefold()).strip()
    return bool(value_key and value_key in source_key)


def _validate_candidate_consistency(
    field: NormalizedExtractionField,
) -> ExtractionValidationResult:
    candidates = {
        _candidate_identity(field.field_name, str(value or ""))
        for value in [field.value, *field.candidate_values]
        if str(value or "").strip()
    }
    valid = len(candidates) <= 1
    return ExtractionValidationResult(
        validator="candidate_conflict",
        status="PASS" if valid else "ERROR",
        reason=(
            "All extracted candidates agree."
            if valid
            else "Multiple source candidates disagree for this field."
        ),
    )


def _candidate_identity(field_name: str, value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    label = field_name.strip().lower()
    if _is_currency_amount(clean):
        amount = parse_decimal(clean)
        if amount is not None:
            return f"amount:{amount.normalize()}"
    if label.startswith(("1f.", "1g.")) or "date" in label:
        normalized_date = _normalized_date(clean)
        if normalized_date:
            return f"date:{normalized_date}"
    if label.startswith("1e."):
        digits = clean.replace(",", "")
        if digits.isdigit():
            return f"integer:{int(digits)}"
    if label.startswith("1b."):
        digits = re.sub(r"\D", "", clean)
        if len(digits) == 9:
            return f"ein:{digits}"
    if label.startswith("1c."):
        digits = re.sub(r"\D", "", clean)
        if 4 <= len(digits) <= 6:
            return f"naic:{digits}"
    return f"text:{clean.casefold()}"


def _validate_persons_covered_semantics(
    field: NormalizedExtractionField,
) -> ExtractionValidationResult | None:
    if not field.field_name.strip().lower().startswith("1e."):
        return None
    extracted = str(field.value or "").replace(",", "").strip()
    evidence_text = " ".join(
        text
        for text in [field.source_text, *(item.source_text for item in field.evidence)]
        if text
    )
    normalized = re.sub(r"\s+", " ", evidence_text).strip().lower()
    explicit_totals = {
        match.group(1).replace(",", "")
        for pattern in (
            r"(?:total\s+)?(?:persons?|people|lives?)\s+covered(?:\s+at\s+(?:the\s+)?end)?\D{0,24}([\d,]+)",
            r"total\s+covered\s+(?:persons?|people|lives?)\D{0,24}([\d,]+)",
            r"(?:subscribers?|employees?)\s*(?:\+|plus|and)\s*dependents?\D{0,24}([\d,]+)",
        )
        for match in re.finditer(pattern, normalized, re.IGNORECASE)
    }
    if re.search(r"\b(?:subscribers?|members?|dependents?)\b", normalized):
        explicit_totals.update(
            match.group(1).replace(",", "")
            for pattern in (
                r"\b[\d,]+\s*/\s*([\d,]+)\b",
                r"\b[\d,]+\s+([\d,]+)\s+\d{9}\s+\d{4,6}\s+\$",
            )
            for match in re.finditer(pattern, normalized, re.IGNORECASE)
        )
    tier_terms_present = bool(
        re.search(r"\b(?:subscribers?|employees?|dependents?|enrollment\s+tier)\b", normalized)
    )
    conflicts_with_explicit_total = bool(explicit_totals and extracted not in explicit_totals)
    ambiguous_tiers = tier_terms_present and not explicit_totals
    valid = not conflicts_with_explicit_total and not ambiguous_tiers
    return ExtractionValidationResult(
        validator="persons_covered_semantics",
        status="PASS" if valid else "ERROR",
        reason=(
            "Persons-covered value agrees with its source context."
            if valid
            else "Enrollment tiers or an explicit covered-lives total do not support this persons-covered value."
        ),
    )


def _validate_financial_section_context(
    field: NormalizedExtractionField,
) -> ExtractionValidationResult | None:
    label = field.field_name.strip().lower()
    is_experience = label.startswith("9")
    is_nonexperience = label.startswith("10")
    if not is_experience and not is_nonexperience:
        return None
    evidence_text = " ".join(
        text
        for text in [field.source_text, *(item.source_text for item in field.evidence)]
        if text
    ).lower()
    normalized = re.sub(r"\s+", " ", evidence_text)
    if is_experience:
        valid = _has_explicit_experience_evidence(field)
        expected = "Part II/experience-rated/line 9"
    else:
        valid = bool(
            re.search(r"\bnon[- ]?experience[- ]rated\b", normalized)
            or re.search(r"\b(?:part\s+iii\s+)?line\s+10[a-z]?\b", normalized)
            or re.search(r"\b10[a-z]\s*[.(]", normalized)
            or (
                "payments received by carrier from plan" in normalized
                and re.search(r"\btotal\s*:\s*\$", normalized)
            )
        )
        expected = "nonexperience-rated/line 10"
    return ExtractionValidationResult(
        validator="section_context",
        status="PASS" if valid else "ERROR",
        reason=(
            "Source evidence identifies the matching Schedule A financial section."
            if valid
            else f"Automatic extraction requires explicit {expected} source evidence."
        ),
    )


def _configured_rule(field_name: str, rules: list[FieldRule] | None) -> FieldRule | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(field_name or "").lower()).strip()
    for rule in rules or []:
        names = [rule.label, *rule.aliases]
        if normalized in {
            re.sub(r"[^a-z0-9]+", " ", str(candidate or "").lower()).strip()
            for candidate in names
        }:
            return rule
    return None


def _validator_from_field_type(field_type: str | None) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(field_type or "").lower()).strip("_")
    aliases = {
        "amount": "currency",
        "money": "currency",
        "number": "integer",
        "whole_number": "integer",
        "contract": "contract_id",
        "contract_id": "contract_id",
        "ein": "ein",
        "naic": "naic",
        "date": "date",
        "currency": "currency",
        "integer": "integer",
        "boolean": "boolean",
        "address": "address",
        "enum": "enum",
        "text": "text",
        "organization_code": "organization_code",
    }
    return aliases.get(normalized)


def _run_named_validator(name: str, value: str) -> ExtractionValidationResult:
    validators = {
        "ein": _validate_ein,
        "naic": _validate_naic,
        "contract_id": _validate_contract,
        "date": _validate_date,
        "integer": _validate_integer,
        "currency": _validate_currency,
        "boolean": _validate_boolean,
        "organization_code": _validate_organization_code,
        "text": _validate_text,
        "address": _validate_text,
        "enum": _validate_text,
    }
    validator = validators.get(name)
    if validator is None:
        return _result(name, False, f"Unknown configured validator: {name}.")
    return validator(value)


def _result(validator: str, valid: bool, reason: str, normalized: str | None = None) -> ExtractionValidationResult:
    return ExtractionValidationResult(
        validator=validator,
        status="PASS" if valid else "ERROR",
        reason=reason,
        normalized_value=normalized if valid else None,
    )


def _validate_ein(value: str) -> ExtractionValidationResult:
    valid = bool(re.fullmatch(r"\d{2}-\d{7}", value))
    return _result("carrier_ein", valid, "Valid carrier EIN." if valid else "Expected carrier EIN in NN-NNNNNNN format.", value)


def _validate_carrier_name(value: str) -> ExtractionValidationResult:
    clean = re.sub(r"\s+", " ", value).strip()
    lower = clean.casefold()
    looks_like_sentence_fragment = bool(
        re.match(r"^(during|is|was|were|has|have|providing|paid|total)\b", lower)
        or "hereby certifies" in lower
    )
    contains_numeric_noise = bool(
        re.search(r"\$\s*\d", clean)
        or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)
    )
    valid = (
        2 <= len(clean) <= 160
        and not looks_like_sentence_fragment
        and not contains_numeric_noise
        and bool(re.search(r"[A-Za-z]", clean))
    )
    return _result(
        "carrier_name",
        valid,
        "Valid insurance carrier name." if valid else "Value looks like narrative, a table row, or another non-carrier value.",
        clean,
    )


def _validate_naic(value: str) -> ExtractionValidationResult:
    clean = value.replace(" ", "")
    valid = bool(re.fullmatch(r"\d{5}", clean))
    return _result("naic", valid, "Valid NAIC company code." if valid else "Expected a five-digit NAIC company code.", clean)


def _validate_contract(value: str) -> ExtractionValidationResult:
    clean = re.sub(r"\s+", " ", value).strip()
    looks_like_date = bool(re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", clean))
    valid = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./_-]{1,79}", clean)) and not looks_like_date
    placeholder = clean.casefold().rstrip("#:. ")
    valid = valid and placeholder not in {
        "contract",
        "contract number",
        "contract/policy number",
        "policy number",
        "type",
        "type of coverage",
        "group number",
        "coverage",
        "see above",
        "same as above",
        "on file",
        "not provided",
        "n/a",
        "na",
        "multiple",
    }
    return _result("contract_identifier", valid, "Valid contract identifier." if valid else "Value is a heading, date, or invalid contract identifier.", clean)


def _validate_persons(value: str) -> ExtractionValidationResult:
    clean = value.replace(",", "").strip()
    valid = bool(re.fullmatch(r"\d+", clean)) and 0 < int(clean) <= 5_000_000
    return _result("persons_covered", valid, "Valid persons-covered count." if valid else "Expected a positive whole-number persons-covered count.", clean)


def _validate_integer(value: str) -> ExtractionValidationResult:
    clean = value.replace(",", "").strip()
    valid = bool(re.fullmatch(r"-?\d+", clean))
    return _result("integer", valid, "Valid whole number." if valid else "Expected a whole number.", clean)


def _validate_currency(value: str) -> ExtractionValidationResult:
    valid = _is_currency_amount(value)
    normalized = parse_decimal(value)
    return _result(
        "currency",
        valid and normalized is not None,
        "Valid currency amount." if valid else "Expected a valid currency amount.",
        format(normalized, "f") if valid and normalized is not None else None,
    )


def _validate_boolean(value: str) -> ExtractionValidationResult:
    clean = value.strip().lower()
    valid = clean in {"true", "false", "yes", "no", "y", "n", "1", "0", "x"}
    return _result("boolean", valid, "Valid boolean value." if valid else "Expected yes/no or true/false.", clean)


def _validate_organization_code(value: str) -> ExtractionValidationResult:
    clean = value.strip()
    valid = clean in {"1", "2", "3", "4", "5", "6"}
    return _result("organization_code", valid, "Valid organization code." if valid else "Expected a supported organization code (1-6).", clean)


def _validate_text(value: str) -> ExtractionValidationResult:
    clean = re.sub(r"\s+", " ", value).strip()
    valid = bool(clean) and len(clean) <= 500
    return _result("text", valid, "Valid text value." if valid else "Expected nonblank text under 500 characters.", clean)


def _validate_date(value: str) -> ExtractionValidationResult:
    normalized = _normalized_date(value)
    return _result("schedule_a_date", normalized is not None, "Valid date." if normalized else "Expected a valid policy date.", normalized)


def _normalized_date(value: str) -> str | None:
    for pattern in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return None


def _validate_date_order(fields: list[NormalizedExtractionField], error_fields: set[str]) -> None:
    beginning = next((field for field in fields if field.field_name.strip().lower().startswith("1f.")), None)
    ending = next((field for field in fields if field.field_name.strip().lower().startswith("1g.")), None)
    if not beginning or not ending:
        return
    begin_value = _normalized_date(beginning.value)
    end_value = _normalized_date(ending.value)
    if not begin_value or not end_value:
        return
    if datetime.strptime(begin_value, "%m/%d/%Y") <= datetime.strptime(end_value, "%m/%d/%Y"):
        return
    for field in (beginning, ending):
        field.validation_results.append(
            ExtractionValidationResult(
                validator="policy_date_order",
                status="ERROR",
                reason="Policy beginning date must not be after policy ending date.",
            )
        )
        field.confidence = min(float(field.confidence or 0), REVIEW_CONFIDENCE_CEILING)
        field.decision = "REVIEW_REQUIRED"
        error_fields.add(field.field_name)


def parse_decimal(value: str | None) -> Decimal | None:
    """Public normalization helper used by later broker reconciliation slices."""
    clean = re.sub(r"[^0-9.()-]", "", str(value or "")).replace("(", "-").replace(")", "")
    if not clean:
        return None
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def _validate_cross_section_duplicates(
    fields: list[NormalizedExtractionField],
) -> list[str]:
    """Fail closed when a model copies one amount across incompatible sections.

    Equal values are allowed only when the experience-rated field carries
    explicit source evidence for Part II/line 9. This avoids treating a generic
    premium, commission, or fee label as proof of an experience-rated value.
    """

    def find(prefix: str) -> NormalizedExtractionField | None:
        normalized = prefix.lower()
        return next(
            (
                field
                for field in fields
                if field.field_name.strip().lower().startswith(normalized)
            ),
            None,
        )

    errors: list[str] = []
    for experience_prefix, source_prefix, error_suffix in (
        ("9a.", "10a.", "9a"),
        ("9c(1)(a).", "3b.", "9c_commissions"),
        ("9c(1)(b).", "3c.", "9c_fees"),
    ):
        experience = find(experience_prefix)
        source = find(source_prefix)
        if not experience or not source:
            continue
        experience_value = parse_decimal(experience.value)
        source_value = parse_decimal(source.value)
        if (
            experience_value is None
            or source_value is None
            or experience_value != source_value
            or _has_explicit_experience_evidence(experience)
        ):
            continue
        error = f"cross_section_duplicate:{error_suffix}"
        errors.append(error)
        _mark_fields_review(
            fields,
            (experience_prefix,),
            validator="cross_section_duplicate",
            reason=(
                "This amount duplicates a value from an incompatible Schedule A section "
                "without explicit experience-rated source evidence."
            ),
        )
    return errors


def _has_explicit_experience_evidence(field: NormalizedExtractionField) -> bool:
    evidence_text = " ".join(
        text
        for text in [
            field.source_text,
            *(item.source_text for item in field.evidence),
        ]
        if text
    ).lower()
    normalized = re.sub(r"\s+", " ", evidence_text)
    return bool(
        re.search(r"\bpart\s+ii\b", normalized)
        or re.search(r"\bexperience[- ]rated\b", normalized)
        or re.search(r"\b9[abcd](?:\b|\s*\()", normalized)
    )


def _validate_broker_rows(
    result: NormalizedExtractionResult,
    rows: list[ScheduleABrokerRow],
) -> list[str]:
    errors: list[str] = []
    has_row_semantic_error = False
    has_column_semantic_error = False
    for row in rows:
        if not row.evidence and (row.source_page or row.name):
            row.evidence.append(
                SourceEvidence(
                    provider=result.provider,
                    page=row.source_page,
                    source_text=row.name,
                )
            )
        validations: list[ExtractionValidationResult] = []
        row_pages = [row.source_page, *(item.page for item in row.evidence)]
        row_source_texts = [item.source_text for item in row.evidence]
        has_row_evidence = any(
            isinstance(page, int) and page > 0 for page in row_pages
        ) and any(bool(str(text or "").strip()) for text in row_source_texts)
        validations.append(
            ExtractionValidationResult(
                validator="source_evidence",
                status="PASS" if has_row_evidence else "ERROR",
                reason=(
                    "Broker row has page-level source evidence."
                    if has_row_evidence
                    else "Automatic broker extraction requires a source page and supporting row text."
                ),
            )
        )
        broker_name = str(row.name or "").strip()
        if not broker_name:
            validations.append(
                ExtractionValidationResult(
                    validator="broker_name",
                    status="ERROR",
                    reason="Broker name is required for a structured broker row.",
                )
            )
        else:
            validations.append(
                ExtractionValidationResult(
                    validator="broker_name",
                    status="PASS",
                    reason="Broker name is present.",
                )
            )
        normalized_name = re.sub(r"\s+", " ", broker_name).strip(" .:-").lower()
        name_is_noise = (
            normalized_name in _BROKER_NOISE_NAMES
            or bool(re.fullmatch(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?", normalized_name))
            or normalized_name.startswith(("amount of ", "total ", "policy year", "contract year"))
        )
        validations.append(
            ExtractionValidationResult(
                validator="broker_name_semantics",
                status="ERROR" if name_is_noise else "PASS",
                reason="Broker name looks like a date, month, heading, or table label." if name_is_noise else "Broker name is semantically plausible.",
            )
        )
        commission_source = str(row.commission_source_text or "").lower()
        fee_source = str(row.fee_source_text or "").lower()
        commission_crossed = bool(
            commission_source
            and re.search(r"\bfees?\b", commission_source)
            and not re.search(r"\bcommissions?\b", commission_source)
        )
        fee_crossed = bool(
            fee_source
            and re.search(r"\bcommissions?\b", fee_source)
            and not re.search(r"\bfees?\b", fee_source)
        )
        crossed_columns = commission_crossed or fee_crossed
        validations.append(
            ExtractionValidationResult(
                validator="broker_column_semantics",
                status="ERROR" if crossed_columns else "PASS",
                reason=(
                    "Broker commission/fee evidence points to the opposite table column."
                    if crossed_columns
                    else "Broker amount evidence does not contradict its assigned column."
                ),
            )
        )
        for label, value in (("commission", row.commission_total), ("fee", row.fee_total)):
            if value not in (None, "") and not _is_currency_amount(value):
                validations.append(
                    ExtractionValidationResult(
                        validator=f"broker_{label}_amount",
                        status="ERROR",
                        reason=f"Broker {label} total is not a valid currency amount.",
                    )
                )
        row.validation_results = validations
        row_has_error = any(item.status == "ERROR" for item in validations)
        row.decision = "REVIEW_REQUIRED" if row_has_error else "AUTOMATIC"
        if row_has_error:
            row.confidence = min(float(row.confidence or 0), REVIEW_CONFIDENCE_CEILING)
            has_row_semantic_error = True
        if crossed_columns:
            has_column_semantic_error = True

    if has_row_semantic_error:
        errors.append("broker_row_semantics")
    if has_column_semantic_error:
        errors.append("broker_column_semantics")
    if any(
        any(item.validator == "source_evidence" and item.status == "ERROR" for item in row.validation_results)
        for row in rows
    ):
        errors.append("broker_source_evidence")

    commission_error = _reconcile_broker_total(result.fields, rows, "3b.", "commission_total")
    fee_error = _reconcile_broker_total(result.fields, rows, "3c.", "fee_total")
    if commission_error:
        errors.append("broker_commission_total")
        _mark_fields_review(
            result.fields,
            ("3b.",),
            validator="broker_total_reconciliation",
            reason="Commission total does not reconcile with the extracted broker rows.",
        )
    if fee_error:
        errors.append("broker_fee_total")
        _mark_fields_review(
            result.fields,
            ("3c.",),
            validator="broker_total_reconciliation",
            reason="Fee total does not reconcile with the extracted broker rows.",
        )
    commission_field = next(
        (field for field in result.fields if field.field_name.strip().lower().startswith("3b.")),
        None,
    )
    fee_field = next(
        (field for field in result.fields if field.field_name.strip().lower().startswith("3c.")),
        None,
    )
    commission_value = parse_decimal(commission_field.value) if commission_field else None
    fee_value = parse_decimal(fee_field.value) if fee_field else None
    if (
        commission_value is not None
        and commission_value != 0
        and commission_value == fee_value
        and (commission_error or fee_error)
    ):
        errors.append("ambiguous_compensation_split")
        _mark_fields_review(
            result.fields,
            ("3b.", "3c."),
            validator="ambiguous_compensation_split",
            reason=(
                "The same nonzero amount was extracted as both commissions and fees, "
                "but the broker rows do not support both allocations."
            ),
        )
    if errors:
        for row in rows:
            row.validation_results.append(
                ExtractionValidationResult(
                    validator="broker_total_reconciliation",
                    status="ERROR",
                    reason="Structured broker totals do not match the Schedule A section total.",
                )
            )
            row.confidence = min(float(row.confidence or 0), REVIEW_CONFIDENCE_CEILING)
            row.decision = "REVIEW_REQUIRED"
    return errors


def _mark_fields_review(
    fields: list[NormalizedExtractionField],
    prefixes: tuple[str, ...],
    *,
    validator: str,
    reason: str,
) -> None:
    normalized_prefixes = tuple(prefix.lower() for prefix in prefixes)
    for field in fields:
        if not field.field_name.strip().lower().startswith(normalized_prefixes):
            continue
        if not any(item.validator == validator and item.status == "ERROR" for item in field.validation_results):
            field.validation_results.append(
                ExtractionValidationResult(
                    validator=validator,
                    status="ERROR",
                    reason=reason,
                )
            )
        field.confidence = min(float(field.confidence or 0), REVIEW_CONFIDENCE_CEILING)
        field.decision = "REVIEW_REQUIRED"


def _is_currency_amount(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(
        re.fullmatch(
            r"(?:\$\s*)?(?:-\s*)?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?"
            r"|\((?:\$\s*)?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?\)",
            text,
        )
    )


def _reconcile_broker_total(
    fields: list[NormalizedExtractionField],
    rows: list[ScheduleABrokerRow],
    field_prefix: str,
    row_attribute: str,
) -> bool:
    total_field = next(
        (field for field in fields if field.field_name.strip().lower().startswith(field_prefix.lower())),
        None,
    )
    expected = parse_decimal(total_field.value) if total_field else None
    if expected is None or not rows:
        return False
    actual_values = [parse_decimal(getattr(row, row_attribute)) for row in rows]
    actual = sum((value or Decimal("0") for value in actual_values), Decimal("0"))
    return abs(expected - actual) > Decimal("0.01")
