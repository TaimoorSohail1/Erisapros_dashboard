"""Closed labelled Schedule A workbook grids, checked with two source readers.

Openpyxl values must agree with the OOXML cells, including every sheet. Only a
single explicitly totalled recipient grid can establish complete-list proof.
"""
from io import BytesIO
from pathlib import PurePosixPath
from decimal import Decimal
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from openpyxl import load_workbook
from app.services.schedule_a_broker_roster import _fingerprint, _money, attest_source_broker_roster, source_roster_review
from app.services.schedule_a_closed_source import ClosedSourceProof
from app.services.schedule_a_native_brokers import _row

NS = {'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def closed_workbook_roster(content):
    with zipfile.ZipFile(BytesIO(content)) as z:
        if sum(i.file_size for i in z.infolist()) > 64*1024*1024:
            return None
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(s.itertext()) for s in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si',NS)]
        rel={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('x:sheets/x:sheet',NS)
        xml_sheets=[]
        for sheet in sheets:
            target=rel[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            path=target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl')/target)
            cells={}
            for c in ET.fromstring(z.read(path)).findall('.//x:sheetData/x:row/x:c',NS):
                value=c.findtext('x:v',namespaces=NS)
                if c.find('x:f',NS) is not None and value is None:return None
                kind=c.attrib.get('t')
                if kind=='e':return None
                if kind=='s':value=shared[int(value)]
                elif kind=='inlineStr':value=''.join(c.find('x:is',NS).itertext())
                if value is not None:cells[c.attrib['r']]=value
            xml_sheets.append((sheet.attrib['name'],cells))
    book=load_workbook(BytesIO(content),read_only=True,data_only=True)
    try:
        if book.sheetnames != [s[0] for s in xml_sheets]:return None
        pages=[];grids=[]
        for page,(sheet,(name,xml_cells)) in enumerate(zip(book,xml_sheets),1):
            values={c.coordinate:c.value for row in sheet for c in row if c.value is not None}
            if set(values)!=set(xml_cells):return None
            for cell,value in values.items():
                other=xml_cells[cell]
                if isinstance(value,bool):return None
                if isinstance(value,(int,float,Decimal)):
                    if Decimal(str(value))!=Decimal(other):return None
                elif str(value)!=other:return None
            text='\n'.join(f'{cell}: {value}' for cell,value in values.items())
            pages.append((page,text))
            labels={}
            for row in sheet:
                if len(row)<3:continue
                label=re.sub(r'[^a-z0-9]','',str(row[0].value or '').lower())
                if label in {'1d','2a','2b','3a','3b','3c','3d','3e'}:
                    if label in labels:return None
                    labels[label]=row[2].value
            if '3a' in labels:
                if not all(k in labels for k in ['1d','2a','2b','3a','3b','3c']):return None
                if _money(str(labels['2a']))!=_money(str(labels['3b'])) or _money(str(labels['2b']))!=_money(str(labels['3c'])):return None
                amounts=[_money(str(labels[k])) for k in ['3b','3c']]
                if any(v is None or Decimal(v)<0 for v in amounts):return None
                recipient=str(labels['3a'] or '').splitlines()
                if not recipient:return None
                row=_row(recipient[0],recipient[1:],*amounts,page,text,provider='OOXML and openpyxl cell agreement')
                if row is None:return None
                code=str(labels.get('3e') or '3').strip()
                if code not in {'1','2','3','4','5','6','7','8','9'}:return None
                row.organization_code=code
                row.purpose='COMMISSIONS AND FEES' if Decimal(amounts[1]) else 'COMMISSIONS'
                grids.append((str(labels['1d']),row))
        if len(grids)!=1:return None
        coverage={'source_sha256':hashlib.sha256(content).hexdigest(),'page_count':len(pages),
                  'pages':[{'page':p,'readable':True,'text_sha256':hashlib.sha256(t.encode()).hexdigest()} for p,t in pages]}
        rows=[grids[0][1]]
        proof=ClosedSourceProof(coverage['source_sha256'],_fingerprint(rows),hashlib.sha256(json.dumps(coverage,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        return grids[0][0],rows,proof,coverage
    finally:book.close()


async def verify_stored_workbook_source(repo,filing,fields,document):
    from app.services.storage import StorageService
    from app.models import NormalizedExtractionResult,ExtractedFieldStatus,AuditLog
    from app.services.schedule_a_broker_source_repair import set_corroborated_totals
    import asyncio
    roster=filing.schedule_a_broker_roster
    if getattr(roster,'reviewer_confirmation',None) or any(v.validator.startswith('reviewer_') and v.status=='PASS' for r in filing.schedule_a_broker_rows for v in r.validation_results):return filing,fields
    content=await asyncio.to_thread(StorageService().load_pdf,document['s3_key'],document.get('s3_bucket') or filing.s3_bucket,document.get('storage_path'))
    if hashlib.sha256(content).hexdigest()!=document.get('source_sha256'):return filing,fields
    answer=await asyncio.to_thread(closed_workbook_roster,content)
    if not answer:return filing,fields
    contract,rows,proof,coverage=answer
    contracts={re.sub(r'[^a-z0-9]','',f.value.lower()) for f in fields if f.mapped_rule_key=='schedule_a_part_i_1d_contract_policy_number' and f.value}
    if contracts!={re.sub(r'[^a-z0-9]','',contract.lower())}:return filing,fields
    result=NormalizedExtractionResult(provider='Stored OOXML source verification',fields=[],schedule_a_broker_rows=rows,raw={'independent_source_page_coverage':coverage})
    set_corroborated_totals(result,rows,corroboration='complete OOXML cell read and independent workbook reader; printed totals agree')
    attest_source_broker_roster(result,rows,proof.source_sha256,native_proof=proof)
    result.schedule_a_broker_roster=source_roster_review(rows,roster)
    if result.schedule_a_broker_roster.mode!='REPLACE':return filing,fields
    updates=[]
    for f in fields:
        native=next((v for v in result.fields if v.field_name==(f.mapped_label or f.source_field_name)),None)
        if native and f.status not in {ExtractedFieldStatus.EDITED,ExtractedFieldStatus.IGNORED} and not f.keep_current_context:
            after=f.model_copy(update={'value':native.value,'proposed_value':native.value,'page':native.page,'source_text':native.source_text,'confidence':native.confidence,'status':ExtractedFieldStatus.MATCHED,'status_reason':'Recovered from complete, independently checked workbook cells.','validation_results':[]},deep=True)
            if f.model_dump(exclude={'updated_at'})!=after.model_dump(exclude={'updated_at'}):updates.append((f,after))
    saved=await repo.save_source_verification(filing,updates,{'schedule_a_broker_rows':rows,'schedule_a_broker_roster':result.schedule_a_broker_roster})
    if not saved:return (await repo.get_filing(filing.id)) or filing,await repo.list_fields(filing.id)
    await repo.add_audit(AuditLog(filing_id=filing.id,event='stored_schedule_a_workbook_verified',message='Complete workbook broker grid and both printed payment totals verified without reupload.',details={'source_sha256':proof.source_sha256}))
    by_id={old.id:new for old,new in updates}
    return saved,[by_id.get(f.id,f) for f in fields]
