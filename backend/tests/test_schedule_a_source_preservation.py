"""Shared candidate selection must not destroy a correct source-backed value."""
import pytest

from app.models import NormalizedExtractionField, SourceEvidence
from app.services.extractor import merge_schedule_a_fields


@pytest.mark.parametrize('label,correct,wrong', [
    ('1a. Name of Insurance Company', 'Example Life Insurance Company', 'Example Life Insurance Company Field 1a'),
    ('1a. Name of Insurance Company', 'HARTFORD LIFE AND ACCIDENT', 'EIN: 06-0838648'),
    ('1c. NAIC Code', '60054', '2025'),
    ('1d. Contract/Policy Number', '00803136', '00803136Field'),
])
def test_source_supported_provider_value_survives_invalid_local_candidate(label, correct, wrong):
    provider = NormalizedExtractionField(
        field_name=label, value=correct, confidence=.97,
        evidence=[SourceEvidence(provider='GroundX structured extract')],
    )
    local = NormalizedExtractionField(
        field_name=label, value=wrong, confidence=.99, page=2,
        source_text='Labelled source row with an adjacent yellow Field annotation',
        evidence=[SourceEvidence(provider='Position-aware layout engine', page=2, table_cell=(3, 1))],
    )

    selected = merge_schedule_a_fields([provider], [local])

    assert selected[0].value == correct
    assert correct in selected[0].candidate_values
    assert provider.value == correct and local.value == wrong, 'Keep producer snapshots immutable.'


@pytest.mark.parametrize('value', ['b) Contract number', 'For Policy Year beginning January'])
def test_document_headings_cannot_become_recipient_names(value):
    from app.services.extractor import select_best_schedule_a_fields
    field = NormalizedExtractionField(field_name='3a. Name of Agent/Broker/Person',value=value,confidence=.99)
    assert select_best_schedule_a_fields([field]) == []


@pytest.mark.parametrize('value', ['Contract Resources LLC', 'Policy Year Advisors Inc', 'B. Carter'])
def test_heading_guard_preserves_real_names_with_similar_words(value):
    from app.services.extractor import select_best_schedule_a_fields
    field = NormalizedExtractionField(field_name='3a. Name of Agent/Broker/Person',value=value,confidence=.99)
    assert select_best_schedule_a_fields([field])[0].value == value


def test_carrier_administrative_retention_fee_is_not_a_broker_fee():
    from app.services.extractor import select_best_schedule_a_fields
    field=NormalizedExtractionField(field_name='3c. Amount of Fees',value='67975.56',confidence=.99,
        source_text='Total Administrative Fees Paid to Carrier: $67,975.56')
    assert select_best_schedule_a_fields([field]) == []


def test_explicit_missing_count_label_cannot_take_a_number_from_the_next_line():
    from app.services.extractor import select_best_schedule_a_fields
    field=NormalizedExtractionField(field_name='1e. Persons Covered (End of Policy Year)',value='165',confidence=.99,
        source_text='Approximate Number of Persons Insured: To be Provided by the Plan Administrator\n165')
    assert select_best_schedule_a_fields([field]) == []
