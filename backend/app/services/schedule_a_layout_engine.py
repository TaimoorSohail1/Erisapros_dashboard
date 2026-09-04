"""Carrier-neutral Schedule A extraction from PDF word geometry.

The older universal parser receives flattened text.  On columnar carrier
statements that can turn the next column's heading into a field value.  This
module keeps every PDF word's page and bounding box, identifies label anchors,
and reads values from the same row or the cell immediately below the anchor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import re
from typing import Iterable

from app.models import NormalizedExtractionField, SourceEvidence


_TOKEN = re.compile(r"[a-z0-9]+")
_DATE = re.compile(r"\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:\d{2}|\d{4})\b")
_EIN = re.compile(r"\b\d{2}-\d{7}\b")
_NAIC = re.compile(r"\b\d{4,6}\b")
_INTEGER = re.compile(r"\b\d[\d,]*\b")
_MONEY = re.compile(r"(?:\$\s*)?\(?\d[\d,]*(?:\.\d{1,2})?\)?")


@dataclass(frozen=True)
class LayoutWord:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class LayoutLine:
    row: int
    words: list[LayoutWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(item.text for item in sorted(self.words, key=lambda item: item.x0)).strip()

    @property
    def top(self) -> float:
        return min((item.top for item in self.words), default=0.0)

    @property
    def bottom(self) -> float:
        return max((item.bottom for item in self.words), default=0.0)


@dataclass
class LayoutPage:
    number: int
    width: float
    height: float
    words: list[LayoutWord] = field(default_factory=list)

    def lines(self, *, tolerance: float = 3.0) -> list[LayoutLine]:
        lines: list[LayoutLine] = []
        for item in sorted(self.words, key=lambda word: (word.top, word.x0)):
            target = next(
                (
                    line
                    for line in reversed(lines[-4:])
                    if abs(line.top - item.top) <= tolerance
                    or min(line.bottom, item.bottom) - max(line.top, item.top) >= 0.5
                ),
                None,
            )
            if target is None:
                target = LayoutLine(row=len(lines))
                lines.append(target)
            target.words.append(item)
        for index, line in enumerate(sorted(lines, key=lambda item: item.top)):
            line.row = index
            line.words.sort(key=lambda item: item.x0)
        return sorted(lines, key=lambda item: item.row)


@dataclass(frozen=True)
class _FieldSpec:
    label: str
    kind: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class _Anchor:
    spec: _FieldSpec
    line: LayoutLine
    first: int
    last: int

    @property
    def x0(self) -> float:
        return self.line.words[self.first].x0

    @property
    def x1(self) -> float:
        return self.line.words[self.last].x1

    @property
    def top(self) -> float:
        return min(word.top for word in self.line.words[self.first : self.last + 1])

    @property
    def bottom(self) -> float:
        return max(word.bottom for word in self.line.words[self.first : self.last + 1])


_SPECS = (
    _FieldSpec(
        "1a. Name of Insurance Company",
        "carrier",
        (
            "name of insurance company",
            "name of insurance carrier",
            "name of carrier",
            "insurance carrier name",
            "carrier name",
        ),
    ),
    _FieldSpec(
        "1b. Insurance Carrier EIN",
        "ein",
        ("insurance carrier ein", "carrier ein", "ein of insurance carrier", "ein insurance carrier", "ein"),
    ),
    _FieldSpec(
        "1c. NAIC Code",
        "naic",
        ("naic code", "naic number", "carrier naic code", "insurance carrier naic code", "naic"),
    ),
    _FieldSpec(
        "1d. Contract/Policy Number",
        "identifier",
        (
            "contract policy number",
            "contract or identification number",
            "contract identification policy number",
            "contract number",
            "policy number",
            "group policy",
            "group number",
        ),
    ),
    _FieldSpec(
        "1e. Persons Covered (End of Policy Year)",
        "integer",
        (
            "persons covered end of policy year",
            "number of persons covered",
            "number covered at end of year",
            "approximate number of persons insured",
            "covered lives",
            "number of members",
            "number of subscribers",
        ),
    ),
    _FieldSpec(
        "10a. Total premiums or subscription charges paid to carrier",
        "money",
        (
            "total premiums or subscription charges paid to carrier",
            "total payments made to carrier",
            "premiums paid to carrier",
            "total premium paid to carrier",
            "gross premiums paid",
            "total premium paid",
        ),
    ),
)


def extract_pdf_layout(file_bytes: bytes) -> list[LayoutPage]:
    """Return structured pages using the PDF's real word bounding boxes."""
    try:
        import pdfplumber
    except ImportError:
        return []

    pages: list[LayoutPage] = []
    try:
        with pdfplumber.open(BytesIO(file_bytes)) as document:
            for number, page in enumerate(document.pages, start=1):
                raw_words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
                words = [
                    LayoutWord(
                        text=str(item.get("text") or "").strip(),
                        x0=round(float(item["x0"]), 3),
                        top=round(float(item["top"]), 3),
                        x1=round(float(item["x1"]), 3),
                        bottom=round(float(item["bottom"]), 3),
                    )
                    for item in raw_words
                    if str(item.get("text") or "").strip()
                ]
                pages.append(
                    LayoutPage(
                        number=number,
                        width=float(page.width),
                        height=float(page.height),
                        words=words,
                    )
                )
    except Exception:
        return []
    return pages


def extract_layout_aware_schedule_a_fields(
    file_bytes: bytes,
) -> list[NormalizedExtractionField]:
    return extract_layout_aware_schedule_a_fields_from_pages(extract_pdf_layout(file_bytes))


def extract_layout_aware_schedule_a_fields_from_pages(
    pages: Iterable[LayoutPage],
) -> list[NormalizedExtractionField]:
    candidates: dict[str, list[NormalizedExtractionField]] = {}
    for page in pages:
        lines = page.lines()
        anchors = _find_anchors(lines)
        for anchor in anchors:
            value_groups: list[tuple[list[LayoutWord], str]] = []
            value_words = _same_line_value(anchor, anchors)
            value_words, value = _valid_subspan(anchor.spec.kind, value_words)
            if not value_words or not value:
                value_words = _below_value(anchor, anchors, lines, kind=anchor.spec.kind)
                value_words, value = _valid_subspan(anchor.spec.kind, value_words)
            if value_words and value and not is_layout_label_text(value):
                value_groups.append((value_words, value))
            if anchor.spec.kind == "carrier":
                for table_words in _table_column_values(anchor, anchors, lines):
                    table_words, table_value = _valid_subspan(anchor.spec.kind, table_words)
                    if table_words and table_value and not is_layout_label_text(table_value):
                        value_groups.append((table_words, table_value))
            seen_values: set[str] = set()
            for field_words, field_value in value_groups:
                normalized_value = _normalized(field_value)
                if normalized_value in seen_values:
                    continue
                seen_values.add(normalized_value)
                field = _field_from_words(page, anchor, field_words, field_value, anchors)
                candidates.setdefault(field.field_name, []).append(field)
        for field in _period_fields(page, lines):
            candidates.setdefault(field.field_name, []).append(field)

    output: list[NormalizedExtractionField] = []
    for label, values in candidates.items():
        unique: dict[str, NormalizedExtractionField] = {}
        for value in values:
            unique.setdefault(_normalized(value.value), value)
        selected = values[0]
        distinct = [item.value for item in unique.values()]
        if len(distinct) > 1:
            selected = selected.model_copy(
                update={
                    "candidate_values": distinct,
                    "confidence": 0.5,
                    "decision": "REVIEW_REQUIRED",
                },
                deep=True,
            )
        output.append(selected)
    return output


def is_layout_label_text(value: str) -> bool:
    normalized = _normalized(value)
    if not normalized:
        return False
    if normalized in {
        "of america",
        "insurance company",
        "life insurance company",
        "insurance carrier",
        "name of carrier",
        "service or other organization",
    }:
        return True
    tokens = set(normalized.split())
    if "ein" in tokens and ("coverage" in tokens or "coverages" in tokens or "naic" in tokens or "code" in tokens):
        return True
    known = {
        _normalized(alias)
        for spec in _SPECS
        for alias in spec.aliases
    }
    known.update(
        {
            "ein insurance carrier",
            "insurance carrier naic code",
            "service or other organization",
            "name and address of agent or broker",
            "amount of commissions",
            "amount of fees",
            "coverages",
        }
    )
    if normalized in known:
        return True
    return any(
        len(label.split()) >= 2
        and (normalized.startswith(label) or label.startswith(normalized))
        and len(tokens & set(label.split())) >= 2
        for label in known
    )


def _find_anchors(lines: list[LayoutLine]) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    for line in lines:
        tokens = [_normalized(word.text) for word in line.words]
        flattened = [token for item in tokens for token in item.split()]
        word_token_ranges: list[tuple[int, int]] = []
        cursor = 0
        for token in tokens:
            count = len(token.split())
            word_token_ranges.append((cursor, cursor + count))
            cursor += count
        for spec in _SPECS:
            best: tuple[int, int] | None = None
            aliases = sorted(
                spec.aliases,
                key=lambda item: len(_normalized(item).split()),
                reverse=spec.kind not in {"ein", "naic"},
            )
            for alias in aliases:
                alias_tokens = _normalized(alias).split()
                if not alias_tokens:
                    continue
                for start in range(0, len(flattened) - len(alias_tokens) + 1):
                    if flattened[start : start + len(alias_tokens)] == alias_tokens:
                        first = next(index for index, bounds in enumerate(word_token_ranges) if bounds[0] <= start < bounds[1])
                        end_token = start + len(alias_tokens) - 1
                        last = next(index for index, bounds in enumerate(word_token_ranges) if bounds[0] <= end_token < bounds[1])
                        best = (first, last)
                        break
                if best:
                    break
            if best:
                anchors.append(_Anchor(spec=spec, line=line, first=best[0], last=best[1]))
    return anchors


def _same_line_value(anchor: _Anchor, anchors: list[_Anchor]) -> list[LayoutWord]:
    words = anchor.line.words
    right_limit = min(
        (item.x0 for item in anchors if item.line is anchor.line and item.x0 > anchor.x1 + 2),
        default=float("inf"),
    )
    return [word for word in words[anchor.last + 1 :] if word.x0 < right_limit]


def _below_value(
    anchor: _Anchor,
    anchors: list[_Anchor],
    lines: list[LayoutLine],
    *,
    kind: str,
) -> list[LayoutWord]:
    peer_anchors = [
        item
        for item in anchors
        if item is not anchor and abs(item.top - anchor.top) <= 22
    ]
    right = min((item.x0 - 2 for item in peer_anchors if item.x0 > anchor.x0 + 5), default=float("inf"))
    left = max(0.0, anchor.x0 - 8)
    candidate_lines = [
        line
        for line in lines
        if line.top > anchor.bottom + 1 and line.top - anchor.bottom <= 90
    ]
    for index, line in enumerate(candidate_lines):
        selected = [word for word in line.words if word.x1 >= left and word.x0 < right]
        if not selected:
            continue
        if all(is_layout_label_text(word.text) for word in selected):
            continue
        if kind == "carrier" and right != float("inf") and not any(word.x0 >= right for word in line.words):
            previous = line
            for next_line in candidate_lines[index + 1 : index + 4]:
                if next_line.top - previous.bottom > 8:
                    break
                continuation = [word for word in next_line.words if word.x1 >= left and word.x0 < right]
                if not continuation:
                    break
                selected = [*selected, *continuation]
                previous = next_line
                if any(word.x0 >= right for word in next_line.words):
                    break
        if not _valid_subspan(kind, selected)[1]:
            continue
        return selected
    return []


def _table_column_values(
    anchor: _Anchor,
    anchors: list[_Anchor],
    lines: list[LayoutLine],
) -> list[list[LayoutWord]]:
    peers = [item for item in anchors if item is not anchor and abs(item.top - anchor.top) <= 22]
    right = min((item.x0 - 2 for item in peers if item.x0 > anchor.x0 + 5), default=float("inf"))
    if right == float("inf"):
        return []
    left = max(0.0, anchor.x0 - 8)
    rows: list[list[LayoutWord]] = []
    for line in lines:
        if line.top <= anchor.bottom + 1 or line.top - anchor.bottom > 90:
            continue
        # A populated neighbouring column distinguishes another table row
        # from a wrapped continuation of the current cell.
        if not any(word.x0 >= right for word in line.words):
            continue
        selected = [word for word in line.words if word.x1 >= left and word.x0 < right]
        if selected and _valid_subspan(anchor.spec.kind, selected)[1]:
            rows.append(selected)
    return rows


def _valid_subspan(kind: str, words: list[LayoutWord]) -> tuple[list[LayoutWord], str]:
    if not words:
        return [], ""
    text = " ".join(word.text for word in words).strip(" :")
    if kind == "ein":
        return _matching_words(words, _EIN)
    if kind == "naic":
        return _matching_words(words, _NAIC)
    if kind == "integer":
        return _matching_words(words, _INTEGER)
    if kind == "money":
        matched_words, value = _matching_words(words, _MONEY)
        return matched_words, value.lstrip("$").strip() if value else ""
    if kind == "identifier":
        for word in words:
            value = word.text.strip(" :#")
            if re.fullmatch(r"(?=.*\d)[A-Za-z0-9][A-Za-z0-9./-]{2,}", value):
                return [word], value
        return [], ""
    if kind == "carrier":
        text = re.sub(r"\s+", " ", text).strip(" :")
        text = re.sub(
            r"\b(Assurance|Insurance|Life|Health)(Company|Corporation)\b",
            r"\1 \2",
            text,
            flags=re.IGNORECASE,
        )
        if len(text) < 4 or len(text) > 160 or is_layout_label_text(text):
            return [], ""
        if not re.search(
            r"\b(?:insurance|assurance|health\s+plan|blue\s+cross|blue\s+shield|guardian|anthem|metlife|kaiser|cigna|unum|principal|lincoln|zurich|equitable|sun\s+life)\b",
            text,
            re.IGNORECASE,
        ):
            return [], ""
        return words, text.rstrip(".")
    return [], ""


def _matching_words(words: list[LayoutWord], pattern: re.Pattern[str]) -> tuple[list[LayoutWord], str]:
    for word in words:
        match = pattern.search(word.text)
        if match:
            return [word], match.group(0)
    joined = " ".join(word.text for word in words)
    match = pattern.search(joined)
    return (words, match.group(0)) if match else ([], "")


def _field_from_words(
    page: LayoutPage,
    anchor: _Anchor,
    words: list[LayoutWord],
    value: str,
    anchors: list[_Anchor],
) -> NormalizedExtractionField:
    box = (
        round(min(word.x0 for word in words), 3),
        round(min(word.top for word in words), 3),
        round(max(word.x1 for word in words), 3),
        round(max(word.bottom for word in words), 3),
    )
    peer_x = sorted({item.x0 for item in anchors if abs(item.top - anchor.top) <= 22})
    column = sum(1 for x0 in peer_x if x0 < anchor.x0)
    source = f"{anchor.line.text}\n{value}" if value not in anchor.line.text else anchor.line.text
    return NormalizedExtractionField(
        field_name=anchor.spec.label,
        value=value,
        confidence=0.97,
        page=page.number,
        source_text=source,
        evidence=[
            SourceEvidence(
                provider="Position-aware layout engine",
                page=page.number,
                source_text=source,
                bounding_box=box,
                table_cell=(anchor.line.row + 1, column),
            )
        ],
    )


def _period_fields(page: LayoutPage, lines: list[LayoutLine]) -> list[NormalizedExtractionField]:
    output: list[NormalizedExtractionField] = []
    for line in lines:
        text = line.text
        if not re.search(r"\b(?:policy|contract|data|report|for)\s+(?:year|period)|\bperiod\b", text, re.IGNORECASE):
            continue
        dates = _DATE.findall(text)
        if len(dates) < 2:
            continue
        date_words = [word for word in line.words if _DATE.search(word.text)]
        for label, value, value_word, column in (
            ("1f. Policy Year Beginning Date", dates[0], date_words[0] if date_words else line.words[0], 0),
            ("1g. Policy Year Ending Date", dates[1], date_words[1] if len(date_words) > 1 else line.words[-1], 1),
        ):
            normalized = _normalize_date(value)
            box = (value_word.x0, value_word.top, value_word.x1, value_word.bottom)
            output.append(
                NormalizedExtractionField(
                    field_name=label,
                    value=normalized,
                    confidence=0.97,
                    page=page.number,
                    source_text=text,
                    evidence=[
                        SourceEvidence(
                            provider="Position-aware layout engine",
                            page=page.number,
                            source_text=text,
                            bounding_box=box,
                            table_cell=(line.row + 1, column),
                        )
                    ],
                )
            )
        break
    return output


def _normalize_date(value: str) -> str:
    month, day, year = re.split(r"[/.-]", value)
    numeric_year = int(year)
    if numeric_year < 100:
        numeric_year += 2000
    return f"{int(month):02d}/{int(day):02d}/{numeric_year:04d}"


def _normalized(value: str) -> str:
    return " ".join(_TOKEN.findall(str(value or "").lower()))
