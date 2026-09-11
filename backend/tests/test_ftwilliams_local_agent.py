import json
import tempfile
from pathlib import Path

import pytest

from app.services.ftwilliams_local_agent import (
    LocalFTWTarget,
    load_local_ftw_targets,
    verify_local_ftw_identity,
)


def sample_target(**overrides):
    values = {
        "label": "Demo Plan",
        "url": "https://www.ftwilliam.com/cgi-bin/index.cgi#go=iframe&Year=2025",
        "plan_name": "Demo Health and Welfare Plan",
        "ein": "12-3456789",
        "plan_number": "501",
        "year": "2025",
    }
    values.update(overrides)
    return LocalFTWTarget.from_dict(values)


def test_local_target_rejects_non_ftw_url():
    with pytest.raises(ValueError, match="ftwilliam.com"):
        sample_target(url="https://example.com/?Year=2025")


def test_local_target_requires_year_in_url():
    with pytest.raises(ValueError, match="year"):
        sample_target(url="https://www.ftwilliam.com/cgi-bin/index.cgi")


def test_load_targets_rejects_duplicate_labels():
    target = {
        "label": "Demo Plan",
        "url": "https://www.ftwilliam.com/cgi-bin/index.cgi?Year=2025",
        "plan_name": "Demo Health and Welfare Plan",
        "ein": "12-3456789",
        "plan_number": "501",
        "year": "2025",
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "targets.json"
        path.write_text(json.dumps({"targets": [target, target]}), encoding="utf-8")
        with pytest.raises(ValueError, match="unique"):
            load_local_ftw_targets(path)


def test_identity_verification_requires_login_first():
    result = verify_local_ftw_identity(
        sample_target(),
        "",
        expected_account="HighlandTech",
        password_visible=True,
    )

    assert result.success is False
    assert result.state == "LOGIN_REQUIRED"


def test_identity_verification_requires_exact_account_plan_ein_number_and_year():
    target = sample_target()
    correct = (
        "HighlandTech\n"
        "Demo Health and Welfare Plan\n"
        "Details: EIN: 12-3456789 • PN: 501\n"
        "5500 - 2025"
    )

    assert verify_local_ftw_identity(
        target,
        correct,
        expected_account="HighlandTech",
        password_visible=False,
    ).success
    assert verify_local_ftw_identity(
        target,
        correct.replace("PN: 501", "PN: 502"),
        expected_account="HighlandTech",
        password_visible=False,
    ).state == "INVALID_TARGET"
    assert verify_local_ftw_identity(
        target,
        correct.replace("HighlandTech", "Another Account"),
        expected_account="HighlandTech",
        password_visible=False,
    ).state == "WRONG_ACCOUNT"
