"""Find covered-lives maxima using preserved text columns, not nearby numbers."""
from dataclasses import dataclass
import re


_HEADER = re.compile(
    r"(?:approx(?:imate)?\.?\s*(?:#|number|no\.?)?\s*(?:of\s+)?)?"
    r"(?:persons?\s+covered|group\s+covered|(?:employee\s+)?lives(?:\s+covered)?|"
    r"employee\s+count|subscribers?\s*/\s*members?)\s*:?", re.I)
_COUNT = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)\Z")


@dataclass(frozen=True)
class LivesColumnSelection:
    value: str
    page: int
    row: int
    source_text: str
    counts: tuple[str, ...]
    contracts: tuple[str, ...]
    ambiguous: bool = False


def lives_column_selections(pages: list[tuple[int, str]]) -> list[LivesColumnSelection]:
    selections = []
    for page, text in pages:
        lines = text.splitlines()
        for index, header in enumerate(lines):
            cells = list(re.finditer(r"\S.*?(?=\s{2,}|$)", header))
            # Unaligned OCR/paragraphs do not establish a numeric column.
            if len(cells) < 2:
                if _HEADER.fullmatch(header.strip()):
                    nearby = lines[index + 1:index + 5]
                    if any(len(re.findall(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])", row)) > 1
                           for row in nearby):
                        selections.append(LivesColumnSelection("", page, index + 1,
                            "Column positions cannot be verified:\n" + header + "\n" + "\n".join(nearby),
                            (), (), True))
                continue
            for position, cell in enumerate(cells):
                if not _HEADER.fullmatch(cell.group().strip()):
                    continue
                start = cell.start()
                end = cells[position + 1].start() if position + 1 < len(cells) else None
                policy_position = next((i for i, item in enumerate(cells)
                    if re.fullmatch(r"(?:policy|contract)(?:\s*(?:/|or)\s*policy)?\s+(?:number|no\.?|id|#)", item.group().strip(), re.I)), None)
                counts, source_rows, unreadable_rows, contracts = [], [], [], set()
                incomplete = False
                for offset, line in enumerate(lines[index + 1:], index + 1):
                    if not line.strip():
                        if counts:
                            break
                        # At most two wrapped header lines before data.
                        if offset > index + 2:
                            break
                        continue
                    parts = list(re.finditer(r"\S.*?(?=\s{2,}|$)", line))
                    if counts and not re.search(r"\s{2,}", line.strip()):
                        break
                    value = line[start:end].strip()
                    if value.lower() in {"covered", "count", "members", "persons"}:
                        continue
                    if re.search(r"\btotal\b", line, re.I) and not value:
                        break
                    # Cell slicing must not accept the trailing digit of a value
                    # which actually started in an adjacent column.
                    crossing = start > 0 and len(line) > start and not line[start - 1].isspace() and not line[start].isspace()
                    if not crossing and _COUNT.fullmatch(value):
                        counts.append(value.replace(",", ""))
                        source_rows.append((offset + 1, line))
                        if policy_position is not None:
                            pstart = cells[policy_position].start()
                            pend = cells[policy_position + 1].start() if policy_position + 1 < len(cells) else None
                            contract = line[pstart:pend].strip()
                            if contract:
                                contracts.add(contract.casefold())
                    elif len(parts) >= 2:
                        # Do not turn a partly unreadable table into a claimed
                        # authoritative maximum of only the readable rows.
                        incomplete = True
                        unreadable_rows.append(line)
                if not counts:
                    if incomplete:
                        selections.append(LivesColumnSelection("", page, index + 1,
                            header + "\nUnreadable covered-lives rows:\n" + "\n".join(unreadable_rows),
                            (), tuple(sorted(contracts)), True))
                    continue
                highest = max(counts, key=int)
                selected_row = next(row for (row, _line), value in zip(source_rows, counts) if value == highest)
                evidence = ("Highest lives-covered count selected: " + highest + "\n"
                            + "Counts in covered-lives column: " + ", ".join(counts) + "\n"
                            + header + "\n" + "\n".join(line for _, line in source_rows))
                if unreadable_rows:
                    evidence += "\nUnreadable covered-lives rows:\n" + "\n".join(unreadable_rows)
                selections.append(LivesColumnSelection(highest, page, selected_row, evidence,
                    tuple(counts), tuple(sorted(contracts)), incomplete or len(contracts) > 1))
    return selections
