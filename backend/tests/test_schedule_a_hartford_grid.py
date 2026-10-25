"""Exercise the cell reader at the external renderer/OCR boundaries."""
from pathlib import Path
import subprocess
import pytest


@pytest.fixture
def grid_ocr(monkeypatch):
    width,height=1000,380
    image=bytearray([255])*(width*height)
    xs=[20,260,320,440,560,680,800,980]
    ys=[20,100,300,340]
    for row in range(3):
        for column in range(7):
            color=210+row*10+column
            for y in range(ys[row]+1,ys[row+1]):
                image[y*width+xs[column]+1:y*width+xs[column+1]]=bytes([color])*(xs[column+1]-xs[column]-1)
    for y in ys:image[y*width+xs[0]:y*width+xs[-1]+1]=bytes(xs[-1]-xs[0]+1)
    for x in xs:
        for y in range(ys[0],ys[-1]+1):image[y*width+x]=0
    pgm=f'P5\n{width} {height}\n255\n'.encode()+image
    replies={210:'Producer and Address',211:'Org\nCode',220:'EXAMPLE RESEARCH\nAGENCY 6 LLC\n42 TEST RD\nBOSTON, MA 02110',221:'06'}
    def run(args,**kwargs):
        if Path(args[0]).name=='renderer':
            if '-jpeg' in args:
                for page in [1,2,3]:
                    Path(args[-1]+f'-{page}.jpg').write_bytes(b'P5\n1 1\n255\n'+bytes([200+page]))
                return subprocess.CompletedProcess(args,0,stdout=b'',stderr=b'')
            assert args[args.index('-f')+1]==args[args.index('-l')+1]=='3'
            Path(args[-1]+'.pgm').write_bytes(pgm)
            return subprocess.CompletedProcess(args,0,stdout=b'',stderr=b'')
        content=Path(args[1]).read_bytes()
        color=content.split(b'\n',3)[-1][0]
        mode=args[args.index('--psm')+1]
        return subprocess.CompletedProcess(args,0,stdout=replies.get((color,mode),replies.get(color,'')),stderr='')
    monkeypatch.setattr('app.services.schedule_a_hartford_grid.shutil.which',lambda name:{'pdftoppm':'renderer','tesseract':'ocr'}.get(name))
    monkeypatch.setattr('app.services.schedule_a_hartford_grid.subprocess.run',run)
    return replies


def test_grid_identity_keeps_a_real_digit_in_the_name_separate_from_the_code(grid_ocr):
    from app.services.schedule_a_hartford_grid import read_hartford_grid_identities
    # A page-layout read can miss a single numeric cell; a line read must
    # independently recover it, not borrow the primary value.
    grid_ocr[(221,'4')]=''
    records=read_hartford_grid_identities(b'%PDF-synthetic-grid',[3])
    assert len(records)==1
    record=records[0]
    assert record['name']=='EXAMPLE RESEARCH AGENCY 6 LLC'
    assert record['organization_code']=='06'
    assert record['address_line_1']=='42 TEST RD'
    assert record['city']=='BOSTON' and record['zip_code']=='02110'
    assert record['page']==3 and record['identity_bounds'][0]>0
    assert 'AGENCY 6 LLC' in record['source_text'] and '06' in record['source_text']
    assert record['independent_identity_read']['identity_psm']==4
    assert record['independent_identity_read']['code_psm']==7


@pytest.mark.parametrize('difference',['name','address','code','missing_code'])
def test_disagreeing_secondary_cell_read_cannot_corroborate_itself(grid_ocr,difference):
    from app.services.schedule_a_hartford_grid import read_hartford_grid_identities
    if difference=='name':grid_ocr[(220,'4')]=grid_ocr[220].replace('AGENCY 6 LLC','AGENCY Z LLC')
    if difference=='address':grid_ocr[(220,'4')]=grid_ocr[220].replace('42 TEST RD','43 TEST RD')
    if difference=='code':grid_ocr[(221,'7')]='07'
    if difference=='missing_code':grid_ocr[(221,'7')]=''
    assert read_hartford_grid_identities(b'%PDF-synthetic-grid',[3])==[]


@pytest.mark.parametrize('wrong_source',[None,'file','url','process','multiple','malformed','chunks','pages','page_chunks'])
def test_scanned_grid_recovery_is_bound_to_one_completed_current_document(grid_ocr,wrong_source):
    from app.services.schedule_a_hartford_grid import recover_hartford_scanned_identities
    source_url='https://qa.invalid/current-source'
    raw={'local_ocr_pages':[{'page':3,'text':'The Hartford Annual Statement of Premiums and Producer Compensation'}],
        'ingest':{'ingest':{'processId':'source-process'}},
        'process':{'ingest':{'processId':'source-process','status':'complete',
            'progress':{'complete':{'documents':[{'fileName':'source.pdf','sourceUrl':source_url}]}}}},
        'outputs':[{'fileName':'source.pdf','sourceUrl':source_url,'chunks':[
            {'pageNumbers':[3],'contentType':['table'],'text':'Producer and Address\nCommissions Fees Paid'}]}]}
    if wrong_source=='file':raw['outputs'][0]['fileName']='other.pdf'
    if wrong_source=='url':raw['outputs'][0]['sourceUrl']='https://qa.invalid/other-source'
    if wrong_source=='process':raw['process']['ingest']['processId']='other-process'
    if wrong_source=='multiple':raw['outputs'].append(dict(raw['outputs'][0]))
    if wrong_source=='malformed':raw['process']['ingest']['progress']='unknown'
    if wrong_source=='chunks':raw['outputs'][0]['chunks']=[None]
    if wrong_source=='pages':raw['outputs'][0]['documentPages']='unknown'
    if wrong_source=='page_chunks':raw['outputs'][0]['documentPages']=[{'chunks':[None]}]
    pages=[(3,raw['local_ocr_pages'][0]['text'])]
    records=recover_hartford_scanned_identities(b'%PDF-synthetic-grid','source.pdf',pages,raw,[])
    if wrong_source:
        assert records==[]
    else:
        assert len(records)==1 and records[0]['name']=='EXAMPLE RESEARCH AGENCY 6 LLC'


@pytest.mark.parametrize('wrong_payment_column',[False,True])
def test_physical_identity_recovery_survives_the_public_extraction_pipeline(grid_ocr,monkeypatch,wrong_payment_column):
    import asyncio
    from io import BytesIO
    from types import SimpleNamespace
    import httpx
    from pypdf import PdfWriter
    from app.config import Settings
    from app.services import extractor as module
    cfg=Settings(_env_file=None,groundx_api_key='offline-test',groundx_bucket_id=1,
        mongodb_uri=None,s3_bucket_name=None,ftwlink_key_id=None,groundx_max_wait_seconds=1,
        groundx_poll_seconds=1,schedule_a_clarifications_path=None,
        schedule_a_canonical_validation_enabled=True,schedule_a_canonical_validation_shadow_enabled=False)
    monkeypatch.setattr(module,'get_settings',lambda:cfg)
    grid_ocr.update({201:'The Hartford Annual Statement of Premiums and Producer Compensation',
        202:'''The Hartford Annual Statement of Premiums and Producer Compensation
Name of Insurance Carrier EIN NAIC Code Policy Number
HARTFORD LIFE AND ACCIDENT 23-4567890 54321 000123G
Plan/Policy Year - 01/01/2024 to 12/31/2024
Premium was applied as follows during the Plan/Policy Year -
000123G Life $100.00 284
Total $100.00
''',203:'Producer and Address unreadable'})
    name='synthetic-hartford.pdf'
    url='https://qa.invalid/current-document'
    document={'documentId':'source-doc','fileName':name,'sourceUrl':url,'extracted':True}
    process={'ingest':{'processId':'source-process','status':'complete','progress':{'complete':{'documents':[document]}}}}
    table=('Producer and Address EXAMPLE RESEARCH AGENCY 06 6 LLC 42 TEST RD BOSTON , MA 02110 '
        'Org Code Policy Number 000123-GL Total Commissions Paid $ 0.00 $ 0.00 '
        'Fees Paid $ 0.00 $ 0.00 (1) Bonus Paid $ 25.15 $ 25.15 '
        '(2) Additional Compensation Paid $ 0.00 $ 0.00')
    payload={'broker_rows':[{'name':'EXAMPLE RESEARCH AGENCY 06 6 LLC','address_line_1':'42 TEST RD',
        'city':'BOSTON','state':'MA','zip_code':'02110','organization_code':'06',
        'commission_total':'0.00','fee_total':'0.00'}]}
    if wrong_payment_column:
        # A reconciled source bonus must not become a second copy of the
        # provider's incorrectly classified commission for this same recipient.
        payload['broker_rows'][0]['commission_total']='25.15'
    class Provider:
        def __init__(self,**kwargs):pass
        def ingest(self,**kwargs):return SimpleNamespace(model_dump=lambda **kwargs:process)
    def respond(request):
        path=request.url.path
        if '/ingest/documents/' in path:return httpx.Response(200,json={'documents':[document]})
        if '/ingest/document/extract/' in path:return httpx.Response(200,json=payload)
        if '/ingest/document/xray/' in path:return httpx.Response(200,json={
            'fileName':name,'sourceUrl':url,'documentPages':[],
            'chunks':[{'pageNumbers':[3],'contentType':['table'],'text':table}]})
        if '/ingest/' in path:return httpx.Response(200,json=process)
        raise AssertionError(path)
    original=httpx.AsyncClient
    def client(*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(respond)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'GroundX',Provider)
    monkeypatch.setattr(module.httpx,'AsyncClient',client)
    writer=PdfWriter()
    for _ in range(3):writer.add_blank_page(width=612,height=792)
    stream=BytesIO();writer.write(stream)
    result=asyncio.run(module.ExtractionService().extract_schedule_a(stream.getvalue(),name))
    assert len(result.schedule_a_broker_rows)==1
    row=result.schedule_a_broker_rows[0]
    assert row.name=='EXAMPLE RESEARCH AGENCY 6 LLC'
    assert row.organization_code=='06' and row.fee_total=='25.15' and row.source_page==3
    assert any('grid' in e.provider.casefold() and e.bounding_box for e in row.evidence)
    assert next(f for f in result.fields if f.field_name.startswith('3c.')).value=='25.15'
