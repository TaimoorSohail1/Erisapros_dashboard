"""Recheck stored, hash-bound evidence before FTW identity and roster matching.

This does not reupload a document, rerun the vendor, or manufacture approval.
Existing source, confidence, payment and complete-list checks remain authoritative.
"""
import asyncio
import hashlib
import logging
import re
from datetime import datetime

from app.models import AuditLog, ExtractedFieldStatus, NormalizedExtractionField, NormalizedExtractionResult
from app.services.broker_address_policy import apply_address_policy
from app.services.schedule_a_broker_roster import attest_source_broker_roster, broker_roster_is_complete, source_roster_review
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
IDENTITY_KEYS = {
    'schedule_a_part_i_1a_name_of_insurance_company': '1a. Name of Insurance Company',
    'schedule_a_part_i_1b_insurance_carrier_ein': '1b. Insurance Carrier EIN',
}


def _recoverable(field):
    return (field.mapped_rule_key in IDENTITY_KEYS and field.status == ExtractedFieldStatus.MISSING
        and not field.value.strip() and not field.proposed_value.strip() and not field.keep_current_context
        and not re.search(r'reviewer|manual|marked missing|user', field.status_reason or '', re.I))


async def verify_stored_schedule_a_source(repo, filing, fields):
    # Freeze preimages before storage I/O; the in-memory repository returns live models.
    filing = filing.model_copy(deep=True)
    fields = [field.model_copy(deep=True) for field in fields]
    documents = [d for d in filing.package_documents if d.get('document_type') == 'SCHEDULE_A']
    if not filing.package_documents and str(filing.document_type) in {'SCHEDULE_A', 'DocumentType.SCHEDULE_A'}:
        documents = [{'document_type': 'SCHEDULE_A', 'file_name': filing.file_name,
            's3_key': filing.s3_key, 's3_bucket': filing.s3_bucket, 'storage_path': filing.storage_path}]
    if len(documents) != 1 or not str(documents[0].get('file_name', '')).lower().endswith('.pdf'):
        return filing, fields
    document = documents[0]
    source_sha = document.get('source_sha256') or getattr(filing.schedule_a_broker_roster, 'source_sha256', None)
    if not re.fullmatch(r'[a-f0-9]{64}', str(source_sha or '')):
        return filing, fields
    missing = [f for f in fields if _recoverable(f)]
    roster = filing.schedule_a_broker_roster
    needs_roster = bool(filing.schedule_a_broker_rows and not getattr(roster, 'reviewer_confirmation', None)
        and not (broker_roster_is_complete(filing.schedule_a_broker_rows, roster)
                 and getattr(roster, 'source_sha256', None) == source_sha))
    if not missing and not needs_roster:
        return filing, fields
    # Evidence must be tied to these exact bytes, never merely the same filename.
    evidence = await repo.source_verification_evidence(filing.id, document['file_name'], source_sha)
    if not evidence:
        return filing, fields
    try:
        content = await asyncio.to_thread(StorageService().load_pdf, document['s3_key'],
            document.get('s3_bucket') or filing.s3_bucket, document.get('storage_path'))
        if hashlib.sha256(content).hexdigest() != source_sha:
            return filing, fields
        from app.services.extractor import extract_pdf_layout_text_pages, extract_schedule_a_broker_rows_from_document
        from app.services.schedule_a_statement_evidence import statement_identity_fields
        recovered = {f.field_name: f for f in statement_identity_fields(extract_pdf_layout_text_pages(content))}
        updates = []
        for field in missing:
            native = recovered.get(IDENTITY_KEYS[field.mapped_rule_key])
            if native:
                after = field.model_copy(update={'value': native.value, 'proposed_value': native.value,
                    'status': ExtractedFieldStatus.MATCHED, 'confidence': native.confidence,
                    'page': native.page, 'source_text': native.source_text, 'updated_at': datetime.utcnow(),
                    'status_reason': 'Recovered from explicit carrier identity labels in the hash-verified stored Schedule A PDF.'}, deep=True)
                updates.append((field, after))
        values = {}
        if needs_roster:
            final = next((stage for stage in reversed(evidence.get('extraction_stages', []))
                if stage.get('stage') == 'final_normalized'), {})
            result = NormalizedExtractionResult(provider='Stored source verification',
                fields=[NormalizedExtractionField.model_validate(f) for f in final.get('fields', [])],
                schedule_a_broker_rows=[r.model_copy(deep=True) for r in filing.schedule_a_broker_rows],
                schedule_a_broker_roster=roster.model_copy(deep=True) if roster else None,
                schedule_a_worksheet_summaries=filing.schedule_a_worksheet_summaries,
                raw=dict(evidence))
            coverage = {}
            independent = extract_schedule_a_broker_rows_from_document(content, document['file_name'],
                page_coverage=coverage, retained_ocr_source_reads=evidence.get('local_ocr_source_reads'))
            result.raw['independent_source_page_coverage'] = coverage
            result.schedule_a_broker_rows = apply_address_policy(result.schedule_a_broker_rows)
            attest_source_broker_roster(result, independent, source_sha)
            checked = source_roster_review(result.schedule_a_broker_rows, result.schedule_a_broker_roster)
            if checked.mode == 'REPLACE':
                values = {'schedule_a_broker_rows': result.schedule_a_broker_rows, 'schedule_a_broker_roster': checked}
        if not updates and not values:
            return filing, fields
        saved = await repo.save_source_verification(filing, updates, values)
        if not saved:
            return (await repo.get_filing(filing.id)) or filing, await repo.list_fields(filing.id)
        await repo.add_audit(AuditLog(filing_id=filing.id, event='stored_schedule_a_source_verified',
            message='Stored PDF evidence recovered without reupload or manual broker approval.',
            details={'source_sha256': source_sha, 'recovered_rule_keys': [old.mapped_rule_key for old,new in updates],
                'broker_roster_verified': bool(values), 'broker_count': len(saved.schedule_a_broker_rows)}))
        by_id = {old.id: new for old,new in updates}
        return saved, [by_id.get(f.id, f) for f in fields]
    except Exception as exc:
        # Optional recovery cannot authorize a replacement or break ordinary review.
        logger.warning('Stored Schedule A verification unavailable for %s (%s)', filing.id, type(exc).__name__)
        return filing, fields
