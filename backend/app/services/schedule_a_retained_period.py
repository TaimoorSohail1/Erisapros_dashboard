"""Reconcile pre-normalization policy dates using the retained carrier document."""

import asyncio
from datetime import datetime
import hashlib
import logging

from app.models import DocumentType, ExtractedFieldStatus, ExtractionJobStatus, FilingStatus, FormType, SourceEvidence
from app.services.schedule_a_policy_period import _maximum_inclusive_period_end, explicit_contract_periods
from app.services.storage import StorageService


_BEGIN_RULE = "schedule_a_part_i_1f_policy_year_beginning_date"
_END_RULE = "schedule_a_part_i_1g_policy_year_ending_date"
_LOGGER = logging.getLogger(__name__)


async def refresh_retained_policy_end(repo, filing, fields):
    if not filing.id or filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
        return fields
    beginning = [field for field in fields if field.mapped_rule_key == _BEGIN_RULE]
    ending = [field for field in fields if field.mapped_rule_key == _END_RULE]
    if len(beginning) != 1 or len(ending) != 1:
        return fields
    start, end = beginning[0], ending[0]
    if not all(field.id and field.form_type == FormType.SCHEDULE_A
               and field.source_document_type == DocumentType.SCHEDULE_A
               and field.status == ExtractedFieldStatus.MATCHED and not field.keep_current_context
               and field.value == field.proposed_value and field.page and field.source_text for field in (start, end)):
        return fields
    try:
        begin_date, old_end = (datetime.strptime(field.value, "%m/%d/%Y") for field in (start, end))
    except ValueError:
        return fields
    if begin_date.year == 9999 or old_end <= _maximum_inclusive_period_end(begin_date):
        return fields
    documents = [doc for doc in filing.package_documents if doc.get("document_type") == DocumentType.SCHEDULE_A]
    if len(documents) != 1:
        return fields
    document = documents[0]
    digest, file_name = document.get("source_sha256"), document.get("file_name") or ""
    if not digest or not file_name.lower().endswith(".pdf"):
        return fields

    def load_pages():
        from app.services.extractor import extract_pdf_layout_text_pages

        data = StorageService().load_pdf(document["s3_key"], document.get("s3_bucket"), document.get("storage_path"))
        if hashlib.sha256(data).hexdigest() != digest:
            return None
        return extract_pdf_layout_text_pages(data)

    try:
        pages = await asyncio.to_thread(load_pages)
        if pages is None:
            return fields
        periods = [(page, period) for page, text in pages for period in explicit_contract_periods(text)]
        if not periods:
            jobs = await repo.list_extraction_jobs(filing.id)
            job = max(jobs, key=lambda item: item.created_at, default=None)
            if not job or not job.id or job.status != ExtractionJobStatus.COMPLETED:
                return fields
            reads = await repo.schedule_a_ocr_reads(filing.id, job.id, file_name)
            by_mode = {mode: [] for mode in (4, 6)}
            for read in reads:
                if (read.get("source_sha256") == digest and read.get("psm") in by_mode
                        and type(read.get("page")) is int and isinstance(read.get("text"), str)):
                    by_mode[read["psm"]].extend((read["page"], period) for period in explicit_contract_periods(read["text"]))
            if not all(any(page == end.page for page, _ in matches) for matches in by_mode.values()):
                return fields
            periods = by_mode[4] + by_mode[6]
    except Exception:
        _LOGGER.warning("Could not verify retained Schedule A dates for filing %s", filing.id)
        return fields

    identities = {(p.beginning, p.ending, p.original_ending, p.month_precision) for _, p in periods}
    if len(identities) != 1:
        return fields
    page, period = periods[0]
    if (period.month_precision or not period.adjusted_to_twelve_months or period.beginning != start.value
            or period.original_ending != end.value or not all(
                any(source_page == field.page and source_period.source_text in field.source_text
                    for source_page, source_period in periods) for field in (start, end)
            )):
        return fields
    reason = f"Schedule A policy ending date limited to 12 months using the original printed period on page {page}."
    evidence = SourceEvidence(provider="Schedule A explicit contract period", page=page,
                              source_text=f"{period.source_text}\nOriginal ending date {end.value} exceeded 12 months; automatically limited to {period.ending}.")
    updated = await repo.correct_extracted_value(end, period.ending, evidence, reason)
    return [updated if field.id == end.id else field for field in fields] if updated else fields
