import asyncio
import re
from datetime import datetime, timedelta

from app.config import get_settings
from app.models import AuditLog, DocumentType, ExtractedField, ExtractedFieldStatus, ExtractionJobStatus, FilingStatus, FormType, RawExtraction, ScheduleABrokerRow, ScheduleAWorksheetSummary
from app.repositories import get_repository
from app.services.extractor import ExtractionService
from app.services.field_rule_admin import FieldRuleService
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_contract import FTWPayloadValidationError
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_classification import apply_schedule_a_classification, filter_schedule_a_fields_for_contract_type
from app.services.xml_builder import build_proposed_ftw_xml


async def process_extraction_job(filing_id: str, job_id: str, file_bytes: bytes, file_name: str) -> None:
    document_type = classify_document(file_name)
    await process_package_extraction_job(
        filing_id,
        job_id,
        [{"file_bytes": file_bytes, "file_name": file_name, "document_type": document_type}],
    )


async def process_package_extraction_job(filing_id: str, job_id: str, documents: list[dict]) -> None:
    repo = get_repository()
    rule_snapshot = await FieldRuleService(repo).published_snapshot()
    jobs = await repo.list_extraction_jobs(filing_id)
    job = next((item for item in jobs if item.id == job_id), None)
    max_attempts = job.max_attempts if job else 3

    for attempt in range(1, max_attempts + 1):
        try:
            await repo.update_extraction_job(
                job_id,
                {
                    "status": ExtractionJobStatus.SENT_TO_GROUNDX,
                    "attempts": attempt,
                    "started_at": datetime.utcnow(),
                    "last_error": None,
                },
            )
            await repo.update_filing(filing_id, {"status": FilingStatus.EXTRACTING, "error_message": None})
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="EXTRACTION_SENT",
                    message=f"Sent filing package to extractor. Attempt {attempt} of {max_attempts}.",
                    details={"document_count": len(documents)},
                )
            )

            await repo.update_extraction_job(job_id, {"status": ExtractionJobStatus.EXTRACTING})
            extractor = ExtractionService(rule_snapshot.rules)
            mapped_fields: list[ExtractedField] = []
            providers: list[str] = []
            raw_items: list[dict] = []
            schedule_a_broker_rows: list[ScheduleABrokerRow] = []
            schedule_a_worksheet_summaries: list[ScheduleAWorksheetSummary] = []
            schedule_a_classification_signals: list[str] = []

            for document in documents:
                file_name = str(document["file_name"])
                file_bytes = document["file_bytes"]
                document_type = document.get("document_type") or classify_document(file_name)
                form_type = form_type_for_document(document_type)
                extraction = await extractor.extract_document(file_bytes, file_name, document_type)
                providers.append(f"{document_label(document_type)}: {extraction.provider}")
                if document_type == DocumentType.SCHEDULE_A:
                    schedule_a_broker_rows.extend(extraction.schedule_a_broker_rows)
                    schedule_a_worksheet_summaries.extend(extraction.schedule_a_worksheet_summaries)
                    schedule_a_classification_signals.extend(extraction.classification_signals)
                raw_items.append(
                    {
                        "file_name": file_name,
                        "document_type": document_type.value,
                        "provider": extraction.provider,
                        "raw": extraction.raw,
                        "schedule_a_broker_row_count": len(extraction.schedule_a_broker_rows),
                        "schedule_a_worksheet_summary_count": len(extraction.schedule_a_worksheet_summaries),
                        "classification_signals": extraction.classification_signals,
                    }
                )

                await repo.add_raw_extraction(
                    RawExtraction(
                        filing_id=filing_id,
                        job_id=job_id,
                        provider=extraction.provider,
                        raw={
                            "file_name": file_name,
                            "document_type": document_type.value,
                            "raw": extraction.raw,
                            "schedule_a_broker_rows": [row.model_dump(mode="json") for row in extraction.schedule_a_broker_rows],
                            "schedule_a_worksheet_summaries": [summary.model_dump(mode="json") for summary in extraction.schedule_a_worksheet_summaries],
                            "classification_signals": extraction.classification_signals,
                        },
                    )
                )
                mapped = map_extraction_to_rules(
                    filing_id,
                    extraction.fields,
                    form_type=form_type,
                    source_document_type=document_type,
                    rules=rule_snapshot.rules,
                )
                mapped_fields.extend(mapped["fields"])

            await repo.update_extraction_job(job_id, {"status": ExtractionJobStatus.RAW_EXTRACTION_SAVED})
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="RAW_EXTRACTION_SAVED",
                    message="Raw extractor response saved to MongoDB.",
                    details={"providers": providers, "field_count": len(mapped_fields)},
                )
            )

            await repo.update_extraction_job(job_id, {"status": ExtractionJobStatus.MAPPING})
            mapped_fields = harmonize_schedule_a_reference_fields(mapped_fields)
            mapped_fields = harmonize_schedule_a_business_rule_fields(mapped_fields)
            mapped_fields = apply_schedule_a_sanity_checks(mapped_fields)
            schedule_a_classification_signals = sorted(set(schedule_a_classification_signals))
            contract_classification = apply_schedule_a_classification(
                mapped_fields,
                schedule_a_classification_signals,
            )
            relevant_fields = filter_schedule_a_fields_for_contract_type(
                mapped_fields,
                contract_classification.contract_type,
                rules=rule_snapshot.rules,
            )
            proposed_xml, preview_validation_issues = build_safe_proposed_ftw_xml(relevant_fields)
            summary = summarize_mapped_fields(relevant_fields)
            fields: list[ExtractedField] = await repo.replace_fields(filing_id, mapped_fields)

            await repo.update_filing(
                filing_id,
                {
                    "status": summary["status"],
                    "extraction_provider": " + ".join(providers),
                    "field_rule_set_version": rule_snapshot.version,
                    "overall_confidence": summary["overall_confidence"],
                    "missing_high_priority_count": summary["missing_high_priority_count"],
                    "missing_medium_priority_count": summary["missing_medium_priority_count"],
                    "missing_low_priority_count": summary["missing_low_priority_count"],
                    "low_confidence_count": summary["low_confidence_count"],
                    "unmapped_count": summary["unmapped_count"],
                    "review_field_count": summary["review_field_count"],
                    "found_field_count": summary["found_field_count"],
                    "excluded_field_count": max(0, len(mapped_fields) - len(relevant_fields)),
                    "schedule_a_contract_type": contract_classification.contract_type,
                    "schedule_a_contract_type_reason": contract_classification.reason,
                    "schedule_a_contract_type_confirmed": True,
                    "schedule_a_contract_type_confidence": contract_classification.confidence,
                    "schedule_a_contract_type_evidence": list(contract_classification.evidence),
                    "schedule_a_classification_signals": schedule_a_classification_signals,
                    "schedule_a_broker_rows": [row.model_dump(mode="json") for row in schedule_a_broker_rows],
                    "schedule_a_worksheet_summaries": [summary.model_dump(mode="json") for summary in schedule_a_worksheet_summaries],
                    "proposed_xml": proposed_xml,
                    "error_message": None,
                },
            )
            await supersede_duplicate_active_package_rows(filing_id)
            await repo.update_extraction_job(
                job_id,
                {
                    "status": ExtractionJobStatus.COMPLETED,
                    "completed_at": datetime.utcnow(),
                    "next_retry_at": None,
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="MAPPING_COMPLETED",
                    message="Fields mapped, validation completed, and XML preview generated.",
                    details={
                        "status": summary["status"],
                        "missing_high": summary["missing_high_priority_count"],
                        "low_confidence": summary["low_confidence_count"],
                        "schedule_a_broker_row_count": len(schedule_a_broker_rows),
                        "schedule_a_worksheet_summary_count": len(schedule_a_worksheet_summaries),
                        "documents": [{"file_name": item["file_name"], "document_type": item["document_type"], "provider": item["provider"]} for item in raw_items],
                        "field_rule_set_version": rule_snapshot.version,
                        "schedule_a_contract_type": contract_classification.contract_type,
                        "schedule_a_contract_type_reason": contract_classification.reason,
                        "schedule_a_contract_type_confidence": contract_classification.confidence,
                        "schedule_a_contract_type_evidence": list(contract_classification.evidence),
                        "preview_validation_issue_count": len(preview_validation_issues),
                    },
                )
            )
            if preview_validation_issues:
                await repo.add_audit(
                    AuditLog(
                        filing_id=filing_id,
                        event="FTW_PREVIEW_VALIDATION_BLOCKED",
                        message="Extraction completed, but the FT Williams XML preview needs reviewer corrections before it can be generated.",
                        details={
                            "issues": [
                                {"tag": issue.tag, "value": issue.value, "reason": issue.reason}
                                for issue in preview_validation_issues
                            ]
                        },
                    )
                )
            await auto_query_ftw_current(filing_id)
            for document in documents:
                sharefile_item_id = document.get("sharefile_item_id")
                if sharefile_item_id:
                    await repo.upsert_sharefile_file(
                        str(sharefile_item_id),
                        {
                            "status": "EXTRACTED",
                            "last_extracted_at": datetime.utcnow(),
                            "filing_id": filing_id,
                            "extraction_job_id": job_id,
                        },
                    )
            return
        except Exception as exc:
            error = str(exc)
            retry_at = datetime.utcnow() + timedelta(seconds=5 * attempt)
            await repo.update_extraction_job(
                job_id,
                {
                    "status": ExtractionJobStatus.FAILED,
                    "attempts": attempt,
                    "last_error": error,
                    "next_retry_at": retry_at if attempt < max_attempts else None,
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="EXTRACTION_FAILED",
                    message=error,
                    details={"attempt": attempt, "will_retry": attempt < max_attempts},
                )
            )
            if attempt >= max_attempts:
                await repo.update_filing(filing_id, {"status": FilingStatus.FAILED, "error_message": error})
                for document in documents:
                    sharefile_item_id = document.get("sharefile_item_id")
                    if sharefile_item_id:
                        await repo.upsert_sharefile_file(
                            str(sharefile_item_id),
                            {
                                "status": "FAILED",
                                "last_extraction_failed_at": datetime.utcnow(),
                                "last_extraction_error": error,
                                "filing_id": filing_id,
                                "extraction_job_id": job_id,
                            },
                        )
                return
            await asyncio.sleep(5 * attempt)


def build_safe_proposed_ftw_xml(fields: list[ExtractedField]):
    """Build the read-only XML preview without failing document extraction.

    A contract validation error means one or more extracted values require a
    reviewer. It is not an extraction-system failure. Mark the affected field
    for review, suppress the unsafe preview, and let the filing continue into
    the normal review workflow.
    """
    try:
        return build_proposed_ftw_xml(fields), []
    except FTWPayloadValidationError as exc:
        issues_by_tag = {issue.tag: issue for issue in exc.issues}
        for field in fields:
            tag = str(field.ftw_resolved_tag or field.xml_tag or "")
            issue = issues_by_tag.get(tag)
            if not issue:
                continue
            field.status = ExtractedFieldStatus.LOW_CONFIDENCE
            field.confidence = min(field.confidence, 0.5)
            field.status_reason = f"FT Williams pre-send validation: {issue.reason}"
            field.updated_at = datetime.utcnow()
        return None, list(exc.issues)


async def process_extraction_batch(packages: list[tuple[str, str, list[dict]]]) -> None:
    """Process a bulk intake with a bounded number of active packages."""
    concurrency = max(1, get_settings().filing_extraction_concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_package(item: tuple[str, str, list[dict]]):
        filing_id, job_id, documents = item
        async with semaphore:
            return await process_package_extraction_job(filing_id, job_id, documents)

    results = await asyncio.gather(*(run_package(item) for item in packages), return_exceptions=True)
    repo = get_repository()
    for (filing_id, job_id, _documents), result in zip(packages, results):
        if not isinstance(result, Exception):
            continue
        error = str(result)
        await repo.update_extraction_job(
            job_id,
            {
                "status": ExtractionJobStatus.FAILED,
                "last_error": error,
                "next_retry_at": None,
            },
        )
        await repo.update_filing(filing_id, {"status": FilingStatus.FAILED, "error_message": error})
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="EXTRACTION_BATCH_FAILED",
                message=error,
            )
        )


async def auto_query_ftw_current(filing_id: str, review_service: FTWilliamsReviewService | None = None) -> None:
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    previous_status = filing.status if filing else FilingStatus.NEEDS_REVIEW
    if previous_status not in {FilingStatus.FAILED, FilingStatus.REJECTED, FilingStatus.SUPERSEDED, FilingStatus.DELETED}:
        await repo.update_filing(filing_id, {"status": FilingStatus.QUERYING_FTW_CURRENT})
    await repo.add_audit(
        AuditLog(
            filing_id=filing_id,
            event="FTWILLIAMS_CURRENT_AUTO_QUERY_STARTED",
            message="Automatic FT Williams current-data query started after extraction completed.",
        )
    )
    try:
        if review_service is not None:
            # Test doubles and explicit callers keep the original two-argument
            # service contract. Production auto-query enables snapshot reuse.
            review = await review_service.prepare_review(filing_id, send_queries=True)
        else:
            review = await FTWilliamsReviewService().prepare_review(
                filing_id,
                send_queries=True,
                reuse_current_snapshot=True,
            )
        await repo.update_filing(filing_id, {"status": previous_status})
        if review.current_query_success:
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="FTWILLIAMS_CURRENT_AUTO_QUERY_SUCCEEDED",
                    message="Automatic FT Williams current-data query completed.",
                    details={
                        "comparison_year": review.comparison_year,
                        "comparison_year_source": review.comparison_year_source,
                        "field_count": len(review.fields),
                    },
                )
            )
            return
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_CURRENT_AUTO_QUERY_FAILED",
                message="Automatic FT Williams current-data query did not return current FTW values.",
                details={
                    "error": review.error_message,
                    "plan_lookup_status": review.plan_lookup.status if review.plan_lookup else None,
                    "field_count": len(review.fields),
                },
            )
        )
    except Exception as exc:
        await repo.update_filing(filing_id, {"status": previous_status})
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_CURRENT_AUTO_QUERY_FAILED",
                message="Automatic FT Williams current-data query failed.",
                details={"error": str(exc)},
            )
        )


async def supersede_duplicate_active_package_rows(keep_filing_id: str) -> None:
    repo = get_repository()
    filings = await repo.list_filings()
    keep = next((filing for filing in filings if filing.id == keep_filing_id), None)
    if not keep or not keep.id:
        return
    package_key = filing_package_key(keep)
    if not package_key:
        return

    for filing in filings:
        if not filing.id or filing.id == keep.id:
            continue
        if filing.status in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}:
            continue
        if filing_package_key(filing) != package_key:
            continue
        await repo.update_filing(
            filing.id,
            {
                "status": FilingStatus.SUPERSEDED,
                "error_message": f"Duplicate active ShareFile package row was superseded by {keep.id}.",
            },
        )
        await repo.add_audit(
            AuditLog(
                filing_id=filing.id,
                event="SHAREFILE_DUPLICATE_PACKAGE_SUPERSEDED",
                message="Duplicate active ShareFile package row was superseded after extraction completed.",
                details={"package_key": package_key, "kept_filing_id": keep.id},
            )
        )


def filing_package_key(filing) -> str | None:
    for document in filing.package_documents:
        if document.get("package_key"):
            return str(document["package_key"])
    s3_key = str(filing.s3_key or "")
    if s3_key.startswith("sharefile-package/"):
        return s3_key.removeprefix("sharefile-package/")
    return None


def classify_document(file_name: str) -> DocumentType:
    name = file_name.lower()
    if "worksheet" in name or "plan worksheet" in name:
        return DocumentType.PLAN_WORKSHEET
    if name.endswith((".docx", ".doc")):
        return DocumentType.PLAN_WORKSHEET
    if "schedule" in name and "a" in name:
        return DocumentType.SCHEDULE_A
    return DocumentType.SCHEDULE_A


def form_type_for_document(document_type: DocumentType) -> FormType:
    if document_type == DocumentType.PLAN_WORKSHEET:
        return FormType.FORM_5500
    return FormType.SCHEDULE_A


def document_label(document_type: DocumentType) -> str:
    if document_type == DocumentType.PLAN_WORKSHEET:
        return "Plan Worksheet"
    if document_type == DocumentType.SCHEDULE_A:
        return "Schedule A"
    return "Unknown"


SCHEDULE_A_REFERENCE_RULE_MAP = {
    "schedule_a_part_iv_4a_plan_name": "form_5500_part_i_1a_plan_name",
    "schedule_a_part_iv_4b_plan_number_pn": "form_5500_part_i_1b_plan_number_pn",
    "schedule_a_part_iv_4c_sponsor_ein": "form_5500_part_i_1e_plan_sponsor_ein",
    "schedule_a_part_iv_4d_plan_year_beginning_date": "form_5500_part_i_6_plan_year_beginning_date",
    "schedule_a_part_iv_4e_plan_year_ending_date": "form_5500_part_i_7_plan_year_ending_date",
}


def harmonize_schedule_a_reference_fields(fields: list[ExtractedField]) -> list[ExtractedField]:
    by_rule_key = {field.mapped_rule_key: field for field in fields if field.mapped_rule_key}
    for schedule_key, worksheet_key in SCHEDULE_A_REFERENCE_RULE_MAP.items():
        schedule_field = by_rule_key.get(schedule_key)
        worksheet_field = by_rule_key.get(worksheet_key)
        if not schedule_field or not worksheet_field:
            continue

        worksheet_value = str(worksheet_field.proposed_value or worksheet_field.value or "").strip()
        if not worksheet_value:
            continue

        schedule_value = str(schedule_field.proposed_value or schedule_field.value or "").strip()
        if schedule_value and not _schedule_reference_values_match(schedule_key, schedule_value, worksheet_value):
            reason = (
                f"Schedule A identity value {schedule_value!r} conflicts with Plan Worksheet value "
                f"{worksheet_value!r}. Confirm the correct client, plan year, EIN, and plan number in Review."
            )
            for field in (schedule_field, worksheet_field):
                field.status = ExtractedFieldStatus.LOW_CONFIDENCE
                field.confidence = min(field.confidence, 0.5)
                field.status_reason = reason
                field.updated_at = datetime.utcnow()
            continue

        if schedule_value:
            # Both documents agree. Preserve the Schedule A source evidence
            # instead of replacing it with evidence from another document.
            continue

        schedule_field.value = worksheet_value
        schedule_field.proposed_value = worksheet_value
        schedule_field.confidence = max(schedule_field.confidence, worksheet_field.confidence)
        schedule_field.source_document_type = worksheet_field.source_document_type
        schedule_field.page = worksheet_field.page
        schedule_field.source_text = worksheet_field.source_text or worksheet_value
        schedule_field.status = ExtractedFieldStatus.MATCHED
        schedule_field.status_reason = (
            f"Copied from {worksheet_field.mapped_label or worksheet_field.source_field_name} "
            "because Schedule A Part IV uses the filing reference values."
        )
        schedule_field.updated_at = datetime.utcnow()

    return fields


def _schedule_reference_values_match(rule_key: str, left: str, right: str) -> bool:
    if rule_key.endswith("sponsor_ein"):
        return re.sub(r"\D", "", left) == re.sub(r"\D", "", right)
    if rule_key.endswith("plan_number_pn"):
        return re.sub(r"[^A-Z0-9]", "", left.upper()) == re.sub(r"[^A-Z0-9]", "", right.upper())
    if rule_key.endswith(("beginning_date", "ending_date")):
        def date_parts(value: str) -> tuple[int, int, int] | None:
            parts = [int(part) for part in re.findall(r"\d+", value)]
            if len(parts) != 3:
                return None
            if parts[0] > 1900:
                return parts[0], parts[1], parts[2]
            if parts[2] < 100:
                parts[2] += 2000
            return parts[2], parts[0], parts[1]

        return date_parts(left) == date_parts(right)
    normalize = lambda value: re.sub(r"[^A-Z0-9]", "", value.upper())
    return normalize(left) == normalize(right)


def harmonize_schedule_a_business_rule_fields(fields: list[ExtractedField]) -> list[ExtractedField]:
    by_rule_key = {field.mapped_rule_key: field for field in fields if field.mapped_rule_key}
    purpose_field = by_rule_key.get("schedule_a_part_i_3d_purpose")
    commissions_field = by_rule_key.get("schedule_a_part_i_3b_amount_of_commissions")
    fees_field = by_rule_key.get("schedule_a_part_i_3c_amount_of_fees")
    if not purpose_field:
        return fields

    derived_purpose = derive_schedule_a_purpose_from_fields(commissions_field, fees_field)
    if not derived_purpose:
        return fields

    purpose_field.value = derived_purpose
    purpose_field.proposed_value = derived_purpose
    purpose_field.confidence = max(purpose_field.confidence, 0.95)
    purpose_field.status = ExtractedFieldStatus.MATCHED
    purpose_field.status_reason = "Derived from Schedule A commission and fee values per field rules."
    purpose_field.updated_at = datetime.utcnow()
    return fields


SANITY_MONEY_RULE_KEYS = {
    "schedule_a_part_i_3b_amount_of_commissions",
    "schedule_a_part_i_3c_amount_of_fees",
    "schedule_a_part_iii_9a_premiums_1_amount_received",
    "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier",
}
SANITY_NAME_RULE_KEYS = {
    "schedule_a_part_i_1a_name_of_insurance_company",
    "schedule_a_part_i_3a_name_of_agent_broker_person",
    "schedule_a_part_iv_4a_plan_name",
    "form_5500_part_i_1a_plan_name",
    "form_5500_part_i_1d_plan_sponsor_name",
}
SANITY_HEADER_TOKENS = (
    "contract number",
    "naic code",
    "total premium",
    "# covered",
    "policy year",
    "agent or broker",
    "feespaid",
    "persons covered",
    "beginning date",
    "ending date",
    "insurance carrier",
)


def _flag_field_suspicious(field: ExtractedField, reason: str) -> None:
    if field.status not in (ExtractedFieldStatus.MATCHED, ExtractedFieldStatus.LOW_CONFIDENCE):
        return
    field.status = ExtractedFieldStatus.LOW_CONFIDENCE
    field.confidence = min(field.confidence, 0.5)
    field.status_reason = f"Sanity check: {reason}"
    field.updated_at = datetime.utcnow()


def apply_schedule_a_sanity_checks(fields: list[ExtractedField]) -> list[ExtractedField]:
    """Flag values that are very likely mis-parsed (date fragments as amounts,
    header text as names, commissions larger than premiums). Values are never
    changed or removed - only routed to review with lowered confidence."""
    by_rule_key = {field.mapped_rule_key: field for field in fields if field.mapped_rule_key}

    premium_field = by_rule_key.get("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier") or by_rule_key.get(
        "schedule_a_part_iii_9a_premiums_1_amount_received"
    )
    premium_amount = parse_numeric_amount(premium_field.proposed_value or premium_field.value) if premium_field else None

    for rule_key in SANITY_MONEY_RULE_KEYS:
        field = by_rule_key.get(rule_key)
        if not field:
            continue
        amount = parse_numeric_amount(field.proposed_value or field.value)
        if amount is None:
            continue
        if 0 < amount <= 31 and float(amount).is_integer():
            _flag_field_suspicious(field, f"Amount {amount:.0f} looks like a date fragment rather than a dollar amount.")
        elif rule_key in ("schedule_a_part_i_3b_amount_of_commissions", "schedule_a_part_i_3c_amount_of_fees") and premium_amount and amount > premium_amount:
            _flag_field_suspicious(field, "Commission/fee amount exceeds the total premium, which is very unlikely.")

    covered_field = by_rule_key.get("schedule_a_part_i_1e_persons_covered_end_of_policy_year")
    if covered_field and premium_field:
        covered_amount = parse_numeric_amount(covered_field.proposed_value or covered_field.value)
        if covered_amount is not None and premium_amount is not None and covered_amount == premium_amount and covered_amount > 0:
            _flag_field_suspicious(covered_field, "Persons covered equals the premium amount, which suggests both were mis-parsed from the same text.")
            _flag_field_suspicious(premium_field, "Premium equals the persons-covered count, which suggests both were mis-parsed from the same text.")

    for rule_key in SANITY_NAME_RULE_KEYS:
        field = by_rule_key.get(rule_key)
        if not field:
            continue
        value = str(field.proposed_value or field.value or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if len(value) > 80 or "\n" in value or any(token in lowered for token in SANITY_HEADER_TOKENS):
            _flag_field_suspicious(field, "Value looks like table header or layout text captured by mistake, not a real name.")

    return fields


def derive_schedule_a_purpose_from_fields(
    commissions_field: ExtractedField | None,
    fees_field: ExtractedField | None,
) -> str | None:
    commission_amount = parse_numeric_amount(commissions_field.proposed_value if commissions_field else "")
    fee_amount = parse_numeric_amount(fees_field.proposed_value if fees_field else "")

    has_commission = commission_amount is not None and commission_amount > 0
    has_fee = fee_amount is not None and fee_amount > 0

    if has_commission and has_fee:
        return "COMMISSIONS & FEES"
    if has_commission:
        return "COMMISSIONS"
    if has_fee:
        return "FEES"
    return None


def parse_numeric_amount(value: str | None) -> float | None:
    clean = str(value or "").replace("$", "").replace(",", "").strip()
    if not clean:
        return None
    try:
        return float(clean)
    except ValueError:
        return None


def summarize_mapped_fields(fields: list[ExtractedField]) -> dict:
    low_confidence_count = len([field for field in fields if field.status.value == "LOW_CONFIDENCE"])
    unmapped_count = len([field for field in fields if field.status.value == "UNMAPPED"])
    missing_high_priority_count = len([field for field in fields if field.status.value == "MISSING" and field.priority.value == "HIGH"])
    missing_medium_priority_count = len([field for field in fields if field.status.value == "MISSING" and field.priority.value == "MEDIUM"])
    missing_low_priority_count = len([field for field in fields if field.status.value == "MISSING" and field.priority.value == "LOW"])
    scored = [field for field in fields if field.status.value != "MISSING" and field.priority.value != "IGNORE"]
    overall_confidence = sum(field.confidence for field in scored) / len(scored) if scored else 0
    status = FilingStatus.NEEDS_REVIEW if missing_high_priority_count or low_confidence_count or unmapped_count else FilingStatus.READY_FOR_APPROVAL
    review_fields = [field for field in fields if field.priority.value != "IGNORE"]
    found_field_count = len(
        [
            field
            for field in review_fields
            if field.status.value not in {"MISSING", "UNMAPPED"}
            and str(field.proposed_value or field.value or "").strip()
        ]
    )
    return {
        "low_confidence_count": low_confidence_count,
        "missing_high_priority_count": missing_high_priority_count,
        "missing_medium_priority_count": missing_medium_priority_count,
        "missing_low_priority_count": missing_low_priority_count,
        "unmapped_count": unmapped_count,
        "overall_confidence": overall_confidence,
        "status": status,
        "review_field_count": len(review_fields),
        "found_field_count": found_field_count,
    }
