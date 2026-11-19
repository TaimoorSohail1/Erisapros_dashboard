import asyncio
import hashlib
from io import BytesIO
from unittest.mock import patch
from openpyxl import Workbook
from app.models import Filing,ExtractedField,FormType,ExtractedFieldStatus
from app.repositories import MemoryRepository
from app.services.schedule_a_workbook_source import closed_workbook_roster,verify_stored_workbook_source

def source(total=100,detail=100):
    book=Workbook();sheet=book.active;sheet.title='Carrier form'
    for row in [('1(d)','Contract','POLICY-1'),('2(a)','Commission total',total),('2(b)','Fee total',0),
                ('3(a)','Recipient','Example Brokerage LLC\n160 FEDERAL STREET\n4th FLOOR\nBOSTON, MA 02110'),
                ('3(b)','Commission',detail),('3(c)','Fees','$0.00'),('3(e)','Code',3)]:sheet.append(row)
    b=BytesIO();book.save(b);return b.getvalue()

def test_independent_cells_agree_with_both_totals_and_complete_address():
    contract,rows,proof,coverage=closed_workbook_roster(source())
    row,=rows
    assert contract=='POLICY-1' and row.commission_total=='100' and row.fee_total=='0'
    assert (row.address_line_1,row.address_line_2,row.city,row.state,row.zip_code)==('160 FEDERAL STREET','4th FLOOR','BOSTON','MA','02110')
    assert row.organization_code=='3'

def test_mismatched_printed_total_cannot_verify_list():
    assert closed_workbook_roster(source(total=101)) is None

def test_missing_formula_cache_cannot_verify_list():
    assert closed_workbook_roster(source(detail='=50+50')) is None

def test_all_sheets_checked_and_duplicate_policy_grid_not_assumed_same():
    from openpyxl import load_workbook
    book=load_workbook(BytesIO(source()));book.copy_worksheet(book.active);b=BytesIO();book.save(b)
    assert closed_workbook_roster(b.getvalue()) is None

def test_stored_workbook_recovers_real_broker_without_reviewer_confirmation():
    async def run():
        content=source();repo=MemoryRepository()
        document={'document_type':'SCHEDULE_A','file_name':'any-carrier.xlsx','s3_key':'stored','source_sha256':hashlib.sha256(content).hexdigest()}
        filing=await repo.create_filing(Filing(file_name='any-carrier.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',file_size=len(content),s3_key='stored',package_documents=[document]))
        fields=await repo.add_fields([ExtractedField(filing_id=filing.id,source_field_name='Contract',normalized_field_name='contract',mapped_rule_key='schedule_a_part_i_1d_contract_policy_number',value='POLICY-1',proposed_value='POLICY-1',form_type=FormType.SCHEDULE_A)])
        with patch('app.services.storage.StorageService.load_pdf',return_value=content):
            saved,fields=await verify_stored_workbook_source(repo,filing,fields,document)
        assert len(saved.schedule_a_broker_rows)==1
        assert saved.schedule_a_broker_roster.mode=='REPLACE'
        assert not saved.schedule_a_broker_roster.reviewer_confirmation
        assert saved.package_documents==filing.package_documents
    asyncio.run(run())
