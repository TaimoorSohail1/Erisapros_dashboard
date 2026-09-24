from __future__ import annotations

import re


_STRUCTURED_ADDRESS_LABEL = re.compile(
    r"\b(?:ADDRESS(?:\s+LINE\s+[12])?|CITY|STATE|ST|ZIP(?:\s+CODE)?)\s*:",
    re.IGNORECASE,
)
_US_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def broker_address_semantic_issue(field_name: str, value: object) -> str | None:
    """Reject parser fragments that combine structured broker address fields."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None

    normalized_field = str(field_name or "").strip().lower()
    if normalized_field == "city" and (
        _STRUCTURED_ADDRESS_LABEL.search(text)
        or _US_ZIP.search(text)
        or re.search(r",\s*[A-Z]{2}(?:\s|$)", text, re.IGNORECASE)
    ):
        return (
            "expected a city name only; move any street, state, or ZIP data "
            "to the matching broker address field"
        )

    if normalized_field in {"address_line_1", "address_line_2"} and re.search(
        r"\b(?:CITY|STATE|ST|ZIP(?:\s+CODE)?)\s*:",
        text,
        re.IGNORECASE,
    ):
        return (
            "expected a street/address line only; move any city, state, or ZIP data "
            "to the matching broker address field"
        )
    return None
