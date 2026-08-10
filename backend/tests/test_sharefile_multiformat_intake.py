"""End to end at intake: a non-PDF Schedule A must reach a filing.

The failed acceptance test TC-001 was three files that never appeared on the
dashboard: a Word document, an Outlook email, and a legacy Excel workbook -
all sitting in the correct Schedule A's folder. Word already worked (it is
classified by reading its contents); Excel and email did not.
"""
import email.message
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from app.models import DocumentType, ShareFileOAuthToken
from app.repositories import MemoryRepository
from app.services.sharefile import ShareFileService


def _folder(id_, name):
    return {"Id": id_, "Name": name, "odata.type": "ShareFile.Api.Models.Folder"}


def _file(id_, name):
    return {
        "Id": id_,
        "Name": name,
        "odata.type": "ShareFile.Api.Models.File",
        "FileSizeBytes": 5000,
        "ClientModifiedDate": "2026-08-07T10:00:00Z",
        "CreationDate": "2026-08-07T10:00:00Z",
    }


def _eml_with_pdf() -> bytes:
    message = email.message.EmailMessage()
    message["Subject"] = "RE: [EXT]AlphaSights Schedule A - Health Advocate"
    message["From"] = "carrier@example.com"
    message["To"] = "filings@erisapros.com"
    message.set_content("Attached.")
    message.add_attachment(
        b"%PDF-1.4 SCHEDULE A Insurance Information carrier premium commissions",
        maintype="application",
        subtype="pdf",
        filename="Health Advocate Schedule A.pdf",
    )
    return message.as_bytes()


def _eml_with_values_and_pdf() -> bytes:
    message = email.message.EmailMessage()
    message["Subject"] = "Schedule A values and supporting statement"
    message["From"] = "carrier@example.com"
    message["To"] = "filings@erisapros.com"
    message.set_content(
        "Legal Name: Health Advocate Solutions, Inc.\n"
        "EIN: 23-3080019\n"
        "Persons covered: 490\n"
        "Fees paid: $5,134.75\n"
    )
    message.add_attachment(
        b"%PDF-1.4 supporting Schedule A",
        maintype="application",
        subtype="pdf",
        filename="Health Advocate Schedule A.pdf",
    )
    return message.as_bytes()


def _legacy_xls() -> bytes:
    import xlwt

    book = xlwt.Workbook()
    sheet = book.add_sheet("Schedule A")
    sheet.write(0, 0, "Schedule A - The International Group, Inc.")
    sheet.write(1, 0, "Contract Number")
    sheet.write(1, 1, "187319")
    sheet.write(2, 0, "Total Premium")
    sheet.write(2, 1, 125000.5)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class MultiFormatIntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.repo_patch = patch("app.services.sharefile.get_repository", return_value=self.repo)
        self.repo_patch.start()
        await self.repo.upsert_sharefile_token(
            ShareFileOAuthToken(
                access_token="fake",
                refresh_token="fake",
                subdomain="erisapros",
                apicp="sf-api.com",
                appcp="sharefile.com",
                expires_at=datetime.utcnow() + timedelta(hours=8),
            )
        )

        self.tree = {
            "allshared": [_folder("f_c", "The International Group TEST")],
            "f_c": [_folder("f_5500", "5500 Filing")],
            "f_5500": [_folder("f_2025", "2025 Filing")],
            "f_2025": [
                _folder("f_sa", "Schedule A's"),
                _file("d_ws", "5500 Plan Worksheet - The International Group, Inc. 5500 - PY25.docx"),
            ],
            "f_sa": [],
        }
        self.payloads = {
            "d_ws": b"PK fake docx worksheet",
        }

        outer = self

        async def fake_list_folder(_self, client, token, folder_id):
            return list(outer.tree.get(folder_id) or [])

        async def fake_download(_self, client, token, item_id):
            return outer.payloads.get(item_id, b"%PDF-1.4 fallback")

        self.patches = [
            patch.object(ShareFileService, "_list_folder", fake_list_folder),
            patch.object(ShareFileService, "_download_item", fake_download),
            patch.object(ShareFileService, "_should_content_sniff", lambda _self, name: False),
            patch.object(
                ShareFileService,
                "_ensure_access_token",
                new=AsyncMock(side_effect=lambda c, t: t),
            ),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        self.repo_patch.stop()

    async def _baseline(self, service):
        await service.sync_changes(BackgroundTasks(), process_new_files=True)

    async def _documents_for_latest_filing(self):
        filings = await self.repo.list_filings()
        self.assertTrue(filings, "expected a filing to be created")
        return filings[0].package_documents or []

    async def test_legacy_xls_schedule_a_becomes_a_filing_as_xlsx(self):
        try:
            import xlwt  # noqa: F401
        except ImportError:
            self.skipTest("xlwt is only needed to author the .xls fixture")

        service = ShareFileService()
        await self._baseline(service)

        name = "1. The Interational Group Inc _187319_01-01-25 thru 12-31-25_Schedule A ALIC Form.xls"
        self.tree["f_sa"].append(_file("d_xls", name))
        self.payloads["d_xls"] = _legacy_xls()

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("synced"), 1)

        documents = await self._documents_for_latest_filing()
        schedule = next(d for d in documents if d["document_type"] == DocumentType.SCHEDULE_A.value)
        # Stored as a workbook the extractor can read, with the original
        # carrier file name kept for traceability.
        self.assertTrue(schedule["file_name"].endswith(".xlsx"))
        self.assertEqual(schedule["source_file_name"], name)
        self.assertIn("legacy", (schedule["intake_conversion"] or "").lower())

    async def test_email_schedule_a_is_filed_as_its_attachment(self):
        service = ShareFileService()
        await self._baseline(service)

        name = "4. RE_ [EXT]AlphaSights Schedule A - Health Advocate.eml"
        self.tree["f_sa"].append(_file("d_eml", name))
        self.payloads["d_eml"] = _eml_with_pdf()

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("synced"), 1)

        documents = await self._documents_for_latest_filing()
        schedule = next(d for d in documents if d["document_type"] == DocumentType.SCHEDULE_A.value)
        self.assertEqual(schedule["file_name"], "Health Advocate Schedule A.pdf")
        self.assertEqual(schedule["source_file_name"], name)
        self.assertEqual(schedule["content_type"], "application/pdf")

    async def test_email_body_and_real_attachment_are_both_filed(self):
        service = ShareFileService()
        await self._baseline(service)

        name = "Health Advocate Schedule A values.eml"
        self.tree["f_sa"].append(_file("d_email_values", name))
        self.payloads["d_email_values"] = _eml_with_values_and_pdf()

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("synced"), 1)

        documents = await self._documents_for_latest_filing()
        self.assertEqual(
            {document["file_name"] for document in documents if document["document_type"] == DocumentType.SCHEDULE_A.value},
            {"Health Advocate Schedule A.pdf", "Health Advocate Schedule A values email body.txt"},
        )

    async def test_spreadsheet_and_scan_in_a_schedule_a_folder_are_classified(self):
        service = ShareFileService()
        for name in (
            "1. Carrier workbook.xlsx",
            "2. Scanned schedule.png",
            "3. Export.csv",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    service._classify_sharefile_document(name, ["Client", "5500 Filing", "2025 Filing", "Schedule A's", name]),
                    DocumentType.SCHEDULE_A,
                )

    async def test_non_documents_are_still_ignored(self):
        service = ShareFileService()
        for name in ("archive.zip", "thumbs.db", "walkthrough.mp4"):
            with self.subTest(name=name):
                self.assertIsNone(
                    service._classify_sharefile_document(name, ["Client", "5500 Filing", "2025 Filing", "Schedule A's", name])
                )

    async def test_cover_pages_are_still_excluded_in_every_format(self):
        service = ShareFileService()
        for name in ("cover sheet.xlsx", "signed acknowledgement.png"):
            with self.subTest(name=name):
                self.assertIsNone(
                    service._classify_sharefile_document(name, ["Client", "5500 Filing", "2025 Filing", "Schedule A's", name])
                )


if __name__ == "__main__":
    unittest.main()
