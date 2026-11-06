"""Explicit human roster confirmation; never an automatic extraction attestation."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from app.models import SourceEvidence
from app.services.schedule_a_broker_roster import _fingerprint, _money
from app.services.schedule_a_customer_rules import canonical_broker_payment_purpose
from app.services.xml_builder import schedule_a_broker_update_values


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def disclosure_items(status):
    """IDs depend only on immutable extracted evidence, never array position."""
    result = []
    for raw in status.other_disclosures if status else []:
        item = {k: v for k, v in raw.items() if k != 'id'}
        item['id'] = digest(item)
        if not any(previous['id'] == item['id'] for previous in result): result.append(item)
    return result


def review_token(rows, status, source_context, snapshot):
    return digest({'source': source_context, 'rows': [r.model_dump(mode='json') for r in rows],
        'source_pdf_sha256': status.source_sha256 if status else None,
        'disclosures': disclosure_items(status), 'snapshot': snapshot,
        'confirmation': status.reviewer_confirmation if status else None})


def confirmation_matches(rows, status):
    confirmation = status.reviewer_confirmation if status else None
    return bool(confirmation and confirmation.get('actor') and confirmation.get('reason')
        and confirmation.get('complete_list_confirmed') is True
        and confirmation.get('roster_sha256') == _fingerprint(rows)
        and (rows or confirmation.get('empty_list_evidence'))
        and all(_money(r.commission_total) is not None and _money(r.fee_total) is not None
                and r.decision != 'REVIEW_REQUIRED'
                and not any(v.status == 'ERROR' for v in r.validation_results) for r in rows))


def confirm_broker_review(rows, original_rows, status, request, *, actor, source_context, snapshot):
    if not status or request.expected_context != review_token(original_rows, status, source_context, snapshot):
        raise ValueError('Broker source or FTWilliam data changed. Refresh and review again.')
    if not actor or not source_context or not snapshot:
        raise ValueError('Authenticated reviewer, source and current FTWilliam destination are required.')
    if not request.complete_list_confirmed or not request.reason.strip():
        raise ValueError('Confirm the complete broker list and provide the client instruction or review reason.')
    if not rows and not request.empty_list_evidence.strip():
        raise ValueError('An empty replacement requires explicit evidence of no reportable brokers; a blank table is insufficient.')
    if len(rows) != len(request.row_sources):
        raise ValueError('Each proposed broker must reference its source or an explicit new-entry confirmation.')
    disclosures = {item['id']: item for item in disclosure_items(status)}
    decisions = {d.disclosure_id: d.treatment for d in request.disclosure_decisions}
    if len(decisions) != len(request.disclosure_decisions) or set(decisions) != set(disclosures):
        raise ValueError('Review every service-provider disclosure exactly once.')
    if 'UNRESOLVED' in decisions.values():
        raise ValueError('Unclassified disclosures remain unresolved. Complete their review before replacing brokers.')
    sources = request.row_sources
    if len(set(s for s in sources if s != 'NEW')) != len([s for s in sources if s != 'NEW']):
        raise ValueError('A source recipient cannot be counted twice.')
    used_indexes = set()
    used_disclosures = set()
    verified = []
    for draft, source in zip(rows, sources):
        row = draft.model_copy(deep=True)
        # The browser cannot supply proofs, confidence or evidence.
        row.validation_results = []
        row.evidence = []
        row.confidence = 0
        row.decision = 'UNASSESSED'
        row.commission_rows, row.fee_rows = [], []
        row.commission_source_text = row.fee_source_text = None
        if source.startswith('SOURCE:'):
            try: index = int(source[7:]); original = original_rows[index] if index >= 0 else None
            except (ValueError, IndexError): original = None
            if original is None: raise ValueError('The original broker row changed. Refresh and try again.')
            used_indexes.add(index)
            row.evidence = deepcopy(original.evidence)
            row.source_page = original.source_page
            # Full-list acknowledgement does not silently clear unrelated source errors.
            row.validation_results = [v.model_copy(deep=True) for v in original.validation_results
                if v.status == 'ERROR' and v.validator not in {
                    'complete_source_broker_roster', 'payment_classification_ambiguity',
                    'business_rule_confirmation', 'source_section_ambiguity'}]
            if row.validation_results: raise ValueError('Unresolved source issues require correction before roster confirmation.')
        elif source.startswith('DISCLOSURE:'):
            key = source[11:]; disclosure = disclosures.get(key)
            if disclosure is None or decisions.get(key) != 'INCLUDED':
                raise ValueError('Confirm that this disclosure belongs on the selected Schedule A.')
            used_disclosures.add(key)
            commission, fee, amount = _money(row.commission_total), _money(row.fee_total), _money(disclosure.get('amount'))
            if None in (commission, fee, amount) or Decimal(commission) + Decimal(fee) != Decimal(amount):
                raise ValueError('Classified commission plus fees must equal the original amount exactly.')
            row.source_page = disclosure.get('page')
            row.evidence = [SourceEvidence(page=disclosure.get('page'), provider='Reviewer-classified disclosure',
                source_text=disclosure.get('source_text') or disclosure.get('address'))]
        elif source == 'NEW':
            row.source_page = None
            row.evidence = [SourceEvidence(provider='Reviewer instruction', source_text=request.reason.strip())]
        else: raise ValueError('Unknown broker source reference.')
        if _money(row.commission_total) is None or _money(row.fee_total) is None:
            raise ValueError('Commission and fee amounts must be classified; unknown is not zero.')
        if not all(str(getattr(row, key) or '').strip() for key in ('name','address_line_1','city','state','zip_code')):
            raise ValueError('Complete the broker name, street, city, state and ZIP.')
        row.organization_code = str(row.organization_code or '').strip() or '3'
        row.purpose = canonical_broker_payment_purpose(row.commission_total,row.fee_total)
        verified.append(row)
    if used_disclosures != {k for k,v in decisions.items() if v == 'INCLUDED'}:
        raise ValueError('Every included disclosure requires one classified broker row.')
    excluded = request.excluded_source_indexes
    if len(set(excluded)) != len(excluded) or used_indexes & set(excluded) or used_indexes | set(excluded) != set(range(len(original_rows))):
        raise ValueError('Review or explicitly exclude every original broker row.')
    schedule_a_broker_update_values(verified, require_complete=True)
    reviewed = status.model_copy(deep=True)
    reviewed.reviewer_confirmation = {'actor': actor, 'at': datetime.now(timezone.utc).isoformat(),
        'reason': request.reason.strip(), 'complete_list_confirmed': True,
        'empty_list_evidence': request.empty_list_evidence.strip(), 'source_context_sha256': source_context,
        'snapshot_sha256': snapshot, 'roster_sha256': _fingerprint(verified),
        'disclosure_decisions': decisions, 'row_sources': sources, 'excluded_source_indexes': excluded,
        'original_rows': [r.model_dump(mode='json') for r in original_rows]}
    reviewed.reasons = []
    return verified, reviewed
