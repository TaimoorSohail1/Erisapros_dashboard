"""Supporting narrative is not an experience-rated retention answer."""
import pytest

from app.services.extractor import extract_fields_from_document_text


@pytest.mark.parametrize('narrative', [
    'compensation is used by the distributor to pay other expenses, including Management',
    'compensation is used to pay other expenses, including $25.00 for conferences.',
])
def test_cover_letter_expenses_do_not_become_retention_answers(narrative):
    source = ('9 Experience-rated contracts: N/A\n'
              '10 Nonexperience-rated contracts\n'
              'Total premiums or subscription charges paid to carrier: $1250.00\n'
              + narrative)
    fields = extract_fields_from_document_text(source.encode(), 'statement.txt')
    assert not any(f.field_name.startswith('9c(1)(D).') for f in fields)
    assert any(f.field_name.startswith('10a.') for f in fields)


@pytest.mark.parametrize('amount', ['$125.25', '0.00', '-$25.50'])
def test_real_labelled_experience_expense_still_extracts(amount):
    source = '9 Experience-rated contracts\n9c(1)(D). Other expenses: ' + amount
    fields = extract_fields_from_document_text(source.encode(), 'statement.txt')
    expense = next(f for f in fields if f.field_name.startswith('9c(1)(D).'))
    from decimal import Decimal
    assert Decimal(expense.value.replace('$','').replace(',','')) == Decimal(amount.replace('$',''))


def test_expense_guard_does_not_remove_other_existing_retention_fields():
    from app.services.extractor import extract_rules_driven_schedule_a_fields
    source = '''9 Experience-rated contracts
3. Other retention charges . . . . . . . . . . . . . . . . . . $0.00
3. Commissions: $125.25
2. Dividends or retroactive rate refunds . (These amounts were paid in cash and/or credited). . . $0.00
'''
    fields = extract_rules_driven_schedule_a_fields([(1,source)])
    assert any(f.field_name.startswith('9c(1)(G).') for f in fields)
    assert any(f.field_name.startswith('9c(1)(A).') for f in fields)
    assert any(f.field_name.startswith('9c(2).') for f in fields)
