from app.services.extractor import extract_unitedhealthcare_broker_rows, extract_position_aware_schedule_a_broker_rows


def test_uhc_recipient_labels_preserve_explicit_code_and_separate_payment_columns():
    source='''Schedule A (Form 5500) Parts I and III
UnitedHealthcare Insurance Company
Insurance fees and commissions paid to agents, brokers, and other persons
(a) Name and address of the agents or brokers to whom commissions or fees were paid:
EXAMPLE BENEFITS AGENCY LLC
42 RESEARCH RD
BOSTON MA 02110
(b) Amount of commissions paid: $125.25
(c) Fees paid / Amount: $15.50
(d) Fees paid / Purpose: Consulting
(e) Organizational Code: 6
'''
    rows=extract_unitedhealthcare_broker_rows([(2,source)])
    assert len(rows)==1
    assert rows[0].organization_code=='6'
    assert rows[0].commission_total=='125.25'
    assert rows[0].fee_total=='15.50'
    assert extract_position_aware_schedule_a_broker_rows([(2,source)])==[], 'A labelled fee column is not a second commission column.'
