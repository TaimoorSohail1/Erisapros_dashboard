from app.services.extractor import extract_columnar_broker_compensation_rows


def test_stacked_ocr_column_headers_do_not_become_part_of_a_broker_name():
    source='''INSURANCE FEES AND COMMISSION INFORMATION:
:
NAME AND ADDRESS OF
EACH SOLICITING AGENT
OR BROKER RECEIVING
COMPENSATION :
SALES
ADDITIONAL
COMMISSION
FEES
COMPENSATION
PAID
PAID
PAID
EXAMPLE BENEFITS AGENCY LLC
125.25
.00
.00
42 RESEARCH RD
BOSTON MA 02110
6. COVERAGE/BENEFITS PROVIDED: DBL
'''
    rows=extract_columnar_broker_compensation_rows([(1,source)])
    assert len(rows)==1
    assert rows[0].name=='EXAMPLE BENEFITS AGENCY LLC'
    assert rows[0].commission_total=='125.25'
    assert rows[0].fee_total=='0'
