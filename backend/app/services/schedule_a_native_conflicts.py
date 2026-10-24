"""Hold numeric provider/local conflicts when native PDF glyphs overlap.

A white correction rectangle does not remove the older PDF text. Neither the
provider's visual reading nor the native decoder can silently approve which
layer is authoritative when different digits occupy the same source position.
This adds a review hold, never a scalar replacement or a business approval.
"""
from io import BytesIO
import re

from app.models import ExtractionValidationResult
from app.services.schedule_a_pdf_source import visible_source_page


def overlapping_native_digit_pages(file_bytes):
    """Return page-level evidence of differing, substantially overlapping digits."""
    try:
        import pdfplumber
        conflicts = {}
        with pdfplumber.open(BytesIO(file_bytes)) as document:
            for number,page in enumerate(document.pages,start=1):
                digits = sorted((c for c in visible_source_page(page).chars
                    if len(c.get('text','')) == 1 and c['text'].isdigit()),key=lambda c:c['x0'])
                hits = []
                for index,left in enumerate(digits):
                    for right in digits[index+1:]:
                        if right['x0'] >= left['x1']: break
                        if left['text'] == right['text']: continue
                        width = min(left['x1'],right['x1'])-max(left['x0'],right['x0'])
                        height = min(left['bottom'],right['bottom'])-max(left['top'],right['top'])
                        if (width >= .75*min(left['x1']-left['x0'],right['x1']-right['x0'])
                                and height >= .6*min(left['bottom']-left['top'],right['bottom']-right['top'])):
                            hits.append({'digits':[left['text'],right['text']],
                                'bounds':[round(v,2) for v in [max(left['x0'],right['x0']),max(left['top'],right['top']),
                                                               min(left['x1'],right['x1']),min(left['bottom'],right['bottom'])]]})
                if hits: conflicts[number] = hits
        return conflicts
    except Exception:
        # Other PDF-quality and evidence validators retain their existing path.
        return {}


def _numeric(value):
    value = re.sub(r'[,\s$]','',str(value or ''))
    return value if re.fullmatch(r'[+()0-9./-]+',value) else None


def apply_native_provider_conflict_holds(result,file_bytes):
    raw = result.raw if isinstance(result.raw,dict) else {}
    structured = next((stage for stage in raw.get('extraction_stages',[])
                       if stage.get('stage') == 'structured_provider_normalized'),None)
    if not structured: return
    possible = []
    for field in result.fields:
        current = _numeric(field.value)
        if current is None or field.page is None: continue
        code = field.field_name.split('.',1)[0].strip().lower()
        candidates = [f for f in structured.get('fields',[]) if
            str(f.get('field_name','')).split('.',1)[0].strip().lower() == code
            and (f.get('page') is None or f['page'] == field.page)
            and _numeric(f.get('value')) is not None and _numeric(f['value']) != current]
        if candidates: possible.append((field,candidates))
    if not possible: return
    overlaps = overlapping_native_digit_pages(file_bytes)
    held = []
    for field,candidates in possible:
        if field.page not in overlaps: continue
        field.decision = 'REVIEW_REQUIRED'
        field.candidate_values = list(dict.fromkeys([*field.candidate_values,field.value,*[f['value'] for f in candidates]]))
        field.validation_results.append(ExtractionValidationResult(
            validator='overlapping_native_source_conflict',status='ERROR',
            reason='Different numeric PDF glyphs overlap on this source page, and provider/local values disagree. Review the rendered source before sending.'))
        held.append({'field':field.field_name,'page':field.page,'current':field.value,
                     'provider_candidates':[f['value'] for f in candidates],'overlapping_digits':overlaps[field.page]})
    if held:
        raw['overlapping_native_source_conflicts'] = held
        result.raw = raw
