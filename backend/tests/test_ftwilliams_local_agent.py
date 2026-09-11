import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.ftwilliams_local_agent import (
    LocalFTWTarget,
    load_local_ftw_targets,
    verify_local_ftw_identity,
)
from app.config import Settings
from app.models import (
    Filing,
    FTWClientWorkspace,
    FTWAutomationStatus,
    FTWilliamsPlanLookup,
    FTWilliamsPlanLookupStatus,
    FTWilliamsReview,
    FTWLocalAgentCompleteRequest,
    FTWLocalAgentDevice,
    FTWLocalAgentDeviceStatus,
    FTWLocalAgentHeartbeatRequest,
    FTWLocalAgentJob,
    FTWLocalAgentJobStatus,
    FTWLocalAgentPairingCode,
    FTWLocalAgentPairRequest,
    FTWWorkspacePlanMapping,
)
from app.repositories import MemoryRepository, to_mongo_bson
from app.services.ftwilliams_local_agent_jobs import FTWLocalAgentService


def run_async(value):
    return asyncio.run(value)


class FailingPostBringForwardReviewService:
    async def prepare_review(self, *_args, **_kwargs):
        raise RuntimeError("ftwLink timeout")


class StaticPostBringForwardReviewService:
    def __init__(self, review: FTWilliamsReview):
        self.review = review

    async def prepare_review(self, *_args, **_kwargs):
        return self.review


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


def test_pairing_code_is_single_use_and_device_token_is_never_stored_plaintext():
    repo = MemoryRepository()
    settings = Settings(ftw_local_agent_enabled=True)
    service = FTWLocalAgentService(repo=repo, settings=settings)
    pairing = run_async(service.create_pairing_code(created_by="admin@example.com"))
    payload = FTWLocalAgentPairRequest(
        pairing_code=pairing.pairing_code,
        device_name="Benefits workstation",
        agent_version="0.1.0",
    )

    paired = run_async(service.pair(payload))

    assert paired.device_token
    assert paired.expected_account == "HighlandTech"
    assert all(device.token_hash != paired.device_token for device in repo.ftw_local_agent_devices.values())
    assert run_async(service.authenticate(paired.device_token)).id == paired.device_id
    with pytest.raises(ValueError, match="already used"):
        run_async(service.pair(payload))


def test_mongo_local_agent_expiry_fields_are_stored_as_native_datetimes():
    now = datetime.utcnow()

    payload = to_mongo_bson(
        FTWLocalAgentPairingCode(
            code_hash="hash",
            code_prefix="code",
            expected_account="HighlandTech",
            expires_at=now + timedelta(minutes=10),
        )
    )

    assert isinstance(payload["expires_at"], datetime)
    assert isinstance(payload["created_at"], datetime)


def test_heartbeat_and_job_claim_are_pull_only_and_claim_token_is_one_time():
    repo = MemoryRepository()
    settings = Settings(
        ftw_local_agent_enabled=True,
        ftw_local_agent_expected_account="HighlandTech",
    )
    service = FTWLocalAgentService(repo=repo, settings=settings)
    pairing = run_async(service.create_pairing_code())
    paired = run_async(
        service.pair(
            FTWLocalAgentPairRequest(
                pairing_code=pairing.pairing_code,
                device_name="Benefits workstation",
                agent_version="0.1.0",
            )
        )
    )
    device = run_async(service.authenticate(paired.device_token))
    run_async(
        service.heartbeat(
            device,
            FTWLocalAgentHeartbeatRequest(agent_version="0.1.0", browser_ready=True),
        )
    )
    filing = run_async(
        repo.create_filing(
            Filing(
                file_name="schedule-a.pdf",
                content_type="application/pdf",
                file_size=100,
                s3_key="schedule-a.pdf",
            )
        )
    )
    review = FTWilliamsReview(
        filing_id=str(filing.id),
        current_year_exists=False,
        bring_forward_required=True,
        year="2025",
        ftw_browser_customer_id="customer-1",
        ftw_browser_plan_id="plan-1",
        browser_mapping_confirmed=True,
        ftw_plan_url=(
            "https://www.ftwilliam.com/cgi-bin/index.cgi#go=iframe&"
            "plan=customer-1,plan-1&Year=2025"
        ),
        plan_lookup=FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            company_employer_id="12-3456789",
            plan_number="501",
            plan_name="Demo Health and Welfare Plan",
            year="2025",
        ),
    )
    run_async(repo.upsert_ftwilliams_review(review))
    job = run_async(
        service.enqueue_bring_forward(
            filing,
            review,
            run_id="run-1",
            before_record_ids=["1"],
        )
    )

    status = run_async(service.status())
    claim = run_async(service.claim(device))

    assert status.connected is True
    assert claim.job is not None
    assert claim.job.id == job.id
    assert claim.claim_token
    assert "claim_token_hash" not in claim.model_dump(mode="json")["job"]
    assert run_async(service.claim(device)).job is None
    with pytest.raises(ValueError, match="invalid"):
        run_async(
            service.complete(
                device,
                str(job.id),
                FTWLocalAgentCompleteRequest(
                    claim_token="wrong-token",
                    state="SUBMITTED",
                    message="Submitted",
                ),
            )
        )
    completed = run_async(
        service.complete(
            device,
            str(job.id),
            FTWLocalAgentCompleteRequest(
                claim_token=str(claim.claim_token),
                state="SUBMITTED",
                message="Submitted",
            ),
        )
    )
    assert completed.status == FTWLocalAgentJobStatus.SUBMITTED


def _submitted_job_fixture(review_service):
    repo = MemoryRepository()
    settings = Settings(
        ftw_automation_enabled=True,
        ftw_automation_bring_forward_enabled=True,
        ftw_local_agent_enabled=True,
        ftw_local_agent_expected_account="HighlandTech",
        ftwlink_sandbox_ftw_customer_id="customer-1",
        ftwlink_sandbox_ftw_plan_id="plan-1",
        ftwlink_sandbox_year_end="2025",
    )
    service = FTWLocalAgentService(
        repo=repo,
        settings=settings,
        review_service=review_service,
    )
    filing = run_async(
        repo.create_filing(
            Filing(
                file_name="schedule-a.pdf",
                content_type="application/pdf",
                file_size=100,
                s3_key="schedule-a.pdf",
            )
        )
    )
    review = FTWilliamsReview(
        filing_id=str(filing.id),
        configured=True,
        current_query_sent=True,
        current_year_exists=False,
        bring_forward_required=True,
        year="2025",
        ftw_customer_id="customer-1",
        ftw_plan_id="plan-1",
        ftw_browser_customer_id="customer-1",
        ftw_browser_plan_id="plan-1",
        browser_mapping_confirmed=True,
        ftw_plan_url=(
            "https://www.ftwilliam.com/cgi-bin/index.cgi#go=iframe&"
            "plan=customer-1,plan-1&Year=2025"
        ),
        schedule_a_records=[{"ftw_seq_no": "1", "query_results": {}}],
        plan_lookup=FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            company_employer_id="12-3456789",
            plan_number="501",
            plan_name="Demo Health and Welfare Plan",
            year="2025",
        ),
    )
    run_async(repo.upsert_ftwilliams_review(review))
    job = run_async(
        service.enqueue_bring_forward(
            filing,
            review,
            run_id="run-1",
            before_record_ids=["1"],
        )
    )
    job = run_async(
        repo.update_ftw_local_agent_job(
            str(job.id),
            {
                "status": FTWLocalAgentJobStatus.SUBMITTED,
                "result_state": "SUBMITTED",
                "result_message": "Submitted",
            },
        )
    )
    return repo, service, filing, job


def test_post_bring_forward_query_failure_becomes_action_needed_instead_of_server_error():
    repo, service, filing, job = _submitted_job_fixture(FailingPostBringForwardReviewService())

    result = run_async(service.continue_after_completion(job))

    updated_filing = run_async(repo.get_filing(str(filing.id)))
    assert result.status == FTWLocalAgentJobStatus.ACTION_NEEDED
    assert updated_filing.automation_status == FTWAutomationStatus.ACTION_NEEDED
    assert updated_filing.automation_next_action == "RETRY_AFTER_CURRENT_QUERY"
    assert "ftwLink verification query failed" in updated_filing.automation_reasons[0]


def test_post_bring_forward_requires_a_new_record_id_before_continuing():
    refreshed = FTWilliamsReview(
        filing_id="placeholder",
        configured=True,
        current_query_sent=True,
        current_query_success=True,
        current_query_complete=True,
        current_year_exists=True,
        bring_forward_required=False,
        year="2025",
        schedule_a_records=[{"ftw_seq_no": "1", "query_results": {}}],
    )
    review_service = StaticPostBringForwardReviewService(refreshed)
    repo, service, filing, job = _submitted_job_fixture(review_service)
    refreshed.filing_id = str(filing.id)

    result = run_async(service.continue_after_completion(job))

    updated_filing = run_async(repo.get_filing(str(filing.id)))
    assert result.status == FTWLocalAgentJobStatus.ACTION_NEEDED
    assert updated_filing.automation_status == FTWAutomationStatus.ACTION_NEEDED
    assert updated_filing.automation_bring_forward_new_record_ids == []


def test_expired_job_claim_cannot_be_completed():
    repo = MemoryRepository()
    job = _submitted_job_fixture(FailingPostBringForwardReviewService())[3]
    # Use a repository-local claimed job to prove expiry is enforced by the
    # persistence boundary, not only by the caller.
    job.status = FTWLocalAgentJobStatus.CLAIMED
    job.device_id = "device-1"
    job.claim_token_hash = "claim-hash"
    job.claim_expires_at = datetime.utcnow() - timedelta(seconds=1)
    repo.ftw_local_agent_jobs[str(job.id)] = job

    completed = run_async(
        repo.complete_ftw_local_agent_job(
            str(job.id),
            "device-1",
            "claim-hash",
            datetime.utcnow(),
            {"status": FTWLocalAgentJobStatus.SUBMITTED},
        )
    )

    assert completed is None


def test_login_required_job_is_automatically_reclaimed_after_login():
    repo, service, filing, job = _submitted_job_fixture(FailingPostBringForwardReviewService())
    now = datetime.utcnow()
    job.status = FTWLocalAgentJobStatus.ACTION_NEEDED
    job.result_state = "LOGIN_REQUIRED"
    job.result_message = "Sign in to FT Williams."
    job.completed_at = now - timedelta(seconds=10)
    job.expires_at = now + timedelta(minutes=5)
    repo.ftw_local_agent_jobs[str(job.id)] = job
    device = run_async(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Highland workstation",
        token_hash="device-token",
        token_prefix="device",
        expected_account="HighlandTech",
        status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True,
        last_seen_at=now,
    )))

    claim = run_async(service.claim(device))

    assert claim.job is not None
    assert claim.job.id == job.id
    reclaimed = run_async(repo.get_ftw_local_agent_job(str(job.id)))
    assert reclaimed.status == FTWLocalAgentJobStatus.CLAIMED
    assert reclaimed.result_state is None
    assert reclaimed.result_message is None
    assert reclaimed.completed_at is None


def test_device_cannot_claim_a_job_for_another_ftw_account():
    repo = MemoryRepository()
    service = FTWLocalAgentService(
        repo=repo,
        settings=Settings(
            ftw_local_agent_enabled=True,
            ftw_local_agent_expected_account="HighlandTech",
        ),
    )
    pairing = run_async(service.create_pairing_code())
    paired = run_async(
        service.pair(
            FTWLocalAgentPairRequest(
                pairing_code=pairing.pairing_code,
                device_name="Highland workstation",
                agent_version="0.1.0",
            )
        )
    )
    device = run_async(service.authenticate(paired.device_token))
    run_async(
        repo.create_or_get_ftw_local_agent_job(
            FTWLocalAgentJob(
                filing_id="other-filing",
                run_id="other-run",
                idempotency_key="other-account-job",
                target_url="https://www.ftwilliam.com/cgi-bin/index.cgi?Year=2025",
                expected_account="Another Account",
                expected_plan_name="Another Plan",
                expected_ein="12-3456789",
                expected_plan_number="501",
                expected_year="2025",
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
        )
    )

    claim = run_async(service.claim(device))

    assert claim.job is None


def test_enqueue_renews_an_expired_job_instead_of_leaving_it_stuck():
    repo, service, filing, job = _submitted_job_fixture(FailingPostBringForwardReviewService())
    run_async(
        repo.update_ftw_local_agent_job(
            str(job.id),
            {
                "status": FTWLocalAgentJobStatus.QUEUED,
                "expires_at": datetime.utcnow() - timedelta(seconds=1),
            },
        )
    )
    review = run_async(repo.get_ftwilliams_review(str(filing.id)))

    renewed = run_async(
        service.enqueue_bring_forward(
            filing,
            review,
            run_id="run-2",
            before_record_ids=["1"],
        )
    )

    assert renewed.id == job.id
    assert renewed.status == FTWLocalAgentJobStatus.QUEUED
    assert renewed.run_id == "run-2"
    assert renewed.expires_at > datetime.utcnow()


def test_claim_cancels_a_queued_job_after_manual_bring_forward_completed():
    repo, service, filing, job = _submitted_job_fixture(FailingPostBringForwardReviewService())
    run_async(
        repo.update_ftw_local_agent_job(
            str(job.id),
            {"status": FTWLocalAgentJobStatus.QUEUED},
        )
    )
    review = run_async(repo.get_ftwilliams_review(str(filing.id)))
    review.current_year_exists = True
    review.bring_forward_required = False
    run_async(repo.upsert_ftwilliams_review(review))
    pairing = run_async(service.create_pairing_code())
    paired = run_async(
        service.pair(
            FTWLocalAgentPairRequest(
                pairing_code=pairing.pairing_code,
                device_name="Benefits workstation",
                agent_version="0.1.0",
            )
        )
    )
    device = run_async(service.authenticate(paired.device_token))

    claim = run_async(service.claim(device))

    cancelled = run_async(repo.get_ftw_local_agent_job(str(job.id)))
    assert claim.job is None
    assert cancelled.status == FTWLocalAgentJobStatus.EXPIRED
    assert cancelled.result_state == "NO_LONGER_REQUIRED"


def _workspace_routing_fixture():
    repo = MemoryRepository()
    settings = Settings(
        ftw_local_agent_enabled=True,
        ftw_local_agent_workspace_routing_enabled=True,
        ftw_local_agent_heartbeat_ttl_seconds=90,
    )
    service = FTWLocalAgentService(repo=repo, settings=settings)
    workspace = run_async(repo.create_ftw_client_workspace(FTWClientWorkspace(
        name="Client A",
        slug="client-a",
        expected_account="Shared Account",
        admin_subjects=["admin@example.com"],
    )))
    filing = run_async(repo.create_filing(Filing(
        file_name="schedule-a.pdf",
        content_type="application/pdf",
        file_size=100,
        s3_key="schedule-a.pdf",
        workspace_id=str(workspace.id),
    )))
    review = FTWilliamsReview(
        filing_id=str(filing.id),
        current_year_exists=False,
        bring_forward_required=True,
        year="2025",
        ftw_customer_id="link-customer-a",
        ftw_plan_id="link-plan-a",
        ftw_browser_customer_id="browser-customer-a",
        ftw_browser_plan_id="browser-plan-a",
        browser_mapping_confirmed=True,
        plan_lookup=FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            company_employer_id="12-3456789",
            plan_number="501",
            plan_name="Client A Health Plan",
            year="2025",
        ),
    )
    run_async(repo.upsert_ftwilliams_review(review))
    return repo, service, workspace, filing, review


def test_workspace_routing_requires_a_verified_plan_mapping():
    _repo, service, _workspace, filing, review = _workspace_routing_fixture()

    with pytest.raises(ValueError, match="verified FT Williams plan mapping"):
        run_async(service.enqueue_bring_forward(filing, review, run_id="run-1", before_record_ids=[]))


def test_workspace_job_is_assigned_to_one_ready_device_and_cannot_cross_workspace():
    repo, service, workspace, filing, review = _workspace_routing_fixture()
    mapping = run_async(repo.upsert_ftw_workspace_plan_mapping(FTWWorkspacePlanMapping(
        workspace_id=str(workspace.id),
        expected_account="Shared Account",
        company_employer_id="12-3456789",
        plan_number="501",
        year="2025",
        plan_name="Client A Health Plan",
        ftw_customer_id="link-customer-a",
        ftw_plan_id="link-plan-a",
        ftw_browser_customer_id="browser-customer-a",
        ftw_browser_plan_id="browser-plan-a",
        verification_evidence="Verified against demo account on 2026-09-11",
        verified_by="admin@example.com",
    )))
    now = datetime.utcnow()
    assigned = run_async(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Client A computer",
        token_hash="assigned-token",
        token_prefix="assign",
        expected_account="Shared Account",
        workspace_id=str(workspace.id),
        status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True,
        last_seen_at=now,
    )))
    other_workspace = run_async(repo.create_ftw_client_workspace(FTWClientWorkspace(
        name="Client B",
        slug="client-b",
        expected_account="Shared Account",
        admin_subjects=["admin-b@example.com"],
    )))
    other = run_async(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Client B computer",
        token_hash="other-token",
        token_prefix="other-",
        expected_account="Shared Account",
        workspace_id=str(other_workspace.id),
        status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True,
        last_seen_at=now,
    )))

    job = run_async(service.enqueue_bring_forward(filing, review, run_id="run-1", before_record_ids=[]))

    assert job.workspace_id == workspace.id
    assert job.mapping_id == mapping.id
    assert job.assigned_device_id == assigned.id
    assert run_async(service.claim(other)).job is None
    claim = run_async(service.claim(assigned))
    assert claim.job is not None
    assert claim.job.workspace_id == workspace.id
    assert claim.job.mapping_id == mapping.id


def test_workspace_job_cannot_be_claimed_by_an_unassigned_device_in_same_workspace():
    repo, service, workspace, filing, review = _workspace_routing_fixture()
    run_async(repo.upsert_ftw_workspace_plan_mapping(FTWWorkspacePlanMapping(
        workspace_id=str(workspace.id),
        expected_account="Shared Account",
        company_employer_id="12-3456789",
        plan_number="501",
        year="2025",
        plan_name="Client A Health Plan",
        ftw_customer_id="link-customer-a",
        ftw_plan_id="link-plan-a",
        ftw_browser_customer_id="browser-customer-a",
        ftw_browser_plan_id="browser-plan-a",
        verification_evidence="Verified",
        verified_by="admin@example.com",
    )))
    now = datetime.utcnow()
    first = run_async(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Most recent computer",
        token_hash="first-token",
        token_prefix="first-",
        expected_account="Shared Account",
        workspace_id=str(workspace.id),
        status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True,
        last_seen_at=now,
    )))
    second = run_async(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
        name="Older computer",
        token_hash="second-token",
        token_prefix="second",
        expected_account="Shared Account",
        workspace_id=str(workspace.id),
        status=FTWLocalAgentDeviceStatus.CONNECTED,
        browser_ready=True,
        last_seen_at=now - timedelta(seconds=1),
    )))
    job = run_async(service.enqueue_bring_forward(filing, review, run_id="run-1", before_record_ids=[]))

    assert job.assigned_device_id == first.id
    assert run_async(service.claim(second)).job is None
    assert run_async(service.claim(first)).job is not None
