from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.models import FormType, NormalizedExtractionField, NormalizedExtractionResult
from app.services.extractor import ExtractionService
from app.services.mapping import map_extraction_to_rules


def document(*, overlay):
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    commands=['BT /F1 10 Tf 25 700 Td (Approximate number of persons covered at end of policy year:) Tj ET',
              'BT /F1 10 Tf 500 700 Td (358) Tj ET']
    if overlay is not None:
        commands.extend(['1 1 1 rg 499 697 24 15 re f 0 0 0 rg',f'BT /F1 10 Tf 500 700 Td ({overlay}) Tj ET'])
    content=DecodedStreamObject()
    content.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(content)
    stream=BytesIO()
    writer.write(stream)
    return stream.getvalue()


class NativeConflictIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def extract(self,pdf,provider_value):
        field=NormalizedExtractionField(field_name='1e. Persons Covered (End of Policy Year)',value='358',
            confidence=.99,page=1,source_text='Approximate number of persons covered: 358')
        result=NormalizedExtractionResult(provider='Recorded provider boundary',fields=[field],raw={
            'extraction_stages':[{'stage':'structured_provider_normalized','fields':[
                field.model_copy(update={'value':provider_value}).model_dump(mode='json')],'broker_rows':[]}]})
        service=ExtractionService([])
        with patch.object(service,'_extract_schedule_a_unresolved',AsyncMock(return_value=result)), \
             patch('app.services.extractor.get_settings',return_value=Settings(_env_file=None,
                schedule_a_canonical_validation_enabled=True,schedule_a_canonical_validation_shadow_enabled=False)):
            return await service.extract_schedule_a(pdf,'synthetic-count.pdf')

    async def test_overlaid_native_count_cannot_erase_provider_disagreement(self):
        result=await self.extract(document(overlay='359'),'359')
        fields=[f for f in result.fields if f.field_name.startswith('1e.')]
        self.assertTrue(fields)
        self.assertTrue(all(f.decision=='REVIEW_REQUIRED' for f in fields))
        self.assertTrue(all(any(v.validator=='overlapping_native_source_conflict' and v.status=='ERROR'
                                for v in f.validation_results) for f in fields))
        self.assertIn('359',fields[0].candidate_values)
        mapped=map_extraction_to_rules('offline-conflict',result.fields,form_type=FormType.SCHEDULE_A)
        counts=[f for f in mapped['fields'] if f.mapped_rule_key=='schedule_a_part_i_1e_persons_covered_end_of_policy_year']
        self.assertEqual(len(counts),1)
        self.assertEqual(counts[0].proposed_value,'')

    async def test_provider_local_disagreement_without_native_overlap_is_not_globally_blocked(self):
        result=await self.extract(document(overlay=None),'359')
        self.assertFalse(any(v.validator=='overlapping_native_source_conflict'
                             for f in result.fields for v in f.validation_results))

    async def test_identical_duplicate_native_glyphs_do_not_add_conflict_hold(self):
        result=await self.extract(document(overlay='358'),'358')
        self.assertFalse(any(v.validator=='overlapping_native_source_conflict'
                             for f in result.fields for v in f.validation_results))
