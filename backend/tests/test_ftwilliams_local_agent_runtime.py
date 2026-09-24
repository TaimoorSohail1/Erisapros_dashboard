import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import httpx

from app.services.ftwilliams_local_agent_runtime import (
    FTWLocalAgentRunner,
    LocalAgentActionResult,
    LocalAgentApiClient,
    PersistentFTWBrowser,
)
from app.services.windows_secret_store import load_secret_json, save_secret_json


def run_async(value):
    return asyncio.run(value)


class FakeAgentApi:
    def __init__(self, claim=None):
        self.claim_result = claim or {"job": None, "claim_token": None}
        self.heartbeats = []
        self.completions = []

    async def heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)

    async def claim(self):
        return self.claim_result

    async def control(self):
        return {"pause_requested": False, "browser_allowed": True}

    async def complete(self, job_id, claim_token, result):
        self.completions.append((job_id, claim_token, result))

    async def close(self):
        return None


class FakeAgentBrowser:
    def __init__(self, *, ready=True, result=None):
        self.ready = ready
        self.result = result or LocalAgentActionResult("SUBMITTED", "Submitted")
        self.executions = []

    async def session_ready(self):
        return self.ready

    async def execute(self, job):
        self.executions.append(job)
        return self.result

    async def close(self):
        return None


class StubBrowserPage:
    def __init__(self, *, closed=False):
        self.closed = closed
        self.goto_calls = []
        self.url = "about:blank"

    def is_closed(self):
        return self.closed

    def set_default_timeout(self, _timeout):
        return None

    def on(self, _event, _handler):
        return None

    async def goto(self, url, **_kwargs):
        if self.closed:
            raise RuntimeError("Target page has been closed")
        self.goto_calls.append(url)
        self.url = url

    async def wait_for_timeout(self, _timeout):
        return None


class StubBrowserContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    async def new_page(self):
        page = StubBrowserPage()
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True


class StubPlaywright:
    def __init__(self, context):
        self.context = context
        self.chromium = self
        self.stopped = False
        self.launch_kwargs = []

    async def launch_persistent_context(self, *_args, **_kwargs):
        self.launch_kwargs.append(_kwargs)
        return self.context

    async def stop(self):
        self.stopped = True


class StubPlaywrightStarter:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


def test_runner_does_not_claim_a_job_until_ftw_login_is_ready():
    api = FakeAgentApi()
    browser = FakeAgentBrowser(ready=False)

    processed = run_async(FTWLocalAgentRunner(api, browser).run_once())

    assert processed is False
    assert api.heartbeats == [
        {
            "browser_ready": False,
            "login_required": True,
            "last_error": "Sign in to FT Williams in the dedicated ERISAPros browser window.",
        }
    ]
    assert browser.executions == []


def test_runner_executes_and_completes_one_claimed_job():
    job = {"id": "job-1", "filing_id": "filing-1"}
    api = FakeAgentApi(claim={"job": job, "claim_token": "one-time-token"})
    browser = FakeAgentBrowser()

    processed = run_async(FTWLocalAgentRunner(api, browser).run_once())

    assert processed is True
    assert api.heartbeats == [{"browser_ready": True}]
    assert browser.executions == [job]
    assert api.completions[0][0:2] == ("job-1", "one-time-token")
    assert api.completions[0][2].state == "SUBMITTED"


def test_persistent_browser_reopens_after_the_client_closes_its_window(tmp_path):
    old_page = StubBrowserPage(closed=True)
    old_context = StubBrowserContext(old_page)
    old_playwright = StubPlaywright(old_context)
    new_page = StubBrowserPage()
    new_context = StubBrowserContext(new_page)
    new_playwright = StubPlaywright(new_context)

    class ReadyBrowser(PersistentFTWBrowser):
        async def _page_text(self):
            return "HighlandTech", False

    browser = ReadyBrowser(tmp_path / "profile", expected_account="HighlandTech")
    browser._page = old_page
    browser._context = old_context
    browser._playwright = old_playwright

    with patch(
        "playwright.async_api.async_playwright",
        return_value=StubPlaywrightStarter(new_playwright),
    ):
        ready = run_async(browser.session_ready())

    assert ready is True
    assert old_context.closed is True
    assert old_playwright.stopped is True
    assert new_page.goto_calls == ["https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home"]
    assert "--start-minimized" not in new_playwright.launch_kwargs[0]["args"]


def test_persistent_browser_automatically_logs_in_and_verifies_the_expected_account(tmp_path):
    credentials = {
        "company_code": "company-01",
        "username": "client.user",
        "password": "client-password",
    }

    class AutoLoginBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(
                tmp_path / "profile",
                expected_account="HighlandTech",
                login_credentials=credentials,
            )
            self._page = StubBrowserPage()
            self.responses = iter([("Enter Login Information", True), ("HighlandTech", False)])
            self.submissions = []

        async def start(self):
            return None

        async def _page_text(self):
            return next(self.responses)

        async def _submit_saved_login(self):
            self.submissions.append(dict(self.login_credentials))

        async def _post_login_search_ready(self):
            return True

    browser = AutoLoginBrowser()

    ready = run_async(browser.session_ready())

    assert ready is True
    assert browser.submissions == [credentials]


def test_failed_automatic_login_waits_before_retrying_to_protect_the_account(tmp_path):
    class RejectedLoginBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(
                tmp_path / "profile",
                expected_account="HighlandTech",
                login_credentials={
                    "company_code": "company-01",
                    "username": "client.user",
                    "password": "wrong-password",
                },
            )
            self._page = StubBrowserPage()
            self.submissions = 0

        async def start(self):
            return None

        async def _page_text(self):
            return "Enter Login Information", True

        async def _submit_saved_login(self):
            self.submissions += 1

    browser = RejectedLoginBrowser()

    first = run_async(browser.session_ready())
    second = run_async(browser.session_ready())

    assert first is False
    assert second is False
    assert browser.submissions == 1
    assert "Automatic login" in browser.login_required_message


def test_saved_login_is_typed_only_into_the_expected_ft_williams_fields(tmp_path):
    class Field:
        def __init__(self):
            self.value = ""
            self.keys = []

        async def is_visible(self):
            return True

        async def fill(self, value):
            self.value = value

        async def press(self, key):
            self.keys.append(key)

    class Locator:
        def __init__(self, fields):
            self.fields = fields

        async def count(self):
            return len(self.fields)

        def nth(self, index):
            return self.fields[index]

        @property
        def first(self):
            return self.fields[0]

    company, username, password = Field(), Field(), Field()

    class LoginFrame:
        def locator(self, selector):
            if "password" in selector:
                return Locator([password])
            if "company" in selector:
                return Locator([company])
            if "user" in selector:
                return Locator([username])
            return Locator([])

    browser = PersistentFTWBrowser(
        tmp_path / "profile",
        expected_account="HighlandTech",
        login_credentials={
            "company_code": "company-01",
            "username": "client.user",
            "password": "client-password",
        },
    )
    browser._page = type("LoginPage", (), {"url": "https://www.ftwilliam.com/cgi-bin/index.cgi", "frames": [LoginFrame()]})()

    run_async(browser._submit_saved_login())

    assert company.value == "company-01"
    assert username.value == "client.user"
    assert password.value == "client-password"
    assert password.keys == ["Enter"]


def test_legacy_ft_williams_login_button_is_clicked_and_session_becomes_ready(tmp_path):
    class Control:
        def __init__(self, *, on_click=None):
            self.value = ""
            self.keys = []
            self.clicks = 0
            self.on_click = on_click

        async def is_visible(self):
            return True

        async def fill(self, value):
            self.value = value

        async def press(self, key):
            self.keys.append(key)

        async def click(self):
            self.clicks += 1
            if self.on_click:
                self.on_click()

    class Locator:
        def __init__(self, controls):
            self.controls = controls

        async def count(self):
            return len(self.controls)

        def nth(self, index):
            return self.controls[index]

        @property
        def first(self):
            return self.controls[0]

    state = {"logged_in": False}
    company, username, password = Control(), Control(), Control()
    login = Control(on_click=lambda: state.__setitem__("logged_in", True))

    class LegacyLoginFrame:
        def locator(self, selector):
            if "password" in selector:
                return Locator([password])
            if "company" in selector:
                return Locator([company])
            if "user" in selector:
                return Locator([username])
            return Locator([])

        def get_by_role(self, role, **_kwargs):
            return Locator([login] if role == "button" else [])

    class LegacyLoginPage(StubBrowserPage):
        def __init__(self):
            super().__init__()
            self.url = "https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home"
            self.frames = [LegacyLoginFrame()]

    class LegacyLoginBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(
                tmp_path / "profile",
                expected_account="HighlandTech",
                login_credentials={
                    "company_code": "highland01",
                    "username": "highlandtech.test",
                    "password": "client-password",
                },
            )
            self._page = LegacyLoginPage()

        async def start(self):
            return None

        async def _page_text(self):
            return (
                ("HighlandTech", False)
                if state["logged_in"]
                else ("Enter Login Information\nhighlandtech.test", True)
            )

        async def _post_login_search_ready(self):
            return True

    browser = LegacyLoginBrowser()

    assert run_async(browser.session_ready()) is True
    assert login.clicks == 1
    assert password.keys == []


def test_first_login_waits_for_ftw_search_surface_before_reporting_ready(tmp_path):
    class FirstLoginBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(
                tmp_path / "profile",
                expected_account="HighlandTech",
                login_credentials={
                    "company_code": "highland01",
                    "username": "highlandtech.test",
                    "password": "client-password",
                },
            )
            self._page = StubBrowserPage()
            self._page.url = "https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home"
            self.logged_in = False
            self.search_ready_checks = 0

        async def start(self):
            return None

        async def _page_text(self):
            if self.logged_in:
                return "HighlandTech", False
            return "Enter Login Information", True

        async def _submit_saved_login(self):
            self.logged_in = True

        async def _post_login_search_ready(self):
            self.search_ready_checks += 1
            return self.search_ready_checks >= 3

    browser = FirstLoginBrowser()

    assert run_async(browser.session_ready()) is True
    assert browser.search_ready_checks == 3


def test_target_navigation_retries_empty_first_load_without_duplicate_click(tmp_path):
    class Candidate:
        def __init__(self):
            self.clicks = 0

        async def click(self, **_kwargs):
            self.clicks += 1

    class RetryBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(tmp_path / "profile", expected_account="HighlandTech", timeout_ms=9_000)
            self._page = StubBrowserPage()
            self.verifications = iter([
                SimpleNamespace(success=False, state="INVALID_TARGET", message="The first FTW page was empty."),
                SimpleNamespace(success=False, state="INVALID_TARGET", message="The FTW search was still loading."),
                SimpleNamespace(success=True, state="VERIFIED", message="Verified"),
            ])
            self.candidate = Candidate()

        async def start(self):
            return None

        async def _wait_for_identity(self, _target, **_kwargs):
            return next(self.verifications)

        async def _post_login_search_ready(self):
            return True

        async def _single_bring_forward_candidate(self):
            return self.candidate

        async def _page_text(self):
            return "HighlandTech", False

    browser = RetryBrowser()
    target_url = (
        "https://www.ftwilliam.com/cgi-bin/index.cgi?#go=iframe&page=/cgi-bin/PlanDoc2.cgi"
        "&PerformDoc5500=1&plan=2402914769,2950067216&Year=2025"
    )
    job = {
        "id": "job-first-login",
        "filing_id": "filing-first-login",
        "expected_account": "HighlandTech",
        "target_url": target_url,
        "expected_plan_name": "BTIG LLC Health and Welfare Plan TEST",
        "expected_ein": "04-3695739",
        "expected_plan_number": "501",
        "expected_year": "2025",
    }

    result = run_async(browser.execute(job))

    assert result.state == "SUBMITTED"
    assert browser._page.goto_calls.count(target_url) == 3
    assert browser.candidate.clicks == 1


def test_target_navigation_waits_for_account_header_before_rejecting_valid_plan(tmp_path):
    """A hydrated plan iframe can appear before the outer FTW account header."""

    class Candidate:
        def __init__(self):
            self.clicks = 0

        async def click(self, **_kwargs):
            self.clicks += 1

    class DelayedAccountBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(tmp_path / "profile", expected_account="HighlandTech", timeout_ms=9_000)
            self._page = StubBrowserPage()
            self.candidate = Candidate()
            self.full_identity = (
                "HighlandTech\n"
                "Socure, Inc. Health And Welfare Plan\n"
                "Details: EIN: 90-0888790 • PN: 501\n"
                "5500 - 2025"
            )
            self.responses = [self.full_identity.replace("HighlandTech\n", "")]

        async def start(self):
            return None

        async def _page_text(self):
            return (self.responses.pop(0) if self.responses else self.full_identity), False

        async def _single_bring_forward_candidate(self):
            return self.candidate

    browser = DelayedAccountBrowser()
    target_url = (
        "https://www.ftwilliam.com/cgi-bin/index.cgi?#go=iframe&page=/cgi-bin/PlanDoc2.cgi"
        "&PerformDoc5500=1&plan=2405648717,2954016184&Year=2025"
    )
    job = {
        "id": "job-delayed-account",
        "filing_id": "filing-delayed-account",
        "expected_account": "HighlandTech",
        "target_url": target_url,
        "expected_plan_name": "Socure, Inc. Health And Welfare Plan",
        "expected_ein": "90-0888790",
        "expected_plan_number": "501",
        "expected_year": "2025",
    }

    result = run_async(browser.execute(job))

    assert result.state == "SUBMITTED"
    assert browser.candidate.clicks == 1


def test_saved_login_is_blocked_outside_the_ft_williams_domain(tmp_path):
    browser = PersistentFTWBrowser(
        tmp_path / "profile",
        expected_account="HighlandTech",
        login_credentials={
            "company_code": "company-01",
            "username": "client.user",
            "password": "client-password",
        },
    )
    browser._page = type("WrongPage", (), {"url": "https://example.com/login", "frames": []})()

    with pytest.raises(RuntimeError, match="not on FT Williams"):
        run_async(browser._submit_saved_login())


def test_mfa_page_is_preserved_until_the_client_completes_it(tmp_path):
    class MfaBrowser(PersistentFTWBrowser):
        def __init__(self):
            super().__init__(tmp_path / "profile", expected_account="HighlandTech")
            self._page = StubBrowserPage()
            self.responses = iter([
                ("Enter verification code", False),
                ("HighlandTech", False),
            ])

        async def start(self):
            return None

        async def _page_text(self):
            return next(self.responses)

    browser = MfaBrowser()

    assert run_async(browser.session_ready()) is False
    assert run_async(browser.session_ready()) is True
    assert browser._page.goto_calls == ["https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home"]


def test_runner_resumes_automatically_on_the_first_cycle_after_login():
    class LoginThenReadyBrowser(FakeAgentBrowser):
        def __init__(self):
            super().__init__()
            self.readiness = iter([False, True])

        async def session_ready(self):
            return next(self.readiness)

    job = {"id": "job-resume", "filing_id": "filing-resume"}
    api = FakeAgentApi(claim={"job": job, "claim_token": "resume-token"})
    browser = LoginThenReadyBrowser()
    runner = FTWLocalAgentRunner(api, browser)

    first = run_async(runner.run_once())
    second = run_async(runner.run_once())

    assert first is False
    assert second is True
    assert api.heartbeats[0]["login_required"] is True
    assert api.heartbeats[1] == {"browser_ready": True}
    assert [item[0] for item in api.completions] == ["job-resume"]


def test_agent_api_rejects_plain_http_except_localhost():
    with pytest.raises(ValueError, match="HTTPS"):
        LocalAgentApiClient("http://dashboard.example.com", "token")
    with pytest.raises(ValueError, match="HTTPS"):
        LocalAgentApiClient("http://localhost.evil.example", "token")


def test_agent_completion_allows_server_readback_within_cloudfront_limit():
    observed = {}

    async def handler(request):
        observed.update(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "SUBMITTED"})

    async def exercise():
        client = LocalAgentApiClient(
            "https://dashboard.example.com",
            "token",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.complete(
                "job-1",
                "claim-token",
                LocalAgentActionResult("SUBMITTED", "Submitted"),
            )
        finally:
            await client.close()

    run_async(exercise())

    assert observed["read"] == 115.0


def test_agent_can_revoke_its_own_device_during_uninstall():
    observed = {}

    async def handler(request):
        observed["method"] = request.method
        observed["path"] = request.url.path
        observed["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "REVOKED"})

    async def exercise():
        client = LocalAgentApiClient(
            "https://dashboard.example.com",
            "device-token",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.revoke_self()
        finally:
            await client.close()

    result = run_async(exercise())

    assert result["status"] == "REVOKED"
    assert observed == {
        "method": "POST",
        "path": "/api/ftwilliams/local-agent/agent/revoke",
        "authorization": "Bearer device-token",
    }


def test_browser_rejects_a_different_account_job_before_navigation():
    browser = PersistentFTWBrowser("unused-profile", expected_account="HighlandTech")

    result = run_async(browser.execute({"expected_account": "Another Account"}))

    assert result.state == "INVALID_TARGET"
    assert "no navigation or click" in result.message


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI test")
def test_device_token_round_trips_through_dpapi_without_plaintext_on_disk():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "agent.credential"
        payload = {"device_token": "secret-device-token", "server_url": "https://example.com"}

        save_secret_json(path, payload)

        assert b"secret-device-token" not in path.read_bytes()
        assert load_secret_json(path) == payload
