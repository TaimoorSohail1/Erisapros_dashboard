"""Dates bound to an explicit contract-year label, never correspondence dates."""
import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
import re


_MONTH_NAME = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_PERIOD_DATE = (
    rf"(?:[0-9]{{4}}-[0-9]{{1,2}}-[0-9]{{1,2}}|[0-9]{{1,2}}-[0-9]{{1,2}}-[0-9]{{4}}"
    rf"|[0-9]{{1,2}}(?:/[0-9]{{1,2}})?/[0-9]{{4}}|{_MONTH_NAME}\s+[0-9]{{1,2}},?\s+[0-9]{{4}})"
)
_PERIOD = re.compile(
    r"\b(?:(?:Contract/Policy|Contract|Policy)\s+Year\s+from|Date\s+Range\s+for\s+Period"
    r"|(?P<report_period>Date\s+for\s+Period\s*:\s*from)|(?:Policy|Contract)\s+Period\s*:)"
    rf"\s*:?\s*(?P<start>{_PERIOD_DATE})\s*(?:[-\u2013\u2014]|to|through)\s*(?P<end>{_PERIOD_DATE})\b",
    re.IGNORECASE,
)
_REPORT_HEADER = re.compile(
    r"\b(insurance|supplemental\s+compensation)\s+data\s+for\s+schedule\s+a\b",
    re.IGNORECASE,
)


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
    text = str(text or "")
    periods = []
    for match in _PERIOD.finditer(text):
        if match["report_period"]:
            headers = list(_REPORT_HEADER.finditer(text, 0, match.start()))
            if not headers or headers[-1][1].casefold() != "insurance":
                continue
        tokens = (match["start"], match["end"])
        try:
            dates = []
            for index, token in enumerate(tokens):
                if "-" in token:
                    date_format = "%Y-%m-%d" if len(token.split("-")[0]) == 4 else "%m-%d-%Y"
                    dates.append(datetime.strptime(token, date_format))
                    continue
                if "/" not in token:
                    token = re.sub(r"\s+", " ", token.replace(",", "")).strip()
                    date_format = "%b %d %Y" if len(token.split()[0]) == 3 else "%B %d %Y"
                    dates.append(datetime.strptime(token, date_format))
                    continue
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
            any(token.count("/") == 1 for token in tokens),
            original_ending=original_ending.strftime("%m/%d/%Y") if adjusted else None,
            adjusted_to_twelve_months=adjusted,
        ))
    return periods
