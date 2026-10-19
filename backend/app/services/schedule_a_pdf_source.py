"""Read carrier source text without treating reviewer mapping notes as data.

Do not remove yellow/colored text globally. Only spatially connected colored
text blocks containing explicit QA field instructions are excluded. Original
PDF bytes and their rendered appearance are never modified.
"""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
from io import BytesIO
import re

_FIELD_NOTE=re.compile(r'\bFields?\s+\d+[a-z](?:\b|\()',re.IGNORECASE)
_FIELD_REFERENCE = re.compile(r'\b\d+[a-z]\b', re.IGNORECASE)
_INSTRUCTION = re.compile(r'\b(?:into|entries|would|otherwise|should|pulled|leave\s+blank)\b', re.IGNORECASE)
_TARGET_FORM = re.compile(r'\b5500\s+Sch(?:edule)?\s+A\b', re.IGNORECASE)

def character_key(char:dict)->tuple:
    return tuple(char.get(k) for k in ('text','x0','x1','top','bottom','fontname','size'))

def mapping_note_character_keys(characters:list[dict])->set[tuple]:
    groups=defaultdict(list)
    for char in characters:
        color=char.get('non_stroking_color')
        if not isinstance(color,(tuple,list)) or len(color)!=3:
            continue
        # Black/grey printed form text, colored logos and highlights without
        # a mapping-instruction block remain untouched.
        if max(color)-min(color)<.25:
            continue
        groups[(tuple(color),char.get('fontname'),round(float(char.get('size') or 10),1))].append(char)
    excluded=set()
    for (_,_,size), chars in groups.items():
        lines=[]
        for char in sorted(chars,key=lambda c:(c['top'],c['x0'])):
            line=next((r for r in reversed(lines[-3:]) if abs(r[0]['top']-char['top'])<2),None)
            if line is None:
                line=[];lines.append(line)
            line.append(char)
        segments=[]
        for line in lines:
            segment=[]
            for char in sorted(line,key=lambda c:c['x0']):
                if segment and char['x0']-segment[-1]['x1']>size*2:
                    segments.append(segment);segment=[]
                segment.append(char)
            if segment: segments.append(segment)
        blocks=[]
        for segment in sorted(segments,key=lambda r:(r[0]['top'],r[0]['x0'])):
            x0=min(c['x0'] for c in segment);top=min(c['top'] for c in segment)
            block=next((b for b in reversed(blocks) if 0<top-b['top']<=size*1.8 and abs(x0-b['x0'])<=size*2),None)
            if block is None:
                block={'chars':[],'lines':[],'x0':x0,'top':top};blocks.append(block)
            block['chars'].extend(segment);block['lines'].append(''.join(c['text'] for c in segment));block['top']=top
        for block in blocks:
            text = ' '.join(block['lines'])
            if _FIELD_NOTE.search(text) or ((_FIELD_REFERENCE.search(text) or _TARGET_FORM.search(text)) and _INSTRUCTION.search(text)):
                excluded.update(character_key(c) for c in block['chars'])
    return excluded

def source_page(page):
    excluded=mapping_note_character_keys(page.chars)
    if not excluded: return page
    return page.filter(lambda obj: obj.get('object_type')!='char' or character_key(obj) not in excluded)


def visible_source_page(page):
    """Exclude known invisible native text and reviewer instructions.

    This is a conservative native geometry view, not a replacement for pixel
    evidence on scanned pages or checkbox selections.
    """
    page = source_page(page)
    def visible(obj):
        if obj.get('object_type') != 'char':
            return True
        color = obj.get('non_stroking_color')
        if isinstance(color, (float, int)) and color >= .95:
            return False
        if isinstance(color, (tuple, list)) and len(color) == 3 and min(color) >= .95:
            return False
        if isinstance(color, (tuple, list)) and len(color) == 4 and max(color) <= .05:
            return False
        return (0 <= obj['x0'] < obj['x1'] <= page.width
                and 0 <= obj['top'] < obj['bottom'] <= page.height)
    return page.filter(visible)


@lru_cache(maxsize=2)
def visible_native_text_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    """Additional source input for review holds; never a scalar override."""
    try:
        import pdfplumber
        with pdfplumber.open(BytesIO(file_bytes)) as document:
            return [(number, '\n'.join(line['text'] for line in
                     visible_source_page(page).extract_text_lines(return_chars=False)))
                    for number, page in enumerate(document.pages, start=1)]
    except Exception:
        return []


@lru_cache(maxsize=2)
def mapping_note_text_overrides(file_bytes: bytes) -> dict[int, tuple[str, str]]:
    """Override only annotated pages; leave unannotated extraction unchanged.

    Both readers and the geometry engine see the same source characters. The
    small cache avoids repeatedly opening the same PDF within an extraction.
    """
    try:
        import pdfplumber
    except ImportError:
        return {}
    overrides = {}
    try:
        with pdfplumber.open(BytesIO(file_bytes)) as document:
            for number, page in enumerate(document.pages, start=1):
                filtered = source_page(page)
                if filtered is not page:
                    overrides[number] = (
                        filtered.extract_text() or "",
                        filtered.extract_text(layout=True) or "",
                    )
    except Exception:
        # Reader failures still use the existing extraction/fallback paths.
        return {}
    return overrides


def has_substantive_native_text(page_texts: list[tuple[int, str]]) -> bool:
    """A logo or a few reviewer notes are not a readable insurance statement.

    This is an OCR trigger, not permission to discard existing native values.
    Short documents with an actual labelled value still retain their path.
    """
    for _, text in page_texts:
        if len(re.findall(r"\b\w+\b", text)) >= 40:
            return True
        if re.search(
            r"\b\d{2}-\d{7}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b|"
            r"\b\d[\d,]*\.\d{2}\b|\b(?:NAIC|EIN|CONTRACT|POLICY|COVERED)\s*:?\s*\d{2,}\b",
            text, re.IGNORECASE,
        ):
            return True
    return False
