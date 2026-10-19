"""Preserve source facts when a proposed business interpretation is unapproved."""
from decimal import Decimal, InvalidOperation
import re

from app.models import ExtractionValidationResult, SourceEvidence


def explicit_missing_person_count(text):
    return re.search(
        r"(?:Approximate\s+)?Number\s+of\s+Persons\s+(?:Insured|Covered)[^\n:]{0,90}:\s*"
        r"(?P<literal>To\s+be\s+Provided[^\n]*|Not\s+Provided|N\s*/\s*A)\b",
        text or "", re.IGNORECASE,
    )


def _money(value):
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _hold(field, reason):
    if not any(v.validator == "business_rule_confirmation" and v.reason == reason for v in field.validation_results):
        field.validation_results.append(ExtractionValidationResult(
            validator="business_rule_confirmation", status="ERROR", reason=reason,
        ))
    field.decision = "REVIEW_REQUIRED"


def apply_source_business_review(result, page_texts):
    fields = {f.field_name.split(".", 1)[0].strip().lower(): f for f in result.fields}
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    text = '\n'.join(text for _, text in page_texts)
    # A letter's unpaid ending balance is not an increase/decrease, and its
    # supplementary acquisition cost is not an experience retention answer.
    # Keep both literal source facts and the rejected provider interpretation.
    # Do not infer the contract's experience classification from this layout.
    letter_premiums = re.search(r'NON[- ]PARTICIPATING\s+CONTRACTS\s*\(\s*PREMIUMS\s*\)', text, re.I)
    experience_section = re.search(r'(?im)^\s*(?:9[.)]?\s*)?Experience[- ]rated\s+contracts\b|^\s*9[a-e](?:\(|[.)])', text)
    if letter_premiums and not experience_section:
        reported = {value['key']: value for value in (raw.get('source_review') or {}).get('reported_values', [])}
        rejected = []
        for field in result.fields:
            source_key = {'9a(2)': 'unpaid_premium', '9c(1)(C)': 'additional_costs'}.get(field.field_name.split('.',1)[0])
            source = reported.get(source_key)
            if source and _money(source['value']) == _money(field.value):
                rejected.append(field)
                record = {'candidate': field.model_dump(mode='json'), 'source_value': source,
                    'decision': 'SOURCE_ONLY', 'reason': 'Letter premium information does not establish this experience-rated field. No destination value is proposed.'}
                if record not in raw.setdefault('source_only_field_candidates', []):
                    raw['source_only_field_candidates'].append(record)
        result.fields = [field for field in result.fields if field not in rejected]
        result.raw = raw
    observations = list(raw.get("source_observations") or [])
    naic = fields.get("1c")
    for page, text in page_texts:
        missing_count = explicit_missing_person_count(text)
        if missing_count:
            count = fields.get("1e")
            positive_count_source = count is not None and re.search(
                r"(?:persons\s+(?:insured|covered)|number\s+of\s+persons)[^\n:]{0,90}:"
                r"[^\S\n]*" + re.escape(str(count.value)) + r"\b", count.source_text or "", re.I,
            )
            observation = {
                "label": "Persons covered not provided", "literal": missing_count.group("literal").strip(),
                "page": page, "source_text": missing_count.group(0), "decision": "MISSING_SOURCE_VALUE",
                "rejected_values": [count.value] if count is not None and not positive_count_source else [],
            }
            if observation not in observations:
                observations.append(observation)
            if count is not None:
                if positive_count_source:
                    _hold(count, "Source pages disagree about whether the covered-person count was provided. Confirm the population and record before using a value.")
                else:
                    result.fields = [field for field in result.fields if not field.field_name.startswith("1e.")]
                    fields.pop("1e", None)
        for match in re.finditer(r"\bNAIC\s+(?:CODE|NUMBER)\s*:?\s*([\d][\d -]{4,14}\d)(?!\d)", text, re.I):
            literal = match.group(1).strip()
            digits = re.sub(r"\D", "", literal)
            if len(digits) <= 5 or naic is None:
                continue
            # Do not borrow an appendix identifier for a different entity.
            current = re.sub(r"\D", "", str(naic.value))
            if not current or current.lstrip("0") != digits.lstrip("0"):
                continue
            naic.value = literal
            naic.candidate_values = list(dict.fromkeys([*naic.candidate_values, literal]))
            naic.page = page
            naic.source_text = match.group(0)
            if not any(e.page == page and e.source_text == match.group(0) for e in naic.evidence):
                naic.evidence.append(SourceEvidence(provider="Native/source-labelled NAIC review", page=page, source_text=match.group(0)))
            _hold(naic, "Printed NAIC is longer than five digits. Confirm its normalization; an annotation is not approval to drop identifier digits.")
            observation = {"label": "Reported NAIC (source format)", "literal": literal, "page": page,
                           "source_text": match.group(0), "decision": "REVIEW_REQUIRED"}
            if observation not in observations:
                observations.append(observation)
    persons, premium = fields.get("1e"), fields.get("10a")
    if persons is not None and _money(persons.value) == 0 and premium is not None and (_money(premium.value) or 0) > 0:
        _hold(persons, "Source count is zero while premiums are reported. Retain zero; confirm any proposed replacement using a verified source, never an invented Form 5500 count.")
    if persons is not None and re.search(r"\bEmployees?\b", str(persons.source_text or ""), re.I):
        if any(re.search(r"employees\s+covered\s+rather\s+than\s+employees\s+and\s+dependents", text, re.I)
               for _, text in page_texts):
            _hold(persons, "The repeated header reports employees and the supporting text distinguishes employees from dependents. Confirm the intended covered-person population without changing the printed count.")
    if observations:
        raw["source_observations"] = observations
        result.raw = raw
