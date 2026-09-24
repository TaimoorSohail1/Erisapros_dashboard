"""Layout-independent semantic enrichment for Schedule A extraction.

EyeLevel and the local parsers remain candidate producers.  This module uses
the source document's labels, rows, and section context to decide what those
candidates mean and to attach the evidence required by the canonical review
pipeline.  It deliberately fails closed when one upload contains multiple
Schedule A policy groups.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable

from app.models import (
    FieldRule,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerRow,
    SourceEvidence,
)
from app.services.field_rules import normalize_name
from app.services.schedule_a_lives_column import lives_column_selections
from app.services.schedule_a_policy_period import explicit_contract_periods


_MONEY = re.compile(r"(?<![A-Za-z0-9])\$?\s*(-?\d[\d,]*(?:\.\d{1,2})?)(?![A-Za-z0-9])")
_STANDARD_PERSONS = re.compile(
    r"(?:approximate\s+number\s+of\s+)?persons?\s+covered[^\d]{0,80}([\d,]+)\b",
    re.IGNORECASE,
)
_CONTRACT_LABEL = re.compile(
    r"(?:contract|policy)(?:\s+or\s+identification)?(?:\s*(?:/|or)\s*policy)?\s+(?:number|no\.?|id\s*#?)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticLine:
    page: int
    row: int
    text: str
    column_start: int = 0
    column_end: int = 0

    @property
    def normalized(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


@dataclass(frozen=True)
class SemanticCandidate:
    value: str
    page: int
    source_text: str
    row: int
    reason: str

    def evidence(self, provider: str = "Schedule A semantic layer") -> SourceEvidence:
        return SourceEvidence(
            provider=provider,
            page=self.page,
            source_text=self.source_text,
            table_cell=(self.row, 0),
        )


@dataclass(frozen=True)
class SemanticPolicyGroup:
    group_id: str
    page: int
    contract_number: str | None = None
    persons_covered: str | None = None


@dataclass
class SemanticDocument:
    lines: list[SemanticLine] = field(default_factory=list)
    pages: dict[int, str] = field(default_factory=dict)
    groups: list[SemanticPolicyGroup] = field(default_factory=list)

    @classmethod
    def from_page_texts(cls, page_texts: Iterable[tuple[int, str]]) -> "SemanticDocument":
        pages: dict[int, str] = {}
        lines: list[SemanticLine] = []
        for page, text in page_texts:
            clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
            pages[int(page)] = clean
            for row, line in enumerate(clean.splitlines(), start=1):
                if line.strip():
                    rendered = line.rstrip()
                    column_start = len(rendered) - len(rendered.lstrip())
                    lines.append(
                        SemanticLine(
                            page=int(page),
                            row=row,
                            text=rendered,
                            column_start=column_start,
                            column_end=len(rendered),
                        )
                    )
        document = cls(lines=lines, pages=pages)
        document.groups = document._detect_policy_groups()
        return document

    def page_lines(self, page: int) -> list[SemanticLine]:
        return [line for line in self.lines if line.page == page]

    def context(self, line: SemanticLine, radius: int = 3) -> str:
        page_lines = self.page_lines(line.page)
        try:
            index = page_lines.index(line)
        except ValueError:
            return line.normalized
        start = max(0, index - radius)
        end = min(len(page_lines), index + radius + 1)
        return "\n".join(item.normalized for item in page_lines[start:end])

    def _detect_policy_groups(self) -> list[SemanticPolicyGroup]:
        """Detect independently completed Schedule A Part I blocks.

        A carrier appendix can contain many rows, so it is not treated as
        multiple Schedule As.  Separate official/worksheet Part I blocks with
        their own contract and persons-covered labels are separate groups.
        """
        groups: list[SemanticPolicyGroup] = []
        seen: set[tuple[str, str]] = set()
        for page, text in sorted(self.pages.items()):
            normalized = re.sub(r"\s+", " ", text)
            has_part_i = bool(re.search(r"\bPART\s+I\b", normalized, re.IGNORECASE))
            has_carrier = bool(
                re.search(r"Name\s+of\s+Insurance\s+Carrier", normalized, re.IGNORECASE)
            )
            has_persons = bool(
                re.search(r"Persons?\s+Covered\s+at\s+End\s+of\s+Policy\s+Year", normalized, re.IGNORECASE)
            )
            if not (has_part_i and has_carrier and has_persons):
                continue
            contract_match = _CONTRACT_LABEL.search(normalized)
            persons_match = _STANDARD_PERSONS.search(normalized)
            contract = contract_match.group(1) if contract_match else None
            persons = persons_match.group(1).replace(",", "") if persons_match else None
            identity = contract or f"page-{page}"
            group_key = (str(contract or "").casefold(), str(persons or ""))
            if group_key in seen:
                continue
            seen.add(group_key)
            groups.append(
                SemanticPolicyGroup(
                    group_id=f"{identity}@{page}",
                    page=page,
                    contract_number=contract,
                    persons_covered=persons,
                )
            )
        for page, text in sorted(self.pages.items()):
            normalized = re.sub(r"\s+", " ", text)
            support_contract = re.search(
                r"Contract\s+Identification\s*/\s*Policy\s+Number\s*:\s*"
                r"([A-Za-z0-9][A-Za-z0-9_./-]+)",
                normalized,
                re.IGNORECASE,
            )
            policy_period = re.search(
                r"Policy\s+Period\s*:\s*"
                r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|to|through)\s*"
                r"(\d{1,2}/\d{1,2}/\d{2,4})",
                normalized,
                re.IGNORECASE,
            )
            carrier = re.search(
                r"Name\s+of\s+Insurance\s+Company\s*:\s*(.+?)(?=\s+(?:Federal\s+)?(?:Tax\s+ID|EIN|NAIC|Benefits?|Approximate)|$)",
                normalized,
                re.IGNORECASE,
            )
            if not (support_contract and policy_period and carrier):
                continue
            contract = support_contract.group(1)
            group_key = (contract.casefold(), policy_period.group(1) + "-" + policy_period.group(2))
            if any(existing.contract_number and existing.contract_number.casefold() == contract.casefold() for existing in groups):
                continue
            if group_key in seen:
                continue
            seen.add(group_key)
            groups.append(
                SemanticPolicyGroup(
                    group_id=f"{contract}@{page}",
                    page=page,
                    contract_number=contract,
                )
            )
        return groups


def enrich_schedule_a_result(
    result: NormalizedExtractionResult,
    document: SemanticDocument,
    *,
    rules: Iterable[FieldRule],
) -> NormalizedExtractionResult:
    """Attach source meaning to provider candidates and correct explicit totals."""
    enriched = result.model_copy(deep=True)
    rule_list = list(rules)
    corrections: list[dict[str, str]] = []

    persons_field = _field_by_prefix(enriched.fields, "1e.")
    persons_candidates = _persons_covered_candidates(document)
    columns = lives_column_selections(list(document.pages.items()))
    column_ambiguous = any(column.ambiguous for column in columns)
    column_contracts = {contract for column in columns for contract in column.contracts}
    column_ambiguous = column_ambiguous or len(column_contracts) > 1
    if columns and not column_ambiguous:
        persons_candidates = [SemanticCandidate(column.value, column.page,
            column.source_text, column.row, "highest_lives_covered_column") for column in columns]
        if len(columns) > 1 and len(column_contracts) == 1 and all(column.contracts for column in columns):
            # A continued table may repeat the same verified policy on several
            # pages. Aggregate only that exact identity, not unrelated tables.
            highest_column = max(columns, key=lambda column: int(column.value))
            all_counts = [value for column in columns for value in column.counts]
            evidence = ("Highest lives-covered count selected: " + highest_column.value + "\n"
                + "Counts in covered-lives column: " + ", ".join(all_counts) + "\n"
                + "\n".join(column.source_text for column in columns))
            persons_candidates = [SemanticCandidate(highest_column.value, highest_column.page,
                evidence, highest_column.row, "highest_lives_covered_column")]
    elif column_ambiguous:
        # Retain the provider's value for review; never merge different contracts
        # or assert a maximum when any of the column's rows is unreadable.
        persons_candidates = []
    unique_persons = _unique_candidates(persons_candidates)
    if persons_field is None and len(unique_persons) == 1:
        rule = next(
            (item for item in rule_list if item.label.strip().lower().startswith("1e.")),
            None,
        )
        if rule is not None:
            candidate = unique_persons[0]
            persons_field = NormalizedExtractionField(
                field_name=rule.label,
                value=candidate.value,
                candidate_values=[candidate.value],
                confidence=0.92,
            )
            _apply_candidate(persons_field, candidate, replace_value=True)
            enriched.fields.append(persons_field)
            corrections.append(
                {
                    "field": persons_field.field_name,
                    "before": "",
                    "after": candidate.value,
                    "reason": candidate.reason,
                }
            )
    elif persons_field is not None and len(unique_persons) == 1:
        candidate = unique_persons[0]
        old_value = persons_field.value
        if _integer_identity(old_value) != _integer_identity(candidate.value):
            corrections.append(
                {
                    "field": persons_field.field_name,
                    "before": old_value,
                    "after": candidate.value,
                    "reason": candidate.reason,
                }
            )
        _apply_candidate(persons_field, candidate, replace_value=True)
    elif persons_field is not None and len(unique_persons) > 1:
        persons_field.candidate_values = [candidate.value for candidate in unique_persons]
        for candidate in unique_persons:
            _append_evidence(persons_field, candidate.evidence())

    premium_field = next(
        (
            field_item
            for field_item in enriched.fields
            if field_item.field_name.strip().lower().startswith("10a.")
            and re.search(r"premiums?|subscription\s+charges", field_item.field_name, re.IGNORECASE)
        ),
        None,
    )
    premium_candidates = _unique_candidates(_premium_candidates(document), numeric=True)
    if premium_field is None and len(premium_candidates) == 1:
        rule = next(
            (
                item
                for item in rule_list
                if item.label.strip().lower().startswith("10a.")
                and re.search(r"premiums?|subscription\s+charges", item.label, re.IGNORECASE)
                and "schedule a" in str(item.source or "").lower()
            ),
            None,
        )
        if rule is not None:
            candidate = premium_candidates[0]
            premium_field = NormalizedExtractionField(
                field_name=rule.label,
                value=candidate.value,
                candidate_values=[candidate.value],
                confidence=0.92,
            )
            _apply_candidate(premium_field, candidate, replace_value=True)
            enriched.fields.append(premium_field)
            corrections.append(
                {
                    "field": premium_field.field_name,
                    "before": "",
                    "after": candidate.value,
                    "reason": candidate.reason,
                }
            )
    elif premium_field is not None and len(premium_candidates) == 1:
        candidate = premium_candidates[0]
        old_value = premium_field.value
        if _decimal_identity(old_value) != _decimal_identity(candidate.value):
            corrections.append(
                {
                    "field": premium_field.field_name,
                    "before": old_value,
                    "after": candidate.value,
                    "reason": candidate.reason,
                }
            )
        _apply_candidate(premium_field, candidate, replace_value=True)
    elif premium_field is not None and len(premium_candidates) > 1:
        premium_field.candidate_values = [candidate.value for candidate in premium_candidates]
        for candidate in premium_candidates:
            _append_evidence(premium_field, candidate.evidence())

    _add_explicit_rule_fields(enriched, document, rule_list, corrections)

    for field_item in enriched.fields:
        if field_item is persons_field:
            continue
        evidence = _find_field_evidence(field_item, document, rule_list)
        if evidence is not None:
            _apply_candidate(field_item, evidence, replace_value=False)

    for broker in enriched.schedule_a_broker_rows:
        _enrich_broker_evidence(broker, document)

    # Apply after generic evidence enrichment so a provider's letter date or
    # confidence cannot overwrite the explicitly labelled contract period.
    period_ambiguities = _reconcile_contract_period(enriched, document, corrections)
    carrier_ambiguities = _reconcile_carrier_worksheets(enriched, document, corrections)

    raw = dict(enriched.raw) if isinstance(enriched.raw, dict) else {"provider_raw": enriched.raw}
    group_count = len(document.groups) or (1 if document.lines else 0)
    ambiguities = _combined_compensation_ambiguities(enriched, document)
    ambiguities.extend(period_ambiguities)
    ambiguities.extend(carrier_ambiguities)
    if column_ambiguous:
        ambiguities.append({"type": "lives_covered_column_ambiguous",
            "reason": "Covered-lives column positions, contracts or row counts are ambiguous; review its count."})
        if persons_field is not None:
            persons_field.confidence = min(float(persons_field.confidence or 0), 0.5)
            persons_field.decision = "REVIEW_REQUIRED"
    manual_fallback = bool(raw.get("manual_review_required"))
    if group_count > 1 or manual_fallback:
        # One dashboard filing cannot safely collapse distinct Schedule A
        # identities into a single set of values. Preserve every candidate and
        # force review before mapping or any downstream update can use it.
        # Source-backed fields newly recovered by a customer rule must also
        # preserve an unvalidated provider-failure review hold.
        for field_item in enriched.fields:
            field_item.confidence = min(float(field_item.confidence or 0), 0.5)
            field_item.decision = "REVIEW_REQUIRED"
        for broker in enriched.schedule_a_broker_rows:
            broker.confidence = min(float(broker.confidence or 0), 0.5)
            broker.decision = "REVIEW_REQUIRED"
    raw["semantic_resolution"] = {
        "version": 1,
        "decision": (
            "REVIEW_REQUIRED"
            if group_count > 1 or manual_fallback or len(unique_persons) > 1 or ambiguities
            else "RESOLVED"
        ),
        "group_count": group_count,
        "groups": [
            {
                "group_id": group.group_id,
                "page": group.page,
                "contract_number": group.contract_number,
                "persons_covered": group.persons_covered,
            }
            for group in document.groups
        ],
        "corrections": corrections,
        "lives_covered_columns": [{"page": column.page, "counts": list(column.counts),
            "selected": column.value if not column_ambiguous else None,
            "contracts": list(column.contracts), "ambiguous": column.ambiguous} for column in columns],
        "ambiguities": ambiguities,
    }
    enriched.raw = raw
    return enriched


def _reconcile_carrier_worksheets(result, document, corrections):
    """Prefer complete labelled source tables over generic provider numbers.

    Import lazily: extractor adapters also call the semantic layer. No provider
    requests are made here, and an incomplete paid broker table returns no rows.
    """
    from app.services.extractor import (
        extract_bcbsma_schedule_a_worksheet_fields,
        extract_columnar_broker_compensation_rows,
        extract_eyemed_payment_records,
        is_eyemed_schedule_a_worksheet,
        normalize_ocr_text,
        sum_money_values,
    )
    ambiguities = []

    def apply(label, value, page, source, *, review=False):
        item = next((f for f in result.fields if normalize_name(f.field_name) == normalize_name(label)), None)
        if item is None:
            item = NormalizedExtractionField(field_name=label, value="", confidence=.94)
            result.fields.append(item)
        before = item.value
        item.value = value
        item.candidate_values = [value]
        item.page = page
        item.source_text = source
        item.evidence = [SourceEvidence(provider="Schedule A labelled carrier table", page=page, source_text=source)]
        item.confidence = .5 if review else .94
        item.decision = "REVIEW_REQUIRED" if review else "AUTOMATIC"
        if before != value:
            corrections.append({"field": label, "before": before, "after": value,
                                "reason": "labelled_carrier_table"})
        return item

    financials = {}
    for page, text in document.pages.items():
        for parsed in extract_bcbsma_schedule_a_worksheet_fields(text, page):
            if parsed.field_name.startswith("9"):
                financials.setdefault(parsed.field_name, []).append(parsed)
    for label, candidates in financials.items():
        identities = {_decimal_identity(candidate.value) for candidate in candidates}
        if len(identities) == 1:
            parsed = candidates[0]
            apply(label, parsed.value, parsed.page, parsed.source_text)
        else:
            ambiguities.append({"type": "carrier_worksheet_financial_conflict",
                                "reason": f"Conflicting labelled carrier amounts for {label}."})
            existing = _field_by_prefix(result.fields, label.split(".")[0] + ".")
            if existing is not None:
                existing.confidence = .5
                existing.decision = "REVIEW_REQUIRED"

    rows = extract_columnar_broker_compensation_rows(list(document.pages.items()))
    if rows:
        # Keep the established classification of additional compensation in
        # fee totals, but expose its separate component and require review.
        has_additional = any(m.purpose == "Additional Compensation" for r in rows for m in r.fee_rows)
        source = "\n".join(r.fee_source_text or "" for r in rows)
        apply("3b. Amount of Commissions", sum_money_values(*(r.commission_total for r in rows)) or "0",
              rows[0].source_page, source)
        apply("3c. Amount of Fees", sum_money_values(*(r.fee_total for r in rows)) or "0",
              rows[0].source_page, source, review=has_additional)
        for row in rows:
            row.commission_source_text = source
            row.fee_source_text = source
            row.evidence = [SourceEvidence(provider="Schedule A labelled carrier table", page=row.source_page, source_text=source)]
            if has_additional:
                row.confidence = .5
                row.decision = "REVIEW_REQUIRED"
        result.schedule_a_broker_rows = rows
        if has_additional:
            ambiguities.append({"type": "additional_compensation_classification_required",
                                "reason": "Separate additional compensation is retained in the existing fee total; review its classification. Explicit paid-fee column is preserved in source evidence."})

    records = []
    for page, text in document.pages.items():
        normalized = normalize_ocr_text(text)
        if is_eyemed_schedule_a_worksheet(normalized):
            records.extend((page, r) for r in extract_eyemed_payment_records(normalized))
    if records:
        highest_page, highest = max(records, key=lambda pair: int(pair[1]["persons_covered"].replace(",", "")))
        contracts = {r["contract_number"] for _, r in records}
        multiple = len(contracts) > 1
        source = "Highest subscribers-and-dependents covered count selected: " + highest["persons_covered"] + "\n"
        source += "\n".join(r["contract_number"] + ": " + r["persons_covered"] for _, r in records)
        apply("1e. Persons Covered (End of Policy Year)", highest["persons_covered"], highest_page, source, review=multiple)
        if multiple:
            ambiguities.append({"type": "eyemed_contract_grouping_required",
                                "reason": "Multiple EyeMed source contracts: highest lives is a proposal, not proof of a single FTW policy grouping. Review combined contract and financial amounts."})
            for item in result.fields:
                if item.field_name.startswith(("1d.", "3b.", "10a.")):
                    item.confidence = min(float(item.confidence or 0), .5)
                    item.decision = "REVIEW_REQUIRED"
            for row in result.schedule_a_broker_rows:
                row.confidence = min(float(row.confidence or 0), .5)
                row.decision = "REVIEW_REQUIRED"
    return ambiguities


def _reconcile_contract_period(result, document, corrections):
    matches = [(page, period) for page, text in document.pages.items()
               for period in explicit_contract_periods(text)]
    identities = {(period.beginning, period.ending) for _, period in matches}
    if len(identities) > 1:
        return [{"type": "contract_period_ambiguous",
                 "reason": "Conflicting explicitly labelled contract periods; select the correct policy."}]
    if not matches:
        return []
    page, period = matches[0]
    month_precision = any(p.month_precision for _, p in matches)
    for prefix, label, value in (("1f.", "1f. Policy Year Beginning Date", period.beginning),
                                 ("1g.", "1g. Policy Year Ending Date", period.ending)):
        item = _field_by_prefix(result.fields, prefix)
        if item is None:
            item = NormalizedExtractionField(field_name=label, value="", confidence=0.9)
            result.fields.append(item)
        before = item.value
        source = period.source_text
        if month_precision:
            source += "\nMonth-only source: day boundaries derived; confirm exact policy dates."
        item.value = value
        item.candidate_values = [value]
        item.page = page
        item.source_text = source
        item.evidence = [SourceEvidence(provider="Schedule A explicit contract period", page=page, source_text=source)]
        item.confidence = 0.5 if month_precision else 0.9
        if before != value:
            corrections.append({"field": label, "before": before, "after": value,
                                "reason": "explicit_contract_period"})
    return ([{"type": "contract_period_month_precision",
              "reason": "The source prints months, not exact days; confirm derived policy-date boundaries."}]
            if month_precision else [])


def _add_explicit_rule_fields(
    result: NormalizedExtractionResult,
    document: SemanticDocument,
    rules: list[FieldRule],
    corrections: list[dict[str, str]],
) -> None:
    """Add only values explicitly bound to a published Schedule A label.

    This is the layout-independent path for newly published Field Rules and
    aliases.  It intentionally requires a visible delimiter (``:`` or ``=``)
    and never infers a value merely because it is nearby.
    """
    existing = {
        normalize_name(name)
        for field_item in result.fields
        for name in (field_item.field_name,)
        if name
    }
    for rule in rules:
        if "schedule a" not in str(rule.source or "").lower():
            continue
        if "reference" in str(rule.field_type or "").lower():
            continue
        rule_names = [rule.label, rule.ftw_field, *rule.aliases]
        identities = {normalize_name(name) for name in rule_names if str(name or "").strip()}
        if existing.intersection(identities):
            continue
        candidates = _explicit_rule_candidates(rule, document)
        numeric = _rule_is_numeric(rule)
        unique = _unique_candidates(candidates, numeric=numeric)
        if not unique:
            continue
        selected = unique[0]
        field_item = NormalizedExtractionField(
            field_name=rule.label,
            value=selected.value,
            confidence=0.88 if len(unique) == 1 else 0.5,
        )
        _apply_candidate(field_item, selected, replace_value=True)
        field_item.candidate_values = [candidate.value for candidate in unique]
        for candidate in unique[1:]:
            _append_evidence(field_item, candidate.evidence())
        result.fields.append(field_item)
        existing.add(normalize_name(rule.label))
        if len(unique) == 1:
            corrections.append(
                {
                    "field": rule.label,
                    "before": "",
                    "after": selected.value,
                    "reason": "explicit_published_alias_value",
                }
            )


def _explicit_rule_candidates(
    rule: FieldRule,
    document: SemanticDocument,
) -> list[SemanticCandidate]:
    candidates: list[SemanticCandidate] = []
    aliases = sorted(
        {
            str(name).strip()
            for name in (rule.label, rule.ftw_field, *rule.aliases)
            if str(name or "").strip()
        },
        key=len,
        reverse=True,
    )
    for line in document.lines:
        for alias in aliases:
            match = re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}\s*[:=]\s*(.+)$",
                line.normalized,
                re.IGNORECASE,
            )
            if not match:
                continue
            value = _parse_explicit_rule_value(match.group(1), rule)
            if value is None:
                continue
            candidates.append(
                SemanticCandidate(
                    value=value,
                    page=line.page,
                    row=line.row,
                    source_text=document.context(line, radius=1),
                    reason="explicit_published_alias_value",
                )
            )
            break
    return candidates


def _parse_explicit_rule_value(text: str, rule: FieldRule) -> str | None:
    clean = re.sub(r"\s+", " ", text).strip()
    placeholder = normalize_name(clean)
    if placeholder in {"none", "n a", "na", "unknown", "blank", "not available"} or re.search(
        r"\b(?:to\s+be\s+provided|plan\s+will\s+provide)\b",
        placeholder,
    ):
        return None
    validators = {str(item).strip().lower() for item in rule.validators}
    semantic_type = _rule_semantic_type(rule)
    if "currency" in validators or semantic_type == "currency":
        match = re.search(r"(?:\$\s*)?(-?\(?[\d,]+(?:\.\d{1,2})?\)?)", clean)
        return match.group(1) if match else None
    if "date" in validators or semantic_type == "date":
        match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)
        return match.group(0) if match else None
    if "ein" in validators or semantic_type == "ein":
        match = re.search(r"\b\d{2}-?\d{7}\b", clean)
        return match.group(0) if match else None
    if "integer" in validators or semantic_type == "integer":
        match = re.search(r"\b[\d,]+\b", clean)
        return match.group(0) if match else None
    # Text/enum/boolean fields still require a compact, explicit value.  A
    # paragraph after a label is more likely instructions than a field value.
    value = clean.strip(" |;")
    return value if value and len(value) <= 250 else None


def _rule_is_numeric(rule: FieldRule) -> bool:
    validators = {str(item).strip().lower() for item in rule.validators}
    return bool(
        validators.intersection({"currency", "integer", "number"})
        or _rule_semantic_type(rule) in {"currency", "integer"}
    )


def _rule_semantic_type(rule: FieldRule) -> str:
    field_type = str(rule.field_type or "").strip().lower()
    if field_type in {"currency", "amount", "money"}:
        return "currency"
    if field_type in {"integer", "number", "whole number"}:
        return "integer"
    if field_type in {"date", "ein"}:
        return field_type
    label = " ".join(
        str(value or "")
        for value in (rule.key, rule.label, rule.ftw_field, *rule.aliases)
    ).lower()
    if "ein" in label or "employer identification" in label:
        return "ein"
    if "date" in label or "year beginning" in label or "year ending" in label:
        return "date"
    if re.search(r"persons?\s+covered|covered\s+lives|number\s+of\s+(?:members|subscribers)", label):
        return "integer"
    if re.search(
        r"amount|premium|commission|fee|charge|claim|tax|retention|expense|dividend|refund",
        label,
    ):
        return "currency"
    return "text"


def _persons_covered_candidates(document: SemanticDocument) -> list[SemanticCandidate]:
    candidates: list[SemanticCandidate] = []
    for page, page_text in document.pages.items():
        # Principal-style worksheets put the authoritative Total(e) above the
        # Employees/Dependents breakdown.  A nearest-number extractor commonly
        # mistakes Employees for line 1e.
        principal_total = re.search(
            r"(?:Approximate\s*Number\s*of|ApproximateNumberof).*?"
            r"Total\s*\(?e\)?\s*([\d,]+).*?"
            r"(?:Persons\s*Covered|PersonsCovered).*?Employees",
            page_text,
            re.IGNORECASE | re.DOTALL,
        )
        if principal_total:
            value = principal_total.group(1).replace(",", "")
            candidates.append(
                SemanticCandidate(
                    value=value,
                    page=page,
                    row=0,
                    source_text=principal_total.group(0)[:1000],
                    reason="explicit_total_persons_covered",
                )
            )
    for line in document.lines:
        normalized = line.normalized
        standard = _STANDARD_PERSONS.search(normalized)
        if standard and not re.search(r"\b(?:employees?|dependents?|subscribers?|members?)\b", normalized, re.IGNORECASE):
            value = standard.group(1).replace(",", "")
            candidates.append(
                SemanticCandidate(
                    value=value,
                    page=line.page,
                    row=line.row,
                    source_text=document.context(line, radius=2),
                    reason="explicit_total_persons_covered",
                )
            )

        # Enrollment reports print subscribers first and the combined total
        # second, followed by carrier EIN, NAIC, and premium.
        if re.search(r"\$\s*[\d,]+(?:\.\d{2})?", normalized):
            table_match = re.search(
                r"\b([\d,]+)\s+([\d,]+)\s+\d{9}\s+\d{4,6}\s+\$",
                normalized,
            )
            if table_match and _page_mentions_all(
                document, line.page, ("subscribers", "dependents", "covered")
            ):
                total_value = table_match.group(2).replace(",", "")
                if int(total_value) <= 0:
                    continue
                candidates.append(
                    SemanticCandidate(
                        value=total_value,
                        page=line.page,
                        row=line.row,
                        source_text=_header_plus_line(
                            document,
                            line,
                            "subscribers and dependents covered",
                        ),
                        reason="explicit_total_persons_covered",
                    )
                )

        # Anthem-style benefit tables use subscriber/total notation.  The
        # value after the slash is the total covered count.
        fraction = re.search(r"\b([\d,]+)\s*/\s*([\d,]+)\b", normalized)
        if (
            fraction
            and ("$" in normalized or bool(re.search(r"\bpremium\b", normalized, re.IGNORECASE)))
            and _page_mentions_all(document, line.page, ("subscribers", "members"))
        ):
            candidates.append(
                SemanticCandidate(
                    value=fraction.group(2).replace(",", ""),
                    page=line.page,
                    row=line.row,
                    source_text=_header_plus_line(document, line, "subscribers/members"),
                    reason="explicit_total_persons_covered",
                )
            )
    return candidates


def _find_field_evidence(
    field_item: NormalizedExtractionField,
    document: SemanticDocument,
    rules: list[FieldRule],
) -> SemanticCandidate | None:
    aliases = _aliases_for_field(field_item.field_name, rules)
    best: tuple[int, SemanticCandidate] | None = None
    for line in document.lines:
        if not _line_contains_value(line.text, field_item.value):
            continue
        context = document.context(line, radius=3)
        context_key = normalize_name(context)
        alias_score = max(
            (len(normalize_name(alias)) for alias in aliases if normalize_name(alias) in context_key),
            default=0,
        )
        section_score = _section_score(field_item.field_name, context)
        score = alias_score + section_score
        if score <= 0:
            continue
        candidate = SemanticCandidate(
            value=field_item.value,
            page=line.page,
            row=line.row,
            source_text=context,
            reason="label_and_section_evidence",
        )
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def _premium_candidates(document: SemanticDocument) -> list[SemanticCandidate]:
    candidates: list[SemanticCandidate] = []
    for line in document.lines:
        normalized = line.normalized
        page_text = document.pages.get(line.page, "")
        direct = re.search(
            r"(?:total\s+)?premiums?(?:\s+or\s+subscription\s+charges)?(?:\s+paid\s+to\s+carrier)?[^$\d]{0,80}\$\s*([\d,]+(?:\.\d{1,2})?)",
            normalized,
            re.IGNORECASE,
        )
        if direct:
            candidates.append(
                SemanticCandidate(
                    value=direct.group(1),
                    page=line.page,
                    row=line.row,
                    source_text=document.context(line, radius=2),
                    reason="explicit_line_10a_premium",
                )
            )
            continue

        carrier_total = re.search(
            r"\btotal\s*:\s*\$\s*([\d,]+(?:\.\d{1,2})?)",
            normalized,
            re.IGNORECASE,
        )
        if carrier_total and re.search(
            r"Payments\s+Received\s+by\s+carrier\s+from\s+plan",
            page_text,
            re.IGNORECASE,
        ):
            section_header = next(
                (
                    candidate.normalized
                    for candidate in document.page_lines(line.page)
                    if re.search(
                        r"Payments\s+Received\s+by\s+carrier\s+from\s+plan",
                        candidate.text,
                        re.IGNORECASE,
                    )
                ),
                "",
            )
            candidates.append(
                SemanticCandidate(
                    value=carrier_total.group(1),
                    page=line.page,
                    row=line.row,
                    source_text="\n".join(
                        value for value in (section_header, document.context(line, radius=2)) if value
                    ),
                    reason="explicit_carrier_payment_total",
                )
            )
            continue

        if (
            "$" in normalized
            and re.search(r"\b10a\.?\s+Premium\b", page_text, re.IGNORECASE)
            and re.search(r"Non\s+Experience\s+Rated", page_text, re.IGNORECASE)
            and not re.search(r"commissions?|fees?", normalized, re.IGNORECASE)
        ):
            values = re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", normalized)
            if len(values) == 1:
                candidates.append(
                    SemanticCandidate(
                        value=values[0],
                        page=line.page,
                        row=line.row,
                        source_text=document.context(line, radius=3),
                        reason="explicit_line_10a_benefit_row",
                    )
                )
    return candidates


def _combined_compensation_ambiguities(
    result: NormalizedExtractionResult,
    document: SemanticDocument,
) -> list[dict[str, str | int]]:
    commission = _field_by_prefix(result.fields, "3b.")
    fee = _field_by_prefix(result.fields, "3c.")
    if commission is None or fee is None:
        return []
    if _decimal_identity(commission.value) != _decimal_identity(fee.value):
        return []
    amount = _decimal_identity(commission.value)
    if amount in {None, Decimal("0")}:
        return []
    for line in document.lines:
        page_text = document.pages.get(line.page, "")
        if not re.search(r"commissions?\s*/\s*fees?", page_text, re.IGNORECASE):
            continue
        context = document.context(line, radius=3)
        if amount not in {_decimal_identity(value) for value in _MONEY.findall(context)}:
            continue
        return [
            {
                "type": "combined_commission_fee_source",
                "page": line.page,
                "value": str(commission.value),
                "source_text": context,
            }
        ]
    return []


def _aliases_for_field(field_name: str, rules: list[FieldRule]) -> list[str]:
    normalized = normalize_name(field_name)
    for rule in rules:
        names = [rule.key, rule.label, rule.ftw_field, *rule.aliases]
        if normalized in {normalize_name(name) for name in names if name}:
            return [name for name in [rule.label, rule.ftw_field, *rule.aliases] if name]
    label_without_number = re.sub(r"^\s*\d+[a-z]?(?:\([^)]*\))*[.)]?\s*", "", field_name, flags=re.IGNORECASE)
    return [field_name, label_without_number]


def _section_score(field_name: str, context: str) -> int:
    label = field_name.strip().lower()
    normalized = context.lower()
    if label.startswith("1"):
        return 40 if re.search(r"carrier|coverage|contract|policy", normalized) else 0
    if label.startswith("3"):
        return 40 if re.search(r"commission|fees?|broker|agent|recipient", normalized) else 0
    if label.startswith("9"):
        return 40 if re.search(r"experience[- ]rated|part\s+(?:ii|iii)|line\s*9", normalized) else 0
    if label.startswith("10"):
        return 40 if re.search(r"non[- ]?experience|premium|line\s*10", normalized) else 0
    return 10


def _enrich_broker_evidence(row: ScheduleABrokerRow, document: SemanticDocument) -> None:
    name_key = normalize_name(row.name)
    if not name_key:
        return
    commission = _decimal_identity(row.commission_total)
    fee = _decimal_identity(row.fee_total)
    for line in document.lines:
        context = document.context(line, radius=3)
        if name_key not in normalize_name(context):
            continue
        amounts = {_decimal_identity(value) for value in _MONEY.findall(context)}
        required = {value for value in (commission, fee) if value not in {None, Decimal("0")}}
        if required and not required.issubset(amounts):
            continue
        row.source_page = line.page
        _append_row_evidence(
            row,
            SourceEvidence(
                provider="Schedule A semantic layer",
                page=line.page,
                source_text=context,
                table_cell=(line.row, 0),
            ),
        )
        if commission is not None:
            row.commission_source_text = context
        if fee is not None:
            row.fee_source_text = context
        return


def _apply_candidate(
    field_item: NormalizedExtractionField,
    candidate: SemanticCandidate,
    *,
    replace_value: bool,
) -> None:
    if replace_value:
        field_item.value = candidate.value
        field_item.candidate_values = [candidate.value]
        field_item.confidence = max(float(field_item.confidence or 0), 0.92)
    field_item.page = candidate.page
    field_item.source_text = candidate.source_text
    _append_evidence(field_item, candidate.evidence(
        "Schedule A covered-lives column" if candidate.reason == "highest_lives_covered_column"
        else "Schedule A semantic layer"))


def _append_evidence(field_item: NormalizedExtractionField, evidence: SourceEvidence) -> None:
    identity = (
        evidence.provider,
        evidence.page,
        evidence.source_text,
        evidence.bounding_box,
        evidence.table_cell,
    )
    existing = {
        (item.provider, item.page, item.source_text, item.bounding_box, item.table_cell)
        for item in field_item.evidence
    }
    if identity not in existing:
        field_item.evidence.append(evidence)


def _append_row_evidence(row: ScheduleABrokerRow, evidence: SourceEvidence) -> None:
    identity = (evidence.provider, evidence.page, evidence.source_text, evidence.table_cell)
    existing = {
        (item.provider, item.page, item.source_text, item.table_cell)
        for item in row.evidence
    }
    if identity not in existing:
        row.evidence.append(evidence)


def _unique_candidates(
    candidates: list[SemanticCandidate], *, numeric: bool = False
) -> list[SemanticCandidate]:
    output: list[SemanticCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = (
            str(_decimal_identity(candidate.value))
            if numeric and _decimal_identity(candidate.value) is not None
            else _integer_identity(candidate.value) or candidate.value
        )
        if identity in seen:
            continue
        seen.add(identity)
        output.append(candidate)
    return output


def _field_by_prefix(
    fields: list[NormalizedExtractionField], prefix: str
) -> NormalizedExtractionField | None:
    return next(
        (field_item for field_item in fields if field_item.field_name.strip().lower().startswith(prefix.lower())),
        None,
    )


def _integer_identity(value: str | None) -> str | None:
    clean = re.sub(r"\D", "", str(value or ""))
    return str(int(clean)) if clean else None


def _decimal_identity(value: str | None) -> Decimal | None:
    clean = re.sub(r"[^0-9.()-]", "", str(value or "")).replace("(", "-").replace(")", "")
    if not clean:
        return None
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def _line_contains_value(text: str, value: str) -> bool:
    expected_decimal = _decimal_identity(value)
    if expected_decimal is not None and any(
        _decimal_identity(candidate) == expected_decimal for candidate in _MONEY.findall(text)
    ):
        return True
    expected = normalize_name(value)
    return bool(expected and expected in normalize_name(text))


def _page_mentions(document: SemanticDocument, page: int, phrase: str) -> bool:
    return normalize_name(phrase) in normalize_name(document.pages.get(page, ""))


def _page_mentions_all(
    document: SemanticDocument, page: int, phrases: tuple[str, ...]
) -> bool:
    normalized = normalize_name(document.pages.get(page, ""))
    return all(normalize_name(phrase) in normalized for phrase in phrases)


def _header_plus_line(document: SemanticDocument, line: SemanticLine, phrase: str) -> str:
    phrase_key = normalize_name(phrase)
    header_lines = [
        candidate.normalized
        for candidate in document.page_lines(line.page)
        if phrase_key in normalize_name(candidate.text)
    ]
    if not header_lines and phrase_key == normalize_name("subscribers and dependents covered"):
        header_lines = [
            candidate.normalized
            for candidate in document.page_lines(line.page)
            if any(
                token in normalize_name(candidate.text)
                for token in ("subscribers", "dependents covered", "covered at end")
            )
        ]
    if not header_lines and phrase_key == normalize_name("subscribers/members"):
        header_lines = [
            candidate.normalized
            for candidate in document.page_lines(line.page)
            if any(token in normalize_name(candidate.text) for token in ("subscribers", "members"))
        ]
    header = "\n".join(header_lines[:4])
    values = [value for value in (header, line.normalized) if value]
    return "\n".join(dict.fromkeys(values))
