"""Distinguish organization codes from other values printed in recipient tables."""
import re
from decimal import Decimal, InvalidOperation

from app.models import ScheduleABrokerRow, SourceEvidence
from app.services.schedule_a_customer_rules import default_blank_organization_codes


_CODE_LABEL = re.compile(r'\bOrg(?:ani[sz]ation(?:al)?)?[\W_]*(?:Codes?|Types?)\b', re.I)


def _identity(row):
    return tuple(re.sub(r'[^a-z0-9]', '', str(getattr(row, key) or '').casefold())
                 for key in ('name', 'address_line_1', 'address_line_2', 'city', 'state', 'zip_code'))


def _money(value):
    try:
        amount = Decimal(re.sub(r'[$,\s]', '', str(value)))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def reconcile_source_name_tokens(result, source_rows, source_pages):
    """Source rows must come from a complete, no-code compensation layout parser.

    The caller supplies current-document pages only. Never turn an invalid code
    into a blank just because it is alphabetic, or accept a name-only match.
    """
    if (not isinstance(result.raw, dict) or not source_rows
            or any(_CODE_LABEL.search(text) for _, text in source_pages)):
        return
    rows = result.schedule_a_broker_rows
    for index, row in enumerate(rows):
        candidate = str(row.organization_code or '').strip()
        if candidate == '3' and not row.organization_code_defaulted:
            # A merge may already have the approved blank-source value while
            # losing its provenance flag. Require the app's original normalized
            # provider row, not just the current value 3, to prove this case.
            upstream = []
            for stage in (result.raw or {}).get('extraction_stages', []):
                if stage.get('stage') != 'structured_provider_normalized':
                    continue
                for item in stage.get('broker_rows', []):
                    try:
                        original = ScheduleABrokerRow.model_validate(item)
                    except (ValueError, TypeError):
                        continue
                    if _identity(original) == _identity(row) and all(
                            _money(getattr(original, key)) is not None
                            and _money(getattr(original, key)) == _money(getattr(row, key))
                            for key in ('commission_total', 'fee_total')):
                        upstream.append(str(original.organization_code or '').strip())
            if len(upstream) != 1:
                continue
            candidate = upstream[0]
        if (not re.fullmatch(r'[A-Za-z][A-Za-z.&\'-]{0,15}', candidate)
                or not re.search(r'(?:^|\s)'+re.escape(candidate)+r'\s*$', row.name or '', re.I)
                or row.decision == 'REVIEW_REQUIRED'
                or any(v.status == 'ERROR' for v in row.validation_results)
                or any(_CODE_LABEL.search(e.source_text or '') for e in row.evidence)):
            continue
        identity = _identity(row)
        if not all(identity[i] for i in (0, 1, 3, 4, 5)) or sum(_identity(r) == identity for r in rows) != 1:
            continue
        matches = [source for source in source_rows if _identity(source) == identity
                   and source.organization_code_defaulted and source.decision == 'AUTOMATIC'
                   and not any(v.status == 'ERROR' for v in source.validation_results)
                   and source.source_page and (row.source_page is None or row.source_page == source.source_page)
                   and all(_money(getattr(row, key)) is not None
                           and _money(getattr(row, key)) == _money(getattr(source, key))
                           for key in ('commission_total', 'fee_total'))]
        if len(matches) != 1:
            continue
        source = matches[0]
        proof = next((e for e in source.evidence if e.page == source.source_page and e.source_text), None)
        if proof is None:
            continue
        corrected = row.model_copy(deep=True)
        corrected.organization_code = None
        corrected = default_blank_organization_codes([corrected])[0]
        corrected.evidence.append(SourceEvidence(provider='Current-document no-code recipient table',
            page=proof.page, source_text=proof.source_text))
        rows[index] = corrected
        result.raw.setdefault('source_organization_code_reconciliations', []).append({
            'broker_index': index, 'rejected_provider_candidate': candidate,
            'merged_code_before_reconciliation': row.organization_code,
            'action': 'REJECT_NAME_TOKEN_THEN_APPLY_APPROVED_BLANK_CODE_DEFAULT',
            'source_page': proof.page, 'source_text': proof.source_text,
            'reason': 'Complete matching recipient identity and payment columns place this token in the name; no organization-code column or label is present.',
        })


_AGENT_TABLE = re.compile(
    r"Annual\s+Policy\s+Information\s+Report.*?"
    r"(?m:^[ \t]*Agent[ \t]+Name and Address of Each Recipient of[^\n]*\n"
    r"[ \t]*Number[ \t]+Fees and/or Commissions[^\n]*Commissions Paid[^\n]*Fees Paid[^\n]*$)",
    re.I | re.S,
)
_AGENT_PAYMENT_ROW = re.compile(
    r"^[ \t]*(?P<number>[A-Z]+-\d+)[ \t]*(?P<name>[^$\n]+?)"
    r"[ \t]+\$[ \t]*(?P<commission>[\d,.]+)[ \t]+\$[ \t]*(?P<fee>[\d,.]+)[^\n]*\n"
    r"[ \t]*(?P<address>\d+[^\n]+)\n"
    r"[ \t]*(?P<city>[A-Z .'-]+?),?[ \t]+(?P<state>[A-Z]{2})[ \t]+(?P<zip>\d{5}(?:-\d{4})?)[ \t]*$",
    re.I | re.M,
)


def reconcile_source_agent_numbers(result, source_pages):
    """Use a complete same-page recipient row to distinguish IDs from codes."""
    rows = result.schedule_a_broker_rows
    corrected_numbers = set()
    for page, text in source_pages:
        header = _AGENT_TABLE.search(text)
        if not header or _CODE_LABEL.search(text):
            continue
        for match in _AGENT_PAYMENT_ROW.finditer(text, header.end()):
            source_row = ScheduleABrokerRow(
                name=match["name"], address_line_1=match["address"].strip(),
                city=match["city"], state=match["state"], zip_code=match["zip"],
            )
            matches = [
                row for row in rows
                if _identity(row) == _identity(source_row)
                and row.source_page in (None, page)
                and str(row.organization_code or "").casefold() == match["number"].casefold()
                and not any(_CODE_LABEL.search(e.source_text or "") for e in row.evidence)
                and _money(row.commission_total) == _money(match["commission"])
                and _money(row.fee_total) == _money(match["fee"])
                and all(_money(match[key]) is not None for key in ("commission", "fee"))
            ]
            if len(matches) != 1:
                continue
            row = matches[0]
            corrected_numbers.add(str(row.organization_code or "").casefold())
            row.organization_code = None
            row.organization_code_defaulted = False
            row.evidence.append(SourceEvidence(
                provider="Printed agent identifier, separate from the organization code",
                page=page, source_text=match[0].strip(),
            ))
    if corrected_numbers and not any(
        str(row.organization_code or "").casefold() in corrected_numbers for row in rows
    ):
        # The numeric destination default is applied to the verified recipient.
        # Keep the printed identifier in the row evidence and original trace.
        result.fields = [
            field for field in result.fields
            if not (field.field_name.startswith("3e.") and field.value.strip().casefold() in corrected_numbers
                    and not any(_CODE_LABEL.search(e.source_text or "") for e in field.evidence))
        ]


_CONTRACT_PAYMENT_TABLE = re.compile(
    r"^[ \t]*Contract or[ \t]+\(a\) Name and address of the agents or brokers"
    r"[ \t]+\(b\) Amount of[^\n]*\n"
    r"[ \t]*Identification[ \t]+to whom commissions or fees were paid\."
    r"[ \t]+(?P<commission_header>commissions paid)[ \t]+(?P<fees_header>Amount)[^\n]*\n",
    re.I | re.M,
)
_CONTRACT_PAYMENT_ROW = re.compile(
    r"^[ \t]*(?P<contract>[A-Z0-9-]+)[ \t]{2,}(?P<name>[^$\n]+?)"
    r"[ \t]{2,}\$[ \t]*(?P<commission>[\d,.]+)[ \t]*\n"
    r"[ \t]+(?P<location>[^\n]+?),[ \t]+(?P<state>[A-Z]{2})[ \t]+"
    r"(?P<zip>\d{5}(?:-\d{4})?)[ \t]*$",
    re.I | re.M,
)


def reconcile_source_contract_numbers(result, source_pages):
    """Clear a provider code when the printed recipient row identifies it as a contract."""
    if not isinstance(result.raw, dict) or any(_CODE_LABEL.search(text) for _, text in source_pages):
        return
    proofs = []
    for page, text in source_pages:
        header = _CONTRACT_PAYMENT_TABLE.search(text)
        if header:
            following_rows = text[header.end():].lstrip("\n")
            match = _CONTRACT_PAYMENT_ROW.match(following_rows)
            label_line_start = text.rfind("\n", 0, header.start("commission_header")) + 1
            if (match and header.start("commission_header") - label_line_start
                    <= match.start("commission") < header.start("fees_header") - label_line_start):
                proofs.append((page, match))

    corrected_contracts = set()
    rows = result.schedule_a_broker_rows
    original_codes = [str(row.organization_code or "").strip().casefold() for row in rows]
    for row, candidate in zip(rows, original_codes):
        if (not candidate or candidate.isdecimal()
                or original_codes.count(candidate) != 1
                or any(_CODE_LABEL.search(e.source_text or "") for e in row.evidence)):
            continue
        name = (row.name or "").lower().split()
        matches = [
            (page, proof) for page, proof in proofs
            if proof["contract"].casefold() == candidate
            and row.source_page in (None, page)
            and name and (proof["name"] + " " + proof["location"]).lower().split()[:len(name)] == name
            and (row.state or "").casefold() == proof["state"].casefold()
            and row.zip_code == proof["zip"]
            and _money(proof["commission"]) is not None
            and _money(row.commission_total) == _money(proof["commission"])
            and _money(row.fee_total) == Decimal(0)
        ]
        if len(matches) != 1:
            continue
        page, proof = matches[0]
        corrected_contracts.add(candidate)
        result.raw.setdefault("source_organization_code_reconciliations", []).append({
            "rejected_provider_candidate": row.organization_code,
            "action": "REJECT_CONTRACT_NUMBER_THEN_APPLY_APPROVED_BLANK_CODE_DEFAULT",
            "source_page": page,
            "source_text": proof[0].strip(),
        })
        row.organization_code = None
        row.organization_code_defaulted = False

    if not corrected_contracts:
        return
    result.fields = [
        field for field in result.fields
        if not (field.field_name.startswith("3e.")
                and field.value.strip().casefold() in corrected_contracts
                and not _CODE_LABEL.search(field.source_text or "")
                and not any(_CODE_LABEL.search(e.source_text or "") for e in field.evidence))
    ]
