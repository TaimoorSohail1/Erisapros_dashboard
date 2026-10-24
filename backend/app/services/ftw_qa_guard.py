"""Private, run-scoped QA authorization. Never selects a production destination.

The server-side folder list must retain closed-run tombstones. The private S3
registry is re-read before every write; absence/expiry never opens a QA filing.
No source documents, target identifiers or live snapshots belong in this module.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import xml.etree.ElementTree as ET

import boto3
from app.config import get_settings
from app.models import FormType
from app.repositories import get_repository
from app.services.storage import StorageService

CONTROLS = {'TransactionType','EditCheck','CustomerID','PlanID','FTWCustomerID','FTWPlanID',
            'FTWSeqNo','Year','Type','ErrorCode','ErrorDesc','StatusSuccess'}
BROKER_BASES = ('ProvinceOrState','AddressLine1','AddressLine2','FeesPdText','CommPdAmt','FeesPdAmt',
                'ForeignAddy','PostalCode','ZipCode','Country','State','City','Code','Name')


def _folder_ids(settings):
    ids = json.loads(getattr(settings,'ftw_qa_guard_folder_ids_json','[]') or '[]')
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('QA folder authorization configuration is invalid.')
    return set(ids)


def _source_folders(filing):
    return {str(i) for i in [filing.sharefile_parent_id,
        *(d.get('sharefile_parent_id') for d in filing.package_documents)] if i}


def is_qa_scoped(filing, settings=None):
    return bool(filing.qa_run_binding or _source_folders(filing) & _folder_ids(settings or get_settings()))


def _read_registry(settings):
    key = settings.ftw_qa_registry_s3_key or ''
    if not key.startswith('qa/control/') or not settings.s3_bucket_name or not settings.aws_region:
        raise ValueError('QA authorization registry is not configured.')
    response = boto3.client('s3', region_name=settings.aws_region).get_object(Bucket=settings.s3_bucket_name, Key=key)
    body = response['Body'].read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('QA authorization registry exceeds its size limit.')
    registry = json.loads(body)
    if registry.get('schema_version') != 1 or not isinstance(registry.get('runs'), list):
        raise ValueError('QA authorization registry format is invalid.')
    return registry


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _active(run):
    expiry = datetime.fromisoformat(str(run.get('expires_at') or '').replace('Z','+00:00'))
    if run.get('state') != 'ACTIVE' or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('QA run is closed or expired; vendor writes are prohibited.')


def _scalar(tag, value):
    text = '' if value is None else str(value).strip()
    if tag.endswith(('Amt','AmtXX')):
        try: return ('amount', Decimal(text.replace(',','').replace('$','')))
        except InvalidOperation: pass
    if tag.endswith('Date'):
        for fmt in ('%m/%d/%Y','%m-%d-%Y','%Y-%m-%d'):
            try: return ('date',datetime.strptime(text,fmt).date())
            except ValueError: pass
    return ('text',re.sub(r'\s+',' ',text))


def _leaf_fields(element, *, containers=()):
    values = {}
    seen = set()
    for child in element:
        if child.tag in seen: raise ValueError('QA payload contains a duplicate field.')
        seen.add(child.tag)
        if child.tag in containers: continue
        if list(child): raise ValueError('QA payload contains an unrecognized nested field.')
        values[child.tag] = _scalar(child.tag,child.text)
    return values


def _schedule_fields(document):
    values = _leaf_fields(document,containers={'DOLSubPartData'})
    brokers = []
    for parts in document.findall('./DOLSubPartData'):
        for row in parts:
            if row.tag != 'Broker':
                raise ValueError('QA Schedule A payload contains an unrecognized subpart.')
            brokers.append(_leaf_fields(row))
    return values,brokers


def _broker_key(tag):
    if tag.endswith('XX'): return tag
    matched = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_.-]*?)(\d{1,2})',tag)
    if matched and int(matched[2]) > 0: return matched[1]+'XX'
    raise ValueError('QA baseline has an unrecognized broker component.')


def _vendor_business(record):
    rows = [r for r in (record.get('query_subparts') or {}).get('Broker',[]) if r]
    flattened = {key for row in rows for key in row}
    primary = {tag:_scalar(tag,value) for tag,value in record['query_results'].items()
        if tag not in CONTROLS and tag not in flattened
        and not any(re.fullmatch(re.escape(base)+r'\d{1,2}',tag) for base in BROKER_BASES)}
    brokers = []
    for row in rows:
        normalized = {}
        for tag,value in row.items():
            key = _broker_key(tag)
            if key in normalized: raise ValueError('QA baseline has duplicate broker aliases.')
            normalized[key] = _scalar(key,value)
        brokers.append(normalized)
    return primary,brokers


class QAWriteGuard:
    def __init__(self, run, case, review):
        self.run,self.case,self.review = run,case,review

    def validate_xml(self, xml):
        root = ET.fromstring(xml)
        nodes = [n for n in root if n.tag not in {'KeyID','DataBatch'}] + root.findall('./DataBatch/*')
        writes = [n for n in nodes if n.findtext('TransactionType') != 'Q' and n.tag != 'GenerateDocument']
        if not writes: return
        _active(self.run)
        target = self.case['target']
        keys = ['ftw_customer_id','ftw_plan_id','year','ftw_seq_no']
        if any(not re.fullmatch(r'\d+',str(target.get(k) or '')) for k in keys):
            raise ValueError('QA exact target authorization is invalid.')
        if tuple(str(getattr(self.review,k) or '') for k in keys) != tuple(str(target[k]) for k in keys):
            raise ValueError('QA matched destination differs from its exact authorization.')
        expected_identity = tuple(str(target[k]) for k in keys[:3])
        if sum(str(r.get('ftw_seq_no')) == str(target['ftw_seq_no']) for r in self.case['baseline_records']) != 1:
            raise ValueError('QA selected sequence is absent or duplicated in the original snapshot.')
        schedule_documents = []
        for node in writes:
            if tuple(node.findtext(k) or '' for k in ['FTWCustomerID','FTWPlanID','Year']) != expected_identity:
                raise ValueError('QA payload attempts a different customer, plan or year.')
            if node.find('CustomerID') is not None or node.find('PlanID') is not None:
                raise ValueError('QA writes require exact FTW identities without alternate browser identifiers.')
            if node.tag == 'DOLScheduleAData' and node.findtext('TransactionType') == '2':
                # Compare all supported business data, never silently omit an
                # unknown nested structure from the preservation boundary.
                _schedule_fields(node)
                schedule_documents.append(node)
            elif node.tag == 'DOL5500Data' and node.findtext('TransactionType') == '1':
                for child in node:
                    if child.tag in CONTROLS: continue
                    if list(child): raise ValueError('QA parent payload contains an unauthorized nested change.')
                    sent = _scalar(child.tag,child.text)
                    baseline = self.case['baseline_form_5500']
                    if child.tag in baseline and sent == _scalar(child.tag,baseline[child.tag]): continue
                    allowed = self.case['allowed_parent_values'].get(child.tag,[])
                    if not isinstance(allowed,list) or not any(sent == _scalar(child.tag,v) for v in allowed):
                        raise ValueError('QA parent payload changes a field outside its approved values.')
            else:
                raise ValueError('QA payload contains an unauthorized vendor operation.')
        if not schedule_documents: return
        records = self.case['baseline_records']
        ordered = sorted(records,key=lambda r:int(r['ftw_seq_no']))
        for record,document in zip(ordered,schedule_documents):
            if document.findtext('ScheduleDesc') != record['query_results'].get('ScheduleDesc'):
                raise ValueError('QA replacement changes the original record order.')
            explicit_sequence = document.findtext('FTWSeqNo')
            if explicit_sequence is not None and explicit_sequence != str(record['ftw_seq_no']):
                raise ValueError('QA replacement contains a different explicit record sequence.')
        by_description = {d.findtext('ScheduleDesc'):d for d in schedule_documents}
        descriptions = [r['query_results'].get('ScheduleDesc') for r in records]
        if (len(schedule_documents) != len(records) or None in descriptions
                or len(set(descriptions)) != len(records) or len(by_description) != len(records)
                or set(by_description) != set(descriptions)):
            raise ValueError('QA replacement set adds, removes or ambiguously identifies a Schedule A.')
        for record in records:
            if str(record['ftw_seq_no']) == str(target['ftw_seq_no']): continue
            document = by_description[record['query_results']['ScheduleDesc']]
            expected,brokers = _vendor_business(record)
            values,actual_brokers = _schedule_fields(document)
            actual = {k:v for k,v in values.items() if k not in CONTROLS}
            if expected != actual or brokers != actual_brokers:
                raise ValueError('QA replacement changes an unselected Schedule A or broker component.')


async def configure_qa_write_guard(review, client):
    """Attach task-local authorization to the common transport, including recovery."""
    from app.services.ftwilliams import set_qa_write_guard
    settings = get_settings()
    configured_folders = _folder_ids(settings)
    filing = await get_repository().get_filing(review.filing_id) if configured_folders else None
    # Also inspect persisted bindings when configuration was removed: old QA
    # records must never silently become unrestricted ordinary filing sends.
    if filing is None:
        filing = await get_repository().get_filing(review.filing_id)
    if filing is None and configured_folders:
        raise ValueError('QA dispatch requires a persisted filing source context.')
    if filing is None or not is_qa_scoped(filing,settings):
        set_qa_write_guard(client,None)
        return
    registry = await asyncio.to_thread(_read_registry,settings)
    folders = _source_folders(filing)
    binding = filing.qa_run_binding or {}
    runs = [r for r in registry['runs'] if (r.get('run_id') == binding.get('run_id') if binding
        else folders & set(r.get('folder_ids') or []))]
    if len(runs) != 1: raise ValueError('QA source folder has no unique registered run.')
    run = runs[0]
    _active(run)
    if not folders or not folders <= set(run.get('folder_ids') or []):
        raise ValueError('QA package contains a source outside the registered folders.')
    hashes = {}
    for document in filing.package_documents:
        kind = document.get('document_type')
        if kind not in {'SCHEDULE_A','PLAN_WORKSHEET'} or kind in hashes:
            raise ValueError('QA requires one exact PDF and its one approved worksheet.')
        if document.get('s3_bucket') != settings.s3_bucket_name or not document.get('s3_key'):
            raise ValueError('QA source evidence is not in the approved private storage.')
        contents = await asyncio.to_thread(StorageService().load_pdf,document['s3_key'],document['s3_bucket'])
        hashes[kind] = hashlib.sha256(contents).hexdigest()
    if set(hashes) != {'SCHEDULE_A','PLAN_WORKSHEET'}:
        raise ValueError('QA source package is incomplete.')
    cases = [c for c in run.get('cases',[]) if c.get('source_sha256') == hashes.get('SCHEDULE_A')
        and c.get('worksheet_sha256') == hashes.get('PLAN_WORKSHEET')]
    if len(cases) != 1: raise ValueError('QA source/worksheet bytes do not match one authorized case.')
    case = cases[0]
    guard = QAWriteGuard(run,case,review)
    case_digest = _digest(case)
    if binding and (binding.get('case_sha256') != case_digest or binding.get('registry_key') != settings.ftw_qa_registry_s3_key):
        raise ValueError('QA authorization changed after source binding.')
    records = lambda rs: sorted([{'ftw_seq_no':str(r['ftw_seq_no']), 'query_results':r.get('query_results') or {},
        'query_subparts':r.get('query_subparts') or {}} for r in rs],key=lambda r:int(r['ftw_seq_no']))
    if records(review.schedule_a_records) != records(case['baseline_records']):
        raise ValueError('QA current Schedule A set changed since its independent baseline.')
    if review.form_5500_current_values != case['baseline_form_5500']:
        raise ValueError('QA parent differs from its independently approved pre-send state.')
    await get_repository().update_filing(filing.id,{'qa_run_binding':{
        'run_id':run['run_id'],'case_sha256':case_digest,'registry_key':settings.ftw_qa_registry_s3_key}})
    async def authorize(xml):
        parsed = ET.fromstring(xml)
        nodes = [n for n in parsed if n.tag not in {'KeyID','DataBatch'}] + parsed.findall('./DataBatch/*')
        if not any(n.findtext('TransactionType') != 'Q' and n.tag != 'GenerateDocument' for n in nodes): return
        latest = await asyncio.to_thread(_read_registry,settings)
        matches = [r for r in latest['runs'] if r.get('run_id') == run['run_id']]
        if len(matches) != 1: raise ValueError('QA authorization is no longer available.')
        if not folders <= set(matches[0].get('folder_ids') or []):
            raise ValueError('QA source folder authorization changed before dispatch.')
        latest_cases = [c for c in matches[0].get('cases',[]) if _digest(c) == case_digest]
        if len(latest_cases) != 1: raise ValueError('QA case authorization changed before dispatch.')
        guard.run = matches[0]
        guard.validate_xml(xml)
    set_qa_write_guard(client,authorize)
