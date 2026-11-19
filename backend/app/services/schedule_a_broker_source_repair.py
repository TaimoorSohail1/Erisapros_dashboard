"""Reconcile typed source rows without changing provider history or reviewer edits."""
from decimal import Decimal
from collections import Counter

from app.models import NormalizedExtractionField, ScheduleABrokerRow, SourceEvidence
from app.services.schedule_a_broker_roster import _money, _key
from app.services.schedule_a_native_brokers import provider_supports_labelled_payments, recipient_sets_corroborate, SECTION_TOTALS, PAYMENT_COLUMNS, CLOSED_ROSTER


def repair_source_rows(result, independent_rows):
    """Recover mapping for the same complete recipient set, without approving it."""
    raw = result.raw if isinstance(result.raw, dict) else {}
    stage = next((s for s in raw.get('extraction_stages', []) if s.get('stage') == 'structured_provider_normalized'), {})
    try:
        provider_rows = [ScheduleABrokerRow.model_validate(row) for row in stage.get('broker_rows', [])]
    except (ValueError, TypeError):
        return False
    if (not independent_rows or not provider_rows
            or not recipient_sets_corroborate(provider_rows, independent_rows)
            or any(not r.name or not r.source_page or r.confidence < .8
                   or _money(r.commission_total) is None or _money(r.fee_total) is None
                   for r in independent_rows)
            or any(v.validator.startswith('reviewer_') and v.status == 'PASS'
                   for r in result.schedule_a_broker_rows for v in r.validation_results)):
        return False
    if any(not any(e.page == r.source_page and e.source_text for e in r.evidence) for r in independent_rows):
        return False
    before = [r.model_dump(mode='json') for r in result.schedule_a_broker_rows]
    result.schedule_a_broker_rows = [r.model_copy(deep=True) for r in independent_rows]
    exact_rows_and_printed_totals = (Counter(_key(r) for r in independent_rows) == Counter(_key(r) for r in provider_rows)
        and all(any(v.validator == SECTION_TOTALS and v.status == 'PASS' for v in r.validation_results) for r in independent_rows))
    # A stale generic quality flag can withhold a perfectly matching section
    # value. Clear it only when the complete provider row detail, source column
    # detail AND each already extracted section total all agree numerically.
    exact_classified_detail = Counter(_key(r) for r in independent_rows) == Counter(_key(r) for r in provider_rows)
    exact_classified_detail &= all(any(v.validator == PAYMENT_COLUMNS and v.status == 'PASS' for v in r.validation_results)
        and not any(v.status == 'ERROR' for v in r.validation_results) for r in independent_rows)
    coverage = raw.get('independent_source_page_coverage') or {}
    pages = coverage.get('pages') or []
    full_coverage = (type(coverage.get('page_count')) is int and coverage['page_count'] > 0
        and len(pages) == coverage['page_count']
        and {p.get('page') for p in pages} == set(range(1, coverage['page_count']+1))
        and all(p.get('readable') is True for p in pages))
    closed_detail = (exact_classified_detail and full_coverage
        and all(any(v.validator == CLOSED_ROSTER and v.status == 'PASS' for v in r.validation_results) for r in independent_rows))
    if provider_supports_labelled_payments(result, independent_rows) or exact_rows_and_printed_totals or closed_detail:
        set_corroborated_totals(result, independent_rows,
            corroboration='complete original provider recipient payments and independently labelled source category columns')
    raw['broker_source_mapping_recovery'] = {'previous_rows': before,
        'reason': 'Full provider recipient identities agree with the independently read physical source; component and category mapping recovered. Complete-list verification remains required.'}
    result.raw = raw
    return before != [r.model_dump(mode='json') for r in result.schedule_a_broker_rows]


def set_corroborated_totals(result, rows, *, corroboration):
    for prefix, attribute, label in (('3b.', 'commission_total', '3b. Amount of Commissions'),
                                     ('3c.', 'fee_total', '3c. Amount of Fees')):
        value = format(sum(Decimal(_money(getattr(r, attribute))) for r in rows), 'f')
        source = f'Verified sum of full labelled recipient payment columns; corroborated by {corroboration}.\n' + '\n'.join(
            e.source_text for r in rows for e in r.evidence if e.source_text)
        result.fields = [f for f in result.fields if not f.field_name.startswith(prefix)]
        result.fields.append(NormalizedExtractionField(field_name=label, value=value, page=rows[0].source_page,
            confidence=.99, source_text=source, evidence=[SourceEvidence(provider='Corroborated source category sum',
                page=rows[0].source_page, source_text=source)]))
