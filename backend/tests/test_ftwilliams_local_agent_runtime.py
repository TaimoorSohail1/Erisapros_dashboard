import asyncio
import os
import tempfile
from pathlib import Path

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
