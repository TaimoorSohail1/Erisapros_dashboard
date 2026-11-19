"""Source totals for actionable review; never an automatic approval."""
from decimal import Decimal
from app.models import BrokerPaymentReconciliation
from app.services.schedule_a_native_brokers import printed_payment_totals


def payment_reconciliation(rows, fields):
    required=any(v.validator=='broker_total_reconciliation' and v.status=='ERROR'
                 for row in rows for v in row.validation_results)
    try:
        totals=printed_payment_totals(rows)
    except ValueError:
        return BrokerPaymentReconciliation(required=True,source='CONFLICTING_SOURCE')
    source='PRINTED_SOURCE'
    if totals is None:
        from app.services.schedule_a_extraction_pipeline import _source_text_supports_value,parse_decimal
        by_key={f.mapped_rule_key:f for f in fields}
        totals=tuple(parse_decimal(f.value) if f and f.page and _source_text_supports_value(f.value,f.source_text or '') else None
            for f in (by_key.get(k) for k in ('schedule_a_part_i_3b_amount_of_commissions','schedule_a_part_i_3c_amount_of_fees')))
        source='EXTRACTED_SOURCE'
    if any(v is None for v in totals):
        return BrokerPaymentReconciliation(required=required,source='UNAVAILABLE')
    return BrokerPaymentReconciliation(required=required,source=source,
        expected_commission=format(Decimal(totals[0]),'.2f'),expected_fees=format(Decimal(totals[1]),'.2f'))
