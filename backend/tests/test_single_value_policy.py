import pytest
from app.models import (ExtractedField, ExtractedFieldStatus, FormType, NormalizedExtractionField,
                        ExtractionValidationResult, FTWFieldDecision)
from app.services.mapping import map_extraction_to_rules
from app.services.ftwilliams_review import FTWilliamsReviewService

KEY = 'schedule_a_part_i_1e_persons_covered_end_of_policy_year'

def source(**kwargs):
    return ExtractedField(filing_id='qa', source_field_name='1e. Persons Covered (End of Policy Year)',
        normalized_field_name='persons_covered', mapped_rule_key=KEY, form_type=FormType.SCHEDULE_A,
        value='275', proposed_value='', status=ExtractedFieldStatus.LOW_CONFIDENCE,
        status_reason='Extraction requires review; automatic proposal withheld. Automatic extraction requires a source page and source text that supports the extracted value.', **kwargs)

@pytest.mark.parametrize('current,decision,proposed', [('', 'WILL_UPDATE','275'),('275','NO_CHANGE','275'),('300','CONFLICT','275')])
def test_legacy_evidence_only_value_reaches_real_decision(current,decision,proposed):
    service=FTWilliamsReviewService();field=source()
    safe=service._safe_update_fields([field],FormType.SCHEDULE_A,{'InsPrsnCoveredEoyCnt':current})
    result=service._comparison_fields([field],{}, {'InsPrsnCoveredEoyCnt':current},safe)[0]
    assert result.decision.value==decision
    assert result.proposed_value==proposed
    assert not result.validation_blocking
    assert result.processing_warnings
    assert field.page is None and field.source_text is None

def test_new_mapping_keeps_evidence_warning_without_withholding_value():
    normalized=NormalizedExtractionField(field_name='1e. Persons Covered (End of Policy Year)',value='275',confidence=.5,
        decision='REVIEW_REQUIRED',validation_results=[ExtractionValidationResult(validator='source_evidence',status='ERROR',reason='Missing source page')])
    fields=map_extraction_to_rules('qa',[normalized],form_type=FormType.SCHEDULE_A)['fields']
    field=next(f for f in fields if f.value=='275')
    assert field.proposed_value=='275'

def test_true_candidate_conflict_remains_blocked():
    field=source();field.status_reason='Extraction requires review; automatic proposal withheld. Multiple source candidates disagree for this field.'
    result=FTWilliamsReviewService()._comparison_fields([field],{}, {})[0]
    assert result.decision==FTWFieldDecision.BLOCKED

def test_unknown_legacy_hold_is_not_silently_cleared():
    field=source();field.status_reason='Unclassified source interpretation issue'
    result=FTWilliamsReviewService()._comparison_fields([field],{}, {})[0]
    assert result.validation_blocking

def test_no_evidence_value_is_sendable_under_normal_automation_policy():
    from test_ftwilliams_automation import FTWAutomationPolicyTests
    from app.services.ftwilliams_automation import FTWAutomationPolicy
    from app.models import FTWAutomationStatus
    filing,review,field,settings=FTWAutomationPolicyTests().safe_case()
    field.page=None;field.source_text=None
    result=FTWAutomationPolicy(settings).evaluate(filing,review,[field])
    assert result.status==FTWAutomationStatus.SAFE_TO_SEND,result.reasons

@pytest.mark.parametrize('value',['0','275'])
def test_missing_evidence_warning_preserves_explicit_zero_and_reloads(value):
    import asyncio
    from app.repositories import MemoryRepository
    async def run():
        repo=MemoryRepository();field=source();field.value=value
        stored=await repo.add_fields([field]);before=FTWilliamsReviewService._automatic_field_state(stored)
        fields=[f.model_copy(deep=True) for f in stored]
        service=FTWilliamsReviewService();service._safe_update_fields(fields,FormType.SCHEDULE_A,{})
        await service._persist_automatic_field_changes(repo,'qa',before,fields)
        fresh=await repo.list_fields('qa')
        comparison=service._comparison_fields(fresh,{}, {})[0]
        assert comparison.decision==FTWFieldDecision.WILL_UPDATE
        assert comparison.proposed_value==value and fresh[0].value==value
        assert fresh[0].page is None and fresh[0].source_text is None
    asyncio.run(run())

def test_wrong_identifier_in_source_is_a_real_conflict_not_missing_proof():
    from app.models import NormalizedExtractionResult
    from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
    result=resolve_schedule_a_result(NormalizedExtractionResult(provider='test',fields=[NormalizedExtractionField(
        field_name='1b. Insurance Carrier EIN',value='42-0127290',page=1,source_text='Insurance Carrier EIN 99-9999999',confidence=.99)]))
    assert any(v.validator=='source_value_conflict' and v.status=='ERROR' for v in result.fields[0].validation_results)
