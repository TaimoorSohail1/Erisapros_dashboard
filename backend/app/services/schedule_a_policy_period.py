"""Dates bound to an explicit contract-year label, never correspondence dates."""
import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
import re


_PERIOD = re.compile(
    r"\b(?:(?:Contract/Policy|Contract|Policy)\s+Year\s+from|Date\s+Range\s+for\s+Period)\s*:?\s*"
    r"([0-9]{1,2}(?:/[0-9]{1,2})?/[0-9]{4})\s*(?:[-–—]|to|through)\s*"
    r"([0-9]{1,2}(?:/[0-9]{1,2})?/[0-9]{4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ContractPeriod:
    beginning: str
    ending: str
    source_text: str
    month_precision: bool
    original_ending: str | None = None
    adjusted_to_twelve_months: bool = False


def _maximum_inclusive_period_end(beginning: datetime) -> datetime:
    try:
        anniversary = beginning.replace(year=beginning.year + 1)
        return anniversary - timedelta(days=1)
    except ValueError:
        # A period beginning on leap day ends on the last valid day of the
        # following February rather than being shortened to February 27.
        return beginning.replace(year=beginning.year + 1, day=28)


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
        original_ending = dates[1]
        maximum_ending = _maximum_inclusive_period_end(dates[0])
        adjusted = original_ending > maximum_ending
        ending = maximum_ending if adjusted else original_ending
        periods.append(ContractPeriod(
            dates[0].strftime("%m/%d/%Y"),
            ending.strftime("%m/%d/%Y"),
            match.group(0),
            any(token.count("/") == 1 for token in match.groups()),
            original_ending=original_ending.strftime("%m/%d/%Y") if adjusted else None,
            adjusted_to_twelve_months=adjusted,
        ))
    return periods
