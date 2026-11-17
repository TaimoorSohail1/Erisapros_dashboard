import asyncio
import hashlib
from unittest.mock import patch

import pytest

from app.models import DocumentType, ExtractedField, ExtractedFieldStatus, ExtractionJob, Filing, FormType, RawExtraction
from app.repositories import MemoryRepository
from app.services.schedule_a_retained_period import refresh_retained_policy_end


LABEL = "DATE FOR PERIOD: FROM 02-01-2025 TO 02-01-2026"
SOURCE = "INSURANCE DATA FOR SCHEDULE A (FORM 5500)\n" + LABEL
BODY = b"retained PDF"
DIGEST = hashlib.sha256(BODY).hexdigest()


@pytest.mark.parametrize("read_path", ["native", "ocr"])
def test_refresh_corrects_old_anniversary_in_place(read_path):
    original, result, stored = asyncio.run(refresh(read_path=read_path))

    assert original[1].value == "02/01/2026"
    assert result[0] == original[0]
    assert stored.value == stored.proposed_value == "01/31/2026"
    assert stored.id == result[1].id == original[1].id
    assert stored.source_text == f"{LABEL}\nOriginal ending date 02/01/2026 exceeded 12 months; automatically limited to 01/31/2026."


@pytest.mark.parametrize("reason", ["different_hash", "conflicting_ocr", "one_mode", "concurrent_edit", "already_reviewed", "different_page"])
def test_refresh_preserves_unsafe_or_reviewed_dates(reason):
    original, result, stored = asyncio.run(refresh(read_path="ocr", issue=reason))

    assert result == original
    assert stored.value == "02/01/2026"
    if reason == "concurrent_edit":
        assert stored.proposed_value == "02/02/2026"


async def refresh(*, read_path, issue=None):
    repo = MemoryRepository()
    filing = Filing(id="filing", file_name="Schedule A.pdf", content_type="application/pdf", file_size=len(BODY),
                    s3_key="schedule-a", package_documents=[{"document_type": "SCHEDULE_A", "file_name": "Schedule A.pdf",
                                                              "s3_key": "schedule-a", "source_sha256": DIGEST}])
    fields = [ExtractedField(id=key, filing_id=filing.id, mapped_rule_key=f"schedule_a_part_i_{key}_policy_year_{label}_date",
                             source_field_name=label, normalized_field_name=label, value=value, proposed_value=value,
                             form_type=FormType.SCHEDULE_A, source_document_type=DocumentType.SCHEDULE_A,
                             source_text=LABEL, page=3)
              for key, label, value in [("1f", "beginning", "02/01/2025"), ("1g", "ending", "02/01/2026")]]
    if issue == "already_reviewed":
        fields[1].status = ExtractedFieldStatus.EDITED
    repo.fields.update({field.id: field.model_copy(deep=True) for field in fields})
    job = ExtractionJob(id="job", filing_id=filing.id, status="COMPLETED")
    repo.jobs[job.id] = job
    alternate = SOURCE.replace("02-01-2026", "02-05-2026") if issue == "conflicting_ocr" else SOURCE
    reads = [{"psm": 6, "page": 3, "source_sha256": DIGEST, "text": SOURCE}]
    if issue != "one_mode":
        reads.append({"psm": 4, "page": 4 if issue == "different_page" else 3, "source_sha256": DIGEST, "text": alternate})
    await repo.add_raw_extraction(RawExtraction(filing_id=filing.id, job_id=job.id, provider="retained",
        raw={"file_name": filing.file_name, "document_type": "SCHEDULE_A", "raw": {"local_ocr_source_reads": reads}}))
    if issue == "concurrent_edit":
        await repo.update_field(filing.id, "1g", "02/02/2026")
    with (patch("app.services.schedule_a_retained_period.StorageService.load_pdf", return_value=b"other" if issue == "different_hash" else BODY),
          patch("app.services.extractor.extract_pdf_layout_text_pages", return_value=[(3, SOURCE)] if read_path == "native" else [])):
        result = await refresh_retained_policy_end(repo, filing, fields)
    return fields, result, repo.fields["1g"]
