"""Outbound purpose follows payment categories; printed purpose remains source evidence."""
import pytest
from app.models import ScheduleABrokerMoneyRow, ScheduleABrokerRow
from app.services.xml_builder import schedule_a_broker_update_values


@pytest.mark.parametrize('purpose',[None,'COMMISSIONS','N/A'])
def test_nonzero_commission_and_zero_fees_send_commissions_without_changing_source(purpose):
    row=ScheduleABrokerRow(name='WEST ADVISORY LLC',address_line_1='12 TEST ROAD',city='DENVER',
        state='CO',zip_code='80202',organization_code='6',commission_total='125.25',fee_total='0.00',
        purpose=purpose,commission_rows=[ScheduleABrokerMoneyRow(amount='125.25',purpose='BASE COMMISSIONS')],
        decision='AUTOMATIC')
    values=schedule_a_broker_update_values([row],require_complete=True)
    assert values['FeesPdText1']=='COMMISSIONS'
    assert row.purpose == purpose
    assert values['CommPdAmt1']=='125.25'
    assert values['FeesPdAmt1']=='0'
    assert row.fee_total == '0.00'


def test_nonzero_commission_and_fee_send_both_categories_and_keep_printed_evidence():
    row=ScheduleABrokerRow(name='WEST ADVISORY LLC',organization_code='6',
        commission_total='125.25',fee_total='25.50',purpose='Annual service payment',decision='AUTOMATIC')
    values=schedule_a_broker_update_values([row],require_complete=True)
    assert values['FeesPdText1']=='COMMISSIONS AND FEES'
    assert row.purpose == 'Annual service payment'
