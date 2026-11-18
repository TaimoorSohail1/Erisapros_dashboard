from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.extractor import _extract_workbook_broker_rows, extract_document_text_pages, local_schedule_a_pdf_result
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for rows in sheets:
        sheet = workbook.create_sheet()
        for row in rows:
            sheet.append(row)
    with BytesIO() as stream:
        workbook.save(stream)
        return stream.getvalue()


def test_broker_workbook_preserves_coverage_columns_and_cents():
    content = workbook_bytes([
        [["Blue Cross Blue Shield of Massachusetts, Inc."], ["#5500A WORKSHEET"]],
        [[None, None, None, None, "MEDICAL", "DENTAL", "SENIOR", "VISION"],
         [None, "COMMISSION BREAKDOWN"],
         [None, "Example Brokerage LLC", None, None, 0, 3481.37, 0, 630.95],
         [],
         [None, "OTHER COMMISSION"],
         [None, "All accounts", None, None, 0, 850.50, 0, 200.25]],
    ])
    parsed = resolve_schedule_a_result(local_schedule_a_pdf_result(content, "worksheet.xlsx"))
    row, = parsed.schedule_a_broker_rows
    assert row.source_page == 2
    assert row.commission_total == "4112.32"
    assert [(component.coverage, component.amount) for component in row.commission_rows] == [
        ("Dental", "3481.37"), ("Vision", "630.95"),
    ]
    assert row.decision == "REVIEW_REQUIRED"
    assert any(result.validator == "business_rule_confirmation" and result.status == "ERROR" for result in row.validation_results)


@pytest.mark.parametrize("amounts", [(0, None, 0, 6.25), (0, True, 0, 6.25), (0, 4.123, 0, 6.25)])
def test_incomplete_or_invalid_workbook_cells_do_not_shift_coverage(amounts):
    content = workbook_bytes([
        [["Blue Cross Blue Shield of Massachusetts"], ["#5500A WORKSHEET"]],
        [[None, None, "DENTAL", "VISION", "MEDICAL", "SENIOR"], [None, "COMMISSION BREAKDOWN"],
         [None, "Example Brokerage LLC", *amounts]],
    ])
    assert _extract_workbook_broker_rows(content, "worksheet.xlsx", extract_document_text_pages(content, "worksheet.xlsx")) == []


def test_workbook_group_summary_keeps_listed_recipient_and_reported_amount():
    content = workbook_bytes([
        [["Schedule A Information"], ["Group Number", "ACCOUNT-1"],
         ["Broker", "Example Brokerage LLC"], ["Commission Paid", 39885]],
        [["Broker Name", "Group Number", "Amount"], ["Another Agency LLC", "OTHER-1", 39885],
         ["Independent Partner LLC", "OTHER-2", 39885]],
    ])
    result = resolve_schedule_a_result(local_schedule_a_pdf_result(content, "worksheet.xlsx"))
    row, = result.schedule_a_broker_rows
    assert row.name == "Example Brokerage LLC"
    assert row.commission_total == "39885.00"
    assert row.source_page == 1 and row.decision == "REVIEW_REQUIRED"
