from __future__ import annotations

import re
from collections import Counter

from app.models import (
    AuditLog,
    FTWilliamsFailureIssueGroup,
    FTWilliamsFailureType,
    FTWilliamsReview,
)


_PLAN_PATTERN = re.compile(r"\b(plan|mapping|customer|identifier|match|ein|pn|ftw id|plan id|customer id)\b", re.I)
_DATA_PATTERN = re.compile(r"\b(field|xml|form|checkbox|edit check|value|line|schedule|payload|invalid)\b", re.I)
_SERVICE_PATTERN = re.compile(
    r"\b(login|session|credential|auth|unauthorized|forbidden|token|permission|network|timeout|connection|service unavailable|gateway|rate limit)\b",
    re.I,
)


def failure_reason(review: FTWilliamsReview, failed_audit: AuditLog | None = None) -> str:
    client_error = review.active_failure_client_error or review.client_error
    details = failed_audit.details if failed_audit and failed_audit.details else {}
    return str(
        (client_error.message if client_error else None)
        or details.get("error")
        or review.active_failure_reason
        or review.error_message
        or (
            "FT Williams update requires verification."
            if review.status.value == "UPDATE_UNKNOWN"
            else "FT Williams update failed."
        )
    ).strip()


def classify_ftwilliams_failure(
    review: FTWilliamsReview,
    failed_audit: AuditLog | None = None,
) -> FTWilliamsFailureType:
    if review.active_failure_type:
        return review.active_failure_type
    client_error = review.active_failure_client_error or review.client_error
    text = " ".join(
        filter(
            None,
            [
                failure_reason(review, failed_audit),
                client_error.next_action if client_error else None,
                review.status.value,
            ],
        )
    )
    if _PLAN_PATTERN.search(text):
        return FTWilliamsFailureType.NEEDS_PLAN_MATCH
    if _DATA_PATTERN.search(text):
        return FTWilliamsFailureType.NEEDS_DATA_FIX
    if _SERVICE_PATTERN.search(text):
        return FTWilliamsFailureType.NEEDS_SERVICE_CHECK
    return FTWilliamsFailureType.NEEDS_RETRY


def short_failure_reason(reason: str, limit: int = 180) -> str:
    plain = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(plain) <= limit:
        return plain
    sentence = re.split(r"(?<=[.!?])\s+", plain, maxsplit=1)[0]
    if 24 <= len(sentence) <= limit:
        return sentence
    return plain[: limit - 1].rstrip() + "…"


def failure_issue_groups(review: FTWilliamsReview) -> tuple[int, list[FTWilliamsFailureIssueGroup]]:
    if review.active_failure_issue_count is not None:
        return review.active_failure_issue_count, list(review.active_failure_issue_groups)
    issues = list(review.edit_check_final_issues or review.edit_check_baseline_issues or [])
    if not issues:
        count = max(1, review.update_remaining_count or 0)
        return count, []

    labels: list[str] = []
    for issue in issues:
        text = " ".join(filter(None, [issue.field_label, issue.message, issue.field_line])).lower()
        if "broker" in text or "provider" in text or str(issue.field_line or "").startswith("3"):
            labels.append("Broker issue")
        elif issue.field_label:
            labels.append(issue.field_label)
        elif issue.code:
            labels.append(issue.code)
        else:
            labels.append("FT Williams issue")

    groups = [
        FTWilliamsFailureIssueGroup(label=label, count=count)
        for label, count in Counter(labels).most_common(3)
    ]
    return len(issues), groups
