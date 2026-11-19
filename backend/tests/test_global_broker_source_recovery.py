import hashlib
import unittest
from unittest.mock import patch
from app.models import Filing,RawExtraction,NormalizedExtractionResult,NormalizedExtractionField,ScheduleABrokerRow,SourceEvidence,ExtractionValidationResult
from app.repositories import MemoryRepository
from app.services.extractor import extract_pdf_layout_text_pages,extract_schedule_a_broker_rows_from_document
from app.services.schedule_a_closed_source import closed_native_roster,proof_matches
from app.services.schedule_a_broker_roster import source_roster_review,attest_source_broker_roster,separate_service_provider_disclosures
from app.services.schedule_a_broker_source_repair import repair_source_rows
from broker_source_fixture import source_pdf,ruled_source_pdf

class ClosedSourceTests(unittest.TestCase):
 def test_identical_physical_pages_do_not_double_broker_payments(self):
  from io import BytesIO
  from pypdf import PdfReader,PdfWriter
  reader=PdfReader(BytesIO(source_pdf()));writer=PdfWriter()
  writer.add_page(reader.pages[0]);writer.add_page(reader.pages[0]);out=BytesIO();writer.write(out)
  coverage={};rows=extract_schedule_a_broker_rows_from_document(out.getvalue(),'duplicate.pdf',page_coverage=coverage)
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0].commission_total,'100')
  self.assertEqual(coverage['page_count'],2)
  self.assertEqual(coverage['pages'][1]['duplicate_of'],1)

 def test_different_drawing_streams_with_shared_resources_are_not_duplicates(self):
  from io import BytesIO
  from pypdf import PdfReader,PdfWriter
  from pypdf.generic import NameObject,DecodedStreamObject
  reader=PdfReader(BytesIO(source_pdf()));writer=PdfWriter()
  writer.add_page(reader.pages[0]);second=writer.add_page(reader.pages[0])
  stream=DecodedStreamObject();stream.set_data(second.get_contents().get_data().replace(b'Example Brokerage LLC',b'Other Brokerage LLC'))
  second[NameObject('/Contents')]=writer._add_object(stream)
  out=BytesIO();writer.write(out);coverage={}
  rows=extract_schedule_a_broker_rows_from_document(out.getvalue(),'different.pdf',page_coverage=coverage)
  self.assertEqual({r.name for r in rows},{'Example Brokerage LLC','Other Brokerage LLC'})
  self.assertFalse(any('duplicate_of' in p for p in coverage['pages']))

 def test_pixel_confirmation_handles_redundant_drawing_but_keeps_visual_changes(self):
  from io import BytesIO
  from pypdf import PdfReader,PdfWriter
  from pypdf.generic import NameObject,DecodedStreamObject
  for extra,expected in [(b'0 0 0 rg\n',1),(b'0 0 0 rg 500 20 5 5 re f\n',2)]:
   reader=PdfReader(BytesIO(source_pdf()));writer=PdfWriter()
   writer.add_page(reader.pages[0]);second=writer.add_page(reader.pages[0])
   stream=DecodedStreamObject();stream.set_data(extra+second.get_contents().get_data())
   second[NameObject('/Contents')]=writer._add_object(stream)
   out=BytesIO();writer.write(out);coverage={}
   rows=extract_schedule_a_broker_rows_from_document(out.getvalue(),'rendered.pdf',page_coverage=coverage)
   self.assertEqual(len(rows),expected)
   self.assertEqual('duplicate_of' in coverage['pages'][1],expected==1)

 def test_incomplete_vendor_query_cannot_be_reported_as_a_different_policy(self):
  from app.models import FTWilliamsReview
  from app.services.ftwilliams_review import FTWilliamsReviewService
  service=FTWilliamsReviewService()
  review=FTWilliamsReview(filing_id='qa',current_query_success=True,current_query_complete=False,
    ftw_editable=True,schedule_a_records=[{'ftw_seq_no':'1','query_results':{'InsContractNum':'OTHER'}}])
  with patch.object(service,'_top_schedule_a_matches',side_effect=AssertionError('Partial records must not be ranked')):
   self.assertIn('Refresh',service._broker_review_destination_error(review,[object()]))

 def test_ruled_cells_and_pdfium_corroborate_without_double_counting_grand_total(self):
  content=ruled_source_pdf();coverage={}
  rows=extract_schedule_a_broker_rows_from_document(content,'carrier.pdf',page_coverage=coverage)
  closed,proof=closed_native_roster(content,extract_pdf_layout_text_pages(content),coverage)
  self.assertEqual(len(rows),1);self.assertIsNotNone(proof)
  self.assertEqual((closed[0].commission_total,closed[0].fee_total,closed[0].organization_code),('100','20','3'))
  # The printed agent number is an identifier, never an organization code.
  broken=ruled_source_pdf(total='900.00');coverage={}
  extract_schedule_a_broker_rows_from_document(broken,'carrier.pdf',page_coverage=coverage)
  self.assertIsNone(closed_native_roster(broken,extract_pdf_layout_text_pages(broken),coverage)[1])

 def test_two_independent_readers_verify_legacy_grid_without_provider_history(self):
  content=source_pdf();coverage={}
  rows=extract_schedule_a_broker_rows_from_document(content,'any-carrier.pdf',page_coverage=coverage)
  closed,proof=closed_native_roster(content,extract_pdf_layout_text_pages(content),coverage)
  self.assertEqual(len(rows),1);self.assertIsNotNone(proof)
  self.assertEqual((closed[0].name,closed[0].city,closed[0].commission_total,closed[0].fee_total),('Example Brokerage LLC','BOSTON','100','20'))
  self.assertTrue(proof_matches(proof,closed,coverage,hashlib.sha256(content).hexdigest()))
  self.assertFalse(proof_matches(proof,closed,coverage,'a'*64))
  changed=[closed[0].model_copy(update={'fee_total':'200'})]
  self.assertFalse(proof_matches(proof,changed,coverage,hashlib.sha256(content).hexdigest()))
 def test_category_discrepancy_and_schedule_c_cannot_be_closed_grid_proof(self):
  for content in (source_pdf(fee='200.00'),source_pdf(schedule_c=True)):
   coverage={};extract_schedule_a_broker_rows_from_document(content,'any.pdf',page_coverage=coverage)
   rows,proof=closed_native_roster(content,extract_pdf_layout_text_pages(content),coverage)
   self.assertIsNone(proof)
 def test_service_provider_heading_on_schedule_a_is_not_a_schedule_c_disclosure(self):
  row=ScheduleABrokerRow(name='Example Brokerage LLC',source_page=1,commission_total='100',fee_total='20')
  result=NormalizedExtractionResult(provider='test',fields=[],schedule_a_broker_rows=[row],raw={'service_provider_disclosure_pages':[1]})
  pages=extract_pdf_layout_text_pages(source_pdf());separate_service_provider_disclosures(result,pages)
  self.assertEqual(len(result.schedule_a_broker_rows),1)
  self.assertEqual(result.raw['service_provider_disclosure_pages'],[])
 def test_recovery_keeps_human_identity_and_payment_edits(self):
  row=ScheduleABrokerRow(name='Human correction',commission_total='99',fee_total='0',source_page=1,
   validation_results=[ExtractionValidationResult(validator='reviewer_broker_identity_confirmation',status='PASS',normalized_value='x')])
  source=ScheduleABrokerRow(name='Original Broker',commission_total='100',fee_total='0',source_page=1,evidence=[SourceEvidence(page=1,source_text='Original Broker 100')])
  result=NormalizedExtractionResult(provider='test',fields=[],schedule_a_broker_rows=[row],raw={'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[source.model_dump(mode='json')]}]})
  self.assertFalse(repair_source_rows(result,[source]));self.assertEqual(result.schedule_a_broker_rows,[row])

 def test_full_classified_detail_recovers_stale_summary_but_not_partial_or_different_payments(self):
  from app.services.schedule_a_native_brokers import mark_payment_columns
  rows=[mark_payment_columns(ScheduleABrokerRow(name=name,commission_total=amount,fee_total='0',
    source_page=1,confidence=.99,evidence=[SourceEvidence(page=1,source_text=name+' Commissions '+amount)]))
    for name,amount in [('First Brokerage','100'),('Second Brokerage','20')]]
  def result(complete=True,wrong=False):
   provider=[r.model_dump(mode='json') for r in rows]
   if wrong:provider[1]['commission_total']='900'
   return NormalizedExtractionResult(provider='test',fields=[NormalizedExtractionField(field_name='3b. Amount of Commissions',value='100'),
    NormalizedExtractionField(field_name='3c. Amount of Fees',value='0')],schedule_a_broker_rows=[r.model_copy(deep=True) for r in rows],raw={
     'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':provider}],
     'independent_source_page_coverage':{'page_count':2,'pages':[{'page':1,'readable':True}]+([{'page':2,'readable':True}] if complete else [])}})
  # Readable pages and matching named recipients do not prove that every
  # payment was assigned. A decoder must also establish a closed roster.
  unclosed=result();repair_source_rows(unclosed,rows)
  self.assertEqual(next(f.value for f in unclosed.fields if f.field_name.startswith('3b.')),'100')
  from app.services.schedule_a_native_brokers import mark_closed_roster
  closed=mark_closed_roster([r.model_copy(deep=True) for r in rows],'All monetary cells accounted for')
  verified=result();repair_source_rows(verified,closed)
  self.assertEqual(next(f.value for f in verified.fields if f.field_name.startswith('3b.')),'120')
  for probe in [result(complete=False),result(wrong=True)]:
   repair_source_rows(probe,closed)
   self.assertEqual(next(f.value for f in probe.fields if f.field_name.startswith('3b.')),'100')

 def test_unassigned_printed_fee_cannot_be_replaced_by_zero(self):
  from app.services.extractor import extract_guardian_broker_rows
  from app.services.schedule_a_native_brokers import provider_supports_labelled_payments
  pages=[(1,'''Guardian Schedule A/5500 EIN 13-5123390
000AB123 EXAMPLE BROKER LLC $100.00
123 MAIN ST BOSTON MA 02110
Total commissions for plan $100.00
Total Fees Paid $25.00''')]
  rows=extract_guardian_broker_rows(pages)
  self.assertEqual(len(rows),1)
  self.assertTrue(any(v.status=='ERROR' for v in rows[0].validation_results))
  # Even a provider that also omitted the fee cannot corroborate the omission.
  original=rows[0].model_copy(update={'decision':'AUTOMATIC','validation_results':[]})
  result=NormalizedExtractionResult(provider='test',fields=[NormalizedExtractionField(field_name='3b. Amount of Commissions',value='100'),
   NormalizedExtractionField(field_name='3c. Amount of Fees',value='25')],schedule_a_broker_rows=[original],raw={'extraction_stages':[
    {'stage':'structured_provider_normalized','broker_rows':[original.model_dump(mode='json')],
     'fields':[{'field_name':'3b. Amount of Commissions','value':'100'},{'field_name':'3c. Amount of Fees','value':'0'}]}]})
  self.assertFalse(provider_supports_labelled_payments(result,rows))
  repair_source_rows(result,rows)
  self.assertEqual(next(f.value for f in result.fields if f.field_name.startswith('3c.')),'25')
  # A reviewer can resolve the allocation through the ordinary reconciliation
  # flow; the new source check must not create an unresolvable UI-only error.
  from decimal import Decimal
  from app.models import BrokerRosterReviewRequest,ScheduleABrokerRosterReview
  from app.services.broker_review_confirmation import confirm_broker_review,review_token
  status=ScheduleABrokerRosterReview();draft=[rows[0].model_copy(update={'fee_total':'25'})]
  request=BrokerRosterReviewRequest(expected_context=review_token(rows,status,'source','snapshot'),
    complete_list_confirmed=True,reason='Verified fee allocation against source',row_sources=['SOURCE:0'])
  corrected,reviewed=confirm_broker_review(draft,rows,status,request,actor='test',source_context='source',snapshot='snapshot',
    expected_payment_totals=(Decimal('100'),Decimal('0')))
  self.assertIsNotNone(reviewed.reviewer_confirmation)
  self.assertFalse(any(v.status=='ERROR' for v in corrected[0].validation_results))
  with self.assertRaisesRegex(ValueError,'must match both'):
   confirm_broker_review(rows,rows,status,request,actor='test',source_context='source',snapshot='snapshot',
    expected_payment_totals=(Decimal('100'),Decimal('0')))

 def test_legacy_automatic_proof_requires_new_payment_verification(self):
  from app.services.schedule_a_broker_roster import broker_roster_is_complete
  content=source_pdf();coverage={};rows=extract_schedule_a_broker_rows_from_document(content,'carrier.pdf',page_coverage=coverage)
  closed,proof=closed_native_roster(content,extract_pdf_layout_text_pages(content),coverage)
  result=NormalizedExtractionResult(provider='test',fields=[],schedule_a_broker_rows=closed,raw={'independent_source_page_coverage':coverage})
  from app.services.schedule_a_broker_source_repair import set_corroborated_totals
  set_corroborated_totals(result,closed,corroboration='two source reads')
  attest_source_broker_roster(result,closed,hashlib.sha256(content).hexdigest(),native_proof=proof)
  self.assertTrue(broker_roster_is_complete(result.schedule_a_broker_rows))
  for row in result.schedule_a_broker_rows:
   for value in row.validation_results:
    if value.validator=='complete_source_broker_roster':value.normalized_value=value.normalized_value.removesuffix(':v2')
  self.assertFalse(broker_roster_is_complete(result.schedule_a_broker_rows))

class ExistingFilingTests(unittest.IsolatedAsyncioTestCase):
 async def test_unbound_legacy_mismatch_remains_review_only_with_printed_totals(self):
  from app.models import ExtractedField,FormType
  from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
  content=source_pdf(fee='25.00');sha=hashlib.sha256(content).hexdigest();repo=MemoryRepository()
  f=await repo.create_filing(Filing(file_name='legacy.pdf',content_type='application/pdf',file_size=len(content),s3_key='source',
   package_documents=[{'document_type':'SCHEDULE_A','file_name':'legacy.pdf','source_sha256':sha,'s3_key':'source'}]))
  fields=await repo.add_fields([ExtractedField(filing_id=f.id,source_field_name='3c. Amount of Fees',normalized_field_name='fees',
   mapped_label='3c. Amount of Fees',form_type=FormType.SCHEDULE_A,value='0',proposed_value='0',confidence=.99)])
  with patch('app.services.storage.StorageService.load_pdf',return_value=content):updated,after=await verify_stored_schedule_a_source(repo,f,fields)
  self.assertEqual((after[0].value,after[0].proposed_value,after[0].status.value),('25','','LOW_CONFIDENCE'))
  self.assertEqual(updated.schedule_a_broker_roster.mode,'PRESERVE')
  self.assertIsNone(updated.schedule_a_broker_roster.reviewer_confirmation)
  self.assertTrue(any(v.normalized_value=='source_totals:100:25' for row in updated.schedule_a_broker_rows for v in row.validation_results))

 async def test_unverified_automatic_summary_is_withdrawn_and_original_value_restored(self):
  from app.models import ExtractedField,FormType
  from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
  content=source_pdf(fee='25.00');sha=hashlib.sha256(content).hexdigest();repo=MemoryRepository()
  row=ScheduleABrokerRow(name='Example Brokerage LLC',address_line_1='123 MAIN ST',city='BOSTON',state='MA',zip_code='02110',
    commission_total='100',fee_total='20',source_page=1,confidence=.99)
  f=await repo.create_filing(Filing(file_name='legacy.pdf',content_type='application/pdf',file_size=len(content),s3_key='source',
   package_documents=[{'document_type':'SCHEDULE_A','file_name':'legacy.pdf','source_sha256':sha,'s3_key':'source'}],schedule_a_broker_rows=[row]))
  fields=await repo.add_fields([ExtractedField(filing_id=f.id,source_field_name='3c. Amount of Fees',normalized_field_name='fees',
   mapped_label='3c. Amount of Fees',form_type=FormType.SCHEDULE_A,value='0',proposed_value='0',confidence=.99,
   source_text='Verified sum of full labelled recipient payment columns; older proof',
   status_reason='Category sum recovered from labelled physical rows and independent source corroboration.')])
  await repo.add_raw_extraction(RawExtraction(filing_id=f.id,provider='provider',raw={'file_name':'legacy.pdf','document_type':'SCHEDULE_A',
   'schedule_a_worksheet_summaries':[],'raw':{'independent_source_page_coverage':{'source_sha256':sha},'extraction_stages':[
    {'stage':'structured_provider_normalized','broker_rows':[row.model_dump(mode='json')]},
    {'stage':'final_normalized','fields':[{'field_name':'3b. Amount of Commissions','value':'100'},
                                       {'field_name':'3c. Amount of Fees','value':'25'}]}]}}))
  with patch('app.services.storage.StorageService.load_pdf',return_value=content):updated,after=await verify_stored_schedule_a_source(repo,f,fields)
  self.assertEqual(after[0].value,'25')
  self.assertEqual(after[0].proposed_value,'')
  self.assertEqual(after[0].status.value,'LOW_CONFIDENCE')
  self.assertNotEqual(getattr(updated.schedule_a_broker_roster,'mode',None),'REPLACE')

 async def test_existing_filing_recovers_without_reupload_or_fake_approval(self):
  from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
  content=source_pdf();sha=hashlib.sha256(content).hexdigest();repo=MemoryRepository()
  f=await repo.create_filing(Filing(file_name='legacy.pdf',content_type='application/pdf',file_size=len(content),s3_key='source',
   package_documents=[{'document_type':'SCHEDULE_A','file_name':'legacy.pdf','source_sha256':sha,'s3_key':'source'}]))
  with patch('app.services.storage.StorageService.load_pdf',return_value=content):
   updated,fields=await verify_stored_schedule_a_source(repo,f,[])
  self.assertEqual(updated.schedule_a_broker_roster.mode,'REPLACE')
  self.assertIsNone(updated.schedule_a_broker_roster.reviewer_confirmation)
  self.assertEqual(updated.schedule_a_broker_rows[0].fee_total,'20')
  self.assertEqual(updated.package_documents,f.package_documents)
  self.assertEqual(len(repo.raw_extractions),0)
  self.assertTrue(repo.audit[0].details['broker_roster_verified'])
