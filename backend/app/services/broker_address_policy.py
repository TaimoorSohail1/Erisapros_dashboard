"""Optional broker addresses, with explicit acknowledgement of partial data."""
import hashlib
import json

from app.models import ExtractionValidationResult

ADDRESS_FIELDS = ('address_line_1', 'address_line_2', 'city', 'state', 'zip_code')
ADDRESS_REVIEW = 'broker_partial_address'
ADDRESS_CONFIRMATION = 'reviewer_partial_address_confirmation'


def address_state(row):
    parts = {key: str((row.get(key) if isinstance(row, dict) else getattr(row, key, '')) or '').strip()
             for key in ADDRESS_FIELDS}
    if not any(parts.values()):
        return 'BLANK'
    return 'COMPLETE' if all(parts[key] for key in ('address_line_1', 'city', 'state', 'zip_code')) else 'PARTIAL'


def address_key(row):
    return hashlib.sha256(json.dumps([str(getattr(row, key, '') or '').strip()
        for key in ('name', *ADDRESS_FIELDS)], separators=(',', ':')).encode()).hexdigest()


def partial_address_confirmed(row):
    return any(v.validator == ADDRESS_CONFIRMATION and v.status == 'PASS'
               and v.normalized_value == address_key(row) for v in row.validation_results)


def acknowledge_partial_address(row):
    row.validation_results = [v for v in row.validation_results if v.validator not in {ADDRESS_REVIEW, ADDRESS_CONFIRMATION}]
    row.validation_results.append(ExtractionValidationResult(validator=ADDRESS_CONFIRMATION, status='PASS',
        normalized_value=address_key(row), reason='Reviewer chose Save and send as-is for the displayed incomplete address.'))


def apply_address_policy(rows):
    for row in rows:
        was_address_review = any(v.validator == ADDRESS_REVIEW and v.status == 'ERROR' for v in row.validation_results)
        row.validation_results = [v for v in row.validation_results if v.validator != ADDRESS_REVIEW]
        if address_state(row) == 'PARTIAL' and not partial_address_confirmed(row):
            row.validation_results.append(ExtractionValidationResult(validator=ADDRESS_REVIEW, status='ERROR',
                reason='Broker address is incomplete. Complete it or choose Save and send as-is; address fields are optional.'))
            row.decision = 'REVIEW_REQUIRED'
        elif was_address_review and not any(v.status == 'ERROR' for v in row.validation_results):
            row.decision = 'UNASSESSED'
    return rows
