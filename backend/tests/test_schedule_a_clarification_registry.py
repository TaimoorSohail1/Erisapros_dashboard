"""Normal extraction must resolve trusted approvals without changing automation."""
import hashlib
import json
import asyncio
from types import SimpleNamespace

import pytest

from app.services.schedule_a_clarification_registry import clarification_for_source


PDF = b'a changed carrier document, not a sample filename'


def write_registry(tmp_path, **changes):
    record = {'source_sha256': hashlib.sha256(PDF).hexdigest(),
              'rules': ['trim_contract_trailing_zeros'], 'reference': 'Client approval 2026-10-21'}
    record.update(changes)
    p = tmp_path / 'trusted-approvals.json'
    p.write_text(json.dumps({'schema_version': 1, 'approvals': [record]}), encoding='utf-8')
    return p


def test_no_registry_or_unknown_source_preserves_existing_behavior(tmp_path):
    assert clarification_for_source(PDF, None) is None
    assert clarification_for_source(b'unreviewed upload', write_registry(tmp_path)) is None


def test_exact_source_loads_reviewed_rule_and_reference(tmp_path):
    a = clarification_for_source(PDF, write_registry(tmp_path))
    assert a.rules == frozenset({'trim_contract_trailing_zeros'})
    assert a.reference == 'Client approval 2026-10-21'
    assert a.source_sha256 == hashlib.sha256(PDF).hexdigest()


@pytest.mark.parametrize('changes', [
    {'rules': ['turn_off_all_review']}, {'reference': ''}, {'source_sha256': 'invalid'},
])
def test_invalid_approved_records_cannot_silently_activate(tmp_path, changes):
    with pytest.raises(ValueError):
        clarification_for_source(PDF, write_registry(tmp_path, **changes))


def test_duplicate_source_approvals_are_not_first_match_wins(tmp_path):
    p = write_registry(tmp_path)
    data = json.loads(p.read_text())
    data['approvals'].append(dict(data['approvals'][0], rules=['employees_as_covered']))
    p.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        clarification_for_source(PDF, p)


def test_registry_changes_are_not_hidden_by_a_path_cache(tmp_path):
    p = write_registry(tmp_path)
    assert clarification_for_source(PDF, p) is not None
    p.write_text(json.dumps({'schema_version': 1, 'approvals': []}), encoding='utf-8')
    assert clarification_for_source(PDF, p) is None


@pytest.mark.parametrize('data', [{'schema_version': 2, 'approvals': []}, {'schema_version': 1}])
def test_unknown_registry_contract_is_rejected(tmp_path, data):
    p = tmp_path / 'approvals.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        clarification_for_source(PDF, p)


@pytest.mark.parametrize('configured', [False, True])
def test_normal_extraction_entry_point_resolves_source_approval_without_caller_override(tmp_path, monkeypatch, configured):
    from app.models import NormalizedExtractionField, NormalizedExtractionResult, SourceEvidence
    from app.services import extractor as module
    p = write_registry(tmp_path)
    monkeypatch.setattr(module, 'get_settings', lambda: SimpleNamespace(
        schedule_a_clarifications_path=str(p) if configured else None,
        schedule_a_canonical_validation_enabled=True, schedule_a_canonical_validation_shadow_enabled=False))
    async def unresolved(self, *args):
        return NormalizedExtractionResult(provider='Provider fixture', fields=[NormalizedExtractionField(
            field_name='1d. Contract/Policy Number', value='001230000', candidate_values=['001230000'],
            confidence=.99, page=1, source_text='Contract: 001230000',
            evidence=[SourceEvidence(provider='Printed source', page=1, source_text='Contract: 001230000')])])
    monkeypatch.setattr(module.ExtractionService, '_extract_schedule_a_unresolved', unresolved)
    monkeypatch.setattr(module, '_semantic_page_texts', lambda *args: [])
    result = asyncio.run(module.ExtractionService([]).extract_schedule_a(PDF, 'unrelated-name.txt'))
    field = next(f for f in result.fields if f.field_name.startswith('1d.'))
    assert field.value == ('00123' if configured else '001230000')
    assert bool(result.raw.get('confirmed_clarifications')) == configured
