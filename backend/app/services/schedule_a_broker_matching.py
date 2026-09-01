from __future__ import annotations

import re
from typing import Mapping

from app.models import ScheduleABrokerMatch, ScheduleABrokerRow


def match_schedule_a_brokers(
    extracted_rows: list[ScheduleABrokerRow],
    current_rows: list[dict[str, str]],
    *,
    decisions: Mapping[int, Mapping[str, object]] | None = None,
) -> list[ScheduleABrokerMatch]:
    """Match extracted brokers to FT rows without relying on row order."""
    decisions = decisions or {}
    normalized_current = [_current_broker_row(row) for row in current_rows]
    assigned: set[int] = set()
    matches: list[ScheduleABrokerMatch] = []

    for extracted_index, row in enumerate(extracted_rows):
        decision = decisions.get(extracted_index)
        if decision:
            create_new = bool(decision.get("create_new"))
            selected = decision.get("ftw_index")
            if create_new:
                duplicate_rows = [
                    index
                    for index, current in enumerate(normalized_current)
                    if index not in assigned and _same_broker_business_row(row, current)
                ]
                if len(duplicate_rows) == 1:
                    ftw_index = duplicate_rows[0]
                    assigned.add(ftw_index)
                    matches.append(
                        ScheduleABrokerMatch(
                            extracted_index=extracted_index,
                            ftw_index=ftw_index,
                            status="AUTO_MATCHED",
                            resolved=True,
                            reason="Matched an exact existing broker row instead of adding a duplicate.",
                            current_row=normalized_current[ftw_index],
                        )
                    )
                    continue
                matches.append(
                    ScheduleABrokerMatch(
                        extracted_index=extracted_index,
                        status="CONFIRMED_NEW",
                        resolved=True,
                        reason="Reviewer confirmed this is a new FT Williams broker row.",
                    )
                )
                continue
            if selected is None:
                raise ValueError(f"Broker row {extracted_index + 1} needs an FT Williams row or create_new=true.")
            ftw_index = int(selected)
            if ftw_index < 0 or ftw_index >= len(current_rows):
                raise ValueError(f"FT Williams broker row {ftw_index + 1} does not exist.")
            if ftw_index in assigned:
                raise ValueError(f"FT Williams broker row {ftw_index + 1} was assigned more than once.")
            assigned.add(ftw_index)
            matches.append(
                ScheduleABrokerMatch(
                    extracted_index=extracted_index,
                    ftw_index=ftw_index,
                    status="CONFIRMED",
                    resolved=True,
                    reason="Reviewer confirmed the FT Williams broker row.",
                    current_row=normalized_current[ftw_index],
                )
            )
            continue

        available = [
            index
            for index in range(len(current_rows))
            if index not in assigned and _has_broker_content(normalized_current[index])
        ]
        name_matches = [
            index
            for index in available
            if _key(row.name) and _key(row.name) == _key(normalized_current[index].name)
        ]
        identity_scores = {
            index: _secondary_identity_score(row, normalized_current[index]) for index in name_matches
        }
        best_identity_score = max(identity_scores.values(), default=0)
        identity_matches = [
            index for index, score in identity_scores.items() if score == best_identity_score and score > 0
        ]
        address_matches = [
            index
            for index in available
            if _key(row.address_line_1)
            and _key(row.address_line_1) == _key(normalized_current[index].address_line_1)
        ]

        selected: int | None = None
        reason = ""
        if len(identity_matches) == 1:
            selected = identity_matches[0]
            reason = "Matched by broker name and address or ZIP."
        elif len(name_matches) == 1:
            selected = name_matches[0]
            reason = "Matched by a unique broker name."
        elif len(address_matches) == 1:
            selected = address_matches[0]
            reason = "Matched by a unique exact broker address."
        elif len(extracted_rows) == 1 and len(current_rows) == 1 and available:
            selected = available[0]
            reason = "Matched because both documents contain one broker row."

        if selected is not None:
            assigned.add(selected)
            matches.append(
                ScheduleABrokerMatch(
                    extracted_index=extracted_index,
                    ftw_index=selected,
                    status="AUTO_MATCHED",
                    resolved=True,
                    reason=reason,
                    current_row=normalized_current[selected],
                )
            )
            continue

        candidates = identity_matches or name_matches
        matches.append(
            ScheduleABrokerMatch(
                extracted_index=extracted_index,
                status="NEEDS_CONFIRMATION",
                resolved=False,
                reason=(
                    "More than one FT Williams broker row could match; select the correct row."
                    if candidates
                    else "No safe FT Williams broker match was found; select a row or add this as new."
                ),
                candidate_ftw_indexes=candidates or available,
            )
        )

    return matches


def resolved_schedule_a_broker_rows(
    extracted_rows: list[ScheduleABrokerRow],
    current_rows: list[dict[str, str]],
    matches: list[ScheduleABrokerMatch],
) -> list[ScheduleABrokerRow | None]:
    """Return positional overrides while preserving every unmatched FT row."""
    if any(not match.resolved for match in matches):
        raise ValueError("Every Schedule A broker row must be matched or confirmed as new before updating FT Williams.")
    aligned: list[ScheduleABrokerRow | None] = [None] * len(current_rows)
    for match in matches:
        row = extracted_rows[match.extracted_index]
        if match.status == "CONFIRMED_NEW":
            aligned.append(row)
        elif match.ftw_index is not None:
            if aligned[match.ftw_index] is not None:
                raise ValueError(f"FT Williams broker row {match.ftw_index + 1} was assigned more than once.")
            # A source that exposes fewer broker rows than FT Williams is not a
            # complete per-recipient breakdown. Its commission/fee figures may
            # be summary totals, so applying them to one matched row can double
            # the Schedule A totals. The reviewer still confirms identity, but
            # every existing FT broker row is preserved byte-for-byte.
            lacks_row_identity_detail = not any(
                (row.address_line_1, row.address_line_2, row.city, row.state, row.zip_code)
            )
            if len(extracted_rows) < len(current_rows) and lacks_row_identity_detail:
                continue
            current = _current_broker_row(current_rows[match.ftw_index])
            # FT Williams often stores a suite/unit in AddressLine1 while the
            # source document splits it into AddressLine2.  Preserve the two
            # current address lines as one identity unit so we do not combine
            # those representations and duplicate the suite in the payload.
            current_has_address_lines = bool(current.address_line_1 or current.address_line_2)
            aligned[match.ftw_index] = row.model_copy(
                update={
                    "name": current.name or row.name,
                    "address_line_1": (
                        current.address_line_1 if current_has_address_lines else row.address_line_1
                    ),
                    "address_line_2": (
                        current.address_line_2 if current_has_address_lines else row.address_line_2
                    ),
                    "city": current.city or row.city,
                    "state": current.state or row.state,
                    "zip_code": current.zip_code or row.zip_code,
                }
            )
    return aligned


def current_schedule_a_broker_rows(record: dict | None) -> list[dict[str, str]]:
    rows = list((((record or {}).get("query_subparts") or {}).get("Broker") or []))
    if rows:
        return _trim_trailing_empty_broker_rows(rows)
    grouped: dict[int, dict[str, str]] = {}
    for tag, value in ((record or {}).get("query_results") or {}).items():
        parsed = _tag_index(str(tag))
        if parsed is not None and str(value or "").strip():
            grouped.setdefault(parsed, {})[str(tag)] = str(value)
    return _trim_trailing_empty_broker_rows([grouped[index] for index in sorted(grouped)])


def _trim_trailing_empty_broker_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    trimmed = list(rows)
    while trimmed and not _has_broker_content(_current_broker_row(trimmed[-1])):
        trimmed.pop()
    return trimmed


def _has_broker_content(row: ScheduleABrokerRow) -> bool:
    identity_values = (
        row.name,
        row.address_line_1,
        row.address_line_2,
        row.city,
        row.state,
        row.zip_code,
    )
    if any(str(value or "").strip() for value in identity_values):
        return True
    return any(
        str(value or "").strip() not in {"", "0", "0.0", "0.00"}
        for value in (row.commission_total, row.fee_total)
    )


def _current_broker_row(row: Mapping[str, object]) -> ScheduleABrokerRow:
    return ScheduleABrokerRow(
        name=_value(row, "Name"),
        address_line_1=_value(row, "AddressLine1") or None,
        address_line_2=_value(row, "AddressLine2") or None,
        city=_value(row, "City") or None,
        state=(_value(row, "State") or _value(row, "ProvinceOrState") or None),
        zip_code=(_value(row, "ZipCode") or _value(row, "PostalCode") or None),
        organization_code=_value(row, "Code") or None,
        commission_total=_value(row, "CommPdAmt") or None,
        fee_total=_value(row, "FeesPdAmt") or None,
    )


def _value(row: Mapping[str, object], base: str) -> str:
    for tag, value in row.items():
        tag_text = str(tag)
        if tag_text == f"{base}XX" or re.fullmatch(rf"{re.escape(base)}(?:0?[1-9]|[1-9][0-9])", tag_text):
            return str(value or "").strip()
    return ""


def _secondary_identity_score(extracted: ScheduleABrokerRow, current: ScheduleABrokerRow) -> int:
    address_matches = bool(
        _key(extracted.address_line_1) and _key(extracted.address_line_1) == _key(current.address_line_1)
    )
    zip_matches = bool(_key(extracted.zip_code) and _key(extracted.zip_code) == _key(current.zip_code))
    return int(address_matches) + int(zip_matches)


def _same_broker_business_row(extracted: ScheduleABrokerRow, current: ScheduleABrokerRow) -> bool:
    if not _key(extracted.name) or _key(extracted.name) != _key(current.name):
        return False
    if _secondary_identity_score(extracted, current) == 0:
        return False
    for attribute in ("commission_total", "fee_total", "organization_code"):
        proposed = getattr(extracted, attribute)
        if str(proposed or "").strip() and _key(proposed) != _key(getattr(current, attribute)):
            return False
    return True


def _key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _tag_index(tag: str) -> int | None:
    bases = (
        "ProvinceOrState", "AddressLine1", "AddressLine2", "FeesPdText",
        "CommPdAmt", "FeesPdAmt", "ForeignAddy", "PostalCode", "ZipCode",
        "Country", "State", "City", "Code", "Name",
    )
    for base in bases:
        if tag.startswith(base) and re.fullmatch(r"\d{1,2}", tag[len(base):]):
            index = int(tag[len(base):])
            return index if index > 0 else None
    return None
