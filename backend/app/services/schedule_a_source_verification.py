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
from app.services.schedule_a_broker_roster import attest_source_broker_roster, broker_roster_is_complete, source_roster_review, _money
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
IDENTITY_KEYS = {
    'schedule_a_part_i_1a_name_of_insurance_company': '1a. Name of Insurance Company',
    'schedule_a_part_i_1b_insurance_carrier_ein': '1b. Insurance Carrier EIN',
    'schedule_a_part_i_1c_naic_code': '1c. NAIC Code',
    'schedule_a_part_i_1d_contract_policy_number': '1d. Contract/Policy Number',
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
    needs_roster = bool(not getattr(roster, 'reviewer_confirmation', None)
        and not (broker_roster_is_complete(filing.schedule_a_broker_rows, roster)
                 and getattr(roster, 'source_sha256', None) == source_sha))
    if not missing and not needs_roster:
        return filing, fields
    # Evidence must be tied to these exact bytes, never merely the same filename.
    evidence = await repo.source_verification_evidence(filing.id, document['file_name'], source_sha)
    try:
        content = await asyncio.to_thread(StorageService().load_pdf, document['s3_key'],
            document.get('s3_bucket') or filing.s3_bucket, document.get('storage_path'))
        if hashlib.sha256(content).hexdigest() != source_sha:
            return filing, fields
        from app.services.extractor import extract_pdf_layout_text_pages, extract_schedule_a_broker_rows_from_document, extract_schedule_a_acroform
        from app.services.schedule_a_statement_evidence import statement_identity_fields
        pages = await asyncio.to_thread(extract_pdf_layout_text_pages, content)
        coverage, native_proof, independent, unbound_payment_mismatch = {}, None, None, False
        if not evidence:
            # Old traces without a hash cannot act as a corroborating read.
            # Establish a NEW proof from two separate native source readers.
            from app.services.schedule_a_closed_source import closed_native_roster
            independent = await asyncio.to_thread(extract_schedule_a_broker_rows_from_document, content, document['file_name'], page_coverage=coverage)
            closed_rows, native_proof = await asyncio.to_thread(closed_native_roster, content, pages, coverage)
            if native_proof is None:
                from app.services.schedule_a_native_brokers import printed_payment_totals
                if printed_payment_totals(independent) is None:
                    return filing, fields
                unbound_payment_mismatch = True
            else:
                independent = closed_rows
            evidence = {'document_worksheet_summaries': [], 'extraction_stages': []}
        recovered = {f.field_name: f for f in statement_identity_fields(pages)}
        widgets, _ = await asyncio.to_thread(extract_schedule_a_acroform, content)
        recovered.update({f.field_name: f for f in widgets if f.field_name in IDENTITY_KEYS.values()})
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
        source_summaries = evidence.get('document_worksheet_summaries')
        if needs_roster and isinstance(source_summaries, list):
            final = next((stage for stage in reversed(evidence.get('extraction_stages', []))
                if stage.get('stage') == 'final_normalized'), {})
            result = NormalizedExtractionResult(provider='Stored source verification',
                fields=[NormalizedExtractionField.model_validate(f) for f in final.get('fields', [])],
                schedule_a_broker_rows=[r.model_copy(deep=True) for r in filing.schedule_a_broker_rows],
                schedule_a_broker_roster=roster.model_copy(deep=True) if roster else None,
                schedule_a_worksheet_summaries=source_summaries,
                raw=dict(evidence))
            if independent is None:
                independent = await asyncio.to_thread(extract_schedule_a_broker_rows_from_document, content, document['file_name'],
                    page_coverage=coverage, retained_ocr_source_reads=evidence.get('local_ocr_source_reads'))
            if native_proof is None:
                from app.services.schedule_a_closed_source import closed_native_roster
                closed_rows, native_proof = await asyncio.to_thread(closed_native_roster, content, pages, coverage)
                if native_proof is not None:
                    independent = closed_rows
            result.raw['independent_source_page_coverage'] = coverage
            from app.services.schedule_a_broker_roster import separate_service_provider_disclosures
            separate_service_provider_disclosures(result, pages)
            from app.services.schedule_a_broker_source_repair import repair_source_rows
            if unbound_payment_mismatch and not any(v.validator.startswith('reviewer_') and v.status == 'PASS'
                    for row in result.schedule_a_broker_rows for v in row.validation_results):
                # This is review-only recovery of an explicit source mismatch,
                # never independent corroboration or permission to replace.
                result.schedule_a_broker_rows = [row.model_copy(deep=True) for row in independent]
            if native_proof is not None:
                from app.services.schedule_a_broker_source_repair import set_corroborated_totals
                protected_rows = any(v.validator.startswith('reviewer_') and v.status == 'PASS'
                    for row in result.schedule_a_broker_rows for v in row.validation_results)
                if not protected_rows:
                    result.schedule_a_broker_rows = [r.model_copy(deep=True) for r in independent]
                set_corroborated_totals(result, independent, corroboration='two independent source readers and both printed section totals')
                repaired = not protected_rows
            else:
                repaired = repair_source_rows(result, independent) or unbound_payment_mismatch
            # A payee/producer identifier accidentally mapped into org code is
            # a role error, not an explicit unsupported organization code.
            # Confirm the role across complete reads of these exact source bytes.
            from app.services.extractor import _broker_page_coverage
            from app.services.schedule_a_broker_source_repair import repair_identifier_organization_codes
            count=coverage.get('page_count');packets=[]
            if count and len(pages)==count and all(p['readable'] for p in _broker_page_coverage(pages)):
                packets.append(pages)
            retained=evidence.get('local_ocr_source_reads') or []
            for mode in {read.get('psm') for read in retained}:
                packet=sorted((read['page'],read['text']) for read in retained if read.get('psm')==mode
                    and read.get('source_sha256')==source_sha and type(read.get('page')) is int and isinstance(read.get('text'),str))
                if count and [p for p,_ in packet]==list(range(1,count+1)) and all(p['readable'] for p in _broker_page_coverage(packet)):
                    packets.append(packet)
            repaired=repair_identifier_organization_codes(result.schedule_a_broker_rows,packets) or repaired
            from app.services.schedule_a_broker_validation import hold_invalid_source_organization_codes, hold_invalid_source_zip_codes
            for rows in (result.schedule_a_broker_rows, independent):
                hold_invalid_source_organization_codes(rows)
                hold_invalid_source_zip_codes(rows)
            result.schedule_a_broker_rows = apply_address_policy(result.schedule_a_broker_rows)
            if native_proof is None:
                attest_source_broker_roster(result, independent, source_sha)
            else:
                attest_source_broker_roster(result, independent, source_sha, native_proof=native_proof)
            checked = source_roster_review(result.schedule_a_broker_rows, result.schedule_a_broker_roster)
            if checked.mode == 'REPLACE' or repaired or (filing.schedule_a_broker_roster is not None and filing.schedule_a_broker_roster != checked):
                values = {'schedule_a_broker_rows': result.schedule_a_broker_rows, 'schedule_a_broker_roster': checked}
            # Keep displayed section totals synchronized only with the separately
            # corroborated category sums, never with unverified nearby numbers.
            for native in result.fields:
                if not native.source_text or not native.source_text.startswith('Verified sum of full labelled recipient payment columns;'):
                    continue
                field = next((f for f in fields if f.form_type and str(f.form_type) in {'SCHEDULE_A', 'FormType.SCHEDULE_A'}
                    and (f.mapped_label or f.source_field_name) == native.field_name), None)
                if (field and field.status not in {ExtractedFieldStatus.EDITED, ExtractedFieldStatus.IGNORED}
                        and not field.keep_current_context and (field.value != native.value or field.proposed_value != native.value)
                        and not re.search(r'reviewer|manual|user', field.status_reason or '', re.I)):
                    updates.append((field, field.model_copy(update={'value':native.value, 'proposed_value':native.value,
                        'status':ExtractedFieldStatus.MATCHED, 'confidence':native.confidence, 'page':native.page,
                        'source_text':native.source_text, 'status_reason':'Category sum recovered from labelled physical rows and independent source corroboration.',
                        'updated_at':datetime.utcnow()}, deep=True)))
            # Withdraw summaries made under an older, weaker automatic proof
            # when the current source cannot corroborate the complete amounts.
            # User corrections and explicit Keep Current choices are protected.
            verified = {f.field_name for f in result.fields if f.source_text and f.source_text.startswith(
                'Verified sum of full labelled recipient payment columns;')}
            originals = {f.get('field_name'): f for f in final.get('fields', [])}
            from app.services.schedule_a_native_brokers import printed_payment_totals
            printed = printed_payment_totals(independent)
            for field in fields:
                label = field.mapped_label or field.source_field_name
                owned = (str(field.source_text or '').startswith('Verified sum of full labelled recipient payment columns;')
                    or str(field.status_reason or '').startswith('SOURCE_RECOVERY_PENDING:'))
                printed_value = (format(printed[0 if str(label).startswith('3b.') else 1],'f')
                    if printed is not None and str(label).startswith(('3b.','3c.')) else None)
                wrong_printed_value = printed_value is not None and _money(field.value) != _money(printed_value)
                if ((not owned and not wrong_printed_value) or label in verified or not str(label).startswith(('3b.', '3c.'))
                        or field.status in {ExtractedFieldStatus.EDITED, ExtractedFieldStatus.IGNORED}
                        or field.keep_current_context or re.search(r'reviewer|manual|user',field.status_reason or '',re.I)):
                    continue
                native = originals.get(label) or {}
                value = printed_value if printed_value is not None else (native.get('value') if native.get('value') is not None else field.value)
                reason = 'SOURCE_RECOVERY_PENDING: payment totals require reconciliation against the complete source.'
                source_text = ('\n'.join(e.source_text for row in independent for e in row.evidence if e.source_text)
                    if printed_value is not None else native.get('source_text') or field.source_text)
                if field.value == value and not field.proposed_value and field.status == ExtractedFieldStatus.LOW_CONFIDENCE and field.status_reason == reason:
                    continue
                updates.append((field, field.model_copy(update={'value':value,'proposed_value':'',
                    'confidence':0,'status':ExtractedFieldStatus.LOW_CONFIDENCE,
                    'source_text':source_text,'status_reason':reason,
                    'updated_at':datetime.utcnow()},deep=True)))
        if not updates and not values:
            return filing, fields
        saved = await repo.save_source_verification(filing, updates, values)
        if not saved:
            return (await repo.get_filing(filing.id)) or filing, await repo.list_fields(filing.id)
        await repo.add_audit(AuditLog(filing_id=filing.id, event='stored_schedule_a_source_verified',
            message='Stored PDF evidence recovered without reupload or manual broker approval.',
            details={'source_sha256': source_sha, 'recovered_rule_keys': [old.mapped_rule_key for old,new in updates],
                'broker_roster_verified': bool(values and values['schedule_a_broker_roster'].mode == 'REPLACE'),
                'broker_mapping_recovered': bool(values), 'broker_count': len(saved.schedule_a_broker_rows)}))
        by_id = {old.id: new for old,new in updates}
        return saved, [by_id.get(f.id, f) for f in fields]
    except Exception as exc:
        # Optional recovery cannot authorize a replacement or break ordinary review.
        logger.warning('Stored Schedule A verification unavailable for %s (%s)', filing.id, type(exc).__name__)
        return filing, fields
