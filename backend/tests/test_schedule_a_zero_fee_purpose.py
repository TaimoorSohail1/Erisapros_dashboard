"""A commission category is not a fee-purpose value when no fees were paid."""
import pytest
from app.models import ScheduleABrokerMoneyRow, ScheduleABrokerRow
from app.services.xml_builder import schedule_a_broker_update_values


@pytest.mark.parametrize('purpose',[None,'COMMISSIONS','N/A'])
def test_zero_fees_do_not_generate_a_fee_purpose_from_commissions(purpose):
    row=ScheduleABrokerRow(name='WEST ADVISORY LLC',address_line_1='12 TEST ROAD',city='DENVER',
        state='CO',zip_code='80202',organization_code='6',commission_total='125.25',fee_total='0.00',
        purpose=purpose,commission_rows=[ScheduleABrokerMoneyRow(amount='125.25',purpose='BASE COMMISSIONS')],
        decision='AUTOMATIC')
    values=schedule_a_broker_update_values([row],require_complete=True)
    assert values.get('FeesPdText1','')==''
    assert values['CommPdAmt1']=='125.25'
    assert values['FeesPdAmt1']=='0'


def test_nonzero_fee_keeps_its_printed_purpose():
    row=ScheduleABrokerRow(name='WEST ADVISORY LLC',organization_code='6',
        commission_total='125.25',fee_total='25.50',purpose='Annual service payment',decision='AUTOMATIC')
    values=schedule_a_broker_update_values([row],require_complete=True)
    assert values['FeesPdText1']=='ANNUAL SERVICE PAYMENT'
