"""Reject a structured name token only when a complete source row disproves it."""
import re
from decimal import Decimal, InvalidOperation

from app.models import ScheduleABrokerRow, SourceEvidence
from app.services.schedule_a_customer_rules import default_blank_organization_codes


_CODE_LABEL = re.compile(r'\bOrg(?:ani[sz]ation(?:al)?)?[.\s_-]*(?:Code|Type)\b', re.I)


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
