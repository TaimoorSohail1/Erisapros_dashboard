"""Turn whatever a client sends into something the extractor can read.

Clients do not only send PDFs. Real intake folders contain Word documents,
Excel workbooks - including the pre-2007 .xls format some carriers still
produce - plain text, scans, and Outlook emails with the Schedule A sitting
inside as an attachment.

Two of those need work before extraction:

* An email is a wrapper. Sending the email itself to the extractor would
  extract the subject line and the signature block, not the Schedule A. The
  attachment inside is the actual document.
* Legacy .xls is a different binary format from .xlsx and is not among the
  file types the extractor accepts, so it is converted first.

Everything else is passed through untouched. When a conversion is not
possible the original file is returned with a note explaining why, so the
filing still reaches a human instead of disappearing.
"""
from __future__ import annotations

import email
import logging
import os
import re
from dataclasses import dataclass
from email import policy
from io import BytesIO

logger = logging.getLogger(__name__)

# File types the extraction engine (EyeLevel/GroundX) can read directly.
EXTRACTABLE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}
# Types we accept at intake and convert or unwrap on the way through.
CONVERTIBLE_EXTENSIONS = {".xls", ".msg", ".eml"}
# Everything a client may drop into a filing folder that we are willing to
# take. Anything outside this set is not a document we can file.
SUPPORTED_INTAKE_EXTENSIONS = EXTRACTABLE_EXTENSIONS | CONVERTIBLE_EXTENSIONS

EMAIL_EXTENSIONS = {".msg", ".eml"}
_SCHEDULE_A_NAME = re.compile(r"sched\w*\s*a\b|schedulea", re.IGNORECASE)


@dataclass
class IntakeDocument:
    """The document as the extractor should see it."""

    file_name: str
    file_bytes: bytes
    original_file_name: str | None = None
    conversion: str | None = None
    note: str | None = None

    @property
    def converted(self) -> bool:
        return bool(self.original_file_name and self.original_file_name != self.file_name)


def file_extension(file_name: str) -> str:
    return os.path.splitext(str(file_name or "").lower())[1]


def is_supported_intake_file(file_name: str) -> bool:
    return file_extension(file_name) in SUPPORTED_INTAKE_EXTENSIONS


def normalize_intake_document(file_name: str, file_bytes: bytes) -> IntakeDocument:
    """Return the file the extractor should actually receive."""
    extension = file_extension(file_name)

    if extension in EMAIL_EXTENSIONS:
        return _unwrap_email(file_name, file_bytes, extension)
    if extension == ".xls":
        return _convert_xls(file_name, file_bytes)
    return IntakeDocument(file_name=file_name, file_bytes=file_bytes)


# --- email ------------------------------------------------------------------


def _unwrap_email(file_name: str, file_bytes: bytes, extension: str) -> IntakeDocument:
    try:
        attachments = _read_eml_attachments(file_bytes) if extension == ".eml" else _read_msg_attachments(file_bytes)
    except Exception as exc:  # a malformed email must not break intake
        logger.warning("Could not read attachments from %s: %s", file_name, exc)
        return IntakeDocument(
            file_name=file_name,
            file_bytes=file_bytes,
            note=f"Email could not be opened ({type(exc).__name__}); needs manual handling.",
        )

    best = _best_attachment(attachments)
    if not best:
        return IntakeDocument(
            file_name=file_name,
            file_bytes=file_bytes,
            note="Email has no attachment that can be extracted; needs manual handling.",
        )

    attachment_name, attachment_bytes = best
    return IntakeDocument(
        file_name=attachment_name,
        file_bytes=attachment_bytes,
        original_file_name=file_name,
        conversion=f"Attachment taken from email {file_name}",
    )


def _read_eml_attachments(file_bytes: bytes) -> list[tuple[str, bytes]]:
    message = email.message_from_bytes(file_bytes, policy=policy.default)
    attachments: list[tuple[str, bytes]] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if not name:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append((_clean_name(name), payload))
    return attachments


def _read_msg_attachments(file_bytes: bytes) -> list[tuple[str, bytes]]:
    """Read attachments out of an Outlook .msg file.

    A .msg is an OLE compound file: each attachment is its own storage, with
    the bytes in the 3701 property stream and the file name in 3707 (long) or
    3704 (short).
    """
    import olefile

    attachments: list[tuple[str, bytes]] = []
    with olefile.OleFileIO(BytesIO(file_bytes)) as ole:
        entries = ole.listdir(streams=True, storages=True)
        storages = {
            entry[0]
            for entry in entries
            if entry and entry[0].startswith("__attach_version1.0")
        }
        for storage in sorted(storages):
            data = _read_msg_stream(ole, [storage, "__substg1.0_37010102"])
            if not data:
                continue
            name = (
                _read_msg_text(ole, [storage, "__substg1.0_3707001F"])
                or _read_msg_text(ole, [storage, "__substg1.0_3707001E"])
                or _read_msg_text(ole, [storage, "__substg1.0_3704001F"])
                or _read_msg_text(ole, [storage, "__substg1.0_3704001E"])
                or "attachment"
            )
            attachments.append((_clean_name(name), data))
    return attachments


def _read_msg_stream(ole, path: list[str]) -> bytes | None:
    try:
        if not ole.exists("/".join(path)):
            return None
        with ole.openstream(path) as stream:
            return stream.read()
    except Exception:
        return None


def _read_msg_text(ole, path: list[str]) -> str | None:
    raw = _read_msg_stream(ole, path)
    if not raw:
        return None
    encoding = "utf-16-le" if path[-1].endswith("001F") else "cp1252"
    try:
        return raw.decode(encoding, errors="ignore").strip("\x00").strip() or None
    except Exception:
        return None


def _best_attachment(attachments: list[tuple[str, bytes]]) -> tuple[str, bytes] | None:
    """Pick the attachment most likely to be the Schedule A.

    Signatures and logos ride along in most emails, so prefer a file the
    extractor can read, then a Schedule A-looking name, then the largest.
    """
    usable = [
        (name, data)
        for name, data in attachments
        if file_extension(name) in SUPPORTED_INTAKE_EXTENSIONS and data
    ]
    if not usable:
        return None
    named = [item for item in usable if _SCHEDULE_A_NAME.search(item[0])]
    pool = named or usable
    return max(pool, key=lambda item: len(item[1]))


def _clean_name(name: str) -> str:
    cleaned = os.path.basename(str(name or "").replace("\\", "/")).strip()
    return cleaned or "attachment"


# --- legacy Excel -----------------------------------------------------------


def _convert_xls(file_name: str, file_bytes: bytes) -> IntakeDocument:
    """Convert a pre-2007 .xls workbook to .xlsx.

    Carriers still send the old binary format, which the extractor does not
    accept. The cell grid is what matters for extraction, so the values are
    copied sheet by sheet into a modern workbook.
    """
    try:
        import xlrd
        from openpyxl import Workbook
    except ImportError as exc:
        logger.warning("Cannot convert %s: %s", file_name, exc)
        return IntakeDocument(
            file_name=file_name,
            file_bytes=file_bytes,
            note="Legacy .xls support is not installed; needs manual handling.",
        )

    try:
        book = xlrd.open_workbook(file_contents=file_bytes)
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet in book.sheets():
            target = workbook.create_sheet(title=(sheet.name or "Sheet")[:31])
            for row_index in range(sheet.nrows):
                for column_index in range(sheet.ncols):
                    value = sheet.cell_value(row_index, column_index)
                    if value == "":
                        continue
                    target.cell(row=row_index + 1, column=column_index + 1, value=value)
        if not workbook.sheetnames:
            workbook.create_sheet(title="Sheet1")
        buffer = BytesIO()
        workbook.save(buffer)
    except Exception as exc:
        logger.warning("Could not convert %s to xlsx: %s", file_name, exc)
        return IntakeDocument(
            file_name=file_name,
            file_bytes=file_bytes,
            note=f"Legacy .xls could not be converted ({type(exc).__name__}); needs manual handling.",
        )

    converted_name = f"{os.path.splitext(file_name)[0]}.xlsx"
    return IntakeDocument(
        file_name=converted_name,
        file_bytes=buffer.getvalue(),
        original_file_name=file_name,
        conversion=f"Converted from legacy Excel format ({file_name})",
    )
