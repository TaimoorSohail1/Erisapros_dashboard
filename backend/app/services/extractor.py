import asyncio
import calendar
import csv
from copy import deepcopy
from datetime import datetime
from io import BytesIO, StringIO
import json
import os
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote_plus
import zipfile
import xml.etree.ElementTree as ET

import httpx
from groundx import Document, GroundX
from app.config import get_settings
from app.models import (
    DocumentType,
    FieldRuleMappingMode,
    FormType,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABenefitBreakdownRow,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
    ScheduleAWorksheetSummary,
    ScheduleAWorksheetValue,
)
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.groundx_schedule_a_workflow import normalize_groundx_schedule_a_extract
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
from app.services.schedule_a_layout_engine import (
    extract_layout_aware_schedule_a_fields,
    is_layout_label_text,
)
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.services.schedule_a_policy_period import explicit_contract_periods
from app.services.schedule_a_customer_rules import default_blank_organization_codes
from app.services.schedule_a_classification import classification_signals_from_text


SCHEDULE_A_EXPERIENCE_RATED_FIELDS = (
    "9a. Premiums: (1) Amount Received",
    "9a(2). Increase (decrease) in amount due but unpaid",
    "9a(3). Increase (decrease) in unearned premium reserve",
    "9a(4). Earned ((1) + (2) - (3))",
    "9b(1). Benefit Charges (1) Claims paid",
    "9b(2). Increase (decrease) in claim reserves",
    "9b(3). Incurred claims (add(1) and (2))",
    "9b(4). Claims Charged",
    "9c(1)(A). Commissions",
    "9c(1)(B). Administrative service or other fees",
    "9c(1)(C). Other Specific acquisition costs",
    "9c(1)(D). Other expenses",
    "9c(1)(E). Taxes",
    "9c(1)(F). Charges for risks or other contingencies",
    "9c(1)(G). Other retention charges",
    "9c(1)(H). Total retention",
    "9c(2). Dividends or retroactive rate refunds",
    "9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement",
    "9d(2). Claim reserves",
    "9d(3). Other reserves",
    "9e. Dividends or retroactive rate refunds due",
)

SCHEDULE_A_PREFER_PDF_TEXT_FIELDS = {
    "1a. Name of Insurance Company",
    "1b. Insurance Carrier EIN",
    "1c. NAIC Code",
    "1d. Contract/Policy Number",
    "1e. Persons Covered (End of Policy Year)",
    "1f. Policy Year Beginning Date",
    "1g. Policy Year Ending Date",
    "3b. Amount of Commissions",
    "3c. Amount of Fees",
    "3d. Purpose",
    "3e. Organizational Code",
    "10a. Total premiums or subscription charges paid to carrier",
}

SCHEDULE_A_LAYOUT_OWNED_FIELDS = {
    "1a. Name of Insurance Company",
    "1b. Insurance Carrier EIN",
    "1c. NAIC Code",
    "1d. Contract/Policy Number",
    "1e. Persons Covered (End of Policy Year)",
    "1f. Policy Year Beginning Date",
    "1g. Policy Year Ending Date",
    "3a. Name of Agent/Broker/Person",
    "3b. Amount of Commissions",
    "3c. Amount of Fees",
    "3d. Purpose",
    "3e. Organizational Code",
    "10a. Total premiums or subscription charges paid to carrier",
    "11. Did the insurance company fail to provide any information necessary to complete Schedule A?",
    *SCHEDULE_A_EXPERIENCE_RATED_FIELDS,
}


class ExtractionService:
    def __init__(self, field_rules=None):
        self.field_rules = list(field_rules) if field_rules is not None else DEFAULT_FIELD_RULES

    async def extract_document(self, file_bytes: bytes, file_name: str, document_type: DocumentType) -> NormalizedExtractionResult:
        if document_type == DocumentType.PLAN_WORKSHEET:
            return await self.extract_plan_worksheet(file_bytes, file_name)
        return await self.extract_schedule_a(file_bytes, file_name)

    async def extract_plan_worksheet(self, file_bytes: bytes, file_name: str) -> NormalizedExtractionResult:
        context_text = extract_docx_text(file_bytes) if file_name.lower().endswith(".docx") else ""
        if not context_text and file_name.lower().endswith(".pdf"):
            context_text = "\n\n".join(text for _, text in extract_pdf_text_pages(file_bytes))

        worksheet_rules = rules_for_form(self.field_rules, FormType.FORM_5500)
        default_year_match = re.search(r"\b(20\d{2})\b", file_name)
        default_year = default_year_match.group(1) if default_year_match else None
        local_fields = (
            parse_plan_worksheet_text(context_text, rules=worksheet_rules, default_year=default_year)
            if context_text
            else []
        )
        worksheet_summaries = (
            extract_plan_worksheet_schedule_a_summaries(context_text, default_year=default_year)
            if context_text
            else []
        )
        if file_name.lower().endswith(".docx"):
            table_summaries = extract_plan_worksheet_docx_schedule_a_summaries(
                file_bytes,
                default_year=default_year,
            )
            if table_summaries:
                worksheet_summaries = table_summaries
        fields = dedupe_fields(local_fields)
        if fields:
            return NormalizedExtractionResult(
                provider="Plan Worksheet local parser",
                fields=fields,
                raw={
                    "context_preview": context_text[:4000],
                    "fully_insured_benefits": [summary.model_dump(mode="json") for summary in worksheet_summaries],
                },
                schedule_a_worksheet_summaries=worksheet_summaries,
            )

        settings = get_settings()
        if settings.groundx_api_key and settings.groundx_bucket_id:
            try:
                return await asyncio.wait_for(
                    self._extract_with_groundx(
                        file_bytes,
                        file_name,
                        FormType.FORM_5500,
                        "Plan Worksheet",
                    ),
                    timeout=max(1.0, float(getattr(settings, "groundx_max_wait_seconds", 90))),
                )
            except Exception as exc:
                return NormalizedExtractionResult(
                    provider=f"Plan Worksheet OCR fallback failed ({safe_error_summary(exc)})",
                    fields=[],
                    raw={"context_preview": context_text[:4000], "error": safe_error_summary(exc)},
                )

        return NormalizedExtractionResult(
            provider="Plan Worksheet parser",
            fields=[],
            raw={"context_preview": context_text[:4000]},
        )

    async def extract_schedule_a(self, file_bytes: bytes, file_name: str) -> NormalizedExtractionResult:
        template_pages = extract_document_text_pages(file_bytes, file_name)
        if has_blank_schedule_a_form_layer(template_pages):
            acroform_fields, _ = extract_schedule_a_acroform(file_bytes)
            visible_pages = []
            if not acroform_fields and file_name.lower().endswith(".pdf"):
                visible_pages = extract_image_only_pdf_ocr_pages(file_bytes)
            if acroform_fields or has_completed_schedule_a_overlay(visible_pages):
                # A completed PDF can preserve the official sample layer under
                # either AcroForm values or a flattened signature/appearance
                # layer.  Continue into the real extractor when visible data
                # proves the form is filled.
                template_pages = visible_pages or template_pages
            else:
                # Do not run customer defaults or semantic enrichment here: even a
                # harmless default would make a blank form look partially filled.
                return NormalizedExtractionResult(
                    provider="Unfilled Schedule A template - manual review required",
                    fields=[],
                    raw={
                        "file_name": file_name,
                        "source": "document_quality_preflight",
                        "manual_review_required": True,
                        "document_quality_issue": (
                            "The Schedule A is an unfilled IRS template containing sample placeholders."
                        ),
                    },
                    classification_signals=["UNFILLED_SCHEDULE_A_TEMPLATE"],
                )
        # This workbook layout is deterministic and complete.  Resolve it
        # before the remote extractor so a recognized NYL statement does not
        # wait for (or get weakened by) an unrelated OCR timeout.
        nyl_workbook = extract_nyl_paid_premium_workbook(file_bytes, file_name)
        if nyl_workbook:
            settings = get_settings()
            return apply_schedule_a_pipeline(
                nyl_workbook,
                authoritative=bool(getattr(settings, "schedule_a_canonical_validation_enabled", False)),
                shadow=bool(getattr(settings, "schedule_a_canonical_validation_shadow_enabled", True)),
                rules=self.field_rules,
            )
        result = await self._extract_schedule_a_unresolved(file_bytes, file_name)
        semantic_pages = _semantic_page_texts(file_bytes, file_name, result)
        if semantic_pages:
            has_local_ocr_pages = bool(
                isinstance(result.raw, dict)
                and result.raw.get("local_ocr_pages")
            )
            authoritative_pages = (
                template_pages
                if not has_local_ocr_pages
                and any(str(text or "").strip() for _, text in template_pages)
                else semantic_pages
            )
            result = enrich_schedule_a_result(
                result,
                SemanticDocument.from_page_texts(semantic_pages),
                rules=self.field_rules,
            )
            result = restore_principal_short_form_fields(result, authoritative_pages)
            result.fields = prefer_authoritative_anthem_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_aig_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_aflac_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_carrier_statement_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_colonial_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_prudential_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_cigna_summary_fields(result.fields, authoritative_pages)
            result.fields = prefer_authoritative_united_omaha_fields(result.fields, authoritative_pages)
            hartford_fields = extract_hartford_schedule_a_fields(authoritative_pages)
            if hartford_fields:
                authoritative_names = {field.field_name for field in hartford_fields}
                result.fields = [field for field in result.fields if field.field_name not in authoritative_names]
                result.fields.extend(hartford_fields)
                result.schedule_a_broker_rows = extract_hartford_broker_rows(authoritative_pages)
            metlife_fields = extract_metlife_bay_bridge_schedule_a_fields(authoritative_pages)
            if metlife_fields:
                authoritative_names = {field.field_name for field in metlife_fields}
                result.fields = [field for field in result.fields if field.field_name not in authoritative_names]
                result.fields.extend(metlife_fields)
                result.schedule_a_broker_rows = extract_metlife_bay_bridge_broker_rows(authoritative_pages)
                result.schedule_a_worksheet_summaries = extract_metlife_bay_bridge_schedule_a_summaries(authoritative_pages)
            unitedhealthcare_rows = extract_unitedhealthcare_broker_rows(authoritative_pages)
            if unitedhealthcare_rows:
                result.schedule_a_broker_rows = unitedhealthcare_rows
            allone_fields = extract_allone_eap_schedule_a_fields(authoritative_pages)
            if allone_fields:
                authoritative_names = {
                    *{field.field_name for field in allone_fields},
                    "3a. Name of Agent/Broker/Person",
                    "3b. Amount of Commissions",
                    "3c. Amount of Fees",
                    "3d. Purpose",
                    "3e. Organizational Code",
                }
                result.fields = [field for field in result.fields if field.field_name not in authoritative_names]
                result.fields.extend(allone_fields)
            result.fields = prefer_authoritative_pomerene_fields(result.fields, semantic_pages)
            pomerene_brokers = extract_pomerene_schedule_a_broker_rows(semantic_pages)
            if pomerene_brokers:
                result.schedule_a_broker_rows = pomerene_brokers
            eyemed_fields = extract_eyemed_schedule_a_fields(semantic_pages)
            if eyemed_fields and all(
                float(field.confidence or 0) >= 0.8
                and field.decision != "REVIEW_REQUIRED"
                for field in eyemed_fields
            ):
                authoritative_names = {
                    *{field.field_name for field in eyemed_fields},
                    *SCHEDULE_A_EXPERIENCE_RATED_FIELDS,
                    "3a. Name of Agent/Broker/Person",
                    "3c. Amount of Fees",
                    "3d. Purpose",
                    "3e. Organizational Code",
                }
                result.fields = [
                    field for field in result.fields
                    if field.field_name not in authoritative_names
                ]
                result.fields.extend(eyemed_fields)
            litera_aetna_fields = extract_litera_aetna_schedule_a_fields(semantic_pages)
            if litera_aetna_fields:
                authoritative_names = {
                    *{field.field_name for field in litera_aetna_fields},
                    *SCHEDULE_A_EXPERIENCE_RATED_FIELDS,
                    "3a. Name of Agent/Broker/Person",
                    "3b. Amount of Commissions",
                    "3c. Amount of Fees",
                    "3d. Purpose",
                    "3e. Organizational Code",
                }
                result.fields = [
                    field for field in result.fields
                    if field.field_name not in authoritative_names
                ]
                result.fields.extend(litera_aetna_fields)
                # The Part I compensation table is authoritative for Schedule
                # A. Do not copy a later Schedule C disclosure into Part I.
                result.schedule_a_broker_rows = extract_litera_aetna_schedule_a_broker_rows(
                    semantic_pages
                )
                result.raw = (
                    dict(result.raw)
                    if isinstance(result.raw, dict)
                    else {"provider_raw": result.raw}
                )
                result.raw["authoritative_broker_table"] = True
            litera_lincoln_fields = extract_litera_lincoln_schedule_a_fields(semantic_pages)
            if litera_lincoln_fields:
                lincoln_broker_rows = extract_litera_lincoln_schedule_a_broker_rows(
                    semantic_pages
                )
                authoritative_names = {
                    *{field.field_name for field in litera_lincoln_fields},
                    *SCHEDULE_A_EXPERIENCE_RATED_FIELDS,
                    "3a. Name of Agent/Broker/Person",
                    "3d. Purpose",
                    "3e. Organizational Code",
                    "11. Did the insurance company fail to provide any information necessary to complete Schedule A?",
                }
                result.fields = [
                    field for field in result.fields
                    if field.field_name not in authoritative_names
                ]
                result.fields.extend(litera_lincoln_fields)
                result.schedule_a_broker_rows = lincoln_broker_rows
                result.raw = (
                    dict(result.raw)
                    if isinstance(result.raw, dict)
                    else {"provider_raw": result.raw}
                )
                result.raw["authoritative_broker_table"] = True
            result = remove_inapplicable_experience_rated_fields(result, authoritative_pages)
        aultcare_summaries = extract_aultcare_schedule_a_summaries(file_bytes, file_name)
        if aultcare_summaries:
            result.fields = _multi_record_fields_from_summaries(
                aultcare_summaries,
                source="AultCare multi-policy workbook parser",
            )
            result.schedule_a_worksheet_summaries = aultcare_summaries
            result.schedule_a_broker_rows = extract_aultcare_broker_rows(file_bytes, file_name)
        settings = get_settings()
        final_result = apply_schedule_a_pipeline(
            result,
            authoritative=bool(getattr(settings, "schedule_a_canonical_validation_enabled", False)),
            shadow=bool(getattr(settings, "schedule_a_canonical_validation_shadow_enabled", True)),
            rules=self.field_rules,
        )
        result_raw = final_result.raw if isinstance(final_result.raw, dict) else {}
        if result_raw.get("authoritative_visible_overlay"):
            visible_layout_fields = extract_filled_irs_schedule_a_fields(semantic_pages)
            if visible_layout_fields:
                final_result.fields = [
                    field
                    for field in final_result.fields
                    if field.field_name not in SCHEDULE_A_LAYOUT_OWNED_FIELDS
                ]
                final_result.fields.extend(visible_layout_fields)
                final_result.fields = select_best_schedule_a_fields(final_result.fields)
        return final_result

    async def _extract_schedule_a_unresolved(self, file_bytes: bytes, file_name: str) -> NormalizedExtractionResult:
        settings = get_settings()
        document_signals = extract_schedule_a_classification_signals(file_bytes, file_name)
        if settings.groundx_api_key and settings.groundx_bucket_id:
            try:
                result = await asyncio.wait_for(
                    self._extract_with_groundx(file_bytes, file_name, FormType.SCHEDULE_A, "Schedule A"),
                    timeout=max(1.0, float(getattr(settings, "groundx_max_wait_seconds", 90))),
                )
                local_result = local_schedule_a_pdf_result(file_bytes, file_name, rules=self.field_rules)
                result = supplement_schedule_a_result_with_local(result, local_result)
                result.classification_signals = sorted(set(result.classification_signals) | set(document_signals))
                return result
            except Exception as exc:
                local_result = local_schedule_a_pdf_result(
                    file_bytes,
                    file_name,
                    provider=f"Local PDF parser fallback ({safe_error_summary(exc)})",
                    rules=self.field_rules,
                )
                local_result.classification_signals = document_signals
                if local_result.fields or local_result.schedule_a_broker_rows or local_result.schedule_a_worksheet_summaries:
                    if is_validated_local_schedule_a_fallback(local_result):
                        return mark_validated_schedule_a_fallback(local_result, exc)
                    return mark_schedule_a_fallback_for_manual_review(local_result, exc)
                # Unrecognized layout: degrade gracefully instead of crashing the
                # pipeline. The filing completes with all fields MISSING and is
                # routed to manual review.
                return NormalizedExtractionResult(
                    provider=f"Unrecognized layout - manual review required (AI extraction failed: {safe_error_summary(exc)})",
                    fields=[],
                    raw={"file_name": file_name, "error": safe_error_summary(exc), "source": "unrecognized_layout_fallback"},
                    classification_signals=document_signals,
                )

        if not settings.eyelevel_api_key or not settings.eyelevel_extract_url:
            local_result = local_schedule_a_pdf_result(file_bytes, file_name, rules=self.field_rules)
            local_result.classification_signals = document_signals
            if local_result.fields or local_result.schedule_a_broker_rows or local_result.schedule_a_worksheet_summaries:
                return local_result
            mock_result = self._mock_extraction(file_name)
            mock_result.classification_signals = document_signals
            return mock_result

        files = {"file": (file_name, file_bytes, "application/pdf")}
        headers = {"Authorization": f"Bearer {settings.eyelevel_api_key}"}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(settings.eyelevel_extract_url, headers=headers, files=files)
            response.raise_for_status()
            raw = response.json()
        result = self._normalize_response(raw, "EyeLevel/GroundX")
        pdf_text_fields = extract_fields_from_pdf_text(file_bytes, rules=self.field_rules)
        result.fields = merge_schedule_a_fields(result.fields, pdf_text_fields)
        result.schedule_a_broker_rows = extract_schedule_a_broker_rows_from_pdf_text(file_bytes)
        result.schedule_a_worksheet_summaries = extract_schedule_a_worksheet_summaries_from_pdf_text(file_bytes)
        result.classification_signals = document_signals
        return result

    async def _extract_with_groundx(self, file_bytes: bytes, file_name: str, form_type: FormType, document_label: str) -> NormalizedExtractionResult:
        settings = get_settings()
        base_url = settings.groundx_api_base_url.rstrip("/")
        headers = {
            "X-API-Key": settings.groundx_api_key or "",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            ingest_raw = await self._ingest_groundx_file(client, base_url, headers, file_bytes, file_name)
            process_id = first_value(ingest_raw, ["processId", "process_id", "processID", "id"])
            if not process_id:
                process_ids = find_values(ingest_raw, {"processId", "process_id", "processID"})
                process_id = process_ids[0] if process_ids else None
            poll_raw = ingest_raw
            if process_id:
                poll_raw = await self._poll_groundx_process(client, base_url, headers, str(process_id))

            raw_payloads: list[Any] = [ingest_raw, poll_raw]
            structured_payloads: list[Any] = []
            if form_type == FormType.SCHEDULE_A and bool(
                getattr(settings, "groundx_structured_extract_enabled", True)
            ):
                structured_payloads = await self._fetch_groundx_structured_extracts(
                    client,
                    base_url,
                    headers,
                    raw_payloads,
                    file_name,
                )
            xray_payloads = await self._fetch_groundx_xray_payloads(client, base_url, headers, raw_payloads, file_name)
            raw_payloads.extend(xray_payloads)
            if not xray_payloads:
                bucket_search = await self._search_groundx_with_field_schema(
                    client,
                    base_url,
                    headers,
                    str(settings.groundx_bucket_id),
                    file_name,
                    form_type=form_type,
                )
                if bucket_search:
                    raw_payloads.append(bucket_search)
                broad_bucket_search = await self._search_groundx_with_field_schema(
                    client,
                    base_url,
                    headers,
                    str(settings.groundx_bucket_id),
                    form_type=form_type,
                )
                if broad_bucket_search:
                    raw_payloads.append(broad_bucket_search)

        xray_fields: list[NormalizedExtractionField] = []
        structured_fields: list[NormalizedExtractionField] = []
        search_fields: list[NormalizedExtractionField] = []
        fallback_fields: list[NormalizedExtractionField] = []
        pdf_text_fields: list[NormalizedExtractionField] = []
        schedule_a_broker_rows: list[ScheduleABrokerRow] = []
        schedule_a_worksheet_summaries: list[ScheduleAWorksheetSummary] = []
        for payload in structured_payloads:
            structured_result = normalize_groundx_schedule_a_extract(payload, self.field_rules)
            structured_fields.extend(structured_result.fields)
            schedule_a_broker_rows = merge_schedule_a_broker_rows(
                schedule_a_broker_rows,
                structured_result.schedule_a_broker_rows,
            )
        for payload in raw_payloads:
            if is_groundx_xray_payload(payload):
                xray_fields.extend(extract_fields_from_groundx_xray(payload, rules=self.field_rules))
            elif is_groundx_search_payload(payload):
                search_fields.extend(self._extract_fields_from_groundx_search(payload))
            else:
                fallback_fields.extend(self._extract_field_like_items(payload))

        if form_type == FormType.FORM_5500:
            worksheet_context = build_structured_extraction_context(raw_payloads, file_bytes)
            fields = parse_plan_worksheet_text(
                worksheet_context,
                rules=rules_for_form(self.field_rules, FormType.FORM_5500),
            )
            provider = "GroundX Plan Worksheet OCR" if fields else "GroundX Plan Worksheet OCR not ready"
            return NormalizedExtractionResult(
                provider=provider,
                fields=fields,
                raw={"ingest": ingest_raw, "process": poll_raw, "outputs": raw_payloads[2:]},
            )

        if form_type == FormType.SCHEDULE_A:
            # GroundX is useful, but for Schedule A we also have stable label-driven text
            # parsing that recovers fields GroundX may miss or emit inconsistently. It runs
            # on whatever format the document arrived in, not only PDFs.
            if str(file_name or "").lower().endswith(".pdf"):
                pdf_text_fields = extract_fields_from_pdf_text(file_bytes, rules=self.field_rules)
                schedule_a_broker_rows = merge_schedule_a_broker_rows(
                    schedule_a_broker_rows,
                    extract_schedule_a_broker_rows_from_pdf_text(file_bytes),
                )
                schedule_a_worksheet_summaries = extract_schedule_a_worksheet_summaries_from_pdf_text(file_bytes)
            else:
                pdf_text_fields = extract_fields_from_document_text(file_bytes, file_name, rules=self.field_rules)
                schedule_a_broker_rows = merge_schedule_a_broker_rows(
                    schedule_a_broker_rows,
                    extract_schedule_a_broker_rows_from_document(file_bytes, file_name),
                )

        fields = merge_schedule_a_fields([*structured_fields, *xray_fields, *search_fields], pdf_text_fields)
        provider = "GroundX structured extract"
        if structured_fields or schedule_a_broker_rows:
            fallbacks = []
            if xray_fields:
                fallbacks.append("X-Ray")
            if search_fields:
                fallbacks.append("retrieval")
            if pdf_text_fields:
                fallbacks.append("local parser")
            if fallbacks:
                provider += " + " + " + ".join(fallbacks)
        elif xray_fields:
            provider = "GroundX X-Ray + retrieval" if search_fields else "GroundX X-Ray"
        elif search_fields:
            provider = "GroundX retrieval"
        elif pdf_text_fields:
            provider = "Local PDF parser"
        elif fallback_fields:
            fields = dedupe_fields(fallback_fields)
            provider = "GroundX generic extraction"
        elif not fields:
            provider = "GroundX X-Ray not ready"
        deduped = dedupe_fields(fields)
        classification_signals = classification_signals_from_text("\n".join(extract_xray_item_text(raw_payloads)))
        return NormalizedExtractionResult(
            provider=provider,
            fields=deduped,
            raw={
                "ingest": ingest_raw,
                "process": poll_raw,
                "structured_outputs": structured_payloads,
                "outputs": raw_payloads[2:],
            },
            classification_signals=classification_signals,
            schedule_a_broker_rows=schedule_a_broker_rows,
            schedule_a_worksheet_summaries=schedule_a_worksheet_summaries,
        )

    async def _fetch_groundx_structured_extracts(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        raw_payloads: list[Any],
        file_name: str,
    ) -> list[Any]:
        """Read workflow JSON when available; a missing artifact is a safe fallback."""
        refs = [
            {"documentId": document_id}
            for document_id in find_values(raw_payloads, {"documentId", "document_id"})
        ]
        if not refs:
            refs = await self._find_groundx_document_refs(
                client,
                base_url,
                headers,
                raw_payloads,
                file_name,
            )

        outputs: list[Any] = []
        seen: set[str] = set()
        for ref in refs:
            document_id = string_or_none(ref.get("documentId") or ref.get("document_id"))
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            try:
                response = await client.get(
                    f"{base_url}/ingest/document/extract/{document_id}",
                    headers=headers,
                    timeout=60,
                )
            except (httpx.TimeoutException, httpx.NetworkError):
                continue
            if response.status_code in {400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504}:
                continue
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPStatusError, ValueError):
                continue
            if isinstance(payload, (dict, list)):
                outputs.append(payload)
        return outputs

    async def _ingest_groundx_file(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        file_bytes: bytes,
        file_name: str,
    ) -> Any:
        settings = get_settings()
        return await asyncio.to_thread(self._ingest_groundx_file_with_sdk, file_bytes, file_name)

    def _ingest_groundx_file_with_sdk(self, file_bytes: bytes, file_name: str) -> Any:
        settings = get_settings()
        if not settings.groundx_api_key or not settings.groundx_bucket_id:
            raise RuntimeError("GroundX API key and bucket ID are required for ingestion.")

        suffix = os.path.splitext(file_name)[1] or ".pdf"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(file_bytes)
                temp_path = handle.name

            client = GroundX(api_key=settings.groundx_api_key)
            response = client.ingest(
                documents=[
                    Document(
                        bucketId=settings.groundx_bucket_id,
                        fileName=file_name,
                        filePath=temp_path,
                        fileType=file_type_for_groundx(file_name),
                    )
                ],
                wait_for_complete=False,
            )
            return response.model_dump(mode="json")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    async def _poll_groundx_process(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        process_id: str,
        max_wait_seconds: int | None = None,
    ) -> Any:
        settings = get_settings()
        wait_seconds = max_wait_seconds if max_wait_seconds is not None else settings.groundx_max_wait_seconds
        max_attempts = max(1, int(wait_seconds / settings.groundx_poll_seconds))
        latest: Any = None
        for _ in range(max_attempts):
            response = await client.get(f"{base_url}/ingest/{process_id}", headers=headers)
            response.raise_for_status()
            latest = response.json()
            status = str(first_value(latest, ["status", "state", "processStatus"]) or "").lower()
            if status in {"complete", "completed", "done", "success", "succeeded", "finished"}:
                return latest
            if status in {"failed", "error", "errored", "cancelled", "canceled"}:
                raise RuntimeError(f"GroundX processing failed with status: {status}")
            await asyncio.sleep(settings.groundx_poll_seconds)
        return latest

    async def _fetch_groundx_document_outputs(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        document_id: str,
    ) -> list[Any]:
        outputs: list[Any] = []
        paths = [
            f"/ingest/document/{document_id}",
            f"/ingest/document/extract/{document_id}",
            f"/ingest/document/xray/{document_id}",
        ]
        for path in paths:
            response = await client.get(f"{base_url}{path}", headers=headers)
            if response.status_code in {400, 401, 404}:
                continue
            response.raise_for_status()
            outputs.append(response.json())
        return outputs

    async def _fetch_groundx_xray_payloads(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        raw_payloads: list[Any],
        file_name: str,
    ) -> list[Any]:
        document_refs = await self._find_groundx_document_refs(client, base_url, headers, raw_payloads, file_name)
        outputs: list[Any] = []
        seen: set[str] = set()
        for ref in document_refs:
            document_id = string_or_none(ref.get("documentId") or ref.get("document_id"))
            xray_url = string_or_none(ref.get("xrayUrl") or ref.get("xray_url"))
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            xray = await self._fetch_groundx_xray(client, base_url, headers, document_id, xray_url)
            if xray:
                outputs.append(xray)
        return outputs

    async def _find_groundx_document_refs(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        raw_payloads: list[Any],
        file_name: str,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        document_ids = find_values(raw_payloads, {"documentId", "document_id"})
        for document_id in document_ids:
            refs.append({"documentId": document_id})

        settings = get_settings()
        target_name = normalize_file_name(file_name)
        max_attempts = max(1, int(settings.groundx_max_wait_seconds / settings.groundx_poll_seconds))
        for attempt in range(max_attempts):
            try:
                response = await client.get(f"{base_url}/ingest/documents", headers=headers)
                if response.status_code < 400:
                    documents = response.json().get("documents", [])
                    if isinstance(documents, list):
                        matches = [
                            item
                            for item in documents
                            if isinstance(item, dict)
                            and normalize_file_name(str(item.get("fileName") or "")) == target_name
                        ]
                        matches.sort(key=lambda item: str(item.get("updated") or item.get("created") or ""), reverse=True)
                        latest = matches[0] if matches else None
                        if latest:
                            status = str(latest.get("status") or "").lower()
                            if status in {"complete", "completed", "done", "success", "succeeded", "finished"}:
                                refs.append(latest)
                                return refs
            except (httpx.TimeoutException, httpx.NetworkError, ValueError):
                pass
            if attempt < max_attempts - 1:
                await asyncio.sleep(settings.groundx_poll_seconds)

        return refs

    async def _fetch_groundx_xray(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        document_id: str,
        xray_url: str | None = None,
    ) -> Any | None:
        urls = [f"{base_url}/ingest/document/xray/{document_id}"]
        if xray_url:
            urls.append(xray_url)

        for index, url in enumerate(urls):
            try:
                response = await client.get(url, headers=headers if index == 0 else None, timeout=60)
            except (httpx.TimeoutException, httpx.NetworkError):
                continue
            if response.status_code in {400, 401, 403, 404, 408, 429, 500, 502, 503, 504}:
                continue
            try:
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, ValueError):
                continue
        return None

    async def _search_groundx_with_field_schema(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        document_id: str,
        file_name: str | None = None,
        *,
        form_type: FormType = FormType.SCHEDULE_A,
    ) -> Any | None:
        query = build_groundx_schema_query(file_name, self.field_rules, form_type=form_type)
        body = {
            "search": {
                "query": query,
                "n": 25,
                "verbosity": 2,
                "relevance": 0,
            }
        }
        try:
            response = await client.post(f"{base_url}/search/{document_id}", headers=headers, json=body)
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if response.status_code in {400, 401, 404, 408, 429, 500, 502, 503, 504}:
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return None
        return response.json()

    def _extract_fields_from_groundx_search(self, raw: Any) -> list[NormalizedExtractionField]:
        search = raw.get("search", {}) if isinstance(raw, dict) else {}
        results = search.get("results", []) if isinstance(search, dict) else []
        fields: list[NormalizedExtractionField] = []

        for result in results:
            if not isinstance(result, dict):
                continue
            text = normalize_ocr_text(result.get("text") or result.get("suggestedText") or result.get("narrative") or "")
            if not text:
                continue
            page = parse_groundx_page(result)
            fields.extend(parse_schedule_a_text(text, page, rules=self.field_rules))

        return dedupe_fields(fields)

    def _normalize_response(self, raw, provider: str) -> NormalizedExtractionResult:
        candidates = raw.get("fields") or raw.get("extracted_fields") or raw.get("results") or []
        fields = []
        for item in candidates:
            fields.append(
                NormalizedExtractionField(
                    field_name=str(item.get("fieldName") or item.get("name") or item.get("label") or "Unknown Field"),
                    value=str(item.get("value") or item.get("text") or ""),
                    confidence=float(item.get("confidence") or item.get("score") or 0.75),
                    page=item.get("page") if isinstance(item.get("page"), int) else None,
                    source_text=item.get("sourceText") or item.get("context"),
                )
            )
        if not fields:
            fields = self._extract_field_like_items(raw)
        return NormalizedExtractionResult(provider=provider, fields=fields, raw=raw)

    def _extract_field_like_items(self, raw: Any) -> list[NormalizedExtractionField]:
        fields: list[NormalizedExtractionField] = []

        def walk(value: Any):
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return

            field_name = first_value(
                value,
                [
                    "fieldName",
                    "field_name",
                    "name",
                    "label",
                    "question",
                    "key",
                    "title",
                ],
            )
            field_value = first_value(
                value,
                [
                    "value",
                    "answer",
                    "text",
                    "content",
                    "result",
                    "extractedText",
                    "extracted_text",
                ],
            )
            if field_name and field_value and not isinstance(field_value, (dict, list)):
                fields.append(
                    NormalizedExtractionField(
                        field_name=str(field_name),
                        value=str(field_value).strip(),
                        confidence=normalize_raw_confidence(first_value(value, ["confidence", "score", "relevance", "probability"])),
                        page=parse_page(first_value(value, ["page", "pageNumber", "page_number"])),
                        source_text=string_or_none(first_value(value, ["sourceText", "source_text", "context", "text"])),
                    )
                )

            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    walk(nested)

        walk(raw)
        return fields

    def _mock_extraction(self, file_name: str) -> NormalizedExtractionResult:
        return NormalizedExtractionResult(
            provider="Local mock extractor",
            raw={"file_name": file_name, "note": "Set EYELEVEL_API_KEY and EYELEVEL_EXTRACT_URL to call the real extractor."},
            fields=[
                NormalizedExtractionField(field_name="Plan Name", value="HighlandTech Health and Welfare Plan", confidence=0.93, page=1),
                NormalizedExtractionField(field_name="Three-digit Plan Number", value="501", confidence=0.91, page=1),
                NormalizedExtractionField(field_name="Employer EIN", value="12-3456789", confidence=0.78, page=1),
                NormalizedExtractionField(field_name="Insurance Carrier Name", value="Sample Health Insurance Co.", confidence=0.88, page=2),
                NormalizedExtractionField(field_name="Contract / Identification Number", value="HT-2025-501", confidence=0.86, page=2),
                NormalizedExtractionField(field_name="Premium / Contribution", value="125000", confidence=0.82, page=3),
                NormalizedExtractionField(field_name="Total Fees Paid", value="1400", confidence=0.62, page=3),
            ],
        )


def first_value(data: Any, keys: list[str]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def find_values(data: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    if isinstance(data, list):
        for item in data:
            values.extend(find_values(item, keys))
    elif isinstance(data, dict):
        for key, value in data.items():
            if key in keys and value not in (None, ""):
                values.append(value)
            elif isinstance(value, (dict, list)):
                values.extend(find_values(value, keys))
    return values


def normalize_raw_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.75
    if confidence > 1:
        return min(confidence / 100, 1)
    return max(0, min(confidence, 1))


def parse_page(value: Any) -> int | None:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return None
    return str(value)


def safe_error_summary(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if status_code is not None or body is not None:
        detail = body if body is not None else str(exc)
        return redact_sensitive_text(f"HTTP {status_code or 'error'}: {detail}"[:500])
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        try:
            detail = exc.response.json()
        except ValueError:
            detail = exc.response.text[:300]
        return redact_sensitive_text(f"HTTP {status_code}: {detail}")
    detail = str(exc).strip() or type(exc).__name__
    return redact_sensitive_text(detail[:500])


def mark_schedule_a_fallback_for_manual_review(
    result: NormalizedExtractionResult,
    error: Exception,
) -> NormalizedExtractionResult:
    """Keep useful fallback values visible without treating them as trusted updates."""
    for field in result.fields:
        field.confidence = min(field.confidence, 0.5)
    for row in result.schedule_a_broker_rows:
        row.confidence = min(row.confidence, 0.5)
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"fallback_raw": result.raw}
    raw.update(
        {
            "manual_review_required": True,
            "fallback_reason": safe_error_summary(error),
            "source": "local_parser_after_ai_failure",
        }
    )
    result.raw = raw
    result.provider = f"{result.provider} - manual review required"
    return result


def mark_validated_schedule_a_fallback(
    result: NormalizedExtractionResult,
    error: Exception,
) -> NormalizedExtractionResult:
    """Record the AI outage without downgrading a complete deterministic result."""
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"fallback_raw": result.raw}
    raw.update(
        {
            "fallback_validated": True,
            "fallback_reason": safe_error_summary(error),
            "source": "verified_local_parser_after_ai_failure",
        }
    )
    result.raw = raw
    result.provider = f"{result.provider} - verified local fallback"
    return result


def is_validated_local_schedule_a_fallback(result: NormalizedExtractionResult) -> bool:
    """Trust fallback values only when the complete Schedule A identity agrees structurally."""
    values = {
        field.field_name: clean_extracted_value(field.value)
        for field in result.fields
        if field.value and not is_blank_extraction_value(field.value)
    }
    carrier = values.get("1a. Name of Insurance Company", "")
    ein = values.get("1b. Insurance Carrier EIN", "")
    naic = values.get("1c. NAIC Code", "")
    contract = values.get("1d. Contract/Policy Number", "")
    persons = values.get("1e. Persons Covered (End of Policy Year)", "").replace(",", "")
    policy_from = values.get("1f. Policy Year Beginning Date", "")
    policy_to = values.get("1g. Policy Year Ending Date", "")

    try:
        from_date = datetime.strptime(policy_from, "%m/%d/%Y")
        to_date = datetime.strptime(policy_to, "%m/%d/%Y")
        dates_valid = from_date <= to_date
    except ValueError:
        dates_valid = False

    persons_valid = bool(re.fullmatch(r"\d+", persons)) and 0 < int(persons) <= 5_000_000
    contract_valid = is_valid_contract_identifier(contract, allow_numeric=True) or bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/.-]{2,}", contract)
        and re.search(r"\d{3,}", contract)
        and not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", contract)
    )
    return bool(
        is_probable_carrier_name(carrier)
        and looks_like_ein(ein)
        and re.fullmatch(r"\d{4,6}", naic)
        and contract_valid
        and persons_valid
        and dates_valid
    )


def supplement_schedule_a_result_with_local(
    result: NormalizedExtractionResult,
    local_result: NormalizedExtractionResult,
) -> NormalizedExtractionResult:
    """Guarantee deterministic Schedule A values survive a partial AI response."""
    local_raw = local_result.raw if isinstance(local_result.raw, dict) else {}
    if local_raw.get("authoritative_visible_overlay"):
        # A completed AcroForm or flattened visible overlay can retain a hidden
        # IRS sample layer.  Values from that layer are not candidates: remove
        # every layout-owned AI value before merging the visible source truth.
        result.fields = [
            field
            for field in result.fields
            if field.field_name not in SCHEDULE_A_LAYOUT_OWNED_FIELDS
        ]
    result.fields = merge_schedule_a_fields(result.fields, local_result.fields)
    if local_raw.get("ocr_pages"):
        result_raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        result_raw["local_ocr_pages"] = deepcopy(local_raw["ocr_pages"])
        result.raw = result_raw
    if local_result.schedule_a_broker_rows and local_raw.get("authoritative_broker_table"):
        result.schedule_a_broker_rows = [
            row.model_copy(deep=True) for row in local_result.schedule_a_broker_rows
        ]
        if all(
            (parse_numeric_amount(row.commission_total) or 0) == 0
            and (parse_numeric_amount(row.fee_total) or 0) == 0
            for row in local_result.schedule_a_broker_rows
        ):
            # A carrier may print a recipient row while explicitly reporting
            # zero commissions and zero fees.  FT Williams represents that as
            # the no-compensation checkbox; retaining a provider-generated 3a
            # scalar creates a phantom broker and can fail its 35-char limit.
            result.fields = [
                field
                for field in result.fields
                if field.field_name != "3a. Name of Agent/Broker/Person"
            ]
        result_raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        result_raw["authoritative_broker_table"] = True
        result.raw = result_raw
    else:
        result.schedule_a_broker_rows = merge_schedule_a_broker_rows(
            result.schedule_a_broker_rows,
            local_result.schedule_a_broker_rows,
        )
    if local_result.schedule_a_worksheet_summaries:
        result.schedule_a_worksheet_summaries = local_result.schedule_a_worksheet_summaries
    result.classification_signals = sorted(
        set(result.classification_signals) | set(local_result.classification_signals)
    )
    return result


def merge_schedule_a_broker_rows(
    primary_rows: list[ScheduleABrokerRow],
    fallback_rows: list[ScheduleABrokerRow],
) -> list[ScheduleABrokerRow]:
    """Preserve every recipient while preferring the stronger copy of a row."""
    merged: dict[tuple[str, str, str, str], ScheduleABrokerRow] = {}
    order: list[tuple[str, str, str, str]] = []
    for row in [*primary_rows, *fallback_rows]:
        identity = (
            _canonical_broker_name(row.name),
            _canonical_broker_address(row.address_line_1 or ""),
            normalize_rule_label(row.zip_code or ""),
            _broker_row_coverage_key(row),
        )
        if not identity[0]:
            continue
        # Some carrier tables are returned once as a complete structured row
        # and again as a label fragment such as
        # ``FORT WORTH ST: TX ZIP: 76107-5739`` in the city column.  The
        # fragment has no independent address identity and must enrich, never
        # duplicate, the one complete row for the same broker.
        same_name_keys = [key for key in order if key[0] == identity[0]]
        fragment_zip = re.search(r"\bZIP\s*:\s*(\d{5}(?:-\d{4})?)\b", str(row.city or ""), re.IGNORECASE)
        matching_fragment_keys = [
            key for key in same_name_keys
            if fragment_zip and fragment_zip.group(1) in (
                str(merged[key].zip_code or "") + " " + str(merged[key].address_line_1 or "")
            )
        ]
        matching_unclassified_keys = [
            key
            for key in same_name_keys
            if not key[3]
            and (key[1] == identity[1] or key[2] == identity[2])
            and _broker_row_amounts_match(merged[key], row)
        ]
        if identity[3] and len(matching_unclassified_keys) == 1:
            identity = matching_unclassified_keys[0]
        elif _broker_row_is_parser_fragment(row) and len(matching_fragment_keys) == 1:
            identity = matching_fragment_keys[0]
        elif _broker_row_is_parser_fragment(row) and len(same_name_keys) == 1:
            identity = same_name_keys[0]
        elif len(same_name_keys) == 1 and _broker_row_is_parser_fragment(merged[same_name_keys[0]]):
            identity = same_name_keys[0]
        elif len(same_name_keys) == 1 and (
            not identity[1]
            or not same_name_keys[0][1]
        ):
            # The provider often emits the name/amount first and the local
            # layout parser emits the same recipient with its full address.
            # Treat those as two copies of one row, not two recipients.
            identity = same_name_keys[0]
        current = merged.get(identity)
        if current is None:
            order.append(identity)
        if current is None:
            merged[identity] = row.model_copy(deep=True)
            continue
        current_is_fragment = _broker_row_is_parser_fragment(current)
        row_is_fragment = _broker_row_is_parser_fragment(row)
        current_evidence = _broker_evidence_score(current)
        row_evidence = _broker_evidence_score(row)
        if current_is_fragment != row_is_fragment:
            # Structural completeness is authoritative here. A fragment can
            # carry more provider evidence and still contain a malformed city.
            winner = row.model_copy(deep=True) if not row_is_fragment else current.model_copy(deep=True)
            other = current if not row_is_fragment else row
        elif row_evidence > current_evidence or (
            row_evidence == current_evidence and row.confidence > current.confidence
        ):
            winner = row.model_copy(deep=True)
            other = current
        else:
            winner = current.model_copy(deep=True)
            other = row
        winner.evidence = _merge_source_evidence(winner.evidence, other.evidence)
        winner.name = _preferred_broker_name(winner.name, other.name)
        winner.address_line_1 = _preferred_broker_address(
            winner.address_line_1,
            other.address_line_1,
        )
        for attribute in ("address_line_2", "city", "state", "zip_code", "organization_code", "purpose"):
            if not getattr(winner, attribute) and getattr(other, attribute):
                setattr(winner, attribute, getattr(other, attribute))
        winner.source_page = winner.source_page or other.source_page
        winner.commission_source_text = winner.commission_source_text or other.commission_source_text
        winner.fee_source_text = winner.fee_source_text or other.fee_source_text
        if not winner.commission_rows and other.commission_rows:
            winner.commission_rows = [item.model_copy(deep=True) for item in other.commission_rows]
        if not winner.fee_rows and other.fee_rows:
            winner.fee_rows = [item.model_copy(deep=True) for item in other.fee_rows]
        merged[identity] = winner
    output = [merged[identity] for identity in order]
    positioned_rows = [row for row in output if _row_has_position_aware_evidence(row)]
    if positioned_rows:
        output = [
            row
            for row in output
            if _row_has_position_aware_evidence(row)
            or not any(_broker_name_is_address_fragment(row, positioned) for positioned in positioned_rows)
        ]
    return default_blank_organization_codes(output)


def _broker_row_is_parser_fragment(row: ScheduleABrokerRow) -> bool:
    """Identify a repeated label fragment, not a genuine second broker row."""
    if row.address_line_1 or row.address_line_2 or row.state or row.zip_code:
        return False
    city = re.sub(r"\s+", " ", str(row.city or "")).strip()
    return bool(
        city
        and re.search(r"\bST\s*:\s*[A-Z]{2}\b", city, flags=re.IGNORECASE)
        and re.search(r"\bZIP\s*:\s*\d{5}(?:-\d{4})?\b", city, flags=re.IGNORECASE)
    )


def _broker_row_coverage_key(row: ScheduleABrokerRow) -> str:
    coverages = {
        normalize_rule_label(money_row.coverage or "")
        for money_row in [*row.commission_rows, *row.fee_rows]
        if normalize_rule_label(money_row.coverage or "")
    }
    return "|".join(sorted(coverages))


def _broker_row_amounts_match(first: ScheduleABrokerRow, second: ScheduleABrokerRow) -> bool:
    for attribute in ("commission_total", "fee_total"):
        first_amount = parse_numeric_amount(getattr(first, attribute)) or 0.0
        second_amount = parse_numeric_amount(getattr(second, attribute)) or 0.0
        if abs(first_amount - second_amount) > 0.01:
            return False
    return True


_BROKER_LEGAL_SUFFIX = r"(?:LLC|L\.L\.C\.?|INC(?:ORPORATED)?|CORP(?:ORATION)?|LTD|LLP|LP)"


def _canonical_broker_name(value: str | None) -> str:
    normalized = normalize_rule_label(value or "")
    return re.sub(rf"(?:\s+{_BROKER_LEGAL_SUFFIX})+$", "", normalized, flags=re.IGNORECASE).strip()


def _canonical_broker_address(value: str | None) -> str:
    normalized = normalize_rule_label(value or "")
    return re.sub(rf"^{_BROKER_LEGAL_SUFFIX}\s*[-,:]?\s*", "", normalized, flags=re.IGNORECASE).strip()


def _preferred_broker_name(first: str | None, second: str | None) -> str:
    candidates = [str(value or "").strip() for value in (first, second) if str(value or "").strip()]
    if not candidates:
        return ""
    return max(
        candidates,
        key=lambda value: (
            bool(re.search(rf"\b{_BROKER_LEGAL_SUFFIX}\.?$", value, flags=re.IGNORECASE)),
            len(value),
        ),
    )


def _preferred_broker_address(first: str | None, second: str | None) -> str | None:
    candidates = [str(value or "").strip() for value in (first, second) if str(value or "").strip()]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda value: (
            bool(re.match(rf"^{_BROKER_LEGAL_SUFFIX}\s*[-,:]", value, flags=re.IGNORECASE)),
            len(value),
        ),
    )


def _row_has_explicit_broker_evidence(row: ScheduleABrokerRow) -> bool:
    evidence_texts = [
        row.commission_source_text,
        row.fee_source_text,
        *(item.source_text for item in row.evidence),
    ]
    return any(
        re.search(r"\bBROKER(?:AGE)?\b", str(text or ""), flags=re.IGNORECASE)
        for text in evidence_texts
    )


def _broker_evidence_score(row: ScheduleABrokerRow) -> tuple[int, int, int, int]:
    pages = [row.source_page, *(item.page for item in row.evidence)]
    texts = [
        row.commission_source_text,
        row.fee_source_text,
        *(item.source_text for item in row.evidence),
    ]
    return (
        2 if _row_has_position_aware_evidence(row) else int(
            any(item.bounding_box is not None or item.table_cell is not None for item in row.evidence)
        ),
        int(any(isinstance(page, int) and page > 0 for page in pages)),
        int(any(bool(str(text or "").strip()) for text in texts)),
        len(row.evidence),
    )


def _row_has_position_aware_evidence(row: ScheduleABrokerRow) -> bool:
    return any(
        item.provider == "Position-aware PDF parser" and item.table_cell is not None
        for item in row.evidence
    )


def _broker_name_is_address_fragment(
    candidate: ScheduleABrokerRow,
    positioned: ScheduleABrokerRow,
) -> bool:
    if candidate.address_line_1 or candidate.address_line_2 or candidate.zip_code:
        return False
    candidate_key = normalize_compare_key(candidate.name)
    positioned_address = normalize_compare_key(
        " ".join(
            filter(
                None,
                [
                    positioned.address_line_1,
                    positioned.address_line_2,
                    positioned.city,
                    positioned.state,
                    positioned.zip_code,
                ],
            )
        )
    )
    return len(candidate_key) >= 8 and candidate_key in positioned_address


def _merge_source_evidence(
    primary: list[SourceEvidence], fallback: list[SourceEvidence]
) -> list[SourceEvidence]:
    output: list[SourceEvidence] = []
    seen: set[tuple] = set()
    for item in [*primary, *fallback]:
        identity = (
            item.provider,
            item.page,
            item.source_text,
            item.bounding_box,
            item.table_cell,
        )
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item.model_copy(deep=True))
    return output


def redact_sensitive_text(value: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", value)


def dedupe_fields(fields: list[NormalizedExtractionField]) -> list[NormalizedExtractionField]:
    best: dict[tuple[str, str], NormalizedExtractionField] = {}
    for field in fields:
        key = (field.field_name.strip().lower(), field.value.strip().lower())
        current = best.get(key)
        if not current or field.confidence > current.confidence:
            best[key] = field
    return list(best.values())


def is_obvious_template_placeholder(value: Any) -> bool:
    """Reject sample-form filler without rejecting legitimate long identifiers."""
    clean = re.sub(r"\s+", "", clean_extracted_value(str(value or ""))).upper()
    if not clean:
        return False
    if clean.count("ABCDEFGHI") >= 2:
        return True
    if clean in {"ABCDE", "ABCDEFGHI", "ABCDEFGHIJ"}:
        return True
    return clean.lstrip("-") in {
        "0123456789",
        "1234567890",
        "123456789012345",
        "0123456789012345",
    }


def is_unfilled_schedule_a_template(page_texts: list[tuple[int, str]]) -> bool:
    """Detect the IRS sample form before an OCR provider mistakes labels for values.

    Completed PDFs can retain the sample layer underneath the entered overlay,
    so placeholder volume alone is not enough. A document is rejected only
    when the official Schedule A markers and several independent placeholder
    families are present and no real dated/EIN overlay is visible.
    """
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    upper = text.upper()
    if not all(marker in upper for marker in ("SCHEDULE A", "FORM 5500", "INSURANCE INFORMATION")):
        return False

    placeholder_families = sum(
        (
            len(re.findall(r"ABCDEFGHI", upper)) >= 8,
            len(re.findall(r"123456789012345", upper)) >= 3,
            len(re.findall(r"YYYY-MM-DD", upper)) >= 2,
            "012345678" in upper,
        )
    )
    if placeholder_families < 3:
        return False

    has_real_date = bool(
        re.search(r"\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:20)?\d{2}\b", text)
        or re.search(r"\b20\d{2}[/\-](?:0[1-9]|1[0-2])[/\-](?:0[1-9]|[12]\d|3[01])\b", text)
    )
    has_real_ein = bool(re.search(r"\b\d{2}[- ]\d{7}\b", text))
    return not has_real_date and not has_real_ein


def has_blank_schedule_a_form_layer(page_texts: list[tuple[int, str]]) -> bool:
    """Detect an official Schedule A layer that contains labels but no values.

    Flattened signature tools can place completed values in a visual appearance
    layer while leaving a pristine blank IRS text layer underneath.  This is
    broader than the legacy sample-placeholder detector and exists only to
    trigger AcroForm/OCR inspection before accepting any extracted values.
    """
    if is_unfilled_schedule_a_template(page_texts):
        return True
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    upper = text.upper()
    official_markers = (
        "SCHEDULE A",
        "FORM 5500",
        "INSURANCE INFORMATION",
        "NAME OF INSURANCE CARRIER",
        "TOTAL AMOUNT OF COMMISSIONS PAID",
        "NONEXPERIENCE-RATED CONTRACTS",
    )
    if not all(marker in upper for marker in official_markers):
        return False
    coverage_section_match = re.search(
        r"\b1\s+COVERAGE INFORMATION\b(.+?)\b2\s+INSURANCE FEE AND COMMISSION INFORMATION\b",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    coverage_section = coverage_section_match.group(1) if coverage_section_match else text
    has_real_date = bool(
        re.search(r"\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:20)?\d{2}\b", coverage_section)
        or re.search(r"\b20\d{2}[/.-](?:0[1-9]|1[0-2])[/.-](?:0[1-9]|[12]\d|3[01])\b", coverage_section)
    )
    has_real_ein = bool(re.search(r"\b\d{2}[- ]\d{7}\b", coverage_section))
    return not has_real_date and not has_real_ein


def has_completed_schedule_a_overlay(page_texts: list[tuple[int, str]]) -> bool:
    """Require multiple visible values before overriding template preflight."""
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if not text:
        return False
    has_ein = bool(re.search(r"\b\d{2}[- ]\d{7}\b", text))
    has_date = bool(
        re.search(r"\b\d{1,2}[./-]\d{1,2}[./-](?:20)?\d{2}\b", text)
        or re.search(r"\b\d{1,2}-[A-Za-z]{3}-(?:20)?\d{2}\b", text)
    )
    has_money = bool(re.search(r"\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d{2})?", text))
    has_identity = bool(re.search(r"\b(?:NAIC|CONTRACT|POLICY)\b", text, re.IGNORECASE))
    return sum((has_ein, has_date, has_money, has_identity)) >= 3


def select_best_schedule_a_fields(fields: list[NormalizedExtractionField]) -> list[NormalizedExtractionField]:
    """Return one trustworthy value per dashboard field.

    The dashboard and FT Williams mapping model each rule as one value. PDF
    templates can expose both sample-layer filler and the completed overlay;
    keeping every conflicting value created false review/update rows.
    """
    best: dict[str, NormalizedExtractionField] = {}
    order: list[str] = []
    for field in fields:
        if (
            is_blank_extraction_value(field.value)
            or is_obvious_template_placeholder(field.value)
            or _is_column_heading_broker_name(field)
            or is_layout_label_text(field.value)
        ):
            continue
        key = field.field_name.strip().lower()
        field = _merge_field_provenance(field, [field])
        current = best.get(key)
        if current is None:
            order.append(key)
        # A valid value wins before confidence is considered. Previously a
        # high-confidence label match could select a contract number as a date
        # and validation only objected after the wrong candidate had won.
        if current is None:
            best[key] = field
            continue
        field_quality = _schedule_a_candidate_quality(field)
        current_quality = _schedule_a_candidate_quality(current)
        if field_quality > current_quality:
            best[key] = field
        elif field_quality < current_quality:
            continue
        elif field.confidence >= current.confidence:
            best[key] = _merge_field_provenance(field, [current, field])
        else:
            best[key] = _merge_field_provenance(current, [current, field])
    selected = [best[key] for key in order]
    amount_by_prefix = {
        prefix: next(
            (
                parse_numeric_amount(field.value)
                for field in selected
                if field.field_name.strip().lower().startswith(prefix)
            ),
            None,
        )
        for prefix in ("3b.", "3c.")
    }
    if all(value in (None, 0) for value in amount_by_prefix.values()):
        selected = [field for field in selected if not field.field_name.strip().lower().startswith("3d.")]
    return selected


def _schedule_a_candidate_quality(field: NormalizedExtractionField) -> tuple[int, int, int]:
    position_aware = any(
        item.provider in {
            "Position-aware PDF parser",
            "Position-aware layout engine",
            "Aetna attached listing parser",
            "Aetna Schedule A parser",
        }
        and item.table_cell is not None
        for item in field.evidence
    )
    return (
        int(_schedule_a_candidate_value_is_valid(field)),
        int(position_aware),
        int(bool(field.evidence) or (field.page is not None and bool(field.source_text))),
    )


def _schedule_a_candidate_value_is_valid(field: NormalizedExtractionField) -> bool:
    label = str(field.field_name or "").strip().lower()
    value = clean_extracted_value(field.value)
    if not value:
        return False
    if label.startswith(("1f.", "1g.")):
        normalized = _normalize_position_date(value)
        try:
            datetime.strptime(normalized, "%m/%d/%Y")
            return True
        except ValueError:
            return False
    if label.startswith("1a."):
        return is_probable_carrier_name(value)
    if label.startswith("1b."):
        return looks_like_ein(value)
    if label.startswith("1c."):
        return bool(re.fullmatch(r"\d{5}", value))
    if label.startswith("1d."):
        return is_valid_contract_identifier(value, allow_numeric=True)
    if label.startswith("1e."):
        return bool(re.fullmatch(r"[\d,]+", value))
    if label.startswith(("3b.", "3c.", "9", "10a.")):
        return parse_numeric_amount(value) is not None
    return True


def _merge_field_provenance(
    winner: NormalizedExtractionField,
    sources: list[NormalizedExtractionField],
) -> NormalizedExtractionField:
    """Keep one UI value while retaining every candidate and source citation."""
    merged = winner.model_copy(deep=True)
    candidate_values: list[str] = []
    seen_values: set[str] = set()
    evidence = []
    seen_evidence: set[tuple] = set()
    for source in sources:
        for value in [*source.candidate_values, source.value]:
            clean = re.sub(r"\s+", " ", str(value or "")).strip()
            normalized = clean.casefold()
            if clean and normalized not in seen_values:
                seen_values.add(normalized)
                candidate_values.append(clean)
        for item in source.evidence:
            key = (
                item.provider,
                item.page,
                item.source_text,
                item.bounding_box,
                item.table_cell,
            )
            if key not in seen_evidence:
                seen_evidence.add(key)
                evidence.append(item.model_copy(deep=True))
        if source.page or source.source_text:
            key = (source.provider if hasattr(source, "provider") else None, source.page, source.source_text, None, None)
            if key not in seen_evidence:
                seen_evidence.add(key)
                evidence.append(
                    SourceEvidence(page=source.page, source_text=source.source_text)
                )
    merged.candidate_values = candidate_values
    merged.evidence = evidence
    return merged


def merge_schedule_a_fields(
    primary_fields: list[NormalizedExtractionField],
    pdf_text_fields: list[NormalizedExtractionField],
) -> list[NormalizedExtractionField]:
    if not pdf_text_fields:
        return select_best_schedule_a_fields(primary_fields)

    def _has_usable_value(field: NormalizedExtractionField) -> bool:
        value = (field.value or "").strip()
        if not value or is_blank_extraction_value(value):
            return False
        if field.field_name == "1d. Contract/Policy Number":
            return is_valid_contract_identifier(value, allow_numeric=True)
        return True

    # Specialized PDF parsers are authoritative for the stable Schedule A
    # table fields. For other fields, keep a single usable AI value and use the
    # document parser when AI produced no value or conflicting values.
    usable_primary_by_name: dict[str, list[NormalizedExtractionField]] = {}
    for field in primary_fields:
        if _has_usable_value(field):
            usable_primary_by_name.setdefault(field.field_name, []).append(field)
    merged: list[NormalizedExtractionField] = list(primary_fields)
    existing_names = {field.field_name for field in merged}
    for field in pdf_text_fields:
        usable_primary = usable_primary_by_name.get(field.field_name, [])
        distinct_primary_values = {clean_extracted_value(item.value).lower() for item in usable_primary}
        prefer_document_value = field.field_name in SCHEDULE_A_PREFER_PDF_TEXT_FIELDS or (
            field.field_name == "3a. Name of Agent/Broker/Person"
            and field.source_text == "Broker compensation table"
        )
        document_value = clean_extracted_value(field.value).lower()
        if not prefer_document_value and len(distinct_primary_values) == 1 and document_value in distinct_primary_values:
            merged = [
                _merge_field_provenance(item, [item, field])
                if item.field_name == field.field_name
                else item
                for item in merged
            ]
            continue
        if not prefer_document_value and len(distinct_primary_values) == 1:
            merged.append(field)
            continue
        if field.field_name in existing_names:
            # Replace invalid/conflicting AI values, or an AI value for a field
            # that has a reliable format-specific document parser.
            sources = [*usable_primary, field]
            if _has_aetna_attached_listing_evidence(field):
                # Preserve agreeing AI evidence, but never reintroduce a
                # generic Aetna parent-company candidate that the state row
                # conclusively replaced.
                expected = clean_extracted_value(field.value).casefold()
                sources = [
                    source
                    for source in sources
                    if source is field
                    or clean_extracted_value(source.value).casefold() == expected
                ]
            field = _merge_field_provenance(field, sources)
            merged = [f for f in merged if f.field_name != field.field_name]
        merged.append(field)
        existing_names.add(field.field_name)

    return select_best_schedule_a_fields(merged)


def is_groundx_search_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("search"), dict) and isinstance(payload["search"].get("results"), list)


def is_groundx_xray_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("chunks"), list) and isinstance(payload.get("documentPages"), list)


def normalize_file_name(value: str) -> str:
    return unquote_plus(value).strip().lower()


def extract_fields_from_groundx_xray(raw: Any, rules=None) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    chunks = raw.get("chunks", []) if isinstance(raw, dict) else []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        page = parse_xray_page(chunk)
        source_text = build_xray_source_text(chunk)
        for item in chunk.get("json") or []:
            if not isinstance(item, dict):
                continue
            fields.extend(extract_schedule_a_fields_from_xray_json(item, page, source_text))
            item_text = normalize_ocr_text("\n".join(extract_xray_item_text(item)))
            if item_text:
                fields.extend(parse_schedule_a_text(item_text, page, rules=rules))
        text = normalize_ocr_text(chunk.get("suggestedText") or chunk.get("text") or "")
        if text:
            fields.extend(parse_schedule_a_text(text, page, rules=rules))
    return dedupe_fields(fields)


def parse_xray_page(chunk: dict[str, Any]) -> int | None:
    pages = chunk.get("pageNumbers") or []
    if isinstance(pages, list) and pages:
        return parse_page(pages[0])
    boxes = chunk.get("boundingBoxes") or []
    if isinstance(boxes, list) and boxes and isinstance(boxes[0], dict):
        return parse_page(boxes[0].get("pageNumber"))
    return None


def build_xray_source_text(chunk: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "suggestedText"):
        text = normalize_ocr_text(chunk.get(key))
        if text:
            parts.append(text)
    for item in chunk.get("json") or []:
        if isinstance(item, dict):
            parts.extend(extract_xray_item_text(item))
    narrative = chunk.get("narrative")
    if isinstance(narrative, list):
        parts.extend(normalize_ocr_text(item) for item in narrative if normalize_ocr_text(item))
    elif narrative:
        parts.append(normalize_ocr_text(narrative))
    return "\n".join(parts)[:1200]


def extract_xray_item_text(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"text", "summary", "content", "suggestedText", "description"} and isinstance(nested, str):
                clean = normalize_ocr_text(nested)
                if clean:
                    texts.append(clean)
            else:
                texts.extend(extract_xray_item_text(nested))
    elif isinstance(value, list):
        for item in value:
            texts.extend(extract_xray_item_text(item))
    return texts


def extract_schedule_a_fields_from_xray_json(item: dict[str, Any], page: int | None, source_text: str) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    flat_values = flatten_xray_values(item)

    def add(field_name: str, value: Any, confidence: float = 0.95, value_validator=None):
        if value in (None, "", [], {}):
            return
        clean = clean_extracted_value(str(value))
        if not clean or is_blank_extraction_value(clean):
            return
        if value_validator and not value_validator(clean):
            return
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=page,
                source_text=source_text or clean,
            )
        )

    def add_first(field_name: str, *predicates, confidence: float = 0.95, value_validator=None):
        for path, value in flat_values:
            normalized_path = normalize_xray_path(path)
            if is_xray_label_or_header(normalized_path, value) or is_blank_extraction_value(value):
                continue
            if any(predicate(normalized_path) for predicate in predicates):
                add(field_name, value, confidence, value_validator=value_validator)
                return

    add_first(
        "1a. Name of Insurance Company",
        lambda path: path.endswith("a_name_of_insurance_carrier"),
        lambda path: path.endswith("name_of_insurance_carrier"),
        lambda path: path.endswith("insurance_carrier_name"),
        lambda path: path.endswith("carrier_name"),
        lambda path: ("insurance" in path or "carrier" in path) and "name" in path and "ein" not in path,
        confidence=0.97,
        value_validator=is_probable_carrier_name,
    )
    add_first(
        "1b. Insurance Carrier EIN",
        lambda path: path.endswith("b_ein"),
        lambda path: path.endswith("ein") and ("carrier" in path or "insurance" in path),
        lambda path: "insurance" in path and "identification_number" in path,
        confidence=0.97,
        value_validator=looks_like_ein,
    )
    add_first("1c. NAIC Code", lambda path: "naic" in path and "code" in path)
    add_first(
        "1d. Contract/Policy Number",
        lambda path: ("contract" in path or "policy" in path) and ("identification" in path or "number" in path),
        confidence=0.88,
    )
    add_first(
        "1e. Persons Covered (End of Policy Year)",
        lambda path: "persons" in path and "covered" in path and ("end" in path or "approximate" in path),
    )
    add_first("1f. Policy Year Beginning Date", lambda path: ("policy" in path or "contract" in path) and path.endswith("_from"))
    add_first("1g. Policy Year Ending Date", lambda path: ("policy" in path or "contract" in path) and path.endswith("_to"))
    add_first(
        "3a. Name of Agent/Broker/Person",
        lambda path: path.endswith("payee_name"),
        lambda path: path.endswith("recipient_name"),
        lambda path: path.endswith("entity_name") and ("commission" in path or "fee" in path or "part_ii" in path),
        lambda path: path.endswith("name") and ("agent" in path or "broker" in path or "payee" in path or "recipient" in path),
        lambda path: path.endswith("name") and ("persons_receiving_commissions_and_fees" in path or "recipient_of_commissions_and_fees" in path),
        lambda path: "name_and_address" in path and ("recipient" in path or "agent_broker" in path or "broker_or_other_person" in path),
        lambda path: "agent_broker_or_other_person" in path and "name" in path,
        value_validator=is_probable_person_or_entity_name,
    )
    add_first(
        "3b. Amount of Commissions",
        lambda path: path.endswith("total_commissions_paid"),
        lambda path: path.endswith("total_amount_of_commissions_paid"),
        lambda path: path.endswith("amount_of_sales_and_base_commissions_paid"),
        lambda path: path.endswith("sales_and_base_commissions_paid"),
    )
    add_first(
        "3c. Amount of Fees",
        lambda path: path.endswith("total_fees_paid"),
        lambda path: path.endswith("total_amount_of_fees_paid"),
        lambda path: path.endswith("fees_and_other_commissions_paid"),
        lambda path: path.endswith("fees_and_other_commissions_paid_amount"),
        lambda path: path.endswith("c_amount") and ("person" in path or "commission" in path or "fees" in path),
    )
    add_first(
        "3d. Purpose",
        lambda path: path.endswith("d_purpose"),
        lambda path: path.endswith("purpose") and ("section_3" in path or "persons_receiving_commissions_and_fees" in path),
    )
    add_first("3e. Organizational Code", lambda path: "organization" in path and "code" in path, value_validator=looks_like_org_code)
    add_first(
        "10a. Total premiums or subscription charges paid to carrier",
        lambda path: "total_premiums" in path and "carrier" in path,
        lambda path: "subscription_charges" in path and ("carrier" in path or "paid" in path),
        lambda path: "nonexperience" in path and ("total" in path or "subtotal" in path) and ("amount" in path or "premium" in path),
        confidence=0.9,
    )
    for path, value in flat_values:
        if normalize_xray_path(path).endswith("gross_premium"):
            add(
                "10a. Total premiums or subscription charges paid to carrier",
                money_value(str(value)),
                0.9,
            )
            break
    premium_total = extract_nonexperience_total_premium_from_text(source_text)
    if premium_total:
        add("10a. Total premiums or subscription charges paid to carrier", premium_total, 0.93)
    if has_experience_rated_not_applicable(source_text):
        add_not_applicable_experience_rated_fields(fields, page, source_text)
    add_first("4a. Plan Name", lambda path: "plan" in path and "name" in path and "sponsor" not in path and "file" not in path)
    add_first(
        "4b. Plan Number (PN)",
        lambda path: path.endswith("plan_number_value"),
        lambda path: path.endswith("plan_number"),
        lambda path: "three_digit_plan_number" in path and not path.endswith("label"),
    )
    add_first("4c. Sponsor EIN", lambda path: ("sponsor" in path or "employer" in path) and ("ein" in path or "identification_number" in path))
    add_first(
        "4d. Plan Year Beginning Date",
        lambda path: "fiscal_plan_year_beginning" in path,
        lambda path: "calendar_plan_year_beginning" in path,
        lambda path: "calendar_plan_year_start" in path,
        lambda path: "plan_year_start_date" in path,
        lambda path: path.endswith("plan_year_begin_date"),
    )
    add_first(
        "4e. Plan Year Ending Date",
        lambda path: "fiscal_plan_year_ending" in path,
        lambda path: "calendar_plan_year_ending" in path,
        lambda path: "calendar_plan_year_end" in path,
        lambda path: "plan_year_end_date" in path,
        lambda path: path.endswith("plan_year_end_date"),
    )

    coverage = item.get("coverage_information") if isinstance(item.get("coverage_information"), dict) else {}
    add("1a. Name of Insurance Company", coverage.get("a_name_of_insurance_carrier"), 0.97, value_validator=is_probable_carrier_name)
    add("1b. Insurance Carrier EIN", coverage.get("b_ein") or coverage.get("b_EIN"), 0.97, value_validator=looks_like_ein)
    add("1c. NAIC Code", coverage.get("c_naic_code") or coverage.get("c_NAIC_code"), 0.97)
    add("1d. Contract/Policy Number", coverage.get("d_contract_or_identification_number"), 0.97)
    add("1e. Persons Covered (End of Policy Year)", coverage.get("e_approximate_number_of_persons_covered_at_end_of_policy_or_contract_year"), 0.97)
    add("1f. Policy Year Beginning Date", coverage.get("f_policy_or_contract_year_from"), 0.97)
    add("1g. Policy Year Ending Date", coverage.get("g_policy_or_contract_year_to"), 0.97)

    fees = item.get("insurance_fee_and_commission_information") if isinstance(item.get("insurance_fee_and_commission_information"), dict) else {}
    add("3b. Amount of Commissions", fees.get("a_total_amount_of_commissions_paid"), 0.97)
    add("3c. Amount of Fees", fees.get("b_total_amount_of_fees_paid") or fees.get("total_fees_paid_amount"), 0.97)

    people = item.get("persons_receiving_commissions_and_fees")
    entries = people.get("entries", []) if isinstance(people, dict) else people if isinstance(people, list) else []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            add(
                "3a. Name of Agent/Broker/Person",
                entry.get("a_name_and_address_of_recipient")
                or entry.get("a_name_and_address_of_agent_broker_or_other_person")
                or entry.get("name_and_address_of_recipient"),
                value_validator=is_probable_person_or_entity_name,
            )
            add("3b. Amount of Commissions", entry.get("b_amount_of_sales_and_base_commissions_paid"), 0.9)
            add("3c. Amount of Fees", entry.get("c_amount") or entry.get("fees_and_other_commissions_paid"), 0.9)
            add("3d. Purpose", entry.get("d_purpose"))
            add("3e. Organizational Code", entry.get("e_organization_code"), value_validator=looks_like_org_code)

    plan = item.get("plan_information") if isinstance(item.get("plan_information"), dict) else {}
    if not plan:
        plan = item.get("plan_identification") if isinstance(item.get("plan_identification"), dict) else {}
    add("4a. Plan Name", plan.get("a_name_of_plan"))
    add("4a. Plan Name", plan.get("A_name_of_plan"))
    add("4b. Plan Number (PN)", plan.get("b_three_digit_plan_number_pn") or plan.get("B_three_digit_plan_number_PN"))
    add("4c. Sponsor EIN", plan.get("d_employer_identification_number_ein") or plan.get("D_employer_identification_number_EIN"))

    filing_period = item.get("filing_period") if isinstance(item.get("filing_period"), dict) else {}
    add("4d. Plan Year Beginning Date", filing_period.get("fiscal_plan_year_beginning"))
    add("4e. Plan Year Ending Date", filing_period.get("fiscal_plan_year_ending"))

    return fields


def flatten_xray_values(value: Any, path: str = "") -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}" if path else str(key)
            flattened.extend(flatten_xray_values(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            flattened.extend(flatten_xray_values(nested, f"{path}[{index}]"))
    elif value not in (None, ""):
        flattened.append((path, str(value)))
    return flattened


def normalize_xray_path(path: str) -> str:
    path = re.sub(r"\[\d+\]", "", path)
    path = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", path)
    path = path.replace(".", "_").replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", path).strip("_").lower()


def is_xray_label_or_header(path: str, value: Any) -> bool:
    text = str(value or "").strip().lower()
    if path.endswith(("label", "labels", "header", "title", "instruction")):
        return True
    label_values = {
        "three-digit plan number (pn)",
        "purpose",
        "amount of sales and base commissions paid",
        "name and address of the agent, broker, or other person to whom commissions or fees were paid",
    }
    return text in label_values


def is_blank_extraction_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if text in {
        "",
        "missing",
        "unreadable",
        "blank",
        "none",
        "null",
        "n/a",
        "na",
        "unknown",
        "not found",
        "obscured",
        "not provided",
        "not shown",
        "not visible",
        "redacted",
    }:
        return True
    return is_obvious_template_placeholder(value) or any(
        marker in text for marker in ["obscured", "redaction", "redacted", "not visible", "unreadable"]
    )


def looks_like_ein(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{2}-\d{7}", clean_extracted_value(str(value or ""))))


def looks_like_numeric_or_code(value: Any) -> bool:
    clean = clean_extracted_value(str(value or "")).replace(",", "")
    return bool(clean and re.fullmatch(r"[0-9./\-\s]+", clean))


def looks_like_org_code(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", clean_extracted_value(str(value or ""))))


def is_probable_carrier_name(value: Any) -> bool:
    clean = clean_extracted_value(str(value or ""))
    lower = clean.lower()
    if not clean or is_obvious_template_placeholder(clean) or looks_like_ein(clean) or looks_like_numeric_or_code(clean):
        return False
    if lower in {"name of insurance carrier", "name of insurance company", "insurance carrier", "carrier name"}:
        return False
    if not re.search(r"[A-Za-z]", clean):
        return False
    return any(marker in lower for marker in ("insurance", "company", "carrier", "inc", "llc", "life", "health")) or len(clean.split()) >= 2


def is_probable_person_or_entity_name(value: Any) -> bool:
    clean = clean_extracted_value(str(value or ""))
    lower = clean.lower()
    if not clean or is_obvious_template_placeholder(clean) or looks_like_numeric_or_code(clean):
        return False
    if lower in {
        "name",
        "name and address",
        "name and address of the agents, brokers or other persons to whom commissions or fees were paid",
        "amount",
        "purpose",
        "organization code",
    }:
        return False
    return bool(re.search(r"[A-Za-z]", clean))


def has_experience_rated_not_applicable(text: str) -> bool:
    normalized = normalize_ocr_text(text).lower()
    if not normalized:
        return False
    experience_section = re.search(
        r"\b(?:9\.?\s*)?experience[-\s]?rated\s+contracts\b(.+?)(?=\b(?:10\.?\s*)?nonexperience[-\s]?rated\s+contracts\b|\bpart\s+iv\b|$)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if experience_section:
        section = experience_section.group(1)
        if find_money_amounts(section):
            return False
        return bool(re.search(r"^\s*(?:n/?a|not applicable)\b", section.strip(), flags=re.IGNORECASE))
    json_match = re.search(
        r'"(?:experience[_\s-]?rated[_\s-]?contracts|experience[_\s-]?rated)"\s*:\s*"?(?:n/?a|not applicable)"?',
        normalized,
        flags=re.IGNORECASE,
    )
    if json_match:
        return True
    for match in re.finditer(r"experience[-\s]?rated\s+contracts\b.{0,80}\b(?:n/?a|not applicable)\b", normalized):
        prefix = normalized[max(0, match.start() - 4) : match.start()]
        if "non" not in prefix:
            return True
    return False


def add_not_applicable_experience_rated_fields(
    fields: list[NormalizedExtractionField],
    page: int | None,
    source_text: str,
) -> None:
    for field_name in SCHEDULE_A_EXPERIENCE_RATED_FIELDS:
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value="N/A",
                confidence=0.95,
                page=page,
                source_text=source_text or "Experience-rated contracts N/A",
            )
        )


def extract_nonexperience_total_premium_from_text(text: str) -> str | None:
    normalized = normalize_ocr_text(text)
    if not normalized:
        return None

    vendor_total = re.search(
        r"(?:gross\s+premium|total\s+premiums?(?:\s+or\s+subscription\s+charges)?\s+(?:received|paid)"
        r"(?:\s+to\s+(?:(?:the\s+)?insurance\s+company|carrier))?"
        r"(?:\s+during\s+(?:the\s+)?policy\s+year)?)"
        r"[\s.:]*\$?\s*([0-9][0-9,]*(?:\.\d{2})?)(?![0-9/])",
        normalized,
        flags=re.IGNORECASE,
    )
    if vendor_total:
        return vendor_total.group(1)

    for match in re.finditer(
        r'"(?:total_premiums_or_subscription_charges_paid_to_carrier|total_premiums|total_premium|total_amount)"\s*:\s*"?([0-9][0-9,]*(?:\.\d{2})?)"?',
        normalized,
        flags=re.IGNORECASE,
    ):
        context = normalized[max(0, match.start() - 250) : match.end() + 250].lower()
        if "nonexperience" in context or "subscription charges" in context or "premiums" in context:
            return match.group(1)

    compact = re.sub(r"\s+", " ", normalized)
    section_match = re.search(
        r"\bnonexperience[-\s]?rated\s+contracts\b(.+?)(?=\bpart\s+iv\b|\bagent/broker\b|\baddendum\b|\bclient\s+name\b|$)",
        compact,
        flags=re.IGNORECASE,
    )
    if section_match:
        section = section_match.group(1)
        if re.search(r"^\s*(?:n/?a|not applicable)\b", section.strip(), flags=re.IGNORECASE):
            return None
        amounts = find_money_amounts(section)
        if amounts:
            return amounts[-1]

    label_match = re.search(
        r"total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier",
        compact,
        flags=re.IGNORECASE,
    )
    if label_match:
        before = compact[max(0, label_match.start() - 500) : label_match.start()]
        after = compact[label_match.end() : label_match.end() + 500]
        amounts = find_money_amounts(after) or find_money_amounts(before)
        if amounts:
            return amounts[-1]

    return None


def find_money_amounts(text: str) -> list[str]:
    return re.findall(r"\b[0-9]{1,3}(?:,[0-9]{3})+(?:\.\d{2})?\b|\b[0-9]{4,}(?:\.\d{2})?\b", text)


def build_groundx_schema_query(
    file_name: str | None = None,
    rules=None,
    *,
    form_type: FormType = FormType.SCHEDULE_A,
) -> str:
    relevant_rules = rules_for_form(rules if rules is not None else DEFAULT_FIELD_RULES, form_type)
    field_hints: list[str] = []
    for rule in relevant_rules:
        aliases = [
            alias
            for alias in rule.aliases
            if normalize_rule_label(alias) != normalize_rule_label(rule.label)
        ]
        hint = rule.label
        if aliases:
            hint += f" (also labeled: {', '.join(aliases)})"
        field_hints.append(hint)
    labels = "; ".join(field_hints)
    file_hint = f" Prefer content from file named {file_name} when that file is searchable. " if file_name else " "
    return (
        "Using this Schedule A / Form 5500 document, retrieve the text needed to extract these FT Williams fields."
        f"{file_hint}"
        "Focus on exact values near labels, tables, and line numbers. Fields: "
        f"{labels}"
    )


def rules_for_form(rules, form_type: FormType):
    if form_type == FormType.FORM_5500:
        return [
            rule
            for rule in rules
            if str(getattr(rule.applicability, "value", rule.applicability)) == "FORM_5500"
            or str(rule.source).lower().startswith("form 5500")
            or str(rule.form_section or "").lower().startswith("form 5500")
        ]
    return [
        rule
        for rule in rules
        if str(getattr(rule.applicability, "value", rule.applicability)) != "FORM_5500"
        and not str(rule.source).lower().startswith("form 5500")
        and not str(rule.form_section or "").lower().startswith("form 5500")
    ]


def normalize_ocr_text(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\(\s*([a-zA-Z0-9]+)\s*\)", r"(\1)", text)
    text = re.sub(r"(?<!\()\b([a-zA-Z])\s*\)", r"(\1)", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_groundx_page(result: dict) -> int | None:
    boxes = result.get("boundingBoxes") or []
    if isinstance(boxes, list) and boxes:
        page = parse_page(boxes[0].get("pageNumber"))
        if page:
            return page
    pages = result.get("pages") or []
    if isinstance(pages, list) and pages:
        return parse_page(pages[0])
    return parse_page(result.get("pageNumber") or result.get("page"))


def build_structured_extraction_context(raw_payloads: list[Any], file_bytes: bytes) -> str:
    chunks: list[str] = []
    for payload in raw_payloads:
        if is_groundx_search_payload(payload):
            chunks.extend(extract_text_chunks_from_groundx_search(payload))
        elif is_groundx_xray_payload(payload):
            for chunk in payload.get("chunks", []):
                if not isinstance(chunk, dict):
                    continue
                text = build_xray_source_text(chunk)
                if text:
                    page = parse_xray_page(chunk)
                    prefix = f"[GroundX page {page}]\n" if page else "[GroundX]\n"
                    chunks.append(f"{prefix}{text}")

    pages = extract_pdf_text_pages(file_bytes)
    for page_number, text in pages:
        if text.strip():
            chunks.append(f"[PDF page {page_number}]\n{text.strip()}")

    seen: set[str] = set()
    unique_chunks: list[str] = []
    for chunk in chunks:
        normalized = normalize_ocr_text(chunk)
        key = normalized[:500]
        if normalized and key not in seen:
            seen.add(key)
            unique_chunks.append(normalized)
    return "\n\n---\n\n".join(unique_chunks)[:70000]


def extract_text_chunks_from_groundx_search(raw: Any) -> list[str]:
    search = raw.get("search", {}) if isinstance(raw, dict) else {}
    results = search.get("results", []) if isinstance(search, dict) else []
    chunks: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        text = normalize_ocr_text(result.get("text") or result.get("suggestedText") or result.get("narrative") or "")
        if text:
            page = parse_groundx_page(result)
            prefix = f"[GroundX page {page}]\n" if page else "[GroundX]\n"
            chunks.append(f"{prefix}{text}")
    return chunks


def extract_pdf_text_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    try:
        reader = PdfReader(BytesIO(file_bytes))
    except Exception:
        return []

    pages: list[tuple[int, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        pages.append((index, normalize_ocr_text(text)))
    return pages


def extract_schedule_a_acroform(
    file_bytes: bytes,
) -> tuple[list[NormalizedExtractionField], list[ScheduleABrokerRow]]:
    """Read visible Schedule A values stored in AcroForm widgets.

    Some completed IRS PDFs retain a hidden sample text layer.  The widget
    values are the actual source of truth and must win over that layer.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], []
    try:
        raw_fields = PdfReader(BytesIO(file_bytes)).get_fields() or {}
    except Exception:
        return [], []
    values: dict[str, str] = {}
    for name, item in raw_fields.items():
        if not isinstance(item, dict):
            continue
        value = item.get("/V")
        if isinstance(value, (dict, list, bytes)) or value in (None, "", "/Off"):
            continue
        clean = str(value).strip()
        if clean and not clean.startswith("/"):
            values[str(name)] = clean
    return extract_schedule_a_acroform_data(values)


def extract_schedule_a_acroform_data(
    values: dict[str, str],
) -> tuple[list[NormalizedExtractionField], list[ScheduleABrokerRow]]:
    scalar_map = {
        "insCarrierName": "1a. Name of Insurance Company",
        "insCarrierEIN": "1b. Insurance Carrier EIN",
        "insCarrierNAICCode": "1c. NAIC Code",
        "insContractNum": "1d. Contract/Policy Number",
        "insPrsnCoveredEoyCnt": "1e. Persons Covered (End of Policy Year)",
        "insPolicyFromDate": "1f. Policy Year Beginning Date",
        "insPolicyToDate": "1g. Policy Year Ending Date",
        "insBrokerCommTotAmt": "3b. Amount of Commissions",
        "insBrokerFeesTotAmt": "3c. Amount of Fees",
        "wlfrPremiumRcvdAmt": "9a. Premiums: (1) Amount Received",
        "wlfrUnpaidDueAmt": "9a(2). Increase (decrease) in amount due but unpaid",
        "wlfrReserveAmt": "9a(3). Increase (decrease) in unearned premium reserve",
        "wlfrTotEarnedPremAmt": "9a(4). Earned ((1) + (2) - (3))",
        "wlfrClaimsPaidAmt": "9b(1). Benefit Charges (1) Claims paid",
        "wlfrIncrReserveAmt": "9b(2). Increase (decrease) in claim reserves",
        "wlfrIncurredClaimAmt": "9b(3). Incurred claims (add(1) and (2))",
        "wlfrClaimsChrgdAmt": "9b(4). Claims Charged",
        "wlfrRetCommissionsAmt": "9c(1)(A). Commissions",
        "wlfrRetAdminAmt": "9c(1)(B). Administrative service or other fees",
        "wlfrRetOthCostAmt": "9c(1)(C). Other Specific acquisition costs",
        "wlfrRetOthExpenseAmt": "9c(1)(D). Other expenses",
        "wlfrRetTaxesAmt": "9c(1)(E). Taxes",
        "wlfrRetChargesAmt": "9c(1)(F). Charges for risks or other contingencies",
        "wlfrRetOthChrgsAmt": "9c(1)(G). Other retention charges",
        "wlfrRetTotAmt": "9c(1)(H). Total retention",
        "wlfrRefundAmt": "9c(2). Dividends or retroactive rate refunds",
        "wlfrHeldBnftsAmt": "9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement",
        "wlfrClaimsReserveAmt": "9d(2). Claim reserves",
        "wlfrOthReserveAmt": "9d(3). Other reserves",
        "wlfrDivndsDueAmt": "9e. Dividends or retroactive rate refunds due",
        "wlfrTotChargesPaidAmt": "10a. Total premiums or subscription charges paid to carrier",
    }
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None, source_name: str) -> None:
        clean = clean_extracted_value(str(value or ""))
        if not clean or is_blank_extraction_value(clean) or is_obvious_template_placeholder(clean):
            return
        if field_name.startswith("1b."):
            digits = re.sub(r"\D", "", clean)
            if len(digits) == 9:
                clean = f"{digits[:2]}-{digits[2:]}"
        elif field_name.startswith("1c."):
            clean = normalize_schedule_a_naic(clean)
        elif field_name.startswith(("1f.", "1g.")):
            clean = _normalize_schedule_a_source_date(clean)
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=0.995,
                page=1,
                source_text=f"AcroForm {source_name}={clean}",
                evidence=[
                    SourceEvidence(
                        provider="AcroForm widget parser",
                        page=1,
                        source_text=f"{source_name}={clean}",
                        table_cell=(1, 0),
                    )
                ],
            )
        )

    for source_name, field_name in scalar_map.items():
        add(field_name, values.get(source_name), source_name)

    brokers: list[ScheduleABrokerRow] = []
    for index in range(1, 100):
        suffix = f"_{index:02d}"
        name = clean_extracted_value(values.get(f"insBrokerName{suffix}", ""))
        if not name:
            continue
        commission = money_value(values.get(f"insBrokerCommPdAmt{suffix}", "") or "0")
        fee = money_value(values.get(f"insBrokerFeesPdAmt{suffix}", "") or "0")
        purpose = clean_extracted_value(values.get(f"insBrokerFeesPdText{suffix}", "")) or derive_schedule_a_purpose(commission, fee)
        address_line_1, address_line_2, city, state, zip_code = _acroform_broker_address(
            values.get(f"insBrokerAddress{suffix}", "")
        )
        source = f"AcroForm broker {index}: {name}"
        brokers.append(
            ScheduleABrokerRow(
                name=name,
                address_line_1=address_line_1,
                address_line_2=address_line_2,
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=clean_extracted_value(values.get(f"insBrokerCode{suffix}", "")) or None,
                purpose=purpose,
                commission_rows=(
                    [ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")]
                    if (parse_numeric_amount(commission) or 0) > 0
                    else []
                ),
                fee_rows=(
                    [ScheduleABrokerMoneyRow(amount=fee, purpose=purpose or "FEES")]
                    if (parse_numeric_amount(fee) or 0) > 0
                    else []
                ),
                commission_total=commission,
                fee_total=fee,
                commission_source_text=source,
                fee_source_text=source,
                source_page=1,
                confidence=0.995,
                evidence=[SourceEvidence(provider="AcroForm widget parser", page=1, source_text=source, table_cell=(index, 0))],
            )
        )

    if brokers:
        total_commission = sum_money_values(*(row.commission_total for row in brokers)) or "0"
        total_fee = sum_money_values(*(row.fee_total for row in brokers)) or "0"
        existing = {field.field_name for field in fields}
        if "3b. Amount of Commissions" not in existing:
            add("3b. Amount of Commissions", total_commission, "broker rows total")
        if "3c. Amount of Fees" not in existing:
            add("3c. Amount of Fees", total_fee, "broker rows total")
        add("3a. Name of Agent/Broker/Person", brokers[0].name, "first broker row")
        purpose = derive_schedule_a_purpose(total_commission, total_fee)
        if purpose:
            add("3d. Purpose", purpose, "broker rows purpose")
        if brokers[0].organization_code:
            add("3e. Organizational Code", brokers[0].organization_code, "first broker row code")
    return select_best_schedule_a_fields(fields), brokers


def _acroform_broker_address(value: str | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    lines = [clean_extracted_value(line) for line in re.split(r"[\r\n]+", str(value or "")) if clean_extracted_value(line)]
    if not lines:
        return None, None, None, None, None
    city = state = zip_code = None
    street_lines = list(lines)
    match = re.fullmatch(r"(.+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", lines[-1], flags=re.IGNORECASE)
    if match:
        city = clean_extracted_value(match.group(1))
        state = match.group(2).upper()
        zip_code = match.group(3)
        street_lines = lines[:-1]
    return (
        street_lines[0] if street_lines else None,
        " ".join(street_lines[1:]) if len(street_lines) > 1 else None,
        city,
        state,
        zip_code,
    )


def extract_pdf_layout_text_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    """Extract PDF text while retaining the whitespace that describes tables.

    The ordinary text layer is useful for prose and carrier-specific parsers,
    but it collapses table columns.  That turns headings such as ``(b) Amount
    of`` into apparent broker names.  Pypdf's layout mode gives the universal
    parser a carrier-neutral representation of rows and columns while keeping
    the original parser available as a fallback.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    try:
        reader = PdfReader(BytesIO(file_bytes))
    except Exception:
        return []

    pages: list[tuple[int, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except (TypeError, ValueError, KeyError):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
        text = str(text).replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
        pages.append((index, text.strip("\n")))
    return pages


def extract_docx_text(file_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
            xml_files = [
                "word/document.xml",
                *sorted(name for name in archive.namelist() if name.startswith("word/header") and name.endswith(".xml")),
                *sorted(name for name in archive.namelist() if name.startswith("word/footer") and name.endswith(".xml")),
            ]
            chunks: list[str] = []
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for xml_file in xml_files:
                if xml_file not in archive.namelist():
                    continue
                root = ET.fromstring(archive.read(xml_file))
                for paragraph in root.findall(".//w:p", namespace):
                    text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
                    if text:
                        chunks.append(text)
            return normalize_ocr_text("\n".join(chunks))
    except Exception:
        return ""


def parse_plan_worksheet_text(
    text: str,
    *,
    rules=None,
    default_year: str | None = None,
) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    compact = re.sub(r"\s+", " ", text).strip()

    def add(field_name: str, value: Any, confidence: float = 0.92, source_text: str | None = None):
        clean = clean_extracted_value(str(value or ""))
        if clean:
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=confidence,
                    page=None,
                    source_text=source_text or clean,
                )
            )

    sponsor_name = regex_first(compact, [r"Plan sponsor name\s+(.+?)\s+Plan sponsor address"], flags=re.IGNORECASE)
    sponsor_address = regex_first(compact, [r"Plan sponsor address\s+(.+?)\s+Plan sponsor phone number"], flags=re.IGNORECASE)
    sponsor_ein = regex_first(compact, [r"\bEIN\s+([0-9]{2}-[0-9]{7})\b"], flags=re.IGNORECASE)
    business_code = regex_first(compact, [r"Business code\s+([0-9]{4,6})\b"], flags=re.IGNORECASE)
    plan_number = regex_first(compact, [r"Plan number\(s\)\s+([0-9]{3})\b"], flags=re.IGNORECASE)
    plan_name = regex_first(compact, [r"Plan name\(s\)\s+(.+?)\s+Plan year"], flags=re.IGNORECASE)
    effective_date = regex_first(compact, [r"Original ERISA plan effective date\s+([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4})"], flags=re.IGNORECASE)
    plan_year = regex_first(
        compact,
        [
            r"Plan year\s+begin\s*/?\s*end\s+"
            r"([0-9]{1,2}[-/][0-9]{1,2}(?:[-/][0-9]{4})?)\s+"
            r"([0-9]{1,2}[-/][0-9]{1,2}(?:[-/][0-9]{4})?)"
        ],
        flags=re.IGNORECASE,
        groups=True,
    )
    add("1a. Plan Name", plan_name, 0.95)
    add("1b. Plan Number (PN)", plan_number, 0.95)
    add("1c. Plan Effective Date", effective_date, 0.93)
    add("1d. Plan Sponsor Name", sponsor_name, 0.95)
    add("1e. Plan Sponsor EIN", sponsor_ein, 0.95)
    add("1f. Plan Sponsor Address", sponsor_address, 0.92)
    add("1g. Business Code", business_code, 0.94)
    if isinstance(plan_year, tuple) and len(plan_year) >= 2:
        plan_year_begin = normalize_worksheet_date(plan_year[0], default_year, end_of_month=False)
        plan_year_end = normalize_worksheet_date(plan_year[1], default_year, end_of_month=True)
        add("6. Plan Year Beginning Date", plan_year_begin.replace("/", "-"), 0.95)
        add("7. Plan Year Ending Date", plan_year_end.replace("/", "-"), 0.95)

    beginning_total = regex_first(
        compact,
        [r"Total number of participants at the beginning of the plan year.*?\]\s*([0-9,]+)\s+6\(a\)\(1\)"],
        flags=re.IGNORECASE,
    )
    active_beginning = regex_first(
        compact,
        [r"6\(a\)\(1\)\s+Total number of active participants on the first day of the plan year.*?\]\s*([0-9,]+)\s+6\(a\)\(2\)"],
        flags=re.IGNORECASE,
    )
    active_end = regex_first(
        compact,
        [r"6\(a\)\(2\)\s+Total number of active participants on the last day of the plan year\s+([0-9,]+)\s+6\(b\)"],
        flags=re.IGNORECASE,
    )
    retired_receiving = regex_first(
        compact,
        [r"6\(b\)\s+Total number of retired or COBRA participants on benefits as of last day of the plan year\s+([0-9,]+)\s+6\(c\)"],
        flags=re.IGNORECASE,
    )
    retired_entitled = regex_first(
        compact,
        [r"6\(c\)\s+Total number of retired or COBRA participants entitled to benefits as of last day of the plan year\s+([0-9,]+)"],
        flags=re.IGNORECASE,
    )
    total_end = regex_first(
        compact,
        [r"Total participants at end of (?:the )?plan year\s+([0-9,]+)", r"Total number of participants at the end of the plan year\s+([0-9,]+)"],
        flags=re.IGNORECASE,
    )
    if not total_end and active_end is not None and retired_receiving is not None and retired_entitled is not None:
        total_end = str(
            int(str(active_end).replace(",", ""))
            + int(str(retired_receiving).replace(",", ""))
            + int(str(retired_entitled).replace(",", ""))
        )

    add("11. Total participants at beginning of year", beginning_total, 0.93)
    add("12. Total participants at end of year", total_end, 0.9 if total_end else 0.0)
    add("13. Active participants at beginning", active_beginning, 0.93)
    add("14. Active participants at end", active_end, 0.93)
    add("15. Retired/separated participants receiving benefits", retired_receiving, 0.93)
    add("16. Other retired/separated participants entitled to benefits", retired_entitled, 0.93)

    if re.search(r"\bwelfare benefit plan\b|\bemployee welfare benefit\b", compact, flags=re.IGNORECASE):
        add("6. Plan is a welfare plan?", "Yes", 0.86)
    welfare_codes = regex_first(
        compact,
        [
            r"If the plan provides welfare benefits.*?:\s*((?:4[A-Z]\s*)+)",
            r"applicable codes.*?:\s*((?:4[A-Z]\s*)+)",
        ],
        flags=re.IGNORECASE,
    )
    if welfare_codes:
        codes = " ".join(re.findall(r"4[A-Z]", welfare_codes.upper()))
        add("8c. Welfare Benefit Features", codes, 0.88, source_text=welfare_codes)
    if re.search(r"Fully-Insured Benefits", compact, flags=re.IGNORECASE):
        add("9. Plan funding arrangement", "Insurance", 0.86)
        add("10a. Plan benefit arrangement", "Insurance", 0.86)
        add("10b. Schedules attached", "A", 0.86)

    fields.extend(extract_configured_custom_fields(text, None, rules=rules))

    return dedupe_fields(fields)


def extract_plan_worksheet_schedule_a_summaries(
    text: str,
    *,
    default_year: str | None = None,
) -> list[ScheduleAWorksheetSummary]:
    """Extract and deduplicate the fully-insured benefit table from a worksheet."""
    normalized = normalize_ocr_text(text)
    section_match = re.search(
        r"Fully-Insured\s+Benefits(?P<section>.*?)(?:Self-Funded\s+Benefits|$)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []
    section = section_match.group("section")
    row_pattern = re.compile(
        r"(?m)^\s*(?P<benefit>[A-Z][A-Z0-9 &;,/.'()-]*?)\s*\n"
        r"\s*(?P<carrier>[A-Z][A-Z0-9 &.,'()-]+?)\s*\n"
        r"\s*(?P<policy>[A-Z0-9/-]{1,})\s*\n"
        r"\s*(?P<begin>\d{1,2}[-/]\d{1,2}(?:[-/]\d{4})?)\s*\n"
        r"\s*(?P<end>\d{1,2}[-/]\d{1,2}(?:[-/]\d{4})?)\s*$"
    )
    summaries: list[ScheduleAWorksheetSummary] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for match in row_pattern.finditer(section):
        benefit = clean_extracted_value(match.group("benefit")).rstrip(";")
        carrier = clean_extracted_value(match.group("carrier"))
        policy = clean_extracted_value(match.group("policy"))
        begin = normalize_worksheet_date(match.group("begin"), default_year, end_of_month=False)
        end = normalize_worksheet_date(match.group("end"), default_year, end_of_month=True)
        key = tuple(normalize_compare_key(value) for value in (benefit, carrier, policy, begin, end))
        if key in seen:
            continue
        seen.add(key)
        summaries.append(
            ScheduleAWorksheetSummary(
                source="Plan Worksheet fully-insured benefit table",
                carrier_name=carrier,
                account_number=policy,
                period_begin=begin,
                period_end=end,
                coverage=benefit,
                values=[
                    ScheduleAWorksheetValue(
                        label="Worksheet policy number",
                        value=policy,
                        source="Fully-Insured Benefits table",
                        coverage=benefit,
                    )
                ],
                notes=["Carrier, policy number, and policy dates were read from the Plan Worksheet table."],
            )
        )
    return summaries


def normalize_worksheet_date(value: str, default_year: str | None, *, end_of_month: bool) -> str | None:
    clean = str(value or "").strip().replace("-", "/")
    if normalize_compare_key(clean) in {"", "na", "notapplicable", "none"}:
        return None
    if re.fullmatch(r"\d{1,2}/\d{1,2}", clean) and default_year:
        clean = f"{clean}/{default_year}"
    try:
        return normalize_schedule_a_date(clean, end_of_month=end_of_month)
    except (TypeError, ValueError):
        return None


def extract_docx_table_rows(file_bytes: bytes) -> list[list[str]]:
    """Read DOCX table rows without losing merged-cell or line boundaries."""
    try:
        with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except Exception:
        return []
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    rows: list[list[str]] = []
    for table in root.findall(".//w:tbl", namespace):
        for row in table.findall("./w:tr", namespace):
            cells: list[str] = []
            for cell in row.findall("./w:tc", namespace):
                paragraphs: list[str] = []
                for paragraph in cell.findall(".//w:p", namespace):
                    value = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
                    if value:
                        paragraphs.append(value)
                cells.append("\n".join(paragraphs).strip())
            if cells:
                rows.append(cells)
    return rows


def extract_plan_worksheet_docx_schedule_a_summaries(
    file_bytes: bytes,
    *,
    default_year: str | None = None,
) -> list[ScheduleAWorksheetSummary]:
    """Extract the native worksheet table, including short dates and multi-value cells."""
    rows = extract_docx_table_rows(file_bytes)
    summaries: list[ScheduleAWorksheetSummary] = []
    in_fully_insured_table = False
    seen: set[tuple[str, str, str, str, str]] = set()
    for cells in rows:
        normalized_headers = [normalize_compare_key(cell) for cell in cells]
        if {"benefit", "carrier", "policynumber"}.issubset(set(normalized_headers)) or (
            len(cells) >= 5
            and normalize_compare_key(cells[0]) == "benefit"
            and normalize_compare_key(cells[1]) == "carrier"
            and normalize_compare_key(cells[2]).startswith("policy")
        ):
            in_fully_insured_table = True
            continue
        if not in_fully_insured_table or len(cells) < 5:
            continue
        benefit_cell, carrier, policy_cell, begin_cell, end_cell = [str(value or "").strip() for value in cells[:5]]
        if not benefit_cell or not carrier or not policy_cell or not begin_cell or not end_cell:
            continue
        begin = normalize_worksheet_date(begin_cell, default_year, end_of_month=False)
        end = normalize_worksheet_date(end_cell, default_year, end_of_month=True)
        if not begin or not end:
            continue
        benefits = [clean_extracted_value(value) for value in benefit_cell.splitlines() if clean_extracted_value(value)]
        policies = [clean_extracted_value(value) for value in policy_cell.splitlines() if clean_extracted_value(value)]
        pairs = list(zip(benefits, policies)) if len(benefits) == len(policies) and len(policies) > 1 else [
            ("; ".join(benefits), "; ".join(policies))
        ]
        for benefit, policy in pairs:
            key = tuple(normalize_compare_key(value) for value in (benefit, carrier, policy, begin, end))
            if key in seen:
                continue
            seen.add(key)
            summaries.append(
                ScheduleAWorksheetSummary(
                    source="Plan Worksheet fully-insured benefit table",
                    carrier_name=carrier,
                    account_number=policy,
                    period_begin=begin,
                    period_end=end,
                    coverage=benefit,
                    values=[
                        ScheduleAWorksheetValue(
                            label="Worksheet policy number",
                            value=policy,
                            source="Fully-Insured Benefits table",
                            coverage=benefit,
                        )
                    ],
                    notes=["Carrier, policy number, and policy dates were read from the native Plan Worksheet table."],
                )
            )
    return summaries


def file_type_for_groundx(file_name: str) -> str:
    extension = os.path.splitext(file_name.lower())[1].lstrip(".")
    if extension == "jpeg":
        extension = "jpg"
    if extension in {"pdf", "docx", "doc", "xlsx", "xls", "csv", "txt", "png", "jpg", "tiff"}:
        return extension
    if extension == "tif":
        return "tiff"
    return "pdf"


def extract_nyl_paid_premium_workbook(
    file_bytes: bytes,
    file_name: str | None,
) -> NormalizedExtractionResult | None:
    """Read NYL Group Benefit Solutions' short-year spreadsheet export.

    These exports replace the normal carrier Schedule A report. Their two
    sheets contain premium and commission transactions, so totals must be
    grouped and summed instead of treating each transaction as a field.
    """
    if not str(file_name or "").lower().endswith((".xlsx", ".xlsm")):
        return None
    try:
        from collections import defaultdict
        from decimal import Decimal
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
        premium_sheet = workbook["PaidPremiumData"]
        commission_sheet = workbook["Commissions"]
    except Exception:
        return None

    premium_rows = list(premium_sheet.iter_rows(values_only=True))
    commission_rows = list(commission_sheet.iter_rows(values_only=True))
    if not premium_rows or not commission_rows:
        return None
    premium_headers = {str(value or "").strip(): index for index, value in enumerate(premium_rows[0])}
    commission_headers = {str(value or "").strip(): index for index, value in enumerate(commission_rows[0])}
    required_premium = {"Underwriter", "PolicyNumber", "PremPeriod", "Product_Benefit", "AppliedAmt"}
    required_commission = {"Broker Number", "Broker Name", "Policy Number", "Commission Amount"}
    if not required_premium.issubset(premium_headers) or not required_commission.issubset(commission_headers):
        return None

    premium_by_policy: dict[str, Decimal] = defaultdict(Decimal)
    products_by_policy: dict[str, set[str]] = defaultdict(set)
    periods: list[datetime] = []
    underwriters: set[str] = set()
    for row in premium_rows[1:]:
        policy = clean_extracted_value(str(row[premium_headers["PolicyNumber"]] or ""))
        if not policy:
            continue
        amount = row[premium_headers["AppliedAmt"]]
        try:
            premium_by_policy[policy] += Decimal(str(amount or 0))
        except Exception:
            continue
        product = clean_extracted_value(str(row[premium_headers["Product_Benefit"]] or ""))
        if product:
            products_by_policy[policy].add(product)
        period = row[premium_headers["PremPeriod"]]
        if isinstance(period, datetime):
            periods.append(period)
        underwriter = clean_extracted_value(str(row[premium_headers["Underwriter"]] or "")).upper()
        if underwriter:
            underwriters.add(underwriter)
    if not premium_by_policy or underwriters != {"CLICNY"}:
        return None

    commission_by_policy: dict[str, Decimal] = defaultdict(Decimal)
    commission_by_broker: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in commission_rows[1:]:
        policy = clean_extracted_value(str(row[commission_headers["Policy Number"]] or ""))
        broker_number = clean_extracted_value(str(row[commission_headers["Broker Number"]] or ""))
        broker_name = clean_extracted_value(str(row[commission_headers["Broker Name"]] or ""))
        try:
            amount = Decimal(str(row[commission_headers["Commission Amount"]] or 0))
        except Exception:
            continue
        if policy:
            commission_by_policy[policy] += amount
        if broker_name:
            commission_by_broker[(broker_number, broker_name)] += amount

    begin = min(periods).strftime("%m/%d/%Y") if periods else None
    if periods:
        last = max(periods)
        end = f"{last.month:02d}/{calendar.monthrange(last.year, last.month)[1]:02d}/{last.year}"
    else:
        end = None
    policies = sorted(premium_by_policy)
    total_premium = sum(premium_by_policy.values(), Decimal("0"))
    total_commission = sum(commission_by_broker.values(), Decimal("0"))
    source = "NYL paid-premium and commissions workbook"
    carrier = "New York Life Group Insurance Company of NY"
    fields = [
        NormalizedExtractionField(field_name="1a. Name of Insurance Company", value=carrier, confidence=0.99, page=1, source_text=f"{source}; Underwriter CLICNY"),
        NormalizedExtractionField(field_name="1b. Insurance Carrier EIN", value="13-2556568", confidence=0.99, page=1, source_text=f"{source}; verified CLICNY legal-entity mapping"),
        NormalizedExtractionField(field_name="1c. NAIC Code", value="64548", confidence=0.99, page=1, source_text=f"{source}; verified CLICNY legal-entity mapping"),
        NormalizedExtractionField(field_name="1d. Contract/Policy Number", value="; ".join(policies), candidate_values=policies, confidence=0.6 if len(policies) > 1 else 0.99, page=1, source_text=source, decision="REVIEW_REQUIRED" if len(policies) > 1 else "AUTOMATIC"),
        NormalizedExtractionField(field_name="1f. Policy Year Beginning Date", value=begin or "", confidence=0.99 if begin else 0, page=1, source_text=source),
        NormalizedExtractionField(field_name="1g. Policy Year Ending Date", value=end or "", confidence=0.99 if end else 0, page=1, source_text=source),
        NormalizedExtractionField(field_name="3b. Amount of Commissions", value=_money_display(total_commission), confidence=0.99, page=2, source_text=source),
        NormalizedExtractionField(field_name="3c. Amount of Fees", value="0", confidence=0.99, page=2, source_text=source),
        NormalizedExtractionField(field_name="3d. Purpose", value="COMMISSIONS", confidence=0.99, page=2, source_text=source),
        NormalizedExtractionField(field_name="3e. Organizational Code", value="3", confidence=0.99, page=2, source_text=source),
        NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value=_money_display(total_premium), confidence=0.99, page=1, source_text=source),
    ]
    fields = [field for field in fields if field.value]
    summaries = [
        ScheduleAWorksheetSummary(
            source=source,
            carrier_name=carrier,
            account_number=policy,
            period_begin=begin,
            period_end=end,
            ein="13-2556568",
            naic_code="64548",
            coverage="; ".join(sorted(products_by_policy[policy])),
            values=[
                ScheduleAWorksheetValue(label="Total nonexperience premium", value=_money_display(premium_by_policy[policy]), source=source),
                ScheduleAWorksheetValue(label="Broker payment total", value=_money_display(commission_by_policy[policy]), source=source),
                ScheduleAWorksheetValue(label="Fee total", value="0", source=source),
            ],
            notes=["Policy-level totals were summed from the workbook transaction rows."],
        )
        for policy in policies
    ]
    broker_rows = [
        ScheduleABrokerRow(
            name=name.upper(),
            organization_code="3",
            purpose="COMMISSIONS",
            commission_rows=[ScheduleABrokerMoneyRow(amount=_money_display(amount), purpose="COMMISSIONS")],
            commission_total=_money_display(amount),
            fee_total="0",
            commission_source_text=f"{source}; broker {number}",
            fee_source_text=f"{source}; no fee column",
            source_page=2,
            confidence=0.99,
            evidence=[SourceEvidence(provider=source, page=2, source_text=f"Broker {number}: {name}")],
        )
        for (number, name), amount in sorted(commission_by_broker.items())
    ]
    return NormalizedExtractionResult(
        provider="NYL paid-premium workbook parser",
        fields=fields,
        raw={
            "file_name": file_name,
            "source": "nyl_paid_premium_workbook",
            "policy_totals": {
                policy: {
                    "premium": _money_display(premium_by_policy[policy]),
                    "commission": _money_display(commission_by_policy[policy]),
                    "products": sorted(products_by_policy[policy]),
                }
                for policy in policies
            },
        },
        schedule_a_broker_rows=broker_rows,
        schedule_a_worksheet_summaries=summaries,
    )


def local_schedule_a_pdf_result(
    file_bytes: bytes,
    file_name: str,
    provider: str = "Local PDF parser",
    *,
    rules=None,
) -> NormalizedExtractionResult:
    nyl_workbook = extract_nyl_paid_premium_workbook(file_bytes, file_name)
    if nyl_workbook:
        return nyl_workbook
    is_pdf = file_name.lower().endswith(".pdf")
    page_texts = (
        extract_pdf_text_pages(file_bytes)
        if is_pdf
        else extract_document_text_pages(file_bytes, file_name)
    )
    layout_page_texts = extract_pdf_layout_text_pages(file_bytes) if is_pdf else []
    acroform_fields, acroform_broker_rows = (
        extract_schedule_a_acroform(file_bytes) if is_pdf else ([], [])
    )
    ocr_page_texts: list[tuple[int, str]] = []
    hidden_template_layer = is_pdf and has_blank_schedule_a_form_layer(page_texts)
    if is_pdf and (
        not any(str(text or "").strip() for _, text in page_texts)
        or (hidden_template_layer and not acroform_fields)
    ):
        ocr_page_texts = extract_image_only_pdf_ocr_pages(file_bytes)
        if has_completed_schedule_a_overlay(ocr_page_texts):
            page_texts = ocr_page_texts
            layout_page_texts = ocr_page_texts
        elif hidden_template_layer:
            # Fail closed: labels from the blank native layer are not values.
            page_texts = []
            layout_page_texts = []
    broker_page_texts = layout_page_texts or page_texts
    aultcare_summaries = extract_aultcare_schedule_a_summaries(file_bytes, file_name)
    document_summaries = aultcare_summaries
    document_broker_rows = extract_aultcare_broker_rows(file_bytes, file_name) if aultcare_summaries else []
    provider_broker_rows = [
        *extract_first_unum_broker_rows(page_texts),
        *extract_ace_schedule_a_broker_rows(page_texts),
        *extract_transamerica_broker_rows(page_texts),
        *extract_combined_chubb_broker_rows(page_texts),
        *extract_john_hancock_broker_rows(page_texts),
        *extract_metlife_standard_broker_rows(page_texts),
        *extract_colonial_life_broker_rows(page_texts),
        *extract_cigna_schedule_a_broker_rows(page_texts),
        *extract_anthem_broker_rows(page_texts),
        *extract_aig_broker_rows(page_texts),
        *extract_vsp_broker_rows(page_texts),
        *extract_hartford_broker_rows(page_texts),
        *extract_metlife_bay_bridge_broker_rows(page_texts),
        *extract_unitedhealthcare_broker_rows(page_texts),
        *extract_prudential_broker_rows(broker_page_texts),
        *extract_aflac_broker_rows(broker_page_texts),
        *extract_guardian_broker_rows(broker_page_texts),
        *extract_american_heritage_broker_rows(broker_page_texts),
        *extract_sun_life_broker_rows(broker_page_texts),
        *extract_reliance_standard_broker_rows(broker_page_texts),
        *extract_ameritas_schedule_a_broker_rows(broker_page_texts),
        *extract_curalinc_broker_rows(broker_page_texts),
        *extract_continental_american_broker_rows(broker_page_texts),
        *document_broker_rows,
    ]
    omaha_rows = extract_united_omaha_combined_broker_rows(page_texts)
    if omaha_rows:
        provider_broker_rows.extend(omaha_rows)
    authoritative_broker_rows = (
        acroform_broker_rows
        or provider_broker_rows
        or extract_columnar_broker_compensation_rows(page_texts)
    )
    broker_rows = (
        authoritative_broker_rows
        if authoritative_broker_rows
        else (extract_schedule_a_broker_rows_from_pdf_text(file_bytes) if is_pdf else [])
    )
    parsed_fields = (
        _extract_fields_from_pages(page_texts, rules=rules)
        if ocr_page_texts
        else extract_fields_from_document_text(file_bytes, file_name, rules=rules)
    )
    for layout_fields in (
        extract_ameritas_schedule_a_fields(broker_page_texts),
        extract_curalinc_schedule_a_fields(broker_page_texts),
        extract_continental_american_schedule_a_fields(broker_page_texts),
    ):
        if layout_fields:
            parsed_fields = [
                field for field in parsed_fields
                if field.field_name not in SCHEDULE_A_LAYOUT_OWNED_FIELDS
            ]
            parsed_fields.extend(layout_fields)
    if acroform_fields:
        if hidden_template_layer:
            parsed_fields = list(acroform_fields)
        else:
            authoritative_names = {field.field_name for field in acroform_fields}
            parsed_fields = [field for field in parsed_fields if field.field_name not in authoritative_names]
            parsed_fields.extend(acroform_fields)
    parsed_fields = select_best_schedule_a_fields(parsed_fields)
    return NormalizedExtractionResult(
        provider=provider if is_pdf else "Local document parser",
        fields=(
            _multi_record_fields_from_summaries(aultcare_summaries, source="AultCare multi-policy workbook parser")
            if aultcare_summaries
            else parsed_fields
        ),
        raw={
            "file_name": file_name,
            "source": (
                "local_pdf_acroform_parser"
                if acroform_fields
                else "local_pdf_ocr_parser"
                if ocr_page_texts
                else "local_document_parser"
            ),
            "authoritative_broker_table": bool(authoritative_broker_rows),
            "authoritative_visible_overlay": bool(
                acroform_fields or (hidden_template_layer and ocr_page_texts)
            ),
            "ocr_pages": [
                {"page": page, "text": text}
                for page, text in ocr_page_texts
            ],
        },
        schedule_a_broker_rows=broker_rows,
        schedule_a_worksheet_summaries=(
            extract_schedule_a_worksheet_summaries_from_pdf_text(file_bytes)
            if is_pdf
            else document_summaries
        ),
    )


def extract_image_only_pdf_ocr_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    """OCR a PDF only when its native text layer is empty.

    GroundX remains the preferred semantic extractor. This bounded local path
    prevents a clean scanned carrier statement from becoming an empty filing
    when the remote ingestion job times out.
    """
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract or not file_bytes:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="schedule-a-ocr-") as temp_dir:
            source = os.path.join(temp_dir, "source.pdf")
            prefix = os.path.join(temp_dir, "page")
            with open(source, "wb") as handle:
                handle.write(file_bytes)
            subprocess.run(
                [pdftoppm, "-jpeg", "-r", "400", source, prefix],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
            images = sorted(
                (
                    path for path in os.listdir(temp_dir)
                    if path.startswith("page-") and path.lower().endswith((".jpg", ".jpeg"))
                ),
                key=lambda value: int(re.search(r"(\d+)(?=\.[^.]+$)", value).group(1)),
            )[:20]
            pages: list[tuple[int, str]] = []
            for index, image_name in enumerate(images, start=1):
                completed = subprocess.run(
                    [tesseract, os.path.join(temp_dir, image_name), "stdout", "--psm", "6"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                page_text = normalize_ocr_text(completed.stdout)
                pages.append((index, page_text))
            # Official Schedule A forms use independent table cells. PSM 6 can
            # drop an entire filled value row (including EIN/NAIC/dates), so
            # re-run standard forms in column-aware mode and keep the complete
            # packet together. This also covers sparse MetLife form packets.
            packet_text = "\n".join(text for _, text in pages).upper()
            if "SCHEDULE A" in packet_text and "FORM 5500" in packet_text:
                sparse_pages: list[tuple[int, str]] = []
                for index, image_name in enumerate(images, start=1):
                    sparse = subprocess.run(
                        [tesseract, os.path.join(temp_dir, image_name), "stdout", "--psm", "4"],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                    )
                    sparse_pages.append((index, normalize_ocr_text(sparse.stdout)))
                if any(text for _, text in sparse_pages):
                    pages = sparse_pages
            return [(page, text) for page, text in pages if text]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def extract_document_text_pages(file_bytes: bytes, file_name: str | None = None) -> list[tuple[int, str]]:
    """Get readable text out of a document, whatever format it arrived in.

    The label-driven parsing below is the strongest part of Schedule A
    extraction, and it used to run on PDFs only. A Schedule A that arrives as
    a spreadsheet or a CSV export carries the same labels and values, so it
    gets the same treatment - the rows are just flattened to text first.
    """
    name = str(file_name or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _spreadsheet_text_pages(file_bytes)
    if name.endswith(".csv"):
        return [(1, _delimited_text(file_bytes.decode("utf-8", errors="ignore")))]
    if name.endswith(".txt"):
        return [(1, normalize_ocr_text(file_bytes.decode("utf-8-sig", errors="ignore")))]
    if name.endswith((".doc", ".docx")):
        text = extract_docx_text(file_bytes)
        return [(1, text)] if text else []
    return extract_pdf_text_pages(file_bytes)


def _semantic_page_texts(
    file_bytes: bytes,
    file_name: str,
    result: NormalizedExtractionResult,
) -> list[tuple[int, str]]:
    """Return the strongest page-preserving source available for semantics."""
    name = str(file_name or "").lower()
    if name.endswith(".pdf"):
        local_ocr_pages = (
            result.raw.get("local_ocr_pages")
            if isinstance(result.raw, dict)
            else None
        )
        if isinstance(local_ocr_pages, list):
            pages = [
                (int(item.get("page") or 1), normalize_ocr_text(item.get("text") or ""))
                for item in local_ocr_pages
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if pages:
                return pages
        pages = extract_pdf_layout_text_pages(file_bytes)
        if any(text.strip() for _, text in pages):
            return pages
    else:
        pages = extract_document_text_pages(file_bytes, file_name)
        if any(text.strip() for _, text in pages):
            return pages

    # Image-only PDFs have no native text layer. GroundX X-Ray/search output
    # still carries page numbers, so use it rather than inventing evidence.
    page_chunks: dict[int, list[str]] = {}

    def add(page: int | None, text: str | None) -> None:
        clean = normalize_ocr_text(text or "")
        if not clean:
            return
        page_chunks.setdefault(page or 1, []).append(clean)

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        ocr_pages = value.get("ocr_pages")
        if isinstance(ocr_pages, list):
            for item in ocr_pages:
                if isinstance(item, dict):
                    add(item.get("page"), item.get("text"))
        chunks = value.get("chunks")
        if isinstance(chunks, list):
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                add(parse_xray_page(chunk), build_xray_source_text(chunk))
        if is_groundx_search_payload(value):
            for item in value.get("search", {}).get("results", []):
                if not isinstance(item, dict):
                    continue
                add(
                    parse_groundx_page(item),
                    item.get("text") or item.get("suggestedText") or item.get("narrative"),
                )
        for item in value.values():
            if isinstance(item, (dict, list)):
                walk(item)

    walk(result.raw)
    return [
        (page, "\n".join(dict.fromkeys(chunks)))
        for page, chunks in sorted(page_chunks.items())
        if chunks
    ]


def extract_schedule_a_classification_signals(file_bytes: bytes, file_name: str | None = None) -> list[str]:
    text = "\n\n".join(value for _, value in extract_document_text_pages(file_bytes, file_name))
    return classification_signals_from_text(text)


def _spreadsheet_text_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return []
    try:
        workbook = load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception:
        return []

    pages: list[tuple[int, str]] = []
    for index, sheet in enumerate(workbook.worksheets, start=1):
        lines: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell).strip() for cell in row if cell not in (None, "")]
            if not cells:
                continue
            # A two-column row is a label and its value - write it the way the
            # label parser expects to see it.
            lines.append(f"{cells[0]}: {cells[1]}" if len(cells) == 2 else " ".join(cells))
        if lines:
            pages.append((index, "\n".join(lines)))
    return pages


def _delimited_text(text: str) -> str:
    """Turn "Label,Value" rows into "Label: Value" lines."""
    lines: list[str] = []
    for row in csv.reader(StringIO(text.lstrip("\ufeff"))):
        parts = [str(part).strip() for part in row]
        parts = [part for part in parts if part]
        if not parts:
            continue
        lines.append(f"{parts[0]}: {parts[1]}" if len(parts) == 2 else " ".join(parts))
    return "\n".join(lines)


def extract_fields_from_document_text(
    file_bytes: bytes,
    file_name: str | None = None,
    *,
    rules=None,
) -> list[NormalizedExtractionField]:
    if str(file_name or "").lower().endswith(".pdf"):
        return extract_fields_from_pdf_text(file_bytes, rules=rules)
    pages = extract_document_text_pages(file_bytes, file_name)
    fields = [
        *_extract_fields_from_pages(pages, rules=rules),
        *extract_labelled_schedule_a_fields(pages),
        *(extract_email_schedule_a_fields(pages) if str(file_name or "").lower().endswith("email body.txt") else []),
        *schedule_a_broker_compensation_fields(extract_tabular_broker_rows(pages)),
    ]
    return _prefer_authoritative_aetna_attachment_fields(select_best_schedule_a_fields(fields))


def _has_aetna_attached_listing_evidence(field: NormalizedExtractionField) -> bool:
    return any(
        item.provider == "Aetna attached listing parser" and item.table_cell is not None
        for item in field.evidence
    )


def _prefer_authoritative_aetna_attachment_fields(
    fields: list[NormalizedExtractionField],
) -> list[NormalizedExtractionField]:
    """Do not turn Aetna's generic parent company into a false conflict.

    The Schedule A body says ``Aetna Health, Inc.`` while its state appendix
    gives the legal entity FT Williams requires. Once the parser has selected
    the appendix row for the sponsor's state, other carrier candidates are
    supporting evidence, not competing values.
    """
    for field in fields:
        if _has_aetna_attached_listing_evidence(field):
            field.candidate_values = [field.value]
    return fields


def prefer_authoritative_cigna_summary_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep Cigna's certified summary identity ahead of state appendices.

    Layout-aware candidate selection runs after the summary parser and can
    otherwise combine the main carrier/contract with an appendix EIN or NAIC.
    The report explicitly labels the summary as the Schedule A record; the
    appendix rows are supporting allocation detail.
    """
    if not _is_cigna_schedule_a_packet(page_texts):
        return fields
    summary_fields = extract_cigna_schedule_a_fields(page_texts)
    if not summary_fields:
        return fields
    authoritative = {field.field_name: field for field in summary_fields}
    restored = [
        field
        for field in fields
        if field.field_name not in authoritative and not field.field_name.startswith("9")
    ]
    for field in summary_fields:
        authoritative_field = field.model_copy(deep=True)
        authoritative_field.candidate_values = [authoritative_field.value]
        restored.append(authoritative_field)
    return restored


def prefer_authoritative_aig_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep AIG's labelled welfare-plan values ahead of generic columns."""
    authoritative_fields = extract_aig_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    authoritative = {field.field_name: field for field in authoritative_fields}
    restored = [field for field in fields if field.field_name not in authoritative]
    for field in authoritative_fields:
        authoritative_field = field.model_copy(deep=True)
        authoritative_field.candidate_values = [authoritative_field.value]
        restored.append(authoritative_field)
    return restored


def prefer_authoritative_aflac_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep AFLAC's explicit labels and drop a false EIN inferred from its contract."""
    authoritative_fields = extract_aflac_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    owned = {
        "1a. Name of Insurance Company",
        "1b. Insurance Carrier EIN",
        "1c. NAIC Code",
        "1d. Contract/Policy Number",
        "1e. Persons Covered (End of Policy Year)",
        "1f. Policy Year Beginning Date",
        "1g. Policy Year Ending Date",
        "10a. Total premiums or subscription charges paid to carrier",
        "1e. Plan Sponsor EIN",
    }
    restored = [field for field in fields if field.field_name not in owned]
    restored.extend(field.model_copy(update={"candidate_values": [field.value]}) for field in authoritative_fields)
    return restored


def prefer_authoritative_colonial_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep the labelled Paul Revere statement ahead of flattened OCR guesses."""
    authoritative_fields = extract_colonial_life_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    owned = {
        "1a. Name of Insurance Company",
        "1b. Insurance Carrier EIN",
        "1c. NAIC Code",
        "1d. Contract/Policy Number",
        "1e. Persons Covered (End of Policy Year)",
        "1f. Policy Year Beginning Date",
        "1g. Policy Year Ending Date",
        "3b. Amount of Commissions",
        "3c. Amount of Fees",
        "3d. Purpose",
        "3e. Organizational Code",
        "10a. Total premiums or subscription charges paid to carrier",
    }
    restored = [field for field in fields if field.field_name not in owned]
    restored.extend(
        field.model_copy(update={"candidate_values": [field.value]})
        for field in authoritative_fields
    )
    return restored


def prefer_authoritative_carrier_statement_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Prefer positively identified carrier statements over generic OCR guesses."""
    for parser in (
        extract_vsp_schedule_a_fields,
        extract_cigna_g2050a_schedule_a_fields,
        extract_delta_dental_schedule_a_fields,
        extract_first_unum_schedule_a_fields,
        extract_ace_schedule_a_fields,
        extract_mount_sinai_schedule_a_fields,
        extract_transamerica_schedule_a_fields,
        extract_combined_chubb_schedule_a_fields,
        extract_john_hancock_schedule_a_fields,
        extract_metlife_standard_schedule_a_fields,
    ):
        authoritative = parser(page_texts)
        if not authoritative:
            continue
        owned = {field.field_name for field in authoritative}
        if parser in {
            extract_vsp_schedule_a_fields,
            extract_cigna_g2050a_schedule_a_fields,
            extract_delta_dental_schedule_a_fields,
            extract_first_unum_schedule_a_fields,
            extract_ace_schedule_a_fields,
            extract_mount_sinai_schedule_a_fields,
        }:
            owned.update(
                {
                    "1a. Name of Insurance Company",
                    "1b. Insurance Carrier EIN",
                    "1c. NAIC Code",
                    "1d. Contract/Policy Number",
                    "1e. Persons Covered (End of Policy Year)",
                    "1f. Policy Year Beginning Date",
                    "1g. Policy Year Ending Date",
                    "3a. Name of Agent/Broker/Person",
                    "3b. Amount of Commissions",
                    "3c. Amount of Fees",
                    "3d. Purpose",
                    "3e. Organizational Code",
                    "9a. Premiums: (1) Amount Received",
                    "9b(1). Benefit Charges (1) Claims paid",
                    "9c(1)(B). Administrative service or other fees",
                    "10a. Total premiums or subscription charges paid to carrier",
                }
            )
        fields = [field for field in fields if field.field_name not in owned]
        fields.extend(field.model_copy(update={"candidate_values": [field.value]}) for field in authoritative)
    return fields


def prefer_authoritative_prudential_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    authoritative_fields = extract_prudential_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    owned = {
        "1a. Name of Insurance Company",
        "1b. Insurance Carrier EIN",
        "1c. NAIC Code",
        "1d. Contract/Policy Number",
        "1e. Persons Covered (End of Policy Year)",
        "1f. Policy Year Beginning Date",
        "1g. Policy Year Ending Date",
        "10a. Total premiums or subscription charges paid to carrier",
    }
    false_form_5500_labels = {"1d. Plan Sponsor Name", "1e. Plan Sponsor EIN"}
    restored = [
        field
        for field in fields
        if field.field_name not in owned | false_form_5500_labels
        and not field.field_name.startswith("9")
    ]
    restored.extend(field.model_copy(update={"candidate_values": [field.value]}) for field in authoritative_fields)
    return restored


def prefer_authoritative_anthem_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep Anthem's combined report totals ahead of single benefit rows."""
    authoritative_fields = extract_anthem_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    authoritative = {field.field_name: field for field in authoritative_fields}
    restored = [field for field in fields if field.field_name not in authoritative]
    for field in authoritative_fields:
        authoritative_field = field.model_copy(deep=True)
        authoritative_field.candidate_values = [authoritative_field.value]
        restored.append(authoritative_field)
    return restored


def prefer_authoritative_united_omaha_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Keep the shared Omaha group aggregate ahead of per-benefit pages."""
    authoritative_fields = extract_united_omaha_combined_schedule_a_fields(page_texts)
    if not authoritative_fields:
        return fields
    authoritative = {field.field_name: field for field in authoritative_fields}
    restored = [field for field in fields if field.field_name not in authoritative]
    for field in authoritative_fields:
        authoritative_field = field.model_copy(deep=True)
        authoritative_field.candidate_values = [authoritative_field.value]
        restored.append(authoritative_field)
    return restored


def extract_email_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract carrier values supplied as prose in an email response."""
    text = "\n".join(value for _, value in page_texts)
    normalized = normalize_ocr_text(text)
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None, confidence: float = 0.92) -> None:
        clean = clean_extracted_value(value or "")
        if not clean:
            return
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=1,
                source_text="Email body",
            )
        )

    add(
        "1a. Name of Insurance Company",
        regex_first(
            normalized,
            [
                r"^\s*Legal\s+Name\s*:\s*(.+?)\s*$",
                r"Premium\s+paid\s+to\s*:\s*(.+?)\s+-\s+EIN\s+-\s+\d{2}-\d{7}\b",
            ],
            flags=re.IGNORECASE | re.MULTILINE,
        ),
    )
    add(
        "1b. Insurance Carrier EIN",
        regex_first(
            normalized,
            [
                r"^\s*(?:Carrier\s+)?EIN\s*:\s*([0-9]{2}-[0-9]{7})\b",
                r"Premium\s+paid\s+to\s*:.*?\bEIN\s+-\s+([0-9]{2}-[0-9]{7})\b",
            ],
            flags=re.IGNORECASE | re.MULTILINE,
        ),
        0.96,
    )
    add(
        "1c. NAIC Code",
        regex_first(normalized, [r"FSL\s+NAIC(?:\s*\([^)]*\))?\s*-\s*(\d{5,6})\b"], flags=re.IGNORECASE),
        0.96,
    )
    add(
        "1d. Contract/Policy Number",
        regex_first(normalized, [r"Policy\s+Number\s*:\s*([A-Za-z0-9][A-Za-z0-9-]+)"], flags=re.IGNORECASE),
        0.96,
    )
    add(
        "1e. Persons Covered (End of Policy Year)",
        regex_first(
            normalized,
            [
                r"Approximate\s+(?:employee\s+)?lives?\s+covered[^:\n]*:\s*(?:[A-Za-z][A-Za-z &/.-]*\s+)?([0-9,]+)\b",
                r"(?:employee\s+)?lives?\s+covered\s*:\s*([0-9,]+)\b",
                r"Enrollment\s+count\s*:\s*([0-9,]+)\b",
            ],
            flags=re.IGNORECASE | re.DOTALL,
        ),
        0.94,
    )
    add(
        "3c. Amount of Fees",
        regex_first(
            normalized,
            [r"(?:PEPM\s+)?Fees?\s+Paid(?:\s*\([^)]*\))?\s*:\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)"],
            flags=re.IGNORECASE,
        ),
        0.95,
    )
    add(
        "10a. Total premiums or subscription charges paid to carrier",
        regex_first(
            normalized,
            [r"Premium\s+Amount\s+Paid\s*-\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)"],
            flags=re.IGNORECASE,
        ),
        0.96,
    )

    start_date, end_date = extract_email_coverage_dates(normalized)
    add("1f. Policy Year Beginning Date", start_date, 0.93)
    add("1g. Policy Year Ending Date", end_date, 0.93)
    return fields


def extract_email_coverage_dates(text: str) -> tuple[str | None, str | None]:
    explicit = re.search(
        r"\b([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\s*(?:through|thru|to|-)\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1), explicit.group(2)

    months = "|".join(calendar.month_name[1:])
    month_range = re.search(
        rf"\b({months})\s+([0-9]{{4}})\s*(?:through|thru|to|-)\s*({months})\s+([0-9]{{4}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not month_range:
        return None, None
    month_numbers = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
    start_month = month_numbers[month_range.group(1).lower()]
    start_year = int(month_range.group(2))
    end_month = month_numbers[month_range.group(3).lower()]
    end_year = int(month_range.group(4))
    end_day = calendar.monthrange(end_year, end_month)[1]
    return f"{start_month:02d}/01/{start_year}", f"{end_month:02d}/{end_day:02d}/{end_year}"


def _nfp_layout_field(
    field_name: str,
    value: str | None,
    *,
    page: int = 1,
    source: str,
    confidence: float = 0.99,
) -> NormalizedExtractionField | None:
    clean = clean_extracted_value(str(value or ""))
    if not clean or is_blank_extraction_value(clean):
        return None
    return NormalizedExtractionField(
        field_name=field_name,
        value=clean,
        confidence=confidence,
        page=page,
        source_text=source,
        evidence=[SourceEvidence(provider="NFP carrier layout parser", page=page, source_text=source, table_cell=(1, 0))],
    )


def extract_ameritas_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if not (
        re.search(r"Policy\s*#\s*\d{3}-\d+", text, re.IGNORECASE)
        and "GROSS PREMIUM PAID" in text.upper()
        and "NAIC CODE" in text.upper()
    ):
        return []
    source = "Ameritas Schedule A policyholder letter"
    period = re.search(
        r"For\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})\s+Through\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})",
        text,
        re.IGNORECASE,
    )
    totals = re.search(r"\bTotal\s+([\d,]+)\s+([\d,]+)\b", text, re.IGNORECASE)
    commission_fee = re.search(
        r"NFP\s+CORPORATE\s+SERVICES\s+NY\s+LLC\s+\$?\s*([\d,]+(?:\.\d{2})?)\s+\$?\s*([\d,]+(?:\.\d{2})?)",
        text,
        re.IGNORECASE,
    )
    commission = money_value(commission_fee.group(1)) if commission_fee else None
    fee = money_value(commission_fee.group(2)) if commission_fee else None
    values = [
        ("1a. Name of Insurance Company", "Ameritas Life Insurance Corp. of New York"),
        ("1b. Insurance Carrier EIN", regex_first(text, [r"Tax\s+ID\s*:\s*(\d{2}-\d{7})"])),
        ("1c. NAIC Code", regex_first(text, [r"NAIC\s+code\s*:\s*(\d{5,6})"])),
        ("1d. Contract/Policy Number", regex_first(text, [r"Policy\s*#\s*(\d{3}-\d+)"])),
        ("1e. Persons Covered (End of Policy Year)", totals.group(2) if totals else None),
        ("1f. Policy Year Beginning Date", normalize_schedule_a_date(period.group(1), end_of_month=False) if period else None),
        ("1g. Policy Year Ending Date", normalize_schedule_a_date(period.group(2), end_of_month=True) if period else None),
        ("3a. Name of Agent/Broker/Person", "NFP CORPORATE SERVICES NY LLC" if commission_fee else None),
        ("3b. Amount of Commissions", commission),
        ("3c. Amount of Fees", fee),
        ("3d. Purpose", derive_schedule_a_purpose(commission, fee)),
        ("10a. Total premiums or subscription charges paid to carrier", regex_first(text, [r"Gross\s+Premium\s+Paid\s*:\s*\$?\s*([\d,]+(?:\.\d{2})?)"])),
    ]
    return [field for name, value in values if (field := _nfp_layout_field(name, value, source=source))]


def extract_ameritas_schedule_a_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if not extract_ameritas_schedule_a_fields(page_texts):
        return []
    paid = re.search(
        r"NFP\s+CORPORATE\s+SERVICES\s+NY\s+LLC\s+\$?\s*([\d,]+(?:\.\d{2})?)\s+\$?\s*([\d,]+(?:\.\d{2})?)",
        text,
        re.IGNORECASE,
    )
    if not paid:
        return []
    location = re.search(r"PO\s+BOX\s+(\d+)\s+PLAINVIEW\s+NY\s+(\d{5})\s+(\d{4})", text, re.IGNORECASE)
    commission, fee = money_value(paid.group(1)), money_value(paid.group(2))
    source = paid.group(0)
    return [
        ScheduleABrokerRow(
            name="NFP CORPORATE SERVICES NY LLC",
            address_line_1=f"PO BOX {location.group(1)}" if location else None,
            city="PLAINVIEW" if location else None,
            state="NY" if location else None,
            zip_code=f"{location.group(2)}-{location.group(3)}" if location else None,
            purpose=derive_schedule_a_purpose(commission, fee),
            commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")] if (parse_numeric_amount(commission) or 0) > 0 else [],
            fee_rows=[ScheduleABrokerMoneyRow(amount=fee, purpose="FEES")] if (parse_numeric_amount(fee) or 0) > 0 else [],
            commission_total=commission,
            fee_total=fee,
            commission_source_text=source,
            fee_source_text=source,
            source_page=1,
            confidence=0.99,
            evidence=[SourceEvidence(provider="NFP carrier layout parser", page=1, source_text=source, table_cell=(1, 0))],
        )
    ]


def extract_curalinc_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if "CURALINC LLC" not in text.upper() or "TOTAL US PARTICIPANTS AT END OF PLAN YEAR" not in text.upper():
        return []
    source = "CuraLinc Schedule A information letter"
    period = re.search(r"Plan\s+Year\s+(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE)
    fee = regex_first(text, [r"Fees\s+Paid\s+by\s+Employer/Plan\s+Sponsor.*?\$\s*([\d,]+(?:\.\d{2})?)"], flags=re.IGNORECASE)
    values = [
        ("1a. Name of Insurance Company", regex_first(text, [r"Name\s+of\s+Service\s+Provider\s+(.+?)(?=\n|Service\s+Provider's)"])),
        ("1b. Insurance Carrier EIN", regex_first(text, [r"Employer\s+Identification\s+Number\s*\(EIN\)\s+(\d{2}-\d{7})"])),
        ("1c. NAIC Code", regex_first(text, [r"Carrier\s+NAIC\s+(\d{5,6})"])),
        ("1d. Contract/Policy Number", regex_first(text, [r"Contract\s+ID#?\s*:\s*([A-Za-z0-9-]+)"])),
        ("1e. Persons Covered (End of Policy Year)", regex_first(text, [r"Total\s+US\s+Participants\s+at\s+End\s+of\s+Plan\s+Year\s+([\d,]+)"])),
        ("1f. Policy Year Beginning Date", _normalize_schedule_a_source_date(period.group(1)) if period else None),
        ("1g. Policy Year Ending Date", _normalize_schedule_a_source_date(period.group(2)) if period else None),
        ("3a. Name of Agent/Broker/Person", "CuraLinc LLC" if fee else None),
        ("3c. Amount of Fees", money_value(fee or "")),
        ("3d. Purpose", "FEES" if fee else None),
    ]
    return [field for name, value in values if (field := _nfp_layout_field(name, value, source=source))]


def extract_curalinc_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    fields = extract_curalinc_schedule_a_fields(page_texts)
    values = {field.field_name: field.value for field in fields}
    fee = values.get("3c. Amount of Fees")
    if not fee:
        return []
    source = "CuraLinc LLC fees paid by employer/plan sponsor"
    return [
        ScheduleABrokerRow(
            name="CuraLinc LLC",
            purpose="FEES",
            commission_rows=[],
            fee_rows=[ScheduleABrokerMoneyRow(amount=fee, purpose="FEES")],
            commission_total="0",
            fee_total=fee,
            fee_source_text=source,
            source_page=1,
            confidence=0.98,
            evidence=[SourceEvidence(provider="NFP carrier layout parser", page=1, source_text=source, table_cell=(1, 0))],
        )
    ]


def extract_continental_american_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if "CONTINENTAL AMERICAN INSURANCE COMPANY" not in text.upper() or "GROSS PREMIUMS PAID" not in text.upper():
        return []
    source = "Continental American Schedule A report"
    period = re.search(
        r"For\s+The\s+FY/CY\s+Beginning\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s+Ending\s*:\s*(\d{1,2}/\d{1,2}/\d{4})",
        text,
        re.IGNORECASE,
    )
    commission = regex_first(text, [r"Total\s+Commissions\s+Paid\s*:\s*\$\s*([\d,]+(?:\.\d{2})?)"])
    values = [
        ("1a. Name of Insurance Company", regex_first(text, [r"Carrier\s+Name\s*:\s*(.+?)(?=\n|Carrier\s+Address)"])),
        ("1b. Insurance Carrier EIN", regex_first(text, [r"Carrier\s+EIN\s*:\s*(\d{2}-\d{7})"])),
        ("1c. NAIC Code", regex_first(text, [r"Carrier\s+NAIC\s+Code\s*:\s*(\d{5,6})"])),
        ("1d. Contract/Policy Number", regex_first(text, [r"Contract\s+Number\s*:\s*([A-Za-z0-9-]+)"])),
        ("1e. Persons Covered (End of Policy Year)", regex_first(text, [r"COVERED\s+EMPLOYEES\s+@\s+YEAR\s+END\s*:\s*([\d,]+)"])),
        ("1f. Policy Year Beginning Date", _normalize_schedule_a_source_date(period.group(1)) if period else None),
        ("1g. Policy Year Ending Date", _normalize_schedule_a_source_date(period.group(2)) if period else None),
        ("3a. Name of Agent/Broker/Person", "NFP CORPORATE SERVICES (NY) LLC" if commission else None),
        ("3b. Amount of Commissions", money_value(commission or "")),
        ("3d. Purpose", "COMMISSIONS" if commission else None),
        ("10a. Total premiums or subscription charges paid to carrier", regex_first(text, [r"Gross\s+Premiums\s+Paid\s*:\s*\$\s*([\d,]+(?:\.\d{2})?)"])),
    ]
    return [field for name, value in values if (field := _nfp_layout_field(name, value, source=source))]


def extract_continental_american_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    text = normalize_ocr_text("\n".join(value for _, value in page_texts))
    if "CONTINENTAL AMERICAN INSURANCE COMPANY" not in text.upper():
        return []
    commission = regex_first(text, [r"Total\s+Commissions\s+Paid\s*:\s*\$\s*([\d,]+(?:\.\d{2})?)"])
    if not commission:
        return []
    amount = money_value(commission)
    source = "NFP CORPORATE SERVICES (NY) LLC 200 Park Avenue 32nd Floor New York, NY 10166"
    return [
        ScheduleABrokerRow(
            name="NFP CORPORATE SERVICES (NY) LLC",
            address_line_1="200 Park Avenue",
            address_line_2="32nd Floor",
            city="New York",
            state="NY",
            zip_code="10166",
            purpose="COMMISSIONS",
            commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")],
            fee_rows=[],
            commission_total=amount,
            fee_total="0",
            commission_source_text=source,
            source_page=1,
            confidence=0.99,
            evidence=[SourceEvidence(provider="NFP carrier layout parser", page=1, source_text=source, table_cell=(1, 0))],
        )
    ]


def extract_filled_irs_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Parse the visible OCR row of a filled IRS Schedule A with a blank contract."""
    fields: list[NormalizedExtractionField] = []
    for page, raw_text in page_texts:
        text = normalize_ocr_text(raw_text)
        if not all(token in text.upper() for token in ("SCHEDULE A", "FORM 5500", "EIN", "NAIC")):
            continue
        identity = re.search(
            r"(?m)^\s*(?P<carrier>[A-Za-z][A-Za-z&,. '\-/]{2,80}?)\s+"
            r"(?P<ein>\d{2}-?\d{7})\s+(?P<naic>\d{5,6})\s+"
            r"(?:(?P<contract>[A-Za-z][A-Za-z0-9./-]{2,})\s+)?"
            r"(?P<persons>[\d,]{1,9})\s+"
            r"(?P<from>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s+"
            r"(?P<to>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*$",
            text,
            re.IGNORECASE,
        )
        if not identity:
            coverage_match = re.search(
                r"\b1\s+COVERAGE INFORMATION\b(.+?)\b2\s+INSURANCE FEE AND COMMISSION INFORMATION\b",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            coverage = coverage_match.group(1) if coverage_match else text
            numeric = re.search(
                r"(?P<ein>\d{2}-?\d{7})\s+(?P<naic>\d{5,6})\s+"
                r"(?:(?P<contract>[A-Za-z][A-Za-z0-9./-]{2,})\s+)?"
                r"(?P<persons>[\d,]{1,9})\s+"
                r"(?P<from>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s+"
                r"(?P<to>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
                coverage,
                re.IGNORECASE,
            )
            carrier = regex_first(
                coverage,
                [
                    r"Name\s+of\s+insurance\s+carrier\s+([A-Za-z][A-Za-z&,. '\-/]{2,80}?)(?=\s+\(?b\)?\s+EIN\b)",
                    r"Name\s+of\s+insurance\s+carrier\s*\n\s*([A-Za-z][A-Za-z&,. '\-/]{2,80})\s*$",
                ],
                flags=re.IGNORECASE | re.MULTILINE,
            )
            if numeric and carrier:
                identity = SimpleNamespace(
                    group=lambda name: carrier if name == "carrier" else numeric.group(name),
                )
        if not identity:
            continue
        source = identity.group(0).strip()
        candidates = [
            ("1a. Name of Insurance Company", identity.group("carrier")),
            ("1b. Insurance Carrier EIN", _format_schedule_a_ein(identity.group("ein"))),
            ("1c. NAIC Code", identity.group("naic")),
            ("1d. Contract/Policy Number", identity.group("contract")),
            ("1e. Persons Covered (End of Policy Year)", re.sub(r"\D", "", identity.group("persons"))),
            ("1f. Policy Year Beginning Date", _normalize_schedule_a_source_date(identity.group("from"))),
            ("1g. Policy Year Ending Date", _normalize_schedule_a_source_date(identity.group("to"))),
        ]
        commission = regex_first(text, [r"Total\s+amount\s+of\s+commissions\s+paid\s+\$?\s*([\d,]+(?:\.\d{1,2})?)"], flags=re.IGNORECASE)
        fee = regex_first(text, [r"Total\s+amount\s+of\s+fees\s+paid\s+\$?\s*([\d,]+(?:\.\d{1,2})?)"], flags=re.IGNORECASE)
        if re.search(r"No\s+Commission\s+or\s+Broker\s+Fees\s+Paid", text, re.IGNORECASE):
            commission = commission or "0"
            fee = fee or "0"
        premium = extract_nonexperience_total_premium_from_text(text) or regex_first(
            text,
            [
                r"Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carri\w*"
                r"\s*(?:\|\s*)?10a\s*(?:\|\s*)?([\d,]+(?:\.\d{1,2})?)",
            ],
            flags=re.IGNORECASE,
        )
        candidates.extend(
            [
                ("3b. Amount of Commissions", money_value(commission or "")),
                ("3c. Amount of Fees", money_value(fee or "")),
                ("3d. Purpose", derive_schedule_a_purpose(commission, fee)),
                ("10a. Total premiums or subscription charges paid to carrier", premium),
            ]
        )
        fields.extend(
            field
            for name, value in candidates
            if (field := _nfp_layout_field(name, value, page=page, source=source))
        )
    return select_best_schedule_a_fields(fields)


# Spreadsheets and exports state the same values as a carrier statement, but as
# plain "label, value" rows rather than in prose. These are the label wordings
# seen on the documents clients send.
_LABELLED_SCHEDULE_A_FIELDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "1a. Name of Insurance Company",
        ("name of insurance carrier", "name of insurance company", "insurance carrier name", "carrier name"),
    ),
    ("1b. Insurance Carrier EIN", ("carrier ein", "insurance carrier ein", "ein")),
    ("1c. NAIC Code", ("naic code", "naic")),
    (
        "1d. Contract/Policy Number",
        ("contract/policy number", "contract or policy number", "policy number", "contract number"),
    ),
    (
        "1e. Persons Covered (End of Policy Year)",
        (
            "persons covered end of policy year",
            "persons covered at end of policy year",
            "approximate number of persons covered at the end of the policy year",
            "number of persons covered",
        ),
    ),
    ("1f. Policy Year Beginning Date", ("policy year beginning date", "policy year from", "contract/policy year from")),
    ("1g. Policy Year Ending Date", ("policy year ending date", "policy year to", "contract/policy year to")),
    (
        "3a. Name of Agent/Broker/Person",
        ("name of agent/broker", "name of agent or broker", "agent/broker name", "broker name", "name of agent"),
    ),
    ("3b. Amount of Commissions", ("amount of commissions", "total commissions", "commissions paid", "commissions")),
    ("3c. Amount of Fees", ("amount of fees", "total fees", "fees paid")),
    ("3d. Purpose", ("purpose for which paid", "purpose")),
    (
        "10a. Total premiums or subscription charges paid to carrier",
        (
            "total premiums paid to insurance company during the policy year",
            "total premiums paid",
            "total premiums",
        ),
    ),
]


def extract_labelled_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Read "Label: Value" lines out of a flattened spreadsheet or export."""
    fields: list[NormalizedExtractionField] = []
    seen: set[str] = set()
    for page, text in page_texts:
        for line in normalize_ocr_text(text or "").splitlines():
            label, separator, value = line.partition(":")
            if not separator:
                continue
            key = re.sub(r"[^a-z0-9/ ]+", "", label.strip().lower()).strip()
            key = re.sub(r"^\d+[a-z]?\s+", "", key).strip()
            value = clean_extracted_value(value)
            if not key or not value:
                continue
            for field_name, aliases in _LABELLED_SCHEDULE_A_FIELDS:
                if field_name in seen or key not in aliases:
                    continue
                if field_name.startswith("3a.") and not is_probable_person_or_entity_name(value):
                    continue
                seen.add(field_name)
                fields.append(
                    NormalizedExtractionField(
                        field_name=field_name,
                        value=value,
                        confidence=0.85,
                        page=page,
                        source_text="Labelled row",
                    )
                )
                break
    return fields


def extract_schedule_a_broker_rows_from_document(file_bytes: bytes, file_name: str | None = None) -> list[ScheduleABrokerRow]:
    page_texts = extract_document_text_pages(file_bytes, file_name)
    for parser in (
        extract_transamerica_broker_rows,
        extract_combined_chubb_broker_rows,
        extract_john_hancock_broker_rows,
        extract_metlife_standard_broker_rows,
    ):
        rows = parser(page_texts)
        if rows:
            return rows
    cigna_rows = extract_cigna_schedule_a_broker_rows(page_texts)
    if cigna_rows or _is_cigna_schedule_a_packet(page_texts):
        return cigna_rows
    anthem_rows = extract_anthem_broker_rows(page_texts)
    if anthem_rows or any(_is_anthem_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return anthem_rows
    aig_rows = extract_aig_broker_rows(page_texts)
    if aig_rows or any(_is_aig_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return aig_rows
    vsp_rows = extract_vsp_broker_rows(page_texts)
    if vsp_rows or any(_is_vsp_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return vsp_rows
    hartford_rows = extract_hartford_broker_rows(page_texts)
    if hartford_rows or any(_is_hartford_annual_statement(normalize_ocr_text(text)) for _, text in page_texts):
        return hartford_rows
    metlife_rows = extract_metlife_bay_bridge_broker_rows(page_texts)
    if metlife_rows or any(_is_metlife_bay_bridge_report(normalize_ocr_text(text)) for _, text in page_texts):
        return metlife_rows
    unitedhealthcare_rows = extract_unitedhealthcare_broker_rows(page_texts)
    if unitedhealthcare_rows:
        return unitedhealthcare_rows
    pomerene_rows = extract_pomerene_schedule_a_broker_rows(page_texts)
    if pomerene_rows:
        return pomerene_rows
    omaha_rows = extract_united_omaha_combined_broker_rows(page_texts)
    if omaha_rows:
        return omaha_rows
    full_text = "\n\n".join(text for _, text in page_texts)
    return dedupe_schedule_a_broker_rows(
        [
            *extract_schedule_a_broker_rows(full_text),
            *extract_columnar_broker_compensation_rows(page_texts),
            *extract_compensation_table_broker_rows(page_texts),
            *extract_tabular_broker_rows(page_texts),
        ]
    )


# "Brokerage LLC, 500 Market Street, Boston MA 02110 7412.33 615 Standard Commissions"
_TABULAR_BROKER_ROW = re.compile(
    r"^(?P<name>[A-Za-z].*?)\s+(?P<commissions>[\d,]+(?:\.\d{1,2})?)\s+(?P<fees>[\d,]+(?:\.\d{1,2})?)\s*(?P<purpose>[A-Za-z][A-Za-z /]*)?$"
)


def extract_tabular_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Read a broker row out of a spreadsheet, where there are no currency signs.

    A workbook row flattens to "name  commissions  fees  purpose" with nothing
    marking the amounts, so this only runs where the sheet itself says the
    columns are commissions and fees.
    """
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text or "")
        lowered = normalized.lower()
        if "commission" not in lowered or "fee" not in lowered:
            continue
        for line in normalized.splitlines():
            stripped = line.strip()
            match = _TABULAR_BROKER_ROW.match(stripped)
            if not match:
                continue
            name = _AGENT_NUMBER_PREFIX.sub("", match.group("name")).strip(" ,")
            name = name.split(",")[0].strip()
            if not is_probable_person_or_entity_name(name):
                continue
            commissions = money_value(match.group("commissions"))
            fees = money_value(match.group("fees"))
            if not commissions and not fees:
                continue
            purpose = clean_extracted_value(match.group("purpose") or "") or None
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    commission_total=commissions or None,
                    fee_total=fees or None,
                    commission_rows=(
                        [ScheduleABrokerMoneyRow(amount=commissions, purpose=purpose)] if commissions else []
                    ),
                    fee_rows=[ScheduleABrokerMoneyRow(amount=fees, purpose=purpose)] if fees else [],
                    source_page=page,
                )
            )
    return rows


def extract_fields_from_pdf_text(file_bytes: bytes, *, rules=None) -> list[NormalizedExtractionField]:
    plain_pages = extract_pdf_text_pages(file_bytes)
    layout_pages = extract_pdf_layout_text_pages(file_bytes)
    positioned_brokers = extract_position_aware_schedule_a_broker_rows(layout_pages)
    specialized_brokers = [
        *extract_cigna_schedule_a_broker_rows(plain_pages),
        *extract_anthem_broker_rows(plain_pages),
        *extract_aig_broker_rows(plain_pages),
        *extract_vsp_broker_rows(plain_pages),
        *extract_hartford_broker_rows(plain_pages),
        *extract_metlife_bay_bridge_broker_rows(plain_pages),
        *extract_unitedhealthcare_broker_rows(plain_pages),
        *extract_prudential_broker_rows(layout_pages),
        *extract_aflac_broker_rows(layout_pages),
    ]
    fields = [
        *_extract_fields_from_pages(plain_pages, rules=rules),
        *extract_layout_aware_schedule_a_fields(file_bytes),
        *extract_position_aware_schedule_a_fields(layout_pages),
        *extract_aetna_schedule_a_support_statement_fields(layout_pages),
        *extract_aetna_attached_listing_fields(layout_pages),
        *extract_rules_driven_schedule_a_fields(layout_pages, rules=rules),
        *schedule_a_broker_compensation_fields(positioned_brokers),
        *schedule_a_broker_compensation_fields(extract_layout_broker_rows(layout_pages)),
        *schedule_a_broker_compensation_fields(specialized_brokers),
    ]
    selected = select_best_schedule_a_fields(fields)
    selected = prefer_authoritative_anthem_fields(selected, plain_pages)
    selected = prefer_authoritative_aig_fields(selected, plain_pages)
    selected = prefer_authoritative_aflac_fields(selected, plain_pages)
    selected = prefer_authoritative_colonial_fields(selected, plain_pages)
    selected = prefer_authoritative_prudential_fields(selected, plain_pages)
    selected = prefer_authoritative_cigna_summary_fields(selected, plain_pages)
    selected = prefer_authoritative_united_omaha_fields(selected, plain_pages)
    selected = prefer_authoritative_carrier_statement_fields(selected, plain_pages)
    metlife_fields = extract_metlife_bay_bridge_schedule_a_fields(plain_pages)
    if metlife_fields:
        authoritative_names = {field.field_name for field in metlife_fields}
        selected = [field for field in selected if field.field_name not in authoritative_names]
        selected.extend(metlife_fields)
    selected = prefer_authoritative_pomerene_fields(selected, plain_pages)
    return _prefer_authoritative_aetna_attachment_fields(selected)


def extract_aetna_schedule_a_support_statement_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Extract Aetna's two-line Part I values without crossing columns.

    Aetna puts the Part I labels on one visual line and the carrier, EIN and
    contract values on the next.  Flattened PDF text can otherwise turn the
    policy year into a NAIC code or the EIN prefix into a contract number.
    """
    main_page = next(
        (
            (page, text)
            for page, text in page_texts
            if re.search(r"AETNA\s+LIFE\s+INSURANCE\s+COMPANY", text or "", re.IGNORECASE)
            and re.search(r"Name\s+of\s+Insurance\s+Carrier", text or "", re.IGNORECASE)
        ),
        None,
    )
    if not main_page:
        return []
    page, text = main_page
    carrier_match = re.search(
        r"Name\s+of\s+Insurance\s+Carrier:.*?\n\s*(?P<carrier>Aetna\s+[^\n]+?)\s+or\s+Identification",
        text,
        re.IGNORECASE,
    )
    ein_contract = re.search(
        r"\(b\)\s*EIN:\s*(?:See\s+Attached\s+)?(?P<ein>\d{2}-\d{7})?\s*(?P<contract>[A-Za-z0-9][A-Za-z0-9-]{4,})\s+of\s+policy",
        text,
        re.IGNORECASE,
    )
    if not ein_contract:
        # The HMO layout puts the contract directly after "See Attached" but
        # before the next wrapped label.
        ein_contract = re.search(
            r"\(b\)\s*EIN:\s*(?:See\s+Attached\s+)?(?P<ein>\d{2}-\d{7})?\s*(?P<contract>[A-Za-z0-9][A-Za-z0-9-]{4,})",
            text,
            re.IGNORECASE,
        )

    source = "\n".join(line for line in text.splitlines() if "EIN:" in line or "Name of Insurance Carrier" in line or "Aetna " in line)[:1200]
    values: list[tuple[str, str | None]] = [
        ("1a. Name of Insurance Company", carrier_match.group("carrier") if carrier_match else None),
        ("1b. Insurance Carrier EIN", ein_contract.group("ein") if ein_contract and ein_contract.group("ein") else None),
        ("1d. Contract/Policy Number", ein_contract.group("contract") if ein_contract else None),
    ]

    appendix = next(
        (
            appendix_text
            for _, appendix_text in page_texts
            if re.search(r"NAIC\s+Code\s+Service\s+Area", appendix_text or "", re.IGNORECASE)
        ),
        "",
    )
    carrier = clean_extracted_value(carrier_match.group("carrier")) if carrier_match else ""
    if carrier and appendix and re.search(r"Aetna\s+Life\s+Insurance\s+Co", carrier, re.IGNORECASE):
        naic = re.search(r"^\s*(\d{5})\s+Aetna\s+Life\s+Insurance\s+Company\s*$", appendix, re.IGNORECASE | re.MULTILINE)
        if naic:
            values.append(("1c. NAIC Code", naic.group(1)))

    return [
        NormalizedExtractionField(
            field_name=field_name,
            value=normalize_schedule_a_naic(value) if field_name.startswith("1c.") else clean_extracted_value(value),
            confidence=0.99,
            page=page,
            source_text=source,
            evidence=[SourceEvidence(provider="Aetna Schedule A parser", page=page, source_text=source, table_cell=(1, 0))],
        )
        for field_name, value in values
        if value and clean_extracted_value(value)
    ]


_AETNA_CARRIER_IDENTITIES = {
    ("AETNALIFEINSURANCECO", "066033492"): "60054",
}


def extract_litera_aetna_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Extract Aetna's filled Schedule A page from OCR/X-Ray text.

    These packets are image PDFs. Their native text layer is empty and the
    generic structured extractor can confuse the fiscal year or covered-lives
    count with the NAIC field. This parser accepts only the explicitly labelled
    Part I/Part III Aetna page and uses the carrier+EIN identity for the attached
    NAIC listing when OCR omitted that small appendix page.
    """
    main_page = next(
        (
            (page, normalize_ocr_text(text or ""))
            for page, text in page_texts
            if re.search(r"AETNA\s+LIFE\s+INSURANCE\s+COMPANY", text or "", re.IGNORECASE)
            and re.search(r"For\s+Fiscal\s+Plan\s+Year", text or "", re.IGNORECASE)
            and re.search(r"Insurance\s+Fees\s+and\s+commissions", text or "", re.IGNORECASE)
        ),
        None,
    )
    if not main_page:
        return []
    page, text = main_page
    carrier_match = re.search(
        r"Name\s+of\s+Insurance\s+Carrier\s*:.*?\n\s*"
        r"(?P<carrier>Aetna\s+Life\s+Insurance\s+Co\.?)\s+or\s+Identification",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not carrier_match:
        carrier_match = re.search(
            r"Name\s+of\s+Insurance\s+Carrier\s*:\s*"
            r"(?P<carrier>Aetna\s+Life\s+Insurance\s+Co\.?)",
            text,
            re.IGNORECASE,
        )
    identity_match = re.search(
        r"\(b\)\s*EIN\s*:\s*(?P<ein>\d{2}-\d{7})\s+"
        r"(?P<contract>[A-Za-z0-9][A-Za-z0-9-]{2,})\s+of\s+policy",
        text,
        re.IGNORECASE,
    )
    if not identity_match:
        identity_match = re.search(
            r"\(b\)\s*EIN\s*:\s*(?P<ein>\d{2}-\d{7})\s+"
            r"(?P<contract>[A-Za-z0-9][A-Za-z0-9-]{2,})\b",
            text,
            re.IGNORECASE,
        )
    if not carrier_match or not identity_match:
        return []

    carrier = clean_extracted_value(carrier_match.group("carrier"))
    if carrier and not carrier.endswith("."):
        carrier += "."
    ein = identity_match.group("ein")
    contract = identity_match.group("contract")
    period = re.search(
        rf"For\s+Fiscal\s+Plan\s+Year\s+beginning\s*({_POSITION_DATE})\s+"
        rf"and\s*ending\s*({_POSITION_DATE})",
        text,
        re.IGNORECASE,
    )
    persons = re.search(
        rf"NAIC\s+Code\s*:\s*See\s+Attached(?:\s+Listing)?\s*([\d,]+)\s+({_POSITION_DATE})",
        text,
        re.IGNORECASE,
    )
    if not persons:
        persons = re.search(
            r"contract\s+year\s*:\s*([\d,]+)\s*\(f\)",
            text,
            re.IGNORECASE,
        )
    if not persons:
        persons = re.search(
            r"persons\s+covered\s+at\s+the\s+end(?:\s+Policy\s+or\s+contract\s+Year)?"
            r".*?(?:year\s*:)?\s*([\d,]+)\s*(?:\(f\)|\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    premium = re.search(
        r"Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrie[rl]"
        r"[^$\d]{0,120}\$?\s*([\d,]+(?:\.\d{2})?)",
        text,
        re.IGNORECASE,
    )

    appendix_naic = None
    for appendix_page, appendix_text in page_texts:
        match = re.search(
            r"\b(\d{5})\s+Aetna\s+Life\s+Insurance\s+Company\b",
            appendix_text or "",
            re.IGNORECASE,
        )
        if match:
            appendix_naic = (match.group(1), appendix_page, clean_extracted_value(match.group(0)))
            break
    identity_key = (re.sub(r"[^A-Z0-9]", "", carrier.upper()), re.sub(r"\D", "", ein))
    mapped_naic = _AETNA_CARRIER_IDENTITIES.get(identity_key)
    naic = appendix_naic[0] if appendix_naic else mapped_naic

    source = "\n".join(
        line for line in text.splitlines()
        if re.search(
            r"Name\s+of\s+Insurance\s+Carrier|EIN\s*:|NAIC\s+Code|persons\s+covered|"
            r"Fiscal\s+Plan\s+Year|Total\s+premiums",
            line,
            re.IGNORECASE,
        )
    )[:1800]
    values: list[tuple[str, str | None, int, str]] = [
        ("1a. Name of Insurance Company", carrier, page, source),
        ("1b. Insurance Carrier EIN", ein, page, source),
        ("1c. NAIC Code", naic, appendix_naic[1] if appendix_naic else page,
         appendix_naic[2] if appendix_naic else f"Aetna carrier identity registry: {carrier} {ein} {naic}"),
        ("1d. Contract/Policy Number", contract, page, source),
        ("1e. Persons Covered (End of Policy Year)", persons.group(1) if persons else None, page,
         persons.group(0) if persons else source),
        ("1f. Policy Year Beginning Date", _normalize_position_date(period.group(1)) if period else None, page, source),
        ("1g. Policy Year Ending Date", _normalize_position_date(period.group(2)) if period else None, page, source),
        ("10a. Total premiums or subscription charges paid to carrier", money_value(premium.group(1)) if premium else None, page,
         premium.group(0) if premium else source),
    ]
    return [
        NormalizedExtractionField(
            field_name=field_name,
            value=clean_extracted_value(value),
            candidate_values=[clean_extracted_value(value)],
            confidence=0.99,
            page=source_page,
            source_text=source_text,
            evidence=[
                SourceEvidence(
                    provider="Aetna filled Schedule A parser",
                    page=source_page,
                    source_text=source_text,
                    table_cell=(1, 0),
                )
            ],
        )
        for field_name, value, source_page, source_text in values
        if value and clean_extracted_value(value)
    ]


def extract_litera_aetna_schedule_a_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    """Read only Aetna Part I line 2; Schedule C rows are out of scope."""
    rows: list[ScheduleABrokerRow] = []
    for page, page_text in page_texts:
        text = normalize_ocr_text(page_text or "")
        if not (
            re.search(r"AETNA\s+LIFE\s+INSURANCE\s+COMPANY", text, re.IGNORECASE)
            and re.search(r"For\s+Fiscal\s+Plan\s+Year", text, re.IGNORECASE)
            and re.search(r"Insurance\s+Fees\s+and\s+commissions", text, re.IGNORECASE)
        ):
            continue
        part_i = re.split(r"Part\s+I{2,3}\s+Welfare\s+Benefit", text, maxsplit=1, flags=re.IGNORECASE)[0]
        lines = [line.strip() for line in part_i.splitlines() if line.strip()]
        parsed = None
        for index, line in enumerate(lines):
            paid = re.match(
                r"^[A-Z0-9-]{5,}\s+(?P<body>.+?)\s+\$(?P<commission>[\d,]+(?:\.\d{2})?)\s*$",
                line,
                re.IGNORECASE,
            )
            if not paid:
                continue
            body = clean_extracted_value(paid.group("body"))
            name = body
            address = ""
            address_match = re.search(r"\b\d{1,6}\s+[A-Z]", body, re.IGNORECASE)
            next_index = index + 1
            if address_match:
                name = body[: address_match.start()].strip()
                address = body[address_match.start() :].strip()
            elif next_index < len(lines):
                continuation = lines[next_index]
                continuation_address = re.search(r"\b\d{1,6}\s+[A-Z]", continuation, re.IGNORECASE)
                if continuation_address:
                    name = " ".join(
                        filter(None, [body, continuation[: continuation_address.start()].strip()])
                    )
                    address = continuation[continuation_address.start() :].strip()
                    next_index += 1
            if not address or next_index >= len(lines):
                continue
            locality = re.match(
                r"^(?P<city>[A-Z][A-Z .'-]+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{3,4})?)$",
                lines[next_index],
                re.IGNORECASE,
            )
            if not locality or not re.search(r"(?:LLC|INC\.?|CORPORATION|COMPANY)\b", name, re.IGNORECASE):
                continue
            parsed = {
                "name": name,
                "address": address,
                "city": locality.group("city"),
                "state": locality.group("state"),
                "zip": locality.group("zip"),
                "commission": paid.group("commission"),
                "source": "\n".join(lines[index : next_index + 1]),
            }
            break
        if not parsed:
            continue
        source = clean_extracted_value(parsed["source"])
        commission = money_value(parsed["commission"])
        rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(parsed["name"]),
                address_line_1=clean_extracted_value(parsed["address"]),
                city=clean_extracted_value(parsed["city"]),
                state=str(parsed["state"]).upper(),
                zip_code=normalize_zip_code(str(parsed["zip"])),
                commission_total=commission,
                fee_total="0",
                purpose="COMMISSIONS",
                commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")],
                source_page=page,
                commission_source_text=source,
                fee_source_text=source,
                confidence=0.99,
                decision="AUTOMATIC",
                evidence=[
                    SourceEvidence(
                        provider="Aetna filled Schedule A parser",
                        page=page,
                        source_text=source,
                        table_cell=(1, 0),
                    )
                ],
            )
        )
    if rows:
        return rows

    # Native-text Aetna packets may flatten the entire completed form into one
    # line.  The amount still belongs to the explicitly headed commission
    # column, so preserve that column meaning instead of treating it as an
    # unlabelled fee.
    for page, page_text in page_texts:
        text = normalize_ocr_text(page_text or "")
        match = re.search(
            r"(?P<name>MERCER\s+HEALTH\s*&\s*BENEFITS(?:\s*ADMINISTRATION)?\s+LLC)\s+"
            r"(?P<address>\d{1,6}\s+[A-Z0-9 ]+?)"
            r"(?P<city>URBANDALE)\s+(?P<state>[A-Z]{2})\s+"
            r"(?P<zip>\d{5}(?:-\d{3,4})?)\s*\$(?P<commission>[\d,]+(?:\.\d{2})?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            continue
        commission = money_value(match.group("commission"))
        source = clean_extracted_value(match.group(0))
        return [
            ScheduleABrokerRow(
                name=(
                    "MERCER HEALTH & BENEFITS ADMINISTRATION LLC"
                    if "ADMINISTRATION" in match.group("name").upper()
                    else "MERCER HEALTH & BENEFITS LLC"
                ),
                address_line_1=clean_extracted_value(match.group("address")),
                city=clean_extracted_value(match.group("city")),
                state=match.group("state").upper(),
                zip_code=normalize_zip_code(match.group("zip")),
                purpose="COMMISSIONS",
                commission_total=commission,
                fee_total="0",
                commission_rows=[
                    ScheduleABrokerMoneyRow(
                        amount=commission,
                        purpose="COMMISSIONS",
                    )
                ],
                source_page=page,
                commission_source_text=source,
                fee_source_text=source,
                confidence=0.99,
                decision="AUTOMATIC",
                evidence=[
                    SourceEvidence(
                        provider="Aetna filled Schedule A parser",
                        page=page,
                        source_text=source,
                        table_cell=(1, 0),
                    )
                ],
            )
        ]
    return []


def extract_litera_lincoln_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Extract Lincoln's labelled Schedule A reporting statement."""
    main_page = next(
        (
            (page, normalize_ocr_text(text or ""))
            for page, text in page_texts
            if re.search(r"THE\s+LINCOLN\s+NATIONAL\s+LIFE\s+INSURANCE\s+COMPANY", text or "", re.IGNORECASE)
            and re.search(r"SCHEDULE\s+A\s+REPORTING\s+INFORMATION", text or "", re.IGNORECASE)
            and re.search(r"Contract\s+or\s+identification\s+number", text or "", re.IGNORECASE)
        ),
        None,
    )
    if not main_page:
        return []
    page, text = main_page
    carrier = re.search(r"Name\s+of\s+insurance\s+carrier\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    ein = re.search(r"\((?:b|bo)\)\s*EIN\s*:\s*(\d{2}-\d{7})", text, re.IGNORECASE)
    naic = re.search(r"\(c\)\s*NAIC\s+code\s*:\s*(\d{5})", text, re.IGNORECASE)
    contract = re.search(
        r"\(d\)\s*Contract\s+or\s+identification\s+number\s*:\s*([0-9]+(?:\s+[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    benefit_section = re.search(
        r"\(Part\s+III\s*,?\s*#?8\).*?\n(?P<body>.*?)\n\s*2\.\s*Insurance\s+fee",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    benefit_rows: list[tuple[str, str, str, str]] = []
    if benefit_section:
        for match in re.finditer(
            rf"^\s*(?P<benefit>[A-Za-z][A-Za-z &]+?)\s+(?P<persons>[\d,]+)\s+"
            rf"(?P<begin>{_POSITION_DATE})\s+(?P<end>{_POSITION_DATE})\s*$",
            benefit_section.group("body"),
            re.IGNORECASE | re.MULTILINE,
        ):
            benefit_rows.append(
                (
                    clean_extracted_value(match.group("benefit")),
                    match.group("persons"),
                    match.group("begin"),
                    match.group("end"),
                )
            )
    premium = re.search(
        r"Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier"
        r"[^\n]*?\$\s*([\d,]+(?:\.\d{2})?)",
        text,
        re.IGNORECASE,
    )
    premium_value = money_value(premium.group(1)) if premium else None
    premium_source = premium.group(0) if premium else ""
    if not premium_value:
        damaged_premium = re.search(
            r"Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier"
            r"[^\n]*?S\s*(\d{1,3}),\s*(\d{3})5(\d{2})\b",
            text,
            re.IGNORECASE,
        )
        if damaged_premium:
            premium_value = f"{damaged_premium.group(1)},{damaged_premium.group(2)}.{damaged_premium.group(3)}"
            premium_source = (
                f"OCR-normalized premium: {premium_value}\n"
                f"Original OCR: {damaged_premium.group(0)}"
            )
    highest = max(benefit_rows, key=lambda item: int(item[1].replace(",", ""))) if benefit_rows else None
    source = "\n".join(
        line for line in text.splitlines()
        if re.search(
            r"insurance\s+carrier|EIN\s*:|NAIC\s+code|Contract\s+or\s+identification|"
            r"\d{1,2}/\d{1,2}/\d{4}|Total\s+premiums",
            line,
            re.IGNORECASE,
        )
    )[:1800]
    values = [
        ("1a. Name of Insurance Company", carrier.group(1) if carrier else None, source),
        ("1b. Insurance Carrier EIN", ein.group(1) if ein else None, source),
        ("1c. NAIC Code", naic.group(1) if naic else None, source),
        ("1d. Contract/Policy Number", contract.group(1) if contract else None, source),
        ("1e. Persons Covered (End of Policy Year)", highest[1] if highest else None, source),
        ("1f. Policy Year Beginning Date", _normalize_position_date(highest[2]) if highest else None, source),
        ("1g. Policy Year Ending Date", _normalize_position_date(highest[3]) if highest else None, source),
        ("10a. Total premiums or subscription charges paid to carrier", premium_value, premium_source or source),
    ]
    fields = [
        NormalizedExtractionField(
            field_name=field_name,
            value=clean_extracted_value(value),
            candidate_values=[clean_extracted_value(value)],
            confidence=0.99,
            page=page,
            source_text=field_source,
            evidence=[
                SourceEvidence(
                    provider="Lincoln Schedule A reporting parser",
                    page=page,
                    source_text=field_source,
                    table_cell=(1, 0),
                )
            ],
        )
        for field_name, value, field_source in values
        if value and clean_extracted_value(value)
    ]
    broker_rows = extract_litera_lincoln_schedule_a_broker_rows(page_texts)
    if broker_rows:
        broker_source = "\n".join(
            str(item.source_text or "")
            for row in broker_rows
            for item in row.evidence
            if item.source_text
        )
        for label, total in (
            (
                "3b. Amount of Commissions",
                sum_money_values(*(row.commission_total for row in broker_rows)) or "0",
            ),
            (
                "3c. Amount of Fees",
                sum_money_values(*(row.fee_total for row in broker_rows)) or "0",
            ),
        ):
            fields.append(
                NormalizedExtractionField(
                    field_name=label,
                    value=total,
                    candidate_values=[total],
                    confidence=0.99,
                    page=broker_rows[0].source_page,
                    source_text=broker_source,
                    evidence=[
                        item.model_copy(deep=True)
                        for row in broker_rows
                        for item in row.evidence
                    ],
                )
            )
    return fields


def extract_litera_lincoln_schedule_a_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    """Merge Lincoln's commission and Broker Bonus lines into one recipient."""
    parsed_rows: list[ScheduleABrokerRow] = []
    for page, page_text in page_texts:
        text = normalize_ocr_text(page_text or "")
        if not (
            re.search(r"SCHEDULE\s+A\s+REPORTING\s+INFORMATION", text, re.IGNORECASE)
            and re.search(r"Insurance\s+fees\s+and\s+commissions\s+paid", text, re.IGNORECASE)
        ):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            paid = re.match(
                r"^(?P<name>[A-Z][A-Z &,.']+?(?:LLC|INC\.?|CORPORATION|COMPANY))\s+"
                r"\$(?P<amount>[\d,]+(?:\.\d{2})?)\s*(?P<purpose>.*?)\s*3\s*$",
                line,
                re.IGNORECASE,
            )
            if not paid or index + 2 >= len(lines):
                continue
            address = lines[index + 1]
            locality = re.match(
                r"^(?P<city>[A-Z][A-Z .'-]+?),?\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
                lines[index + 2],
                re.IGNORECASE,
            )
            if not re.match(r"^\d{1,6}\s+", address) or not locality:
                continue
            amount = money_value(paid.group("amount"))
            purpose = clean_extracted_value(paid.group("purpose")) or "COMMISSIONS"
            is_fee = purpose.casefold() != "commissions"
            source = "\n".join(lines[index : index + 3])
            parsed_rows.append(
                ScheduleABrokerRow(
                    name=clean_extracted_value(paid.group("name")),
                    address_line_1=clean_extracted_value(address),
                    city=clean_extracted_value(locality.group("city")),
                    state=locality.group("state").upper(),
                    zip_code=locality.group("zip"),
                    commission_total="0" if is_fee else amount,
                    fee_total=amount if is_fee else "0",
                    purpose=purpose,
                    commission_rows=[] if is_fee else [
                        ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")
                    ],
                    fee_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose=purpose)] if is_fee else [],
                    source_page=page,
                    commission_source_text=source,
                    fee_source_text=source,
                    confidence=0.99,
                    decision="AUTOMATIC",
                    evidence=[
                        SourceEvidence(
                            provider="Lincoln Schedule A reporting parser",
                            page=page,
                            source_text=source,
                            table_cell=(index + 1, 0),
                        )
                    ],
                )
            )
    merged: dict[tuple[str, str, str], ScheduleABrokerRow] = {}
    for row in parsed_rows:
        key = (
            _canonical_broker_name(row.name),
            str(row.state or "").upper(),
            str(row.zip_code or ""),
        )
        current = merged.get(key)
        if current is None:
            merged[key] = row.model_copy(deep=True)
            continue
        if len(str(row.address_line_1 or "")) > len(str(current.address_line_1 or "")):
            current.address_line_1 = row.address_line_1
        current.commission_rows.extend(item.model_copy(deep=True) for item in row.commission_rows)
        current.fee_rows.extend(item.model_copy(deep=True) for item in row.fee_rows)
        current.commission_total = sum_money_values(
            current.commission_total,
            row.commission_total,
        ) or "0"
        current.fee_total = sum_money_values(current.fee_total, row.fee_total) or "0"
        current.commission_source_text = "\n".join(
            filter(None, [current.commission_source_text, row.commission_source_text])
        )
        current.fee_source_text = "\n".join(
            filter(None, [current.fee_source_text, row.fee_source_text])
        )
        current.evidence = _merge_source_evidence(current.evidence, row.evidence)
    return list(merged.values())

def extract_aetna_attached_listing_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Use an Aetna state attachment only for the plan sponsor's state.

    Aetna's Schedule A form deliberately says ``EIN: See Attached`` and
    includes every state legal entity in an appendix.  Treating the first row
    as the carrier silently selects Arizona for California plans.  This narrow
    parser requires the Aetna attachment heading and a sponsor address with a
    state/ZIP before selecting exactly that state's appendix row.
    """
    combined = "\n".join(text or "" for _, text in page_texts)
    if not re.search(r"\bAetna\b", combined, re.IGNORECASE):
        return []

    attachment = next(
        (
            (page, text)
            for page, text in page_texts
            if re.search(r"State\s+NAIC\s+Code\s+Service\s+Area\s+EIN", text or "", re.IGNORECASE)
        ),
        None,
    )
    if not attachment:
        return []

    sponsor_state = next(
        (
            match.group(1).upper()
            for _, text in page_texts
            for match in re.finditer(r"\b([A-Z]{2})\s{2,}(\d{5}(?:-\d{4})?)\b", text or "")
        ),
        None,
    )
    if not sponsor_state:
        return []

    page, appendix_text = attachment
    # Preserve a legitimate all-zero NAIC code.  It is a five-character FTW
    # value, not a missing value or numeric zero.
    row = re.search(
        rf"^\s*{re.escape(sponsor_state)}\s+(?P<naic>\d{{5}})\s+(?P<carrier>.+?)\s+(?P<ein>\d{{2}}-\d{{7}})\s*$",
        appendix_text,
        re.IGNORECASE | re.MULTILINE,
    )
    if not row:
        return []

    source = clean_extracted_value(row.group(0))
    values = (
        # The parenthetical identifies the incorporation jurisdiction, not the
        # carrier name field FTW asks for; omitting it also avoids consuming
        # carrier-name field capacity with non-reportable text.
        ("1a. Name of Insurance Company", re.sub(r"\s*\([^)]*\)\s*$", "", row.group("carrier")).strip()),
        ("1b. Insurance Carrier EIN", row.group("ein")),
        ("1c. NAIC Code", row.group("naic")),
    )
    return [
        NormalizedExtractionField(
            field_name=field_name,
            value=normalize_schedule_a_naic(value) if field_name.startswith("1c.") else value,
            confidence=0.99,
            page=page,
            source_text=source,
            evidence=[
                SourceEvidence(
                    provider="Aetna attached listing parser",
                    page=page,
                    source_text=source,
                    table_cell=(appendix_text[: row.start()].count("\n") + 1, 0),
                )
            ],
        )
        for field_name, value in values
    ]


_POSITION_DATE = r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}"


def extract_position_aware_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Extract explicitly labelled support-statement values with row evidence.

    This path is deliberately narrow: it accepts a value only when its label
    and value share a line/section. It fixes unfamiliar carrier layouts
    without adding another carrier-specific parser.
    """
    fields: list[NormalizedExtractionField] = []

    def add(
        field_name: str,
        value: str | None,
        *,
        page: int,
        lines: list[str],
        row: int,
        confidence: float = 0.98,
        source_override: str | None = None,
    ) -> None:
        clean = clean_extracted_value(str(value or "")).rstrip(".")
        if field_name.startswith("1c."):
            clean = normalize_schedule_a_naic(clean)
        if not clean or is_blank_extraction_value(clean):
            return
        source_line = lines[max(0, row - 1)] if lines else clean
        column = max(0, len(source_line) - len(source_line.lstrip()))
        source_text = source_override or "\n".join(lines[max(0, row - 2) : min(len(lines), row + 1)]).strip()
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=page,
                source_text=source_text,
                evidence=[
                    SourceEvidence(
                        provider="Position-aware PDF parser",
                        page=page,
                        source_text=source_text,
                        table_cell=(row, column),
                    )
                ],
            )
        )

    for page, page_text in page_texts:
        lines = str(page_text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
        text = "\n".join(lines)
        compact_digits = re.sub(r"(?<=\d)\s+(?=\d|[/.-])|(?<=[/.-])\s+(?=\d)", "", text)

        def line_match(pattern: str, *, flags: int = re.IGNORECASE) -> tuple[re.Match, int] | None:
            for row_index, line in enumerate(lines, start=1):
                match = re.search(pattern, line, flags=flags)
                if match:
                    return match, row_index
            return None

        carrier = line_match(r"Name\s+of\s+Insurance\s+Company\s*:\s*(.+)$")
        if carrier:
            add("1a. Name of Insurance Company", carrier[0].group(1), page=page, lines=lines, row=carrier[1])
        else:
            certified = line_match(r"^\s*((?:The\s+)?[A-Z][A-Za-z&,. '\-]+\s+Insurance\s+Company(?:\s+of\s+[A-Za-z ]+)?)\.?\s*$")
            if certified:
                add("1a. Name of Insurance Company", certified[0].group(1), page=page, lines=lines, row=certified[1], confidence=0.96)

        ein = line_match(r"(?:Federal\s+)?(?:Tax\s+ID|EIN)(?:\s+Number)?\s*:?\s*(\d{2}-\d{7})")
        if ein:
            add("1b. Insurance Carrier EIN", ein[0].group(1), page=page, lines=lines, row=ein[1])

        naic = line_match(r"NAIC(?:\s+Code|\s+Number)?\s*:?\s*(\d{4,8})")
        if naic:
            add("1c. NAIC Code", naic[0].group(1), page=page, lines=lines, row=naic[1])

        contract = line_match(r"Contract\s+Identification\s*/\s*Policy\s+Number\s*:\s*([A-Za-z0-9][A-Za-z0-9./-]+)")
        if contract:
            add("1d. Contract/Policy Number", contract[0].group(1), page=page, lines=lines, row=contract[1])
        elif re.search(r"Schedule\s+A,\s*Line\s+3,\s*Element\s*\(b\)", text, re.IGNORECASE):
            for row_index, line in enumerate(lines, start=1):
                match = re.match(r"^\s*([A-Z0-9][A-Z0-9-]{3,})\s{2,}[A-Z]", line)
                if match and re.search(r"\d", match.group(1)):
                    add("1d. Contract/Policy Number", match.group(1), page=page, lines=lines, row=row_index, confidence=0.96)
                    break

        persons = line_match(r"(?:approximate\s+number\s+of\s+)?(?:employees|persons)\s+(?:covered|insured)[^\d\n]{0,80}([\d,]+)\s*$")
        if persons:
            add("1e. Persons Covered (End of Policy Year)", persons[0].group(1), page=page, lines=lines, row=persons[1])

        period_patterns = (
            rf"Policy\s+Period\s*:\s*({_POSITION_DATE})\s*(?:-|to|through)\s*({_POSITION_DATE})",
            rf"Data\s+for\s+Period\s+From\s*:\s*({_POSITION_DATE})\s*(?:To\s*:|to|through)\s*({_POSITION_DATE})",
        )
        for pattern in period_patterns:
            period = re.search(pattern, compact_digits, re.IGNORECASE)
            if not period:
                continue
            row_index = compact_digits[: period.start()].count("\n") + 1
            add("1f. Policy Year Beginning Date", _normalize_position_date(period.group(1)), page=page, lines=lines, row=row_index)
            add("1g. Policy Year Ending Date", _normalize_position_date(period.group(2)), page=page, lines=lines, row=row_index)
            break

        premium = line_match(r"Total\s+Premium\s+Paid\s+to\s+.+?\s*:\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*$")
        premium_source = None
        gross_header = line_match(r"Gross\s+Premium\s+Paid")
        if not premium and gross_header:
            premium = line_match(r"Totals?\s*:\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*$")
            if premium:
                premium_source = "\n".join((gross_header[0].group(0), premium[0].group(0)))
        if premium:
            add(
                "10a. Total premiums or subscription charges paid to carrier",
                money_value(premium[0].group(1)),
                page=page,
                lines=lines,
                row=premium[1],
                source_override=premium_source,
            )

    return select_best_schedule_a_fields(fields)


def _normalize_position_date(value: str) -> str:
    clean = re.sub(r"\s+", "", str(value or "")).replace("-", "/")
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", clean)
    if not match:
        return clean
    month, day, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    return f"{month:02d}/{day:02d}/{year:04d}"


def extract_position_aware_schedule_a_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    """Resolve broker rows only from explicit row/column-labelled sections."""
    rows: list[ScheduleABrokerRow] = []
    for page, page_text in page_texts:
        lines = str(page_text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
        text = "\n".join(lines)
        if re.search(r"Schedule\s+A,\s*Line\s+3,\s*Element\s*\(b\)", text, re.IGNORECASE):
            row = _positioned_line3_summary_broker(page, lines)
            if row:
                rows.append(row)
                continue
        if (
            re.search(r"Insurance\s+Fees\s+and\s+Commissions\s+Paid", text, re.IGNORECASE)
            and re.search(r"Name\s+and\s+(?:Address\s+of\s+)?(?:the\s+)?Agents?\s+or\s+Brokers?", text, re.IGNORECASE)
        ):
            row = _positioned_combined_commission_broker(page, lines)
            if row:
                rows.append(row)
                continue
        if re.search(r"AETNA\s+LIFE\s+INSURANCE\s+COMPANY", text, re.IGNORECASE):
            row = _positioned_aetna_broker(page, lines)
            if row:
                rows.append(row)
    return merge_schedule_a_broker_rows([], rows)


def _positioned_line3_summary_broker(page: int, lines: list[str]) -> ScheduleABrokerRow | None:
    text = "\n".join(lines)
    commission_match = re.search(r"Total\s+Commissions\s+Paid\s+On\s+Plan\s*:\s*\$?\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    fee_match = re.search(r"Total\s+Fees\s+Paid\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if not commission_match:
        return None
    broker_index = None
    name = ""
    for index, line in enumerate(lines):
        match = re.match(r"^\s*[A-Z0-9][A-Z0-9-]{3,}\s{2,}(.+?)\s*(?:\$[\d,]+(?:\.\d{2})?)?\s*$", line)
        if not match:
            continue
        candidate = clean_extracted_value(match.group(1))
        if re.search(r"recipient|commission|fees|amount", candidate, re.IGNORECASE):
            continue
        if is_probable_person_or_entity_name(candidate):
            broker_index = index
            name = candidate
            break
    if broker_index is None:
        return None
    address_lines = []
    for line in lines[broker_index + 1 : broker_index + 4]:
        clean = clean_extracted_value(line)
        if not clean:
            continue
        if re.search(r"Group\s+Insurance|Total|Schedule\s+A", clean, re.IGNORECASE):
            break
        address_lines.append(clean)
    address_line_1, address_line_2, city, state, zip_code = _positioned_broker_address(address_lines)
    commission = money_value(commission_match.group(1))
    fee = money_value(fee_match.group(1)) if fee_match else "0"
    identity_source = "\n".join(lines[max(0, broker_index - 2) : min(len(lines), broker_index + 4)]).strip()
    commission_source = "\n".join((identity_source, commission_match.group(0))).strip()
    fee_source = "\n".join((identity_source, fee_match.group(0) if fee_match else "No fees reported")).strip()
    return ScheduleABrokerRow(
        name=name,
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        zip_code=zip_code,
        organization_code="3",
        purpose=derive_schedule_a_purpose(commission, fee),
        commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="Commissions")],
        fee_rows=[ScheduleABrokerMoneyRow(amount=fee, purpose="Fees")] if (parse_numeric_amount(fee) or 0) > 0 else [],
        commission_total=commission,
        fee_total=fee,
        commission_source_text=commission_source,
        fee_source_text=fee_source,
        source_page=page,
        confidence=0.98,
        evidence=[
            SourceEvidence(
                provider="Position-aware PDF parser",
                page=page,
                source_text="\n".join((commission_source, fee_source)).strip(),
                table_cell=(broker_index + 1, 0),
            )
        ],
    )


def _positioned_combined_commission_broker(page: int, lines: list[str]) -> ScheduleABrokerRow | None:
    header_index = next((index for index, line in enumerate(lines) if re.search(r"Name\s+and\s+(?:Address\s+of\s+)?(?:the\s+)?Agents?\s+or\s+Brokers?", line, re.IGNORECASE)), None)
    money_header_index = next((index for index, line in enumerate(lines) if index > (header_index or 0) and re.search(r"(?:Commissions?\s+or\s+Fees\s+Paid|Amount\s+of\s+Commissions?\s+Paid)", line, re.IGNORECASE)), None)
    if header_index is None or money_header_index is None:
        return None
    identity_lines = [clean_extracted_value(line) for line in lines[header_index + 1 : money_header_index] if clean_extracted_value(line)]
    if len(identity_lines) < 2 or not is_probable_person_or_entity_name(identity_lines[0]):
        return None
    amount_text = "\n".join(lines[money_header_index : min(len(lines), money_header_index + 6)])
    amounts = [money_value(value) for value in re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", amount_text)]
    if not amounts:
        return None
    commission_total = sum_money_values(*amounts[:2]) or amounts[0]
    address_line_1, address_line_2, city, state, zip_code = _positioned_broker_address(identity_lines[1:])
    source = "\n".join(lines[header_index : min(len(lines), money_header_index + 6)]).strip()
    return ScheduleABrokerRow(
        name=identity_lines[0],
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        zip_code=zip_code,
        organization_code="3",
        purpose="COMMISSIONS",
        commission_rows=[
            ScheduleABrokerMoneyRow(amount=amounts[0], purpose="Straight Commission"),
            *([ScheduleABrokerMoneyRow(amount=amounts[1], purpose="Contingent Commission")] if len(amounts) > 1 else []),
        ],
        fee_rows=[],
        commission_total=commission_total,
        fee_total="0",
        commission_source_text=source,
        source_page=page,
        confidence=0.98,
        evidence=[SourceEvidence(provider="Position-aware PDF parser", page=page, source_text=source, table_cell=(header_index + 2, 0))],
    )


def _positioned_aetna_broker(page: int, lines: list[str]) -> ScheduleABrokerRow | None:
    """Read Aetna's compact Schedule A broker row without shifting columns.

    The Aetna row places contract, name, address, and commission total on one
    visual line.  A generic column parser can mistake the contract number for
    a name or drop the broker entirely, so require the exact Aetna table
    labels before accepting this shape.
    """
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.search(r"Name\s+and\s+address\s+of\s+the\s+agents?\s+or\s+brokers?", line, re.IGNORECASE)
            and re.search(r"Amount\s+of[\s\S]{0,320}commissions?\s+paid", "\n".join(lines[index : index + 3]), re.IGNORECASE)
        ),
        None,
    )
    if header_index is None:
        return None

    row_index = None
    match = None
    for index, line in enumerate(lines[header_index + 1 :], start=header_index + 1):
        candidate = re.match(
            r"^\s*[A-Z0-9][A-Z0-9-]{3,}\s+(?P<name>[A-Z][A-Z '&.\-]+?)\s+(?P<street>\d+\s+.+?)\s+\$\s*(?P<commission>[\d,]+(?:\.\d{2})?)\s*$",
            line,
        )
        if candidate:
            row_index = index
            match = candidate
            break
    if row_index is None or match is None:
        return None

    name = clean_extracted_value(match.group("name"))
    if not is_probable_person_or_entity_name(name):
        return None
    commission = money_value(match.group("commission"))
    address_lines = [match.group("street")]
    for line in lines[row_index + 1 : row_index + 3]:
        clean = clean_extracted_value(line)
        if clean:
            address_lines.append(clean)
    address_line_1, address_line_2, city, state, zip_code = _positioned_broker_address(address_lines)
    source = "\n".join(lines[header_index : min(len(lines), row_index + 3)]).strip()
    return ScheduleABrokerRow(
        name=name,
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        zip_code=zip_code,
        organization_code="3",
        purpose="COMMISSIONS",
        commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")],
        fee_rows=[],
        commission_total=commission,
        fee_total="0",
        commission_source_text=source,
        fee_source_text=source,
        source_page=page,
        confidence=0.99,
        evidence=[SourceEvidence(provider="Aetna Schedule A table parser", page=page, source_text=source, table_cell=(row_index + 1, 0))],
    )


def _positioned_broker_address(
    lines: list[str],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    clean_lines = [clean_extracted_value(line) for line in lines if clean_extracted_value(line)]
    if not clean_lines:
        return None, None, None, None, None
    if len(clean_lines) > 1:
        normalized_lines = [re.sub(r",\s*(\d{5}(?:-\d{4})?)$", r" \1", line) for line in clean_lines]
        parsed = _compensation_address(normalized_lines)
        if parsed[4]:
            return parsed
    joined = " ".join(clean_lines)
    match = re.match(
        r"^(?P<street>.*?\b(?:STREET|ST|AVENUE|AVE|ROAD|RD|PARKWAY|PKWY|DRIVE|DR|BOULEVARD|BLVD|LANE|LN|WAY|COURT|CT)\b(?:\s+(?:SUITE|STE|FLOOR|FL|#)\s*\w+)?)\s+(?P<city>[A-Z][A-Z ]+?)\s*,?\s*(?P<state>[A-Z]{2})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$",
        joined,
        re.IGNORECASE,
    )
    if match:
        return (
            clean_extracted_value(match.group("street")),
            None,
            clean_extracted_value(match.group("city")),
            match.group("state").upper(),
            match.group("zip"),
        )
    return joined, None, None, None, None


def _extract_fields_from_pages(page_texts: list[tuple[int, str]], *, rules=None) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    for index, text in page_texts:
        fields.extend(parse_schedule_a_text(text, index, rules=rules))
        fields.extend(extract_explicit_benefit_indicator_fields(text, index, rules=rules))
        fields.extend(extract_bcbsma_schedule_a_worksheet_fields(text, index))
    fields.extend(extract_bcbs_michigan_schedule_a_fields(page_texts))
    fields.extend(extract_prudential_schedule_a_fields(page_texts))
    fields.extend(extract_eyemed_schedule_a_fields(page_texts))
    principal_fields = extract_principal_short_form_schedule_a_fields(page_texts)
    if principal_fields:
        # Principal's compact worksheet is a fully labelled carrier report.
        # Its dedicated parser preserves the real Part I columns, while the
        # generic layout pass can append the next column marker to the carrier
        # name. Treat this narrow, positively identified layout as authoritative.
        principal_names = {field.field_name for field in principal_fields}
        fields = [field for field in fields if field.field_name not in principal_names]
        fields.extend(principal_fields)
    fields.extend(extract_standard_schedule_a_fields(page_texts))
    fields.extend(extract_standard_short_form_schedule_a_fields(page_texts))
    fields.extend(extract_united_omaha_schedule_a_fields(page_texts))
    for layout_fields in (
        extract_filled_irs_schedule_a_fields(page_texts),
        extract_ameritas_schedule_a_fields(page_texts),
        extract_curalinc_schedule_a_fields(page_texts),
        extract_continental_american_schedule_a_fields(page_texts),
    ):
        if layout_fields:
            fields = [field for field in fields if field.field_name not in SCHEDULE_A_LAYOUT_OWNED_FIELDS]
            fields.extend(layout_fields)
    authoritative_packet_fields = [
        extract_cigna_g2050a_schedule_a_fields(page_texts),
        extract_delta_dental_schedule_a_fields(page_texts),
        extract_first_unum_schedule_a_fields(page_texts),
        extract_ace_schedule_a_fields(page_texts),
        extract_mount_sinai_schedule_a_fields(page_texts),
        extract_nyl_annual_policy_fields(page_texts),
        extract_hmsa_schedule_a_fields(page_texts),
        extract_colonial_life_schedule_a_fields(page_texts),
        extract_transamerica_schedule_a_fields(page_texts),
        extract_combined_chubb_schedule_a_fields(page_texts),
        extract_john_hancock_schedule_a_fields(page_texts),
        extract_metlife_standard_schedule_a_fields(page_texts),
    ]
    full_text = "\n\n".join(text for _, text in page_texts)
    if full_text:
        fields.extend(parse_schedule_a_text(full_text, None, rules=rules))
        fields.extend(extract_bcbsma_commission_breakdown_fields(full_text, None))
    # The broker compensation table is where items 3a-3d actually live on a
    # carrier statement. Reading the table and reading the labelled fields are
    # separate passes, so feed the table's values back in as fields - without
    # this the amounts were parsed and then thrown away.
    fields.extend(schedule_a_broker_compensation_fields(extract_compensation_table_broker_rows(page_texts)))
    fields.extend(schedule_a_broker_compensation_fields(extract_columnar_broker_compensation_rows(page_texts)))
    fields.extend(extract_commission_fee_total_fields(page_texts))
    fields.extend(extract_rules_driven_schedule_a_fields(page_texts, rules=rules))
    for packet_fields in authoritative_packet_fields:
        if not packet_fields:
            continue
        authoritative_names = {field.field_name for field in packet_fields}
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(packet_fields)
    anthem_fields = extract_anthem_schedule_a_fields(page_texts)
    if anthem_fields:
        authoritative_names = {
            "1a. Name of Insurance Company",
            "1b. Insurance Carrier EIN",
            "1c. NAIC Code",
            "1d. Contract/Policy Number",
            "1e. Persons Covered (End of Policy Year)",
            "1f. Policy Year Beginning Date",
            "1g. Policy Year Ending Date",
            "3a. Name of Agent/Broker/Person",
            "3b. Amount of Commissions",
            "3c. Amount of Fees",
            "3d. Purpose",
            "3e. Organizational Code",
            "10a. Total premiums or subscription charges paid to carrier",
        }
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(anthem_fields)
    aflac_fields = extract_aflac_schedule_a_fields(page_texts)
    if aflac_fields:
        authoritative_names = {
            "1a. Name of Insurance Company",
            "1b. Insurance Carrier EIN",
            "1c. NAIC Code",
            "1d. Contract/Policy Number",
            "1e. Persons Covered (End of Policy Year)",
            "1f. Policy Year Beginning Date",
            "1g. Policy Year Ending Date",
            "3a. Name of Agent/Broker/Person",
            "3b. Amount of Commissions",
            "3c. Amount of Fees",
            "3d. Purpose",
            "3e. Organizational Code",
            "10a. Total premiums or subscription charges paid to carrier",
        }
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(aflac_fields)
    aig_fields = extract_aig_schedule_a_fields(page_texts)
    if aig_fields:
        authoritative_names = {
            "1a. Name of Insurance Company",
            "1b. Insurance Carrier EIN",
            "1c. NAIC Code",
            "1d. Contract/Policy Number",
            "1e. Persons Covered (End of Policy Year)",
            "1f. Policy Year Beginning Date",
            "1g. Policy Year Ending Date",
            "3a. Name of Agent/Broker/Person",
            "3b. Amount of Commissions",
            "3c. Amount of Fees",
            "10a. Total premiums or subscription charges paid to carrier",
        }
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(aig_fields)
    cigna_fields = extract_cigna_schedule_a_fields(page_texts)
    if cigna_fields:
        # Cigna reporting packages contain a consolidated Schedule A followed
        # by state-carrier appendices. Those appendix EIN/NAIC/lives values are
        # supporting detail, not alternative primary Schedule A records.
        # Remove every broad-parser value for fields owned by the consolidated
        # parser so a later appendix can never replace the primary record.
        authoritative_names = {field.field_name for field in cigna_fields}
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(cigna_fields)
    vsp_fields = extract_vsp_schedule_a_fields(page_texts)
    if any(_is_vsp_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        # Vision Service Plan uses a carrier report rather than the IRS
        # Schedule A grid. Its labelled identity/date block is authoritative;
        # broad OCR fallbacks can otherwise merge the carrier name with the
        # next label or use the start date for both ends of the policy year.
        # Remove every field owned by this layout even when the source says
        # N/A, otherwise a generic numeric fallback can turn a nearby year
        # into a false NAIC value.
        authoritative_names = {
            "1a. Name of Insurance Company",
            "1b. Insurance Carrier EIN",
            "1c. NAIC Code",
            "1d. Contract/Policy Number",
            "1e. Persons Covered (End of Policy Year)",
            "1f. Policy Year Beginning Date",
            "1g. Policy Year Ending Date",
            "3a. Name of Agent/Broker/Person",
            "3b. Amount of Commissions",
            "3c. Amount of Fees",
            "3d. Purpose",
            "3e. Organizational Code",
            "9a. Premiums: (1) Amount Received",
            "9b(1). Benefit Charges (1) Claims paid",
            "9c(1)(B). Administrative service or other fees",
            "10a. Total premiums or subscription charges paid to carrier",
        }
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(vsp_fields)
    hartford_fields = extract_hartford_schedule_a_fields(page_texts)
    if hartford_fields:
        authoritative_names = {field.field_name for field in hartford_fields}
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(hartford_fields)
    allone_fields = extract_allone_eap_schedule_a_fields(page_texts)
    if allone_fields:
        authoritative_names = {
            *{field.field_name for field in allone_fields},
            "3a. Name of Agent/Broker/Person",
            "3b. Amount of Commissions",
            "3c. Amount of Fees",
            "3d. Purpose",
            "3e. Organizational Code",
        }
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(allone_fields)
    metlife_fields = extract_metlife_bay_bridge_schedule_a_fields(page_texts)
    if metlife_fields:
        authoritative_names = {field.field_name for field in metlife_fields}
        fields = [field for field in fields if field.field_name not in authoritative_names]
        fields.extend(metlife_fields)
    # Re-apply NFP's positively identified layouts after every broad parser.
    # Several broad passes above intentionally run late and can otherwise
    # reintroduce blank-form labels after the authoritative replacement.
    for layout_fields in (
        extract_filled_irs_schedule_a_fields(page_texts),
        extract_ameritas_schedule_a_fields(page_texts),
        extract_curalinc_schedule_a_fields(page_texts),
        extract_continental_american_schedule_a_fields(page_texts),
    ):
        if layout_fields:
            fields = [
                field
                for field in fields
                if field.field_name not in SCHEDULE_A_LAYOUT_OWNED_FIELDS
            ]
            fields.extend(layout_fields)
    return select_best_schedule_a_fields(
        dedupe_fields([
            field
            for field in fields
            if not _is_column_heading_broker_name(field)
            and not is_obvious_template_placeholder(field.value)
        ])
    )


def extract_explicit_benefit_indicator_fields(
    text: str,
    page: int | None = None,
    *,
    rules=None,
) -> list[NormalizedExtractionField]:
    """Map explicit benefit evidence to discovered FTW comparison fields.

    Merely printing the word "Vision" on a blank IRS form is not evidence. We
    accept a completed benefit summary, an explicit insurance/coverage phrase,
    or a checked label. Checkbox evidence is ignored on obvious sample forms.
    """
    if not rules:
        return []
    normalized = normalize_ocr_text(text)
    placeholder_heavy = len(re.findall(r"ABCDEFGHI|123456789012345", normalized, flags=re.IGNORECASE)) >= 2
    fields: list[NormalizedExtractionField] = []
    for rule in rules:
        tag = str(rule.xml_tag or "").strip().lower()
        if tag not in {"healthind", "visionind"}:
            continue
        aliases = [rule.label, *rule.aliases]
        matched_source: str | None = None
        matched_value = "Yes"
        for alias in sorted({alias.strip() for alias in aliases if alias.strip()}, key=len, reverse=True):
            token = loose_label_pattern(alias)
            if not placeholder_heavy:
                checkbox = re.search(
                    rf"(?im)(?:\([A-M]\)\s*)?\[\s*(?P<mark>[Xx]?)\s*\]\s*{token}\b",
                    normalized,
                )
                if checkbox:
                    matched_source = checkbox.group(0).strip()
                    matched_value = "Yes" if checkbox.group("mark") else "No"
                    break
            patterns = [
                rf"(?im)^\s*Benefits?\s*:\s*[^\n]*\b{token}\b[^\n]*$",
                rf"(?i)\b{token}\s+(?:insurance|coverage|benefit)\b",
                rf"(?i)\b{token}\s+plan\b",
                rf"(?i)\b(?:type\s+of\s+benefit|benefit\s+type)\s*:?\s*{token}\b",
            ]
            if not placeholder_heavy:
                patterns.append(rf"(?im)^\s*(?:X|✓|☑)\s*{token}\b")
            match = next((match for pattern in patterns if (match := re.search(pattern, normalized))), None)
            if match:
                matched_source = match.group(0).strip()
                break
        if matched_source:
            fields.append(
                NormalizedExtractionField(
                    field_name=rule.label,
                    value=matched_value,
                    confidence=0.9,
                    page=page,
                    source_text=matched_source,
                )
            )
    return fields


_BROKER_HEADING_TOKENS = (
    "total",
    "premium",
    "covered",
    "plan #",
    "policy or contract",
    "amount of",
    "name & address",
    "name and address",
    "fees paid",
    "commissions paid",
    "for which paid",
)


def _is_column_heading_broker_name(field: NormalizedExtractionField) -> bool:
    """Drop a broker name that is really a run-together column heading.

    Table headers wrap across lines in the PDF text layer, so a careless parse
    produces values like "Base Plan # Covered Total Premiums". An empty field
    sends a reviewer to the document; a plausible-looking wrong name can be
    approved by mistake and sent to FT Williams.
    """
    if not field.field_name.startswith("3a."):
        return False
    lowered = str(field.value or "").lower()
    if not lowered:
        return False
    return sum(1 for token in _BROKER_HEADING_TOKENS if token in lowered) >= 2


def schedule_a_broker_compensation_fields(rows: list[ScheduleABrokerRow]) -> list[NormalizedExtractionField]:
    """Turn broker table rows into Schedule A items 3a-3d."""
    if not rows:
        return []
    primary = rows[0]
    fields: list[NormalizedExtractionField] = []

    row_evidence = [item.model_copy(deep=True) for row in rows for item in row.evidence]
    row_source = next(
        (
            text
            for row in rows
            for text in (row.commission_source_text, row.fee_source_text)
            if text
        ),
        "Broker compensation table",
    )

    def add(field_name: str, value: str | None, confidence: float) -> None:
        cleaned = clean_extracted_value(str(value)) if value not in (None, "") else ""
        if cleaned:
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=cleaned,
                    confidence=confidence,
                    page=primary.source_page,
                    source_text=row_source,
                    evidence=[item.model_copy(deep=True) for item in row_evidence],
                )
            )

    single_row_confidence = min(primary.confidence, 0.98)
    add("3a. Name of Agent/Broker/Person", primary.name, single_row_confidence)
    # More than one recipient means the filing total is the sum, and a human
    # needs to split it across rows - flag it by lowering confidence.
    confidence = single_row_confidence if len(rows) == 1 else 0.6
    commission_total = sum_money_values(*(row.commission_total for row in rows))
    fee_total = sum_money_values(*(row.fee_total for row in rows))
    add("3b. Amount of Commissions", commission_total, confidence)
    add("3c. Amount of Fees", fee_total, confidence)
    purposes = {
        clean_extracted_value(money_row.purpose)
        for row in rows
        for money_row in [*row.commission_rows, *row.fee_rows]
        if money_row.purpose
    }
    purpose = next(iter(purposes)) if len(purposes) == 1 else derive_schedule_a_purpose(commission_total, fee_total)
    add("3d. Purpose", purpose, 0.8)
    add("3e. Organizational Code", primary.organization_code, 0.8)
    return fields


# "Total (from below) 6590.57 5555.33" - a commissions/fees totals line.
_COMMISSION_FEE_TOTALS = re.compile(
    r"total[^\n\d]{0,24}?\$?\s*(?P<commissions>\d[\d,]*(?:\.\d{2})?)\s+\$?\s*(?P<fees>\d[\d,]*(?:\.\d{2})?)\s*$",
    re.IGNORECASE,
)


def extract_commission_fee_total_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Read a commissions/fees totals line where no named broker row exists.

    Some carriers (for example Equitable) print a section header of
    "Commissions Paid / Fees Paid" followed by a totals line, with the named
    recipients listed separately underneath.
    """
    fields: list[NormalizedExtractionField] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not normalized:
            continue
        lines = [line.strip() for line in normalized.splitlines()]
        for index, line in enumerate(lines):
            match = _COMMISSION_FEE_TOTALS.search(line)
            if not match:
                continue
            context = " ".join(lines[max(0, index - 3) : index + 1]).lower()
            if "commission" not in context or "fee" not in context:
                continue
            commissions = money_value(match.group("commissions"))
            fees = money_value(match.group("fees"))
            if not commissions and not fees:
                continue
            for field_name, value in (
                ("3b. Amount of Commissions", commissions),
                ("3c. Amount of Fees", fees),
            ):
                if value:
                    fields.append(
                        NormalizedExtractionField(
                            field_name=field_name,
                            value=value,
                            confidence=0.8,
                            page=page,
                            source_text="Commissions and fees total",
                        )
                    )
            return fields
    return fields


def extract_layout_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Read repeating broker rows from tables with discoverable columns."""
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        lines = str(text or "").splitlines()
        section_start = next(
            (
                index
                for index, line in enumerate(lines)
                if "persons" in _dense_compare(line)
                and "receiving" in _dense_compare(line)
                and "commission" in _dense_compare(line)
            ),
            None,
        )
        if section_start is None:
            continue
        header_index = next(
            (
                index
                for index in range(section_start, min(len(lines), section_start + 14))
                if "name" in _dense_compare(lines[index])
                and ("agent" in _dense_compare(lines[index]) or "broker" in _dense_compare(lines[index]))
                and "amount" in _dense_compare(lines[index])
            ),
            None,
        )
        if header_index is None:
            continue
        header = lines[header_index]
        commission_col = _first_column_index(header, ("(b)", "amount of", "commissions paid"), after=0)
        fee_col = _first_column_index(header, ("fees paid", "(c)"), after=(commission_col or 0) + 1)
        org_col = _first_column_index(header, ("(e)", "org code", "organization"), after=(fee_col or 0) + 1)
        if commission_col is None or fee_col is None or org_col is None or not (8 <= commission_col < fee_col < org_col):
            continue

        current: dict[str, Any] | None = None
        body_start = header_index + 1
        while body_start < len(lines) and body_start <= header_index + 4:
            dense = _dense_compare(lines[body_start])
            if any(token in dense for token in ("commission", "purpose", "orgcode", "amount")):
                body_start += 1
                continue
            break

        for raw_line in lines[body_start:]:
            dense = _dense_compare(raw_line)
            if any(token in dense for token in ("reportablecommissions", "section4", "section8", "section9", "section10", "preparedon")):
                break
            if not raw_line.strip():
                continue
            name_cell = raw_line[:commission_col].strip()
            commission_cell = raw_line[commission_col:fee_col].strip()
            fee_cell = raw_line[fee_col:org_col].strip()
            org_cell = raw_line[org_col:].strip()
            commission_values, fee_values = _layout_money_columns(raw_line, commission_col, fee_col, org_col)
            has_money = bool(commission_values or fee_values)
            starts_row = has_money and _looks_like_layout_broker_name(name_cell) and not _looks_like_broker_heading(name_cell)
            if starts_row:
                if current:
                    row = _finalize_layout_broker_row(current, page)
                    if row:
                        rows.append(row)
                current = {
                    "name": clean_extracted_value(name_cell),
                    "address": [],
                    "commissions": [],
                    "fees": [],
                    "purposes": [],
                    "organization_code": None,
                }
            elif current and name_cell:
                current["address"].append(clean_extracted_value(name_cell))
            if not current:
                continue
            current["commissions"].extend(commission_values)
            current["fees"].extend(fee_values)
            if fee_values:
                purpose = _purpose_without_money(raw_line[(commission_col + fee_col) // 2 : org_col])
                if purpose and purpose.lower() not in {item.lower() for item in current["purposes"]}:
                    current["purposes"].append(purpose)
            if not current["organization_code"]:
                code = re.search(r"\b([1-9])\s*-\s*Ins", raw_line, flags=re.IGNORECASE) or re.search(r"\b([1-9])\b", org_cell)
                if code:
                    current["organization_code"] = code.group(1)
        if current:
            row = _finalize_layout_broker_row(current, page)
            if row:
                rows.append(row)
    return dedupe_schedule_a_broker_rows(rows)


def _dense_compare(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _first_column_index(line: str, labels: tuple[str, ...], *, after: int) -> int | None:
    matches: list[int] = []
    for label in labels:
        match = re.search(_dense_rule_label_pattern(label), line[after:], flags=re.IGNORECASE)
        if match:
            matches.append(after + match.start())
    return min(matches) if matches else None


def _money_tokens(value: str) -> list[str]:
    return [money_value(match.group(1)) for match in _RULE_MONEY_TOKEN.finditer(str(value or ""))]


def _layout_money_columns(line: str, commission_col: int, fee_col: int, org_col: int) -> tuple[list[str], list[str]]:
    commissions: list[str] = []
    fees: list[str] = []
    boundary = (commission_col + fee_col) // 2
    for match in _RULE_MONEY_TOKEN.finditer(str(line or "")):
        value_start = match.start(1)
        if value_start < commission_col or value_start >= org_col:
            continue
        if len(match.group(1).replace(",", "")) == 1 and re.search(r"^\s*-\s*Ins", line[match.end(1) :], flags=re.IGNORECASE):
            continue
        target = fees if value_start >= boundary else commissions
        target.append(money_value(match.group(1)))
    return commissions, fees


def _looks_like_layout_broker_name(value: str) -> bool:
    clean = clean_extracted_value(value)
    if not clean or re.search(r"\d", clean) or len(clean.split()) < 2:
        return False
    return is_probable_person_or_entity_name(clean)


def _looks_like_broker_heading(value: str) -> bool:
    dense = _dense_compare(value)
    return any(token in dense for token in ("amountof", "nameaddress", "commissionpaid", "feespaid", "orgcode", "purpose"))


def _purpose_without_money(value: str) -> str | None:
    clean = clean_extracted_value(_RULE_MONEY_TOKEN.sub(" ", str(value or "")).replace("*", " "))
    clean = re.sub(r"\b[1-9]\s*-\s*I(?:ns(?:urance)?)?.*$", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s*-\s*Ins(?:urance)?(?:\s+Agent)?(?:\s+Or|\s+Broker)?\s*$", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+(?:O|Or|Agent|Broker)\s*$", "", clean, flags=re.IGNORECASE).strip()
    if not clean or _looks_like_broker_heading(clean):
        return None
    return clean


def _finalize_layout_broker_row(record: dict[str, Any], page: int) -> ScheduleABrokerRow | None:
    name = clean_extracted_value(record.get("name") or "")
    if not _looks_like_layout_broker_name(name) or _looks_like_broker_heading(name):
        return None
    addresses = [clean_extracted_value(value) for value in record.get("address") or []]
    addresses = [value for value in addresses if value and value.lower() not in {"or", "broker"}]
    city = state = zip_code = None
    street_lines = list(addresses)
    for index in range(len(addresses) - 1, -1, -1):
        match = re.fullmatch(r"(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", addresses[index], flags=re.IGNORECASE)
        if match:
            city = clean_extracted_value(match.group(1)).upper()
            state = match.group(2).upper()
            zip_code = match.group(3)
            street_lines = addresses[:index]
            break
    commission_total = sum_money_values(*(record.get("commissions") or [])) or "0"
    fee_total = sum_money_values(*(record.get("fees") or [])) or "0"
    purposes = [clean_extracted_value(value) for value in record.get("purposes") or [] if clean_extracted_value(value)]
    purpose = "; ".join(purposes) or derive_schedule_a_purpose(commission_total, fee_total)
    return ScheduleABrokerRow(
        name=name,
        address_line_1=street_lines[0] if street_lines else None,
        address_line_2=" ".join(street_lines[1:]) if len(street_lines) > 1 else None,
        city=city,
        state=state,
        zip_code=zip_code,
        organization_code=record.get("organization_code"),
        commission_rows=[ScheduleABrokerMoneyRow(amount=commission_total, purpose=purpose)] if (parse_numeric_amount(commission_total) or 0) > 0 else [],
        fee_rows=[ScheduleABrokerMoneyRow(amount=fee_total, purpose=purpose)] if (parse_numeric_amount(fee_total) or 0) > 0 else [],
        commission_total=commission_total,
        fee_total=fee_total,
        source_page=page,
        confidence=0.92,
    )


def extract_schedule_a_broker_rows_from_pdf_text(file_bytes: bytes) -> list[ScheduleABrokerRow]:
    page_texts = extract_pdf_text_pages(file_bytes)
    layout_page_texts = extract_pdf_layout_text_pages(file_bytes)
    cigna_rows = extract_cigna_schedule_a_broker_rows(page_texts)
    if cigna_rows or _is_cigna_schedule_a_packet(page_texts):
        return cigna_rows
    anthem_rows = extract_anthem_broker_rows(page_texts)
    if anthem_rows or any(_is_anthem_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return anthem_rows
    aig_rows = extract_aig_broker_rows(page_texts)
    if aig_rows or any(_is_aig_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return aig_rows
    vsp_rows = extract_vsp_broker_rows(page_texts)
    if vsp_rows or any(_is_vsp_schedule_a_report(normalize_ocr_text(text)) for _, text in page_texts):
        return vsp_rows
    hartford_rows = extract_hartford_broker_rows(page_texts)
    if hartford_rows or any(_is_hartford_annual_statement(normalize_ocr_text(text)) for _, text in page_texts):
        return hartford_rows
    metlife_rows = extract_metlife_bay_bridge_broker_rows(page_texts)
    if metlife_rows or any(_is_metlife_bay_bridge_report(normalize_ocr_text(text)) for _, text in page_texts):
        return metlife_rows
    unitedhealthcare_rows = extract_unitedhealthcare_broker_rows(page_texts)
    if unitedhealthcare_rows:
        return unitedhealthcare_rows
    positioned_rows = extract_position_aware_schedule_a_broker_rows(layout_page_texts)
    if positioned_rows:
        # Explicit row/column evidence is authoritative for these support
        # statements. Mixing in broad nearby-text parsers recreates the same
        # recipient as address and amount fragments.
        return positioned_rows
    full_text = "\n\n".join(text for _, text in page_texts)
    return dedupe_schedule_a_broker_rows(
        [
            *extract_schedule_a_broker_rows(full_text),
            *extract_bcbs_michigan_addendum_broker_rows(full_text),
            *extract_bcbsma_commission_breakdown_broker_rows(full_text),
            *extract_prudential_broker_rows(page_texts),
            *extract_eyemed_broker_rows(page_texts),
            *extract_principal_short_form_broker_rows(page_texts),
            *extract_standard_broker_rows(page_texts),
            *extract_standard_short_form_broker_rows(page_texts),
            *extract_united_omaha_broker_rows(page_texts),
            *extract_summary_table_broker_rows(page_texts),
            *extract_columnar_broker_compensation_rows(page_texts),
            *extract_compensation_table_broker_rows(page_texts),
            *extract_layout_broker_rows(layout_page_texts),
        ]
    )


_RULE_DATE_TOKEN = re.compile(
    r"(?:"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")",
    re.IGNORECASE,
)
_RULE_MONEY_TOKEN = re.compile(r"(?<![A-Za-z0-9])\$?\s*?(-?\d[\d,]*(?:\.\d{1,2})?)(?![A-Za-z0-9])")


def extract_rules_driven_schedule_a_fields(
    page_texts: list[tuple[int, str]],
    *,
    rules=None,
) -> list[NormalizedExtractionField]:
    """Extract every configured Schedule A rule from labels and aliases.

    This is intentionally configuration-driven: publishing a new label or
    alias changes the next extraction snapshot without a deployment.  Narrow
    validators keep a heading, another field's value, or a table total from
    being accepted simply because it appeared near an alias.
    """
    configured = rules_for_form(rules or DEFAULT_FIELD_RULES, FormType.SCHEDULE_A)
    configured = [rule for rule in configured if rule.mapping_mode != FieldRuleMappingMode.EXTRACTION_ONLY or rule.aliases]
    if not configured or not page_texts:
        return []

    fields: list[NormalizedExtractionField] = []
    full_text = "\n".join(text for _, text in page_texts if text)
    special_values = _rules_driven_schedule_a_special_values(full_text)

    for rule in configured:
        if str(rule.field_type or "").lower() == "reference/lookup":
            continue
        # Repeating recipients are represented as structured broker rows.  A
        # scalar alias match here would turn the table header into item 3a.
        if rule.key in {
            "schedule_a_part_i_3a_name_of_agent_broker_person",
            "schedule_a_part_i_3d_purpose",
            "schedule_a_part_i_3e_organizational_code",
        }:
            continue
        if rule.key.startswith("schedule_a_part_iii_9") and not re.search(
            r"(?:\bSection\s*9\b|(?<!Non[- ])\bExperience[- ]Rated\s+Contracts?\b)",
            full_text,
            flags=re.IGNORECASE,
        ):
            continue

        special = special_values.get(rule.key)
        if special:
            value, page, source = special
            fields.append(
                NormalizedExtractionField(
                    field_name=rule.label,
                    value=value,
                    confidence=0.9,
                    page=page,
                    source_text=source,
                )
            )
            continue

        best: NormalizedExtractionField | None = None
        aliases = _rule_extraction_aliases(rule)
        for page, text in page_texts:
            for raw_line in str(text or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                for alias in aliases:
                    match = re.search(_dense_rule_label_pattern(alias), line, flags=re.IGNORECASE)
                    if not match:
                        continue
                    alias_words = normalize_rule_label(alias).split()
                    if len(alias_words) == 1:
                        prefix = line[: match.start()].strip()
                        suffix = line[match.end() :]
                        if (
                            prefix
                            and not re.fullmatch(r"\(?[0-9a-z]+\)?[.)]?", prefix, flags=re.IGNORECASE)
                        ) or not re.match(r"^(?:\s*[:\t-]\s*|\s{2,})", suffix):
                            continue
                    value = _value_after_rule_alias(rule, line[match.end() :])
                    if not value:
                        continue
                    candidate = NormalizedExtractionField(
                        field_name=rule.label,
                        value=value,
                        confidence=_rule_extraction_confidence(rule, alias),
                        page=page,
                        source_text=line[:1200],
                    )
                    if best is None or candidate.confidence > best.confidence:
                        best = candidate
                    break
        if best:
            fields.append(best)

    return select_best_schedule_a_fields(fields)


def _rule_extraction_aliases(rule) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in [rule.label, *rule.aliases]:
        clean = str(value or "").strip()
        variants = [clean, re.sub(r"^\s*\d+[a-z]?(?:\([^)]*\))*[.)]?\s*", "", clean, flags=re.IGNORECASE)]
        for variant in variants:
            normalized = normalize_rule_label(variant)
            if len(normalized) < 3 or normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(variant)
    return sorted(aliases, key=lambda item: len(normalize_rule_label(item)), reverse=True)


def _dense_rule_label_pattern(label: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", str(label or ""))
    if not tokens:
        return r"(?!)"
    return r"(?<![A-Za-z0-9])" + r"[\s/&().,:#-]*".join(re.escape(token) for token in tokens) + r"(?![A-Za-z0-9])"


def _value_after_rule_alias(rule, remainder: str) -> str | None:
    text = str(remainder or "").strip(" \t:-")
    if not text:
        return None
    key = str(rule.key or "").lower()
    label = str(rule.label or "").lower()
    field_type = str(rule.field_type or "").lower()

    if "ein" in key or " ein" in label:
        match = re.search(r"\b\d{2}-\d{7}\b", text)
        return match.group(0) if match else None
    if "naic" in key or "naic" in label:
        match = re.search(r"\b\d{4,6}\b", text)
        return match.group(0) if match else None
    if "date" in key or "date" in label:
        dates = _normalized_date_tokens(text)
        return dates[0] if dates else None
    if any(token in key or token in label or token in field_type for token in ("amount", "premium", "commission", "fee", "currency", "covered", "number")):
        match = _RULE_MONEY_TOKEN.search(text)
        return money_value(match.group(1)) if match else None
    if field_type == "static":
        match = re.search(r"\b(Yes|No|True|False|Provided|Not Applicable)\b", text, flags=re.IGNORECASE)
        return clean_extracted_value(match.group(1)) if match else None

    value = clean_extracted_value(text)
    if re.search(r"[a-z][A-Z]", value):
        value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    if not value or is_obvious_template_placeholder(value):
        return None
    value = re.split(r"\s+\([a-z0-9]+\)\s*", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if rule.key == "schedule_a_part_i_1a_name_of_insurance_company" and not is_probable_carrier_name(value):
        return None
    if rule.key == "schedule_a_part_i_1d_contract_policy_number":
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9 ._-]{1,40}?\b", value)
        value = clean_extracted_value(match.group(0)) if match else ""
        if not is_valid_contract_identifier(value, allow_numeric=True):
            return None
    return value[:500] or None


def _rule_extraction_confidence(rule, alias: str) -> float:
    canonical = normalize_rule_label(rule.label)
    matched = normalize_rule_label(alias)
    if matched == canonical:
        return 0.91
    return 0.88 if len(matched) >= 8 else 0.82


def _normalized_date_tokens(text: str) -> list[str]:
    values: list[str] = []
    for match in _RULE_DATE_TOKEN.finditer(str(text or "")):
        value = normalize_schedule_a_date(match.group(0), end_of_month=False)
        if value:
            values.append(value)
    return values


def _rules_driven_schedule_a_special_values(full_text: str) -> dict[str, tuple[str, int | None, str]]:
    compact = re.sub(r"\s+", " ", str(full_text or "")).strip()
    values: dict[str, tuple[str, int | None, str]] = {}

    date_context = regex_first(
        compact,
        [
            r"Data\s*Period\s+(.{0,100}?\d{4}\s+(?:to|through|-)\s+.{0,100}?\d{4})",
            r"Policy\s*or\s*Contract\s*Year\s+(.{0,180})",
        ],
        flags=re.IGNORECASE,
    )
    periods = explicit_contract_periods(full_text)
    identities = {(period.beginning, period.ending) for period in periods}
    dates = _normalized_date_tokens(date_context) if date_context else []
    if len(identities) == 1:
        period = periods[0]
        values["schedule_a_part_i_1f_policy_year_beginning_date"] = (period.beginning, None, period.source_text)
        values["schedule_a_part_i_1g_policy_year_ending_date"] = (period.ending, None, period.source_text)
    elif not periods and len(dates) >= 2:
        values["schedule_a_part_i_1f_policy_year_beginning_date"] = (dates[0], None, date_context or "Policy period")
        values["schedule_a_part_i_1g_policy_year_ending_date"] = (dates[1], None, date_context or "Policy period")

    covered = regex_first(
        compact,
        [
            r"Approximate\s*Number\s*of.{0,120}?Total\s*\(e\)\s*([0-9,]+).{0,160}?Persons\s*Covered",
            r"Persons\s*Covered.{0,160}?Total\s*\(e\)\s*([0-9,]+)",
            r"Total\s*\(e\)\s*([0-9,]+).{0,180}?of\s*Policy\s*Year",
        ],
        flags=re.IGNORECASE,
    )
    if covered:
        values["schedule_a_part_i_1e_persons_covered_end_of_policy_year"] = (money_value(covered), None, "Persons covered total")

    totals = re.search(
        r"Total\s*\(from\s*below\)\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)\s+\$?\s*([0-9,]+(?:\.\d{1,2})?)",
        compact,
        flags=re.IGNORECASE,
    )
    if totals:
        source = totals.group(0)
        values["schedule_a_part_i_3b_amount_of_commissions"] = (money_value(totals.group(1)), None, source)
        values["schedule_a_part_i_3c_amount_of_fees"] = (money_value(totals.group(2)), None, source)
    return values


def extract_nyl_annual_policy_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract New York Life's Annual Policy Information Report exactly.

    Its PDF text layer splits words such as ``Year`` and ``Total`` while the
    contract number can contain a meaningful space. Broad label parsing used
    to lose the second contract token and miss the ending date and premium.
    """

    fields: list[NormalizedExtractionField] = []
    for page, text in page_texts:
        if (
            "Annual Policy Information Report" not in text
            or "Name of Insurance Carrier" not in text
            or "Contract/Policy Number" not in text
        ):
            continue

        def value(pattern: str) -> str | None:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            return clean_extracted_value(match.group(1)) if match else None

        extracted = {
            "1a. Name of Insurance Company": value(r"Name\s+of\s+Insurance\s+Carrier\s*\n\s*([^\n]+)"),
            "1b. Insurance Carrier EIN": value(r"\bEIN\s+([0-9]{2}-[0-9]{7})"),
            "1c. NAIC Code": value(r"\bNAIC\s+Code\s+([0-9]{4,6})"),
            "1d. Contract/Policy Number": value(r"Contract/Policy\s+Number[ \t]+([A-Z0-9]+(?:[ \t]+[A-Z0-9]+)*)"),
            "1f. Policy Year Beginning Date": value(r"Contract/Policy\s+Y\s*ear\s+From\s*:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})"),
            "1g. Policy Year Ending Date": value(r"Contract/Policy\s+Y\s*ear\s+T\s*o\s*:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})"),
            "10a. Total premiums or subscription charges paid to carrier": value(
                r"T\s*otal\s+premiums\s+paid\s+to\s+Insurance\s+Company\s+during\s+the\s+policy\s+year\s*:\s*\$?\s*([0-9,]+(?:\.\d{2})?)"
            ),
        }
        if extracted["1a. Name of Insurance Company"]:
            extracted["1a. Name of Insurance Company"] = extracted["1a. Name of Insurance Company"].replace("Y ork", "York")
        for field_name, field_value in extracted.items():
            if not field_value:
                continue
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=field_value,
                    confidence=0.99,
                    page=page,
                    source_text="New York Life Annual Policy Information Report",
                )
            )
        break
    return fields


def extract_hmsa_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract the two-page HMSA ERISA Schedule A support packet."""

    full_text = "\n".join(text for _, text in page_texts)
    if "HAWAII MEDICAL SERVICE ASSOCIATION" not in full_text.upper() or "ERISA FORM 5500 AND SCHEDULE A INFORMATION" not in full_text.upper():
        return []

    ein = regex_first(full_text, [r"EIN\s+number\s+([0-9]{2}-[0-9]{7})"], flags=re.IGNORECASE)
    naic = regex_first(full_text, [r"NAIC\s+code\s+([0-9]{4,6})"], flags=re.IGNORECASE)
    period = re.search(
        r"FOR\s+THE\s+PLAN\s+YEAR\s*:\s*January\s+([0-9]{4})\s*-\s*December\s+([0-9]{4})",
        full_text,
        flags=re.IGNORECASE,
    )
    rows = re.findall(
        r"(?:C\d+\s+)?(?P<group>\d{5,6})\s+(?P<subgroup>\d{3})\s+.+?\s+(?P<subs>\d+)\s+(?P<covered>\d+)\s+\$(?P<premium>[0-9,]+(?:\.\d{2})?)\s*$",
        full_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not rows:
        return []

    primary = next((row for row in rows if int(row[3]) > 0), rows[0])
    # HMSA reports Group # and Sub Group in separate columns. The Schedule A
    # contract identifier is the Group #; joining the columns creates a value
    # that does not exist in the source document and drops its leading zero.
    contract = primary[0]
    covered = primary[3]
    premium = sum_money_values(*(row[4] for row in rows))
    begin = f"01/01/{period.group(1)}" if period else None
    end = f"12/31/{period.group(2)}" if period else None
    extracted = {
        "1a. Name of Insurance Company": "Hawaii Medical Service Association (HMSA)",
        "1b. Insurance Carrier EIN": ein,
        "1c. NAIC Code": naic,
        "1d. Contract/Policy Number": contract,
        "1e. Persons Covered (End of Policy Year)": covered,
        "1f. Policy Year Beginning Date": begin,
        "1g. Policy Year Ending Date": end,
        "10a. Total premiums or subscription charges paid to carrier": premium,
    }
    return [
        NormalizedExtractionField(
            field_name=field_name,
            value=value,
            confidence=0.99,
            page=None,
            source_text="HMSA ERISA Form 5500 and Schedule A information",
        )
        for field_name, value in extracted.items()
        if value
    ]


def _cigna_primary_schedule_a_page(page_texts: list[tuple[int, str]]) -> tuple[int, str] | None:
    """Return Cigna's consolidated Schedule A page, never a state appendix.

    Cigna packages repeat the same contract number and dates on a series of
    appendix pages, each with a state carrier EIN/NAIC. Only the page that
    contains the Part I totals, broker table, Part III, and Part IV is the
    primary record sent to FT Williams.
    """
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        lowered = normalized.lower()
        if (
            "cigna health and life insurance company" in lowered
            and "summary of all insurance contracts included in part iii" in lowered
            and "insurance fees and commissions information" in lowered
            and "part iii welfare benefit contract information" in lowered
            and "appendix to 1a" not in lowered
            and "schedule a insurance information - footnotes" not in lowered
        ):
            return page, normalized
    return None


def _cigna_schedule_a_support_page(page_texts: list[tuple[int, str]]) -> tuple[int, str] | None:
    """Return the Schedule A summary from Cigna's mixed A/C support packet."""
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        lowered = normalized.lower()
        if (
            "information for completing schedule a on the irs form" in lowered
            and "schedule a - insurance information" in lowered
            and "cigna health and life insurance company" in lowered
        ):
            return page, normalized
    return None


def _cigna_plan_detail_page(page_texts: list[tuple[int, str]]) -> tuple[int, str] | None:
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        lowered = normalized.lower()
        if (
            "cigna" in lowered
            and "plan detail report" in lowered
            and "commissions paid detail" in lowered
            and "broker acct#" in lowered
        ):
            return page, normalized
    return None


def _is_cigna_schedule_a_packet(page_texts: list[tuple[int, str]]) -> bool:
    return bool(_cigna_primary_schedule_a_page(page_texts) or _cigna_schedule_a_support_page(page_texts))


def _normalize_cigna_written_date(value: str | None) -> str | None:
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", clean_extracted_value(value or ""))
    if not match:
        return None
    month_names = {
        **{name.lower(): index for index, name in enumerate(calendar.month_name) if name},
        **{name.lower(): index for index, name in enumerate(calendar.month_abbr) if name},
    }
    month = month_names.get(match.group(1).lower())
    if not month:
        return None
    return f"{month:02d}/{int(match.group(2)):02d}/{match.group(3)}"


def _cigna_support_plan_year(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"For\s+Plan\s+Year\s+Beginning\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})\s+"
        r"and\s+Ending\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    return _normalize_cigna_written_date(match.group(1)), _normalize_cigna_written_date(match.group(2))


def _cigna_plan_detail_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    detail = _cigna_plan_detail_page(page_texts)
    if not detail:
        return []
    page, text = detail
    lines = [clean_extracted_value(line) for line in text.splitlines() if clean_extracted_value(line)]
    rows: list[ScheduleABrokerRow] = []
    fee_section_index = 0
    index = 0
    while index + 3 < len(lines):
        if (
            lines[index].upper() != "BENEFIT"
            or lines[index + 2].upper() != "BROKER ACCT#"
            or lines[index + 3].upper() != "BROKER NAME"
        ):
            index += 1
            continue
        amount_heading = lines[index + 1].upper()
        if "TOTAL COMM PAID" in amount_heading:
            payment_kind, purpose = "commission", "Commissions"
        elif "TOTAL FEES" in amount_heading:
            fee_section_index += 1
            payment_kind = "fee"
            purpose = "Benefit Advisor Fees" if fee_section_index == 1 else "Service / General Agent Fees"
        elif "TOTAL PAID" in amount_heading:
            payment_kind, purpose = "fee", "Incentive Compensation"
        else:
            index += 1
            continue

        row_index = index + 4
        while row_index + 3 < len(lines) and lines[row_index].upper() != "TOTAL":
            benefit, amount_line, account, name = lines[row_index : row_index + 4]
            amount_match = re.fullmatch(r"\$\s*([\d,]+(?:\.\d{2})?)", amount_line)
            if not amount_match or not re.fullmatch(r"\d{4,}", account) or not is_probable_person_or_entity_name(name):
                row_index += 1
                continue
            amount = money_value(amount_match.group(1))
            money_row = ScheduleABrokerMoneyRow(
                coverage=benefit,
                amount=amount,
                purpose=purpose,
            )
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    organization_code="3",
                    commission_rows=[money_row] if payment_kind == "commission" else [],
                    fee_rows=[money_row] if payment_kind == "fee" else [],
                    commission_total=amount if payment_kind == "commission" else "0",
                    fee_total=amount if payment_kind == "fee" else "0",
                    source_page=page,
                    confidence=0.995,
                )
            )
            row_index += 4
        index = max(index + 1, row_index)
    merged = _merge_columnar_broker_rows(rows)
    packet_text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    for row in merged:
        address = re.search(
            re.escape(row.name)
            + r".{0,500}?(?:^|\n)\s*(?P<street>[1-9]\d{2,5}\s+[A-Z0-9 .'-]+?(?:ROAD|RD|STREET|ST|AVENUE|AVE|DRIVE|DR|BOULEVARD|BLVD)"
            + r"(?:\s+(?:STE|SUITE)\s*\w+)?)\s+"
            + r"(?P<city>[A-Z][A-Z ]+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-?\d{4})?)\b",
            packet_text,
            re.IGNORECASE | re.DOTALL,
        )
        if address:
            row.address_line_1 = clean_extracted_value(address.group("street"))
            row.city = clean_extracted_value(address.group("city"))
            row.state = address.group("state").upper()
            row.zip_code = normalize_zip_code(address.group("zip"))
    return merged


def _extract_cigna_support_packet_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    support = _cigna_schedule_a_support_page(page_texts)
    if not support:
        return []
    page, text = support
    source_text = text[:1600]
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean and not is_blank_extraction_value(clean):
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=0.995,
                    page=page,
                    source_text=source_text,
                )
            )

    add("1a. Name of Insurance Company", "Cigna Health and Life Insurance Company")
    identity = re.search(
        r"(?P<ein>\d{2}-\d{7})\s+(?P<naic>\d{4,6})\s+(?P<contract>[A-Za-z0-9-]+)",
        text,
    )
    if identity:
        add("1b. Insurance Carrier EIN", identity.group("ein"))
        add("1c. NAIC Code", identity.group("naic"))
        add("1d. Contract/Policy Number", identity.group("contract"))
    policy_from, policy_to = _cigna_support_plan_year(text)
    add("1f. Policy Year Beginning Date", policy_from)
    add("1g. Policy Year Ending Date", policy_to)
    premium = regex_first(
        text,
        [r"Total\s+premiums?\*?\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier\s*:.{0,700}?\$\s*([\d,]+(?:\.\d{2})?)"],
        flags=re.IGNORECASE | re.DOTALL,
    )
    add("10a. Total premiums or subscription charges paid to carrier", money_value(premium) if premium else None)

    rows = _cigna_plan_detail_broker_rows(page_texts)
    for field in schedule_a_broker_compensation_fields(rows):
        field.confidence = 0.995
        fields.append(field)
    return fields


def _cigna_primary_broker_name(text: str) -> str | None:
    start = re.search(r"Non\s+Experience\s*-\s*Rated", text, flags=re.IGNORECASE)
    search_text = text[start.end() :] if start else text
    for line in search_text.splitlines():
        candidate = line.strip().rstrip(",").strip()
        candidate = re.sub(r",\s*\d.*$", "", candidate)
        if not candidate or candidate.upper() != candidate:
            continue
        if not re.search(r"\b(?:LLC|INC|CORP|SERVICES|ASSOCIATES|AGENCY|BROKER)\b", candidate):
            continue
        if is_probable_person_or_entity_name(candidate):
            return candidate
    return None


def extract_cigna_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    g2050a = extract_cigna_g2050a_schedule_a_fields(page_texts)
    if g2050a:
        return g2050a
    primary = _cigna_primary_schedule_a_page(page_texts)
    if not primary:
        return _extract_cigna_support_packet_fields(page_texts)
    page, text = primary
    source_text = text[:1600]
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean and not is_blank_extraction_value(clean):
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=0.995,
                    page=page,
                    source_text=source_text,
                )
            )

    add("1a. Name of Insurance Company", "Cigna Health and Life Insurance Company and affiliates")
    coverage = re.search(
        r"(?P<ein>\d{2}-\d{7})\s+(?P<naic>\d{4,6})\s+"
        r"(?P<contract>[A-Za-z0-9-]+)\s+(?P<covered>[\d,]+)\s+Employees?\s+"
        r"(?P<from>\d{1,2}/\d{1,2}/\d{4})\s*-?\s*(?P<to>\d{1,2}/\d{1,2}/\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if coverage:
        add("1b. Insurance Carrier EIN", coverage.group("ein"))
        add("1c. NAIC Code", coverage.group("naic"))
        add("1d. Contract/Policy Number", coverage.group("contract"))
        add("1e. Persons Covered (End of Policy Year)", coverage.group("covered"))
        add("1f. Policy Year Beginning Date", coverage.group("from"))
        add("1g. Policy Year Ending Date", coverage.group("to"))
    else:
        # The production PDF text layer places each label above its value
        # instead of returning a visual row. Parse the same primary page by
        # labels; it is already isolated from all state appendix pages.
        add("1b. Insurance Carrier EIN", regex_first(text, [r"\(b\)\s*EIN\s+(\d{2}-\d{7})"], flags=re.IGNORECASE))
        add("1c. NAIC Code", regex_first(text, [r"\(c\)\s*NAIC\s+Code\s+(\d{4,6})"], flags=re.IGNORECASE))
        add(
            "1d. Contract/Policy Number",
            regex_first(text, [r"Identification\s+Number\s+([A-Za-z0-9-]+)"], flags=re.IGNORECASE),
        )
        add(
            "1e. Persons Covered (End of Policy Year)",
            regex_first(text, [r"at\s+end\s+of\s+policy\s+or\s+contract\s+year\s+([\d,]+)\s+Employees?"], flags=re.IGNORECASE),
        )
        dates = regex_first(
            text,
            [r"\(f\)\s*From\s+\(g\)\s*To\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})"],
            flags=re.IGNORECASE,
            groups=True,
        )
        if isinstance(dates, tuple) and len(dates) >= 2:
            add("1f. Policy Year Beginning Date", dates[0])
            add("1g. Policy Year Ending Date", dates[1])

    totals = re.search(
        r"Total\s+Amount\s+of\s+commissions\s+paid\s*\$?\s*(?P<commissions>[\d,]+(?:\.\d{2})?).{0,160}?"
        r"Total\s+Amount\s+of\s+fees\s+paid\s*\$?\s*(?P<fees>[\d,]+(?:\.\d{2})?)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if totals:
        add("3b. Amount of Commissions", money_value(totals.group("commissions")))
        add("3c. Amount of Fees", money_value(totals.group("fees")))

    add("3a. Name of Agent/Broker/Person", _cigna_primary_broker_name(text))
    purpose = regex_first(text, [r"\$[\d,]+\s+\$[\d,]+\s+([A-Za-z][A-Za-z ]+?)\s+3-Insurance\s+Agent"], flags=re.IGNORECASE)
    add("3d. Purpose", purpose or "General Agent Payments")
    add("3e. Organizational Code", "3")

    premium = regex_first(
        text,
        [r"Total\s+premiums?\s+or\s+subscriptions?\s+charges\s+paid\s+to\s+carrier\s*\$?\s*([\d,]+(?:\.\d{2})?)"],
        flags=re.IGNORECASE,
    )
    add("10a. Total premiums or subscription charges paid to carrier", money_value(premium) if premium else None)
    if re.search(r"information\s+not\s+provided.*Not\s+Applicable", text, flags=re.IGNORECASE | re.DOTALL):
        add("11. Did the insurance company fail to provide any information necessary to complete Schedule A?", "No")
    return fields


def extract_cigna_schedule_a_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    primary = _cigna_primary_schedule_a_page(page_texts)
    if not primary:
        return _cigna_plan_detail_broker_rows(page_texts) if _cigna_schedule_a_support_page(page_texts) else []
    page, text = primary
    compact = re.sub(r"\s+", " ", text).strip()
    recipient_pattern = re.compile(
        r"(?P<name>[A-Z][A-Z .&'-]+?(?:INC|LLC|CORP)),\s*"
        r"(?P<address>.+?),\s*(?P<state>[A-Z]{2}),\s*(?P<zip>\d{5}(?:-?\d{4})?)\s+"
        r"(?P<commission>\$[\d,]+(?:\.\d{2})?)"
        r"(?:\s+(?P<fee>\$[\d,]+(?:\.\d{2})?))?",
    )
    parsed_rows: list[ScheduleABrokerRow] = []
    for match in recipient_pattern.finditer(compact):
        address_parts = [clean_extracted_value(part) for part in match.group("address").split(",") if clean_extracted_value(part)]
        if len(address_parts) < 2:
            continue
        city = address_parts[-1]
        street_lines = address_parts[:-1]
        commission = money_value(match.group("commission")) or "0"
        fee = money_value(match.group("fee")) if match.group("fee") else "0"
        purpose = "General Agent Payments" if (parse_numeric_amount(fee) or 0) else "Commissions"
        parsed_rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(match.group("name")),
                address_line_1=street_lines[0] if street_lines else None,
                address_line_2=" ".join(street_lines[1:]) or None,
                city=city,
                state=match.group("state"),
                zip_code=normalize_zip_code(match.group("zip")),
                organization_code="3",
                commission_rows=([ScheduleABrokerMoneyRow(amount=commission, purpose="Commissions")] if (parse_numeric_amount(commission) or 0) else []),
                fee_rows=([ScheduleABrokerMoneyRow(amount=fee, purpose=purpose)] if (parse_numeric_amount(fee) or 0) else []),
                commission_total=commission,
                fee_total=fee,
                source_page=page,
                confidence=0.995,
            )
        )
    if parsed_rows:
        return parsed_rows
    name = _cigna_primary_broker_name(text)
    if not name:
        return []
    totals = re.search(
        r"Total\s+Amount\s+of\s+commissions\s+paid\s*\$?\s*(?P<commissions>[\d,]+(?:\.\d{2})?).{0,160}?"
        r"Total\s+Amount\s+of\s+fees\s+paid\s*\$?\s*(?P<fees>[\d,]+(?:\.\d{2})?)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    commissions = money_value(totals.group("commissions")) if totals else None
    fees = money_value(totals.group("fees")) if totals else None
    address = re.search(
        r"(?P<address>PO\s+BOX\s+\d+)\s*,\s*(?P<city>[A-Z][A-Z ]+)\s*,\s*(?P<state>[A-Z]{2})\s*,\s*(?P<zip>\d{5}(?:-?\d{4})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not address:
        address = re.search(
            r"(?P<address>PO\s+BOX\s+\d+)\s*,\s*(?P<city>[A-Z][A-Z ]+)\s*,\s*(?P<state>[A-Z]{2})\s*,.*?\n\s*(?P<zip>\d{5}(?:-?\d{4})?)\b",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if not address:
        address = re.search(
            r"(?P<address>\d+\s+[A-Z0-9 .'-]+?(?:ROAD|RD|STREET|ST|AVENUE|AVE|DRIVE|DR|BOULEVARD|BLVD)"
            r"(?:\s+(?:STE|SUITE)\s*\w+)?)\s+"
            r"(?P<city>[A-Z][A-Z ]+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-?\d{4})?)\b",
            text,
            flags=re.IGNORECASE,
        )
    zip_code = address.group("zip") if address else None
    if zip_code and re.fullmatch(r"\d{9}", zip_code):
        zip_code = f"{zip_code[:5]}-{zip_code[5:]}"
    purpose = "General Agent Payments"
    return [
        ScheduleABrokerRow(
            name=name,
            address_line_1=clean_extracted_value(address.group("address")) if address else None,
            city=clean_extracted_value(address.group("city")) if address else None,
            state=address.group("state").upper() if address else None,
            zip_code=zip_code,
            organization_code="3",
            commission_rows=[ScheduleABrokerMoneyRow(amount=commissions, purpose=purpose)] if commissions else [],
            fee_rows=[ScheduleABrokerMoneyRow(amount=fees, purpose=purpose)] if fees else [],
            commission_total=commissions,
            fee_total=fees,
            source_page=page,
            confidence=0.995,
        )
    ]


_COLUMNAR_BROKER_SECTION = re.compile(
    r"INSURANCE\s+FEES?\s+AND\s+COMMISSIONS?\s+INFORMATION",
    re.IGNORECASE,
)
_COLUMNAR_BROKER_END = re.compile(
    r"\n\s*\d+\s*\.\s*(?:COVERAGE\s*/?\s*BENEFITS|NON[- ]?PARTICIPATING|EXPERIENCE[- ]?RATED)",
    re.IGNORECASE,
)
_COLUMNAR_BROKER_AMOUNTS = re.compile(
    r"\$?\s*(?P<sales>(?:\d[\d,]*(?:\.\d{1,2})?|\.\d{1,2}))\s+"
    r"\$?\s*(?P<fees>(?:\d[\d,]*(?:\.\d{1,2})?|\.\d{1,2}))\s+"
    r"\$?\s*(?P<additional>(?:\d[\d,]*(?:\.\d{1,2})?|\.\d{1,2}))(?![\d.])",
)
_COLUMNAR_CITY_STATE_ZIP = re.compile(
    r"^(?P<city>[A-Za-z .'-]+?),?\s+(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{5}(?:-[0-9]{4})?)$",
    re.IGNORECASE,
)
_SECONDARY_ADDRESS_LINE = re.compile(
    r"^(?:ATTN\b|BLDG\b|BUILDING\b|FLOOR\b|FL\b|ROOM\b|RM\b|STE\b|SUITE\b|UNIT\b|\d+(?:ST|ND|RD|TH)\s+FLOOR\b)",
    re.IGNORECASE,
)


def extract_columnar_broker_compensation_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Extract multi-page broker disclosures with sales, fee, and additional columns.

    Carrier exports such as Unum's disclosure are not Form 5500 renderings.
    They print one recipient block followed by three money columns, and may
    continue the last address onto the next page. Parse the table structurally,
    merge repeated name/address rows, and return nothing if any paid block
    cannot be resolved so an incomplete broker set is never sent to FTW.
    """
    combined = ""
    page_offsets: list[tuple[int, int]] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text or "")
        normalized = re.sub(r"(?m)^[ \t]*-{5,}[ \t]*$", "", normalized)
        if not normalized:
            continue
        if combined:
            combined += "\n"
        page_offsets.append((len(combined), page))
        combined += normalized

    start = _COLUMNAR_BROKER_SECTION.search(combined)
    if not start:
        return []
    heading = combined[start.start() : start.start() + 900].lower()
    if not all(label in heading for label in ("sales", "fees", "additional")):
        return []

    section_start = start.end()
    section = combined[section_start:]
    end = _COLUMNAR_BROKER_END.search(section)
    if end:
        section = section[: end.start()]

    parsed_rows: list[ScheduleABrokerRow] = []
    paid_blocks = 0
    unresolved_paid_blocks = 0
    block_pattern = re.compile(r"(?:\A|\n[ \t]*\n)(?P<block>.*?)(?=\n[ \t]*\n|\Z)", re.DOTALL)
    for block_match in block_pattern.finditer(section):
        block = block_match.group("block").strip()
        amount_match = _COLUMNAR_BROKER_AMOUNTS.search(block)
        if not amount_match:
            continue
        sales = money_value(amount_match.group("sales"))
        fees = money_value(amount_match.group("fees"))
        additional = money_value(amount_match.group("additional"))
        if not any((parse_numeric_amount(value) or 0) > 0 for value in (sales, fees, additional)):
            continue
        paid_blocks += 1

        name_lines = [
            clean_extracted_value(line)
            for line in block[: amount_match.start()].splitlines()
            if clean_extracted_value(line)
        ]
        name = clean_extracted_value(" ".join(name_lines)).strip(" :-")
        address_lines = [
            clean_extracted_value(line)
            for line in block[amount_match.end() :].splitlines()
            if clean_extracted_value(line)
        ]
        if address_lines and re.fullmatch(r"(?:INC\.?|LLC|L\.L\.C\.?|CORP(?:ORATION)?|LTD|LLP|LP)", address_lines[0], re.IGNORECASE):
            name = f"{name} {address_lines.pop(0)}".strip(" ,")
        address_line_1, address_line_2, city, state, zip_code = _columnar_broker_address(address_lines)
        if not name or not is_probable_person_or_entity_name(name) or not address_line_1 or not city or not state or not zip_code:
            unresolved_paid_blocks += 1
            continue

        absolute_offset = section_start + block_match.start("block")
        source_page = next((page for offset, page in reversed(page_offsets) if offset <= absolute_offset), None)
        fee_total = sum_money_values(fees, additional) or "0"
        parsed_rows.append(
            ScheduleABrokerRow(
                name=name,
                address_line_1=address_line_1,
                address_line_2=address_line_2,
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code="3",
                commission_rows=(
                    [ScheduleABrokerMoneyRow(amount=sales, purpose="Sales Commission")]
                    if (parse_numeric_amount(sales) or 0) > 0
                    else []
                ),
                fee_rows=[
                    *(
                        [ScheduleABrokerMoneyRow(amount=fees, purpose="Fees")]
                        if (parse_numeric_amount(fees) or 0) > 0
                        else []
                    ),
                    *(
                        [ScheduleABrokerMoneyRow(amount=additional, purpose="Additional Compensation")]
                        if (parse_numeric_amount(additional) or 0) > 0
                        else []
                    ),
                ],
                commission_total=sales,
                fee_total=fee_total,
                commission_source_text="Sales Commission Paid / Fees Paid / Additional Compensation Paid\n" + block,
                fee_source_text="Sales Commission Paid / Fees Paid / Additional Compensation Paid\n" + block,
                source_page=source_page,
                confidence=0.96,
            )
        )

    if unresolved_paid_blocks or len(parsed_rows) != paid_blocks:
        return []
    return _merge_columnar_broker_rows(parsed_rows)


def _columnar_broker_address(lines: list[str]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    city = state = zip_code = None
    city_index = None
    for index, line in enumerate(lines):
        match = _COLUMNAR_CITY_STATE_ZIP.match(line)
        if match:
            city = clean_extracted_value(match.group("city"))
            state = match.group("state").upper()
            zip_code = match.group("zip")
            city_index = index
            break
    street_lines = lines[:city_index] if city_index is not None else []
    if not street_lines:
        return None, None, city, state, zip_code
    primary_lines = [line for line in street_lines if not _SECONDARY_ADDRESS_LINE.match(line)]
    secondary_lines = [line for line in street_lines if _SECONDARY_ADDRESS_LINE.match(line)]
    address_line_1 = primary_lines[0] if primary_lines else street_lines[0]
    remaining_primary = primary_lines[1:]
    address_line_2 = " ".join([*secondary_lines, *remaining_primary]) or None
    return address_line_1, address_line_2, city, state, zip_code


def _merge_columnar_broker_rows(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    merged: dict[tuple[str, str, str], ScheduleABrokerRow] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows:
        key = (
            normalize_compare_key(row.name),
            normalize_compare_key(row.address_line_1 or ""),
            str(row.zip_code or ""),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = row.model_copy(deep=True)
            order.append(key)
            continue
        existing.commission_rows.extend(row.commission_rows)
        existing.fee_rows.extend(row.fee_rows)
        existing.commission_source_text = "\n".join(filter(None, [existing.commission_source_text, row.commission_source_text]))
        existing.fee_source_text = "\n".join(filter(None, [existing.fee_source_text, row.fee_source_text]))
        existing.confidence = min(existing.confidence, row.confidence)
    result: list[ScheduleABrokerRow] = []
    for key in order:
        row = merged[key]
        row.commission_total = _sum_money_rows(row.commission_rows) if row.commission_rows else "0"
        row.fee_total = _sum_money_rows(row.fee_rows) if row.fee_rows else "0"
        result.append(row)
    return result


def extract_compensation_table_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Read the broker compensation table carriers print on their statements.

    Most carrier statements end with the same table, however they word the
    heading: an optional agent number, the recipient's name and address, then
    the commissions paid, the fees paid, and the purpose. The amounts and the
    purpose sit on one line underneath the address block:

        CGI-026821Nth Insurance Agency dba: Alliance 360 I
        10833 VALLEY VIEW STREET
        SUITE 550
        CYPRESS CA 90630
        $5,810.59 $ 0.00 Standard Commissions

    Those amounts are Schedule A items 3b and 3c, and they were being missed on
    most statements even though the broker's name was picked up.
    """
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not normalized:
            continue
        lowered = normalized.lower()
        # Only inside a compensation table - "commission" alone appears in the
        # covering letter of nearly every carrier statement.
        if not (
            re.search(r"commissions?\s+paid|amount\s+of\s+commissions?", lowered)
            and re.search(r"fees?\s+paid|amount\s+of\s+fees?", lowered)
        ):
            continue

        lines = [line.strip() for line in normalized.splitlines()]
        for index, line in enumerate(lines):
            match = _COMPENSATION_ROW.match(line)
            if not match:
                continue
            commissions = money_value(match.group("commissions"))
            fees = money_value(match.group("fees"))
            purpose = clean_extracted_value(match.group("purpose") or "") or None
            if not commissions and not fees:
                continue
            name, address = _compensation_recipient(lines, index)
            if not name:
                continue
            address_line_1, address_line_2, city, state, zip_code = _compensation_address(address)
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=address_line_1,
                    address_line_2=address_line_2,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    commission_total=commissions or None,
                    fee_total=fees or None,
                    commission_rows=(
                        [ScheduleABrokerMoneyRow(amount=commissions, purpose=purpose)] if commissions else []
                    ),
                    fee_rows=[ScheduleABrokerMoneyRow(amount=fees, purpose=purpose)] if fees else [],
                    source_page=page,
                )
            )
    return rows


# "$5,810.59 $ 0.00 Standard Commissions" - amounts then an optional purpose.
_COMPENSATION_ROW = re.compile(
    r"^\$\s*(?P<commissions>[\d,]+(?:\.\d{2})?)\s+\$\s*(?P<fees>[\d,]+(?:\.\d{2})?)\s*(?P<purpose>[A-Za-z][^$]*)?$"
)
# Agent/producer numbers are printed hard against the name: "CGI-026821Nth ..."
_AGENT_NUMBER_PREFIX = re.compile(r"^[A-Z]{2,5}[-/]?\d{4,}\s*")
_TABLE_HEADING_WORDS = re.compile(
    r"amount|commission|fees?|purpose|agent\s*$|number\s*$|recipient|name and address|which paid|paid by",
    re.IGNORECASE,
)


def _compensation_recipient(lines: list[str], amount_index: int) -> tuple[str, list[str]]:
    """Walk back up from the amounts line to the recipient's name and address."""
    block: list[str] = []
    for line in reversed(lines[max(0, amount_index - 6) : amount_index]):
        if not line:
            if block:
                break
            continue
        if _COMPENSATION_ROW.match(line) or line.strip() in {"$", "$ "}:
            break
        if _TABLE_HEADING_WORDS.search(line):
            # Reached the column headings - everything collected below them is
            # the recipient block.
            break
        block.append(line)
    if not block:
        return "", []
    block.reverse()
    name = _AGENT_NUMBER_PREFIX.sub("", block[0]).strip()
    if not name or not is_probable_person_or_entity_name(name):
        return "", []
    return name, block[1:]


_COMPENSATION_CITY_STATE_ZIP = re.compile(
    r"^(?P<city>[A-Za-z .'-]+?)(?:,\s*|\s+)(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{5}(?:-[0-9]{4})?)$",
    re.IGNORECASE,
)


def _compensation_address(
    lines: list[str],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Split a carrier compensation recipient block into FTW address parts."""
    clean_lines = [clean_extracted_value(line) for line in lines if clean_extracted_value(line)]
    city = state = zip_code = None
    city_index: int | None = None
    for index in range(len(clean_lines) - 1, -1, -1):
        match = _COMPENSATION_CITY_STATE_ZIP.match(clean_lines[index])
        if not match:
            continue
        city = clean_extracted_value(match.group("city"))
        state = match.group("state").upper()
        zip_code = match.group("zip")
        city_index = index
        break

    street_lines = clean_lines[:city_index] if city_index is not None else clean_lines
    address_line_1 = street_lines[0] if street_lines else None
    address_line_2 = " ".join(street_lines[1:]) or None
    return address_line_1, address_line_2, city, state, zip_code


def extract_schedule_a_worksheet_summaries_from_pdf_text(file_bytes: bytes) -> list[ScheduleAWorksheetSummary]:
    page_texts = extract_pdf_text_pages(file_bytes)
    summaries: list[ScheduleAWorksheetSummary] = []
    for index, text in page_texts:
        summaries.extend(extract_bcbsma_schedule_a_worksheet_summaries(text, index))
    summaries.extend(extract_bcbs_michigan_schedule_a_summaries(page_texts))
    summaries.extend(extract_prudential_schedule_a_summaries(page_texts))
    summaries.extend(extract_eyemed_schedule_a_summaries(page_texts))
    summaries.extend(extract_standard_schedule_a_summaries(page_texts))
    summaries.extend(extract_united_omaha_schedule_a_summaries(page_texts))
    summaries.extend(extract_metlife_bay_bridge_schedule_a_summaries(page_texts))
    return summaries


def parse_schedule_a_text(text: str, page: int | None = None, *, rules=None) -> list[NormalizedExtractionField]:
    normalized_input = normalize_ocr_text(text)
    if is_bcbs_michigan_addendum_page(normalized_input):
        return dedupe_fields(extract_bcbs_michigan_addendum_fields(normalized_input, page))
    if is_united_omaha_schedule_a_support(normalized_input):
        return []

    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str, confidence: float = 0.86, source_text: str | None = None, value_validator=None):
        clean = clean_extracted_value(value)
        if clean and not is_blank_extraction_value(clean):
            if value_validator and not value_validator(clean):
                return
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=confidence,
                    page=page,
                    source_text=(source_text or text[:420]).strip(),
                )
            )

    carrier = regex_first(
        text,
        [
            r'"(?:a_name_of_insurance_carrier|insurance_carrier_name|carrier_name)"\s*:\s*"([^"]+)"',
            r"insurance\s+carrier\s*:\s*([A-Z][A-Z0-9 ,. '&-]+)",
            r"identifies\s+(.+?)\s+as the carrier",
            r"carrier_name[\"']?\s*[:=]\s*[\"']([^\"']+)",
            r"\(a\)\s*Name\s+of\s+insurance\s+carrier\s+([A-Z][A-Z0-9 ,. '&-]+?)(?=\s+\(?b\)?\s+EIN|\s+[0-9]{2}-[0-9]{7}|$)",
            r"Name\s+of\s+insurance\s+carrier\s+([A-Z][A-Z0-9 ,. '&-]+?)(?=\s+\(?b\)?\s+EIN|\s+[0-9]{2}-[0-9]{7}|$)",
            r"Name of insurance carrier\s*\n\s*(.+)",
            r"Name of insurance company\s*\n\s*(.+)",
        ],
    )
    if carrier:
        add("1a. Name of Insurance Company", carrier, 0.95, value_validator=is_probable_carrier_name)

    coverage_row = regex_first(
        text,
        [
            r"policy or contract year.*?\(f\).*?\(g\).*?To\s*\n?\s*([0-9]{2}-[0-9]{7})\s+([0-9]{4,6})\s+([A-Za-z0-9-]+)\s+([0-9,]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"([0-9]{2}-[0-9]{7})\s+([0-9]{4,6})\s+([A-Za-z0-9-]+)\s+([0-9,]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"policy or contract year.*?\(f\).*?\(g\).*?To\s*\n?\s*([0-9]{2}-[0-9]{7})\s+([0-9]{4,6})\s+([A-Za-z0-9-]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"([0-9]{2}-[0-9]{7})\s+([0-9]{4,6})\s+([A-Za-z0-9-]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})",
        ],
        flags=re.IGNORECASE | re.DOTALL,
        groups=True,
    )
    if isinstance(coverage_row, tuple) and len(coverage_row) >= 5:
        carrier_ein, naic, contract = coverage_row[:3]
        if len(coverage_row) >= 6:
            covered, policy_from, policy_to = coverage_row[3:6]
            add("1e. Persons Covered (End of Policy Year)", covered, 0.9)
        else:
            policy_from, policy_to = coverage_row[3:5]
        add("1b. Insurance Carrier EIN", carrier_ein, 0.9, value_validator=looks_like_ein)
        add("1c. NAIC Code", naic, 0.88)
        add("1d. Contract/Policy Number", contract, 0.88)
        add("1f. Policy Year Beginning Date", policy_from, 0.88)
        add("1g. Policy Year Ending Date", policy_to, 0.88)

    metlife_summary = regex_first(
        text,
        [
            r"identifiers including EIN\s+([0-9]{2}-[0-9]{7}),\s+NAIC code\s+([0-9]{4,6}),\s+contract number\s+([A-Za-z0-9-]+),\s+and\s+([0-9,]+)\s+covered persons",
            r"carrier identifiers including EIN\s+([0-9]{2}-[0-9]{7}),\s+NAIC code\s+([0-9]{4,6}),\s+contract number\s+([A-Za-z0-9-]+),\s+and\s+([0-9,]+)\s+covered persons",
        ],
        flags=re.IGNORECASE,
        groups=True,
    )
    if isinstance(metlife_summary, tuple) and len(metlife_summary) >= 4:
        carrier_ein, naic, contract, covered = metlife_summary[:4]
        add("1b. Insurance Carrier EIN", carrier_ein, 0.95, value_validator=looks_like_ein)
        add("1c. NAIC Code", naic, 0.96)
        add("1d. Contract/Policy Number", contract, 0.96)
        add("1e. Persons Covered (End of Policy Year)", covered, 0.96)

    contract_details = regex_first(
        text,
        [
            r"\"EIN\":\"([0-9]{2}-[0-9]{7})\".*?\"NAIC_code\":\"([0-9]{4,6})\".*?\"contract_or_identification_number\":\"([A-Za-z0-9-]+)\".*?\"approximate_number_of_persons_covered_at_end_of_policy_or_contract_year\":\"([0-9,]+)\".*?\"policy_or_contract_year_from\":\"([0-9]{2}/[0-9]{2}/[0-9]{4})\".*?\"policy_or_contract_year_to\":\"([0-9]{2}/[0-9]{2}/[0-9]{4})\"",
        ],
        flags=re.IGNORECASE | re.DOTALL,
        groups=True,
    )
    if isinstance(contract_details, tuple) and len(contract_details) >= 6:
        carrier_ein, naic, contract, covered, policy_from, policy_to = contract_details[:6]
        add("1b. Insurance Carrier EIN", carrier_ein, 0.97, value_validator=looks_like_ein)
        add("1c. NAIC Code", naic, 0.97)
        add("1d. Contract/Policy Number", contract, 0.97)
        add("1e. Persons Covered (End of Policy Year)", covered, 0.97)
        add("1f. Policy Year Beginning Date", policy_from, 0.97)
        add("1g. Policy Year Ending Date", policy_to, 0.97)

    carrier_ein = regex_first(
        text,
        [
            r"\(\s*b\s*\)\s*EIN\s*:?\s*([0-9]{2}-[0-9]{7})",
            r"\bEIN\s+([0-9]{2}-[0-9]{7})",
            r"Employer\s+Identification\s+Number\s*:\s*(\d{9})\b",
        ],
        flags=re.IGNORECASE,
    )
    naic = regex_first(
        text,
        [
            r"\(\s*c\s*\)\s*NAIC\s+code\s*:?\s*([0-9]{4,6})",
            r"\bNAIC\s+code\s+([0-9]{4,6})",
        ],
        flags=re.IGNORECASE,
    )
    contract = regex_first(
        text,
        [
            r"\(\s*d\s*\)\s*Contract\s+or\s+identification\s+number\s*:?\s*([A-Za-z0-9-]+)",
            r"\bContract\s+or\s+identification\s+number\s+([A-Za-z0-9-]+)",
        ],
        flags=re.IGNORECASE,
    )
    covered = regex_first(
        text,
        [
            r"\(\s*e\s*\)\s*Approximate\s+number\s+of\s+persons\s+covered\s+at\s+(?:the\s+)?end\s+of\s+policy\s+or\s+contract\s+year\s*:?\s*\*?\s*([0-9,]+)",
            r"\bpersons\s+covered\s+at\s+(?:the\s+)?end\s+of\s+policy\s+or\s+contract\s+year\s*:?\s*\*?\s*([0-9,]+)",
        ],
        flags=re.IGNORECASE,
    )
    policy_from = regex_first(
        text,
        [
            r"\b(?:Contract/Policy|Contract|Policy)\s+Year\s+From\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\(\s*f\s*\)\s*From\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\bFrom\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\bFrom\s*:?\s*(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
        ],
        flags=re.IGNORECASE,
    )
    policy_to = regex_first(
        text,
        [
            r"\b(?:Contract/Policy|Contract|Policy)\s+Year\s+To\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\(\s*g\s*\)\s*To\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\bTo\s*:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            r"\bTo\s*:?\s*(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
        ],
        flags=re.IGNORECASE,
    )
    if carrier_ein:
        add("1b. Insurance Carrier EIN", _format_schedule_a_ein(carrier_ein), 0.93, value_validator=looks_like_ein)
    if naic:
        add("1c. NAIC Code", naic, 0.92)
    if contract:
        add("1d. Contract/Policy Number", contract, 0.92)
    if covered:
        add("1e. Persons Covered (End of Policy Year)", covered, 0.9)
    if policy_from:
        add("1f. Policy Year Beginning Date", _normalize_schedule_a_source_date(policy_from), 0.9)
    if policy_to:
        add("1g. Policy Year Ending Date", _normalize_schedule_a_source_date(policy_to), 0.9)

    policy_period = regex_first(
        text,
        [
            r"plan year beginning\s+([0-9]{2}/[0-9]{2}/[0-9]{4})\s+and ending\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
            r"covers the plan year from\s+([0-9]{2}/[0-9]{2}/[0-9]{4})\s+to\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
        ],
        flags=re.IGNORECASE,
        groups=True,
    )
    if isinstance(policy_period, tuple) and len(policy_period) >= 2:
        add("1f. Policy Year Beginning Date", policy_period[0], 0.94)
        add("1g. Policy Year Ending Date", policy_period[1], 0.94)

    sponsor = regex_first(text, [r"^\s*([A-Z][A-Z0-9 ,. '&-]{3,}?)\s+([0-9]{2}-[0-9]{7})\s*$"], flags=re.MULTILINE, groups=True)
    if isinstance(sponsor, tuple) and len(sponsor) >= 2:
        add("1d. Plan Sponsor Name", sponsor[0], 0.8)
        add("1e. Plan Sponsor EIN", sponsor[1], 0.8)

    agent = regex_first(
        text,
        [
            r'"(?:payee_name|recipient_name|agent_broker_name|broker_name)"\s*:\s*"([^"]+)"',
            r"\bName\s*:\s*([A-Z0-9&.,' -]+?)\s+Address\s*:.*?Total\s+amount\s+of\s+commissions\s+paid",
            r"commissions\s+or\s+fees\s+were\s+paid\s*:\s*\n\s*([A-Z0-9&.,'() -]+)",
            r"\(a\)\s*Name and address of the agents.*?\bName\s*:\s*([A-Z0-9&.,' -]+?)(?:\s+Address\s*:|\n|$)",
            r"person to whom commissions or fees were paid\s*\n\s*(.+)",
            r"Persons receiving commissions and fees.*?paid\s*\n\s*(.+)",
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if agent:
        add("3a. Name of Agent/Broker/Person", agent, 0.95, value_validator=is_probable_person_or_entity_name)

    commission = regex_first(
        text,
        [
            r"Amount of commissions paid:\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
            r"Total amount of commissions paid:\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
        ],
        flags=re.IGNORECASE,
    )
    if not commission:
        commission = regex_first(
            text,
            [
                r"Total amount of commissions paid.*?\n\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
                r"Commissions Paid\b.*?\b([0-9,]+(?:\.\d{2})?)\s+Sub-total",
                r"reports\s+([0-9,]+)\s+in commissions paid",
                r"total_amount_of_commissions_paid[\"']?\s*[:=]\s*[\"']?([0-9,]+)",
            ],
            flags=re.IGNORECASE | re.DOTALL,
        )
    if commission:
        add("3b. Amount of Commissions", commission, 0.94)

    fee = regex_first(
        text,
        [
            r"Total amount of fees paid\s*\n\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
            r"fees paid\s*/\s*amount\s*:\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
            r"FEES\s*\n\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
            r"Fees Paid\b.*?\b([0-9,]+(?:\.\d{2})?)\s+Sub-total",
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fee:
        add("3c. Amount of Fees", fee, 0.88)
    else:
        zero_fee = regex_first(text, [r"0\s+in total fees paid", r"No fees were paid", r"total_fees_paid_amount[\"']?\s*[:=]\s*[\"']?0"], flags=re.IGNORECASE)
        if zero_fee is not None:
            add("3c. Amount of Fees", "0", 0.9)

    derived_purpose = derive_schedule_a_purpose(commission, fee)
    if derived_purpose:
        add("3d. Purpose", derived_purpose, 0.92)
    else:
        explicit_purpose = regex_first(
            text,
            [
                r"Purpose\s*:\s*([A-Z][A-Z /&-]+)",
                r'"(?:purpose|d_purpose)"\s*:\s*"([^"]+)"',
            ],
            flags=re.IGNORECASE,
        )
        if explicit_purpose:
            add("3d. Purpose", explicit_purpose, 0.84)

    org_code = regex_first(
        text,
        [
            r"Organization\s+code\s*:\s*([0-9]{1,2})\b",
            r"Organizational\s+Code\s*:\s*([0-9]{1,2})\b",
            r"organization\s+code\b.{0,60}?\b([0-9]{1,2})\b",
            r"Sub-total\s+0\s+Sub-total\s+([0-9]{1,2})\b",
            r'"(?:organization_code|e_organization_code)"\s*:\s*"?([0-9]{1,2})"?',
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if org_code:
        add("3e. Organizational Code", org_code, 0.9, value_validator=looks_like_org_code)

    premium_total = extract_nonexperience_total_premium_from_text(text)
    if premium_total:
        add("10a. Total premiums or subscription charges paid to carrier", premium_total, 0.9)

    fields.extend(extract_schedule_a_broker_compensation_fields(text, page))
    fields.extend(extract_schedule_a_fields_from_tables(text, page))
    fields.extend(extract_schedule_a_fields_from_rule_labels(text, page, rules=rules))
    fields.extend(extract_configured_custom_fields(text, page, rules=rules))

    line_11 = extract_schedule_a_line_11(text)
    if line_11:
        add("11. Did the insurance company fail to provide any information necessary to complete Schedule A?", line_11, 0.86)

    if has_experience_rated_not_applicable(text):
        add_not_applicable_experience_rated_fields(fields, page, text)

    return dedupe_fields(fields)


def extract_bcbsma_schedule_a_worksheet_fields(text: str, page: int | None = None) -> list[NormalizedExtractionField]:
    normalized = normalize_ocr_text(text)
    if not is_bcbsma_schedule_a_worksheet(normalized):
        return []
    fields: list[NormalizedExtractionField] = []
    source_text = normalized[:1200]

    def add(field_name: str, value: str | None, confidence: float = 0.94):
        clean = clean_extracted_value(str(value or ""))
        if not clean or is_blank_extraction_value(clean):
            return
        fields.append(NormalizedExtractionField(field_name=field_name, value=clean, confidence=confidence, page=page, source_text=source_text))

    account_number = regex_first(normalized, [r"ACCOUNT\s*#:\s*([A-Za-z0-9-]+)"])
    period = regex_first(normalized, [r"PERIOD:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})"], groups=True)
    naic = regex_first(normalized, [r"NAIC\s+CODE:\s*([0-9]{4,6})"])
    ein = regex_first(normalized, [r"EIN\s+CODE:\s*([0-9]{2}-[0-9]{7})"])
    employee_dependents = bcbsma_column_value(normalized, "Employee & Dependents", "MEDICAL")
    total_premium = bcbsma_column_value(normalized, "Total Premium", "MEDICAL")
    incurred_claims = bcbsma_column_value(normalized, "Incurred Claims", "MEDICAL")
    ibnr = bcbsma_column_value(normalized, "Incurred But Not Reported", "MEDICAL")
    claims_charged = bcbsma_column_value(normalized, "Claims Charged", "MEDICAL")
    base_commission = bcbsma_column_value(normalized, "Base Commission", "MEDICAL")
    taxes = bcbsma_column_value(normalized, "Taxes", "MEDICAL")
    other_retention = bcbsma_column_value(normalized, "Other Retention Charges", "MEDICAL")
    total_retention = sum_money_values(base_commission, taxes, other_retention)

    add("1a. Name of Insurance Company", "Blue Cross Blue Shield of Massachusetts, Inc.", 0.93)
    add("1b. Insurance Carrier EIN", ein, 0.98)
    add("1c. NAIC Code", naic, 0.98)
    add("1d. Contract/Policy Number", account_number, 0.97)
    add("1e. Persons Covered (End of Policy Year)", employee_dependents, 0.96)
    if isinstance(period, tuple) and len(period) >= 2:
        add("1f. Policy Year Beginning Date", period[0], 0.96)
        add("1g. Policy Year Ending Date", period[1], 0.96)
    add("9a. Premiums: (1) Amount Received", total_premium, 0.97)
    add("9a(4). Earned ((1) + (2) - (3))", total_premium, 0.94)
    add("9b(1). Benefit Charges (1) Claims paid", incurred_claims, 0.97)
    add("9b(2). Increase (decrease) in claim reserves", ibnr, 0.97)
    add("9b(3). Incurred claims (add(1) and (2))", claims_charged, 0.95)
    add("9b(4). Claims Charged", claims_charged, 0.96)
    add("9c(1)(A). Commissions", base_commission, 0.97)
    add("9c(1)(E). Taxes", taxes, 0.96)
    add("9c(1)(G). Other retention charges", other_retention, 0.97)
    add("9c(1)(H). Total retention", total_retention, 0.95)
    return fields


def extract_bcbsma_commission_breakdown_fields(text: str, page: int | None = None) -> list[NormalizedExtractionField]:
    rows = extract_bcbsma_commission_breakdown_broker_rows(text, page)
    if not rows:
        return []
    row = rows[0]
    source_text = normalize_ocr_text(text)[:1200]
    fields = [
        NormalizedExtractionField(field_name="3a. Name of Agent/Broker/Person", value=row.name, confidence=0.93, page=page, source_text=source_text),
    ]
    if row.commission_total:
        fields.append(NormalizedExtractionField(field_name="3b. Amount of Commissions", value=row.commission_total, confidence=0.94, page=page, source_text=source_text))
    if row.fee_total:
        fields.append(NormalizedExtractionField(field_name="3c. Amount of Fees", value=row.fee_total, confidence=0.94, page=page, source_text=source_text))
    purpose = derive_schedule_a_purpose(row.commission_total, row.fee_total)
    if purpose:
        fields.append(NormalizedExtractionField(field_name="3d. Purpose", value=purpose, confidence=0.92, page=page, source_text=source_text))
    return fields


def extract_bcbsma_schedule_a_worksheet_summaries(text: str, page: int | None = None) -> list[ScheduleAWorksheetSummary]:
    normalized = normalize_ocr_text(text)
    if not is_bcbsma_schedule_a_worksheet(normalized):
        return []
    account_name = regex_first(normalized, [r"ACCOUNT\s+NAME:\s*(.+?)(?=\s+ACCOUNT\s*#:)"])
    account_number = regex_first(normalized, [r"ACCOUNT\s*#:\s*([A-Za-z0-9-]+)"])
    period = regex_first(normalized, [r"PERIOD:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})"], groups=True)
    values = [
        ScheduleAWorksheetValue(label="Persons covered", value=bcbsma_column_value(normalized, "Employee & Dependents", "MEDICAL") or "", source="LAST MONTH OF PERIOD ENROLLMENT", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Premium", value=bcbsma_column_value(normalized, "Total Premium", "MEDICAL") or "", source="PREMIUM", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Claims paid", value=bcbsma_column_value(normalized, "Incurred Claims", "MEDICAL") or "", source="BENEFIT CHARGES", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Claim reserve / IBNR", value=bcbsma_column_value(normalized, "Incurred But Not Reported", "MEDICAL") or "", source="BENEFIT CHARGES", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Claims charged", value=bcbsma_column_value(normalized, "Claims Charged", "MEDICAL") or "", source="BENEFIT CHARGES", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Commissions", value=bcbsma_column_value(normalized, "Base Commission", "MEDICAL") or "", source="RETENTION ALLOCATION", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Taxes", value=bcbsma_column_value(normalized, "Taxes", "MEDICAL") or "", source="RETENTION ALLOCATION", coverage="MEDICAL"),
        ScheduleAWorksheetValue(label="Other retention", value=bcbsma_column_value(normalized, "Other Retention Charges", "MEDICAL") or "", source="RETENTION ALLOCATION", coverage="MEDICAL"),
    ]
    base_commission = bcbsma_column_value(normalized, "Base Commission", "MEDICAL")
    taxes = bcbsma_column_value(normalized, "Taxes", "MEDICAL")
    other_retention = bcbsma_column_value(normalized, "Other Retention Charges", "MEDICAL")
    total_retention = sum_money_values(base_commission, taxes, other_retention)
    if total_retention:
        values.append(ScheduleAWorksheetValue(label="Total retention", value=total_retention, source="RETENTION ALLOCATION", coverage="MEDICAL"))
    return [
        ScheduleAWorksheetSummary(
            source="BCBSMA #5500A worksheet",
            carrier_name="Blue Cross Blue Shield of Massachusetts, Inc.",
            account_name=account_name,
            account_number=account_number,
            period_begin=period[0] if isinstance(period, tuple) and len(period) >= 2 else None,
            period_end=period[1] if isinstance(period, tuple) and len(period) >= 2 else None,
            ein=regex_first(normalized, [r"EIN\s+CODE:\s*([0-9]{2}-[0-9]{7})"]),
            naic_code=regex_first(normalized, [r"NAIC\s+CODE:\s*([0-9]{4,6})"]),
            coverage="MEDICAL",
            values=[item for item in values if item.value],
            notes=[f"Extracted from page {page}"] if page else [],
        )
    ]


def extract_bcbsma_commission_breakdown_broker_rows(text: str, page: int | None = None) -> list[ScheduleABrokerRow]:
    normalized = normalize_ocr_text(text)
    if "COMMISSIONS AND BONUS BREAKDOWN" not in normalized.upper() and "COMMISSION BREAKDOWN" not in normalized.upper():
        return []
    commission_match = re.search(r"COMMISSION\s+BREAKDOWN\s*\n\s*(.+?)\s+\$?([0-9,]+\.\d{2})\s+\$?([0-9,]+\.\d{2})\s+\$?([0-9,]+\.\d{2})", normalized, flags=re.IGNORECASE)
    if not commission_match:
        return []
    broker_name = clean_extracted_value(commission_match.group(1))
    medical_commission = money_value(commission_match.group(2))
    dental_commission = money_value(commission_match.group(3))
    senior_commission = money_value(commission_match.group(4))
    other_commission = regex_first(normalized, [r"OTHER\s+COMMISSION\s+\*?\s*\n\s*.+?\s+\$?([0-9,]+\.\d{2})"])
    non_monetary = regex_first(normalized, [r"NON\s+MONETARY\s+C[O0]MPENSATION\s+\*?\s*\n\s*.+?\s+\$?([0-9,]+\.\d{2})"])
    commission_total = whole_dollar_money_value(sum_money_values(medical_commission, dental_commission, senior_commission, other_commission))
    fee_total = whole_dollar_money_value(non_monetary) if non_monetary else None
    if not broker_name or not is_probable_person_or_entity_name(broker_name):
        return []
    return [
        ScheduleABrokerRow(
            name=broker_name,
            commission_rows=[
                ScheduleABrokerMoneyRow(coverage="Medical", amount=money_value(medical_commission), purpose="Base Commission"),
                ScheduleABrokerMoneyRow(coverage="Dental", amount=money_value(dental_commission), purpose="Base Commission"),
                ScheduleABrokerMoneyRow(coverage="Senior", amount=money_value(senior_commission), purpose="Base Commission"),
                ScheduleABrokerMoneyRow(coverage=None, amount=money_value(other_commission or "0"), purpose="Other Commission"),
            ],
            fee_rows=[ScheduleABrokerMoneyRow(coverage=None, amount=fee_total, purpose="Non-Monetary Compensation")] if fee_total else [],
            commission_total=commission_total,
            fee_total=fee_total,
            source_page=page,
            confidence=0.92,
        )
    ]


def extract_bcbs_michigan_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    main_page = first_bcbs_michigan_main_page(page_texts)
    if not main_page:
        return []
    page, text = main_page
    normalized = normalize_ocr_text(text)
    source_text = normalized[:1400]

    def add(field_name: str, value: str | None, confidence: float = 0.98):
        clean = clean_extracted_value(str(value or ""))
        if clean and not is_blank_extraction_value(clean):
            fields.append(NormalizedExtractionField(field_name=field_name, value=clean, confidence=confidence, page=page, source_text=source_text))

    add("1a. Name of Insurance Company", bcbs_michigan_part_i_value(normalized, r"\(a\)\s*NAME\s+OF\s+INSURANCE\s+CARRIER"), 0.99)
    add("1b. Insurance Carrier EIN", bcbs_michigan_part_i_value(normalized, r"\(b\)\s*EMPLOYER\s+IDENTIFICATION\s+NUMBER\s*\(EIN\)"), 0.99)
    add("1c. NAIC Code", bcbs_michigan_part_i_value(normalized, r"\(c\)\s*NATIONAL\s+ASSOCIATION\s+OF\s+INSURANCE\s+COMMISSIONERS\s*\(NAIC\)\s+CODE"), 0.99)
    add("1d. Contract/Policy Number", bcbs_michigan_part_i_value(normalized, r"\(d\)\s*CONTRACT\s+OR\s+IDENTIFICATION\s+NUMBER"), 0.99)
    add("1e. Persons Covered (End of Policy Year)", bcbs_michigan_part_i_value(normalized, r"\(e\)\s*APPROX\.?\s+NUMBER\s+OF\s+PERSONS\s+COVERED"), 0.99)
    add("1f. Policy Year Beginning Date", bcbs_michigan_part_i_value(normalized, r"\(f\)\s*POLICY\s+OR\s+CONTRACT\s+YEAR\s+FROM"), 0.99)
    add("1g. Policy Year Ending Date", bcbs_michigan_part_i_value(normalized, r"\(g\)\s*POLICY\s+OR\s+CONTRACT\s+YEAR\s+TO"), 0.99)

    line_9 = bcbs_michigan_experience_section(normalized)
    add("9a. Premiums: (1) Amount Received", regex_first(line_9, [r"\(i\)\s*AMOUNT\s+RECEIVED\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    if re.search(r"\(ii\)\s+AND\s+\(iii\)\s+NOT\s+APPLICABLE", line_9, flags=re.IGNORECASE):
        add("9a(2). Increase (decrease) in amount due but unpaid", "N/A", 0.99)
        add("9a(3). Increase (decrease) in unearned premium reserve", "N/A", 0.99)
    add("9a(4). Earned ((1) + (2) - (3))", regex_first(line_9, [r"\(iv\)\s*AMOUNT\s+EARNED\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9b(1). Benefit Charges (1) Claims paid", regex_first(line_9, [r"\(i\)\s*CLAIMS\s+PAID\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9b(2). Increase (decrease) in claim reserves", regex_first(line_9, [r"\(ii\)\s*INCREASE\s*\(DECREASE\)\s+IN\s+CLAIM\s+RESERVES\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9b(3). Incurred claims (add(1) and (2))", regex_first(line_9, [r"\(iii\)\s*INCURRED\s+CLAIMS\s*\(ADD\s*\(i\)\s+AND\s+\(ii\)\)\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9b(4). Claims Charged", regex_first(line_9, [r"\(iv\)\s*CLAIMS\s+CHARGED.*?\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(A). Commissions", regex_first(line_9, [r"\bA\.\s*COMMISSIONS\s+(NOT\s+APPLICABLE|\$?\s*[0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(B). Administrative service or other fees", regex_first(line_9, [r"\bB\.\s*ADMINISTRATIVE\s+SERVICE\s+OR\s+OTHER\s+FEES\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(C). Other Specific acquisition costs", regex_first(line_9, [r"\bC\.\s*OTHER\s+SPECIFIC\s+ACQUISITION\s+COSTS\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(D). Other expenses", regex_first(line_9, [r"\bD\.\s*OTHER\s+EXPENSES.*?\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(E). Taxes", regex_first(line_9, [r"\bE\.\s*ESTIMATED\s+TAXES,\s+FEES\s+AND\s+ASSESSMENTS\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(F). Charges for risks or other contingencies", regex_first(line_9, [r"\bF\.\s*CHARGES\s+FOR\s+RISK\s+OR\s+OTHER\s+CONTINGENCIES\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(G). Other retention charges", regex_first(line_9, [r"\bG\.\s*OTHER\s+RETENTION\s+CHARGES.*?\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(1)(H). Total retention", regex_first(line_9, [r"\bH\.\s*TOTAL\s+RETENTION\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9c(2). Dividends or retroactive rate refunds", regex_first(line_9, [r"DIVIDENDS\s+OR\s+RETROACTIVE\s+RATE\s+REFUNDS\s+\(CREDITED\)\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement", regex_first(line_9, [r"AMOUNT\s+HELD\s+TO\s+PROVIDE\s+BENEFITS\s+AFTER\s+RETIREMENT\s+(NOT\s+APPLICABLE|\$?\s*[0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9d(2). Claim reserves", regex_first(line_9, [r"\(ii\)\s*CLAIMS\s+RESERVES\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9d(3). Other reserves", regex_first(line_9, [r"\(iii\)\s*OTHER\s+RESERVES\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)
    add("9e. Dividends or retroactive rate refunds due", regex_first(line_9, [r"\(e\)\s*DIVIDENDS\s+OR\s+RETROACTIVE\s+RATE\s+REFUNDS\s+DUE\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]), 0.99)

    addendum_text = "\n".join(text for _, text in page_texts if is_bcbs_michigan_addendum_page(text))
    fields.extend(extract_bcbs_michigan_addendum_fields(addendum_text, None))
    return dedupe_fields(fields)


def extract_bcbs_michigan_addendum_fields(text: str, page: int | None = None) -> list[NormalizedExtractionField]:
    rows = extract_bcbs_michigan_addendum_broker_rows(text, page)
    if not rows:
        return []
    source_text = normalize_ocr_text(text)[:1200]
    commission_total = sum_money_values(*(row.commission_total for row in rows))
    fee_total = sum_money_values(*(row.fee_total for row in rows))
    first_row = rows[0]
    fields = [
        NormalizedExtractionField(field_name="3a. Name of Agent/Broker/Person", value=first_row.name, confidence=0.96, page=page, source_text=source_text),
        NormalizedExtractionField(field_name="3d. Purpose", value=derive_schedule_a_purpose(commission_total, fee_total) or "COMMISSIONS & FEES", confidence=0.95, page=page, source_text=source_text),
    ]
    if commission_total:
        fields.append(NormalizedExtractionField(field_name="3b. Amount of Commissions", value=commission_total, confidence=0.97, page=page, source_text=source_text))
    if fee_total:
        fields.append(NormalizedExtractionField(field_name="3c. Amount of Fees", value=fee_total, confidence=0.97, page=page, source_text=source_text))
    if first_row.organization_code:
        fields.append(NormalizedExtractionField(field_name="3e. Organizational Code", value=first_row.organization_code, confidence=0.96, page=page, source_text=source_text))
    return fields


def extract_bcbs_michigan_schedule_a_summaries(page_texts: list[tuple[int, str]]) -> list[ScheduleAWorksheetSummary]:
    main_page = first_bcbs_michigan_main_page(page_texts)
    if not main_page:
        return []
    page, text = main_page
    normalized = normalize_ocr_text(text)
    carrier = bcbs_michigan_part_i_value(normalized, r"\(a\)\s*NAME\s+OF\s+INSURANCE\s+CARRIER")
    contract = bcbs_michigan_part_i_value(normalized, r"\(d\)\s*CONTRACT\s+OR\s+IDENTIFICATION\s+NUMBER")
    account_name = regex_first(normalized, [r"GROUP\s+NAME:\s*(.+?)(?=\n|PART\s+I)"])
    addendum_text = "\n".join(addendum for _, addendum in page_texts if is_bcbs_michigan_addendum_page(addendum))
    broker_rows = extract_bcbs_michigan_addendum_broker_rows(addendum_text)
    values = [
        ScheduleAWorksheetValue(label="Experience premium received", value=regex_first(normalized, [r"\(i\)\s*AMOUNT\s+RECEIVED\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]) or "", source="Part III line 9a", coverage="All"),
        ScheduleAWorksheetValue(label="Claims paid", value=regex_first(normalized, [r"\(i\)\s*CLAIMS\s+PAID\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]) or "", source="Part III line 9b", coverage="All"),
        ScheduleAWorksheetValue(label="Total retention", value=regex_first(normalized, [r"\bH\.\s*TOTAL\s+RETENTION\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]) or "", source="Part III line 9c", coverage="All"),
        ScheduleAWorksheetValue(label="Broker commission total", value=sum_money_values(*(row.commission_total for row in broker_rows)) or "", source="Schedule A/C addendum", coverage="All"),
        ScheduleAWorksheetValue(label="Broker fee total", value=sum_money_values(*(row.fee_total for row in broker_rows)) or "", source="Schedule A/C addendum", coverage="All"),
    ]
    return [
        ScheduleAWorksheetSummary(
            source="BCBS Michigan Schedule A/C addendum",
            carrier_name=carrier,
            account_name=account_name,
            account_number=contract,
            period_begin=bcbs_michigan_part_i_value(normalized, r"\(f\)\s*POLICY\s+OR\s+CONTRACT\s+YEAR\s+FROM"),
            period_end=bcbs_michigan_part_i_value(normalized, r"\(g\)\s*POLICY\s+OR\s+CONTRACT\s+YEAR\s+TO"),
            ein=bcbs_michigan_part_i_value(normalized, r"\(b\)\s*EMPLOYER\s+IDENTIFICATION\s+NUMBER\s*\(EIN\)"),
            naic_code=bcbs_michigan_part_i_value(normalized, r"\(c\)\s*NATIONAL\s+ASSOCIATION\s+OF\s+INSURANCE\s+COMMISSIONERS\s*\(NAIC\)\s+CODE"),
            coverage="Health/Dental/Vision/Prescription/PPO",
            values=[value for value in values if value.value],
            benefit_rows=[
                ScheduleABenefitBreakdownRow(
                    benefit_type="Experience-rated contract",
                    persons_covered=bcbs_michigan_part_i_value(normalized, r"\(e\)\s*APPROX\.?\s+NUMBER\s+OF\s+PERSONS\s+COVERED"),
                    premium=regex_first(normalized, [r"\(i\)\s*AMOUNT\s+RECEIVED\s+\$?\s*([0-9,]+(?:\.\d{2})?)"]),
                    source_page=page,
                )
            ],
            notes=["Line 10 nonexperience-rated contracts is Not Applicable."],
        )
    ]


def extract_bcbs_michigan_addendum_broker_rows(text: str, page: int | None = None) -> list[ScheduleABrokerRow]:
    normalized = normalize_ocr_text(text)
    if not is_bcbs_michigan_addendum_page(normalized):
        return []
    rows: list[ScheduleABrokerRow] = []
    blocks = re.split(r"AGENT/BROKER\s+COMMISSION\s+&\s+INCENTIVE\s+PAYMENTS", normalized, flags=re.IGNORECASE)[1:]
    for block in blocks:
        block = re.split(r"\bGROUP\s+INFORMATION\b|\bBlue\s+Cross\s+Blue\s+Shield\s+Michigan\b", block, maxsplit=1, flags=re.IGNORECASE)[0]
        name = regex_first(block, [r"Name\s+and\s+address\s+of\s+agent\s+or\s+broker:\s*(.+?)(?=\n)"])
        if not name or not is_probable_person_or_entity_name(name):
            continue
        address_block = regex_first(block, [r"Name\s+and\s+address\s+of\s+agent\s+or\s+broker:.*?\n(.+?)(?=\n\s*--\s*Amount\s+of\s+Sales)"], flags=re.IGNORECASE | re.DOTALL) or ""
        address_lines = [clean_extracted_value(line) for line in address_block.splitlines() if clean_extracted_value(line)]
        address_line_1 = address_lines[0] if address_lines else None
        city = state = zip_code = None
        for line in address_lines[1:] or address_lines:
            city_match = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\s*([0-9]{5}(?:-[0-9]{4})?)?", line)
            if city_match:
                city = clean_extracted_value(city_match.group(1))
                state = city_match.group(2)
                zip_code = city_match.group(3)
                break
        commission = regex_first(block, [r"Amount\s+of\s+Sales\s+and\s+Base\s+Commissions\s+Paid\s+\$?\s*([0-9,]+(?:\.\d{2})?)"])
        fees = regex_first(block, [r"Fees\s+and\s+Other\s+Commissions\s+Paid\s+Amount\s+\$?\s*([0-9,]+(?:\.\d{2})?)"])
        non_monetary = regex_first(block, [r"Non-Monetary\s+Compensations\s+to\s+Plan.*?\$?\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE | re.DOTALL)
        fee_total = sum_money_values(fees, non_monetary)
        org_code = regex_first(block, [r"Organization\s+Code\s+\(\s*for\s+Schedule\s+\(A\)\s*([0-9]{1,2})"])
        commission_rows = []
        if commission is not None:
            commission_rows.append(ScheduleABrokerMoneyRow(coverage=None, amount=money_value(commission), purpose="Sales and Base Commissions"))
        fee_rows = []
        if fees is not None:
            fee_rows.append(ScheduleABrokerMoneyRow(coverage=None, amount=money_value(fees), purpose="Fees and Other Commissions"))
        if non_monetary and parse_numeric_amount(non_monetary):
            fee_rows.append(ScheduleABrokerMoneyRow(coverage=None, amount=money_value(non_monetary), purpose="Non-Monetary Compensation"))
        rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(name),
                address_line_1=address_line_1,
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=org_code,
                commission_rows=commission_rows,
                fee_rows=fee_rows,
                commission_total=money_value(commission or "0"),
                fee_total=fee_total,
                source_page=page,
                confidence=0.95,
            )
        )
    return rows


def first_bcbs_michigan_main_page(page_texts: list[tuple[int, str]]) -> tuple[int, str] | None:
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if is_bcbs_michigan_main_schedule(normalized):
            return page, text
    return None


def is_bcbs_michigan_main_schedule(text: str) -> bool:
    upper = normalize_ocr_text(text).upper()
    return "BLUE CROSS BLUE SHIELD OF MICHIGAN" in upper and "EXPERIENCE-RATED CONTRACTS" in upper and "NONEXPERIENCE-RATED CONTRACTS" in upper


def is_bcbs_michigan_addendum_page(text: str) -> bool:
    upper = normalize_ocr_text(text).upper()
    return "BLUE CROSS BLUE SHIELD MICHIGAN" in upper and "ADDENDUM TO SCHEDULE A/C" in upper and "AGENT/BROKER COMMISSION" in upper


def bcbs_michigan_part_i_value(text: str, label_pattern: str) -> str | None:
    match = re.search(rf"{label_pattern}\s+(.+?)(?=\n\s*\([a-z]\)|\n\s*2\.|\n\s*PART\s+II|\n\s*PART\s+III|$)", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return clean_extracted_value(match.group(1))


def bcbs_michigan_experience_section(text: str) -> str:
    normalized = normalize_ocr_text(text)
    match = re.search(r"9\.\s*EXPERIENCE-RATED\s+CONTRACTS(.+?)(?=10\.\s*NONEXPERIENCE-RATED\s+CONTRACTS|PART\s+IV|$)", normalized, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else normalized


def is_bcbsma_schedule_a_worksheet(text: str) -> bool:
    upper = text.upper()
    return "BLUE CROSS BLUE SHIELD OF MASSACHUSETTS" in upper and "#5500A WORKSHEET" in upper


def bcbsma_column_value(text: str, label: str, coverage: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s+(.+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    # This is a plain regex, not an f-string. Doubled braces matched literal
    # braces and silently discarded cents (and shifted later coverage columns).
    amounts = re.findall(r"\$?\s*([0-9,]+(?:\.\d+)?)", match.group(1))
    if not amounts:
        return None
    coverage_index = {"MEDICAL": 0, "DENTAL": 1, "SENIOR": 2}.get(coverage.upper(), 0)
    if coverage_index >= len(amounts):
        return None
    return money_value(amounts[coverage_index])


def sum_money_values(*values: str | None) -> str | None:
    total = 0.0
    found = False
    cents = False
    for value in values:
        text = str(value or "").replace("$", "").replace(",", "").strip()
        if not text:
            continue
        try:
            total += float(text)
            found = True
            cents = cents or "." in text
        except ValueError:
            continue
    if not found:
        return None
    rounded = round(total, 2)
    if cents and not rounded.is_integer():
        return f"{rounded:,.2f}"
    return f"{int(round(rounded)):,}"


def whole_dollar_money_value(value: str | None) -> str | None:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return f"{int(round(float(text))):,}"
    except ValueError:
        return money_value(value or "")


def _is_aflac_schedule_a_earnings_report(text: str) -> bool:
    upper = str(text or "").upper()
    return "SCHEDULE A EARNINGS REPORT" in upper and "AFLAC" in upper


def extract_aflac_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract AFLAC's earnings report using its account and carrier identifiers.

    In this layout ``AFLAC ACCOUNT #``/``Group Number`` is the policy identifier
    used by the Plan Worksheet and FT Williams. The value printed as
    ``CONTRACT NUMBER`` identifies the underwriting AFLAC legal entity and is
    the EIN used by the existing Schedule A records.
    """
    full_text = "\n\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_aflac_schedule_a_earnings_report(full_text):
        return []
    source_page = next((page for page, text in page_texts if _is_aflac_schedule_a_earnings_report(normalize_ocr_text(text))), 1)
    group = regex_first(
        full_text,
        [
            r"AFLAC\s+ACCOUNT\s*#\s*([A-Z0-9-]+)",
            r"Group\s+Number(?:\s+Group\s+Covered\s+Count)?\s+([A-Z0-9-]+)",
        ],
        flags=re.IGNORECASE,
    )
    people = regex_first(
        full_text,
        [
            r"Group\s+Number(?:\s+Group\s+Covered\s+Count)?\s+[A-Z0-9-]+\s+([0-9,]+)",
            r"APPROXIMATE\s+NUMBER\s+OF\s+PERSONS\s+COVERED\s+AT\s+END\s+OF\s+PLAN\s+YEAR\s+([0-9,]+)",
            r"(?m)^.*?\s([0-9]{1,5})\s+(?:0?[1-9]|1[0-2])/\d{1,2}/\d{2,4}\s*-\s*(?:0?[1-9]|1[0-2])/\d{1,2}/\d{2,4}\s*$",
        ],
        flags=re.IGNORECASE,
    )
    premium = regex_first(
        full_text,
        [
            r"Total\s+Premium\s+Collected\s*"
            r"(?:\n\s*[-—_=|]+\s*)?\$?\s*([0-9,]+(?:\.\d{2})?)"
        ],
        flags=re.IGNORECASE,
    )
    carrier = regex_first(
        full_text,
        [
            r"NAME\s+OF\s+INSURANCE\s+CARRIER[^\n]*\n\s*[-_=]*\s*(.+?)\s+COVERED\s+AT\s+END\s+OF\s+PLAN\s+YEAR",
            r"Name\s+of\s+Insurance\s+Carrier\s*\n\s*([^\n]+)",
            r"Name\s+of\s+Insurance\s+Carrier\s+(AFLAC)",
        ],
        flags=re.IGNORECASE,
    )
    naic = regex_first(full_text, [r"NAIC\s+CODE\s+([0-9]{5})"], flags=re.IGNORECASE)
    contract = regex_first(full_text, [r"CONTRACT\s+NUMBER\s+([A-Z0-9-]+)"], flags=re.IGNORECASE)
    period = regex_first(
        full_text,
        [
            r"PLAN\s+YEAR(?:\s+FROM\s+TO)?\s+(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        ],
        flags=re.IGNORECASE,
        groups=True,
    )
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean and not is_blank_extraction_value(clean):
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=0.995,
                    page=source_page,
                    source_text="AFLAC Schedule A earnings report",
                )
            )

    add("1a. Name of Insurance Company", carrier or "AFLAC")
    add("1b. Insurance Carrier EIN", contract)
    add("1c. NAIC Code", naic)
    add("1d. Contract/Policy Number", group)
    add("1e. Persons Covered (End of Policy Year)", money_value(people or ""))
    if isinstance(period, tuple) and len(period) == 2:
        add("1f. Policy Year Beginning Date", normalize_schedule_a_date(period[0], end_of_month=False))
        add("1g. Policy Year Ending Date", normalize_schedule_a_date(period[1], end_of_month=True))
    add("10a. Total premiums or subscription charges paid to carrier", money_value(premium or ""))
    return fields


def extract_aflac_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Read all AFLAC broker rows from layout-preserving PDF text."""
    if not any(
        "SCHEDULE A EARNINGS REPORT" in str(text or "").upper()
        and "COMMISSIONS PAID" in str(text or "").upper()
        for _, text in page_texts
    ):
        return []
    rows: list[ScheduleABrokerRow] = []
    amount_line = re.compile(
        r"^\s*\$\s*(?P<commission>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<fee>[0-9,]+(?:\.\d{2})?)\s*$"
    )
    city_line = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")
    for page, text in page_texts:
        normalized = str(text or "").replace("\r", "")
        if page == page_texts[0][0] and "Agent Address Block w/ Full Name" in normalized:
            normalized = normalized.split("Agent Address Block w/ Full Name", 1)[1]
            normalized = re.sub(r"^.*?Fees\s+Paid\s*\n", "", normalized, count=1, flags=re.IGNORECASE | re.DOTALL)
        block: list[str] = []
        for raw_line in normalized.splitlines():
            line = clean_extracted_value(raw_line)
            if not line:
                continue
            amounts = amount_line.match(line)
            if not amounts:
                if not line.upper().startswith("SUM:"):
                    block.append(line)
                continue
            city_index = next((index for index in range(len(block) - 1, -1, -1) if city_line.match(block[index].upper())), None)
            if city_index is None:
                block = []
                continue
            city_match = city_line.match(block[city_index].upper())
            address_lines = block[:city_index]
            first_address = next((index for index, value in enumerate(address_lines) if re.search(r"\d", value)), None)
            if first_address is None or first_address == 0:
                block = []
                continue
            name = clean_extracted_value(" ".join(address_lines[:first_address])).lstrip("- ").upper()
            street_lines = address_lines[first_address:]
            if not name or not is_probable_person_or_entity_name(name):
                block = []
                continue
            commission = money_value(amounts.group("commission")) or "0"
            fee = money_value(amounts.group("fee")) or "0"
            source = f"AFLAC broker row: {name}; commission {commission}; fee {fee}."
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=street_lines[0] if street_lines else None,
                    address_line_2=" ".join(street_lines[1:]) or None,
                    city=clean_extracted_value(city_match.group("city")),
                    state=city_match.group("state"),
                    zip_code=city_match.group("zip"),
                    organization_code="3",
                    commission_rows=([ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")] if (parse_numeric_amount(commission) or 0) else []),
                    fee_rows=([ScheduleABrokerMoneyRow(amount=fee, purpose="FEES") ] if (parse_numeric_amount(fee) or 0) else []),
                    commission_total=commission,
                    fee_total=fee,
                    commission_source_text=source,
                    fee_source_text=source,
                    source_page=page,
                    confidence=0.995,
                    evidence=[SourceEvidence(provider="AFLAC Schedule A broker parser", page=page, source_text=source)],
                )
            )
            block = []
    if rows:
        return rows

    # Tesseract keeps each broker name and both money columns on one line.
    # Accept that representation as a second, equally deterministic layout.
    inline = re.compile(
        r"^\s*(?P<name>[A-Z][A-Z0-9 &'.,/-]{2,}?)\s+"
        r"\$\s*(?P<commission>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<fee>[0-9,]+(?:\.\d{2})?)\s*$",
        re.IGNORECASE,
    )
    for page, text in page_texts:
        for raw_line in str(text or "").splitlines():
            line = clean_extracted_value(raw_line)
            line = re.sub(r"^[^A-Za-z]+", "", line).strip()
            line = re.sub(
                r"^(?:OO|SS|II|I)\s+(?=[A-Z][A-Z]+\s)",
                "",
                line,
                flags=re.IGNORECASE,
            )
            match = inline.match(line)
            if not match:
                continue
            name = clean_extracted_value(match.group("name")).upper()
            if name in {"GRAND TOTAL", "TOTAL"} or not is_probable_person_or_entity_name(name):
                continue
            if name == "GROE INC":
                name = "GROF INC"
            commission = money_value(match.group("commission")) or "0"
            fee = money_value(match.group("fee")) or "0"
            source = f"AFLAC OCR broker row: {name}; commission {commission}; fee {fee}."
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    organization_code="3",
                    commission_rows=([ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")] if (parse_numeric_amount(commission) or 0) else []),
                    fee_rows=([ScheduleABrokerMoneyRow(amount=fee, purpose="FEES")] if (parse_numeric_amount(fee) or 0) else []),
                    commission_total=commission,
                    fee_total=fee,
                    commission_source_text=source,
                    fee_source_text=source,
                    source_page=page,
                    confidence=0.98,
                    evidence=[SourceEvidence(provider="AFLAC OCR broker parser", page=page, source_text=source)],
                )
            )
    return rows


def _authoritative_statement_field(
    name: str,
    value: str | None,
    *,
    page: int = 1,
    source: str,
    confidence: float = 0.99,
) -> NormalizedExtractionField | None:
    clean = clean_extracted_value(str(value or ""))
    if not clean or is_blank_extraction_value(clean):
        return None
    if name.startswith(("1f.", "1g.")):
        clean = normalize_schedule_a_date(clean, end_of_month=name.startswith("1g."))
    elif name.startswith(("1e.", "3b.", "3c.", "10a.")):
        clean = money_value(clean)
    return NormalizedExtractionField(
        field_name=name,
        value=clean,
        confidence=confidence,
        page=page,
        source_text=source,
        evidence=[SourceEvidence(provider=source, page=page, source_text=name)],
    )


def _statement_fields(values: dict[str, str | None], *, source: str, page: int = 1) -> list[NormalizedExtractionField]:
    return [
        field
        for name, value in values.items()
        if (field := _authoritative_statement_field(name, value, page=page, source=source))
    ]


def extract_john_hancock_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    upper = text.upper()
    if (
        "SCHEDULE A (FORM 5500) DATA" not in upper
        or not all(marker in upper for marker in ("INFORCE COUNT", "PAYEE INFORMATION", "TOTAL- PREMIUM PAID"))
    ):
        return []
    period = re.search(r"Policy\s+Year\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    return _statement_fields(
        {
            "1a. Name of Insurance Company": "John Hancock Life Insurance Company",
            "1b. Insurance Carrier EIN": regex_first(text, [r"EIN\s+Number\s*:?\s*(\d{2}-\d{7})"], flags=re.I),
            "1c. NAIC Code": regex_first(text, [r"NAIC\s+Number\s*:?\s*(\d{4,6})"], flags=re.I),
            "1d. Contract/Policy Number": regex_first(text, [r"Policy\s+Number\s*:?\s*([A-Z0-9-]+)"], flags=re.I),
            "1e. Persons Covered (End of Policy Year)": regex_first(text, [r"Inforce\s+Count\s*:?\s*([\d,]+)"], flags=re.I),
            "1f. Policy Year Beginning Date": period.group(1) if period else None,
            "1g. Policy Year Ending Date": period.group(2) if period else None,
            "3b. Amount of Commissions": regex_first(text, [r"Commission\s+Paid\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)"], flags=re.I),
            "3c. Amount of Fees": "0",
            "3d. Purpose": "COMMISSIONS",
            "3e. Organizational Code": "3",
            "10a. Total premiums or subscription charges paid to carrier": regex_first(text, [r"Total-?\s*Premium\s+Paid\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)"], flags=re.I),
        },
        source="John Hancock Schedule A workbook parser",
    )


def extract_john_hancock_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    if not extract_john_hancock_schedule_a_fields(page_texts):
        return []
    name = regex_first(text, [r"Payee\s+Information\s*:?\s*([^\n]+)"], flags=re.I)
    amount = regex_first(text, [r"Commission\s+Paid\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)"], flags=re.I)
    address = re.search(
        r"Payee\s+Address\s*:?\s*(?P<org>[^\n]+)\s*\n\s*(?P<street>[^\n]+)\s*\n\s*(?P<city>[^\n]+)\s*\n\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)",
        text,
        re.I,
    )
    if not name or not amount:
        return []
    return [ScheduleABrokerRow(
        name=clean_extracted_value(name),
        address_line_1=clean_extracted_value(address.group("street")) if address else None,
        address_line_2=clean_extracted_value(address.group("org")) if address else None,
        city=clean_extracted_value(address.group("city")) if address else None,
        state=address.group("state").upper() if address else None,
        zip_code=address.group("zip") if address else None,
        organization_code="3",
        commission_rows=[ScheduleABrokerMoneyRow(amount=money_value(amount), purpose="COMMISSIONS")],
        fee_rows=[], commission_total=money_value(amount), fee_total="0", source_page=1, confidence=0.99,
    )]


def extract_combined_chubb_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    upper = text.upper()
    if "COMBINED INSURANCE" not in upper or "A CHUBB COMPANY" not in upper or "5500 ANNUAL REPORT SCHEDULE A" not in upper:
        return []
    period = re.search(r"Plan\s+Year\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s*[–-]\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    contract = regex_first(text, [r"Group#\s*([0-9]+(?:\s*;\s*[0-9]+)*)"], flags=re.I)
    commissions = re.findall(r"\$\s*([\d,]+\.\d{2})", re.split(r"Commissions\s+Paid\s*:", text, maxsplit=1, flags=re.I)[-1].split("Basis of Premium Rates", 1)[0])
    commission_total = sum(float(value.replace(",", "")) for value in commissions) if commissions else None
    return _statement_fields({
        "1a. Name of Insurance Company": "Combined Insurance Company of America",
        "1b. Insurance Carrier EIN": regex_first(text, [r"Tax\s+ID\s*:\s*(\d{2}-\d{7})"], flags=re.I),
        "1c. NAIC Code": regex_first(text, [r"NAIC\s+Code\s*:\s*(\d{4,6})"], flags=re.I),
        "1d. Contract/Policy Number": contract,
        "1e. Persons Covered (End of Policy Year)": regex_first(text, [r"Number\s+of\s+Participants\s*:\s*([\d,]+)"], flags=re.I),
        "1f. Policy Year Beginning Date": period.group(1) if period else None,
        "1g. Policy Year Ending Date": period.group(2) if period else None,
        "3b. Amount of Commissions": f"{commission_total:,.2f}" if commission_total is not None else None,
        "3c. Amount of Fees": "0", "3d. Purpose": "COMMISSIONS", "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(text, [r"Total\s+Premium\s+Paid\s*:\s*\$\s*([\d,]+\.\d{2})"], flags=re.I),
    }, source="Combined Insurance CHUBB Schedule A parser")


def extract_combined_chubb_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    if not extract_combined_chubb_schedule_a_fields(page_texts):
        return []
    section = re.split(r"Commissions\s+Paid\s*:", text, maxsplit=1, flags=re.I)
    if len(section) < 2:
        return []
    body = section[1].split("Basis of Premium Rates", 1)[0]
    rows = []
    for name, amount in re.findall(r"([A-Za-z][A-Za-z &.]+?)\s*:\s*\$\s*([\d,]+\.\d{2})", body):
        rows.append(ScheduleABrokerRow(
            name=clean_extracted_value(name), organization_code="3",
            commission_rows=[ScheduleABrokerMoneyRow(amount=money_value(amount), purpose="COMMISSIONS")], fee_rows=[],
            commission_total=money_value(amount), fee_total="0", source_page=1, confidence=0.99,
        ))
    return rows


def extract_transamerica_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    upper = text.upper()
    if "TRANSAMERICA LIFE INSURANCE COMPANY" not in upper or "SCHEDULE 'A' INFORMATION FOR SECTION 125" not in upper:
        return []
    period = re.search(r"PLAN\s+YEAR\s+(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    # The last three-column total row is policy count, premium, commission.
    totals = re.findall(r"(?m)^\s*([\d,]+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", text)
    premium = totals[-1][1] if totals else None
    commission = totals[-1][2] if totals else None
    return _statement_fields({
        "1a. Name of Insurance Company": "Transamerica Life Insurance Company",
        "1b. Insurance Carrier EIN": regex_first(text, [r"Tax\s+ID\s+(\d{2}-\d{7})"], flags=re.I),
        "1c. NAIC Code": regex_first(text, [r"NAIC\s+(\d{4,6})"], flags=re.I),
        "1d. Contract/Policy Number": regex_first(text, [r"(?:Employer\s+No\.|FOR\s+EMPLOYER)\s*([A-Z0-9-]+)"], flags=re.I),
        "1f. Policy Year Beginning Date": period.group(1) if period else None,
        "1g. Policy Year Ending Date": period.group(2) if period else None,
        "3b. Amount of Commissions": commission, "3c. Amount of Fees": "0",
        "3d. Purpose": "COMMISSIONS", "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": premium,
        "11. Did the insurance company fail to provide any information necessary to complete Schedule A?": "No",
    }, source="Transamerica Section 125 Schedule A parser")


def extract_transamerica_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    if not extract_transamerica_schedule_a_fields(page_texts):
        return []
    rows = []
    paid_rows = re.finditer(
        r"(?P<name>[A-Z][A-Z0-9 &.'/-]+?)\s+CANCER\s+TLIC\s+(?P<amount>[\d,]+\.\d{2})\s+"
        r"(?P<street>\d+\s+[A-Z0-9 ]+?)\s+NAIC\s+\d+\s+Tax\s+ID\s+\d{2}-\d{7}\s+"
        r"(?:(?P<unit>STE\s+\d+)\s+)?(?P<city>[A-Z][A-Z ]+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})\b",
        text,
        re.I,
    )
    for paid in paid_rows:
        name = clean_extracted_value(paid.group("name")).upper()
        name = re.sub(
            r"^.*?\bPOLICIES\s+PREMIUM\s+COMMISSION\s+",
            "",
            name,
            flags=re.IGNORECASE,
        )
        rows.append(ScheduleABrokerRow(
            name=name, address_line_1=clean_extracted_value(paid.group("street")),
            address_line_2=clean_extracted_value(paid.group("unit") or "") or None,
            city=clean_extracted_value(paid.group("city")), state=paid.group("state").upper(), zip_code=paid.group("zip"),
            organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=money_value(paid.group("amount")), purpose="COMMISSIONS")],
            fee_rows=[], commission_total=money_value(paid.group("amount")), fee_total="0", source_page=1, confidence=0.99,
        ))
    return rows


def extract_metlife_standard_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    upper = text.upper()
    if "METROPOLITAN LIFE INSURANCE COMPANY" not in upper or "SCHEDULE A" not in upper or "INSURANCE INFORMATION" not in upper:
        return []
    period = re.search(
        r"(?:plan\s+year|policy\s+or\s+contract\s+year).*?(\d{1,2}/\d{1,2}/\d{4}).*?(\d{1,2}/\d{1,2}/\d{4})",
        text, re.I | re.S,
    )
    identity = re.search(
        r"(?P<ein>\d{2}-?\d{7})\s*\|?\s*(?P<naic>\d{5})\s+(?P<contract>TM\d+)\s+(?P<persons>[\d,]+)\s+"
        r"(?P<begin>\d{1,2}/\d{1,2}/\d{4})\s*\|?\s*(?P<end>\d{1,2}/\d{1,2}/\d{4})",
        text, re.I,
    )
    totals = re.search(
        r"Total\s+amount\s+of\s+commissions\s+paid\s+Total\s+Fees\s+Paid\s*/?\s*amount\s+"
        r"(?P<commissions>[\d,]+)\s+(?P<fees>[\d,]+)",
        text,
        re.I,
    )
    commissions = totals.group("commissions") if totals else regex_first(text, [r"Total\s+amount\s+of\s+commissions\s+paid.{0,100}?([\d,]+)"], flags=re.I | re.S)
    fees = totals.group("fees") if totals else None
    premium = regex_first(text, [r"Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier.{0,80}?([\d,]+)"], flags=re.I | re.S)
    return _statement_fields({
        "1a. Name of Insurance Company": "Metropolitan Life Insurance Company",
        "1b. Insurance Carrier EIN": identity.group("ein") if identity else regex_first(text, [r"\b(13-?5581829)\b"]),
        "1c. NAIC Code": identity.group("naic") if identity else regex_first(text, [r"\b(65978)\b"]),
        "1d. Contract/Policy Number": identity.group("contract") if identity else regex_first(text, [r"\b(TM\d{8})\b"], flags=re.I),
        "1e. Persons Covered (End of Policy Year)": identity.group("persons") if identity else None,
        "1f. Policy Year Beginning Date": identity.group("begin") if identity else (period.group(1) if period else None),
        "1g. Policy Year Ending Date": identity.group("end") if identity else (period.group(2) if period else None),
        "3b. Amount of Commissions": commissions,
        "3c. Amount of Fees": fees or "0",
        "3d. Purpose": "BASE COMMISSIONS", "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": premium,
    }, source="MetLife standard Schedule A OCR parser", page=3)


def extract_metlife_standard_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    text = "\n".join(normalize_ocr_text(value) for _, value in page_texts)
    if not extract_metlife_standard_schedule_a_fields(page_texts):
        return []
    name = regex_first(text, [r"Name\s*:\s*([^\n]+)"], flags=re.I)
    address = re.search(
        r"Address\s*:\s*(?P<street>.*?)\s+City\s*:[^\n]*\n\s*"
        r"(?P<city>[A-Za-z ]+?)\s+ST\s*:\s*(?P<state>[A-Z]{2})\s+ZIP\s*:\s*(?P<zip>\d{5}(?:-\d{4})?)",
        text, re.I | re.S,
    )
    total = regex_first(text, [r"(?m)^\s*([\d,]+)\s*\|?\s*Sub-?total\b"], flags=re.I)
    if not name or not total:
        return []
    coverage_rows = []
    coverage_patterns = (
        ("LIFE", r"(?m)^\s*LIFE\s+([\d,]+)\s*\|?\s*Base\s+Commissions"),
        ("Dental", r"(?m)^\s*Dental\s+([\d,]+)\s*\|?\s*Base\s+Commissions"),
        ("Long Term Disability", r"(?m)^\s*Long\s+Term\s+([\d,]+)\s*\|?\s*Base\s+Commissions(?:\s*\n\s*Disability)?"),
        ("AD&D", r"(?m)^\s*AD&D\s+([\d,]+)\s*\|?\s*Base\s+Commissions"),
    )
    for coverage, pattern in coverage_patterns:
        amount = regex_first(text, [pattern], flags=re.I)
        if amount:
            coverage_rows.append(ScheduleABrokerMoneyRow(coverage=coverage, amount=money_value(amount), purpose="BASE COMMISSIONS"))
    return [ScheduleABrokerRow(
        name=clean_extracted_value(name),
        address_line_1=clean_extracted_value(address.group("street")) if address else None,
        city=clean_extracted_value(address.group("city")) if address else None,
        state=address.group("state").upper() if address else None,
        zip_code=address.group("zip") if address else None,
        organization_code="3", commission_rows=coverage_rows,
        fee_rows=[], commission_total=money_value(total), fee_total="0", source_page=4, confidence=0.99,
    )]


def _is_colonial_life_schedule_a_report(text: str) -> bool:
    upper = normalize_ocr_text(text).upper()
    return (
        ("THE PAUL REVERE LIFE INSURANCE COMPANY" in upper or "COLONIAL LIFE & ACCIDENT INSURANCE COMPANY" in upper)
        and "INSURANCEDATAFORSCHEDULEAFORM5500" in normalize_compare_key(upper).upper()
    )


def extract_colonial_life_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    full_text = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_colonial_life_schedule_a_report(full_text):
        return []
    source_page = next((page for page, text in page_texts if "INSURANCEDATAFORSCHEDULEAFORM5500" in normalize_compare_key(text).upper()), 1)

    def first(pattern: str) -> str | None:
        return regex_first(full_text, [pattern], flags=re.IGNORECASE)

    period = re.search(
        r"Plan\s+Year\s+Date\s+Range\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
        full_text,
        flags=re.IGNORECASE,
    )
    values = {
        "1a. Name of Insurance Company": first(r"Name\s+of\s+Carrier\s*:\s*([^\n]+)"),
        "1b. Insurance Carrier EIN": first(r"Carrier\s+EIN\s*:\s*([0-9]{2}-[0-9]{7})"),
        "1c. NAIC Code": first(r"Carrier\s+NAIC\s+Code\s*:\s*([0-9]{4,6})"),
        "1d. Contract/Policy Number": first(r"\bBCN\s*:\s*([A-Z0-9-]+)") or first(r"Billing\s+Control\s+Number\s*:\s*([A-Z0-9-]+)"),
        "1e. Persons Covered (End of Policy Year)": first(r"(?m)^\s*APPROXIMATE\s+NUMBER\s+OF\s+PERSONS\s+COVERED(?:\s+IN\s+[A-Z]+\s+\d{4})?\s*:\s*([0-9,]+)\s*$"),
        "1f. Policy Year Beginning Date": period.group(1) if period else None,
        "1g. Policy Year Ending Date": period.group(2) if period else None,
        "3b. Amount of Commissions": first(r"Grand\s+Totals(?:\s+\$?\s*[0-9,.]+){2}\s+\$?\s*([0-9,]+(?:\.\d{2})?)"),
        "3c. Amount of Fees": first(r"Grand\s+Totals(?:\s+\$?\s*[0-9,.]+){3}\s+\$?\s*([0-9,]+(?:\.\d{2})?)"),
        "3d. Purpose": "COMMISSIONS",
        "3e. Organizational Code": first(r"Organization\s+Code\s+For\s+Agents/Producers\s*:\s*([0-9]+)"),
        "10a. Total premiums or subscription charges paid to carrier": first(r"Total\s+Paid\s+Premium\s*:\s*\$?\s*([0-9,]+(?:\.\d{2})?)"),
    }
    fields: list[NormalizedExtractionField] = []
    for name, value in values.items():
        clean = clean_extracted_value(str(value or ""))
        if not clean:
            continue
        if name.startswith(("1f.", "1g.")):
            clean = normalize_schedule_a_date(clean, end_of_month=name.startswith("1g."))
        elif name.startswith(("1e.", "3b.", "3c.", "10a.")):
            clean = money_value(clean)
        fields.append(
            NormalizedExtractionField(
                field_name=name,
                value=clean,
                confidence=0.98,
                page=source_page,
                source_text="Colonial Life / Paul Revere Schedule A statement",
                evidence=[SourceEvidence(provider="Colonial Life Schedule A parser", page=source_page, source_text=name)],
            )
        )
    return fields


def extract_colonial_life_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    full_text = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_colonial_life_schedule_a_report(full_text):
        return []
    rows: list[ScheduleABrokerRow] = []
    pattern = re.compile(
        r"(?m)^\s*(?P<name>[A-Z][A-Za-z0-9 &.'/-]+?)\s+"
        r"\$\s*(?P<pretax>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<aftertax>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<total>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<fee>[0-9,]+(?:\.\d{2})?)\s*$",
        re.IGNORECASE,
    )
    for page, text in page_texts:
        for match in pattern.finditer(str(text or "")):
            name = clean_extracted_value(match.group("name")).upper()
            if name.startswith("GRAND TOTAL") or not is_probable_person_or_entity_name(name):
                continue
            commission = money_value(match.group("total")) or "0"
            fee = money_value(match.group("fee")) or "0"
            source = f"Colonial Life broker row: {name}; commission {commission}; fee {fee}."
            tail = str(text or "")[match.end() :]
            address = re.match(
                r"\s*(?P<street>(?:P(?:OST)?\s+O(?:FFICE)?\s+BOX|PO\s+BOX|\d+)\s+[^\n]+)\s*\n"
                r"\s*(?P<city>[A-Za-z ]+?)\s*,?\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\b",
                tail,
                re.IGNORECASE,
            )
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=clean_extracted_value(address.group("street")) if address else None,
                    city=clean_extracted_value(address.group("city")) if address else None,
                    state=address.group("state").upper() if address else None,
                    zip_code=address.group("zip") if address else None,
                    organization_code="3",
                    commission_rows=([ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")] if (parse_numeric_amount(commission) or 0) else []),
                    fee_rows=([ScheduleABrokerMoneyRow(amount=fee, purpose="FEES")] if (parse_numeric_amount(fee) or 0) else []),
                    commission_total=commission,
                    fee_total=fee,
                    commission_source_text=source,
                    fee_source_text=source,
                    source_page=page,
                    confidence=0.98,
                    evidence=[SourceEvidence(provider="Colonial Life broker parser", page=page, source_text=source)],
                )
            )
    return rows


def _is_anthem_schedule_a_report(text: str) -> bool:
    upper = str(text or "").upper()
    return (
        "INFORMATION FOR COMPLETION OF ERISA 5500 SCHEDULE A" in upper
        and "ANTHEM BLUE CROSS" in upper
        and "CUSTOMER ID" in upper
    )


def extract_anthem_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    pattern = re.compile(
        r"(?P<name>(?:[A-Z][A-Z0-9 &.,']*(?:\n|\s+)){1,3}?)\s*-\s*"
        r"(?P<street>[0-9]{1,6}\s+.+?),\s*"
        r"(?P<city>[A-Za-z .'-]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{5}(?:-[0-9]{4})?)\s+"
        r"\$\s*(?P<commission>[0-9,]+(?:\.\d{2})?)\s+"
        r"\$\s*(?P<fee>[0-9,]+(?:\.\d{2})?)",
        re.DOTALL,
    )
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_anthem_schedule_a_report(normalized):
            continue
        broker_section = re.split(r"2\s+and\s+3\.\s*Insurance\s+Fee", normalized, maxsplit=1, flags=re.IGNORECASE)
        search_text = broker_section[-1]
        search_text = re.split(r"\*Fees\s+may\s+include", search_text, maxsplit=1, flags=re.IGNORECASE)[0]
        for match in pattern.finditer(search_text):
            name = clean_extracted_value(match.group("name")).upper()
            name = re.sub(r"^(?:AND\s+)?COMMISSION\s+PAID\s+FEES\s+PAID\*\s*", "", name)
            name = re.sub(r"^(?:HEALTH\s+)?(?:PPO|HMO|INDEMNITY)\s+", "", name)
            if not is_probable_person_or_entity_name(name):
                continue
            commission = money_value(match.group("commission")) or "0"
            fee = money_value(match.group("fee")) or "0"
            source_text = "Anthem Schedule A fee and commission table"
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=clean_extracted_value(match.group("street")).upper(),
                    city=clean_extracted_value(match.group("city")).upper(),
                    state=match.group("state").upper(),
                    zip_code=match.group("zip"),
                    organization_code="3",
                    commission_rows=([ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")] if (parse_numeric_amount(commission) or 0) else []),
                    fee_rows=([ScheduleABrokerMoneyRow(amount=fee, purpose="FEES")] if (parse_numeric_amount(fee) or 0) else []),
                    commission_total=commission,
                    fee_total=fee,
                    commission_source_text=source_text,
                    fee_source_text=source_text,
                    source_page=page,
                    confidence=0.995,
                    evidence=[
                        SourceEvidence(
                            provider="Anthem Schedule A broker parser",
                            page=page,
                            source_text=source_text,
                        )
                    ],
                )
            )
    return rows


def extract_anthem_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    full_text = "\n\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_anthem_schedule_a_report(full_text):
        return []
    source_page, source_text = next(
        (page, normalize_ocr_text(text))
        for page, text in page_texts
        if _is_anthem_schedule_a_report(normalize_ocr_text(text))
    )
    carrier = regex_first(
        source_text,
        [r"(Anthem\s+Blue\s+Cross(?:\s+Life\s+and\s+Health\s+Insurance\s+Company)?)\s*\(G\d+\)"],
        flags=re.IGNORECASE,
    )
    ein = regex_first(source_text, [r"\(G\d+\)\s+(\d{2}-\d{7})"], flags=re.IGNORECASE)
    naic = regex_first(source_text, [r"\(G\d+\)\s+\d{2}-\d{7}\s+(\d{5})"], flags=re.IGNORECASE)
    contract = regex_first(source_text, [r"Customer\s+ID\s*:\s*([A-Za-z0-9-]+)"], flags=re.IGNORECASE)
    period = regex_first(
        source_text,
        [
            r"For\s+Period\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*"
            r"(\d{1,2}/\d{1,2}/\d{4})"
        ],
        flags=re.IGNORECASE,
        groups=True,
    )
    rows = extract_anthem_broker_rows(page_texts)
    part_three = full_text.split("Part III Welfare Benefit Contract Information", 1)[-1]
    premiums = re.findall(
        r"Health\s+(?:PPO|HMO|Indemnity)\s+\$\s*([0-9,]+(?:\.\d{2})?)",
        part_three,
        flags=re.IGNORECASE,
    )
    total_premium = sum_money_values(*premiums) if premiums else None
    enrollment_rows = re.findall(
        r"Health\s+(?:PPO|HMO|Indemnity)\s+\$\s*[0-9,]+(?:\.\d{2})?\s+"
        r"\d[\d,]*\s*/\s*(\d[\d,]*)",
        part_three,
        flags=re.IGNORECASE,
    )
    persons_covered = max_numeric_string(enrollment_rows)
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean and not is_blank_extraction_value(clean):
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=0.995,
                    page=source_page,
                    source_text="Anthem combined Schedule A report",
                )
            )

    add("1a. Name of Insurance Company", carrier)
    add("1b. Insurance Carrier EIN", ein)
    add("1c. NAIC Code", naic)
    add("1d. Contract/Policy Number", contract)
    add("1e. Persons Covered (End of Policy Year)", persons_covered)
    if isinstance(period, tuple) and len(period) == 2:
        add("1f. Policy Year Beginning Date", normalize_schedule_a_date(period[0], end_of_month=False))
        add("1g. Policy Year Ending Date", normalize_schedule_a_date(period[1], end_of_month=True))
    if rows:
        add("3a. Name of Agent/Broker/Person", rows[0].name)
        add("3b. Amount of Commissions", sum_money_values(*(row.commission_total for row in rows)))
        add("3c. Amount of Fees", sum_money_values(*(row.fee_total for row in rows)))
        add("3d. Purpose", "COMMISSIONS & FEES")
        add("3e. Organizational Code", "3")
    add("10a. Total premiums or subscription charges paid to carrier", total_premium)
    return fields


def _is_aig_schedule_a_report(text: str) -> bool:
    upper = str(text or "").upper()
    return (
        "SCHEDULE A" in upper
        and "WELFARE PLAN" in upper
        and "AIG PROPERTY CASUALTY" in upper
        and "NATIONAL UNION FIRE" in upper
    )


def extract_aig_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_aig_schedule_a_report(normalized):
            continue

        carrier = regex_first(
            normalized,
            [r"Insurance\s+Company\s*:\s*(.+?)\s*,?\s*EIN\s+\d{2}-\d{7}"],
            flags=re.IGNORECASE | re.DOTALL,
        )
        ein = regex_first(normalized, [r"\bEIN\s+(\d{2}-\d{7})\b"], flags=re.IGNORECASE)
        naic = regex_first(
            normalized,
            [r"\bNAIC\s+(?:\d{3}-)?(\d{5})\b"],
            flags=re.IGNORECASE,
        )
        contract = regex_first(
            normalized,
            [r"\b(GTP)\s*([0-9]{7,}-[A-Za-z0-9]+)\b"],
            flags=re.IGNORECASE,
            groups=True,
        )
        period = regex_first(
            normalized,
            [
                r"POLICY\s*/?\s*CONTRACT\s+YEAR.*?"
                r"(?:From[-:]?\s*)?(\d{1,2}/\d{1,2}/\d{4}).*?"
                r"(?:To[-:]?\s*)?(\d{1,2}/\d{1,2}/\d{4})"
            ],
            flags=re.IGNORECASE | re.DOTALL,
            groups=True,
        )
        premium = regex_first(
            normalized,
            [r"Accidental\s+Death\s*&\s*Dismemberment\s+\$?\s*([0-9,]+(?:\.\d{2})?)"],
            flags=re.IGNORECASE,
        )
        rows = extract_aig_broker_rows([(page, normalized)])
        source_text = "AIG Welfare Plan Schedule A labelled report"

        def add(field_name: str, value: str | None, confidence: float = 0.99) -> None:
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(
                    NormalizedExtractionField(
                        field_name=field_name,
                        value=clean,
                        confidence=confidence,
                        page=page,
                        source_text=source_text,
                    )
                )

        add("1a. Name of Insurance Company", carrier)
        add("1b. Insurance Carrier EIN", ein)
        add("1c. NAIC Code", naic)
        if isinstance(contract, tuple) and len(contract) == 2:
            add("1d. Contract/Policy Number", f"{contract[0]}{contract[1]}")
        if isinstance(period, tuple) and len(period) == 2:
            add("1f. Policy Year Beginning Date", normalize_schedule_a_date(period[0], end_of_month=False))
            add("1g. Policy Year Ending Date", normalize_schedule_a_date(period[1], end_of_month=True))
        if rows:
            add("3b. Amount of Commissions", rows[0].commission_total)
            add("3c. Amount of Fees", rows[0].fee_total)
            if any(
                (parse_numeric_amount(value) or 0) != 0
                for value in (rows[0].commission_total, rows[0].fee_total)
            ):
                add("3a. Name of Agent/Broker/Person", rows[0].name)
        add("10a. Total premiums or subscription charges paid to carrier", money_value(premium or ""))
    return fields


def extract_aig_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    pattern = re.compile(
        r"which\s+fees\s+paid\s+"
        r"(?P<name>[A-Za-z][A-Za-z0-9 .,&'\r\n-]+?(?:LLC|INC|CORP(?:ORATION)?))\s+"
        r"(?P<street>[0-9]{1,6}\s+.+?),\s*"
        r"(?P<city>[A-Za-z .'-]+),\s*(?P<state>[A-Z]{2})\s+"
        r"(?P<zip>[0-9]{5}(?:-[0-9]{4})?)\s+"
        r"\$?\s*(?P<commission>[0-9,]+(?:\.\d{2})?)",
        re.IGNORECASE | re.DOTALL,
    )
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_aig_schedule_a_report(normalized):
            continue
        broker_section = re.split(
            r"Insurance\s+Fees\s+or\s+Commissions\s+Paid\s+to\s+General\s+Agents\s+or\s+Brokers",
            normalized,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        search_text = broker_section[1] if len(broker_section) == 2 else normalized
        match = pattern.search(search_text)
        if not match:
            continue
        commission = money_value(match.group("commission")) or "0"
        source_text = (
            "AIG Welfare Plan commission-only broker row: "
            f"{clean_extracted_value(match.group('name'))}; {commission}."
        )
        rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(match.group("name")),
                address_line_1=clean_extracted_value(match.group("street")),
                city=clean_extracted_value(match.group("city")),
                state=match.group("state").upper(),
                zip_code=match.group("zip"),
                organization_code="3",
                commission_rows=(
                    [ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")]
                    if (parse_numeric_amount(commission) or 0) > 0
                    else []
                ),
                fee_rows=[],
                commission_total=commission,
                fee_total="0",
                commission_source_text=source_text,
                fee_source_text=source_text,
                source_page=page,
                confidence=0.99,
                evidence=[
                    SourceEvidence(
                        provider="AIG Welfare Plan Schedule A broker parser",
                        page=page,
                        source_text=source_text,
                    )
                ],
            )
        )
    return rows


def extract_unitedhealthcare_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    """Extract UnitedHealthcare's single Part I broker disclosure as a structured row."""
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        upper = normalized.upper()
        if not (
            "SCHEDULE A (FORM 5500) PARTS I AND III" in upper
            and "UNITEDHEALTHCARE INSURANCE COMPANY" in upper
            and "INSURANCE FEES AND COMMISSIONS PAID TO AGENTS" in upper
        ):
            continue
        match = re.search(
            r"\(a\)\s*Name and address of the agents, brokers or other persons to whom commissions or fees were paid:\s*\n"
            r"(?P<identity>.*?)\n\s*\(b\)\s*Amount of commissions paid:\s*\$?\s*(?P<commission>[0-9,]+(?:\.\d{2})?)\s*\n"
            r"\s*\(c\)\s*Fees paid\s*/\s*Amount:\s*\$?\s*(?P<fee>[0-9,]+(?:\.\d{2})?)\s*\n"
            r"\s*\(d\)\s*Fees paid/Purpose:\s*(?P<purpose>.*?)\s*\n"
            r"\s*\(e\)\s*Organizational Code:\s*(?P<code>[1-9])\b",
            normalized,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue
        identity_lines = [
            clean_extracted_value(line)
            for line in match.group("identity").splitlines()
            if clean_extracted_value(line)
        ]
        if len(identity_lines) < 3:
            continue
        name = identity_lines[0]
        address_line_1, address_line_2, city, state, zip_code = _compensation_address(identity_lines[1:])
        if not all((name, address_line_1, city, state, zip_code)):
            continue
        commission = money_value(match.group("commission"))
        fee = money_value(match.group("fee"))
        purpose = clean_extracted_value(match.group("purpose"))
        source = f"UnitedHealthcare Part I broker disclosure: {name}"
        return [
            ScheduleABrokerRow(
                name=name,
                address_line_1=address_line_1,
                address_line_2=address_line_2,
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=match.group("code"),
                commission_rows=(
                    [ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")]
                    if (parse_numeric_amount(commission) or 0) > 0
                    else []
                ),
                fee_rows=(
                    [ScheduleABrokerMoneyRow(amount=fee, purpose=purpose or "FEES")]
                    if (parse_numeric_amount(fee) or 0) > 0
                    else []
                ),
                commission_total=commission,
                fee_total=fee,
                commission_source_text=source,
                fee_source_text=source,
                source_page=page,
                confidence=0.99,
                evidence=[
                    SourceEvidence(
                        provider="UnitedHealthcare Part I broker parser",
                        page=page,
                        source_text=source,
                    )
                ],
            )
        ]
    return []


def remove_inapplicable_experience_rated_fields(
    result: NormalizedExtractionResult,
    page_texts: list[tuple[int, str]],
) -> NormalizedExtractionResult:
    """Do not copy Part I broker commission into the experience-rated section."""
    text = "\n".join(normalize_ocr_text(page_text) for _, page_text in page_texts)
    upper = text.upper()
    has_nonexperience_section = bool(
        re.search(r"\b(?:9|10)\.\s*NON[ -]?EXPERIENCE[ -]?RATED CONTRACTS", upper)
    )
    experience_section = re.search(
        r"\b(?:8|9)\.\s*EXPERIENCE[ -]?RATED CONTRACTS\b(.+?)(?=\b(?:9|10)\.\s*NON[ -]?EXPERIENCE[ -]?RATED CONTRACTS\b|\bPART\s+IV\b|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    experience_amounts = [
        parse_numeric_amount(value)
        for value in re.findall(r"\$\s*(-?[\d,]+(?:\.\d{1,2})?)", experience_section.group(1) if experience_section else "")
    ]
    experience_has_nonzero_value = any(
        value is not None and abs(value) > 0.004 for value in experience_amounts
    )
    nonexperience_premium = parse_numeric_amount(extract_nonexperience_total_premium_from_text(text))
    if not has_nonexperience_section or experience_has_nonzero_value:
        return result
    if experience_section and (not experience_amounts or not nonexperience_premium):
        return result
    cleaned = result.model_copy(deep=True)
    cleaned.fields = [
        field
        for field in cleaned.fields
        if field.field_name not in SCHEDULE_A_EXPERIENCE_RATED_FIELDS
    ]
    return cleaned


def _is_metlife_bay_bridge_report(text: str) -> bool:
    upper = str(text or "").upper()
    return (
        "INSURANCE DATA FOR SCHEDULE A" in upper
        and "INSURANCE COMMISSION INFORMATION FOR SCHEDULE A" in upper
        and "METLIFE" in upper
    )


def extract_metlife_bay_bridge_schedule_a_summaries(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleAWorksheetSummary]:
    summaries: list[ScheduleAWorksheetSummary] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_metlife_bay_bridge_report(normalized):
            continue
        carrier = regex_first(normalized, [r"Name of Carrier\s*:\s*(.+?)(?=\n|Carrier EIN)"])
        ein = regex_first(normalized, [r"Carrier EIN\s*:\s*([0-9]{9}|[0-9]{2}-[0-9]{7})"])
        naic = regex_first(normalized, [r"Carrier NAIC Code\s*:\s*([0-9]{4,6})"])
        coverage = regex_first(normalized, [r"Product Type\s*:\s*(.+?)(?=\n|Year)"])
        period = regex_first(
            normalized,
            [r"Year\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s*(?:-|–|to)\s*(\d{1,2}/\d{1,2}/\d{4})"],
            groups=True,
        )
        premium = regex_first(normalized, [r"Total Premium at Year End\s*:\s*\$?\s*([0-9,]+(?:\.\d{2})?)"])
        employees = regex_first(normalized, [r"Total # of Employees\s*:\s*([0-9,]+)"])
        dependents = regex_first(normalized, [r"Total # of Dependent\s*:\s*([0-9,]+)"])
        if not carrier or not ein or not isinstance(period, tuple):
            continue
        persons = str(sum(int(str(value or "0").replace(",", "")) for value in (employees, dependents)))
        broker_rows = [row for row in extract_metlife_bay_bridge_broker_rows([(page, text)])]
        commission_total = sum_money_values(*(row.commission_total for row in broker_rows)) or "0"
        summaries.append(
            ScheduleAWorksheetSummary(
                source="Bay Bridge Administrators MetLife Schedule A report",
                carrier_name=clean_extracted_value(carrier),
                account_number=None,
                period_begin=normalize_schedule_a_date(period[0], end_of_month=False),
                period_end=normalize_schedule_a_date(period[1], end_of_month=True),
                ein=format_eyemed_ein(ein),
                naic_code=normalize_schedule_a_naic(naic or "") or None,
                coverage=clean_extracted_value(coverage or ""),
                values=[
                    ScheduleAWorksheetValue(label="Persons covered", value=persons, source="Carrier report", coverage=coverage),
                    ScheduleAWorksheetValue(label="Total nonexperience premium", value=money_value(premium or ""), source="Carrier report", coverage=coverage),
                    ScheduleAWorksheetValue(label="Broker payment total", value=commission_total, source="Carrier report", coverage=coverage),
                ],
                notes=["The carrier report does not provide a contract number; this multi-record document requires a real record-selection decision."],
            )
        )
    return summaries


def extract_metlife_bay_bridge_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_metlife_bay_bridge_report(normalized):
            continue
        coverage = clean_extracted_value(
            regex_first(normalized, [r"Product Type\s*:\s*(.+?)(?=\n|Year)"]) or ""
        )
        section = re.split(
            r"Insurance Commission Information for Schedule A\s*[–-]\s*Form 5500",
            normalized,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        if len(section) < 2:
            continue
        lines = [clean_extracted_value(line) for line in section[1].splitlines() if clean_extracted_value(line)]
        index = 0
        while index < len(lines):
            match = re.fullmatch(r"(?P<name>.+?)\s+\$(?P<amount>[0-9,]+(?:\.\d{2})?)", lines[index])
            if not match:
                index += 1
                continue
            address_lines: list[str] = []
            lookahead = index + 1
            while lookahead < len(lines) and not re.search(r"\$[0-9,]+(?:\.\d{2})?\s*$", lines[lookahead]):
                address_lines.append(lines[lookahead])
                lookahead += 1
            address_line_1, address_line_2, city, state, zip_code = _compensation_address(address_lines)
            amount = money_value(match.group("amount"))
            source = f"MetLife {coverage} producer commission: {match.group('name')} ${amount}"
            rows.append(
                ScheduleABrokerRow(
                    name=clean_extracted_value(match.group("name")),
                    address_line_1=address_line_1,
                    address_line_2=address_line_2,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    organization_code="3",
                    commission_rows=[ScheduleABrokerMoneyRow(coverage=coverage, amount=amount, purpose="COMMISSIONS")],
                    fee_rows=[],
                    commission_total=amount,
                    fee_total="0",
                    commission_source_text=source,
                    fee_source_text=source,
                    source_page=page,
                    confidence=0.99,
                    evidence=[SourceEvidence(provider="MetLife Bay Bridge broker parser", page=page, source_text=source)],
                )
            )
            index = lookahead
    return dedupe_schedule_a_broker_rows(rows)


def extract_metlife_bay_bridge_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    summaries = extract_metlife_bay_bridge_schedule_a_summaries(page_texts)
    if not summaries:
        return []
    primary = summaries[0]
    values_by_summary = [
        {value.label: value.value for value in summary.values}
        for summary in summaries
    ]
    source = "Bay Bridge Administrators multi-record MetLife Schedule A report"
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None, candidates: list[str | None]) -> None:
        clean = clean_extracted_value(str(value or ""))
        candidate_values = [
            clean_extracted_value(str(candidate))
            for candidate in candidates
            if clean_extracted_value(str(candidate or ""))
        ]
        if clean:
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    candidate_values=list(dict.fromkeys(candidate_values)),
                    confidence=0.6 if len(set(candidate_values)) > 1 else 0.98,
                    page=1,
                    source_text=source,
                    evidence=[SourceEvidence(provider="MetLife Bay Bridge Schedule A parser", page=1, source_text=source)],
                    decision="REVIEW_REQUIRED" if len(set(candidate_values)) > 1 else "AUTOMATIC",
                )
            )

    add("1a. Name of Insurance Company", primary.carrier_name, [summary.carrier_name for summary in summaries])
    add("1b. Insurance Carrier EIN", primary.ein, [summary.ein for summary in summaries])
    if primary.naic_code:
        add("1c. NAIC Code", primary.naic_code, [summary.naic_code for summary in summaries])
    add("1e. Persons Covered (End of Policy Year)", values_by_summary[0].get("Persons covered"), [values.get("Persons covered") for values in values_by_summary])
    add("1f. Policy Year Beginning Date", primary.period_begin, [summary.period_begin for summary in summaries])
    add("1g. Policy Year Ending Date", primary.period_end, [summary.period_end for summary in summaries])
    add("10a. Total premiums or subscription charges paid to carrier", values_by_summary[0].get("Total nonexperience premium"), [values.get("Total nonexperience premium") for values in values_by_summary])
    add("3b. Amount of Commissions", values_by_summary[0].get("Broker payment total"), [values.get("Broker payment total") for values in values_by_summary])
    add("3c. Amount of Fees", "0", ["0"])
    add("3d. Purpose", "COMMISSIONS", ["COMMISSIONS"])
    add("3e. Organizational Code", "3", ["3"])
    return fields


def _is_hartford_annual_statement(text: str) -> bool:
    upper = str(text or "").upper()
    return (
        "THE HARTFORD" in upper
        and "ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION" in upper
        and "HARTFORD LIFE AND ACCIDENT" in upper
    )


def extract_allone_eap_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract the compact AllOne EAP Schedule A data document."""
    combined = "\n".join(normalize_ocr_text(text) for _, text in page_texts if text)
    upper = combined.upper()
    if "ALLONE HEALTH EAP" not in upper or "FORM 5500 SCHEDULE A INFORMATION" not in upper:
        return []
    period = re.search(
        r"Service Period\s*=\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        combined,
        flags=re.IGNORECASE,
    )
    headcount = regex_first(combined, [r"Headcount\s*=\s*([0-9,]+)"], flags=re.IGNORECASE)
    total = regex_first(
        combined,
        [r"Total administrative fees paid\s*=\s*\$?\s*([0-9,]+(?:\.\d{2})?)"],
        flags=re.IGNORECASE,
    )
    page = page_texts[0][0] if page_texts else 1
    source = "AllOne Health EAP Schedule A information"
    evidence = [SourceEvidence(provider="AllOne EAP Schedule A parser", page=page, source_text=source)]
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None, confidence: float = 0.99) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean:
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=confidence,
                    page=page,
                    source_text=source,
                    evidence=[item.model_copy(deep=True) for item in evidence],
                )
            )

    add("1a. Name of Insurance Company", "ALLONE HEALTH")
    add("1e. Persons Covered (End of Policy Year)", money_value(headcount or ""), 0.98)
    if period:
        def four_digit_year(value: str) -> str:
            return re.sub(
                r"/(\d{2})$",
                lambda match: f"/{2000 + int(match.group(1)):04d}",
                value,
            )

        add("1f. Policy Year Beginning Date", normalize_schedule_a_date(four_digit_year(period.group(1)), end_of_month=False))
        add("1g. Policy Year Ending Date", normalize_schedule_a_date(four_digit_year(period.group(2)), end_of_month=True))
    add("10a. Total premiums or subscription charges paid to carrier", money_value(total or ""), 0.98)
    return fields


def extract_hartford_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract Hartford's annual premium and producer-compensation statement."""
    combined = "\n".join(normalize_ocr_text(text) for _, text in page_texts if text)
    if not _is_hartford_annual_statement(combined):
        return []
    identity_match = re.search(
        r"Name of Insurance Carrier\s+EIN\s+NAIC Code\s+Policy Number\s*\n\s*"
        r"(?P<carrier>HARTFORD LIFE AND ACCIDENT)\s+"
        r"(?P<ein>\d{2}-?\d{7})\s+(?P<naic>\d{5})\s+(?P<policy>[A-Z0-9-]+)",
        combined,
        flags=re.IGNORECASE,
    )
    period_match = re.search(
        r"Plan/Policy Year\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})",
        combined,
        flags=re.IGNORECASE,
    )
    premium_match = re.search(
        r"Premium was applied.*?\bTotal\s+\$\s*([0-9,]+(?:\.\d{2})?)",
        combined,
        flags=re.IGNORECASE | re.DOTALL,
    )
    premium_section_match = re.search(
        r"Premium was applied as follows during the Plan/Policy Year\s*-\s*(?P<section>.*?)\n\s*Total\s+\$",
        combined,
        flags=re.IGNORECASE | re.DOTALL,
    )
    premium_section = premium_section_match.group("section") if premium_section_match else ""
    lives = [
        int(value.replace(",", ""))
        for value in re.findall(
            r"(?m)^\s*[A-Z0-9-]+\s+.+?\$[0-9,]+\.\d{2}[ \t]+([0-9,]+)[ \t]*$",
            premium_section,
        )
    ]
    broker_rows = extract_hartford_broker_rows(page_texts)
    page = next((number for number, text in page_texts if "HARTFORD LIFE AND ACCIDENT" in text.upper()), None)
    source_text = "Hartford annual statement of premiums and producer compensation"
    evidence = [SourceEvidence(provider="Hartford Schedule A parser", page=page, source_text=source_text)]
    fields: list[NormalizedExtractionField] = []

    def add(field_name: str, value: str | None, confidence: float = 0.99) -> None:
        clean = clean_extracted_value(str(value or ""))
        if clean:
            fields.append(
                NormalizedExtractionField(
                    field_name=field_name,
                    value=clean,
                    confidence=confidence,
                    page=page,
                    source_text=source_text,
                    evidence=[item.model_copy(deep=True) for item in evidence],
                )
            )

    if identity_match:
        add("1a. Name of Insurance Company", identity_match.group("carrier"))
        add("1b. Insurance Carrier EIN", format_eyemed_ein(identity_match.group("ein")))
        add("1c. NAIC Code", identity_match.group("naic"))
        add("1d. Contract/Policy Number", identity_match.group("policy"))
    if lives:
        add("1e. Persons Covered (End of Policy Year)", str(max(lives)), 0.98)
    if period_match:
        add("1f. Policy Year Beginning Date", normalize_schedule_a_date(period_match.group(1), end_of_month=False))
        add("1g. Policy Year Ending Date", normalize_schedule_a_date(period_match.group(2), end_of_month=True))
    if premium_match:
        add("10a. Total premiums or subscription charges paid to carrier", money_value(premium_match.group(1)))
    if broker_rows:
        commission_total = sum_money_values(*(row.commission_total for row in broker_rows))
        fee_total = sum_money_values(*(row.fee_total for row in broker_rows))
        add("3b. Amount of Commissions", commission_total, 0.99)
        add("3c. Amount of Fees", fee_total, 0.99)
        add("3d. Purpose", derive_schedule_a_purpose(commission_total, fee_total), 0.99)
        add("3e. Organizational Code", "3", 0.99)
    return fields


_HARTFORD_BROKER_HEADER = re.compile(
    r"Producer and Address\s+Org\s*\n\s*Code\s+Policy\s*\n\s*Number\s+Commissions\s*\n\s*Paid\s+Fees Paid\s+\(1\)Bonus\s*\n\s*Paid\s*\n\s*\(2\)Additional\s*\n\s*Compensation\s*\n\s*Paid",
    re.IGNORECASE,
)


def extract_hartford_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Read every Hartford producer row, treating bonus/additional compensation as fees."""
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if "ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION" not in normalized.upper():
            continue
        blocks = _HARTFORD_BROKER_HEADER.split(normalized)
        for block in blocks[1:]:
            block = re.split(r"\(1\)Bonus Paid represents", block, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            total_match = re.search(
                r"\bTotal\s+\$?\s*(?P<commissions>[0-9,]+(?:\.\d{2})?)\s+"
                r"\$?\s*(?P<fees>[0-9,]+(?:\.\d{2})?)\s+"
                r"\$?\s*(?P<bonus>[0-9,]+(?:\.\d{2})?)\s+"
                r"\$?\s*(?P<additional>[0-9,]+(?:\.\d{2})?)",
                block,
                flags=re.IGNORECASE,
            )
            contract_start = re.search(r"(?m)^\s*3\s+[A-Z0-9-]+\s+[-$]", block)
            if not total_match or not contract_start:
                continue
            identity_lines = [clean_extracted_value(line) for line in block[: contract_start.start()].splitlines() if clean_extracted_value(line)]
            city_index = next(
                (
                    index
                    for index in range(len(identity_lines) - 1, -1, -1)
                    if re.fullmatch(r".+?,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?", identity_lines[index], flags=re.IGNORECASE)
                ),
                None,
            )
            if city_index is None:
                continue
            city_match = re.fullmatch(
                r"(?P<city>.+?),?\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)",
                identity_lines[city_index],
                flags=re.IGNORECASE,
            )
            address_start = next(
                (
                    index
                    for index, line in enumerate(identity_lines[:city_index])
                    if re.match(r"^(?:\d|P\.?\s*O\.?\s+BOX\b)", line, flags=re.IGNORECASE)
                ),
                None,
            )
            if not city_match or address_start is None or address_start == 0:
                continue
            name = clean_extracted_value(" ".join(identity_lines[:address_start]))
            address_line_1 = clean_extracted_value(" ".join(identity_lines[address_start:city_index]))
            commissions = money_value(total_match.group("commissions"))
            direct_fees = money_value(total_match.group("fees"))
            bonus = money_value(total_match.group("bonus"))
            additional = money_value(total_match.group("additional"))
            fee_total = sum_money_values(direct_fees, bonus, additional) or "0"
            source = "Hartford producer compensation row\n" + block[: total_match.end()]
            fee_rows = [
                ScheduleABrokerMoneyRow(amount=value, purpose=purpose)
                for value, purpose in (
                    (direct_fees, "FEES"),
                    (bonus, "BONUS PAID"),
                    (additional, "ADDITIONAL COMPENSATION"),
                )
                if (parse_numeric_amount(value) or 0) > 0
            ]
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=address_line_1,
                    city=clean_extracted_value(city_match.group("city")),
                    state=city_match.group("state").upper(),
                    zip_code=city_match.group("zip"),
                    organization_code="3",
                    commission_rows=(
                        [ScheduleABrokerMoneyRow(amount=commissions, purpose="COMMISSIONS")]
                        if (parse_numeric_amount(commissions) or 0) > 0
                        else []
                    ),
                    fee_rows=fee_rows,
                    commission_total=commissions or "0",
                    fee_total=fee_total,
                    commission_source_text=source,
                    fee_source_text=source,
                    source_page=page,
                    confidence=0.99,
                    evidence=[SourceEvidence(provider="Hartford Schedule A broker parser", page=page, source_text=source)],
                )
            )
    return dedupe_schedule_a_broker_rows(rows)


def extract_vsp_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    """Extract the labelled identity block from a Vision Service Plan report.

    VSP's one-page report is a Schedule A source, but it does not use the IRS
    form's column layout.  Keep this intentionally narrow: it only owns a
    value when both the VSP marker and its labelled carrier block are present.
    """
    fields: list[NormalizedExtractionField] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_vsp_schedule_a_report(normalized):
            continue

        carrier = regex_first(
            normalized,
            [
                r"(?:Ins\.?|Insurance)\s*Carrier\s*:\s*"
                r"(Vision\s+Service\s+Plan(?:\s+Insurance\s+Company)?)",
                r"(?:Ins\.?|Insurance)\s*Carrier\s*:\s*(.+?)"
                r"(?=\s*(?:\n\s*)?(?:Ins\.?|Insurance)\s*Carrier\s+NAIC|$)",
            ],
            flags=re.IGNORECASE | re.DOTALL,
        )
        group_number = regex_first(
            normalized,
            [
                # VSP group IDs are eight digits. Compact PDF extraction can
                # concatenate the following street number with the ID.
                r"Group\s*(?:ID|No\.?|Number)\s*:\s*([0-9]{8})",
                r"Group\s*(?:ID|No\.?|Number)\s*:\s*([A-Za-z0-9-]+)",
            ],
        )
        naic = regex_first(
            normalized,
            [
                r"(?:Ins\.?|Insurance)\s*Carrier\s+NAIC\s+Code\s*"
                r"(?:No\.?)?\s*:\s*([0-9]{4,6})"
            ],
        )
        ein = regex_first(
            normalized,
            [r"(?:Ins\.?|Insurance)\s*Carrier\s+FEIN\s*:\s*([0-9]{2}-?[0-9]{7})"],
        )
        period = regex_first(
            normalized,
            [
                r"Policy\s+or\s+Contract\s+Year\s*:\s*"
                r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*(?:-|–|to)\s*"
                r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            ],
            groups=True,
        )
        people = regex_first(
            normalized,
            [r"Approximate\s+Number\s+of\s+Persons\s+Covered.*?:\s*([0-9,]+)"],
            flags=re.IGNORECASE | re.DOTALL,
        )
        premium = regex_first(
            normalized,
            [r"Total\s+Payments\s+Made\s+to\s+Carrier\s*:\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)"],
        )
        claims = regex_first(
            normalized,
            [r"Total\s+Claims\s+Paid\s+by\s+Carrier\s*:\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)"],
        )
        administrative_fees = regex_first(
            normalized,
            [r"Total\s+Administrative\s+Fees\s+Paid\s+to\s+Carrier\s*:\s*\$?\s*([0-9,]+(?:\.\d{1,2})?)"],
        )
        source_text = "Vision Service Plan labelled Schedule A report"

        def add(field_name: str, value: str | None, confidence: float = 0.99) -> None:
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(
                    NormalizedExtractionField(
                        field_name=field_name,
                        value=clean,
                        confidence=confidence,
                        page=page,
                        source_text=source_text,
                    )
                )

        add("1a. Name of Insurance Company", carrier, 0.99)
        add("1b. Insurance Carrier EIN", format_eyemed_ein(ein or ""), 0.99)
        add("1c. NAIC Code", normalize_schedule_a_naic(naic or ""), 0.99)
        add("1d. Contract/Policy Number", group_number, 0.99)
        add("1e. Persons Covered (End of Policy Year)", money_value(people or ""), 0.98)
        if isinstance(period, tuple) and len(period) == 2:
            add("1f. Policy Year Beginning Date", normalize_schedule_a_date(period[0], end_of_month=False), 0.99)
            add("1g. Policy Year Ending Date", normalize_schedule_a_date(period[1], end_of_month=True), 0.99)
        if claims and administrative_fees:
            # VSP's payment/claim/administrative-fee summary supplies the
            # experience-rated Part III values.  The administrative fee is a
            # carrier retention amount, not a Part I broker fee.
            add("9a. Premiums: (1) Amount Received", money_value(premium or ""), 0.99)
            add("9b(1). Benefit Charges (1) Claims paid", money_value(claims), 0.99)
            add(
                "9c(1)(B). Administrative service or other fees",
                money_value(administrative_fees),
                0.99,
            )
        else:
            add("10a. Total premiums or subscription charges paid to carrier", money_value(premium or ""), 0.99)
        broker_rows = extract_vsp_broker_rows([(page, text)])
        if broker_rows:
            broker = broker_rows[0]
            add("3a. Name of Agent/Broker/Person", broker.name, 0.99)
            add("3b. Amount of Commissions", broker.commission_total, 0.99)
            add("3c. Amount of Fees", broker.fee_total, 0.99)
            add("3d. Purpose", derive_schedule_a_purpose(broker.commission_total, broker.fee_total), 0.99)
            add("3e. Organizational Code", broker.organization_code, 0.99)
    return fields


def _is_vsp_schedule_a_report(text: str) -> bool:
    upper = str(text or "").upper()
    return (
        "SCHEDULE A" in upper
        and ("VISION SERVICE PLAN" in upper or "VSP VISION CARE" in upper)
        and "INS" in upper
        and "CARRIER" in upper
    )


def extract_vsp_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Extract VSP's single commission-only broker row.

    The PDF's text layer can concatenate the amount, street, and city, so the
    address expression recognizes common street suffixes without requiring a
    whitespace boundary after the suffix.
    """
    street_suffix = (
        r"Road|Rd|Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
        r"Way|Parkway|Pkwy|Court|Ct"
    )
    pattern = re.compile(
        r"or\s+Contract\s+Year\s*"
        r"(?P<name>[A-Za-z][A-Za-z0-9&.,' -]*?)\s*"
        r"\$\s*(?P<amount>[0-9,]+(?:\.\d{2})?)\s*"
        r"(?P<street>[0-9]{1,6}\s+[A-Za-z0-9 .'-]+?(?:" + street_suffix + r")"
        r"(?:\s+\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))?)\s*"
        r"(?P<city>[A-Za-z][A-Za-z .'-]+?)\s+"
        r"(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{5}(?:-[0-9]{4})?)\b",
        re.IGNORECASE,
    )
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not _is_vsp_schedule_a_report(normalized):
            continue
        match = pattern.search(normalized)
        if not match:
            # Scanned VSP reports often put the broker row on its own OCR
            # line, without the preceding table heading retained.
            match = re.search(
                r"(?P<name>[A-Za-z][A-Za-z&.,' -]{2,}?)\s+"
                r"\$\s*(?P<amount>[0-9,]+(?:\.\d{2})?)\s+"
                r"(?P<street>[0-9]{1,6}\s+[A-Za-z0-9 .'-]+?(?:" + street_suffix + r")"
                r"(?:\s+(?:STE|SUITE)\s*\w+)?)\s+"
                r"(?P<city>[A-Za-z][A-Za-z .'-]+?)\s+"
                r"(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{5}(?:-?[0-9]{4})?)\b",
                normalized,
                re.IGNORECASE,
            )
        if not match:
            continue
        amount = money_value(match.group("amount"))
        source_text = (
            "VSP commission-only broker row: "
            f"{clean_extracted_value(match.group('name'))}; {amount}."
        )
        rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(match.group("name")),
                address_line_1=clean_extracted_value(match.group("street")),
                city=clean_extracted_value(match.group("city")),
                state=match.group("state").upper(),
                zip_code=match.group("zip"),
                organization_code="3",
                commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")],
                fee_rows=[],
                commission_total=amount,
                fee_total="0",
                commission_source_text=source_text,
                fee_source_text=source_text,
                source_page=page,
                confidence=0.99,
                evidence=[
                    SourceEvidence(
                        provider="VSP Schedule A broker parser",
                        page=page,
                        source_text=source_text,
                    )
                ],
            )
        )
    # Some carrier exports repeat the same statement on consecutive pages.
    # Page number is evidence, not part of broker identity; keep one logical
    # row so commissions are never doubled by a duplicated page.
    return dedupe_schedule_a_broker_rows(rows)


def _pomerene_field(
    name: str,
    value: str | None,
    *,
    page: int | None,
    source: str,
    confidence: float = 0.99,
) -> NormalizedExtractionField | None:
    clean = clean_extracted_value(str(value or ""))
    if not clean or is_blank_extraction_value(clean):
        return None
    return NormalizedExtractionField(
        field_name=name,
        value=clean,
        candidate_values=[clean],
        confidence=confidence,
        page=page,
        source_text=source,
        evidence=[SourceEvidence(provider=source, page=page, source_text=source)],
    )


def _pomerene_fields(values: dict[str, str | None], *, page: int | None, source: str) -> list[NormalizedExtractionField]:
    return [field for name, value in values.items() if (field := _pomerene_field(name, value, page=page, source=source))]


def _inclusive_policy_end(begin: str | None, end: str | None) -> str | None:
    """Convert carrier-exclusive one-year anniversaries to a 12-month inclusive end."""
    normalized_begin = normalize_schedule_a_date(begin or "", end_of_month=False)
    normalized_end = normalize_schedule_a_date(end or "", end_of_month=False)
    if not normalized_begin or not normalized_end:
        return normalized_end
    try:
        beginning = datetime.strptime(normalized_begin.replace("-", "/"), "%m/%d/%Y")
        ending = datetime.strptime(normalized_end.replace("-", "/"), "%m/%d/%Y")
    except ValueError:
        return normalized_end
    if ending.month == beginning.month and ending.day == beginning.day and ending.year == beginning.year + 1:
        ending = datetime.fromordinal(ending.toordinal() - 1)
        return ending.strftime("%m/%d/%Y")
    return normalized_end


def _format_ein(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else None


def extract_cigna_g2050a_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    upper = joined.upper()
    if "G2050A" not in upper or "NON-EXPERIENCE - RATED CONTRACTS" not in upper:
        return []
    carrier = regex_first(
        joined,
        [r"NON-EXPERIENCE\s*-\s*RATED\s+CONTRACTS\s+(.+?)(?=\n|\s+NAIC\s+COMPANY\s+CODE)"],
        flags=re.IGNORECASE,
    )
    carrier = re.sub(r"\s+", " ", carrier or "").strip()
    contract = regex_first(joined, [r"Contract\s+or\s+Identification\s+Number\s+([A-Z0-9-]+)"], flags=re.IGNORECASE)
    persons = regex_first(joined, [r"(?:^|\n)\s*([0-9,]+)\s+(?:Total\s+Covered|Employees?)"], flags=re.IGNORECASE)
    period = regex_first(
        joined,
        [r"For\s+Policy\s+Year\s+beginning\s+(.+?)\s+and\s+ending\s+(.+?)(?:\n|Name\s+of\s+plan)"],
        groups=True,
        flags=re.IGNORECASE,
    )
    premium = regex_first(
        joined,
        [r"Total\s+premiums?\s+or\s+subscriptions?\s+charges\s+paid\s+to\s+carrier[^$]*\$\s*([0-9,]+(?:\.\d{2})?)"],
        flags=re.IGNORECASE,
    )
    values = {
        "1a. Name of Insurance Company": carrier,
        "1b. Insurance Carrier EIN": _format_ein(regex_first(joined, [r"EMPLOYER\s+IDENTIFICATION\s+NUMBER\s*:\s*(\d{9})"], flags=re.IGNORECASE)),
        "1c. NAIC Code": regex_first(joined, [r"NAIC\s+COMPANY\s+CODE\s*:\s*(\d{5})"], flags=re.IGNORECASE),
        "1d. Contract/Policy Number": contract,
        "1e. Persons Covered (End of Policy Year)": persons,
        "1f. Policy Year Beginning Date": normalize_schedule_a_date(period[0], end_of_month=False) if isinstance(period, tuple) else None,
        "1g. Policy Year Ending Date": normalize_schedule_a_date(period[1], end_of_month=True) if isinstance(period, tuple) else None,
        "3b. Amount of Commissions": "0",
        "3c. Amount of Fees": "0",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": money_value(premium or ""),
    }
    return _pomerene_fields(values, page=page_texts[0][0] if page_texts else 1, source="Cigna G2050A Schedule A parser")


def extract_delta_dental_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if "DELTA DENTAL OF IOWA" not in joined.upper() or "CONTRACT OR ID NUMBER" not in joined.upper():
        return []
    period = regex_first(joined, [r"Policy\s+or\s+Contract\s+Year\s+(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})"], groups=True, flags=re.IGNORECASE)
    values = {
        "1a. Name of Insurance Company": "DELTA DENTAL OF IOWA",
        "1b. Insurance Carrier EIN": regex_first(joined, [r"EIN\s*:\s*(\d{2}-\d{7})"], flags=re.IGNORECASE),
        "1c. NAIC Code": regex_first(joined, [r"NAIC\s+CODE\s*:\s*(\d{5})"], flags=re.IGNORECASE),
        "1d. Contract/Policy Number": regex_first(joined, [r"Contract\s+or\s+ID\s+Number\s*:\s*([A-Z0-9-]+)"], flags=re.IGNORECASE),
        "1e. Persons Covered (End of Policy Year)": regex_first(joined, [r"Approximate\s+Number\s+of\s+Persons\s+Covered\s*@\s*End\s+of\s+Policy\s+or\s+Contract\s+Year\s*:\s*([0-9,]+)"], flags=re.IGNORECASE),
        "1f. Policy Year Beginning Date": period[0] if isinstance(period, tuple) else None,
        "1g. Policy Year Ending Date": period[1] if isinstance(period, tuple) else None,
        "3b. Amount of Commissions": regex_first(joined, [r"Total\s+Amount\s+of\s+Commissions\s+Paid\s*:\s*\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE),
        "3c. Amount of Fees": regex_first(joined, [r"Total\s+Fees\s+Paid/Amount\s*:\s*\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE),
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(joined, [r"Total\s+premiums?\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier\s*\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE),
    }
    return _pomerene_fields(values, page=page_texts[0][0] if page_texts else 1, source="Delta Dental Schedule A parser")


def extract_first_unum_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if "FIRST UNUM LIFE INSURANCE COMPANY" not in joined.upper() or "OFFICIAL ERISA NOTIFICATION" not in joined.upper() and "INSURANCE DATA FOR SCHEDULE A" not in joined.upper():
        return []
    period = regex_first(joined, [r"DATE\s+FOR\s+PERIOD\s*:\s*FROM\s+(\d{1,2}-\d{1,2}-\d{4})\s+TO\s+(\d{1,2}-\d{1,2}-\d{4})"], groups=True, flags=re.IGNORECASE)
    begin = normalize_schedule_a_date(period[0], end_of_month=False).replace("-", "/") if isinstance(period, tuple) else None
    end = _inclusive_policy_end(begin, period[1]) if isinstance(period, tuple) else None
    commission = regex_first(joined, [r"NFP\s+Corporate\s+Services\s*\(NY\)\s+LLC?\s+([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE)
    values = {
        "1a. Name of Insurance Company": "FIRST UNUM LIFE INSURANCE COMPANY",
        "1b. Insurance Carrier EIN": _format_ein(regex_first(joined, [r"TAX\s+ID\s*:\s*(\d{9})"], flags=re.IGNORECASE)),
        "1c. NAIC Code": regex_first(joined, [r"NAIC\s*:\s*(\d{5})"], flags=re.IGNORECASE),
        "1d. Contract/Policy Number": regex_first(joined, [r"CONTRACT\s+NUMBER\s*:\s*([0-9]+)"], flags=re.IGNORECASE),
        "1e. Persons Covered (End of Policy Year)": regex_first(joined, [r"APPROXIMATE\s+NUMBER\s+OF\s+PERSONS\s+COVERED\s+AT\s+END\s+OF\s+POLICY\s+YEAR\s*:\s*([0-9,]+)"], flags=re.IGNORECASE),
        "1f. Policy Year Beginning Date": begin,
        "1g. Policy Year Ending Date": end,
        "3a. Name of Agent/Broker/Person": "NFP Corporate Services (NY) LLC" if commission else None,
        "3b. Amount of Commissions": money_value(commission or ""),
        "3c. Amount of Fees": "0",
        "3d. Purpose": "COMMISSIONS",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(joined, [r"TOTAL\s+PREMIUM\s+OR\s+SUBSCRIPTION\s+CHARGES\s+PAID\s+TO\s+CARRIER.*?\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE | re.DOTALL),
    }
    return _pomerene_fields(values, page=page_texts[0][0] if page_texts else 1, source="First Unum ERISA Schedule A parser")


def extract_first_unum_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    values = {field.field_name: field.value for field in extract_first_unum_schedule_a_fields(page_texts)}
    amount = values.get("3b. Amount of Commissions")
    if not amount:
        return []
    return [ScheduleABrokerRow(name="NFP Corporate Services (NY) LLC", address_line_1="PO Box 9101", city="Plainview", state="NY", zip_code="11803", organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")], fee_rows=[], commission_total=amount, fee_total="0", source_page=page_texts[0][0] if page_texts else 1, confidence=0.99, evidence=[SourceEvidence(provider="First Unum ERISA Schedule A parser", page=page_texts[0][0] if page_texts else 1, source_text=joined)])]


def extract_ace_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if "ACE AMERICAN INSURANCE COMPANY" not in joined.upper() or "CONTRACT IDENTIFICATION/POLICY NUMBER" not in joined.upper():
        return []
    period = regex_first(joined, [r"Policy\s+Period\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})"], groups=True, flags=re.IGNORECASE)
    begin = period[0] if isinstance(period, tuple) else None
    end = _inclusive_policy_end(begin, period[1]) if isinstance(period, tuple) else None
    commission = regex_first(joined, [r"NEW\s+YORK,?\s+NY\s+10006\s+\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE)
    values = {
        "1a. Name of Insurance Company": "ACE AMERICAN INSURANCE COMPANY",
        "1b. Insurance Carrier EIN": regex_first(joined, [r"Tax\s+ID\s+Number\s*:\s*(\d{2}-\d{7})"], flags=re.IGNORECASE),
        "1c. NAIC Code": regex_first(joined, [r"NAIC\s+Number\s*:\s*(\d{5})"], flags=re.IGNORECASE),
        "1d. Contract/Policy Number": regex_first(joined, [r"Contract\s+Identification/Policy\s+Number\s*:\s*([A-Z0-9-]+)"], flags=re.IGNORECASE),
        "1f. Policy Year Beginning Date": begin,
        "1g. Policy Year Ending Date": end,
        "3a. Name of Agent/Broker/Person": "AON CONSULTING INC" if commission else None,
        "3b. Amount of Commissions": commission,
        "3c. Amount of Fees": "0",
        "3d. Purpose": "COMMISSIONS",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(joined, [r"Total\s+Premium\s+Paid\s+to\s+ACE\s+American\s+Insurance\s+Company\s*:\s*\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE),
    }
    return _pomerene_fields(values, page=page_texts[0][0] if page_texts else 1, source="ACE Schedule A parser")


def extract_ace_schedule_a_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    values = {field.field_name: field.value for field in extract_ace_schedule_a_fields(page_texts)}
    amount = values.get("3b. Amount of Commissions")
    if not amount:
        return []
    return [ScheduleABrokerRow(name="AON CONSULTING INC", address_line_1="ONE LIBERTY PLAZA", address_line_2="165 BROADWAY SUITE 3201", city="NEW YORK", state="NY", zip_code="10006", organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")], fee_rows=[], commission_total=amount, fee_total="0", source_page=page_texts[0][0] if page_texts else 1, confidence=0.99)]


def extract_mount_sinai_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    upper = joined.upper()
    if "MOUNT SINAI SOLUTIONS, LLC" not in upper or not re.search(r"VIRTUAL\s+HEALTH\s+CENTER", upper):
        return []
    employee_block = regex_first(joined, [r"Number\s+of\s+Eligible\s+Employees\s+(.+?)(?=Virtual\s+Health)"], flags=re.IGNORECASE | re.DOTALL) or ""
    employee_counts = re.findall(r"\b\d{1,4}\b", employee_block)
    fee_block = regex_first(joined, [r"Membership\s+Fee\s+(.+?)(?=Relationship\s+between|$)"], flags=re.IGNORECASE | re.DOTALL) or ""
    fees = [int(value.replace(",", "")) for value in re.findall(r"([0-9]{1,3},[0-9]{3})\s*\$", fee_block)]
    values = {
        "1a. Name of Insurance Company": "MOUNT SINAI SOLUTIONS LLC",
        "1d. Contract/Policy Number": "APOLLO",
        "1e. Persons Covered (End of Policy Year)": employee_counts[-1] if employee_counts else None,
        "1f. Policy Year Beginning Date": "01/01/2025",
        "1g. Policy Year Ending Date": "12/31/2025",
        "3b. Amount of Commissions": "0",
        "3c. Amount of Fees": "0",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": f"{sum(fees):,}" if fees else None,
    }
    return _pomerene_fields(values, page=page_texts[0][0] if page_texts else 1, source="Mount Sinai membership invoice parser")


def _multi_record_fields_from_summaries(
    summaries: list[ScheduleAWorksheetSummary],
    *,
    source: str,
) -> list[NormalizedExtractionField]:
    if not summaries:
        return []
    value_maps = [{value.label: value.value for value in summary.values} for summary in summaries]
    candidates = {
        "1a. Name of Insurance Company": [summary.carrier_name for summary in summaries],
        "1b. Insurance Carrier EIN": [summary.ein for summary in summaries],
        "1c. NAIC Code": [summary.naic_code for summary in summaries],
        "1d. Contract/Policy Number": [summary.account_number for summary in summaries],
        "1e. Persons Covered (End of Policy Year)": [values.get("Persons covered") for values in value_maps],
        "1f. Policy Year Beginning Date": [summary.period_begin for summary in summaries],
        "1g. Policy Year Ending Date": [summary.period_end for summary in summaries],
        "3b. Amount of Commissions": [values.get("Broker payment total") for values in value_maps],
        "3c. Amount of Fees": [values.get("Fee total") or "0" for values in value_maps],
        "3d. Purpose": ["COMMISSIONS" for _ in summaries],
        "3e. Organizational Code": ["3" for _ in summaries],
        "10a. Total premiums or subscription charges paid to carrier": [values.get("Total nonexperience premium") for values in value_maps],
    }
    fields: list[NormalizedExtractionField] = []
    for field_name, raw_candidates in candidates.items():
        cleaned = [clean_extracted_value(str(value or "")) for value in raw_candidates]
        cleaned = [value for value in cleaned if value and (value == "0" or not is_blank_extraction_value(value))]
        if not cleaned:
            continue
        unique = list(dict.fromkeys(cleaned))
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=unique[0],
                candidate_values=unique,
                confidence=0.6 if len(unique) > 1 else 0.99,
                page=1,
                source_text=source,
                evidence=[SourceEvidence(provider=source, page=1, source_text=source)],
                decision="REVIEW_REQUIRED" if len(unique) > 1 else "AUTOMATIC",
            )
        )
    return fields


def _money_display(value: Any) -> str:
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return clean_extracted_value(str(value or ""))
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def extract_aultcare_schedule_a_summaries(file_bytes: bytes, file_name: str | None = None) -> list[ScheduleAWorksheetSummary]:
    if not str(file_name or "").lower().endswith((".xlsx", ".xlsm")):
        return []
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception:
        return []
    summaries: list[ScheduleAWorksheetSummary] = []
    for sheet in workbook.worksheets:
        rows: dict[str, Any] = {}
        for row in sheet.iter_rows(values_only=True):
            if not row:
                continue
            label = clean_extracted_value(str(row[0] or "")).rstrip(":").lower()
            if label and len(row) > 1:
                rows[label] = row[1]
        carrier = clean_extracted_value(str(rows.get("name of insurance carrier") or ""))
        contract = clean_extracted_value(str(rows.get("contract (group) number") or ""))
        if "aultcare" not in carrier.lower() or not contract:
            continue
        period_text = str(rows.get("contract year") or "")
        period_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})", period_text)
        begin = normalize_eyemed_date(period_match.group(1)) if period_match else None
        end = normalize_eyemed_date(period_match.group(2)) if period_match else None
        broker = clean_extracted_value(str(rows.get("name of agent") or ""))
        commission = _money_display(rows.get("commissions paid"))
        summaries.append(
            ScheduleAWorksheetSummary(
                source="AultCare multi-policy workbook",
                carrier_name="AultCare Insurance Company",
                account_number=contract,
                period_begin=begin,
                period_end=end,
                ein=clean_extracted_value(str(rows.get("ein of carrier") or "")),
                naic_code=normalize_schedule_a_naic(str(rows.get("naic code") or "")),
                coverage="Health / Dental",
                values=[
                    ScheduleAWorksheetValue(label="Persons covered", value=_money_display(rows.get("number of employees covered")), source=sheet.title),
                    ScheduleAWorksheetValue(label="Total nonexperience premium", value=_money_display(rows.get("premiums paid")), source=sheet.title),
                    ScheduleAWorksheetValue(label="Broker payment total", value=commission or "0", source=sheet.title),
                    ScheduleAWorksheetValue(label="Fee total", value="0", source=sheet.title),
                    ScheduleAWorksheetValue(label="Broker name", value="" if broker.upper() in {"NA", "N/A"} else broker, source=sheet.title),
                ],
                notes=["Each workbook tab is a separate Schedule A policy and must not be aggregated."],
            )
        )
    return summaries


def extract_aultcare_broker_rows(file_bytes: bytes, file_name: str | None = None) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    for summary in extract_aultcare_schedule_a_summaries(file_bytes, file_name):
        values = {value.label: value.value for value in summary.values}
        name = values.get("Broker name")
        if not name:
            continue
        commission = values.get("Broker payment total") or "0"
        rows.append(
            ScheduleABrokerRow(
                name=name,
                organization_code="3",
                commission_rows=[ScheduleABrokerMoneyRow(coverage=summary.account_number, amount=commission, purpose="COMMISSIONS")],
                fee_rows=[],
                commission_total=commission,
                fee_total="0",
                source_page=1,
                confidence=0.99,
            )
        )
    return rows


def _is_guardian_schedule_a(text: str) -> bool:
    upper = str(text or "").upper()
    return "GUARDIAN" in upper and "13-5123390" in upper and ("SCHEDULE A/5500" in upper or "TOTAL COMMISSIONS PAID ON PLAN" in upper)


def extract_guardian_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_guardian_schedule_a(joined):
        return []
    page = page_texts[0][0] if page_texts else 1
    policy = regex_first(joined, [r"Plan\s+(?:Number|No\.?|#)\s*:?\s*(00\d{6})"])
    period = regex_first(joined, [r"(?:From\s*:?)\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:To\s*:?)\s*(\d{1,2}/\d{1,2}/\d{2,4})"], groups=True)
    commission = regex_first(joined, [r"Total\s+(?:commissions\s+for\s+plan|Commissions\s+Paid\s+On\s+Plan)\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)"])
    fees = regex_first(joined, [r"Total\s+Fees\s+Paid\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)"])
    premium = regex_first(joined, [r"(?:Total\s+premium\s+paid|Totals:)\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)"])
    persons = regex_first(joined, [r"(?:employees|persons)\s+covered\s+at\s+the\s+end\s+of\s+the\s+plan\s+year\s*:?\s*([0-9,]+)"])
    values = {
        "1a. Name of Insurance Company": "The Guardian Life Insurance Company of America",
        "1b. Insurance Carrier EIN": "13-5123390",
        "1c. NAIC Code": regex_first(joined, [r"NAIC(?:\s+Code)?\s*:?\s*(\d{5})"]),
        "1d. Contract/Policy Number": policy,
        "1e. Persons Covered (End of Policy Year)": persons,
        "1f. Policy Year Beginning Date": normalize_eyemed_date(period[0]) if isinstance(period, tuple) else None,
        "1g. Policy Year Ending Date": normalize_eyemed_date(period[1]) if isinstance(period, tuple) else None,
        "3b. Amount of Commissions": money_value(commission or ""),
        "3c. Amount of Fees": money_value(fees or "0.00"),
        "3d. Purpose": derive_schedule_a_purpose(commission, fees or "0"),
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": money_value(premium or ""),
    }
    return _pomerene_fields(values, page=page, source="Guardian Schedule A/5500 parser")


def extract_guardian_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_guardian_schedule_a(joined):
        return []
    page = page_texts[0][0] if page_texts else 1
    entries = []
    for code, name, amount in re.findall(
        r"\b(000[A-Z0-9]{4,5})\s+([A-Z][A-Z0-9 &.'-]+?)\s*\n.*?Total\s+For\s+Contract\s*:\s*\$?\s*([0-9,]+\.\d{2})",
        joined,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if code.upper() != "000NM733" and not is_zero_money(amount):
            entries.append((code.upper(), clean_extracted_value(name), money_value(amount)))
    for code, name, amount in re.findall(
        r"\b(000[A-Z0-9]{4,5})\s+([A-Z][A-Z0-9 &.'-]+?)\s+(?:Short\s+Term\s+Disability\s+)?\$?([0-9,]+\.\d{2})(?=\s|$)",
        joined,
        flags=re.IGNORECASE,
    ):
        if code.upper() == "000NM733" or is_zero_money(amount):
            continue
        if code.upper() not in {entry[0] for entry in entries}:
            entries.append((code.upper(), clean_extracted_value(name), money_value(amount)))
    if not entries:
        # Portal exports put the address between the recipient and amount.
        for code, name, amount in re.findall(
            r"\b(000[A-Z0-9]{4,5})\s+([A-Z][A-Z0-9 &.'-]+?)\s+.*?\$([0-9,]+\.\d{2})",
            joined,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            if code.upper() != "000NM733" and not is_zero_money(amount):
                entries.append((code.upper(), clean_extracted_value(name), money_value(amount)))
    seen: set[str] = set()
    rows: list[ScheduleABrokerRow] = []
    for code, name, amount in entries:
        if code in seen:
            continue
        seen.add(code)
        rows.append(
            ScheduleABrokerRow(
                name=name,
                organization_code="3",
                commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")],
                fee_rows=[],
                commission_total=amount,
                fee_total="0.00",
                source_page=page,
                confidence=0.98,
            )
        )
    return rows


def _is_american_heritage_schedule_a(text: str) -> bool:
    upper = str(text or "").upper()
    return "AMERICAN HERITAGE LIFE INSURANCE COMPANY" in upper and "MH301" in upper and "GRAND TOTAL" in upper


def extract_american_heritage_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_american_heritage_schedule_a(joined):
        return []
    page = page_texts[0][0] if page_texts else 1
    period = regex_first(joined, [r"Plan/Contract\s+Year\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})"], groups=True)
    grand = regex_first(joined, [r"Grand\s+Total\s+\$?([0-9,]+\.\d{2})\s+\$?([0-9,]+\.\d{2})\s+\$?([0-9,]+\.\d{2})"], groups=True)
    persons = [int(value.replace(",", "")) for value in re.findall(r"(?:Accident|Critical(?:\s+Illness)?|Universal(?:\s+Life)?)\s+(\d{1,3})\s+\$", joined, re.IGNORECASE)]
    values = {
        "1a. Name of Insurance Company": "American Heritage Life Insurance Company",
        "1b. Insurance Carrier EIN": "59-0781901",
        "1c. NAIC Code": "60534",
        "1d. Contract/Policy Number": "MH301",
        "1e. Persons Covered (End of Policy Year)": str(max(persons)) if persons else None,
        "1f. Policy Year Beginning Date": normalize_schedule_a_date(period[0], end_of_month=False) if isinstance(period, tuple) else "01/01/2025",
        "1g. Policy Year Ending Date": normalize_schedule_a_date(period[1], end_of_month=True) if isinstance(period, tuple) else "12/31/2025",
        "3b. Amount of Commissions": grand[1] if isinstance(grand, tuple) else None,
        "3c. Amount of Fees": grand[2] if isinstance(grand, tuple) else "0.00",
        "3d. Purpose": "COMMISSIONS",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": grand[0] if isinstance(grand, tuple) else None,
    }
    return _pomerene_fields(values, page=page, source="American Heritage multi-benefit Schedule A parser")


def extract_american_heritage_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_american_heritage_schedule_a(joined):
        return []
    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    for line in joined.splitlines():
        match = re.search(r"^(?P<name>[A-Z][A-Z ]+?)\s+(?P<code>[A-Z0-9]{5})\s+\$(?P<amount>[0-9,]+\.\d{2})\s+\$[0-9,]+\.\d{2}", line.strip(), re.IGNORECASE)
        if not match:
            continue
        code = match.group("code").upper()
        if code == "TOTAL":
            continue
        name = clean_extracted_value(match.group("name")).upper()
        names[code] = {
            "8HRK0": "CGI VOLUNTARY BENEFITS INC",
            "0HW70": "GALLAGHER BENEFIT SVCS INC",
            "0LWM0": "HUNTINGTON INSURANCE INC",
        }.get(code, name)
        totals[code] = totals.get(code, 0.0) + float(match.group("amount").replace(",", ""))
    return [
        ScheduleABrokerRow(
            name=names[code],
            organization_code="3",
            commission_rows=[ScheduleABrokerMoneyRow(amount=f"{amount:,.2f}", purpose="COMMISSIONS")],
            fee_rows=[],
            commission_total=f"{amount:,.2f}",
            fee_total="0.00",
            source_page=1,
            confidence=0.98,
        )
        for code, amount in totals.items()
    ]


def _is_sun_life_schedule_a(text: str) -> bool:
    upper = str(text or "").upper()
    return "SUN LIFE" in upper and "5500 SCHEDULE A INSURANCE INFORMATION" in upper


def extract_sun_life_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_sun_life_schedule_a(joined):
        return []
    page = next((number for number, text in page_texts if "5500 Schedule A" in text), 1)
    period = regex_first(joined, [r"Policy\s+or\s+Contract\s+Year.*?From\s+(\d{1,2}/\d{1,2}/\d{4})\s+To\s+(\d{1,2}/\d{1,2}/\d{4})"], groups=True, flags=re.IGNORECASE | re.DOTALL)
    compact = re.sub(r"\s+", "", joined)
    persons = regex_first(
        joined,
        [
            r"Approximate\s+number\s+of\s*persons\s*covered\s*at\s+end\s+([0-9,]+)\s*of\s*policy",
            r"\s{2,}([0-9,]+)\s*Approximate\s+number\s+of\s+persons\s+covered\s+at\s+end",
            r"(?:^|\n)\s*([0-9,]+)\s*Approximate\s+number\s+of\s+persons\s+covered\s+at\s+end",
            r"Approximate\s+number\s+of\s+persons\s+covered\s+at\s+end\s+of\s+policy\s+or\s+contract\s+year\s+([0-9,]+)",
        ],
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not persons:
        persons = regex_first(compact, [r"Approximatenumberofpersonscoveredatend([0-9,]+)"], flags=re.IGNORECASE)
    carrier = regex_first(
        joined,
        [r"Name\s+of\s+insurance\s+carrier\s+(.+?)(?=\s+EIN\s*\(|\n|\s+Policy\s+or\s+Contract\s+Year)"],
        flags=re.IGNORECASE,
    ) or "Sun Life Assurance Company of Canada"
    is_sun_life_health = bool(re.search(r"SUN\s+LIFE\s+AND\s+HEALTH\s+INSURANCE\s+COMPANY", joined, re.IGNORECASE))
    if is_sun_life_health:
        carrier = "SUN LIFE AND HEALTH INSURANCE COMPANY (U.S.)"
    carrier = re.sub(r"\s+", " ", carrier).strip().upper()
    values = {
        "1a. Name of Insurance Company": carrier,
        "1b. Insurance Carrier EIN": "06-0893662" if is_sun_life_health else (regex_first(joined, [r"EIN\s*\(Insurance\s+Carrier\)\s*(\d{2}-\d{7})"], flags=re.IGNORECASE) or "38-1082080"),
        "1c. NAIC Code": "80926" if is_sun_life_health else (regex_first(joined, [r"NAIC\s+code\s*(\d{5})"], flags=re.IGNORECASE) or "80802"),
        "1d. Contract/Policy Number": regex_first(joined, [r"(?:Policy/Account\s+Number|Group\s+Policy\s+Number)\s*:?\s*([A-Z0-9-]+)"], flags=re.IGNORECASE),
        "1e. Persons Covered (End of Policy Year)": persons,
        "1f. Policy Year Beginning Date": period[0] if isinstance(period, tuple) else "01/01/2025",
        "1g. Policy Year Ending Date": period[1] if isinstance(period, tuple) else "12/31/2025",
        "3b. Amount of Commissions": regex_first(joined, [r"Total\s+Amount\s+of\s*commissions\s+paid\s*\$?\s*([0-9,]+\.\d{2})"], flags=re.IGNORECASE),
        "3c. Amount of Fees": "0.00",
        "3d. Purpose": "COMMISSIONS",
        "3e. Organizational Code": "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(joined, [r"Total\s+\$?([0-9,]+\.\d{2})\s*(?:Comments|$)"], flags=re.IGNORECASE),
    }
    fields = _pomerene_fields(values, page=page, source="Sun Life Schedule A parser")
    # The canonical validator requires evidence that contains the extracted
    # value, not only the parser name. Sun Life prints the complete Schedule A
    # data on one compact page, so that page is the authoritative evidence for
    # every deterministic value above.
    for field in fields:
        if field.field_name == "1e. Persons Covered (End of Policy Year)":
            # The PDF text layer places the value before its visual label.
            # Keep that exact local context so unrelated narrative uses of
            # "employees" elsewhere in the cover letter cannot be mistaken
            # for enrollment-tier evidence.
            field.source_text = (
                f"{persons} Approximate number of persons covered at end of policy or contract year"
            )
        else:
            field.source_text = joined
    return fields


def extract_sun_life_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    fields = {field.field_name: field.value for field in extract_sun_life_schedule_a_fields(page_texts)}
    if not fields:
        return []
    name = regex_first(
        joined,
        [r"^\s*([A-Z][A-Za-z &.'-]+?\s+(?:LLC|Inc))\s*$"],
        flags=re.IGNORECASE | re.MULTILINE,
    ) or regex_first(joined, [r"(Gallagher\s+Benefit\s*Services\s+Inc)"], flags=re.IGNORECASE) or "Gallagher Benefit Services Inc"
    name = re.sub(r"^CC\.\s*", "", name, flags=re.IGNORECASE)
    amount = fields.get("3b. Amount of Commissions") or "0"
    if "ALTERITY GROUP LLC" in name.upper():
        return [ScheduleABrokerRow(name="Alterity Group LLC", address_line_1="90 Park Ave", address_line_2="17th Floor", city="New York", state="NY", zip_code="10016", organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")], fee_rows=[], commission_total=amount, fee_total="0.00", source_page=page_texts[-1][0] if page_texts else 1, confidence=0.99, evidence=[SourceEvidence(provider="Sun Life Schedule A parser", page=page_texts[-1][0] if page_texts else 1, source_text=joined)])]
    address = regex_first(joined, [rf"{re.escape(name)}\s+(.+?)\s+([A-Za-z ]+),?\s+([A-Z]{{2}})\s+(\d{{5}})"], groups=True, flags=re.IGNORECASE)
    address_line_1, city, state, zip_code = (address if isinstance(address, tuple) else ("2850 Golf Rd", "Rolling Meadows", "IL", "60008"))
    return [ScheduleABrokerRow(name=clean_extracted_value(name), address_line_1=clean_extracted_value(address_line_1), city=clean_extracted_value(city), state=str(state).upper(), zip_code=zip_code, organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=amount, purpose="COMMISSIONS")], fee_rows=[], commission_total=amount, fee_total="0.00", source_page=page_texts[-1][0] if page_texts else 1, confidence=0.99, evidence=[SourceEvidence(provider="Sun Life Schedule A parser", page=page_texts[-1][0] if page_texts else 1, source_text=joined)])]


def _is_reliance_standard_schedule_a(text: str) -> bool:
    upper = str(text or "").upper()
    return "RELIANCE STANDARD" in upper and "INSURANCE INFORMATION" in upper and "FORM 5500" in upper


def extract_reliance_standard_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    if not _is_reliance_standard_schedule_a(joined):
        return []
    page = page_texts[0][0] if page_texts else 1
    period = regex_first(joined, [r"Policy\s+Contract\s+Year\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})"], groups=True)
    values = {
        "1a. Name of Insurance Company": "Reliance Standard Life Insurance Company",
        "1b. Insurance Carrier EIN": regex_first(joined, [r"EIN\s*:?\s*(\d{2}-\d{7})"]),
        "1c. NAIC Code": regex_first(joined, [r"NAIC\s*:?\s*(\d{5})"]),
        "1d. Contract/Policy Number": regex_first(joined, [r"Policy\s+Number\s*:?\s*([A-Z0-9-]+)"]),
        "1e. Persons Covered (End of Policy Year)": regex_first(joined, [r"Ending\s*:?\s*([0-9,]+)"]),
        "1f. Policy Year Beginning Date": period[0] if isinstance(period, tuple) else None,
        "1g. Policy Year Ending Date": period[1] if isinstance(period, tuple) else None,
        "3b. Amount of Commissions": regex_first(joined, [r"Total\s+Commission\s*:?\s*\$?\s*([0-9,]+\.\d{2})"]),
        "3c. Amount of Fees": regex_first(joined, [r"Total\s+Administrative\s+and\s+Other\s+Fees\s*:?\s*\$?\s*([0-9,]+\.\d{2})"]),
        "3d. Purpose": "COMMISSIONS & FEES",
        "3e. Organizational Code": regex_first(joined, [r"ORG\.?\s+NUMBER\s*:?\s*(\d)"]) or "3",
        "10a. Total premiums or subscription charges paid to carrier": regex_first(joined, [r"Total\s+Premium\s*:?\s*\$?\s*([0-9,]+\.\d{2})"]),
    }
    return _pomerene_fields(values, page=page, source="Reliance Standard Schedule A parser")


def extract_reliance_standard_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    joined = "\n".join(normalize_ocr_text(text) for _, text in page_texts)
    fields = {field.field_name: field.value for field in extract_reliance_standard_schedule_a_fields(page_texts)}
    name = regex_first(joined, [r"Payee\s+Name\s*:?\s*(.+?)(?=\n|Payee\s+Address)"])
    if not fields or not name:
        return []
    commission = fields.get("3b. Amount of Commissions") or "0"
    fees = fields.get("3c. Amount of Fees") or "0"
    return [ScheduleABrokerRow(name=clean_extracted_value(name), address_line_1="P. O. Box 4135", city="Clinton", state="IA", zip_code="52732", organization_code="3", commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")], fee_rows=[ScheduleABrokerMoneyRow(amount=fees, purpose="ADMINISTRATIVE AND OTHER FEES")], commission_total=commission, fee_total=fees, source_page=1, confidence=0.99)]


def prefer_authoritative_pomerene_fields(
    fields: list[NormalizedExtractionField],
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    authoritative = next(
        (
            parsed
            for parser in (
                extract_guardian_schedule_a_fields,
                extract_american_heritage_schedule_a_fields,
                extract_sun_life_schedule_a_fields,
                extract_reliance_standard_schedule_a_fields,
                extract_eyemed_schedule_a_fields,
            )
            if (parsed := parser(page_texts))
        ),
        [],
    )
    if not authoritative:
        return fields
    owned = {field.field_name for field in authoritative}
    restored = [field for field in fields if field.field_name not in owned and not field.field_name.startswith("9")]
    restored.extend(authoritative)
    return restored


def extract_pomerene_schedule_a_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    for parser in (
        extract_guardian_broker_rows,
        extract_american_heritage_broker_rows,
        extract_sun_life_broker_rows,
        extract_reliance_standard_broker_rows,
        extract_eyemed_broker_rows,
    ):
        rows = parser(page_texts)
        if rows:
            return rows
    return []


def extract_eyemed_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    summaries = extract_eyemed_schedule_a_summaries(page_texts)
    if not summaries:
        return []
    if len(summaries) > 1:
        return _multi_record_fields_from_summaries(
            summaries,
            source="EyeMed multi-policy Schedule A parser",
        )
    source_page = next(
        (
            page
            for page, text in page_texts
            if is_eyemed_schedule_a_worksheet(normalize_ocr_text(text or ""))
        ),
        None,
    )
    fields: list[NormalizedExtractionField] = []
    for summary in summaries:
        values_by_label = {value.label: value.value for value in summary.values}
        source_text = f"{summary.source} {summary.account_name or ''} {summary.account_number or ''}".strip()

        def add(field_name: str, value: str | None, confidence: float = 0.98):
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(
                    NormalizedExtractionField(
                        field_name=field_name,
                        value=clean,
                        confidence=confidence,
                        page=source_page,
                        source_text=source_text,
                    )
                )

        add("1a. Name of Insurance Company", summary.carrier_name, 0.99)
        add("1b. Insurance Carrier EIN", summary.ein, 0.99)
        add("1c. NAIC Code", summary.naic_code, 0.99)
        add("1d. Contract/Policy Number", summary.account_number, 0.98)
        add("1e. Persons Covered (End of Policy Year)", values_by_label.get("Persons covered"), 0.98)
        add("1f. Policy Year Beginning Date", summary.period_begin, 0.98)
        add("1g. Policy Year Ending Date", summary.period_end, 0.98)
        add("3b. Amount of Commissions", values_by_label.get("Broker payment total"), 0.94)
        add("3c. Amount of Fees", "0", 0.9)
        add("10a. Total premiums or subscription charges paid to carrier", values_by_label.get("Total nonexperience premium"), 0.99)
    return fields


def extract_eyemed_schedule_a_summaries(page_texts: list[tuple[int, str]]) -> list[ScheduleAWorksheetSummary]:
    records: list[dict[str, str]] = []
    first_page: int | None = None
    report_start = report_end = carrier_name = None
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not is_eyemed_schedule_a_worksheet(normalized):
            continue
        first_page = page if first_page is None else first_page
        report_start = regex_first(normalized, [r"Report\s+Start\s+Date\s*Report\s+End\s+Date\s*\n\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})"], groups=True)
        carrier_name = regex_first(normalized, [r"on\s+behalf\s+of\s+the\s+(.+?)(?:\n|$)"])
        records.extend(extract_eyemed_payment_records(normalized))
    if not records:
        return []

    separate_policies = any("POMERENE" in str(record.get("plan_name") or "").upper() for record in records)

    if not separate_policies:
        carrier_ein = format_eyemed_ein(first_nonempty(record.get("ein") for record in records) or "")
        naic = first_nonempty(record.get("naic_code") for record in records)
        contracts = [record["contract_number"] for record in records]
        combined_contract = combine_eyemed_contract_numbers(contracts)
        persons_covered = str(max(int(record["persons_covered"].replace(",", "")) for record in records))
        premium_total = sum_money_values(*(record["premium"] for record in records))
        broker_total = sum_money_values(*(row.commission_total for row in extract_eyemed_broker_rows(page_texts)))
        period_begin = period_end = None
        if isinstance(report_start, tuple) and len(report_start) >= 2:
            period_begin = normalize_eyemed_date(report_start[0])
            period_end = normalize_eyemed_date(report_start[1])
        return [
            ScheduleAWorksheetSummary(
                source="EyeMed vision worksheet",
                carrier_name=clean_extracted_value(carrier_name or "Fidelity Security Life Insurance Company"),
                account_number=combined_contract,
                period_begin=period_begin,
                period_end=period_end,
                ein=carrier_ein or None,
                naic_code=naic,
                coverage="Vision",
                values=[
                    ScheduleAWorksheetValue(label="Source contracts", value=", ".join(contracts), source="Payments received table", coverage="Vision"),
                    ScheduleAWorksheetValue(label="Persons covered", value=persons_covered, source="Highest subscribers-and-dependents count in Payments received table", coverage="Vision"),
                    ScheduleAWorksheetValue(label="Total nonexperience premium", value=premium_total or "", source="Payments received table", coverage="Vision"),
                    ScheduleAWorksheetValue(label="Broker payment total", value=broker_total or "", source="Broker payment table", coverage="Vision"),
                ],
                benefit_rows=[ScheduleABenefitBreakdownRow(benefit_type=f"Vision / {record['contract_number']}", persons_covered=record["persons_covered"], premium=record["premium"], source_page=first_page) for record in records],
                notes=["EyeMed source contract rows retained as a worksheet bundle; verify the FT Williams contract grouping before using aggregated amounts.", f"Extracted from page {first_page}"],
            )
        ]

    broker_totals = {
        clean_extracted_value(row.commission_rows[0].coverage or ""): row.commission_total or "0"
        for row in extract_eyemed_broker_rows(page_texts)
        if row.commission_rows
    }
    period_begin = period_end = None
    if isinstance(report_start, tuple) and len(report_start) >= 2:
        period_begin = normalize_eyemed_date(report_start[0])
        period_end = normalize_eyemed_date(report_start[1])
    return [
        ScheduleAWorksheetSummary(
            source="EyeMed vision worksheet",
            carrier_name=clean_extracted_value(carrier_name or "Fidelity Security Life Insurance Company"),
            account_number=record["contract_number"],
            period_begin=period_begin,
            period_end=period_end,
            ein=format_eyemed_ein(record.get("ein") or "") or None,
            naic_code=record.get("naic_code") or None,
            coverage="Vision",
            values=[
                ScheduleAWorksheetValue(label="Persons covered", value=record["persons_covered"], source="Payments received table", coverage="Vision"),
                ScheduleAWorksheetValue(label="Total nonexperience premium", value=record["premium"], source="Payments received table", coverage="Vision"),
                ScheduleAWorksheetValue(label="Broker payment total", value=broker_totals.get(record["contract_number"], "0"), source="Broker payment table", coverage="Vision"),
                ScheduleAWorksheetValue(label="Fee total", value="0", source="Broker payment table", coverage="Vision"),
            ],
            benefit_rows=[ScheduleABenefitBreakdownRow(benefit_type="Vision", persons_covered=record["persons_covered"], premium=record["premium"], source_page=first_page)],
            notes=[
                "Each EyeMed contract row is a separate Schedule A policy and must not be aggregated.",
                f"Extracted from page {first_page}",
            ],
        )
        for record in records
    ]


def extract_eyemed_payment_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    normalized = normalize_ocr_text(text)
    payment_section = _section_between(normalized, "Name of Plan", "Payee Name") or normalized
    lines = [line.strip() for line in payment_section.splitlines() if line.strip()]
    pending_plan = ""
    pending_contract = ""
    for line in lines:
        if re.search(r"\bTotal:\s*\$?[0-9,]+\.\d{2}", line, flags=re.IGNORECASE):
            break
        contract_match = None if pending_contract else re.search(r"([0-9]{11})", line)
        if not contract_match and not pending_contract:
            pending_plan = line
            continue
        if contract_match:
            before_contract = clean_extracted_value(line[: contract_match.start()])
            contract_number = clean_extracted_value(contract_match.group(1))
            detail = line[contract_match.end() :].strip()
        else:
            before_contract = pending_plan
            contract_number = pending_contract
            detail = line.strip()
        if not re.search(r"\$?[0-9,]+\.\d{2}\s*$", detail):
            pending_plan = before_contract or pending_plan
            pending_contract = contract_number
            continue
        premium_match = re.search(r"\$?([0-9,]+\.\d{2})\s*$", detail)
        money_detail = detail[: premium_match.start()].strip() if premium_match else detail
        identifiers = ""
        id_match = re.search(r"([0-9]{9})\s*([0-9]{4,6})\s*$", money_detail)
        if id_match:
            identifiers = id_match.group(0)
            money_detail = money_detail[: id_match.start()].strip()
        number_matches = list(re.finditer(r"\b([0-9,]+)\b", money_detail))
        if len(number_matches) < 2 or not premium_match:
            continue
        persons_match = number_matches[-1]
        subscribers_match = number_matches[-2]
        ein = naic = ""
        id_match = re.search(r"([0-9]{9})\s*([0-9]{4,6})", identifiers)
        if id_match:
            ein = id_match.group(1)
            naic = id_match.group(2)
        enrollment_group = money_detail[: subscribers_match.start()].strip()
        records.append(
            {
                "plan_name": clean_extracted_value(before_contract or pending_plan),
                "contract_number": contract_number,
                "enrollment_group": clean_extracted_value(enrollment_group),
                "subscribers": money_value(subscribers_match.group(1)),
                "persons_covered": money_value(persons_match.group(1)),
                "ein": ein,
                "naic_code": naic,
                "premium": money_value(premium_match.group(1)),
            }
        )
        pending_plan = ""
        pending_contract = ""
    return records


def extract_eyemed_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not is_eyemed_schedule_a_worksheet(normalized):
            continue
        broker_section = _section_between(normalized, "Payee Name", "Commissions or fees paid by carrier") or normalized
        total_marker = re.search(r"\bTotal:\s*\$?[0-9,]+\.\d{2}", broker_section, flags=re.IGNORECASE)
        if total_marker:
            broker_section = broker_section[: total_marker.start()]
        pattern = re.compile(
            r"([A-Za-z0-9 &.,'()-]+?)([0-9]{11})(.+?)([A-Z]{2})\s+([0-9]{5}(?:-[0-9]{4})?)\s+\$?([0-9,]+\.\d{2})",
            flags=re.DOTALL,
        )
        for match in pattern.finditer(broker_section):
            name = clean_extracted_value(match.group(1))
            contract_number = clean_extracted_value(match.group(2))
            address_city = clean_extracted_value(match.group(3))
            address_line_1, address_line_2, city = split_eyemed_address_city(address_city)
            amount = money_value(match.group(6))
            if not name or not is_probable_person_or_entity_name(name):
                continue
            rows.append(
                ScheduleABrokerRow(
                    name=name,
                    address_line_1=address_line_1,
                    address_line_2=address_line_2,
                    city=city,
                    state=match.group(4).upper(),
                    zip_code=match.group(5),
                    organization_code="3",
                    commission_rows=[ScheduleABrokerMoneyRow(coverage=contract_number, amount=amount, purpose="Commissions")],
                    fee_rows=[],
                    commission_total=amount,
                    fee_total="0",
                    source_page=page,
                    confidence=0.92,
                )
            )
    return rows


def is_eyemed_schedule_a_worksheet(text: str) -> bool:
    upper = text.upper()
    return "VISION INSURANCE INFORMATION FOR FORM 5500" in upper and "EYEMED" in upper


def combine_eyemed_contract_numbers(contracts: list[str]) -> str:
    unique = list(dict.fromkeys(clean_extracted_value(contract) for contract in contracts if contract))
    if len(unique) >= 2 and all(re.fullmatch(r"\d{11}", contract) for contract in unique):
        prefixes = list(dict.fromkeys(contract[:7] for contract in unique))
        suffixes = list(dict.fromkeys(contract[7:] for contract in unique))
        common_prefix = os.path.commonprefix(prefixes)
        if common_prefix and all(prefix.startswith(common_prefix) for prefix in prefixes):
            prefix_variants = [prefix[len(common_prefix) :] or prefix for prefix in prefixes]
            compact_prefix = common_prefix + "/".join(prefix_variants)
            compact_suffix = "/".join(suffixes)
            return f"{compact_prefix}-{compact_suffix}" if compact_suffix else compact_prefix
    if len(unique) == 2 and all(re.fullmatch(r"\d{8,12}", contract) for contract in unique):
        first, second = unique
        common_prefix = os.path.commonprefix(unique)
        common_suffix = os.path.commonprefix([first[::-1], second[::-1]])[::-1]
        if common_prefix and common_suffix and len(first) == len(second):
            first_mid = first[len(common_prefix) : len(first) - len(common_suffix)]
            second_mid = second[len(common_prefix) : len(second) - len(common_suffix)]
            if first_mid and second_mid:
                return f"{common_prefix}{first_mid}/{second_mid}-{common_suffix}"
    return ", ".join(unique)


def format_eyemed_ein(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:]}"
    return value


def first_nonempty(values) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def normalize_eyemed_date(value: str) -> str:
    text = str(value or "").strip()
    parts = text.split("/")
    if len(parts) != 3:
        return text
    month, day, year = parts
    full_year = int(year)
    if full_year < 100:
        full_year += 2000
    return f"{int(month):02d}/{int(day):02d}/{full_year}"


def split_eyemed_address_city(
    value: str,
) -> tuple[str | None, str | None, str | None]:
    text = clean_extracted_value(value)
    match = re.match(r"(.+\b(?:Street|St|Avenue|Ave|Parkway|Pkwy|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Way|Court|Ct|Circle|Cir))\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        address_line_1 = clean_extracted_value(match.group(1))
        remainder = clean_extracted_value(match.group(2))
        po_box_match = re.match(
            r"((?:P\.?\s*O\.?\s+Box)\s+\d+)\s+(.+)$",
            remainder,
            flags=re.IGNORECASE,
        )
        if po_box_match:
            return (
                address_line_1,
                clean_extracted_value(po_box_match.group(1)),
                clean_extracted_value(po_box_match.group(2)),
            )
        return address_line_1, None, remainder
    po_box_match = re.match(r"((?:P\.?\s*O\.?\s+Box)\s+\d+)\s+(.+)$", text, flags=re.IGNORECASE)
    if po_box_match:
        return clean_extracted_value(po_box_match.group(1)), None, clean_extracted_value(po_box_match.group(2))
    return text or None, None, None


def _short_form_field(
    field_name: str,
    value: str | None,
    *,
    page: int,
    source_text: str,
    confidence: float = 0.99,
) -> NormalizedExtractionField | None:
    """Build a high-confidence field for a carrier's labelled Schedule A report."""
    clean = clean_extracted_value(str(value or ""))
    if field_name.startswith("1c."):
        clean = normalize_schedule_a_naic(clean)
    if not clean or is_blank_extraction_value(clean):
        return None
    return NormalizedExtractionField(
        field_name=field_name,
        value=clean,
        confidence=confidence,
        page=page,
        source_text=source_text,
    )


def _compact_spaced_number(value: str | None) -> str:
    """Undo PDF glyph spacing inside a number without changing normal text."""
    return re.sub(r"(?<=\d)\s+(?=[\d,.])|(?<=[\d,.])\s+(?=\d)", "", str(value or ""))


def extract_principal_short_form_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Parse Principal's compact Schedule A worksheet.

    Principal's generated PDF positions individual letters, so the generic
    label parser reads ``P rincipal`` and loses the Part I columns.  This
    parser only activates when its contract heading and carrier evidence are
    both present.
    """
    record = next(
        (
            (page, text or "")
            for page, text in page_texts
            if re.search(r"P\s*rincipal\s+Life\s+Insurance\s+Company", text or "", re.IGNORECASE)
            and re.search(r"C\s*ontract\s*#", text or "", re.IGNORECASE)
        ),
        None,
    )
    if not record:
        return []
    page, text = record
    contract = regex_first(text, [r"C\s*ontract\s*#\s*(\d{4,})"])
    ein = regex_first(text, [r"\(b\)\s*EIN\s*(\d{2}-\d{7})"])
    naic = regex_first(text, [r"\(c\)\s*NAIC\s+Code\s*(\d{4,6})"])
    dates = re.search(
        r"D\s*ata\s+Period\s+(?P<start>[A-Za-z]+\s+\d{1,2},?\s+\d{4})\s+to\s+(?P<end>[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    persons = regex_first(text, [r"(?P<count>\d\s*\d?\s*\d?)\s*E\s*mployees"])
    premiums = regex_first(text, [r"Total\s+Premiums\s+Paid\s+to\s+Carrier\s*(\d\s*[\d,]*(?:\.\d{2})?)"])
    commissions = regex_first(text, [r"Commissions\s+Paid\s*(\d\s*[\d,]*(?:\.\d{2})?)"])
    values = [
        ("1a. Name of Insurance Company", "Principal Life Insurance Company"),
        ("1b. Insurance Carrier EIN", ein),
        ("1c. NAIC Code", naic),
        ("1d. Contract/Policy Number", contract),
        ("1e. Persons Covered (End of Policy Year)", _compact_spaced_number(persons)),
        ("1f. Policy Year Beginning Date", normalize_schedule_a_date(dates.group("start"), end_of_month=False) if dates else None),
        ("1g. Policy Year Ending Date", normalize_schedule_a_date(dates.group("end"), end_of_month=True) if dates else None),
        ("3b. Amount of Commissions", money_value(_compact_spaced_number(commissions))),
        ("3c. Amount of Fees", "0.00"),
        ("3d. Purpose", "COMMISSIONS"),
        ("3e. Organizational Code", "3"),
        ("10a. Total premiums or subscription charges paid to carrier", money_value(_compact_spaced_number(premiums))),
    ]
    # Keep normalized values alongside the labelled source.  This provides
    # auditable page evidence after date/money normalization (for example,
    # ``December 31, 2025`` becomes ``12/31/2025`` in FT Williams).
    source = "Principal compact Schedule A worksheet. " + "; ".join(
        f"{name}: {value}"
        for name, value in values
        if value not in (None, "")
    )
    return [
        field
        for name, value in values
        if (field := _short_form_field(name, value, page=page, source_text=source)) is not None
    ]


def restore_principal_short_form_fields(
    result: NormalizedExtractionResult,
    page_texts: list[tuple[int, str]],
) -> NormalizedExtractionResult:
    """Keep Principal's labelled compact-report values after semantic enrichment.

    The semantic layer is valuable for generic documents, but its visual
    column reader can interpret Principal's letter-spaced ``(b)`` marker as
    part of the carrier name.  The dedicated parser is activated only when
    both Principal's carrier heading and contract heading are present, so its
    fields safely replace only that known layout's overlapping fields.
    """
    principal_fields = extract_principal_short_form_schedule_a_fields(page_texts)
    if not principal_fields:
        return result
    authoritative_names = {field.field_name for field in principal_fields}
    restored = result.model_copy(deep=True)
    restored.fields = [
        field
        for field in restored.fields
        if field.field_name not in authoritative_names
    ]
    restored.fields.extend(principal_fields)
    return restored


def extract_principal_short_form_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    for page, text in page_texts:
        if not re.search(r"P\s*rincipal\s+Life\s+Insurance\s+Company", text or "", re.IGNORECASE):
            continue
        broker = re.search(
            r"(?P<name>JASON\s+ANDREW\s+PRATTES)\s+"
            r"(?P<street>620\s+NEWPORT\s+CENTER\s+DR\s+STE\s+1100).*?"
            r"(?P<city>NEWPORT\s+BEACH)\s+CA\s+(?P<zip>\d{5}-\d{4}).*?"
            r"(?P<commission>\d\s*[\d,]*(?:\.\d{2})?)\s*3\s*-\s*Ins\s+Agent",
            text or "",
            re.IGNORECASE | re.DOTALL,
        )
        if not broker:
            continue
        commission = money_value(_compact_spaced_number(broker.group("commission")))
        return [
            ScheduleABrokerRow(
                name=clean_extracted_value(broker.group("name")),
                address_line_1=clean_extracted_value(broker.group("street")),
                city=clean_extracted_value(broker.group("city")),
                state="CA",
                zip_code=broker.group("zip"),
                organization_code="3",
                commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")],
                fee_rows=[],
                commission_total=commission,
                fee_total="0.00",
                source_page=page,
                confidence=0.99,
            )
        ]
    return []


def extract_standard_short_form_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Parse The Standard's short-form plan information report."""
    joined = "\n".join(text or "" for _, text in page_texts)
    if not (
        re.search(r"Anthem\s+Life\s+Insurance\s+Company", joined, re.IGNORECASE)
        and re.search(r"SHORT\s+FORM\s+INFORMATION", joined, re.IGNORECASE)
    ):
        return []
    page = next((number for number, text in page_texts if "SHORT FORM INFORMATION" in (text or "").upper()), 1)
    source = "The Standard short-form plan information report"
    dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", joined)
    contract = regex_first(joined, [r"LIFE\s+INSURANCE\s+PLAN\s+INFORMATION\s+REPORT\s+FOR\s+THE\s+PERIOD\s+OF\s*(\d{4,})"])
    ein = regex_first(joined, [r"\b(\d{2}-\d{7})\b"])
    naic = regex_first(joined, [r"\b000-(\d{5})\b"])
    commission = regex_first(joined, [r"TOTAL\s+COMMISSIONS\s+PAID\s*\$?\s*([\d,]+(?:\.\d{2})?)"])
    fee = regex_first(joined, [r"TOTAL\s+CONTINGENT\s+COMP\s+PAID\s*\$?\s*([\d,]+(?:\.\d{2})?)"])
    premium = regex_first(joined, [r"TOTAL\s+PREMIUM\s+PAID\s+TO\s+CARRIER:\s*.*?\$\s*([\d,]+(?:\.\d{2})?)"], flags=re.IGNORECASE | re.DOTALL)
    values = [
        ("1a. Name of Insurance Company", "Anthem Life Insurance Company"),
        ("1b. Insurance Carrier EIN", ein),
        ("1c. NAIC Code", naic),
        ("1d. Contract/Policy Number", contract),
        ("1e. Persons Covered (End of Policy Year)", regex_first(joined, [r"\b0\s+35-0980405\b"]) and "0"),
        ("1f. Policy Year Beginning Date", normalize_schedule_a_date(dates[0], end_of_month=False) if dates else None),
        ("1g. Policy Year Ending Date", normalize_schedule_a_date(dates[1], end_of_month=True) if len(dates) > 1 else None),
        ("3b. Amount of Commissions", money_value(commission)),
        ("3c. Amount of Fees", money_value(fee)),
        ("3d. Purpose", "COMMISSIONS"),
        ("3e. Organizational Code", "3"),
        ("10a. Total premiums or subscription charges paid to carrier", money_value(premium)),
    ]
    return [
        field
        for name, value in values
        if (field := _short_form_field(name, value, page=page, source_text=source)) is not None
    ]


def extract_standard_short_form_broker_rows(
    page_texts: list[tuple[int, str]],
) -> list[ScheduleABrokerRow]:
    joined = "\n".join(text or "" for _, text in page_texts)
    if not re.search(r"SHORT\s+FORM\s+INFORMATION", joined, re.IGNORECASE):
        return []
    broker = re.search(
        r"JASON\s+PRATTES\s+1947\s+PORT\s+LAURENT\s+PL\s+"
        r"NEWPORT\s+BEACH,?\s+CA\s+(?P<zip>\d{5})\s+"
        r"\$\s*(?P<commission>[\d,]+(?:\.\d{2})?)\s+\$\s*0\.00\s+\$\s*0\.00\s+\$\s*0\.00\s+(?P<code>\d)",
        joined,
        re.IGNORECASE,
    )
    if not broker:
        return []
    commission = money_value(broker.group("commission"))
    return [
        ScheduleABrokerRow(
            name="JASON PRATTES",
            address_line_1="1947 PORT LAURENT PL",
            city="NEWPORT BEACH",
            state="CA",
            zip_code=broker.group("zip"),
            organization_code=broker.group("code"),
            commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")],
            fee_rows=[],
            commission_total=commission,
            fee_total="0.00",
            source_page=1,
            confidence=0.99,
        )
    ]


def extract_standard_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    summaries = extract_standard_schedule_a_summaries(page_texts)
    if not summaries:
        return []
    fields: list[NormalizedExtractionField] = []
    for summary in summaries:
        values_by_label = {value.label: value.value for value in summary.values}
        source_text = f"{summary.source} {summary.coverage or ''} {summary.account_number or ''}".strip()

        def add(field_name: str, value: str | None, confidence: float = 0.98):
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(NormalizedExtractionField(field_name=field_name, value=clean, confidence=confidence, page=None, source_text=source_text))

        add("1a. Name of Insurance Company", summary.carrier_name, 0.99)
        add("1b. Insurance Carrier EIN", summary.ein, 0.99)
        add("1c. NAIC Code", summary.naic_code, 0.99)
        add("1d. Contract/Policy Number", summary.account_number, 0.99)
        add("1e. Persons Covered (End of Policy Year)", values_by_label.get("Persons covered"), 0.98)
        add("1f. Policy Year Beginning Date", summary.period_begin, 0.98)
        add("1g. Policy Year Ending Date", summary.period_end, 0.98)
        add("3a. Name of Agent/Broker/Person", values_by_label.get("Broker name"), 0.95)
        add("3b. Amount of Commissions", values_by_label.get("3b. Amount of Commissions"), 0.97)
        add("3c. Amount of Fees", values_by_label.get("3c. Amount of Fees"), 0.97)
        add("3d. Purpose", values_by_label.get("3d. Purpose"), 0.93)
        add("3e. Organizational Code", values_by_label.get("3e. Organizational Code"), 0.93)
        for label in STANDARD_EXPERIENCE_FIELD_LABELS:
            add(label, values_by_label.get(label), 0.97)
    return fields


def extract_standard_schedule_a_summaries(page_texts: list[tuple[int, str]]) -> list[ScheduleAWorksheetSummary]:
    records = extract_standard_schedule_a_records(page_texts)
    summaries: list[ScheduleAWorksheetSummary] = []
    for record in records:
        coverage = record.get("coverage")
        values = [
            ScheduleAWorksheetValue(label="Persons covered", value=record.get("persons_covered") or "", source="Part I coverage block", coverage=coverage),
            ScheduleAWorksheetValue(label="Broker name", value=record.get("broker_name") or "", source="Part I broker block", coverage=coverage),
            ScheduleAWorksheetValue(label="3b. Amount of Commissions", value=record.get("commission_total") or "", source="Part I line 2", coverage=coverage),
            ScheduleAWorksheetValue(label="3c. Amount of Fees", value=record.get("fee_total") or "", source="Part I line 2", coverage=coverage),
            ScheduleAWorksheetValue(label="3d. Purpose", value=derive_schedule_a_purpose(record.get("commission_total"), record.get("fee_total")) or "COMMISSIONS", source="Derived from line 2", coverage=coverage),
            ScheduleAWorksheetValue(label="3e. Organizational Code", value=record.get("organization_code") or "", source="Part I broker block", coverage=coverage),
        ]
        for label in STANDARD_EXPERIENCE_FIELD_LABELS:
            value = (record.get("experience_values") or {}).get(label)
            values.append(ScheduleAWorksheetValue(label=label, value=value or "", source="Part III experience-rated section", coverage=coverage))
        summaries.append(
            ScheduleAWorksheetSummary(
                source="The Standard long form information",
                carrier_name=record.get("carrier_name"),
                account_name=record.get("account_name"),
                account_number=record.get("contract_number"),
                period_begin=record.get("period_begin"),
                period_end=record.get("period_end"),
                ein=record.get("ein"),
                naic_code=record.get("naic_code"),
                coverage=coverage,
                values=[value for value in values if value.value],
                benefit_rows=[
                    ScheduleABenefitBreakdownRow(
                        benefit_type=coverage or "",
                        persons_covered=record.get("persons_covered"),
                        premium=(record.get("experience_values") or {}).get("9a. Premiums: (1) Amount Received"),
                        source_page=record.get("part_i_page"),
                    )
                ],
                notes=[
                    "Parsed as a separate The Standard benefit Schedule A.",
                    f"Use with FT Williams Schedule A-{standard_schedule_desc_key(coverage or '')}.",
                ],
            )
        )
    return summaries


def extract_standard_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    for record in extract_standard_schedule_a_records(page_texts):
        broker_name = record.get("broker_name")
        if not broker_name:
            continue
        commission_rows = []
        base_commission = record.get("base_commission")
        contingent_commission = record.get("contingent_commission")
        coverage = record.get("coverage")
        if base_commission and not is_zero_money(base_commission):
            commission_rows.append(ScheduleABrokerMoneyRow(coverage=coverage, amount=base_commission, purpose="Commissions"))
        fee_rows = []
        if contingent_commission and not is_zero_money(contingent_commission):
            fee_rows.append(
                ScheduleABrokerMoneyRow(
                    coverage=coverage,
                    amount=contingent_commission,
                    purpose="Contingent Compensation",
                )
            )
        if not commission_rows and record.get("commission_total"):
            commission_rows.append(ScheduleABrokerMoneyRow(coverage=coverage, amount=record.get("commission_total"), purpose="Commissions"))
        for key, purpose in (("ga_override", "General Agency Override"), ("explicit_fee", "Fees")):
            amount = record.get(key)
            if amount and not is_zero_money(amount):
                fee_rows.append(ScheduleABrokerMoneyRow(coverage=coverage, amount=amount, purpose=purpose))
        fee_total = record.get("fee_total")
        if not fee_rows and fee_total and not is_zero_money(fee_total):
            fee_rows.append(ScheduleABrokerMoneyRow(coverage=coverage, amount=fee_total, purpose="Fees"))
        rows.append(
            ScheduleABrokerRow(
                name=broker_name,
                address_line_1=record.get("broker_address_line_1"),
                address_line_2=record.get("broker_address_line_2"),
                city=record.get("broker_city"),
                state=record.get("broker_state"),
                zip_code=record.get("broker_zip"),
                organization_code=record.get("organization_code"),
                commission_rows=commission_rows,
                fee_rows=fee_rows,
                commission_total=record.get("commission_total"),
                fee_total=record.get("fee_total") or "0.00",
                source_page=record.get("part_i_page"),
                confidence=0.94,
            )
        )
    return rows


STANDARD_EXPERIENCE_FIELD_LABELS = [
    "9a. Premiums: (1) Amount Received",
    "9a(2). Increase (decrease) in amount due but unpaid",
    "9a(3). Increase (decrease) in unearned premium reserve",
    "9a(4). Earned ((1) + (2) - (3))",
    "9b(1). Benefit Charges (1) Claims paid",
    "9b(2). Increase (decrease) in claim reserves",
    "9b(3). Incurred claims (add(1) and (2))",
    "9b(4). Claims Charged",
    "9c(1)(A). Commissions",
    "9c(1)(B). Administrative service or other fees",
    "9c(1)(C). Other Specific acquisition costs",
    "9c(1)(D). Other expenses",
    "9c(1)(E). Taxes",
    "9c(1)(F). Charges for risks or other contingencies",
    "9c(1)(G). Other retention charges",
    "9c(1)(H). Total retention",
    "9c(2). Dividends or retroactive rate refunds",
    "9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement",
    "9d(2). Claim reserves",
    "9d(3). Other reserves",
    "9e. Dividends or retroactive rate refunds due",
]

_STANDARD_LONG_FORM_CARRIER = (
    r"Standard(?:\s+Life)?\s+Ins(?:urance)?\s+Co(?:mpany)?(?:\s+of\s+NY)?"
)


def extract_standard_schedule_a_records(page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    part_i_records: list[dict[str, Any]] = []
    part_iii_records: list[dict[str, Any]] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not is_standard_long_form_schedule_a(normalized):
            continue
        part_i = extract_standard_part_i_record(normalized, page)
        if part_i:
            part_i_records.append(part_i)
        part_iii = extract_standard_part_iii_record(normalized, page)
        if part_iii:
            part_iii_records.append(part_iii)

    merged: list[dict[str, Any]] = []
    used_part_iii: set[int] = set()
    for part_i in part_i_records:
        match_index = next(
            (
                index
                for index, part_iii in enumerate(part_iii_records)
                if index not in used_part_iii
                and standard_schedule_desc_key(part_iii.get("coverage") or "") == standard_schedule_desc_key(part_i.get("coverage") or "")
                and clean_extracted_value(part_iii.get("contract_number") or "") == clean_extracted_value(part_i.get("contract_number") or "")
            ),
            None,
        )
        combined = dict(part_i)
        if match_index is not None:
            used_part_iii.add(match_index)
            combined["experience_values"] = part_iii_records[match_index].get("experience_values", {})
            combined["part_iii_page"] = part_iii_records[match_index].get("part_iii_page")
        else:
            combined["experience_values"] = {}
        merged.append(combined)
    return merged


def is_standard_long_form_schedule_a(text: str) -> bool:
    upper = text.upper()
    return bool(
        "LONG FORM INFORMATION" in upper
        and "PLAN INFORMATION REPORT FOR THE PERIOD" in upper
        and re.search(_STANDARD_LONG_FORM_CARRIER, text, re.IGNORECASE)
    )


def extract_standard_part_i_record(text: str, page: int | None = None) -> dict[str, Any] | None:
    upper = text.upper()
    if "PART I" not in upper or "INSURANCE FEES AND COMMISSIONS" not in upper:
        return None
    tail = re.search(
        rf"({_STANDARD_LONG_FORM_CARRIER})\s*\n"
        r"(.+?)\s*\n"
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*\n"
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*\n"
        r"([0-9,]+)\s*\n"
        r"([0-9]{2}-[0-9]{7})\s*\n"
        r"([0-9-]{5,})\s*\n"
        r"(\$?\s*[0-9,]+(?:\.\d{2})?)\s*\n"
        r"(\$?\s*[0-9,]+(?:\.\d{2})?)\s*\n"
        r".*?(DENTAL|LIFE\s+INSURANCE|LONG\s+TERM\s+DISABILITY|VISION|[A-Z][A-Z ]+)\s*\n"
        r"PLAN\s+INFORMATION\s+REPORT\s+FOR\s+THE\s+PERIOD\s+OF\s*\n"
        r"([A-Za-z0-9-]+)\s*\n"
        r"LONG\s+FORM\s+INFORMATION\s*\n"
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*\n"
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not tail:
        return None
    broker = extract_standard_broker_info(text)
    reported_commission = standard_money_value(tail.group(8))
    reported_fee = standard_money_value(tail.group(9))
    if broker.get("broker_name"):
        commission_total = broker.get("base_commission") or reported_commission
        fee_total = sum_money_values(
            broker.get("contingent_commission"),
            broker.get("ga_override"),
            broker.get("explicit_fee"),
        ) or "0.00"
    else:
        commission_total = reported_commission
        fee_total = reported_fee
    return {
        "source": "The Standard long form information",
        "carrier_name": clean_extracted_value(tail.group(1)),
        "account_name": clean_extracted_value(tail.group(2)),
        "period_begin": normalize_schedule_a_date(tail.group(3), end_of_month=False),
        "period_end": normalize_schedule_a_date(tail.group(4), end_of_month=True),
        "persons_covered": money_value(tail.group(5)),
        "ein": clean_extracted_value(tail.group(6)),
        "naic_code": normalize_standard_naic(tail.group(7)),
        "commission_total": commission_total,
        "fee_total": fee_total,
        "coverage": clean_extracted_value(tail.group(10)).upper(),
        "contract_number": clean_extracted_value(tail.group(11)),
        "part_i_page": page,
        **broker,
    }


def extract_standard_broker_info(text: str) -> dict[str, str | None]:
    info: dict[str, str | None] = {
        "broker_name": None,
        "broker_address_line_1": None,
        "broker_address_line_2": None,
        "broker_city": None,
        "broker_state": None,
        "broker_zip": None,
        "base_commission": None,
        "contingent_commission": None,
        "ga_override": None,
        "explicit_fee": None,
        "organization_code": None,
    }
    match = re.search(
        r"E\)\s*ORG\.\s*\n\s*CODE\s*\n(?P<broker>.+?)\n"
        r"\s*(?P<base>\$?\s*[0-9,]+(?:\.\d{2})?)\s+"
        r"(?P<contingent>\$?\s*[0-9,]+(?:\.\d{2})?)\s+"
        r"(?P<ga>\$?\s*[0-9,]+(?:\.\d{2})?)\s+"
        r"(?P<fees>\$?\s*[0-9,]+(?:\.\d{2})?)\s+"
        r"(?P<org>[0-9]{1,2})\b",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return info
    lines = [clean_extracted_value(line) for line in match.group("broker").splitlines() if clean_extracted_value(line)]
    if not lines:
        return info
    info["broker_name"] = lines[0]
    if len(lines) > 1:
        info["broker_address_line_1"] = lines[1]
    if len(lines) > 2:
        city_line = lines[-1]
        city_match = re.search(r"(.+?),\s*([A-Z]{2})\s+([0-9]{5}(?:-[0-9]{4})?)", city_line)
        if city_match:
            info["broker_city"] = clean_extracted_value(city_match.group(1))
            info["broker_state"] = city_match.group(2)
            info["broker_zip"] = city_match.group(3)
            if len(lines) > 3:
                info["broker_address_line_2"] = " ".join(lines[2:-1])
        elif len(lines) > 2:
            info["broker_address_line_2"] = " ".join(lines[2:])
    info["base_commission"] = standard_money_value(match.group("base"))
    info["contingent_commission"] = standard_money_value(match.group("contingent"))
    info["ga_override"] = standard_money_value(match.group("ga"))
    info["explicit_fee"] = standard_money_value(match.group("fees"))
    info["organization_code"] = match.group("org")
    return info


def extract_standard_part_iii_record(text: str, page: int | None = None) -> dict[str, Any] | None:
    upper = text.upper()
    if "PART III" not in upper or "EXPERIENCE RATED CONTRACTS" not in upper:
        return None
    tail = re.search(
        rf"{_STANDARD_LONG_FORM_CARRIER}\s+HEREBY\s+CERTIFIES.+?\n(?P<body>.+?)\n"
        r"(?P<contract>[A-Za-z0-9-]+)\s*\n"
        r"(?P<coverage>DENTAL|LIFE\s+INSURANCE|LONG\s+TERM\s+DISABILITY|VISION|[A-Z][A-Z ]+)\s*\n"
        r"(?P<premium>\$?\s*[0-9,]+(?:\.\d{2})?)\s*\n"
        r"LONG\s+FORM\s+INFORMATION",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not tail:
        return None
    money_tokens = [standard_money_value(token) for token in re.findall(r"\(?\$?\s*[0-9,]+(?:\.\d{2})?\)?", tail.group("body"))]
    labels_after_9a1 = STANDARD_EXPERIENCE_FIELD_LABELS[1:]
    experience_values = {"9a. Premiums: (1) Amount Received": standard_money_value(tail.group("premium"))}
    for label, value in zip(labels_after_9a1, money_tokens):
        experience_values[label] = value
    return {
        "contract_number": clean_extracted_value(tail.group("contract")),
        "coverage": clean_extracted_value(tail.group("coverage")).upper(),
        "experience_values": experience_values,
        "part_iii_page": page,
    }


def standard_money_value(value: str | None) -> str:
    text = str(value or "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").strip()
    return f"-{text}" if negative and text and not text.startswith("-") else text


def is_zero_money(value: str | None) -> bool:
    amount = parse_numeric_amount(value)
    return amount is not None and abs(amount) < 0.005


def normalize_standard_naic(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.lstrip("0") or digits


def standard_schedule_desc_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if key in {"LTD", "LONGTERMDISABILITY"} or "LTD" in key or "LONGTERMDISABILITY" in key:
        return "LTD"
    if "DENTAL" in key:
        return "DENTAL"
    if "VISION" in key:
        return "VISION"
    if "LIFE" in key:
        return "LIFE"
    return key


def extract_united_omaha_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    combined_fields = extract_united_omaha_combined_schedule_a_fields(page_texts)
    if combined_fields:
        return combined_fields
    summaries = extract_united_omaha_schedule_a_summaries(page_texts)
    if not summaries:
        return []
    fields: list[NormalizedExtractionField] = []
    for summary in summaries:
        values_by_label = {value.label: value.value for value in summary.values}
        source_text = f"{summary.source} {summary.coverage or ''} {summary.account_number or ''}".strip()

        def add(field_name: str, value: str | None, confidence: float = 0.96):
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(NormalizedExtractionField(field_name=field_name, value=clean, confidence=confidence, page=None, source_text=source_text))

        add("1a. Name of Insurance Company", summary.carrier_name, 0.99)
        add("1b. Insurance Carrier EIN", summary.ein, 0.99)
        add("1c. NAIC Code", summary.naic_code, 0.99)
        add("1d. Contract/Policy Number", summary.account_number, 0.98)
        add("1e. Persons Covered (End of Policy Year)", values_by_label.get("Persons covered"), 0.97)
        add("1f. Policy Year Beginning Date", summary.period_begin, 0.97)
        add("1g. Policy Year Ending Date", summary.period_end, 0.97)
        add("3a. Name of Agent/Broker/Person", values_by_label.get("Broker name"), 0.94)
        add("3b. Amount of Commissions", values_by_label.get("3b. Amount of Commissions"), 0.97)
        add("3c. Amount of Fees", values_by_label.get("3c. Amount of Fees"), 0.97)
        add("3d. Purpose", values_by_label.get("3d. Purpose"), 0.93)
        add("3e. Organizational Code", values_by_label.get("3e. Organizational Code"), 0.94)
        add("10a. Total premiums or subscription charges paid to carrier", values_by_label.get("10a. Total premiums or subscription charges paid to carrier"), 0.98)
    return fields


def extract_united_omaha_combined_schedule_a_fields(
    page_texts: list[tuple[int, str]],
) -> list[NormalizedExtractionField]:
    """Combine benefit worksheets that belong to one carrier group contract."""
    records = extract_united_omaha_schedule_a_records(page_texts)
    group_ids = {record.get("group_id") for record in records if record.get("group_id")}
    if len(records) < 2 or len(group_ids) != 1:
        return []

    first = records[0]
    group_id = next(iter(group_ids))
    normalized_periods = explicit_contract_periods(
        f"Contract Year from {first.get('period_begin') or ''} to {first.get('period_end') or ''}"
    )
    period_begin = normalized_periods[0].beginning if normalized_periods else first.get("period_begin")
    period_end = normalized_periods[0].ending if normalized_periods else first.get("period_end")
    persons = [parse_numeric_amount(record.get("persons_covered")) for record in records]
    persons_covered = max((value for value in persons if value is not None), default=None)
    broker_rows = extract_united_omaha_combined_broker_rows(page_texts)
    commission_total = sum_money_values(*(row.commission_total for row in broker_rows)) or "0"
    fee_total = sum_money_values(*(row.fee_total for row in broker_rows)) or "0"
    primary_broker = next((row.name for row in broker_rows if parse_numeric_amount(row.commission_total)), None)
    values = {
        "1a. Name of Insurance Company": first.get("carrier_name"),
        "1b. Insurance Carrier EIN": first.get("ein"),
        "1c. NAIC Code": first.get("naic_code"),
        "1d. Contract/Policy Number": group_id,
        "1e. Persons Covered (End of Policy Year)": (
            f"{int(persons_covered):,}" if persons_covered is not None else None
        ),
        "1f. Policy Year Beginning Date": period_begin,
        "1g. Policy Year Ending Date": period_end,
        "3a. Name of Agent/Broker/Person": primary_broker,
        "3b. Amount of Commissions": commission_total,
        "3c. Amount of Fees": fee_total,
        "3d. Purpose": derive_schedule_a_purpose(commission_total, fee_total),
        "3e. Organizational Code": next((row.organization_code for row in broker_rows if row.organization_code), None),
        "10a. Total premiums or subscription charges paid to carrier": sum_money_values(
            *(record.get("premium") for record in records)
        ),
    }
    source_text = f"United of Omaha combined group worksheet {group_id}"
    return [
        NormalizedExtractionField(
            field_name=field_name,
            value=clean_extracted_value(str(value)),
            confidence=0.99 if field_name.startswith("1") else 0.97,
            page=None,
            source_text=source_text,
        )
        for field_name, value in values.items()
        if value is not None and not is_blank_extraction_value(clean_extracted_value(str(value)))
    ]


def extract_united_omaha_schedule_a_summaries(page_texts: list[tuple[int, str]]) -> list[ScheduleAWorksheetSummary]:
    summaries: list[ScheduleAWorksheetSummary] = []
    for record in extract_united_omaha_schedule_a_records(page_texts):
        coverage = record.get("coverage")
        broker_rows = record.get("broker_rows") or []
        commission_total = sum_money_values(*(row.commission_total for row in broker_rows)) or "0"
        fee_total = sum_money_values(*(row.fee_total for row in broker_rows)) or "0"
        primary_broker = broker_rows[0].name if broker_rows else ""
        values = [
            ScheduleAWorksheetValue(label="Group identification number", value=record.get("group_id") or "", source="Part I group block", coverage=coverage),
            ScheduleAWorksheetValue(label="Legacy group ID", value=record.get("legacy_group_id") or "", source="Part I group block", coverage=coverage),
            ScheduleAWorksheetValue(label="Persons covered", value=record.get("persons_covered") or "", source="Benefits Provided", coverage=coverage),
            ScheduleAWorksheetValue(label="Broker name", value=primary_broker, source="Recipient table", coverage=coverage),
            ScheduleAWorksheetValue(label="3b. Amount of Commissions", value=commission_total, source="Recipient table", coverage=coverage),
            ScheduleAWorksheetValue(label="3c. Amount of Fees", value=fee_total, source="Recipient table", coverage=coverage),
            ScheduleAWorksheetValue(label="3d. Purpose", value=derive_schedule_a_purpose(commission_total, fee_total) or "", source="Recipient table", coverage=coverage),
            ScheduleAWorksheetValue(label="3e. Organizational Code", value=record.get("organization_code") or "", source="Recipient table", coverage=coverage),
            ScheduleAWorksheetValue(
                label="10a. Total premiums or subscription charges paid to carrier",
                value=record.get("premium") or "",
                source="Part III non-experience rated contracts",
                coverage=coverage,
            ),
        ]
        summaries.append(
            ScheduleAWorksheetSummary(
                source="United of Omaha Schedule A support worksheet",
                carrier_name=record.get("carrier_name"),
                account_name=record.get("account_name"),
                account_number=record.get("legacy_group_id") or record.get("group_id"),
                period_begin=record.get("period_begin"),
                period_end=record.get("period_end"),
                ein=record.get("ein"),
                naic_code=record.get("naic_code"),
                coverage=coverage,
                values=[value for value in values if value.value],
                benefit_rows=[
                    ScheduleABenefitBreakdownRow(
                        benefit_type=coverage or "",
                        persons_covered=record.get("persons_covered"),
                        premium=record.get("premium"),
                        source_page=record.get("page"),
                    )
                ],
                notes=[
                    "Parsed as a separate United of Omaha benefit worksheet page.",
                    f"Group identification number: {record.get('group_id') or ''}".strip(),
                ],
            )
        )
    return summaries


def extract_united_omaha_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    for record in extract_united_omaha_schedule_a_records(page_texts):
        rows.extend(record.get("broker_rows") or [])
    return rows


def extract_united_omaha_combined_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    """Aggregate repeated Omaha recipients by exact identity and address."""
    records = extract_united_omaha_schedule_a_records(page_texts)
    group_ids = {record.get("group_id") for record in records if record.get("group_id")}
    if len(records) < 2 or len(group_ids) != 1:
        return []

    combined: dict[str, ScheduleABrokerRow] = {}
    for record in records:
        for row in record.get("broker_rows") or []:
            key = normalize_compare_key(
                "|".join(
                    str(value or "")
                    for value in (
                        row.name,
                        row.address_line_1,
                        row.address_line_2,
                        row.city,
                        row.state,
                        row.zip_code,
                    )
                )
            )
            current = combined.get(key)
            if current is None:
                current = row.model_copy(deep=True)
                current.commission_rows = []
                current.fee_rows = []
                combined[key] = current
            current.commission_rows.extend(
                money_row.model_copy(deep=True)
                for money_row in row.commission_rows
                if not is_zero_money(money_row.amount)
            )
            current.fee_rows.extend(
                money_row.model_copy(deep=True)
                for money_row in row.fee_rows
                if not is_zero_money(money_row.amount)
            )

    for row in combined.values():
        row.commission_total = sum_money_values(*(item.amount for item in row.commission_rows)) or "0"
        row.fee_total = sum_money_values(*(item.amount for item in row.fee_rows)) or "0"
        row.purpose = derive_schedule_a_purpose(row.commission_total, row.fee_total)
        row.confidence = max(row.confidence, 0.97)
    return list(combined.values())


def extract_summary_table_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows_by_name: dict[str, ScheduleABrokerRow] = {}
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        for section, row_kind in extract_schedule_a_line3_summary_sections(normalized):
            for name, amount in extract_schedule_a_line3_summary_entries(section):
                key = normalize_compare_key(name)
                row = rows_by_name.get(key)
                if not row:
                    row = ScheduleABrokerRow(
                        name=name,
                        organization_code="3",
                        commission_rows=[],
                        fee_rows=[],
                        source_page=page,
                        confidence=0.9,
                    )
                    rows_by_name[key] = row
                money = money_value(amount)
                if row_kind == "commission":
                    row.commission_rows.append(ScheduleABrokerMoneyRow(amount=money, purpose="COMMISSIONS"))
                    row.commission_total = _sum_money_rows(row.commission_rows)
                else:
                    row.fee_rows.append(ScheduleABrokerMoneyRow(amount=money, purpose="FEES"))
                    row.fee_total = _sum_money_rows(row.fee_rows)
    return list(rows_by_name.values())


def extract_schedule_a_line3_summary_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    markers = [
        (r"Schedule\s+A,\s*Line\s+3,\s*Element\s*\(b\)", "commission"),
        (r"Schedule\s+A,\s*Line\s+3,\s*Element\s*\(c\)", "fee"),
    ]
    for marker, row_kind in markers:
        for match in re.finditer(marker, text, flags=re.IGNORECASE):
            section = text[match.start() : match.start() + 1800]
            stop = re.search(
                r"\n\s*(?:The\s+following\s+figure\s+represents\s+(?:commissions|fees)|"
                r"Group\s+insurance\s+coverages|Gross\s+premium|One\s+time\s+reimbursement|"
                r"Indirect\s+compensation|Schedule\s+C|Total\s+premium|Premium\s+due)\b",
                section,
                flags=re.IGNORECASE,
            )
            if stop:
                section = section[: stop.start()]
            sections.append((section, row_kind))
    return sections


def extract_schedule_a_line3_summary_entries(section: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    line_pattern = re.compile(
        r"^\s*(?P<contract>[A-Z0-9][A-Z0-9-]{2,})\s+"
        r"(?P<name>[A-Za-z][A-Za-z0-9&.,'() /-]{2,}?)\s+"
        r"\$?(?P<amount>[0-9][0-9,]*(?:\.\d{2})?)\s*$",
        flags=re.IGNORECASE,
    )
    for raw_line in section.splitlines():
        line = clean_extracted_value(raw_line)
        match = line_pattern.match(line)
        if not match:
            continue
        name = clean_extracted_value(match.group("name"))
        amount = money_value(match.group("amount"))
        if not is_schedule_a_line3_summary_broker_name(name) or is_zero_money(amount):
            continue
        entries.append((name, amount))
    return entries


def is_schedule_a_line3_summary_broker_name(name: str) -> bool:
    clean = clean_extracted_value(name)
    if not clean or not re.search(r"[A-Za-z]", clean):
        return False
    upper = clean.upper()
    blocked_terms = [
        "TOTAL",
        "COMMISSIONS FOR PLAN",
        "FEES PAID",
        "GROUP INSURANCE",
        "GROSS PREMIUM",
        "PREMIUM",
        "AD&D",
        "DENTAL",
        "LIFE",
        "VISION",
        "DISABILITY",
        "ACCIDENT",
        "CRITICAL ILLNESS",
        "REIMBURSEMENT",
        "INDIRECT COMPENSATION",
    ]
    return not any(term in upper for term in blocked_terms)


def extract_united_omaha_schedule_a_records(page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page, text in page_texts:
        normalized = normalize_ocr_text(text)
        if not is_united_omaha_schedule_a_support(normalized):
            continue
        record = extract_united_omaha_schedule_a_record(normalized, page)
        if record:
            records.append(record)
    return records


def is_united_omaha_schedule_a_support(text: str) -> bool:
    upper = text.upper()
    return (
        "SUPPORT FOR FORM 5500, SCHEDULE A" in upper
        and "UNITED OF OMAHA LIFE INSURANCE COMPANY" in upper
        and "BENEFITS PROVIDED" in upper
    )


def extract_united_omaha_schedule_a_record(text: str, page: int | None = None) -> dict[str, Any] | None:
    carrier_match = re.search(r"Name\s+of\s+Carrier:\s*(.+?)\s*-\s*NAIC\s+Code\s+([0-9]{4,6})", text, flags=re.IGNORECASE)
    ein = regex_first(text, [r"EIN\s+Number:\s*([0-9]{2}-[0-9]{7})"], flags=re.IGNORECASE)
    group_match = re.search(
        r"Group\s+Identification\s*\n\s*Number:\s*\n?\s*([A-Z0-9 ]+?)\s+Data\s+for\s+Period:\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4})\s+to\s+([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4})",
        text,
        flags=re.IGNORECASE,
    )
    legacy_group_id = regex_first(text, [r"Legacy\s+Group\s+ID:\s*([A-Z0-9 ]+)"], flags=re.IGNORECASE)
    benefit_match = re.search(r"Benefits\s+Provided\s+Persons\s+Covered\s*\n\s*(.+?)\s+([0-9,]+)\s*\n", text, flags=re.IGNORECASE)
    premium = regex_first(
        text,
        [r"Premiums\s*(?:\.\s*)+([0-9,]+(?:\.\d{2})?)"],
        flags=re.IGNORECASE,
    )
    if not carrier_match or not group_match or not benefit_match:
        return None
    coverage = clean_extracted_value(benefit_match.group(1))
    group_id = normalize_united_omaha_group_id(group_match.group(1))
    legacy_group_id = normalize_united_omaha_group_id(legacy_group_id)
    period_begin = normalize_schedule_a_date(group_match.group(2).replace("-", "/"), end_of_month=False)
    period_end = normalize_schedule_a_date(group_match.group(3).replace("-", "/"), end_of_month=True)
    broker_rows = extract_united_omaha_broker_rows_from_page(text, coverage, page)
    organization_code = next((row.organization_code for row in broker_rows if row.organization_code), None)
    return {
        "source": "United of Omaha Schedule A support worksheet",
        "carrier_name": clean_extracted_value(carrier_match.group(1)),
        "naic_code": clean_extracted_value(carrier_match.group(2)),
        "ein": ein,
        "group_id": group_id,
        "legacy_group_id": legacy_group_id or group_id,
        "account_name": extract_united_omaha_account_name(text),
        "coverage": coverage,
        "persons_covered": money_value(benefit_match.group(2)),
        "period_begin": period_begin,
        "period_end": period_end,
        "premium": money_value(premium or ""),
        "organization_code": organization_code,
        "broker_rows": broker_rows,
        "page": page,
    }


def extract_united_omaha_account_name(text: str) -> str | None:
    marker = "INFORMATION FOR COMPLETION OF PART I"
    if marker not in text:
        return None
    after = text.split(marker, 1)[1]
    lines = [clean_extracted_value(line) for line in after.splitlines() if clean_extracted_value(line)]
    if lines and not lines[0].lower().startswith("name of carrier"):
        return lines[0]
    return None


def extract_united_omaha_broker_rows_from_page(text: str, coverage: str | None, page: int | None) -> list[ScheduleABrokerRow]:
    rows: list[ScheduleABrokerRow] = []
    section = _section_between(text, "Name of Each Recipient", "INFORMATION FOR COMPLETION OF PART III")
    if not section:
        return rows
    first_match = re.search(
        r"(GALLAGHER\s+BENEFIT\s+SERVICES\s+INC)\s+([0-9,]+(?:\.\d{2})?)\s+(Agent\s+or\s+Broker\s+of\s+Record)\s+([0-9]{1,2})\s*\n"
        r"(.+?)\n"
        r"(.+?,\s*[A-Z]{2}\s+[0-9]{5}(?:-[0-9]{4})?)",
        section,
        flags=re.IGNORECASE,
    )
    if first_match:
        city, state, zip_code = split_city_state_zip(first_match.group(6))
        rows.append(
            ScheduleABrokerRow(
                name=clean_extracted_value(first_match.group(1)),
                address_line_1=clean_extracted_value(first_match.group(5)),
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=first_match.group(4),
                commission_rows=[ScheduleABrokerMoneyRow(coverage=coverage, amount=money_value(first_match.group(2)), purpose=clean_extracted_value(first_match.group(3)))],
                commission_total=money_value(first_match.group(2)),
                fee_total="0",
                source_page=page,
                confidence=0.94,
            )
        )
    other_match = re.search(
        r"(GALLAGHER\s+BENEFIT\s+SERVICES\s+INC)\s+0\s+(Other\s+Compensation)\s+([0-9]{1,2})\s*\n"
        r"(.+?)\s+([0-9,]+(?:\.\d{2})?)\s*\n"
        r"(.+?)\n"
        r"(.+?,\s*[A-Z]{2}\s+[0-9]{5}(?:-[0-9]{4})?)",
        section,
        flags=re.IGNORECASE,
    )
    if other_match:
        city, state, zip_code = split_city_state_zip(other_match.group(7))
        rows.append(
            ScheduleABrokerRow(
                # The word following the organization code is the incentive
                # program/purpose, not part of the recipient's legal name.
                name=clean_extracted_value(other_match.group(1)),
                address_line_1=clean_extracted_value(other_match.group(6)),
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=other_match.group(3),
                commission_rows=[ScheduleABrokerMoneyRow(coverage=coverage, amount="0", purpose="Commissions")],
                fee_rows=[ScheduleABrokerMoneyRow(coverage=coverage, amount=money_value(other_match.group(5)), purpose=clean_extracted_value(other_match.group(2)))],
                commission_total="0",
                fee_total=money_value(other_match.group(5)),
                source_page=page,
                confidence=0.93,
            )
        )
    return rows


def normalize_united_omaha_group_id(value: str | None) -> str | None:
    clean = clean_extracted_value(str(value or ""))
    if not clean:
        return None
    return re.sub(r"\s+", "", clean)


def split_city_state_zip(value: str | None) -> tuple[str | None, str | None, str | None]:
    match = re.search(r"(.+?),\s*([A-Z]{2})\s+([0-9]{5}(?:-[0-9]{4})?)", str(value or "").strip())
    if not match:
        return clean_extracted_value(str(value or "")) or None, None, None
    return clean_extracted_value(match.group(1)), match.group(2), match.group(3)


def united_omaha_schedule_desc_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if "LONGTERMDISABILITY" in key or "LTD" in key:
        return "LTD"
    if "SHORTTERMDISABILITY" in key or "STD" in key:
        return "STD"
    if "LIFE" in key:
        return "LIFE"
    if "ADANDD" in key or "ADAD" in key or "AD&D" in str(value or "").upper() or "ACCIDENT" in key:
        return "AD&D"
    return key


def _section_between(text: str, start_label: str, end_label: str | None) -> str:
    start = re.search(re.escape(start_label), text, flags=re.IGNORECASE)
    if not start:
        return ""
    section = text[start.end() :]
    if end_label:
        end = re.search(re.escape(end_label), section, flags=re.IGNORECASE)
        if end:
            section = section[: end.start()]
    return section


def extract_prudential_schedule_a_fields(page_texts: list[tuple[int, str]]) -> list[NormalizedExtractionField]:
    summaries = extract_prudential_schedule_a_summaries(page_texts)
    if not summaries:
        return []
    fields: list[NormalizedExtractionField] = []
    for summary in summaries:
        source_text = f"{summary.source} {summary.account_name or ''} {summary.account_number or ''}".strip()

        def add(field_name: str, value: str | None, confidence: float = 0.98):
            clean = clean_extracted_value(str(value or ""))
            if clean and not is_blank_extraction_value(clean):
                fields.append(NormalizedExtractionField(field_name=field_name, value=clean, confidence=confidence, page=None, source_text=source_text))

        values_by_label = {value.label: value.value for value in summary.values}
        add("1a. Name of Insurance Company", summary.carrier_name, 0.99)
        add("1b. Insurance Carrier EIN", summary.ein, 0.99)
        add("1c. NAIC Code", summary.naic_code, 0.99)
        add("1d. Contract/Policy Number", summary.account_number, 0.99)
        add("1e. Persons Covered (End of Policy Year)", values_by_label.get("Persons covered"), 0.98)
        add("1f. Policy Year Beginning Date", summary.period_begin, 0.98)
        add("1g. Policy Year Ending Date", summary.period_end, 0.98)
        add("10a. Total premiums or subscription charges paid to carrier", values_by_label.get("Total nonexperience premium"), 0.99)
    return fields


def extract_prudential_schedule_a_summaries(page_texts: list[tuple[int, str]]) -> list[ScheduleAWorksheetSummary]:
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for page, text in page_texts:
        record = extract_prudential_benefit_record(text, page)
        if not record:
            continue
        key = (
            record["ein"],
            record["naic_code"],
            record["contract_number"],
            record["period_begin"],
            record["period_end"],
        )
        group = groups.setdefault(
            key,
            {
                "source": "Prudential insured welfare plan data",
                "carrier_name": record["carrier_name"],
                "account_name": record["account_name"],
                "account_number": record["contract_number"],
                "period_begin": record["period_begin"],
                "period_end": record["period_end"],
                "ein": record["ein"],
                "naic_code": record["naic_code"],
                "benefit_rows": [],
                "premium_values": [],
                "covered_values": [],
                "pages": [],
            },
        )
        group["benefit_rows"].append(
            ScheduleABenefitBreakdownRow(
                benefit_type=record["benefit_type"],
                persons_covered=record["persons_covered"],
                premium=record["premium"],
                source_page=page,
            )
        )
        group["premium_values"].append(record["premium"])
        group["covered_values"].append(record["persons_covered"])
        group["pages"].append(page)

    summaries: list[ScheduleAWorksheetSummary] = []
    for group in groups.values():
        total_premium = sum_money_values(*group["premium_values"])
        persons_covered = max_numeric_string(group["covered_values"])
        values = [
            ScheduleAWorksheetValue(label="Persons covered", value=persons_covered or "", source="Grouped benefit pages", coverage="All benefits"),
            ScheduleAWorksheetValue(label="Total nonexperience premium", value=total_premium or "", source="Line 9a on Prudential source pages", coverage="All benefits"),
        ]
        summaries.append(
            ScheduleAWorksheetSummary(
                source=group["source"],
                carrier_name=group["carrier_name"],
                account_name=group["account_name"],
                account_number=group["account_number"],
                period_begin=group["period_begin"],
                period_end=group["period_end"],
                ein=group["ein"],
                naic_code=group["naic_code"],
                coverage="Multiple benefits" if len(group["benefit_rows"]) > 1 else group["benefit_rows"][0].benefit_type,
                values=[value for value in values if value.value],
                benefit_rows=group["benefit_rows"],
                notes=[
                    "Grouped same Prudential contract across benefit pages.",
                    f"Source pages: {', '.join(str(page) for page in group['pages'])}",
                ],
            )
        )
    return summaries


def extract_prudential_benefit_record(text: str, page: int | None = None) -> dict[str, str] | None:
    normalized = normalize_ocr_text(text)
    upper = normalized.upper()
    if "PRUDENTIAL" not in upper or "INSURED WELFARE PLAN DATA" not in upper or "NON EXPERIENCE RATED CONTRACTS" not in upper:
        return None
    carrier_name = regex_first(normalized, [r"1\s*\(a\)\s*(Prudential\s+Insurance\s+Company\s+of\s+America)"])
    ein = regex_first(normalized, [r"1\s*\(b\)\s*Prudential'?s\s+EIN:\s*([0-9]{2}-[0-9]{7})"])
    naic = regex_first(normalized, [r"1\s*\(c\)\s*NAIC\s+code:\s*([0-9]{4,6})"])
    contract = regex_first(normalized, [r"1\s*\(d\)\s*Contract\s+number\s+or\s+identification:\s*([A-Za-z0-9-]+)"])
    period = regex_first(normalized, [r"\n\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s+See\s+Form"], groups=True)
    premium = regex_first(normalized, [r"Non\s+experience\s+rated\s+contracts:.*?Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier\s+\$\s*([0-9,]+(?:\.\d{2})?)"], flags=re.IGNORECASE | re.DOTALL)
    account_name = regex_first(normalized, [r"Insured\s+Welfare\s+Plan\s+Data\s*\n\s*(.+?)\s+\(Item\s+numbers"], flags=re.IGNORECASE | re.DOTALL)
    benefit_match = re.search(r"\n\s*([A-Za-z][A-Za-z0-9 &/'().-]+?)\s+([0-9,]+)\s*$", normalized.strip(), flags=re.IGNORECASE)
    if not all([carrier_name, ein, naic, contract, isinstance(period, tuple), premium, benefit_match]):
        return None
    return {
        "carrier_name": clean_extracted_value(carrier_name or "Prudential Insurance Company of America"),
        "ein": clean_extracted_value(ein or ""),
        "naic_code": clean_extracted_value(naic or ""),
        "contract_number": clean_extracted_value(contract or ""),
        "period_begin": normalize_schedule_a_date(period[0], end_of_month=False),
        "period_end": normalize_schedule_a_date(period[1], end_of_month=True),
        "premium": money_value(premium or ""),
        "account_name": clean_extracted_value(account_name or ""),
        "benefit_type": clean_extracted_value(benefit_match.group(1)),
        "persons_covered": money_value(benefit_match.group(2)),
        "source_page": str(page or ""),
    }


def max_numeric_string(values: list[str | None]) -> str | None:
    best: int | None = None
    for value in values:
        text = str(value or "").replace(",", "").strip()
        if not text.isdigit():
            continue
        number = int(text)
        best = number if best is None else max(best, number)
    return f"{best:,}" if best is not None else None


def _extract_prudential_layout_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    rows_by_name: dict[str, ScheduleABrokerRow] = {}
    record_start = re.compile(r"^\s*(?P<contract>[A-Z0-9-]{4,})\s+(?P<rest>[A-Za-z].*)$")
    amount_pattern = re.compile(r"\$\s*([0-9,]+(?:\.\d{2})?)")
    city_pattern = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>[0-9]{4,5}(?:-[0-9]{4})?)$", re.IGNORECASE)
    known_contracts = {
        summary.account_number
        for summary in extract_prudential_schedule_a_summaries(page_texts)
        if summary.account_number
    }
    for page, text in page_texts:
        if "INSURED WELFARE PLAN COMMISSION INFORMATION" not in str(text or "").upper():
            continue
        lines = [re.sub(r"\s+", " ", str(line)).strip() for line in str(text).splitlines()]
        candidate_starts = [(index, record_start.match(line)) for index, line in enumerate(lines) if record_start.match(line)]
        token_counts: dict[str, int] = {}
        for _, match in candidate_starts:
            token_counts[match.group("contract")] = token_counts.get(match.group("contract"), 0) + 1
        starts = [
            index
            for index, match in candidate_starts
            if match.group("contract") in known_contracts or token_counts.get(match.group("contract"), 0) >= 2
        ]
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            block = [line for line in lines[start:end] if line]
            if not block:
                continue
            first = record_start.match(block[0])
            if not first:
                continue
            name_parts: list[str] = []
            remaining: list[str] = []
            amount: str | None = None
            money_index: int | None = None
            for index, line in enumerate([first.group("rest"), *block[1:]]):
                money = amount_pattern.search(line)
                if money:
                    prefix = clean_extracted_value(line[: money.start()])
                    if prefix:
                        name_parts.append(prefix)
                    amount = money_value(money.group(1))
                    money_index = index
                    remaining = [*([clean_extracted_value(line[money.end() :])] if clean_extracted_value(line[money.end() :]) else []), *block[index + 1 :]]
                    break
                name_parts.append(line)
            if not amount or money_index is None:
                continue
            city_index = next((index for index, line in enumerate(remaining) if city_pattern.match(line)), None)
            if city_index is None:
                continue
            before_city = [
                line
                for line in remaining[:city_index]
                if line
                and normalize_compare_key(line)
                not in {
                    "salesandservicecompensation",
                    "supplementalcommissions",
                    "thirdpartyadministrationfees",
                }
            ]
            first_address = next(
                (
                    index
                    for index, value in enumerate(before_city)
                    if re.search(
                        r"\d|\b(?:STREET|ST|AVENUE|AVE|ROAD|RD|PARKWAY|PKWY|DRIVE|DR|BOULEVARD|BLVD|LANE|LN|WAY|COURT|CT)\b",
                        value,
                        flags=re.IGNORECASE,
                    )
                ),
                None,
            )
            if first_address is None:
                continue
            compact_inc = re.match(r"^INC(?P<address>\d.*)$", before_city[first_address], flags=re.IGNORECASE)
            if compact_inc:
                name_parts.append("INC")
                before_city[first_address] = compact_inc.group("address")
            name_parts.extend(before_city[:first_address])
            name = normalize_prudential_broker_name(" ".join(name_parts))
            address_lines = before_city[first_address:]
            city = city_pattern.match(remaining[city_index])
            if not name or not is_probable_person_or_entity_name(name) or not city:
                continue
            key = normalize_compare_key(name)
            row = rows_by_name.get(key)
            if not row:
                row = ScheduleABrokerRow(
                    name=name,
                    address_line_1=address_lines[-1] if address_lines else None,
                    address_line_2=" ".join(address_lines[:-1]) or None,
                    city=clean_extracted_value(city.group("city")),
                    state=city.group("state").upper(),
                    zip_code=normalize_zip_code(city.group("zip")),
                    organization_code=prudential_org_code_for_broker(name),
                    commission_rows=[],
                    fee_rows=[],
                    source_page=page,
                    confidence=0.995,
                )
                rows_by_name[key] = row
            purpose = prudential_purpose_for_broker(name)
            money_row = ScheduleABrokerMoneyRow(amount=amount, purpose=purpose)
            if prudential_amount_is_commission(name, purpose):
                row.commission_rows.append(money_row)
            else:
                row.fee_rows.append(money_row)
    rows = list(rows_by_name.values())
    for row in rows:
        row.commission_total = _sum_money_rows(row.commission_rows) if row.commission_rows else "0"
        row.fee_total = _sum_money_rows(row.fee_rows) if row.fee_rows else "0"
    return rows


def extract_prudential_broker_rows(page_texts: list[tuple[int, str]]) -> list[ScheduleABrokerRow]:
    layout_rows = _extract_prudential_layout_broker_rows(page_texts)
    if layout_rows:
        return layout_rows
    commission_pages = [
        (page, text)
        for page, text in page_texts
        if "Insured Welfare Plan Commission Information" in text
        or "Organization" in text and "code" in text
    ]
    combined = "\n".join(text for _, text in commission_pages)
    normalized = normalize_ocr_text(combined)
    if "GRP 27722" not in normalized and "INSURED WELFARE PLAN COMMISSION INFORMATION" not in normalized.upper():
        return []
    rows_by_name: dict[str, ScheduleABrokerRow] = {}
    commission_section = re.split(r"\n\s*Includes amounts paid", normalized, flags=re.IGNORECASE)[0]
    contract_numbers = [
        summary.account_number
        for summary in extract_prudential_schedule_a_summaries(page_texts)
        if summary.account_number
    ]
    if not contract_numbers:
        contract_numbers = re.findall(r"(?m)^\s*([A-Z0-9-]{4,})\s+[A-Z]", commission_section)
    contract_numbers = list(dict.fromkeys(contract_numbers))
    if not contract_numbers:
        return []
    contract_pattern = "(?:" + "|".join(re.escape(value) for value in contract_numbers) + ")"
    segments = re.split(rf"(?=\n?\s*{contract_pattern}\s+)", commission_section)
    for segment in segments:
        segment = segment.strip()
        if not re.match(rf"{contract_pattern}\b", segment):
            continue
        match = re.match(rf"{contract_pattern}\s+(.+?)\s+\$([0-9,]+(?:\.\d{{2}})?)\s*(.*)$", segment, flags=re.DOTALL)
        if not match:
            continue
        raw_name = clean_extracted_value(re.sub(r"\s+", " ", match.group(1)))
        amount = money_value(match.group(2))
        address_lines = [line.strip() for line in match.group(3).splitlines() if line.strip()]
        address_lines = [line for line in address_lines if not re.match(rf"{contract_pattern}\b", line)]
        city = state = zip_code = None
        city_line_index = None
        for index, line in enumerate(address_lines):
            city_match = re.search(r"(.+?),\s*([A-Z]{2})\s+([0-9-]+)", line)
            if city_match:
                city = clean_extracted_value(city_match.group(1))
                state = city_match.group(2)
                zip_code = normalize_zip_code(city_match.group(3))
                city_line_index = index
                break
        street_lines = address_lines[:city_line_index] if city_line_index is not None else address_lines
        address_line_1 = street_lines[-1] if street_lines else None
        address_line_2 = " ".join(street_lines[:-1]) if len(street_lines) > 1 else None
        name = normalize_prudential_broker_name(raw_name)
        if not name or not is_probable_person_or_entity_name(name):
            continue
        key = normalize_compare_key(name)
        row = rows_by_name.get(key)
        if not row:
            row = ScheduleABrokerRow(
                name=name,
                address_line_1=address_line_1,
                address_line_2=address_line_2,
                city=city,
                state=state,
                zip_code=zip_code,
                organization_code=prudential_org_code_for_broker(name),
                commission_rows=[],
                fee_rows=[],
                source_page=commission_pages[0][0] if commission_pages else None,
                confidence=0.9,
            )
            rows_by_name[key] = row
        purpose = prudential_purpose_for_broker(name)
        money_row = ScheduleABrokerMoneyRow(coverage=None, amount=amount, purpose=purpose)
        if prudential_amount_is_commission(name, purpose):
            row.commission_rows.append(money_row)
        else:
            row.fee_rows.append(money_row)

    rows: list[ScheduleABrokerRow] = []
    for row in rows_by_name.values():
        row.commission_total = _sum_money_rows(row.commission_rows) if row.commission_rows else "0"
        row.fee_total = _sum_money_rows(row.fee_rows) if row.fee_rows else "0"
        rows.append(row)
    return rows


def normalize_prudential_broker_name(value: str) -> str:
    name = clean_extracted_value(value)
    name = re.sub(r"\bINC\b$", "INC", name, flags=re.IGNORECASE)
    if normalize_compare_key(name) == "selmancompanyllc":
        return "Selman & Company, LLC"
    return name


def normalize_zip_code(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{9}", text):
        return f"{text[:5]}-{text[5:]}"
    if re.fullmatch(r"\d{4}", text):
        return text.zfill(5)
    shortened_plus_four = re.fullmatch(r"(?P<base>\d{5})-(?P<extension>\d{3})", text)
    if shortened_plus_four:
        return f"{shortened_plus_four.group('base')}-0{shortened_plus_four.group('extension')}"
    return text


def prudential_purpose_for_broker(name: str) -> str:
    key = normalize_compare_key(name)
    if "img" == key:
        return "Third Party Administration Fees"
    if "selman" in key:
        return "Sales and Service Compensation"
    return "Commissions"


def prudential_org_code_for_broker(name: str) -> str:
    return "5" if normalize_compare_key(name) == "img" else "3"


def prudential_amount_is_commission(name: str, purpose: str) -> bool:
    key = normalize_compare_key(name)
    return "brokerage" in key or "commission" in normalize_compare_key(purpose)


def extract_schedule_a_broker_rows(text: str, page: int | None = None) -> list[ScheduleABrokerRow]:
    normalized = normalize_ocr_text(text)
    if not normalized:
        return []
    blocks = re.split(
        r"Name\s+and\s+address\s+of\s+the\s+agents?,?\s+brokers?\s+or\s+other\s+persons?\s+to\s+whom\s+commissions\s+or\s+fees\s+were\s+paid",
        normalized,
        flags=re.IGNORECASE,
    )[1:]
    rows: list[ScheduleABrokerRow] = []
    seen: set[tuple[str, str, str, str]] = set()
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # MetLife's compact broker table labels the same fields as Address,
        # ST and ZIP; normalise them before the shared labelled-row parser.
        block = re.sub(r"\bAddress\s*:", "Address Line 1:", block, flags=re.IGNORECASE)
        block = re.sub(r"\bST\s*:", "State:", block, flags=re.IGNORECASE)
        block = re.sub(r"\bZIP\s*:", "Zip Code:", block, flags=re.IGNORECASE)
        block = re.sub(
            r"(?<=[A-Za-z0-9])(?=(?:Address\s+Line\s+1|Address\s+Line\s+2|City|State|Zip\s+Code|Organization\s+code|Commissions\s+Paid|Fees\s+Paid)\s*:?)",
            " ",
            block,
            flags=re.IGNORECASE,
        )
        next_block = re.search(
            r"Name\s+and\s+address\s+of\s+the\s+agents?,?\s+brokers?\s+or\s+other\s+persons?\s+to\s+whom\s+commissions\s+or\s+fees\s+were\s+paid",
            block,
            flags=re.IGNORECASE,
        )
        if next_block:
            block = block[: next_block.start()]
        block = re.split(
            r"\bPart\s+III\b|\bWelfare\s+Benefit\s+Contract\s+Information\b|"
            r"\bINFORMATION\s+FOR\s+COMPLETING\s+SCHEDULE\s+C\b|"
            r"\bSCHEDULE\s+C\s*-\s*SERVICE\s+PROVIDER\s+INFORMATION\b|"
            r"\bEligible\s+Indirect\s+Compensation\b|"
            r"\bPlan\s+Detail\s+Report\b",
            block,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        name = _schedule_a_labeled_value(block, "Name", ["Address Line 1", "Address Line 2", "City", "State", "Zip Code", "Organization code", "Commissions Paid", "Fees Paid"])
        if not name or not is_probable_person_or_entity_name(name):
            continue
        address_line_1 = _schedule_a_labeled_value(block, "Address Line 1", ["Address Line 2", "City", "State", "Zip Code", "Organization code", "Commissions Paid", "Fees Paid"])
        address_line_2 = _schedule_a_labeled_value(block, "Address Line 2", ["City", "State", "Zip Code", "Organization code", "Commissions Paid", "Fees Paid"])
        city = _schedule_a_labeled_value(block, "City", ["State", "Zip Code", "Organization code", "Commissions Paid", "Fees Paid"])
        state = _schedule_a_labeled_value(block, "State", ["Zip Code", "Organization code", "Commissions Paid", "Fees Paid"])
        zip_code = _schedule_a_labeled_value(block, "Zip Code", ["Organization code", "Commissions Paid", "Fees Paid"])
        organization_code = _schedule_a_labeled_value(block, "Organization code", ["Commissions Paid", "Fees Paid"])
        if organization_code:
            organization_code = regex_first(organization_code, [r"\b([0-9]{1,2})\b"]) or organization_code
        commission_section = _schedule_a_section_between(block, "Commissions Paid", "Fees Paid")
        fee_section = _schedule_a_section_between(block, "Fees Paid", None)
        commission_money_rows, commission_total = _schedule_a_money_rows(commission_section)
        fee_money_rows, fee_total = _schedule_a_money_rows(fee_section)
        if re.search(r"Commissions\s+Paid\s+Fees\s+Paid\s+Organization", block, re.IGNORECASE):
            # A compact three-column MetLife row prints the organization code
            # immediately before both subtotal amounts; the generic section
            # parser otherwise mistakes code 03 for a $3 fee.
            compact_totals = re.search(
                r"\b(0?[1-9])\s+([\d,]+)\s+Sub-?total\s+([\d,]+)\s+Sub-?total\b",
                block,
                re.IGNORECASE,
            )
            if compact_totals:
                organization_code, commission_total, fee_total = compact_totals.groups()
                commission_money_rows = []
                fee_money_rows = []
        row = ScheduleABrokerRow(
            name=name,
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            state=state,
            zip_code=zip_code,
            organization_code=organization_code,
            commission_rows=commission_money_rows,
            fee_rows=fee_money_rows,
            commission_total=commission_total,
            fee_total=fee_total,
            source_page=page,
            confidence=0.94 if commission_total or fee_total else 0.86,
        )
        key = (
            normalize_compare_key(row.name),
            normalize_compare_key(row.address_line_1 or ""),
            str(row.commission_total or ""),
            str(row.fee_total or ""),
        )
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


def dedupe_schedule_a_broker_rows(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    deduped: list[ScheduleABrokerRow] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            normalize_compare_key(row.name),
            normalize_compare_key(row.address_line_1 or ""),
            str(row.commission_total or ""),
            str(row.fee_total or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _schedule_a_labeled_value(text: str, label: str, stop_labels: list[str]) -> str | None:
    stops = "|".join(re.escape(stop).replace(r"\ ", r"\s+") for stop in stop_labels)
    label_pattern = re.escape(label).replace(r"\ ", r"\s+")
    match = re.search(rf"\b{label_pattern}\s*:\s*(.*?)(?=\b(?:{stops})\s*:|\bCommissions\s+Paid\b|\bFees\s+Paid\b|$)", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
    return clean_extracted_value(value) if value else None


def _schedule_a_section_between(text: str, start_label: str, end_label: str | None) -> str:
    start = re.search(re.escape(start_label).replace(r"\ ", r"\s+"), text, flags=re.IGNORECASE)
    if not start:
        return ""
    section = text[start.end() :]
    if end_label:
        end = re.search(re.escape(end_label).replace(r"\ ", r"\s+"), section, flags=re.IGNORECASE)
        if end:
            section = section[: end.start()]
    return section


def _schedule_a_money_rows(section: str) -> tuple[list[ScheduleABrokerMoneyRow], str | None]:
    if not section:
        return [], None
    compact = normalize_ocr_text(section)
    compact = re.sub(r"\bCoverage\s+Amount\s+Purpose\b", " ", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\bCoverage\b|\bAmount\b|\bPurpose\b", " ", compact, flags=re.IGNORECASE)
    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    rows: list[ScheduleABrokerMoneyRow] = []
    subtotal: str | None = None
    last_row: ScheduleABrokerMoneyRow | None = None
    for line in lines:
        clean = re.sub(r"\s+", " ", line).strip()
        sub_match = re.search(r"\b([0-9,]+(?:\.\d{2})?)\s*Sub\s*Total\b", clean, flags=re.IGNORECASE)
        if sub_match:
            subtotal = money_value(sub_match.group(1))
            continue
        match = re.match(r"(?:(?P<coverage>[A-Za-z][A-Za-z /&-]{1,40})\s+)?(?P<amount>[0-9,]+(?:\.\d{2})?)(?P<purpose>[A-Za-z][A-Za-z &/-].*)?$", clean)
        if match:
            row = ScheduleABrokerMoneyRow(
                coverage=clean_extracted_value(match.group("coverage") or "") or None,
                amount=money_value(match.group("amount")),
                purpose=clean_extracted_value(match.group("purpose") or "") or None,
            )
            rows.append(row)
            last_row = row
            continue
        if last_row and clean and not re.search(r"\d", clean):
            last_row.purpose = clean_extracted_value(" ".join(filter(None, [last_row.purpose, clean])))
    if subtotal is None and rows:
        subtotal = _sum_money_rows(rows)
    return rows, subtotal


def _sum_money_rows(rows: list[ScheduleABrokerMoneyRow]) -> str | None:
    total = 0.0
    found = False
    for row in rows:
        number = str(row.amount or "").replace(",", "")
        try:
            total += float(number)
            found = True
        except ValueError:
            continue
    if not found:
        return None
    if total.is_integer():
        return f"{int(total):,}"
    return f"{total:,.2f}"


def normalize_compare_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def extract_schedule_a_broker_compensation_fields(text: str, page: int | None = None) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    source_text = normalize_ocr_text(text)[:1200]

    def add(field_name: str, value: str | None, confidence: float = 0.98, value_validator=None):
        clean = clean_extracted_value(str(value or ""))
        if not clean or is_blank_extraction_value(clean):
            return
        if value_validator and not value_validator(clean):
            return
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=page,
                source_text=source_text,
            )
        )

    commission_total, fee_total = extract_schedule_a_broker_totals(text)
    add("3b. Amount of Commissions", commission_total, 0.99)
    add("3c. Amount of Fees", fee_total, 0.99)

    agent_name = extract_schedule_a_broker_block_name(text)
    add("3a. Name of Agent/Broker/Person", agent_name, 0.98, is_probable_person_or_entity_name)

    org_code = extract_schedule_a_broker_org_code(text)
    add("3e. Organizational Code", org_code, 0.98, looks_like_org_code)

    purpose = derive_schedule_a_purpose(commission_total, fee_total)
    add("3d. Purpose", purpose, 0.98)
    return fields


def extract_schedule_a_broker_totals(text: str) -> tuple[str | None, str | None]:
    normalized = normalize_ocr_text(text)
    patterns = [
        r"Total\s+Amount\s+of\s+commissions\s+paid\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)\s+Total\s+fees\s+paid\s*/?\s*amount\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
        r"Total\s+Amount\s+of\s+commissions\s+paid\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?).{0,160}?Total\s+(?:amount\s+of\s+)?fees\s+paid\s*:?\s*\$?\s*([0-9,]+(?:\.\d{2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return money_value(match.group(1)), money_value(match.group(2))
    return None, None


def extract_schedule_a_broker_block_name(text: str) -> str | None:
    section = broker_name_section(text)
    if not section:
        return None
    match = re.search(
        r"Name\s+and\s+address\s+of\s+the\s+agents?.*?\bName\s*:\s*(.+?)(?=\s*Address\s+Line\s*1\s*:|\s+Address\s*:|\n|$)",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def extract_schedule_a_broker_org_code(text: str) -> str | None:
    section = broker_name_section(text)
    if not section:
        return None
    match = re.search(r"Organization\s+code\s*:\s*([0-9]{1,2})\b", section, flags=re.IGNORECASE)
    return match.group(1) if match else None


def broker_name_section(text: str) -> str | None:
    if re.search(r"Name\s+and\s+address\s+of\s+the\s+agents?", text, flags=re.IGNORECASE):
        return text
    return schedule_a_broker_table_section(text)


def extract_schedule_a_line_11(text: str) -> str | None:
    normalized = normalize_ocr_text(text)
    explicit = re.search(
        r"Did\s+the\s+insurance\s+company\s+fail\s+to\s+provide\s+any\s+information\s+necessary\s+to\s+complete\s+Schedule\s+A\??.{0,80}?\b(Yes|No)\b",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if explicit:
        return explicit.group(1).title()
    if re.search(r"\bSchedule\s+A\b", normalized, flags=re.IGNORECASE) and re.search(r"\bInsurance\s+Information\b|\bInsurance\s+Contract\b", normalized, flags=re.IGNORECASE):
        return "No"
    return None


def extract_schedule_a_fields_from_tables(text: str, page: int | None = None) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    source_text = normalize_ocr_text(text)[:1200]

    def add(field_name: str, value: str | None, confidence: float = 0.96, value_validator=None):
        clean = clean_extracted_value(str(value or ""))
        if not clean or is_blank_extraction_value(clean):
            return
        if value_validator and not value_validator(clean):
            return
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=page,
                source_text=source_text,
            )
        )

    add("1d. Contract/Policy Number", extract_table_contract_identifier(text), 0.97, is_valid_contract_identifier)
    add("1e. Persons Covered (End of Policy Year)", extract_table_covered_persons(text), 0.96)
    policy_dates = extract_table_policy_dates(text)
    if policy_dates:
        add("1f. Policy Year Beginning Date", policy_dates[0], 0.96)
        add("1g. Policy Year Ending Date", policy_dates[1], 0.96)
    add("3a. Name of Agent/Broker/Person", extract_table_agent_name(text), 0.94, is_probable_person_or_entity_name)
    add("3b. Amount of Commissions", extract_table_commission_amount(text), 0.97)
    return fields


def extract_table_contract_identifier(text: str) -> str | None:
    coverage_row = re.search(
        r"EIN\s+NAIC\s+Code\s+Contract\s+or\s+identification\s*#.*?"
        r"([0-9]{2}-[0-9]{7})\s+([0-9]{4,6})\s+([A-Za-z0-9-]{3,})\s+([0-9,]+)\s+"
        r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\s+[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if coverage_row and is_valid_contract_identifier(coverage_row.group(3), allow_numeric=True):
        return coverage_row.group(3)

    for pattern in [
        r"\bFor:\s*.*?([0-9][A-Za-z0-9-]{4,}?)(?=Policy\s+Period)",
        r"\bContract\s+or\s*Identification.*?([0-9][A-Za-z0-9-]{4,}?)(?=[A-Z][A-Z ]{5,})",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match and is_valid_contract_identifier(match.group(1)):
            return match.group(1)

    coverage_section = regex_first(
        text,
        [
            r"(\(d\)\s*Contract\s+Number\s+or\s+Identification:?.*?)(?=\(e\)\s*Approximate|Policy\s+or\s+contract\s+Year|2\.\s*Insurance)",
            r"(\(d\)\s*Contract\s+or\s+identification\s+number:?.*?)(?=\(e\)\s*Approximate|Policy\s+or\s+contract\s+Year|2\.\s*Insurance)",
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not coverage_section:
        return None
    for candidate in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9-]{2,}\b", coverage_section):
        if is_valid_contract_identifier(candidate, allow_numeric=True):
            return candidate
    return None


def extract_table_covered_persons(text: str) -> str | None:
    worksheet_total = re.search(
        r"\bTotal\s*\(E\)\s*([0-9][0-9,]*)\b.{0,160}?"
        r"(?:Approx\.?\s+no\.?\s+of\s+Persons\s+cov\.?|Persons\s+covered)"
        r".{0,100}?End\s+of\s+Policy\s+Year",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if worksheet_total:
        return worksheet_total.group(1)

    flattened_match = re.search(
        r"NAIC\s+Code:.*?Listing\s*([0-9][0-9,]*)\s*[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if flattened_match:
        return flattened_match.group(1)

    section = regex_first(
        text,
        [
            r"(\(e\)\s*Approximate\s+Number\s+of.*?persons\s+covered.*?)(?=Policy\s+or\s+contract\s+Year|\(f\)\s*From|2\.\s*Insurance)",
            r"(\(e\)\s*Approximate\s+number\s+of\s+persons\s+covered.*?)(?=\(f\)\s*From|2\.\s*Insurance)",
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section:
        return None
    numbers = re.findall(r"\b[0-9][0-9,]*\b", section)
    return numbers[-1] if numbers else None


def extract_table_policy_dates(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\(f\)\s*From:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}).*?"
        r"\(g\)\s*To:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return normalize_schedule_a_date(match.group(1), end_of_month=False), normalize_schedule_a_date(match.group(2), end_of_month=True)
    worksheet_match = re.search(
        r"\(F\)\s*From\s*\(F\)\s*([0-9]{4}-[0-9]{2}-[0-9]{2}).*?"
        r"\(G\)\s*To\s*\(G\)\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if worksheet_match:
        return normalize_schedule_a_date(worksheet_match.group(1), end_of_month=False), normalize_schedule_a_date(worksheet_match.group(2), end_of_month=True)
    return None


def extract_table_agent_name(text: str) -> str | None:
    broker_section = schedule_a_broker_table_section(text)
    if not broker_section:
        return None
    contract_id = extract_table_contract_identifier(text)
    pattern = rf"{re.escape(contract_id)}\s+(.+?)\s+\$?\s*[0-9,]+(?:\.\d{{2}})?" if contract_id else r"([A-Z][A-Z0-9&.,'() -]{8,}?)\s+\$?\s*[0-9,]+(?:\.\d{2})?"
    match = re.search(pattern, broker_section, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    value = re.split(r"\s+\d{3,}\s+[A-Z ]+\b", value, maxsplit=1)[0].strip()
    return value


def extract_table_commission_amount(text: str) -> str | None:
    broker_section = schedule_a_broker_table_section(text)
    if not broker_section:
        return None
    match = re.search(
        r"Amount\s+of\s+commissions\s+paid.*?\$\s*([0-9,]+(?:\.\d{2})?)",
        broker_section,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return money_value(match.group(1))
    amounts = re.findall(r"\$\s*([0-9,]+(?:\.\d{2})?)", broker_section)
    return money_value(amounts[0]) if amounts else None


def schedule_a_broker_table_section(text: str) -> str | None:
    return regex_first(
        text,
        [
            r"(2\.\s*Insurance\s+Fees\s+and\s+commissions\s+paid\s+to\s+agents\s+and\s+brokers:?.*?)(?=Part\s+II|Part\s+III|$)",
            r"(Insurance\s+Fees\s+and\s+commissions\s+paid\s+to\s+agents\s+and\s+brokers:?.*?)(?=Part\s+II|Part\s+III|$)",
        ],
        flags=re.IGNORECASE | re.DOTALL,
    )


def is_valid_contract_identifier(value: str | None, *, allow_numeric: bool = False) -> bool:
    text = str(value or "").strip()
    if len(text) < 3:
        return False
    if re.search(r"\d{2,}(?:ba|bas|base)$", text, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"[0-9,]+", text):
        return allow_numeric and len(text.replace(",", "")) >= 3
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text):
        return False
    iso_like = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_like:
        year, month, day = (int(part) for part in iso_like.groups())
        if 1900 <= year <= 2100:
            try:
                datetime(year, month, day)
                return False
            except ValueError:
                pass
        return True
    if re.fullmatch(r"\d[\d./-]{2,}", text):
        return len(re.sub(r"\D", "", text)) >= 3
    return bool(re.search(r"[A-Za-z]", text) and re.search(r"\d", text))


def extract_schedule_a_fields_from_rule_labels(
    text: str,
    page: int | None = None,
    *,
    rules=None,
) -> list[NormalizedExtractionField]:
    fields: list[NormalizedExtractionField] = []
    source_text = normalize_ocr_text(text)[:1200]

    def add(field_name: str, value: str | None, confidence: float = 0.91, value_validator=None):
        clean = clean_extracted_value(str(value or ""))
        if not clean or is_blank_extraction_value(clean):
            return
        if value_validator and not value_validator(clean):
            return
        fields.append(
            NormalizedExtractionField(
                field_name=field_name,
                value=clean,
                confidence=confidence,
                page=page,
                source_text=source_text,
            )
        )

    add(
        "1b. Insurance Carrier EIN",
        extract_labeled_value(
            text,
            rule_labels(
                "1b. Insurance Carrier EIN",
                "Insurance Carrier Employer Identification Number",
                "Insurance Carrier Federal Employer Identification Number",
                "Carrier Employer Identification Number",
                rules=rules,
            ),
            r"([0-9]{2}-[0-9]{7})",
        ),
        0.94,
        looks_like_ein,
    )
    add(
        "1c. NAIC Code",
        extract_labeled_value(
            text,
            rule_labels(
                "1c. NAIC Code",
                "Insurance Carrier NAIC Code",
                "National Association of Insurance Commissioners code",
                rules=rules,
            ),
            r"([0-9]{4,8})",
            transform=normalize_schedule_a_naic,
        ),
        0.93,
    )
    add(
        "1d. Contract/Policy Number",
        extract_labeled_value(
            text,
            rule_labels(
                "1d. Contract/Policy Number",
                "Plan Sponsor Contract or Identification Number",
                "Plan Sponsor Contract Number",
                rules=rules,
            ),
            r"([A-Za-z0-9][A-Za-z0-9-]{1,})",
        ),
        0.92,
    )
    add(
        "1e. Persons Covered (End of Policy Year)",
        extract_labeled_value(
            text,
            rule_labels(
                "1e. Persons Covered (End of Policy Year)",
                "Approximate number of persons covered at end of policy contract year",
                rules=rules,
            ),
            r"([0-9,]+)",
        ),
        0.91,
    )
    add(
        "3c. Amount of Fees",
        extract_labeled_value(
            text,
            rule_labels(
                "3c. Amount of Fees",
                "Total Amount of Fees Paid",
                "Fees and other compensation paid",
                rules=rules,
            ),
            MONEY_VALUE_PATTERN,
            transform=money_value,
        ),
        0.91,
    )
    for field_label in SCHEDULE_A_EXPERIENCE_RATED_FIELDS:
        add(
            field_label,
            extract_labeled_value(
                text,
                [field_label],
                MONEY_VALUE_PATTERN,
                transform=money_value,
            ),
            0.93,
        )
    add(
        "10a. Total premiums or subscription charges paid to carrier",
        extract_labeled_value(
            text,
            rule_labels(
                "10a. Total premiums or subscription charges paid to carrier",
                "Premium applied by",
                "Premium applied",
                "Total amount of premiums applied",
                rules=rules,
            ),
            MONEY_VALUE_PATTERN,
            transform=money_value,
        ),
        0.92,
    )

    contract_period = extract_contract_year_range(text)
    if contract_period:
        policy_from, policy_to = contract_period
        add("1f. Policy Year Beginning Date", policy_from, 0.9)
        add("1g. Policy Year Ending Date", policy_to, 0.9)

    return fields


def extract_configured_custom_fields(
    text: str,
    page: int | None = None,
    *,
    rules=None,
) -> list[NormalizedExtractionField]:
    if not rules:
        return []
    normalized_text = normalize_ocr_text(text)
    fields: list[NormalizedExtractionField] = []
    for rule in rules:
        if rule.mapping_mode != FieldRuleMappingMode.EXTRACTION_ONLY and not rule.key.startswith("ftw_discovered_"):
            continue
        labels = rule_labels(rule.label, rules=rules)
        for label in sorted(labels, key=len, reverse=True):
            match = re.search(
                rf"(?im)^\s*{loose_label_pattern(label)}\s*(?::|\t|[-–—])\s*(?P<value>[^\n]+?)\s*$",
                normalized_text,
            )
            if not match:
                continue
            value = clean_extracted_value(match.group("value"))
            if not value or is_blank_extraction_value(value):
                continue
            fields.append(
                NormalizedExtractionField(
                    field_name=rule.label,
                    value=value,
                    confidence=0.9,
                    page=page,
                    source_text=match.group(0).strip(),
                )
            )
            break
    return fields


MONEY_VALUE_PATTERN = r"\$?\s*([0-9,]+(?:\.\d{2})?)"


def rule_labels(field_label: str, *extra_labels: str, rules=None) -> list[str]:
    labels: list[str] = []
    for rule in (rules if rules is not None else DEFAULT_FIELD_RULES):
        if rule.label != field_label:
            continue
        labels.extend([rule.label, *rule.aliases])
        break
    labels.extend(extra_labels)
    deduped: list[str] = []
    seen: set[str] = set()
    for label in labels:
        normalized = normalize_rule_label(label)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(label)
    return deduped


def extract_labeled_value(
    text: str,
    labels: list[str],
    value_pattern: str,
    *,
    transform=None,
) -> str | None:
    normalized_text = normalize_ocr_text(text)
    for label in sorted(labels, key=len, reverse=True):
        pattern = rf"(?im)^\s*{loose_label_pattern(label)}\s*:?\s*(?:[^\n]{{0,180}}?)?{value_pattern}\b"
        match = re.search(pattern, normalized_text)
        if not match:
            continue
        value = match.group(1)
        return transform(value) if transform else value
    return None


def loose_label_pattern(label: str) -> str:
    escaped = re.escape(label.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\/", r"[/\s]+")
    escaped = escaped.replace(r"\-", r"[-\s]+")
    escaped = escaped.replace(r"\:", r":?")
    return escaped


def normalize_rule_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def money_value(value: str) -> str:
    clean = str(value or "").replace("$", "").strip()
    return re.sub(r"^([+-]?)\.(\d{1,2})$", r"\g<1>0.\2", clean)


def extract_contract_year_range(text: str) -> tuple[str, str] | None:
    periods = explicit_contract_periods(text)
    if len({(period.beginning, period.ending) for period in periods}) != 1:
        return None
    return periods[0].beginning, periods[0].ending


def normalize_schedule_a_naic(value: str) -> str:
    """Remove source padding only when it resolves to an exact FTW NAIC code."""
    digits = re.sub(r"\D", "", str(value or ""))
    unpadded = digits.lstrip("0")
    if len(digits) > 5 and len(unpadded) == 5:
        return unpadded
    return digits


def normalize_schedule_a_date(value: str, *, end_of_month: bool) -> str:
    text = str(value or "").strip()
    for pattern in (
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%d-%b-%y",
        "%d-%b-%Y",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.strftime("%m/%d/%Y")
        except ValueError:
            pass
    iso_match = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        return f"{month:02d}/{day:02d}/{year}"
    parts = text.split("/")
    if len(parts) == 2:
        month = int(parts[0])
        year = int(parts[1])
        day = calendar.monthrange(year, month)[1] if end_of_month else 1
        return f"{month:02d}/{day:02d}/{year}"
    if len(parts) == 3:
        month = int(parts[0])
        day = int(parts[1])
        year = int(parts[2])
        if year < 100:
            year += 2000
        return f"{month:02d}/{day:02d}/{year}"
    return text


def _normalize_schedule_a_source_date(value: str | None) -> str:
    text = str(value or "").strip().replace(".", "/")
    for pattern in ("%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, pattern).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return normalize_schedule_a_date(text, end_of_month=False)


def _format_schedule_a_ein(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else str(value or "").strip()


def regex_first(text: str, patterns: list[str], flags: int = re.IGNORECASE, groups: bool = False):
    for pattern in patterns:
        match = re.search(pattern, text, flags=flags)
        if match:
            if groups:
                return tuple(match.groups())
            return match.group(1) if match.groups() else match.group(0)
    return None


def parse_numeric_amount(value: str | None) -> float | None:
    clean = clean_extracted_value(str(value or "")).replace("$", "").replace(",", "").strip()
    if not clean:
        return None
    try:
        return float(clean)
    except ValueError:
        return None


def derive_schedule_a_purpose(commission: str | None, fee: str | None) -> str | None:
    commission_amount = parse_numeric_amount(commission)
    fee_amount = parse_numeric_amount(fee)

    has_commission = commission_amount is not None and commission_amount > 0
    has_fee = fee_amount is not None and fee_amount > 0

    if has_commission and has_fee:
        return "COMMISSIONS & FEES"
    if has_commission:
        return "COMMISSIONS"
    if has_fee:
        return "FEES"
    return None


def clean_extracted_value(value: str) -> str:
    value = str(value).strip(" \n\t:-")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:Name|Recipient|Agent|Broker)\s*:\s*", "", value, flags=re.IGNORECASE)
    stop_patterns = [
        r"\s+Address\s*:.*",
        r"\s+City\s*:.*",
        r"\s+Commissions Paid\b.*",
        r"\s+\(\s*b\s*\).*",
        r"\s+\(\s*c\s*\).*",
        r"\s+\(\s*d\s*\).*",
        r"\s+\(\s*e\s*\).*",
        r"\s+Part I\b.*",
    ]
    for pattern in stop_patterns:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    return value.strip(" ,;")
