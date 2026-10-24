"""Keep recipient fee-purpose text separate from payment-category summaries."""
from decimal import Decimal, InvalidOperation
import re

from app.models import SourceEvidence


def is_commission_category(value):
    return bool(re.fullmatch(
        r'(?:(?:(?:base|sales)\s+)?commissions?(?:\s+paid)?|paid\s+commissions?)',
        str(value or '').strip(),re.I))


def is_payment_category(value):
    return is_commission_category(value) or bool(re.fullmatch(
        r'(?:fees?(?:\s+paid)?|'
        r'commissions?\s*(?:&|and|/)\s*fees?)',str(value or '').strip(),re.I))


def is_compensation_table_heading(row):
    """Recognize an exact generic header, not a recipient's printed purpose.

    The caller must separately establish zero fees without fee components.
    Require same-page row evidence, the literal recipient name and the sales /
    fees / additional-compensation header before that name. Any purpose label
    or occurrence after the recipient keeps the candidate for normal review.
    """
    if str(row.purpose or '').strip().casefold() != 'compensation':
        return False
    name = re.sub(r'\s+', ' ', str(row.name or '')).strip().casefold()
    if not name or not row.source_page:
        return False
    for evidence in row.evidence:
        if evidence.page != row.source_page:
            continue
        source = re.sub(r'\s+', ' ', str(evidence.source_text or '')).strip().casefold()
        index = source.find(name)
        if index < 0 or re.search(r'\bpurpose\b', source):
            continue
        heading, recipient = source[:index], source[index:]
        if (re.search(r'\bname\s+and\s+address\b', heading)
                and re.search(r'\b(?:soliciting\s+agent|broker\s+receiving)\b', heading)
                and all(re.search(r'\b' + label + r'\b', heading)
                        for label in ('sales', 'additional', 'commissions?', 'fees', 'compensation'))
                and not re.search(r'\bcompensation\b', recipient)):
            return True
    return False


def _amount(value):
    try:
        result=Decimal(re.sub(r'[$,\s]','',str(value)))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def reconcile_derived_fee_purpose_summary(result):
    """Use complete explicit fee components, never commission categories.

    Only the application's marked derived-category scalar is affected. Real
    printed scalar purposes, uncertain rows and raw provider stages stay intact.
    Distinct recipient purposes have no invented single aggregate purpose.
    """
    fields=[f for f in result.fields if f.field_name.startswith('3d.') and is_payment_category(f.value)
            and str(f.source_text or '').startswith('Derived recipient payment category:')
            and all(not str(c or '').strip() or is_payment_category(c) for c in f.candidate_values)
            and not any(v.status=='ERROR' for v in f.validation_results)]
    if not fields or not result.schedule_a_broker_rows:return
    purposes={};proof=[]
    for row in result.schedule_a_broker_rows:
        total=_amount(row.fee_total)
        if total is None or row.decision=='REVIEW_REQUIRED' or float(row.confidence or 0)<.8 or any(v.status=='ERROR' for v in row.validation_results):return
        if total==0 and not row.fee_rows:continue
        if not row.fee_rows or not row.evidence or not row.source_page:return
        parts=[_amount(p.amount) for p in row.fee_rows]
        if any(p is None for p in parts) or sum(parts,Decimal(0))!=total:return
        for part,amount in zip(row.fee_rows,parts):
            if amount==0:continue
            literal=re.sub(r'\s+',' ',str(part.purpose or '')).strip()
            if not literal or is_payment_category(literal):return
            purposes.setdefault(literal.casefold(),literal)
        proof.extend(e for e in row.evidence if e.page and e.source_text)
    if not purposes or not proof:return
    raw=dict(result.raw) if isinstance(result.raw,dict) else {'provider_raw':result.raw}
    audit=list(raw.get('fee_purpose_summary_reconciliation') or [])
    for field in fields:
        audit.append({'field':field.field_name,'observed_value':field.value,
            'fee_purposes':list(purposes.values()),'source':'Complete structured fee components; commission purposes excluded'})
    if len(purposes)>1:
        result.fields=[f for f in result.fields if not any(f is item for item in fields)]
    else:
        literal=next(iter(purposes.values()))
        source=f'Reconciled fee-purpose summary from recipient fee components: {literal}\n'
        source+='\n'.join(dict.fromkeys(e.source_text for e in proof))
        for field in fields:
            field.value=literal
            field.candidate_values=[c for c in field.candidate_values if not is_payment_category(c)]
            field.page=proof[0].page
            field.source_text=source
            field.evidence.append(SourceEvidence(provider='Reconciled recipient fee purpose',page=proof[0].page,source_text=source))
    raw['fee_purpose_summary_reconciliation']=audit
    result.raw=raw
