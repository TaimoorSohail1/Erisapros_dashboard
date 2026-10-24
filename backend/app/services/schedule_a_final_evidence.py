"""Attach literal, field-bound evidence after authoritative carrier adapters.

Values and candidate disagreements are never changed here. A whole page with
matching digits is not proof: a relevant label must bind the exact value.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from app.models import SourceEvidence
from app.services.schedule_a_customer_rules import DEFAULT_CODE_REASON

_LABELS = {
    '1a': r'Name\s+of\s+(?:Insurance\s+)?(?:Carrier|Company)|Insurance\s+Carrier\s*:|Carrier\s*:',
    '1b': r'\bF?EIN\b|\bTAX\s*ID\b',
    '1c': r'\bNAIC(?:\s+Code)?\b',
    '1d': r'(?:Contract|Policy)(?:\s+or\s+Identification)?\s*(?:Number|No\.?|#)|Group\s*ID',
    '1e': r'Persons?\s+(?:Covered|Insured)|Number\s+of\s+Persons?|Lives\s+Covered',
}

_IDENTITY_ROW=re.compile(r'(?P<ein>\d{2}-\d{7})\s+(?P<naic>\d{5})\s+'
    r'(?P<contract>[A-Za-z0-9-]+)\s+(?P<count>\d[\d,]*)\s+'
    r'(?P<begin>\d{1,2}/\d{1,2}/\d{4})\s+(?P<end>\d{1,2}/\d{1,2}/\d{4})')
_COLUMN={'1b':'ein','1c':'naic','1d':'contract','1e':'count','1f':'begin','1g':'end'}
_CARRIER_CARD=re.compile(r'(?m)^(?P<carrier>[A-Za-z][A-Za-z &.-]+?)[ \t]+'
    r'(?P<ein>\d{2}-\d{7})[ \t]+(?P<naic>\d{5})[ \t]+(?P<contract>[A-Za-z0-9-]+)[ \t]*$')
_DATE=r'\d{1,2}[/-]\d{1,2}[/-]\d{4}'
_PERIOD=re.compile(r'(?:Policy\s+or\s+Contract\s+Year|Plan/Policy\s+Year|DATE\s+FOR\s+PERIOD\s*:\s*FROM)'
    r'\s*[: -]*('+_DATE+r')\s*(?:TO|[-\u2013])\s*('+_DATE+r')',re.I)
_PREMIUM=re.compile(r'(?:Total\s+Premiums?(?:\s+or\s+Subscriptions?\s+Charges)?\s+Paid\s+to\s+Carrier|'
    r'Total\s+Payments\s+Made\s+to\s+Carrier)[\s.:]*\$?\s*(?P<amount>\d[\d,]*(?:\.\d{1,2})?)',re.I)


def _attach(field,page,source,provider='Final labelled source evidence'):
    evidence=SourceEvidence(provider=provider,page=page,source_text=source)
    if evidence not in field.evidence:
        field.evidence.append(evidence)
    field.page=page
    field.source_text=source


def _normal(value):
    return re.sub(r'[^a-z0-9]','',str(value or '').casefold())


def _money(value):
    try:
        number=Decimal(re.sub(r'[$,\s]','',str(value)))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _recipient_evidence(result):
    rows=result.schedule_a_broker_rows
    for field in result.fields:
        code=field.field_name.split('.',1)[0]
        if code=='3a':
            for row in rows:
                if _normal(row.name)!=_normal(field.value):
                    continue
                proof=next((e for e in row.evidence if e.page and _normal(row.name) in _normal(e.source_text)),None)
                if proof:
                    _attach(field,proof.page,proof.source_text,'Printed recipient identity')
        if code=='3e' and rows and _normal(field.value)=='3' and all(row.organization_code_defaulted for row in rows):
            evidence=SourceEvidence(provider='Customer configured default',source_text=DEFAULT_CODE_REASON)
            if evidence not in field.evidence:
                field.evidence.append(evidence)
        if code=='3e' and rows and all(str(row.organization_code)==str(field.value) for row in rows):
            for row in rows:
                for evidence in row.evidence:
                    source=evidence.source_text or ''
                    labelled=re.search(r'Org(?:anization)?\s*Code\s*'+re.escape(field.value)+r'\b',source,re.I)
                    table=(re.search(r'Commissions\s+Paid\s+Fees\s+Paid\s+Organization',source,re.I)
                        and re.search(r'Coverage[^\n]+Purpose[^\n]+code',source,re.I)
                        and re.search(r'(?m)^\s*[A-Za-z][A-Za-z &/-]*\s+[\d,]+(?:\.\d{1,2})?[ |}]*Base\s+Commissions\s+'
                                      +re.escape(field.value)+r'\s*$',source,re.I))
                    if evidence.page and (labelled or table):
                        _attach(field,evidence.page,evidence.source_text,'Printed recipient organization code')
    if not rows or any(row.decision=='REVIEW_REQUIRED' or any(v.status=='ERROR' for v in row.validation_results) for row in rows):
        return
    proof=[]
    for row in rows:
        source=next((e for e in row.evidence if e.page and _normal(row.name) in _normal(e.source_text)),None)
        if source is None:
            return
        if _money(row.commission_total) is None or _money(row.fee_total) is None:
            return
        for category in ('commission','fee'):
            parts=getattr(row,category+'_rows')
            amounts=[_money(p.amount) for p in parts]
            if any(amount is None for amount in amounts):
                return
            if parts and sum(amounts,Decimal(0))!=_money(getattr(row,category+'_total')):
                return
        if not re.search(r'commission|fees?|bonus',source.source_text or '',re.I):
            # Reconciled adapters may retain the category header separately
            # from the wrapped recipient body. Require every signed component
            # in that body's evidence before using its structured categories.
            parts=row.commission_rows+row.fee_rows
            literals='\n'.join(e.source_text or '' for e in row.evidence if e.page==source.page)
            printed={_money(m.group(0)) for m in re.finditer(r'(?<![\d.])(?:-\s*)?\$?\s*-?\s*\d[\d,]*\.\d{1,2}(?!\d)',literals)}
            if not parts or any(_money(p.amount) not in printed or not p.purpose for p in parts):
                return
        proof.append(source)
    for field in result.fields:
        code=field.field_name.split('.',1)[0]
        if code in {'3b','3c'}:
            category='commission' if code=='3b' else 'fee'
            parts=[_money(getattr(row,category+'_total')) for row in rows]
            if sum(parts,Decimal(0))!=_money(field.value):
                continue
            expression=' + '.join(format(part,'.2f') for part in parts)
            source=f'Derived {category} total: {expression} = {field.value}\n'
            source+='\n'.join(e.source_text for e in proof)
            _attach(field,proof[0].page,source,'Reconciled recipient calculation')
        if code=='3d':
            commission=sum((_money(row.commission_total) for row in rows),Decimal(0))
            fee=sum((_money(row.fee_total) for row in rows),Decimal(0))
            category='COMMISSIONS & FEES' if commission and fee else 'COMMISSIONS' if commission else 'FEES' if fee else ''
            purposes={_normal(part.purpose) for row in rows for part in row.commission_rows+row.fee_rows if part.purpose}
            if _normal(field.value)==_normal(category) or _normal(field.value) in purposes:
                _attach(field,proof[0].page,f'Derived recipient payment category: {field.value}\n'+'\n'.join(e.source_text for e in proof),'Reconciled recipient classification')


def completed_document_source_pages(raw,file_name):
    """Read actual page evidence only for one matching completed document."""
    if not isinstance(raw,dict):
        return []
    as_object=lambda value:value if isinstance(value,dict) else {}
    outputs=raw.get('outputs')
    queued=as_object(as_object(raw.get('ingest')).get('ingest'))
    complete=as_object(as_object(raw.get('process')).get('ingest'))
    documents=as_object(as_object(complete.get('progress')).get('complete')).get('documents')
    if (not isinstance(outputs,list) or not isinstance(documents,list)
        or len(outputs)!=1 or len(documents)!=1 or complete.get('status')!='complete'
        or not queued.get('processId') or queued['processId']!=complete.get('processId')):
        return []
    output,document=outputs[0],documents[0]
    if not isinstance(output,dict) or not isinstance(document,dict):
        return []
    if (output.get('fileName')!=file_name or document.get('fileName')!=file_name
        or not output.get('sourceUrl') or output['sourceUrl']!=document.get('sourceUrl')):
        return []
    chunks=output.get('chunks') or []
    pages=output.get('documentPages') or []
    if not isinstance(chunks,list) or not isinstance(pages,list):
        return []
    for page in pages:
        if not isinstance(page,dict) or not isinstance(page.get('chunks') or [],list):
            return []
        chunks=[*chunks,*(page.get('chunks') or [])]
    if any(not isinstance(chunk,dict) for chunk in chunks):
        return []
    return provider_source_pages(raw)


def provider_source_pages(raw):
    """Actual page-bound X-Ray text only, not structured answers or traces."""
    pages={}
    for output in (raw or {}).get('outputs',[]):
        if not isinstance(output,dict):
            continue
        raw_chunks=output.get('chunks') or []
        chunks=list(raw_chunks) if isinstance(raw_chunks,list) else []
        document_pages=output.get('documentPages') or []
        for page in document_pages if isinstance(document_pages,list) else []:
            if isinstance(page,dict):
                page_chunks=page.get('chunks') or []
                if isinstance(page_chunks,list):
                    chunks.extend(page_chunks)
        for chunk in chunks:
            if not isinstance(chunk,dict):
                continue
            numbers=chunk.get('pageNumbers') or []
            text=chunk.get('text') or ''
            if (not isinstance(numbers,list) or len(numbers)!=1 or type(numbers[0]) is not int
                or numbers[0]<1 or not isinstance(text,str)):
                continue
            if re.search(r'\bFields?\s+\d+[a-z]\b|\bshould\s+(?:also\s+)?(?:default|check|insert)',text,re.I):
                continue
            if text and text not in pages.setdefault(numbers[0],[]):
                pages[numbers[0]].append(text)
    return [(page,'\n'.join(texts)) for page,texts in sorted(pages.items())]


def attach_final_field_evidence(result, page_texts):
    """Evidence-only pass: no new values, default approvals or held decisions."""
    _recipient_evidence(result)
    for field in result.fields:
        code=field.field_name.split('.',1)[0]
        label=_LABELS.get(code)
        candidates=[]
        expected=re.sub(r'[^a-z0-9]','',str(field.value or '').casefold())
        if not expected:
            continue
        if code=='10a':
            review=(result.raw or {}).get('source_review',{})
            for check in review.get('reconciliations',[]):
                parts=[p for p in review.get('premium_components',[]) if p.get('page')==check.get('page')]
                amounts=[_money(p.get('amount')) for p in parts]
                if (check.get('kind')=='premium_components' and check.get('result')=='MATCH'
                    and parts and all(amount is not None for amount in amounts)
                    and sum(amounts,Decimal(0))==_money(check.get('printed_total'))==_money(field.value)):
                    source='Premium applied components:\n'+'\n'.join(p['source_text'] for p in parts)
                    source+='\n'+check['source_text']
                    candidates.append((check['page'],source))
        for page,text in page_texts:
            if code=='1e':
                header=re.search(r'Number\s+of\s+Persons\s+on[\s\S]{0,180}?Reporting\s+Period',text,re.I)
                if header:
                    body=re.split(r'(?im)^\s*[23][.)]\s+Insurance',text[header.end():],maxsplit=1)[0]
                    rows=list(re.finditer(r'(?m)^\s*(?:AD\s*&\s*D|Life|Dental|Vision|Long\s+Term\s+Disability)\s+'
                        r'(?P<count>\d[\d,]*)\s+'+_DATE+r'\s+'+_DATE+r'\s*$',body,re.I))
                    if rows and all(r.group('count').replace(',','')==str(field.value).replace(',','') for r in rows):
                        candidates.append((page,header.group(0)+'\n'+'\n'.join(r.group(0).strip() for r in rows)))
            if code=='10a':
                for premium in _PREMIUM.finditer(text):
                    if _money(premium.group('amount'))==_money(field.value):
                        candidates.append((page,premium.group(0)))
            if code in {'1f','1g'}:
                for period in _PERIOD.finditer(text):
                    literal=period.group(1 if code=='1f' else 2).replace('-','/')
                    try:
                        normalized=datetime.strptime(literal,'%m/%d/%Y').strftime('%m/%d/%Y')
                    except ValueError:
                        continue
                    if normalized==field.value:
                        candidates.append((page,period.group(0)))
            if code in {'1a','1b','1c','1d'}:
                for card in _CARRIER_CARD.finditer(text):
                    header=text[max(0,card.start()-200):card.start()]
                    if not re.search(r'Name\s+of\s+Insurance\s+Carrier.*\bEIN\b.*\bNAIC',header,re.I|re.S):
                        continue
                    value=card.group('carrier' if code=='1a' else _COLUMN[code])
                    if re.sub(r'[^a-z0-9]','',value.casefold())==expected:
                        candidates.append((page,header+card.group(0)))
            if code in _COLUMN and re.search(r'\bEIN\b',text,re.I) and re.search(r'\bNAIC\b',text,re.I):
                for row in _IDENTITY_ROW.finditer(text):
                    value=row.group(_COLUMN[code])
                    if re.sub(r'[^a-z0-9]','',value.casefold())==expected:
                        header=text[max(0,row.start()-500):row.start()]
                        if re.search(r'\bEIN\b',header,re.I) and re.search(r'\bNAIC\b',header,re.I):
                            candidates.append((page,header+row.group(0)))
            if label is None:
                continue
            lines=[line for line in text.splitlines() if line.strip()]
            for index,line in enumerate(lines):
                anchor=re.search(label,line,re.I)
                if not anchor:
                    continue
                # Identity blocks often place labels in one header row and
                # their values in the immediately following row. No radius
                # search across other sections, recipients, or pages.
                source='\n'.join(lines[index:index+2])
                normal=re.sub(r'[^a-z0-9]','',source.casefold())
                if expected not in normal:
                    continue
                if code=='1b':
                    tail=line[anchor.end():].strip(' :#')
                    if not tail and index+1<len(lines):
                        tail=lines[index+1].strip()
                    bound=re.match(r'(\d{2}-?\d{7})(?!\d)',tail)
                    if not bound or re.sub(r'\D','',bound.group(1))!=expected:
                        continue
                if code in {'1c','1d'}:
                    tail=line[anchor.end():].strip(' :#')
                    if not tail and index+1<len(lines):
                        tail=lines[index+1].strip()
                    bound=re.match(r'([A-Za-z0-9]+(?:[-][A-Za-z0-9]+)*)',tail)
                    if not bound or re.sub(r'[^a-z0-9]','',bound.group(1).casefold())!=expected:
                        continue
                if code=='1e' and not re.search(r'(?<!\d)'+re.escape(str(field.value).replace(',',''))+r'(?!\d)',source):
                    continue
                candidates.append((page,source))
        if candidates:
            page,source=candidates[0]
            _attach(field,page,source)
            if code=='1a':
                rejected=[value for value in field.candidate_values if re.fullmatch(
                    r'(?:THE\s+CARRIER\s+)?HEREBY\s+CERTIFIES\s+THAT\s+THIS\s+INFORMATION\s+IS\s+COMPLETE\s+AND\s+ACCURATE[.!]?',str(value).strip(),re.I)]
                field.candidate_values=[value for value in field.candidate_values if value not in rejected]
                for value in rejected:
                    record={'field':field.field_name,'candidate':value,'reason':'Certification sentence is not a carrier identity; labelled carrier evidence retained.'}
                    if not isinstance(result.raw,dict):
                        result.raw={'provider_raw':result.raw}
                    if record not in result.raw.setdefault('rejected_source_candidates',[]):
                        result.raw['rejected_source_candidates'].append(record)
