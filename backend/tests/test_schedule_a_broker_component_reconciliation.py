"""A provider aggregate must not duplicate the same source component row."""
import pytest
from app.models import ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence
from app.services.extractor import merge_schedule_a_broker_rows


def recipients(provider_commission='125.25'):
    identity=dict(name='WEST ADVISORY LLC',address_line_1='12 TEST ROAD',city='DENVER',
                  state='CO',zip_code='80202',organization_code='6')
    source=ScheduleABrokerRow(**identity,commission_total='125.25',fee_total='0.00',confidence=.99,
        commission_rows=[ScheduleABrokerMoneyRow(coverage='0042000-A',amount='-0.75',purpose='COMMISSIONS'),
                         ScheduleABrokerMoneyRow(coverage='0042000-B',amount='126.00',purpose='COMMISSIONS')],
        evidence=[SourceEvidence(page=2,source_text='Printed component table',table_cell=(1,1))])
    provider=ScheduleABrokerRow(**identity,commission_total=provider_commission,fee_total='0',confidence=.97,
        commission_rows=[ScheduleABrokerMoneyRow(amount=provider_commission,purpose='COMMISSIONS')])
    return source,provider


@pytest.mark.parametrize('source_first',[True,False])
def test_component_table_and_identical_provider_aggregate_are_one_recipient_in_either_order(source_first):
    source,provider=recipients()
    rows=merge_schedule_a_broker_rows([source],[provider]) if source_first else merge_schedule_a_broker_rows([provider],[source])
    assert len(rows)==1
    assert [(c.coverage,c.amount) for c in rows[0].commission_rows]==[('0042000-A','-0.75'),('0042000-B','126.00')]
    assert rows[0].commission_total=='125.25'
    assert source.commission_rows[0].amount=='-0.75' and provider.commission_rows[0].coverage is None


@pytest.mark.parametrize('source_first',[True,False])
@pytest.mark.parametrize('difference',['one_cent','fractional_cent','legal_name','contract'])
def test_component_copy_reconciliation_does_not_hide_a_real_difference(source_first,difference):
    source,provider=recipients('125.24' if difference=='one_cent' else '125.2501' if difference=='fractional_cent' else '125.25')
    if difference=='legal_name':provider.name='WEST ADVISORY INC'
    if difference=='contract':provider.commission_rows[0].coverage='0042000-C'
    rows=merge_schedule_a_broker_rows([source],[provider]) if source_first else merge_schedule_a_broker_rows([provider],[source])
    assert len(rows)==2
