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
  verified=result();repair_source_rows(verified,rows)
  self.assertEqual(next(f.value for f in verified.fields if f.field_name.startswith('3b.')),'120')
  for probe in [result(complete=False),result(wrong=True)]:
   repair_source_rows(probe,rows)
   self.assertEqual(next(f.value for f in probe.fields if f.field_name.startswith('3b.')),'100')

class ExistingFilingTests(unittest.IsolatedAsyncioTestCase):
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
