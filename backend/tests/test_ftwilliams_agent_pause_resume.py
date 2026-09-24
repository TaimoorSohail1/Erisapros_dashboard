import asyncio
import pytest
from datetime import datetime, timedelta

from app.config import Settings
from app.models import FTWLocalAgentDevice, FTWLocalAgentDeviceStatus, FTWLocalAgentHeartbeatRequest
from app.models import FTWLocalAgentCompleteRequest, FTWLocalAgentJobStatus
from app.repositories import MemoryRepository
from app.services.ftwilliams_local_agent_jobs import FTWLocalAgentService
from app.services.ftwilliams_local_agent_runtime import FTWLocalAgentRunner, PersistentFTWBrowser
from test_ftwilliams_local_agent_runtime import FakeAgentApi, FakeAgentBrowser, StubBrowserPage
from test_ftwilliams_local_agent import missing_current_year_query


def run(value):
    return asyncio.run(value)


class ControlledApi(FakeAgentApi):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.paused = False
        self.allowed = True
        self.claims = 0

    async def control(self):
        return {"pause_requested": self.paused, "browser_allowed": self.allowed}

    async def claim(self):
        self.claims += 1
        return await super().claim()


class ClosableBrowser(FakeAgentBrowser):
    def __init__(self):
        super().__init__()
        self.closes = 0
        self.readiness_checks = 0

    async def close(self):
        self.closes += 1

    async def session_ready(self):
        self.readiness_checks += 1
        return self.ready


def test_pause_closes_browser_without_login_or_claim_and_resume_continues():
    api, browser = ControlledApi(), ClosableBrowser()
    api.paused = True
    runner = FTWLocalAgentRunner(api, browser)
    assert run(runner.run_once()) is False
    assert browser.closes == 1
    assert browser.readiness_checks == api.claims == 0
    assert api.heartbeats[-1]["paused"] is True
    api.paused = False
    run(runner.run_once())
    assert browser.readiness_checks == api.claims == 1


def test_second_agent_waits_without_opening_browser_or_logging_in():
    api, browser = ControlledApi(), ClosableBrowser()
    api.allowed = False
    run(FTWLocalAgentRunner(api, browser).run_once())
    assert browser.readiness_checks == api.claims == 0
    assert browser.closes == 1


def test_pause_during_job_completes_once_then_closes_before_next_claim():
    api = ControlledApi(claim={"job": {"id": "job-1"}, "claim_token": "claim-1"})

    class PausingBrowser(ClosableBrowser):
        async def execute(self, job):
            api.paused = True
            return await super().execute(job)

    browser = PausingBrowser()
    runner = FTWLocalAgentRunner(api, browser)
    assert run(runner.run_once()) is True
    assert len(api.completions) == len(browser.executions) == 1
    assert browser.closes == 1
    run(runner.run_once())
    assert api.claims == 1


def test_completion_network_failure_does_not_repeat_browser_action():
    class RetryApi(ControlledApi):
        async def complete(self, *args):
            if not hasattr(self, "failed"):
                self.failed = True
                raise OSError("connection lost after vendor submission")
            return await super().complete(*args)

    api = RetryApi(claim={"job": {"id": "job-1"}, "claim_token": "claim-1"})
    browser = ClosableBrowser()
    runner = FTWLocalAgentRunner(api, browser)
    try:
        run(runner.run_once())
    except OSError:
        pass
    run(runner.run_once())
    assert len(browser.executions) == len(api.completions) == api.claims == 1


def test_readiness_does_not_navigate_existing_agent_or_personal_tab():
    class ReadyBrowser(PersistentFTWBrowser):
        async def start(self):
            pass

        async def _page_text(self):
            return "HighlandTech", False

    browser = ReadyBrowser("unused-profile", expected_account="HighlandTech")
    browser._page = StubBrowserPage()
    browser._page.url = "https://www.ftwilliam.com/cgi-bin/index.cgi#go=search"
    personal = StubBrowserPage()
    personal.url = browser._page.url
    for _ in range(5):
        assert run(browser.session_ready()) is True
    assert browser._page.goto_calls == personal.goto_calls == []


def fixture():
    repo = MemoryRepository()
    device = run(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Test device", expected_account="HighlandTech", token_hash="hash", token_prefix="test",
        agent_version="0.4.0", status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True, last_seen_at=datetime.utcnow(),
    )))
    return repo, FTWLocalAgentService(repo=repo, settings=Settings(ftw_local_agent_enabled=True)), device


def test_pause_request_survives_stale_heartbeat_and_blocks_stale_claim():
    repo, service, stale = fixture()
    paused = run(service.set_paused(stale, True))
    assert paused.pause_requested
    updated = run(service.heartbeat(stale, FTWLocalAgentHeartbeatRequest(
        agent_version="0.4.0", browser_ready=True,
    )))
    assert updated.pause_requested
    assert updated.status == FTWLocalAgentDeviceStatus.PAUSING
    assert run(service.claim(stale)).job is None


def test_account_browser_lease_serializes_devices_and_releases_after_pause_ack():
    repo, service, first = fixture()
    second = run(repo.create_ftw_local_agent_device(first.model_copy(update={"id": None, "token_hash": "second"})))
    assert run(service.control(first))["browser_allowed"]
    assert not run(service.control(second))["browser_allowed"]
    run(service.set_paused(first, True))
    run(service.heartbeat(first, FTWLocalAgentHeartbeatRequest(
        agent_version="0.4.0", browser_ready=False, paused=True,
    )))
    assert run(service.control(second))["browser_allowed"]


def test_pause_is_persistent_and_resume_is_idempotent():
    repo, service, device = fixture()
    run(service.set_paused(device, True))
    restarted = FTWLocalAgentService(repo=repo, settings=service.settings)
    assert run(restarted.control(device))["pause_requested"]
    run(restarted.set_paused(device, False))
    run(restarted.set_paused(device, False))
    assert not run(restarted.control(device))["pause_requested"]


def queued_fixture():
    from test_ftwilliams_local_agent import _submitted_job_fixture, FailingPostBringForwardReviewService
    repo, service, filing, job = _submitted_job_fixture(FailingPostBringForwardReviewService())
    job = run(repo.update_ftw_local_agent_job(str(job.id), {"status": FTWLocalAgentJobStatus.QUEUED, "result_state": None}))
    device = run(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Batch device", expected_account="HighlandTech", token_hash="batch-hash", token_prefix="batch",
        agent_version="0.4.0", status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True, last_seen_at=datetime.utcnow(),
    )))
    return repo, service, filing, job, device


def test_ten_same_target_pending_jobs_survive_pause_but_cannot_repeat_a_completed_copy():
    repo, service, filing, job, device = queued_fixture()
    for i in range(1, 10):
        run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={
            "id": None, "idempotency_key": f"batch-{i}", "status": FTWLocalAgentJobStatus.QUEUED,
        })))
    verified = run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={
        "id": None, "idempotency_key": "already-done", "status": FTWLocalAgentJobStatus.VERIFIED,
    })))
    run(service.set_paused(device, True))
    for item in repo.ftw_local_agent_jobs.values():
        item.expires_at = datetime.utcnow() - timedelta(days=2)
    assert run(service.claim(device)).job is None
    assert len(repo.ftw_local_agent_jobs) == 11
    run(service.set_paused(device, False))
    count = 0
    for _ in range(11):
        claim = run(service.claim(device))
        if claim.job:
            count += 1
            assert claim.job.id != verified.id
            run(service.complete(device, claim.job.id, FTWLocalAgentCompleteRequest(
                claim_token=claim.claim_token, state="SUBMITTED", message="Synthetic submission",
            )))
    assert count == 0  # The same target already has a VERIFIED native operation.
    assert sum(j.result_state == "PRIOR_OPERATION_UNCONFIRMED" for j in repo.ftw_local_agent_jobs.values()) == 10
    assert run(repo.get_ftw_local_agent_job(verified.id)).status == FTWLocalAgentJobStatus.VERIFIED


def test_pause_during_claim_allows_completion_but_not_next_job():
    repo, service, filing, job, device = queued_fixture()
    claim = run(service.claim(device))
    run(service.set_paused(device, True))
    completed = run(service.complete(device, claim.job.id, FTWLocalAgentCompleteRequest(
        claim_token=claim.claim_token, state="SUBMITTED", message="Synthetic submission",
    )))
    assert completed.status == FTWLocalAgentJobStatus.SUBMITTED
    assert run(service.claim(device)).job is None


def test_process_crash_expired_claim_is_held_not_repeated():
    repo, service, filing, job, device = queued_fixture()
    claim = run(service.claim(device))
    run(repo.update_ftw_local_agent_job(job.id, {"claim_expires_at": datetime.utcnow() - timedelta(seconds=1)}))
    assert run(service.claim(device)).job is None
    held = run(repo.get_ftw_local_agent_job(job.id))
    assert held.status == FTWLocalAgentJobStatus.ACTION_NEEDED
    assert held.result_state == "UNKNOWN_OUTCOME"
    assert run(service.claim(device)).job is None
    review = run(repo.get_ftwilliams_review(filing.id))
    with pytest.raises(ValueError, match="Refresh FTW"):
        run(service.enqueue_bring_forward(filing, review, run_id="retry", before_record_ids=[]))


def test_lost_completion_response_is_idempotent_and_readback_runs_once():
    repo, service, filing, job, device = queued_fixture()
    claim = run(service.claim(device))
    payload = FTWLocalAgentCompleteRequest(claim_token=claim.claim_token, state="SUBMITTED", message="Synthetic")
    first = run(service.complete(device, job.id, payload))
    run(service.continue_after_completion(first))
    retried = run(service.complete(device, job.id, payload))
    run(service.continue_after_completion(retried))
    events = [item for item in repo.audit if item.event == "FTW_LOCAL_AGENT_BRING_FORWARD_SUBMITTED"]
    assert len(events) == 1
    assert len(repo.ftw_local_agent_jobs) == 1


@pytest.mark.parametrize("profile", [
    "C:/Users/Test/AppData/Local/Google/Chrome/User Data/Default",
    "C:/Users/Test/AppData/Local/Microsoft/Edge/User Data",
])
def test_personal_browser_profile_is_rejected(profile):
    with pytest.raises(ValueError, match="dedicated"):
        PersistentFTWBrowser(profile, expected_account="HighlandTech")


def test_old_agent_cannot_claim_to_support_pause_browser_closure():
    repo, service, device = fixture()
    run(repo.update_ftw_local_agent_device(device.id, {"agent_version": "0.3.2"}))
    with pytest.raises(ValueError, match="Update"):
        run(service.set_paused(device, True))


def test_pause_offline_still_records_request_without_claiming_closed_browser():
    repo, service, device = fixture()
    run(repo.update_ftw_local_agent_device(device.id, {
        "status": FTWLocalAgentDeviceStatus.OFFLINE, "last_seen_at": datetime.utcnow() - timedelta(days=1),
    }))
    run(service.set_paused(device, True))
    assert run(service.status()).pause_requested
    assert run(service.control(device))["pause_requested"]


def test_two_devices_cannot_claim_same_account_jobs_at_once():
    repo, service, filing, job, device = queued_fixture()
    second = run(repo.create_ftw_local_agent_device(device.model_copy(update={"id": None, "token_hash": "other-batch"})))
    run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={"id": None, "idempotency_key": "second-job", "status": FTWLocalAgentJobStatus.QUEUED})))
    first = run(service.claim(device))
    assert first.job
    assert run(service.claim(second)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).status == FTWLocalAgentJobStatus.CLAIMED


def test_legacy_agents_can_work_but_are_also_serialized_by_account_lease():
    repo, service, filing, job, first = queued_fixture()
    run(repo.update_ftw_local_agent_device(first.id, {"agent_version": "0.3.2"}))
    second = run(repo.create_ftw_local_agent_device(first.model_copy(update={
        "id": None, "token_hash": "legacy-second", "agent_version": "0.3.2",
    })))
    run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={"id": None, "idempotency_key": "legacy-second-job", "status": FTWLocalAgentJobStatus.QUEUED})))
    assert run(service.claim(first)).job
    assert run(service.claim(second)).job is None
    assert repo.ftw_browser_leases


def test_updated_agent_waits_for_legacy_agent_to_stop_before_browser_access():
    repo, service, filing, job, updated = queued_fixture()
    legacy = run(repo.create_ftw_local_agent_device(updated.model_copy(update={
        "id": None, "token_hash": "legacy-browser", "agent_version": "0.3.2",
    })))
    control = run(service.control(updated))
    assert not control["browser_allowed"]
    assert "0.4.0" in control["reason"]
    assert run(service.claim(updated)).job is None
    run(repo.update_ftw_local_agent_device(legacy.id, {"last_seen_at": datetime.utcnow() - timedelta(days=1)}))
    assert run(service.control(updated))["browser_allowed"]


def test_revoked_device_cannot_resume_or_acquire_browser():
    repo, service, device = fixture()
    run(service.revoke(device.id))
    with pytest.raises(ValueError, match="revoked"):
        run(service.control(device))


def test_login_waiting_job_is_preserved_through_long_pause():
    repo, service, filing, job, device = queued_fixture()
    run(repo.update_ftw_local_agent_job(job.id, {
        "status": FTWLocalAgentJobStatus.ACTION_NEEDED, "result_state": "LOGIN_REQUIRED",
        "expires_at": datetime.utcnow() - timedelta(days=1),
    }))
    run(service.set_paused(device, True))
    run(service.set_paused(device, False))
    assert run(service.claim(device)).job.id == job.id


def test_idle_device_yields_account_access_to_device_with_assigned_work():
    repo, service, filing, job, device = queued_fixture()
    second = run(repo.create_ftw_local_agent_device(device.model_copy(update={"id": None, "token_hash": "assigned-second"})))
    assert run(service.control(device))["browser_allowed"]
    run(repo.update_ftw_local_agent_job(job.id, {"assigned_device_id": second.id}))
    assert not run(service.control(device))["browser_allowed"]
    run(service.heartbeat(device, FTWLocalAgentHeartbeatRequest(agent_version="0.4.0", browser_ready=False, waiting=True)))
    assert run(service.control(second))["browser_allowed"]


def test_control_recovers_crashed_other_device_without_repeating_job():
    repo, service, filing, job, device = queued_fixture()
    run(service.claim(device))
    run(repo.update_ftw_local_agent_job(job.id, {"claim_expires_at": datetime.utcnow() - timedelta(seconds=1)}))
    second = run(repo.create_ftw_local_agent_device(device.model_copy(update={"id": None, "token_hash": "recovery-second"})))
    run(service.control(second))
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "UNKNOWN_OUTCOME"
    assert run(service.claim(second)).job is None


def test_disabled_rollout_does_not_open_browser_or_claim_pending_work():
    repo, service, filing, job, device = queued_fixture()
    service.settings.ftw_local_agent_enabled = False
    assert not run(service.control(device))["browser_allowed"]
    assert run(service.claim(device)).job is None


def test_unexpected_browser_failure_reports_unknown_outcome_not_blind_retry():
    class InterruptedBrowser(ClosableBrowser):
        async def execute(self, job):
            raise OSError("operation result was lost")

    api = ControlledApi(claim={"job": {"id": "interrupted"}, "claim_token": "claim"})
    run(FTWLocalAgentRunner(api, InterruptedBrowser()).run_once())
    assert api.completions[0][2].state == "UNKNOWN_OUTCOME"
