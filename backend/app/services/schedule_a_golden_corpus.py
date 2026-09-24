"""Golden-corpus contracts and behavior-level extraction comparisons."""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.models import NormalizedExtractionResult


class GoldenScheduleACase(BaseModel):
    case_id: str
    source_file: str
    source_sha256: str
    expected_fields: dict[str, str] = Field(default_factory=dict)
    expected_brokers: list[dict[str, str | None]] = Field(default_factory=list)
    expected_review: bool = False
    layout_family: str | None = None


class GoldenScheduleAReport(BaseModel):
    case_id: str
    passed: bool
    field_precision: float
    field_recall: float
    broker_exact_match: float
    differences: list[str] = Field(default_factory=list)


class GoldenScheduleACorpusReport(BaseModel):
    passed: bool
    case_count: int
    passed_count: int
    failed_count: int
    mean_field_precision: float
    mean_field_recall: float
    broker_exact_match_rate: float
    cases: list[GoldenScheduleAReport] = Field(default_factory=list)


async def run_golden_corpus(
    manifest_path: str | Path,
    source_root: str | Path,
    extractor: Callable[
        [bytes, str],
        NormalizedExtractionResult | Awaitable[NormalizedExtractionResult],
    ],
) -> GoldenScheduleACorpusReport:
    """Run an approved manifest without storing private source documents in git."""
    manifest = Path(manifest_path)
    root = Path(source_root).resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Golden Schedule A manifest must contain a JSON list of cases.")

    reports: list[GoldenScheduleAReport] = []
    for item in payload:
        case = GoldenScheduleACase.model_validate(item)
        source = (root / case.source_file).resolve()
        if source != root and root not in source.parents:
            raise ValueError(f"Golden source path escapes the configured corpus root: {case.source_file}")
        data = source.read_bytes()
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash.casefold() != case.source_sha256.casefold():
            raise ValueError(
                f"Golden source hash mismatch for {case.case_id}: expected {case.source_sha256}, got {actual_hash}."
            )
        extracted = extractor(data, source.name)
        result = await extracted if inspect.isawaitable(extracted) else extracted
        reports.append(evaluate_golden_case(case, result))

    case_count = len(reports)
    passed_count = sum(1 for report in reports if report.passed)
    return GoldenScheduleACorpusReport(
        passed=passed_count == case_count,
        case_count=case_count,
        passed_count=passed_count,
        failed_count=case_count - passed_count,
        mean_field_precision=_mean(report.field_precision for report in reports),
        mean_field_recall=_mean(report.field_recall for report in reports),
        broker_exact_match_rate=_mean(report.broker_exact_match for report in reports),
        cases=reports,
    )


def evaluate_golden_case(
    case: GoldenScheduleACase,
    result: NormalizedExtractionResult,
) -> GoldenScheduleAReport:
    actual_fields = {field.field_name: field.value for field in result.fields if str(field.value or "").strip()}
    matched = 0
    differences: list[str] = []
    for label, expected in case.expected_fields.items():
        actual = actual_fields.get(label)
        if actual is not None and _equivalent_value(label, expected, actual):
            matched += 1
        else:
            differences.append(f"Field {label!r}: expected {expected!r}, got {actual!r}.")

    extra = sorted(set(actual_fields) - set(case.expected_fields))
    differences.extend(f"Unexpected field {label!r}: {actual_fields[label]!r}." for label in extra)
    precision_denominator = len(actual_fields)
    precision = matched / precision_denominator if precision_denominator else 1.0
    recall = matched / len(case.expected_fields) if case.expected_fields else 1.0

    actual_brokers = [_normalized_broker(row.model_dump(mode="json")) for row in result.schedule_a_broker_rows]
    expected_brokers = [_normalized_broker(row) for row in case.expected_brokers]
    broker_match = 1.0 if actual_brokers == expected_brokers else 0.0
    if not broker_match:
        differences.append(f"Broker rows differ: expected {expected_brokers!r}, got {actual_brokers!r}.")

    quality = result.raw.get("extraction_quality", {}) if isinstance(result.raw, dict) else {}
    actual_review = quality.get("decision") == "REVIEW_REQUIRED"
    if case.expected_review != actual_review:
        differences.append(
            f"Review decision differs: expected {case.expected_review}, got {actual_review}."
        )

    return GoldenScheduleAReport(
        case_id=case.case_id,
        passed=not differences,
        field_precision=round(precision, 6),
        field_recall=round(recall, 6),
        broker_exact_match=broker_match,
        differences=differences,
    )


def _equivalent_value(label: str, expected: Any, actual: Any) -> bool:
    if any(token in label.lower() for token in ("amount", "premium", "commission", "fee")):
        expected_decimal = _decimal(expected)
        actual_decimal = _decimal(actual)
        if expected_decimal is not None and actual_decimal is not None:
            return expected_decimal == actual_decimal
    return _text(expected).casefold() == _text(actual).casefold()


def _normalized_broker(row: dict[str, Any]) -> dict[str, str]:
    keys = (
        "name",
        "address_line_1",
        "address_line_2",
        "city",
        "state",
        "zip_code",
        "organization_code",
        "commission_total",
        "fee_total",
    )
    normalized: dict[str, str] = {}
    for key in keys:
        value = row.get(key)
        if key in {"commission_total", "fee_total"}:
            number = _decimal(value)
            normalized[key] = format(number, "f") if number is not None else ""
        else:
            normalized[key] = _text(value).casefold()
    return normalized


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _decimal(value: Any) -> Decimal | None:
    clean = re.sub(r"[^0-9.()-]", "", str(value or "")).replace("(", "-").replace(")", "")
    if not clean:
        return None
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def _mean(values) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 1.0
