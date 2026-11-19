"""Shared scalar policy: absent proof is informative, contradictions are blocking.

This policy never authorizes a broker-roster replacement or creates evidence.
Legacy holds are interpreted by an explicit allowlist, not by clearing all
LOW_CONFIDENCE fields.
"""
import re
from app.models import ExtractedFieldStatus

INFORMATIONAL_VALIDATORS = frozenset({'source_evidence', 'section_context', 'low_confidence'})
EVIDENCE_REASONS = frozenset({
    'Automatic extraction requires a source page and source text that supports the extracted value.',
    'Automatic extraction requires explicit nonexperience-rated/line 10 source evidence.',
    'Automatic extraction requires explicit Part II/experience-rated/line 9 source evidence.',
})


def informational_validation(validation):
    if validation.status == 'WARN' or validation.validator == 'low_confidence':
        return True
    if validation.validator == 'section_context':
        return validation.reason.startswith('Automatic extraction requires explicit ')
    if validation.validator == 'source_evidence':
        return validation.reason in EVIDENCE_REASONS or bool(re.search(r'\bmissing\s+source\s+(?:page|text|evidence)',validation.reason,re.I))
    return False


def scalar_issues(field):
    """Return blocking reasons and nonblocking warnings without certifying proof."""
    validations = getattr(field, 'validation_results', [])
    errors = [v for v in validations if v.status in {'ERROR', 'WARN'}]
    blockers = [v.reason for v in errors if v.status == 'ERROR' and not informational_validation(v)]
    warnings = [v.reason for v in errors if informational_validation(v)]
    if (str(getattr(field,'value','') or getattr(field,'proposed_value','') or '').strip()
            and (getattr(field,'page',None) is None or not str(getattr(field,'source_text','') or '').strip())):
        warnings.append('Source page/text is unavailable; accepted under the single-value policy, not source-verified.')
    reason = str(getattr(field, 'status_reason', '') or '').strip()
    if getattr(field, 'status', None) == ExtractedFieldStatus.LOW_CONFIDENCE:
        prefix = 'Extraction requires review; automatic proposal withheld.'
        parts = [p.strip() for p in reason.removeprefix(prefix).split(';') if p.strip()]
        for part in parts:
            if part in EVIDENCE_REASONS or re.fullmatch(r'Confidence \d+% is below the \d+% review threshold\.', part):
                warnings.append(part)
            elif part and not validations:
                blockers.append(part)
        if not parts and not validations and not str(getattr(field, 'proposed_value', '') or '').strip():
            if reason == prefix:
                warnings.append('Legacy review flag has no specific issue recorded; accepted under the single-value policy, not source-verified.')
            else:
                blockers.append('The source interpretation has not been classified.')
        # Old mappings sometimes retained a payment-column caption as a name
        # without retaining its validation metadata. A caption is not a valid
        # single value, even when the legacy review reason is generic.
        value = str(getattr(field, 'value', '') or '').strip()
        if (re.search(r'_name(?:_|$)', str(getattr(field, 'mapped_rule_key', '') or ''))
                and re.fullmatch(r'\$?\s*(?:commissions?|fees?)(?:\s+(?:and|&)\s+(?:commissions?|fees?))?\s+paid', value, re.I)):
            blockers.append('Payment column header was extracted as a name; confirm the source recipient.')
    if float(getattr(field, 'confidence', 1) or 0) < .8:
        warnings.append('Extraction confidence is low; confidence alone does not require approval.')
    return list(dict.fromkeys(blockers)), list(dict.fromkeys(warnings))


def restore_single_value_proposal(field):
    if (field.status != ExtractedFieldStatus.LOW_CONFIDENCE or field.keep_current_context
            or not str(field.value or '').strip() or str(field.proposed_value or '').strip()):
        return
    blockers, warnings = scalar_issues(field)
    if not blockers and warnings:
        field.proposed_value = field.value
