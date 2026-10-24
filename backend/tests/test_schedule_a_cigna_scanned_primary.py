"""The consolidated record must survive scanned headings and state appendices."""
import pytest

from app.models import NormalizedExtractionResult
from app.services.extractor import (
    extract_cigna_schedule_a_broker_rows,
    extract_cigna_schedule_a_fields,
    prefer_authoritative_cigna_summary_fields,
)
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def packet(count='137', contract='0098765', commission='125.25', *, readable_ids=False, roman='lil'):
    ids = '23-4567890 54321 ' if readable_ids else '; '
    return f'''Cigna Health and Life Insurance Company
Schedule A Insurance Information
Summary of All Insurance Contracts Included in Part III
Name of Insurance Carrier:Cigna Health and Life Insurance Company and affiliates ("Cigna")
(b) EIN (c) NAIC Code (d) Contract or (e) Approx.no.of persons covered Policy/Contract Year 7
unreadable identification Number at end of policy or contract year (f) From (g) To
{ids}{contract} {count} Employees 01/01/2024 — 12/31/2024
2. Insurance fees and commissions information
Total Amount of commissions paid ${commission} | Total Amount of fees paid $0
3. Persons receiving commissions and fees
Non Experience - Rated
EXAMPLE BENEFITS AGENCY LLC,
PO BOX 123,BOSTON. MA, ${commission}
021100000
$0
6-Other Organization
Part ll Investment and Annuity Contract Information
Part {roman} Welfare Benefit Contract Information
Total premiums or subscription charges paid to carrier $1350.75
'''


@pytest.mark.parametrize('count,contract,commission', [('137','0098765','125.25'),('284','0012380','900.75')])
def test_scan_recovers_partial_summary_row_without_taking_a_section_digit(count,contract,commission):
    fields={f.field_name.split('.')[0]:f for f in extract_cigna_schedule_a_fields([(2,packet(count,contract,commission))])}
    assert fields['1e'].value==count
    assert fields['1d'].value==contract
    assert fields['1f'].value=='01/01/2024'
    assert fields['1g'].value=='12/31/2024'
    assert fields['3b'].value==commission
    assert '1b' not in fields and '1c' not in fields


def test_primary_xray_cells_can_fill_unreadable_ids_only_for_the_same_complete_record():
    local=[(2,packet())]
    provider=[(2,packet(readable_ids=True,roman='III'))]
    fields=prefer_authoritative_cigna_summary_fields([],local,corroborating_pages=provider)
    by_code={f.field_name.split('.')[0]:f for f in fields}
    assert by_code['1b'].value=='23-4567890'
    assert by_code['1c'].value=='54321'
    assert by_code['1e'].value=='137'
    assert by_code['1b'].page==2 and '23-4567890' in by_code['1b'].source_text


def test_vertical_labelled_identifiers_are_retained_with_a_partial_coverage_row():
    source=packet().replace('unreadable identification Number',
        '(b) EIN 23-4567890\n(c) NAIC Code 54321\nIdentification Number')
    fields={f.field_name.split('.')[0]:f for f in extract_cigna_schedule_a_fields([(2,source)])}
    assert fields['1b'].value=='23-4567890' and fields['1c'].value=='54321'
    assert fields['1d'].value=='0098765' and fields['1e'].value=='137'


def test_a_different_contract_xray_summary_cannot_fill_the_missing_identifiers():
    fields=prefer_authoritative_cigna_summary_fields([],[(2,packet())],
        corroborating_pages=[(2,packet(contract='0000555',readable_ids=True,roman='III'))])
    assert not any(f.field_name.startswith(('1b.','1c.')) for f in fields)
    assert next(f for f in fields if f.field_name.startswith('1d.')).value=='0098765'


def test_genuine_primary_count_disagreement_stays_in_review_not_silently_repaired():
    fields=prefer_authoritative_cigna_summary_fields([],[(2,packet())],
        corroborating_pages=[(2,packet(count='138',readable_ids=True,roman='III'))])
    result=resolve_schedule_a_result(NormalizedExtractionResult(provider='recorded boundary',fields=fields))
    field=next(f for f in result.fields if f.field_name.startswith('1e.'))
    assert field.decision=='REVIEW_REQUIRED'
    assert {'137','138'}<=set(field.candidate_values)


def test_single_recipient_fallback_keeps_identity_money_and_page_bound_evidence():
    rows=extract_cigna_schedule_a_broker_rows([(2,packet())])
    assert len(rows)==1
    row=rows[0]
    assert row.name=='EXAMPLE BENEFITS AGENCY LLC'
    assert row.address_line_1=='PO BOX 123' and row.city=='BOSTON'
    assert row.state=='MA' and row.zip_code=='02110-0000'
    assert row.commission_total=='125.25' and row.fee_total=='0'
    assert row.organization_code=='6'
    assert not row.fee_rows and not row.purpose
    assert row.evidence and row.evidence[0].page==2
    assert '125.25' in row.evidence[0].source_text


def test_appendix_layout_is_not_promoted_to_a_primary_summary():
    appendix=packet(readable_ids=True,roman='III')+'\nSchedule A Insurance Information - Appendix to 1a, b and c'
    assert extract_cigna_schedule_a_fields([(4,appendix)])==[]


def test_two_different_primary_pages_cannot_supply_missing_identifiers():
    fields=prefer_authoritative_cigna_summary_fields([],[(2,packet())],corroborating_pages=[
        (2,packet(readable_ids=True,roman='III')),
        (5,packet(contract='0000555',readable_ids=True,roman='III')),
    ])
    assert not any(f.field_name.startswith(('1b.','1c.')) for f in fields)


def test_single_name_at_two_addresses_cannot_receive_the_aggregate_as_one_recipient():
    source=packet().replace('Part ll Investment', '''EXAMPLE BENEFITS AGENCY LLC,
PO BOX 987,BOSTON. MA, $12.25
021100001
$0
6-Other Organization
Part ll Investment''')
    assert extract_cigna_schedule_a_broker_rows([(2,source)])==[]


def test_aggregate_fee_cannot_replace_a_different_printed_recipient_fee():
    source=packet().replace('Total Amount of fees paid $0','Total Amount of fees paid $15.50')
    assert extract_cigna_schedule_a_broker_rows([(2,source)])==[]


def test_unreadable_positive_fee_purpose_is_reviewed_not_given_an_invented_label():
    source=packet().replace('$0','$15.50')
    rows=extract_cigna_schedule_a_broker_rows([(2,source)])
    assert len(rows)==1 and rows[0].fee_total=='15.50'
    assert not rows[0].purpose and not rows[0].fee_rows[0].purpose
    result=resolve_schedule_a_result(NormalizedExtractionResult(provider='recorded boundary',fields=[],schedule_a_broker_rows=rows))
    assert result.schedule_a_broker_rows[0].decision=='REVIEW_REQUIRED'


@pytest.mark.parametrize('wrong_document', [None,'source_url','file_name','process_id','multiple_outputs','malformed_metadata'])
def test_scanned_primary_recovery_survives_the_public_pipeline_without_approving_a_new_source(monkeypatch,wrong_document):
    import asyncio
    from io import BytesIO
    import socket
    from pypdf import PdfWriter
    from app.config import Settings
    from app.models import NormalizedExtractionField
    from app.services import extractor as module
    cfg=Settings(_env_file=None,groundx_api_key=None,groundx_bucket_id=0,mongodb_uri=None,s3_bucket_name=None,
        ftwlink_key_id=None,schedule_a_canonical_validation_enabled=True,
        schedule_a_canonical_validation_shadow_enabled=False,
        schedule_a_clarifications_path=None)
    monkeypatch.setattr(module,'get_settings',lambda:cfg)
    local=packet()
    file_name='different-scanned-summary.pdf'
    source_url='https://qa.invalid/primary-document'
    raw={'local_ocr_pages':[{'page':2,'text':local}],
         'ingest':{'ingest':{'processId':'synthetic-process'}},
         'process':{'ingest':{'processId':'synthetic-process','status':'complete','progress':{'complete':{'documents':[
             {'fileName':file_name,'sourceUrl':source_url}
         ]}}}},
         'outputs':[{'fileName':file_name,'sourceUrl':source_url,'documentPages':[{'pageNumber':2,'chunks':[
             {'pageNumbers':[2],'contentType':['table'],'text':packet(readable_ids=True,roman='III')}
         ]}]}]}
    if wrong_document=='source_url':raw['outputs'][0]['sourceUrl']='https://qa.invalid/another-document'
    if wrong_document=='file_name':raw['outputs'][0]['fileName']='another-document.pdf'
    if wrong_document=='process_id':raw['process']['ingest']['processId']='another-process'
    if wrong_document=='multiple_outputs':raw['outputs'].append(dict(raw['outputs'][0]))
    if wrong_document=='malformed_metadata':raw['process']['ingest']['progress']='unparsed'
    async def recorded_boundary(self,*args):
        return NormalizedExtractionResult(provider='Recorded provider/local boundary',raw=raw,
            fields=[NormalizedExtractionField(field_name=name,value=value,page=4,confidence=.99,source_text='Appendix EIN 98-7654321 NAIC 12345')
                    for name,value in [('1b. Insurance Carrier EIN','98-7654321'),('1c. NAIC Code','12345'),
                                       ('1e. Persons Covered (End of Policy Year)','7')]])
    monkeypatch.setattr(module.ExtractionService,'_extract_schedule_a_unresolved',recorded_boundary)
    writer=PdfWriter()
    for _ in range(4):writer.add_blank_page(width=612,height=792)
    stream=BytesIO();writer.write(stream)
    async def run_offline():
        # Windows creates its event-loop self-pipe with a loopback socket.
        # Apply the no-network boundary after that local loop initialization.
        with monkeypatch.context() as boundary:
            boundary.setattr(socket.socket,'connect',lambda *args:(_ for _ in ()).throw(AssertionError('Offline only')))
            return await module.ExtractionService().extract_schedule_a(stream.getvalue(),file_name)
    result=asyncio.run(run_offline())
    fields={f.field_name.split('.')[0]:f for f in result.fields}
    if wrong_document:
        assert fields['1b'].decision=='REVIEW_REQUIRED'
        assert fields['1c'].decision=='REVIEW_REQUIRED'
        assert fields['1b'].value=='98-7654321' and fields['1c'].value=='12345'
        return
    assert fields['1b'].value=='23-4567890' and fields['1c'].value=='54321'
    assert fields['1e'].value=='137' and fields['1d'].value=='0098765'
    assert fields['1b'].page==2 and fields['1c'].page==2
    assert len(result.schedule_a_broker_rows)==1
    assert result.schedule_a_broker_rows[0].organization_code=='6'
    assert not result.raw.get('confirmed_clarifications')
    assert fields['1e'].decision=='REVIEW_REQUIRED', 'A new PDF must not inherit the original employee-population approval.'
