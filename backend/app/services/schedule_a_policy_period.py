"""Dates bound to an explicit contract-year label, never correspondence dates."""
import calendar
from dataclasses import dataclass
from datetime import datetime
import re


_PERIOD = re.compile(
    r"\b(?:Contract/Policy|Contract|Policy)\s+Year\s+from\s*:?\s*"
    r"([0-9]{1,2}(?:/[0-9]{1,2})?/[0-9]{4})\s*(?:[-–—]|to|through)\s*"
    r"([0-9]{1,2}(?:/[0-9]{1,2})?/[0-9]{4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ContractPeriod:
    beginning: str
    ending: str
    source_text: str
    month_precision: bool


def explicit_contract_periods(text: str) -> list[ContractPeriod]:
    periods = []
    for match in _PERIOD.finditer(str(text or "")):
        try:
            dates = []
            for index, token in enumerate(match.groups()):
                parts = [int(part) for part in token.split("/")]
                if len(parts) == 2:
                    month, year = parts
                    day = calendar.monthrange(year, month)[1] if index else 1
                else:
                    month, day, year = parts
                dates.append(datetime(year, month, day))
            if dates[0] > dates[1]:
                continue
        except ValueError:
            continue
        periods.append(ContractPeriod(dates[0].strftime("%m/%d/%Y"),
            dates[1].strftime("%m/%d/%Y"), match.group(0),
            any(token.count("/") == 1 for token in match.groups())))
    return periods
