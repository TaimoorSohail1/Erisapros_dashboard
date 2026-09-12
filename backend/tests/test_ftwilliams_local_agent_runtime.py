import asyncio
import os
import tempfile
from pathlib import Path
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
