"""The normal pipeline must use complete source tables, not poorer OCR."""
import asyncio
from types import SimpleNamespace
import httpx

from app.config import get_settings
from app.services.extractor import ExtractionService


def test_provider_table_recovers_bonus_and_all_recipient_addresses(monkeypatch):
    import app.services.extractor as module
    for key, value in {'GROUNDX_API_KEY':'offline-test', 'GROUNDX_BUCKET_ID':'1',
                       'GROUNDX_MAX_WAIT_SECONDS':'1', 'GROUNDX_POLL_SECONDS':'1',
                       'SCHEDULE_A_CANONICAL_VALIDATION_ENABLED':'true',
                       'SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED':'false'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    source = '''The Hartford Annual Statement of Premiums and Producer Compensation
Name of Insurance Carrier EIN NAIC Code Policy Number
HARTFORD LIFE AND ACCIDENT 06-0838648 70815 0042000G
Plan/Policy Year - 01/01/2024 to 12/31/2024
Premium was applied as follows during the Plan/Policy Year -
0042000G LIFE-BTRM $ 1250.50 17
Total $ 1250.50
Producer and Address unreadable rows
'''
    def table(street, commission, bonus):
        return (f'Producer and Address DIFFERENT AGENCY LLC {street} BOSTON , MA 02110 '
                f'Org Code 3 Policy Number 0042000-GL Total '
                f'Commissions Paid $ {commission} $ {commission} Fees Paid $ 0.00 $ 0.00 '
                f'(1) Bonus Paid $ {bonus} $ {bonus} (2) Additional Compensation Paid $ 0.00 $ 0.00')
    tables = table('42 RESEARCH RD', '125.25', '0.00') + '\n' + table('PO BOX 216', '0.00', '36.75')
    payload = {'broker_rows': [dict(name='DIFFERENT AGENCY LLC', address_line_1=street,
                 city='BOSTON', state='MA', zip_code='02110', commission_total=commission, fee_total='0.00')
                 for street, commission in [('42 RESEARCH RD','125.25'), ('PO BOX 216','0.00')]]}
    process = {'ingest': {'processId': 'test', 'status': 'complete', 'progress': {'complete': {
        'documents': [{'documentId':'test-doc','fileName':'different.txt','extracted':True}]}}}}
    class Provider:
        def __init__(self, **kwargs): pass
        def ingest(self, **kwargs): return SimpleNamespace(model_dump=lambda **kw: process)
    def respond(request):
        path = request.url.path
        if '/ingest/documents/' in path:
            return httpx.Response(200,json={'documents':process['ingest']['progress']['complete']['documents']})
        if '/ingest/document/extract/' in path: return httpx.Response(200,json=payload)
        if '/ingest/document/xray/' in path:
            return httpx.Response(200,json={'chunks':[{'pageNumbers':[1], 'text':tables, 'contentType':['table']}]})
        if '/ingest/' in path: return httpx.Response(200,json=process)
        raise AssertionError(path)
    original = httpx.AsyncClient
    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(respond)
        return original(*args, **kwargs)
    monkeypatch.setattr(module,'GroundX',Provider)
    monkeypatch.setattr(module.httpx,'AsyncClient',client)
    try:
        result = asyncio.run(ExtractionService().extract_schedule_a(source.encode(),'different.txt'))
        rows = result.schedule_a_broker_rows
        assert len(rows) == 2
        assert {r.address_line_1 for r in rows} == {'42 RESEARCH RD','PO BOX 216'}
        assert next(r for r in rows if r.address_line_1=='PO BOX 216').fee_total == '36.75'
        assert next(f for f in result.fields if f.field_name.startswith('3c.')).value == '36.75'
    finally:
        get_settings.cache_clear()


def test_recovery_does_not_overwrite_a_conflicting_independently_read_table():
    from app.services.schedule_a_hartford_columns import recover_hartford_compensation_pages
    header = 'Annual Statement of Premiums and Producer Compensation\n'
    def table(street, amount):
        return (f'Producer and Address EXAMPLE AGENCY {street} BOSTON , MA 02110 Org Code 3 '
                f'Policy Number 0042000-GL Total Commissions Paid $ {amount} $ {amount} '
                'Fees Paid $ 0.00 $ 0.00 (1) Bonus Paid $ 0.00 $ 0.00 (2) Additional Compensation Paid $ 0.00 $ 0.00')
    pages = [(3,header + table('42 RESEARCH RD','25.00'))]
    raw = {'outputs':[{'chunks':[{'pageNumbers':[3], 'contentType':['table'],
                    'text':table('42 RESEARCH RD','99.00')+'\n'+table('PO BOX 216','5.00')}]}]}
    assert recover_hartford_compensation_pages(pages,raw) == pages


def test_partial_provider_chunks_do_not_fill_missing_digits_or_cents():
    from app.services.schedule_a_hartford_columns import recover_hartford_compensation_pages
    pages = [(3,'Annual Statement of Premiums and Producer Compensation\nProducer and Address unreadable')]
    raw = {'outputs':[{'chunks':[{'pageNumbers':[3], 'contentType':['table'],
             'text':'Producer and Address EXAMPLE AGENCY PO BOX 216 BOSTON , MA 02110 Org Code 3 '
                    'Policy Number 0042000-GL Total Commissions Paid $ 25.00 $ 99.00'}]}]}
    assert recover_hartford_compensation_pages(pages,raw) == pages
