"""Visible benefit evidence for PDF text layers that omit checkbox marks."""
import re
import csv
from collections import defaultdict
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
import shutil
import subprocess
import tempfile

_LABELS = {'health':'health', 'dental':'dental', 'vision':'vision', 'life':'life',
           'indemnity':'indemnity', 'hmo':'hmo', 'ppo':'ppo',
           'temporary':'temporary_disability', 'long':'long_term_disability', 'longterm':'long_term_disability',
           'supplemental':'supplemental_unemployment', 'prescription':'prescription_drug',
           'stop':'stop_loss', 'other':'other', 'accidental':'other'}


def attach_visual_benefit_evidence(result, evidence):
    """Add review evidence only; OCR here cannot change scalar or broker data."""
    raw = dict(result.raw) if isinstance(result.raw, dict) else {}
    review = dict(raw.get('source_review') or {})
    observations = list(review.get('benefit_observations') or [])
    kinds = set(review.get('benefit_types') or [])
    conflicts = []
    for page in evidence.get('pages', []):
        selected = {box['benefit_type'] for box in page.get('checkboxes',[]) if box['selected']}
        for box in page.get('checkboxes', []):
            if not box['selected'] and box['benefit_type'] in kinds and box['benefit_type'] not in selected:
                conflicts.append({'page':page['page'], 'benefit_type':box['benefit_type'],
                                  'reason':'Text interpretation and visible unchecked box disagree.',
                                  'decision':'REVIEW_REQUIRED', 'pixel_evidence':box})
        for observation in page.get('observations', []):
            if observation not in observations:
                observations.append(observation)
            if observation.get('benefit_type'):
                kinds.add(observation['benefit_type'])
    review.update(benefit_observations=observations, benefit_types=sorted(kinds), visual_benefit_evidence=evidence)
    if conflicts:
        review['benefit_interpretation_conflicts'] = conflicts
    raw.update(source_review=review, benefit_types=sorted(kinds))
    result.raw = raw


def checked_benefit_boxes(image, words):
    """Return only unambiguous square boxes immediately before OCR labels.

    Pixel coordinates and ink density are retained as source evidence. A word
    alone is never a selected benefit, and uncertain interiors are omitted.
    """
    image = image.convert('L')
    pixels = image.load()
    output = []
    for word in words:
        label = re.sub(r'[^a-z]', '', str(word.get('text') or '').casefold())
        kind = _LABELS.get(label)
        if not kind:
            continue
        x, y, height = (int(word[key]) for key in ('left','top','height'))
        if height < 5 or height > 200:
            continue
        left, top = max(0, x-6*height), max(0, y-height)
        right, bottom = min(image.width, x), min(image.height, y+2*height)
        points = {(a,b) for a in range(left,right) for b in range(top,bottom) if pixels[a,b] < 150}
        candidates = []
        while points:
            todo = [points.pop()]
            group = []
            while todo:
                a,b = todo.pop()
                group.append((a,b))
                for neighbor in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
                    if neighbor in points:
                        points.remove(neighbor)
                        todo.append(neighbor)
            x0,x1 = min(a for a,b in group), max(a for a,b in group)
            y0,y1 = min(b for a,b in group), max(b for a,b in group)
            width, box_height = x1-x0+1, y1-y0+1
            if not (.65*height <= width <= 1.8*height and .65*height <= box_height <= 1.8*height
                    and .7 <= width/box_height <= 1.3 and abs(y0-y) <= .75*height):
                continue
            edges = (
                [(a,y0) for a in range(x0,x1+1)], [(a,y1) for a in range(x0,x1+1)],
                [(x0,b) for b in range(y0,y1+1)], [(x1,b) for b in range(y0,y1+1)],
            )
            border = [sum(pixels[a,b]<150 for a,b in edge)/len(edge) for edge in edges]
            if min(border) < .85:
                continue
            inset = max(2, round(width*.2))
            interior = [pixels[a,b]<150 for a in range(x0+inset,x1-inset+1) for b in range(y0+inset,y1-inset+1)]
            if not interior:
                continue
            ink = sum(interior)/len(interior)
            candidates.append({'bounding_box':[x0,y0,x1+1,y1+1], 'interior_ink_fraction':ink,
                               'border_ink_fractions':border})
        if len(candidates) != 1:
            continue
        box = candidates[0]
        ink = box['interior_ink_fraction']
        if ink > .03 and ink < .12:
            continue
        output.append({'benefit_type':kind, 'literal':word['text'], 'selected':ink >= .12,
                       'label_bounding_box':[x,y,x+int(word['width']),y+height], **box})
    return output


def benefit_evidence_from_ocr(image, words, page):
    """Restrict image interpretation to an explicitly labelled benefit area."""
    lines = defaultdict(list)
    for word in words:
        lines[tuple(word.get(key, '') for key in ('block_num','par_num','line_num'))].append(word)
    observations, headings, ends = [], [], []
    for group in lines.values():
        line = ' '.join(str(word.get('text') or '') for word in group).strip()
        top = min(int(word['top']) for word in group)
        if re.search(r'Welfare\s+Benefit\s+Contract\s+Information|Benefit\s+and\s+contract\s+type', line, re.I):
            headings.append(top)
        if re.match(r'^[\d.)\s]*(?:Non\s*[- ]?\s*)?Experience\s*[- ]?\s*rated\s+contracts', line, re.I):
            ends.append(top)
        typed = re.fullmatch(r'[\d.)\s]*COVERAGE\s*[-:]?\s+(Life\s+Insurance|Vision(?:\s+Care)?|Dental|Health(?:\s+Insurance)?)', line, re.I)
        if typed:
            kind = _LABELS.get(typed.group(1).split()[0].casefold())
            observations.append({'benefit_type':kind, 'literal':typed.group(1), 'page':page,
                                 'source_text':line, 'decision':'SOURCE_ONLY', 'provider':'Visible PDF OCR labelled coverage'})
    boxes = []
    if headings:
        start = min(headings)
        end = min((value for value in ends if value > start), default=image.height)
        boxes = checked_benefit_boxes(image, [word for word in words if start <= int(word['top']) < end])
        for box in boxes:
            if box['selected']:
                observations.append({'benefit_type':box['benefit_type'], 'literal':box['literal'], 'page':page,
                    'source_text':'Visible marked square immediately before '+box['literal'],
                    'decision':'SOURCE_ONLY', 'provider':'Visible PDF checkbox geometry',
                    'pixel_evidence':box, 'image_size':list(image.size)})
    return {'observations':observations, 'checkboxes':boxes}


def _native_labelled_benefits(file_bytes, pages):
    """Read explicit label/value rows, not the set of words anywhere on a page.

    Native overlay streams can be omitted by a layout-text reader. Character
    geometry joins them to their labels while excluding off-page/white text and
    reviewer mapping instructions. Checkbox areas still need pixel evidence.
    """
    from app.services.schedule_a_pdf_source import visible_source_page
    try:
        import pdfplumber
        found=[]
        with pdfplumber.open(BytesIO(file_bytes)) as document:
            for number in pages:
                if not isinstance(number,int) or not 1<=number<=len(document.pages):
                    continue
                visible_page=visible_source_page(document.pages[number-1])
                lines=visible_page.extract_text_lines(return_chars=False)
                observations=[]
                for line in lines:
                    typed=re.fullmatch(r'[\d.)\s]*(?:COVERAGE|BENEFIT\s+TYPE)\s*[-:]?\s+'
                                       r'(Life\s+Insurance|Vision(?:\s+Care)?|Dental|Health(?:\s+Insurance)?)',line['text'],re.I)
                    if not typed:continue
                    observations.append({'benefit_type':_LABELS[typed.group(1).split()[0].casefold()],
                        'literal':typed.group(1),'page':number,'source_text':line['text'],
                        'decision':'SOURCE_ONLY','provider':'Native visible labelled benefit',
                        'bounding_box':[line[key] for key in ('x0','top','x1','bottom')]})
                if observations:
                    needs_boxes=bool(re.search(r'Welfare\s+Benefit\s+Contract\s+Information|Benefit\s+and\s+contract\s+type',
                                              '\n'.join(line['text'] for line in lines),re.I))
                    found.append({'page':number,'observations':observations,'checkboxes':[],
                                  'native_labelled_value':True,'requires_checkbox_ocr':needs_boxes})
        return found
    except Exception:
        # An unreadable native layer is not permission to infer a benefit.
        return []


@lru_cache(maxsize=2)
def read_visual_benefit_evidence(file_bytes, pages):
    """Render only selected pages; never use OCR amounts as replacement data."""
    if not file_bytes.startswith(b'%PDF'):
        return {'status':'NOT_PDF', 'pages':[]}
    pages=tuple(page for page in sorted(set(pages)) if isinstance(page,int) and page>=1)[:20]
    native=_native_labelled_benefits(file_bytes,pages)
    satisfied={item['page'] for item in native if not item['requires_checkbox_ocr']}
    remaining=[page for page in pages if page not in satisfied]
    results=[item for item in native if item['page'] in satisfied]
    if not remaining:
        return {'status':'COMPLETED','pages':results}
    renderer, ocr = shutil.which('pdftoppm'), shutil.which('tesseract')
    if not renderer or not ocr:
        return {'status':'PARTIAL' if native else 'UNAVAILABLE',
                'reason':'PDF renderer or local OCR executable unavailable','pages':native}
    from PIL import Image
    try:
        with tempfile.TemporaryDirectory(prefix='schedule-a-benefit-evidence-') as directory:
            source = Path(directory)/'source.pdf'
            source.write_bytes(file_bytes)
            for page in remaining:
                prefix = Path(directory)/'visible'
                subprocess.run([renderer,'-f',str(page),'-l',str(page),'-singlefile','-r','200','-scale-to','3000','-png',str(source),str(prefix)],
                               check=True,capture_output=True,timeout=45)
                raster = prefix.with_suffix('.png')
                response = subprocess.run([ocr,str(raster),'stdout','--psm','6','tsv'],
                                          check=True,capture_output=True,text=True,encoding='utf-8',timeout=45)
                words = [word for word in csv.DictReader(StringIO(response.stdout),delimiter='\t') if str(word.get('text') or '').strip()]
                with Image.open(raster) as image:
                    evidence = benefit_evidence_from_ocr(image,words,page)
                native_page=next((item for item in native if item['page']==page),None)
                if native_page:
                    evidence['observations']=[*native_page['observations'],*evidence['observations']]
                results.append({'page':page,'dpi':200,**evidence})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'status':'ERROR','error_type':type(error).__name__,'pages':results}
    return {'status':'COMPLETED','pages':results}
