"""Apply explicit, source-bound client decisions without weakening default QA.

The caller supplies a reviewed approval record, never PDF annotations or provider
output. Decisions are bound to the reviewed PDF hash. New PDFs must be reviewed
or covered by a separately approved reporting policy before reusing decisions.
No sample amounts, company names, filenames or folder IDs are embedded here.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re

from app.models import ExtractionValidationResult, NormalizedExtractionField, ScheduleABrokerMoneyRow, SourceEvidence


ALLOWED_RULES = frozenset({
    "naic_field_1c", "org_code_3e", "sum_premium_components", "four_distinct_recipients",
    "employees_as_covered", "maximum_covered_count", "wd_temporary_disability",
    "trim_contract_trailing_zeros", "merge_same_recipient", "padded_naic", "life_add_adnd",
    "combined_payment_commission", "count_from_6a2", "nonexperience_rated", "no_footer_entry",
    "no_additional_pages", "no_additional_compensation",
})
APPROVAL_PROVIDER = "Verified client clarification"
WORKSHEET_PROVIDER = "Verified matching Form 5500 worksheet"


@dataclass(frozen=True)
class VerifiedWorksheetCount:
    value: str
    source_sha256: str
    sponsor_name: str
    policy_number: str
    plan_year_begin: str
    plan_year_end: str
    source_text: str
    reference: str

    def __post_init__(self):
        if not re.fullmatch(r"\d+", self.value) or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("Verified worksheet count and source hash required")
        if not all((self.sponsor_name.strip(), self.policy_number.strip(), self.reference.strip())):
            raise ValueError("Verified worksheet identity and reference required")
        if not re.search(r"6\s*\(?a\)?\s*\(?2\)?", self.source_text, re.I):
            raise ValueError("Worksheet source must explicitly identify line 6(a)(2)")
        if not re.search(r"(?<!\d)" + re.escape(self.value) + r"(?!\d)", self.source_text):
            raise ValueError("Worksheet source must contain the supplied count")
        if datetime.fromisoformat(self.plan_year_begin) > datetime.fromisoformat(self.plan_year_end):
            raise ValueError("Invalid worksheet plan year")


@dataclass(frozen=True)
class ConfirmedClarifications:
    source_sha256: str
    rules: frozenset[str]
    reference: str
    worksheet: VerifiedWorksheetCount | None = None

    def __post_init__(self):
        if isinstance(self.worksheet, dict):
            object.__setattr__(self, "worksheet", VerifiedWorksheetCount(**self.worksheet))
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("A valid source SHA-256 is required")
        if not self.reference.strip():
            raise ValueError("A verified client approval reference is required")
        if self.rules - ALLOWED_RULES:
            raise ValueError("Unknown clarification rule")


def _amount(value):
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _approved(field, rule, approval, detail):
    text = f"Client-approved rule {rule}: {detail}. Approval: {approval.reference}. Source SHA-256: {approval.source_sha256}"
    evidence = SourceEvidence(provider=APPROVAL_PROVIDER, source_text=text)
    if evidence not in field.evidence:
        field.evidence.append(evidence)


def has_confirmed_rule(field, rule):
    """Interpretation evidence does not substitute for numeric source evidence."""
    return any(e.provider == APPROVAL_PROVIDER and (e.source_text or "").startswith(
        f"Client-approved rule {rule}:") for e in field.evidence)


def _remove_specific_hold(field, validator, fragment=None):
    field.validation_results = [v for v in field.validation_results
        if not (v.validator == validator and (fragment is None or fragment in v.reason))]


def _address_identity(row):
    aliases = {"CIRCLE": "CIR", "ROAD": "RD", "STREET": "ST", "AVENUE": "AVE",
               "BOULEVARD": "BLVD", "DRIVE": "DR", "LANE": "LN", "SUITE": "STE"}
    def tokens(value):
        return " ".join(re.findall(r"[A-Z0-9]+", str(value or "").upper()))
    if not all((row.name, row.address_line_1, row.city, row.state, row.zip_code)):
        return None
    return (tokens(row.name), " ".join(aliases.get(t, t) for t in tokens(row.address_line_1).split()),
            tokens(row.address_line_2), tokens(row.city), tokens(row.state), tokens(row.zip_code),
            tokens(row.organization_code))


def apply_confirmed_clarifications(result, page_texts, approval, file_bytes):
    if approval is None:
        return
    if hashlib.sha256(file_bytes).hexdigest() != approval.source_sha256:
        raise ValueError("Clarification approval does not match source PDF")
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    if raw.get("confirmed_clarifications"):
        raise ValueError("Clarifications were already applied; start from the preserved extraction stage")
    ledger = {"source_sha256": approval.source_sha256, "approval_reference": approval.reference,
              "rules": sorted(approval.rules), "changes": [], "remaining": []}
    raw["confirmed_clarifications"] = ledger
    result.raw = raw
    fields = {f.field_name.split(".", 1)[0].strip().lower(): f for f in result.fields}

    def change(key, rule, new_value, detail):
        f = fields.get(key)
        if f is None:
            ledger["remaining"].append({"field": key, "rule": rule, "reason": "Source field not extracted"})
            return
        before = f.value
        if new_value != before:
            f.value, f.candidate_values = new_value, [new_value]
        _approved(f, rule, approval, detail)
        ledger["changes"].append({"field": key, "rule": rule, "before": before, "after": new_value})

    if "padded_naic" in approval.rules and (f := fields.get("1c")):
        match = re.fullmatch(r"0+\s*-\s*(\d{5})", f.value.strip())
        if match:
            change("1c", "padded_naic", match[1], f"Normalize zero-padding separator format; normalized NAIC {match[1]}")
            _remove_specific_hold(f, "business_rule_confirmation", "Printed NAIC is longer")
    if "trim_contract_trailing_zeros" in approval.rules and (f := fields.get("1d")):
        normalized = f.value.rstrip("0")
        if normalized and normalized != f.value:
            change("1d", "trim_contract_trailing_zeros", normalized,
                   f"Remove trailing zeros only; retain leading digits. Normalized contract {normalized}")
    if "employees_as_covered" in approval.rules and (f := fields.get("1e")):
        if _amount(f.value) is not None and _amount(f.value) > 0:
            change("1e", "employees_as_covered", f.value, "Use the source employee population for this approved document")
            _remove_specific_hold(f, "business_rule_confirmation", "repeated header reports employees")
    if "maximum_covered_count" in approval.rules:
        components = raw.get("source_review", {}).get("premium_components", [])
        counts = [c for c in components if str(c.get("persons_covered", "")).isdigit()]
        if counts:
            largest = max(counts, key=lambda c: int(c["persons_covered"]))
            if "1e" not in fields:
                f = NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)",
                    value=largest["persons_covered"], candidate_values=[largest["persons_covered"]], confidence=.94)
                result.fields.append(f)
                fields["1e"] = f
            change("1e", "maximum_covered_count", largest["persons_covered"],
                   "Use the highest independently parsed covered-count component for this approved document")
            f = fields.get("1e")
            if f:
                f.page, f.source_text = largest["page"], largest["source_text"]
                f.evidence.append(SourceEvidence(provider="Source covered-count component", page=largest["page"], source_text=largest["source_text"]))
                _remove_specific_hold(f, "lives_covered_column_ambiguous")
                ambiguities = raw.get("semantic_resolution", {}).get("ambiguities", [])
                raw.get("semantic_resolution", {})["ambiguities"] = [a for a in ambiguities
                    if not isinstance(a, dict) or a.get("type") != "lives_covered_column_ambiguous"]
        else:
            ledger["remaining"].append({"field": "1e", "rule": "maximum_covered_count", "reason": "No independent source count components"})
    if "nonexperience_rated" in approval.rules:
        if "EXPLICIT_EXPERIENCE_RATED" in result.classification_signals:
            ledger["remaining"].append({"field": "classification", "reason": "Printed experience-rated evidence conflicts with client decision"})
        else:
            if "EXPLICIT_NONEXPERIENCE_RATED" not in result.classification_signals:
                result.classification_signals.append("EXPLICIT_NONEXPERIENCE_RATED")
            inapplicable = [f for f in result.fields if re.match(r"^9[abcde](?:\(|\.)", f.field_name, re.I)]
            if inapplicable:
                ledger["excluded_fields"] = [{"field": f.model_dump(mode="json"),
                    "rule": "nonexperience_rated", "reason": "Experience-rated line 9 is inapplicable to this explicitly client-confirmed nonexperience-rated document. Preserve this candidate in diagnostics, not normalized update fields."}
                    for f in inapplicable]
                excluded_names = {f.field_name for f in inapplicable}
                result.fields = [f for f in result.fields if f.field_name not in excluded_names]
                fields = {key: f for key, f in fields.items() if f.field_name not in excluded_names}
            if "10a" in fields:
                change("10a", "nonexperience_rated", fields["10a"].value,
                       "This contract is nonexperience-rated; paid premiums belong in line 10a")
    if "combined_payment_commission" in approval.rules:
        payments = raw.get("source_review", {}).get("reported_values", [])
        combined = [p for p in payments if p.get("key") == "combined_broker_payment"]
        rows = result.schedule_a_broker_rows
        if len(combined) == 1 and len(rows) == 1 and _amount(combined[0].get("value")) is not None:
            p, row = combined[0], rows[0]
            amount = format(_amount(p["value"]), "f")
            if _amount(row.commission_total) != _amount(amount):
                ledger["remaining"].append({"field": "3b", "reason": "Source combined payment and recipient amount disagree"})
            else:
                change("3b", "combined_payment_commission", amount, "The entire printed combined payment is commission")
                f = fields.get("3b")
                if f:
                    f.page, f.source_text = p["page"], p["source_text"]
                    f.evidence.append(SourceEvidence(provider="Printed combined payment", page=p["page"], source_text=p["source_text"]))
                change("3c", "combined_payment_commission", "0", "No fee portion remains after the approved complete commission allocation")
                change("3d", "combined_payment_commission", "", "No fee purpose applies when the payment is entirely commission")
                for key in ("3b", "3c", "3d"):
                    if key in fields:
                        for validator in ("payment_classification_ambiguity", "combined_commission_fee_source"):
                            _remove_specific_hold(fields[key], validator)
                row.commission_total, row.fee_total, row.purpose = amount, "0", None
                row.commission_rows = [ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")]
                row.fee_rows = []
                row.commission_source_text = p["source_text"]
                _remove_specific_hold(row, "payment_classification_ambiguity")
                row.evidence.append(SourceEvidence(provider=APPROVAL_PROVIDER, source_text="Entire combined payment is commission. " + approval.reference))
                raw.get("semantic_resolution", {})["ambiguities"] = [a for a in raw.get("semantic_resolution", {}).get("ambiguities", [])
                    if not isinstance(a, dict) or a.get("type") != "combined_commission_fee_source"]
        else:
            ledger["remaining"].append({"field": "3b", "reason": "Approved allocation requires exactly one source payment and one independently identified recipient"})
    if "merge_same_recipient" in approval.rules:
        grouped, rows = {}, []
        for row in result.schedule_a_broker_rows:
            key = _address_identity(row)
            target = grouped.get(key) if key is not None else None
            if target is None:
                rows.append(row)
                if key is not None:
                    grouped[key] = row
                continue
            values = [_amount(v) for v in (target.commission_total, row.commission_total, target.fee_total, row.fee_total)]
            if any(v is None for v in values):
                rows.append(row)
                ledger["remaining"].append({"field": "brokers", "reason": "Cannot aggregate a missing or nonnumeric recipient payment"})
                continue
            target.commission_total, target.fee_total = format(values[0] + values[1], "f"), format(values[2] + values[3], "f")
            target.commission_rows.extend(row.commission_rows)
            target.fee_rows.extend(row.fee_rows)
            target.evidence.extend(row.evidence)
            target.validation_results.extend(row.validation_results)
            target.commission_source_text = "\n".join(filter(None, [target.commission_source_text, row.commission_source_text]))
            target.fee_source_text = "\n".join(filter(None, [target.fee_source_text, row.fee_source_text]))
            target.purpose = row.purpose or target.purpose
            _remove_specific_hold(target, "recipient_identity_ambiguity", "Distinct printed addresses may be abbreviations")
            ledger["changes"].append({"field": "brokers", "rule": "merge_same_recipient", "name": target.name,
                "commission_total": target.commission_total, "fee_total": target.fee_total, "source_addresses": [target.address_line_1, row.address_line_1]})
        result.schedule_a_broker_rows = rows
    benefits = sorted(set([*(raw.get("benefit_types") or []),
                           *(raw.get("source_review", {}).get("benefit_types") or [])]))
    if "life_add_adnd" in approval.rules and "life" in benefits:
        benefits = sorted(set([*benefits, "other"]))
        ledger["benefit_other_text"] = "AD&D"
    coverage_sources = [text for _, text in page_texts] + [str(c.get("coverage") or "") + " " + str(c.get("source_text") or "")
        for c in raw.get("source_review", {}).get("premium_components", [])]
    if "wd_temporary_disability" in approval.rules and any(re.search(r"\bWD\s*-\s*NST\b", text) for text in coverage_sources):
        benefits = sorted(set([*benefits, "temporary_disability"]))
    raw["benefit_types"] = benefits
    if "count_from_6a2" in approval.rules:
        worksheet = approval.worksheet
        matching = False
        if worksheet:
            normalize = lambda value: re.sub(r"[^a-z0-9]", "", str(value or "").lower())
            sponsor = re.sub(r"\b(?:incorporated|inc|llc|limited|ltd|corporation|corp)\.?\s*$", "", worksheet.sponsor_name, flags=re.I).strip()
            sponsor_present = normalize(sponsor) in normalize(" ".join(text for _, text in page_texts))
            try:
                period = [datetime.strptime(fields[key].value, "%m/%d/%Y") for key in ("1f", "1g")]
                in_year = (datetime.fromisoformat(worksheet.plan_year_begin) <= period[0] <= period[1]
                    <= datetime.fromisoformat(worksheet.plan_year_end))
            except (KeyError, ValueError):
                in_year = False
            matching = (sponsor_present and fields.get("1d") is not None
                        and fields["1d"].value == worksheet.policy_number and in_year)
        if matching and "1e" in fields:
            f = fields["1e"]
            change("1e", "count_from_6a2", worksheet.value,
                "Replace the printed source count using the client-approved matching worksheet line 6(a)(2)")
            _remove_specific_hold(f, "business_rule_confirmation", "Source count is zero")
            f.page = None
            f.source_text = worksheet.source_text
            f.evidence.append(SourceEvidence(provider=WORKSHEET_PROVIDER,
                source_text=worksheet.source_text + "\nWorksheet SHA256: " + worksheet.source_sha256 + "\n" + worksheet.reference))
            ledger["worksheet"] = {"source_sha256": worksheet.source_sha256, "reference": worksheet.reference,
                "value": worksheet.value, "identity_checks": "sponsor, exact policy number, plan year", "pdf_printed_count": "Preserved in before-clarification stage"}
        else:
            reason = ("Matching verified Form 5500 line 6a2 worksheet value is not supplied" if not worksheet
                else "Worksheet sponsor, exact policy number or plan year does not match the extracted source")
            ledger["remaining"].append({"field": "1e", "rule": "count_from_6a2", "reason": reason + "; preserve source count and require review"})
            if "1e" in fields:
                fields["1e"].validation_results.append(ExtractionValidationResult(validator="business_rule_confirmation", status="ERROR", reason=ledger["remaining"][-1]["reason"]))
    # Footnotes remain in the raw provider/source evidence. No invented note or
    # additional compensation is produced from generic references to attachments.
    raw["confirmed_clarifications"] = ledger
    result.raw = raw
