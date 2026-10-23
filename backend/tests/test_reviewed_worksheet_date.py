"""A recorded reviewer decision resolves only its own worksheet conflict."""
import asyncio

import app.repositories as repositories
from app.models import (
    DocumentType, ExtractedField, ExtractedFieldStatus, Filing, FormType,
    FTWFieldDecision, ScheduleAWorksheetSummary,
)
from app.services.ftwilliams_review import FTWilliamsReviewService, clear_ftw_current_snapshot_cache


def test_review_confirmation_survives_preview_refresh_without_hiding_other_conflicts(monkeypatch):
    async def exercise():
        repo = repositories.MemoryRepository()
        monkeypatch.setattr(repositories, "_repository", repo)
        filing = await repo.create_filing(Filing(
            file_name="disposable.pdf", content_type="application/pdf", file_size=1,
            document_type=DocumentType.SCHEDULE_A, s3_key="qa/disposable.pdf",
            schedule_a_worksheet_summaries=[ScheduleAWorksheetSummary(
                source="Plan Worksheet fully-insured benefit table", carrier_name="SAMPLE",
                account_number="123456", period_begin="02/01/2025", period_end="08/31/2025")],
        ))
        def field(suffix, value):
            return ExtractedField(
                filing_id=filing.id, source_field_name=suffix, normalized_field_name=suffix,
                mapped_rule_key="schedule_a_part_i_" + suffix, form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A, value=value,
                proposed_value=value, source_text=value, page=1,
            )
        fields = [field("1a_name_of_insurance_company", "SAMPLE"),
                  field("1d_contract_policy_number", "123456"),
                  field("1f_policy_year_beginning_date", "01/01/2025"),
                  field("1g_policy_year_ending_date", "12/31/2025")]
        await repo.add_fields(fields)
        service = FTWilliamsReviewService()
        first = await service.prepare_review(filing.id, send_queries=False, apply_automatic_derivations=False)
        ending = fields[-1]
        assert next(f for f in first.fields if f.field_id == ending.id).decision == FTWFieldDecision.CONFLICT
        await repo.update_field(filing.id, ending.id, "12/31/2025",
                                status=ExtractedFieldStatus.EDITED,
                                status_reason="Value confirmed by reviewer.")
        for _ in range(2):
            review = await service.prepare_review(filing.id, send_queries=False, apply_automatic_derivations=False)
            end = next(f for f in review.fields if f.field_id == ending.id)
            begin = next(f for f in review.fields if f.field_id == fields[-2].id)
            assert end.decision != FTWFieldDecision.CONFLICT
            assert "reviewer" in str(end.decision_reason).lower()
            assert begin.decision == FTWFieldDecision.CONFLICT
        saved = await repo.list_fields(filing.id)
        assert next(f for f in saved if f.id == ending.id).value == "12/31/2025"
    clear_ftw_current_snapshot_cache()
    try:
        asyncio.run(exercise())
    finally:
        clear_ftw_current_snapshot_cache()
