"""Source-shaped regressions from the approved Oxford TEST canary."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import (
    FTWilliamsQueryResponse, FTWilliamsStatusItem, FTWLocalAgentJobStatus, FTWAutomationStatus,
    NormalizedExtractionField, NormalizedExtractionResult,
)
from app.services.extractor import (
    bcbsma_column_value, extract_columnar_broker_compensation_rows,
    extract_eyemed_schedule_a_fields,
)
from app.services.schedule_a_customer_rules import apply_customer_defaults
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from test_ftwilliams_agent_pause_resume import queued_fixture, run


UNUM = """5. INSURANCE FEES AND COMMISSION INFORMATION:
NAME AND ADDRESS SALES COMMISSIONS FEES ADDITIONAL COMPENSATION

RSC INSURANCE BROKERAGE INC 5,349.89 .00 617.87
4TH FLOOR
160 FEDERAL STREET
BOSTON MA 02110

4. COVERAGE/BENEFITS
"""

EYEMED = """Vision Insurance Information For Form 5500
Information Compiled By: EyeMed Vision Care on behalf of the Fidelity Security Life Insurance Company
Report Start DateReport End Date
1/1/2025 12/31/2025
Name of Plan Contract orID # Enrollment Group
Approximate number ofsubscribers covered atend of policy or contractyear:
Approximate number ofsubscribers anddependents covered at endof policy or contract year:EIN NAIC Amount
OXFORD BIOMEDICA (US) LLC10420751001OXFORD BIOMEDICA (US)LLC 134 335 43094984471870 $11,646.30
OXFORD BIOMEDICA (US) LLC COBRA10420761002OXFORD BIOMEDICA US,INC. COBRA 1 4 43094984471870 $224.48
Total: $11,870.78
Commissions or fees paid by carrier to agents, brokers or other persons:
Payments Received by carrier from plan or plan sponsor:
"""


def test_bcbsma_preserves_decimal_precision_in_named_medical_column():
    assert bcbsma_column_value("Total Premium 1,630,230.90 0.00 0.00", "Total Premium", "MEDICAL") == "1,630,230.90"
    assert bcbsma_column_value("Claims Charged 1422413.5874 0 0", "Claims Charged", "MEDICAL") == "1422413.5874"


def test_unum_leading_decimal_zero_does_not_drop_the_broker_block():
    rows = extract_columnar_broker_compensation_rows([(1, UNUM)])
    assert len(rows) == 1
    assert rows[0].commission_total == "5,349.89"
    assert rows[0].fee_total == "617.87"  # Preserve established additional-compensation classification.
    assert rows[0].fee_rows[0].purpose == "Additional Compensation"


def test_unum_provider_fee_one_is_reconciled_without_losing_additional_compensation():
    result = NormalizedExtractionResult(provider="EyeLevel", fields=[
        NormalizedExtractionField(field_name="3c. Amount of Fees", value="1", confidence=.98)])
    fixed = enrich_schedule_a_result(result, SemanticDocument.from_page_texts([(1, UNUM)]), rules=[])
    fee = next(f for f in fixed.fields if f.field_name.startswith("3c."))
    assert fee.value == "617.87"
    assert fee.decision == "REVIEW_REQUIRED"
    assert "Additional Compensation" in fee.source_text
    assert result.fields[0].value == "1", "Do not mutate provider input"


def test_eyemed_highest_count_is_not_a_sum_and_multiple_contracts_still_require_review():
    fields = extract_eyemed_schedule_a_fields([(1, EYEMED)])
    fixed = enrich_schedule_a_result(NormalizedExtractionResult(provider="EyeLevel", fields=fields),
        SemanticDocument.from_page_texts([(1, EYEMED)]), rules=[])
    lives = next(f for f in fixed.fields if f.field_name.startswith("1e."))
    assert lives.value == "335"
    assert lives.decision == "REVIEW_REQUIRED"
    assert "Highest" in lives.source_text
    assert any(a["type"] == "eyemed_contract_grouping_required"
               for a in fixed.raw["semantic_resolution"]["ambiguities"])


def test_eyemed_separated_ein_and_naic_columns_are_not_mistaken_for_lives():
    text = EYEMED.replace("43094984471870", "430949844 71870")
    fixed = enrich_schedule_a_result(NormalizedExtractionResult(provider="EyeLevel", fields=[
        NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)", value="339", confidence=.98)]),
        SemanticDocument.from_page_texts([(1, text)]), rules=[])
    assert next(f.value for f in fixed.fields if f.field_name.startswith("1e.")) == "335"


def test_absent_organizational_code_defaults_without_inventing_broker_identity():
    result = NormalizedExtractionResult(provider="EyeLevel", fields=[])
    apply_customer_defaults(result)
    assert result.fields[0].value == "3"
    assert result.schedule_a_broker_rows == []
    assert result.fields[0].evidence[0].provider == "Customer configured default"


def test_absent_scalar_code_does_not_override_an_explicit_recipient_code():
    from app.models import ScheduleABrokerRow
    result = NormalizedExtractionResult(provider="EyeLevel", fields=[],
        schedule_a_broker_rows=[ScheduleABrokerRow(name="Recipient", organization_code="6")])
    apply_customer_defaults(result)
    assert result.fields[0].value == "6"
    assert result.schedule_a_broker_rows[0].organization_code == "6"


def response(code, values=None):
    return FTWilliamsQueryResponse(operation="query_schedule_a", configured=True, sent=True,
        request_xml="", http_status=200, success=code == "0", statuses=[
            FTWilliamsStatusItem(error_code=code, query_results=values or {})])


def preflight(monkeypatch, answer):
    calls = []
    async def query(_self, request):
        calls.append(request)
        return answer
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService.run_query", query)
    return calls


@pytest.mark.parametrize("fails", [False, True])
def test_preflight_closes_its_owned_query_client_on_success_or_failure(monkeypatch, fails):
    repo, service, filing, job, device = queued_fixture()
    closed = []
    async def query(_self, _request):
        if fails:
            raise TimeoutError("query timed out")
        return response("59")
    async def close(_self):
        closed.append(_self)
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService.run_query", query)
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService._close_client", close)
    payload = run(service.claim(device))
    assert len(closed) == 1
    assert (payload.job is None) is fails


@pytest.mark.parametrize("identity_change,expected", [
    ({}, "NO_LONGER_REQUIRED"),
    ({"ftw_browser_plan_id": "wrong"}, "CURRENT_QUERY_REQUIRED"),
    # Saved mapping confirmation is no longer part of the runtime identity;
    # the exact API/browser IDs remain authoritative.
    ({"browser_mapping_confirmed": False}, "NO_LONGER_REQUIRED"),
    ({"year": "2024"}, "CURRENT_QUERY_REQUIRED"),
])
def test_stale_sibling_review_never_delivers_a_second_native_copy(monkeypatch, identity_change, expected):
    repo, service, filing, job, device = queued_fixture()
    calls = preflight(monkeypatch, response("0", {"ContractNum": "918412", "FTWSeqNo": "1"}))
    async def refreshed(*_args, **kwargs):
        assert kwargs["reuse_current_snapshot"] is False
        review = await repo.get_ftwilliams_review(filing.id)
        review.current_query_success = review.current_query_complete = review.current_year_exists = True
        review.bring_forward_required = False
        # Real prepare_review intentionally removes this action-only URL when
        # no Bring Forward is needed; identity remains in confirmed ID fields.
        review.ftw_plan_url = None
        for key, value in identity_change.items():
            setattr(review, key, value)
        review.updated_at = datetime.utcnow()
        return review
    service.review_service.prepare_review = refreshed
    claim = run(service.claim(device))
    assert claim.job is None
    assert len(calls) == 1
    saved = run(repo.get_ftw_local_agent_job(job.id))
    assert saved.result_state == expected
    assert run(repo.get_ftwilliams_review(filing.id)).current_year_exists


def test_a_reconciled_completed_job_cannot_be_requeued_by_stale_worker_data():
    repo, service, filing, job, _device = queued_fixture()
    run(repo.update_ftw_local_agent_job(job.id, {
        "status": FTWLocalAgentJobStatus.EXPIRED, "result_state": "NO_LONGER_REQUIRED"}))
    stale = run(repo.get_ftwilliams_review(filing.id))
    result = run(service.enqueue_bring_forward(filing, stale, run_id="stale-worker", before_record_ids=[]))
    assert result.status == FTWLocalAgentJobStatus.EXPIRED
    assert result.result_state == "NO_LONGER_REQUIRED"


@pytest.mark.parametrize("code", ["55", "1", ""])
def test_failed_or_incomplete_fresh_query_cannot_authorize_bring_forward(monkeypatch, code):
    repo, service, _filing, job, device = queued_fixture()
    preflight(monkeypatch, response(code))
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).status == FTWLocalAgentJobStatus.ACTION_NEEDED


def test_vendor_missing_after_a_prior_submission_is_held_not_repeated(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={
        "id": None, "idempotency_key": "prior-other-file", "status": FTWLocalAgentJobStatus.SUBMITTED,
        "result_state": "SUBMITTED"})))
    preflight(monkeypatch, response("59"))
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "PRIOR_OPERATION_UNCONFIRMED"


def test_operator_can_reconcile_failed_bring_forward_before_retry(monkeypatch):
    repo, service, filing, job, _device = queued_fixture()
    run(repo.update_ftw_local_agent_job(str(job.id), {
        "status": FTWLocalAgentJobStatus.ACTION_NEEDED,
        "result_state": "PRIOR_OPERATION_UNCONFIRMED",
        "operation_dispatched_at": datetime.utcnow(),
    }))
    review = run(repo.get_ftwilliams_review(filing.id))

    async def refreshed(*_args, **_kwargs):
        return review

    service.review_service.prepare_review = refreshed
    decision = SimpleNamespace(status=FTWAutomationStatus.ACTION_NEEDED, next_action="MAP_FTW_BROWSER_PLAN")
    monkeypatch.setattr(
        "app.services.ftwilliams_automation.FTWAutomationService.run",
        AsyncMock(return_value=decision),
    )

    result = run(service.reconcile_bring_forward(
        filing.id,
        "RESET_FAILED",
        "Operator confirmed the previous attempt failed.",
    ))
    reconciled = run(repo.get_ftw_local_agent_job(job.id))
    assert result["automation_next_action"] == "MAP_FTW_BROWSER_PLAN"
    assert reconciled.result_state == "RECONCILED_FAILED"
    assert reconciled.operation_dispatched_at is None


@pytest.mark.parametrize("answer", [response("0"), FTWilliamsQueryResponse(
    operation="query_schedule_a", configured=True, sent=True, request_xml="", http_status=200,
    statuses=[FTWilliamsStatusItem(error_code="59"), FTWilliamsStatusItem(error_code="55")])])
def test_empty_success_or_mixed_missing_errors_are_not_conclusive(monkeypatch, answer):
    repo, service, _filing, job, device = queued_fixture()
    preflight(monkeypatch, answer)
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "CURRENT_QUERY_REQUIRED"


def test_query_timeout_never_delivers_work_or_exposes_credentials(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    async def query(*_args):
        raise RuntimeError("SECRET_TEST_CREDENTIAL")
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService.run_query", query)
    assert run(service.claim(device)).job is None
    held = run(repo.get_ftw_local_agent_job(job.id))
    assert held.result_state == "CURRENT_QUERY_REQUIRED"
    assert "SECRET_TEST_CREDENTIAL" not in held.result_message
    assert run(repo.get_ftw_local_agent_device_by_token_hash(device.token_hash)).active_job_id is None


def test_inconsistent_browser_target_is_rejected_before_any_ftw_query(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    run(repo.update_ftw_local_agent_job(job.id, {"target_url": job.target_url.replace("plan-1", "wrong-plan")}))
    calls = preflight(monkeypatch, response("59"))
    assert run(service.claim(device)).job is None
    assert not calls
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "INVALID_TARGET"


def test_pause_during_fresh_query_leaves_pending_work_queued(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    async def query(*_args):
        await service.set_paused(device, True)
        return response("59")
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService.run_query", query)
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).status == FTWLocalAgentJobStatus.QUEUED
    assert run(repo.get_ftw_local_agent_device_by_token_hash(device.token_hash)).active_job_id is None


def test_distinct_vendor_plan_with_same_ein_does_not_block_the_correct_test_target(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={
        "id": None, "idempotency_key": "another-vendor-plan", "status": FTWLocalAgentJobStatus.VERIFIED,
        "target_url": job.target_url.replace("plan-1", "plan-2"), "result_state": "SUBMITTED"})))
    preflight(monkeypatch, response("59"))
    assert run(service.claim(device)).job.id == job.id


def test_ten_distinct_plan_jobs_resume_once_each_after_a_long_pause(monkeypatch):
    from datetime import timedelta
    from app.models import FTWLocalAgentCompleteRequest
    repo, service, filing, job, device = queued_fixture()
    review = run(repo.get_ftwilliams_review(filing.id))
    for index in range(2, 11):
        new_filing = run(repo.create_filing(filing.model_copy(update={"id": None})))
        new_review = review.model_copy(deep=True, update={
            "id": None, "filing_id": new_filing.id, "ftw_browser_plan_id": f"plan-{index}",
            "ftw_plan_id": f"plan-{index}",
            "ftw_plan_url": review.ftw_plan_url.replace("plan-1", f"plan-{index}")})
        run(repo.upsert_ftwilliams_review(new_review))
        run(service.enqueue_bring_forward(new_filing, new_review, run_id="batch", before_record_ids=[]))
    run(service.set_paused(device, True))
    for item in repo.ftw_local_agent_jobs.values():
        item.expires_at = datetime.utcnow() - timedelta(days=2)
    calls = preflight(monkeypatch, response("59"))
    assert run(service.claim(device)).job is None
    assert not calls
    run(service.set_paused(device, False))
    delivered = []
    for _ in range(10):
        claim = run(service.claim(device))
        assert claim.job
        delivered.append(claim.job.id)
        run(service.complete(device, claim.job.id, FTWLocalAgentCompleteRequest(
            claim_token=claim.claim_token, state="SUBMITTED", message="Synthetic test submission")))
    assert len(set(delivered)) == 10
    assert run(service.claim(device)).job is None


def test_current_exists_but_full_review_failure_never_copies_or_claims_success(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    preflight(monkeypatch, response("0", {"FTWSeqNo": "1", "ContractNum": "918412"}))
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "CURRENT_QUERY_REQUIRED"


def test_mongo_history_includes_uncertain_and_completed_operations_not_just_pending():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from app.repositories import MongoRepository
    cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
    collection = SimpleNamespace(find=Mock(return_value=cursor))
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_jobs=collection)
    assert run(repo.list_ftw_target_operation_history("HighlandTech", "2025")) == []
    selector = collection.find.call_args.args[0]
    assert selector["expected_year"] == "2025"
    assert "VERIFIED" in selector["$or"][0]["status"]["$in"]
    assert "UNKNOWN_OUTCOME" in selector["$or"][1]["result_state"]["$in"]


def test_transient_query_failure_recovers_automatically_without_a_manual_retry(monkeypatch):
    from datetime import timedelta
    repo, service, _filing, job, device = queued_fixture()
    preflight(monkeypatch, response("55"))
    assert run(service.claim(device)).job is None
    preflight(monkeypatch, response("59"))
    assert run(service.claim(device)).job is None, "Honor read-only retry backoff"
    run(repo.update_ftw_local_agent_job(job.id, {"preflight_retry_at": datetime.utcnow() - timedelta(seconds=1)}))
    claim = run(service.claim(device))
    assert claim.job.id == job.id
    assert run(repo.get_ftw_local_agent_job(job.id)).operation_dispatched_at is not None


def test_unknown_dispatch_marker_survives_result_reset_and_prevents_a_repeat(monkeypatch):
    from app.models import FTWLocalAgentCompleteRequest
    repo, service, filing, job, device = queued_fixture()
    preflight(monkeypatch, response("59"))
    claim = run(service.claim(device))
    run(service.complete(device, job.id, FTWLocalAgentCompleteRequest(
        claim_token=claim.claim_token, state="UNKNOWN_OUTCOME", message="Lost browser outcome")))
    review = run(repo.get_ftwilliams_review(filing.id))
    review.current_query_success = review.current_query_complete = True
    completed = run(repo.get_ftw_local_agent_job(job.id))
    review.updated_at = completed.completed_at + timedelta(seconds=1)
    run(service.enqueue_bring_forward(filing, review, run_id="retry", before_record_ids=[]))
    assert run(repo.get_ftw_local_agent_job(job.id)).operation_dispatched_at is not None
    assert run(service.claim(device)).job is None
    assert run(repo.get_ftw_local_agent_job(job.id)).result_state == "PRIOR_OPERATION_UNCONFIRMED"


def test_confirmed_login_exit_can_retry_without_being_mistaken_for_a_native_copy(monkeypatch):
    from app.models import FTWLocalAgentCompleteRequest
    repo, service, _filing, job, device = queued_fixture()
    preflight(monkeypatch, response("59"))
    claim = run(service.claim(device))
    run(service.complete(device, job.id, FTWLocalAgentCompleteRequest(
        claim_token=claim.claim_token, state="LOGIN_REQUIRED", message="Needs login before action")))
    assert run(repo.get_ftw_local_agent_job(job.id)).operation_dispatched_at is None
    assert run(service.claim(device)).job.id == job.id


def test_dispatch_marker_write_is_atomic_against_an_expired_or_replaced_claim():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bson import ObjectId
    from app.repositories import MongoRepository
    jobs = SimpleNamespace(find_one_and_update=AsyncMock(return_value=None))
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_jobs=jobs)
    now = datetime.utcnow()
    assert run(repo.mark_ftw_job_dispatched(str(ObjectId()), "device", "claim-hash", now)) is None
    selector = jobs.find_one_and_update.await_args.args[0]
    assert selector["status"] == "CLAIMED"
    assert selector["claim_token_hash"] == "claim-hash"
    assert selector["claim_expires_at"] == {"$gt": now}


def test_competing_devices_cannot_receive_two_native_operations(monkeypatch):
    repo, service, _filing, job, device = queued_fixture()
    second = run(repo.create_ftw_local_agent_device(device.model_copy(update={"id": None, "token_hash": "second"})))
    run(repo.create_or_get_ftw_local_agent_job(job.model_copy(update={"id": None, "idempotency_key": "sibling"})))
    async def query(*_args):
        await asyncio.sleep(0)
        return response("59")
    monkeypatch.setattr("app.services.ftwilliams.FTWilliamsService.run_query", query)
    async def claims():
        return await asyncio.gather(service.claim(device), service.claim(second))
    assert sum(c.job is not None for c in run(claims())) == 1
