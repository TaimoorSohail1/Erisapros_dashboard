"""Reconcile combined Schedule A proposals against a labelled benefit table."""
import re
from decimal import Decimal

from app.models import NormalizedExtractionField, SourceEvidence


_SECTION = re.compile(r"(?im)^\s*non[- ]?experience[- ]rated contracts\s*$")
_HEADER = re.compile(r"10\s*\.\s*a\s*\.?\s*premiums?", re.I)
_PEOPLE = re.compile(r"subscribers\s*/[\s\S]{0,120}?members\s*\)", re.I)
_ROW = re.compile(
    r"^\s*[a-z](?:/[a-z])?\s+(?P<benefit>[A-Za-z][A-Za-z /.&-]*?)\s+"
    r"\$\s*(?P<premium>\d[\d,]*(?:\.\d{1,2})?)\s+"
    r"\d[\d,]*\s*/\s*(?P<members>\d[\d,]*)\b", re.I,
)
_PREMIUM_SOURCE = "Schedule A benefit-table premiums"
_COUNT_SOURCE = "Schedule A covered-lives column"


def _number(value):
    clean = re.sub(r"[$,\s]", "", value or "")
    return Decimal(clean) if re.fullmatch(r"\d+(?:\.\d{1,2})?", clean) else None


def reconcile_labeled_benefit_table(fields, pages):
    sections = [(page, text[match.end():]) for page, text in pages for match in _SECTION.finditer(text)]
    if len(sections) != 1:
        return fields
    page, body = sections[0]
    heading = _HEADER.search(body)
    people = _PEOPLE.search(body)
    if not heading or not people:
        return fields
    header_end = body.find("\n", max(heading.end(), people.end()))
    if header_end == -1:
        return fields
    rows = []
    for line in body[header_end:].splitlines():
        if re.match(r"^\s*(?:\*|part\s+[ivx]+\b|experience[- ]rated\b)", line, re.I):
            break
        match = _ROW.match(line)
        if match and not re.search(r"\$|\d\s*/\s*\d", line[match.end():]):
            rows.append(match)
        elif "$" in line or re.search(r"\d\s*/\s*\d", line):
            # A monetary or enrollment row outside the recognized columns
            # makes the complete table total uncertain.
            return fields
    benefits = [row['benefit'].strip().casefold() for row in rows]
    if len(rows) < 2 or len(set(benefits)) != len(rows):
        return fields
    premiums = [_number(row['premium']) for row in rows]
    counts = [int(row['members'].replace(',', '')) for row in rows]
    premium = sum(premiums, Decimal(0))
    count = max(counts)
    source = "Nonexperience-rated contracts\n" + body[:header_end].strip() + "\n" + "\n".join(row[0].strip() for row in rows)
    specifications = {
        '10a': (f'{premium:,}', {*premiums, premium}, SourceEvidence(
            provider=_PREMIUM_SOURCE, page=page, source_text=source)),
        '1e': (str(count), set(counts), SourceEvidence(
            provider=_COUNT_SOURCE, page=page, source_text='Counts in covered-lives column: ' + ', '.join(map(str, counts)))),
    }
    resolved = []
    for field in fields:
        code = field.field_name.split('.', 1)[0].strip().lower()
        if code not in specifications:
            resolved.append(field)
            continue
        value, components, evidence = specifications[code]
        candidates = {_number(candidate) for candidate in [field.value, *field.candidate_values] if candidate}
        if not candidates or not candidates <= components:
            resolved.append(field)
            continue
        resolved.append(field.model_copy(update={
            'value': value, 'candidate_values': [value], 'page': page, 'source_text': source,
            'confidence': max(field.confidence, 0.95), 'evidence': [*field.evidence, evidence],
        }))
    return resolved


def matches_benefit_table_premium(field: NormalizedExtractionField) -> bool:
    for evidence in field.evidence:
        if evidence.provider != _PREMIUM_SOURCE or not evidence.page:
            continue
        text = evidence.source_text or ''
        if not _SECTION.search(text) or not _HEADER.search(text) or not _PEOPLE.search(text):
            continue
        matched = [match for line in text.splitlines() if (match := _ROW.match(line))]
        if len(matched) >= 2 and sum((_number(row['premium']) for row in matched), Decimal(0)) == _number(field.value):
            return True
    return False
