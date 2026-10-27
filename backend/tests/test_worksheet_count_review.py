"""A present but unreadable worksheet count cannot silently become verified."""
from app.models import DocumentType, ExtractedFieldStatus, FormType
from app.services.extractor import parse_plan_worksheet_text
from app.services.mapping import map_extraction_to_rules
from app.services.ftwilliams_review import FTWilliamsReviewService


def test_present_unreadable_count_is_held_even_when_ftw_already_has_a_value():
    fields = parse_plan_worksheet_text('''6(a)(1) Total number of active participants on the first day of the plan year
[use previous year's count from 6(a)(2)] ?
6(a)(2) Total number of active participants on the last day of the plan year 149 6(b)
''')
    mapped = map_extraction_to_rules('qa', fields, form_type=FormType.FORM_5500,
                                    source_document_type=DocumentType.PLAN_WORKSHEET)
    count = next(field for field in mapped['fields'] if field.mapped_rule_key ==
                 'form_5500_part_ii_13_active_participants_at_beginning')
    assert count.status == ExtractedFieldStatus.LOW_CONFIDENCE
    assert count.value == count.proposed_value == ''
    comparison = FTWilliamsReviewService()._comparison_fields(
        [count], {'TotActPartcpBoyCnt': '142'}, {}, update_fields=[])[0]
    assert comparison.validation_blocking
    assert comparison.decision == 'BLOCKED'
    assert comparison.current_value == comparison.proposed_value == '142'
    assert not comparison.update_included


def test_absent_count_label_does_not_manufacture_a_source_failure():
    fields = parse_plan_worksheet_text('Plan sponsor name EXAMPLE Plan sponsor address 42 MAIN ST Plan sponsor phone number 555')
    assert not any(field.field_name.startswith('13.') for field in fields)
