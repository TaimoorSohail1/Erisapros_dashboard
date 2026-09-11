import hashlib
import secrets
from datetime import datetime, timedelta

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    Filing,
    FTWAutomationStatus,
    FTWilliamsReview,
    FTWLocalAgentClaimResponse,
    FTWLocalAgentCompleteRequest,
    FTWLocalAgentDevice,
    FTWLocalAgentDeviceStatus,
    FTWLocalAgentHeartbeatRequest,
    FTWLocalAgentJob,
    FTWLocalAgentJobPayload,
    FTWLocalAgentJobStatus,
    FTWLocalAgentPairRequest,
    FTWLocalAgentPairResponse,
    FTWLocalAgentPairingCode,
    FTWLocalAgentPairingCodeResponse,
    FTWLocalAgentStatusResponse,
)
from app.repositories import Repository, get_repository
from app.services.ftwilliams_local_agent import LocalFTWTarget


class FTWLocalAgentAuthenticationError(ValueError):
    pass


class FTWLocalAgentService:
    def __init__(
        self,
        *,
        repo: Repository | None = None,
        settings: Settings | None = None,
        review_service=None,
    ):
        self.repo = repo or get_repository()
        self.settings = settings or get_settings()
        self.review_service = review_service

    @staticmethod
    def _hash_token(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    async def create_pairing_code(
        self,
        *,
        created_by: str | None = None,
        workspace_id: str | None = None,
        expected_account: str | None = None,
    ) -> FTWLocalAgentPairingCodeResponse:
        code = secrets.token_urlsafe(9)
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=max(60, self.settings.ftw_local_agent_pairing_ttl_seconds))
        await self.repo.create_ftw_local_agent_pairing_code(
            FTWLocalAgentPairingCode(
                code_hash=self._hash_token(code),
                code_prefix=code[:4],
                expected_account=(expected_account or self.settings.ftw_local_agent_expected_account),
                workspace_id=workspace_id,
                created_by=created_by,
                expires_at=expires_at,
            )
        )
        return FTWLocalAgentPairingCodeResponse(
            pairing_code=code,
            expires_at=expires_at,
            workspace_id=workspace_id,
        )

    async def pair(self, payload: FTWLocalAgentPairRequest) -> FTWLocalAgentPairResponse:
        code = str(payload.pairing_code or "").strip()
        name = str(payload.device_name or "").strip()
        version = str(payload.agent_version or "").strip()
        if not code or not name or not version:
            raise ValueError("Pairing code, device name, and agent version are required.")
        now = datetime.utcnow()
        pairing = await self.repo.consume_ftw_local_agent_pairing_code(self._hash_token(code), now)
        if not pairing:
            raise ValueError("The pairing code is invalid, expired, or already used.")
        token = secrets.token_urlsafe(32)
        device = await self.repo.create_ftw_local_agent_device(
            FTWLocalAgentDevice(
                name=name[:120],
                token_hash=self._hash_token(token),
                token_prefix=token[:6],
                expected_account=pairing.expected_account,
                workspace_id=pairing.workspace_id,
                status=FTWLocalAgentDeviceStatus.OFFLINE,
                agent_version=version[:40],
                paired_by=pairing.created_by,
            )
        )
        return FTWLocalAgentPairResponse(
            device_id=str(device.id),
            device_token=token,
            expected_account=device.expected_account,
            workspace_id=device.workspace_id,
        )

    async def authenticate(self, token: str) -> FTWLocalAgentDevice:
        raw = str(token or "").strip()
        if not raw:
            raise FTWLocalAgentAuthenticationError("A local-agent device token is required.")
        device = await self.repo.get_ftw_local_agent_device_by_token_hash(self._hash_token(raw))
        if not device or device.revoked_at or device.status == FTWLocalAgentDeviceStatus.REVOKED:
            raise FTWLocalAgentAuthenticationError("The local-agent device token is invalid or revoked.")
        return device

    async def heartbeat(
        self,
        device: FTWLocalAgentDevice,
        payload: FTWLocalAgentHeartbeatRequest,
    ) -> FTWLocalAgentDevice:
        now = datetime.utcnow()
        status = (
            FTWLocalAgentDeviceStatus.LOGIN_REQUIRED
            if payload.login_required
            else FTWLocalAgentDeviceStatus.CONNECTED
        )
        updated = await self.repo.update_ftw_local_agent_device(
            str(device.id),
            {
                "status": status,
                "agent_version": str(payload.agent_version or "")[:40],
                "browser_ready": bool(payload.browser_ready and not payload.login_required),
                "last_error": str(payload.last_error or "")[:500] or None,
                "last_seen_at": now,
            },
        )
        if not updated:
            raise FTWLocalAgentAuthenticationError("The paired local-agent device no longer exists.")
        return updated

    async def revoke(self, device_id: str) -> FTWLocalAgentDevice:
        updated = await self.repo.update_ftw_local_agent_device(
            device_id,
            {
                "status": FTWLocalAgentDeviceStatus.REVOKED,
                "revoked_at": datetime.utcnow(),
                "browser_ready": False,
            },
        )
        if not updated:
            raise ValueError("Local-agent device not found.")
        return updated

    async def status(self, *, paired_by: str | None = None) -> FTWLocalAgentStatusResponse:
        if not self.settings.ftw_local_agent_enabled:
            return FTWLocalAgentStatusResponse(enabled=False, connected=False)
        devices = [
            device
            for device in await self.repo.list_ftw_local_agent_devices()
            if not device.revoked_at and device.status != FTWLocalAgentDeviceStatus.REVOKED
            and (not paired_by or device.paired_by == paired_by)
        ]
        if not devices:
            return FTWLocalAgentStatusResponse(enabled=True, connected=False)
        devices.sort(key=lambda item: item.last_seen_at or datetime.min, reverse=True)
        device = devices[0]
        fresh_after = datetime.utcnow() - timedelta(
            seconds=max(15, self.settings.ftw_local_agent_heartbeat_ttl_seconds)
        )
        connected = bool(
            device.last_seen_at
            and device.last_seen_at >= fresh_after
            and device.status == FTWLocalAgentDeviceStatus.CONNECTED
            and device.browser_ready
        )
        state = device.status if device.last_seen_at and device.last_seen_at >= fresh_after else FTWLocalAgentDeviceStatus.OFFLINE
        return FTWLocalAgentStatusResponse(
            enabled=True,
            connected=connected,
            device_count=len(devices),
            status=state,
            device_name=device.name,
            agent_version=device.agent_version,
            last_seen_at=device.last_seen_at,
            last_error=device.last_error,
        )

    async def enqueue_bring_forward(
        self,
        filing: Filing,
        review: FTWilliamsReview,
        *,
        run_id: str,
        before_record_ids: list[str],
    ) -> FTWLocalAgentJob:
        lookup = review.plan_lookup
        if not lookup:
            raise ValueError("A confirmed FT Williams plan lookup is required for the local agent.")
        target = LocalFTWTarget.from_dict(
            {
                "label": filing.file_name,
                "url": review.ftw_plan_url,
                "plan_name": lookup.plan_name,
                "ein": lookup.company_employer_id,
                "plan_number": lookup.plan_number,
                "year": review.year,
            }
        )
        now = datetime.utcnow()
        target_key = "|".join(
            [
                str(review.ftw_browser_customer_id or "").strip().casefold(),
                str(review.ftw_browser_plan_id or "").strip().casefold(),
                target.year,
            ]
        )
        job = await self.repo.create_or_get_ftw_local_agent_job(
            FTWLocalAgentJob(
                filing_id=str(filing.id),
                run_id=run_id,
                idempotency_key=f"{filing.id}|{target_key}",
                target_url=target.url,
                expected_account=self.settings.ftw_local_agent_expected_account,
                expected_plan_name=target.plan_name,
                expected_ein=target.ein,
                expected_plan_number=target.plan_number,
                expected_year=target.year,
                before_record_ids=before_record_ids,
                expires_at=now + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds)),
            )
        )
        if job.expires_at <= now or job.status in {
            FTWLocalAgentJobStatus.ACTION_NEEDED,
            FTWLocalAgentJobStatus.FAILED,
            FTWLocalAgentJobStatus.EXPIRED,
        }:
            refreshed = await self.repo.update_ftw_local_agent_job(
                str(job.id),
                {
                    "status": FTWLocalAgentJobStatus.QUEUED,
                    "run_id": run_id,
                    "target_url": target.url,
                    "expected_account": self.settings.ftw_local_agent_expected_account,
                    "expected_plan_name": target.plan_name,
                    "expected_ein": target.ein,
                    "expected_plan_number": target.plan_number,
                    "expected_year": target.year,
                    "before_record_ids": before_record_ids,
                    "device_id": None,
                    "claim_token_hash": None,
                    "claim_expires_at": None,
                    "result_state": None,
                    "result_message": None,
                    "expires_at": now + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds)),
                },
            )
            if refreshed:
                job = refreshed
        return job

    async def claim(self, device: FTWLocalAgentDevice) -> FTWLocalAgentClaimResponse:
        job = None
        claim_token = None
        for _ in range(10):
            candidate_token = secrets.token_urlsafe(24)
            now = datetime.utcnow()
            candidate = await self.repo.claim_ftw_local_agent_job(
                str(device.id),
                device.expected_account,
                self._hash_token(candidate_token),
                now,
                now + timedelta(seconds=max(30, self.settings.ftw_local_agent_claim_ttl_seconds)),
            )
            if not candidate:
                break
            review = await self.repo.get_ftwilliams_review(candidate.filing_id)
            if review and review.bring_forward_required and not review.current_year_exists:
                job = candidate
                claim_token = candidate_token
                break
            await self.repo.update_ftw_local_agent_job(
                str(candidate.id),
                {
                    "status": FTWLocalAgentJobStatus.EXPIRED,
                    "claim_token_hash": None,
                    "claim_expires_at": None,
                    "result_state": "NO_LONGER_REQUIRED",
                    "result_message": "Bring Forward is no longer required; the queued local-agent job was cancelled.",
                    "completed_at": now,
                },
            )
        payload = (
            FTWLocalAgentJobPayload(
                id=str(job.id),
                filing_id=job.filing_id,
                target_url=job.target_url,
                expected_account=job.expected_account,
                expected_plan_name=job.expected_plan_name,
                expected_ein=job.expected_ein,
                expected_plan_number=job.expected_plan_number,
                expected_year=job.expected_year,
                expires_at=job.expires_at,
            )
            if job
            else None
        )
        return FTWLocalAgentClaimResponse(job=payload, claim_token=claim_token if job else None)

    async def complete(
        self,
        device: FTWLocalAgentDevice,
        job_id: str,
        payload: FTWLocalAgentCompleteRequest,
    ) -> FTWLocalAgentJob:
        state = payload.state.upper()
        status = (
            FTWLocalAgentJobStatus.SUBMITTED
            if state == "SUBMITTED"
            else FTWLocalAgentJobStatus.ACTION_NEEDED
            if state in {"LOGIN_REQUIRED", "INVALID_TARGET", "PAGE_LAYOUT_CHANGED"}
            else FTWLocalAgentJobStatus.FAILED
        )
        completed = await self.repo.complete_ftw_local_agent_job(
            job_id,
            str(device.id),
            self._hash_token(payload.claim_token),
            datetime.utcnow(),
            {
                "status": status,
                "result_state": state,
                "result_message": str(payload.message or "")[:1_000],
                "completed_at": datetime.utcnow(),
            },
        )
        if not completed:
            raise ValueError("The local-agent job claim is invalid, expired, or already completed.")
        if state == "LOGIN_REQUIRED":
            await self.repo.update_ftw_local_agent_device(
                str(device.id),
                {
                    "status": FTWLocalAgentDeviceStatus.LOGIN_REQUIRED,
                    "browser_ready": False,
                    "last_error": completed.result_message,
                    "last_seen_at": datetime.utcnow(),
                },
            )
        return completed

    async def continue_after_completion(self, job: FTWLocalAgentJob) -> FTWLocalAgentJob:
        filing = await self.repo.get_filing(job.filing_id)
        review = await self.repo.get_ftwilliams_review(job.filing_id)
        if not filing or not review:
            raise ValueError("The filing or FT Williams review no longer exists.")
        if job.status != FTWLocalAgentJobStatus.SUBMITTED:
            reason = job.result_message or "The local FT Williams agent stopped safely."
            next_action = "LOGIN_TO_FTW" if job.result_state == "LOGIN_REQUIRED" else "RETRY"
            await self.repo.update_filing(
                job.filing_id,
                {
                    "automation_status": FTWAutomationStatus.ACTION_NEEDED,
                    "automation_reasons": [reason],
                    "automation_next_action": next_action,
                },
            )
            await self.repo.add_audit(
                AuditLog(
                    filing_id=job.filing_id,
                    event="FTW_LOCAL_AGENT_BRING_FORWARD_FAILED",
                    message=reason,
                    details={"job_id": job.id, "state": job.result_state, "run_id": job.run_id},
                )
            )
            return job

        from app.services.ftwilliams_automation import FTWAutomationPolicy, FTWAutomationService
        from app.services.ftwilliams_review import FTWilliamsReviewService

        target_key = FTWAutomationPolicy.bring_forward_target_key(review)
        await self.repo.update_filing(
            job.filing_id,
            {
                "automation_bring_forward_target_key": target_key,
                "automation_bring_forward_submitted_at": datetime.utcnow(),
                "automation_bring_forward_verified_at": None,
                "automation_bring_forward_before_record_ids": list(job.before_record_ids),
                "automation_bring_forward_new_record_ids": [],
            },
        )
        await self.repo.add_audit(
            AuditLog(
                filing_id=job.filing_id,
                event="FTW_LOCAL_AGENT_BRING_FORWARD_SUBMITTED",
                message="The client-local FT Williams agent submitted Bring Forward; ftwLink verification started.",
                details={"job_id": job.id, "run_id": job.run_id, "before_record_ids": job.before_record_ids},
            )
        )
        review_service = self.review_service or FTWilliamsReviewService()
        try:
            refreshed_review = await review_service.prepare_review(
                job.filing_id,
                send_queries=True,
                reuse_current_snapshot=False,
            )
            await self.repo.upsert_ftwilliams_review(refreshed_review)
        except Exception as exc:
            return await self._stop_after_submission(
                job,
                f"Bring Forward was submitted, but the ftwLink verification query failed: {exc}",
            )

        after_record_ids = FTWAutomationService._schedule_a_record_ids(refreshed_review)
        new_record_ids = sorted(set(after_record_ids) - set(job.before_record_ids))
        verified = bool(
            refreshed_review.current_year_exists
            and not refreshed_review.bring_forward_required
            and new_record_ids
        )
        if not verified:
            return await self._stop_after_submission(
                job,
                "Bring Forward was submitted, but ftwLink did not confirm a new current-year Schedule A record.",
                after_record_ids=after_record_ids,
            )

        await self.repo.update_filing(
            job.filing_id,
            {
                "automation_bring_forward_verified_at": datetime.utcnow(),
                "automation_bring_forward_new_record_ids": new_record_ids,
            },
        )
        try:
            decision = await FTWAutomationService(
                repo=self.repo,
                review_service=review_service,
                settings=self.settings,
            ).run(job.filing_id, review=refreshed_review)
        except Exception as exc:
            return await self._stop_after_submission(
                job,
                f"Bring Forward was verified, but the existing automation workflow stopped safely: {exc}",
                after_record_ids=after_record_ids,
            )
        updated = await self.repo.update_ftw_local_agent_job(
            str(job.id),
            {
                "status": FTWLocalAgentJobStatus.VERIFIED,
                "result_message": "ftwLink verified the new current-year Schedule A records.",
            },
        )
        await self.repo.add_audit(
            AuditLog(
                filing_id=job.filing_id,
                event="FTW_LOCAL_AGENT_POST_BRING_FORWARD_VERIFIED",
                message=(updated.result_message if updated else "Local-agent verification finished."),
                details={
                    "job_id": job.id,
                    "run_id": job.run_id,
                    "automation_status": decision.status.value,
                    "before_record_ids": job.before_record_ids,
                    "after_record_ids": after_record_ids,
                    "new_record_ids": new_record_ids,
                },
            )
        )
        return updated or job

    async def _stop_after_submission(
        self,
        job: FTWLocalAgentJob,
        reason: str,
        *,
        after_record_ids: list[str] | None = None,
    ) -> FTWLocalAgentJob:
        await self.repo.update_filing(
            job.filing_id,
            {
                "automation_status": FTWAutomationStatus.ACTION_NEEDED,
                "automation_reasons": [reason],
                "automation_next_action": "RETRY_AFTER_CURRENT_QUERY",
            },
        )
        updated = await self.repo.update_ftw_local_agent_job(
            str(job.id),
            {
                "status": FTWLocalAgentJobStatus.ACTION_NEEDED,
                "result_message": reason[:1_000],
            },
        )
        await self.repo.add_audit(
            AuditLog(
                filing_id=job.filing_id,
                event="FTW_LOCAL_AGENT_POST_BRING_FORWARD_PENDING",
                message=reason,
                details={
                    "job_id": job.id,
                    "run_id": job.run_id,
                    "before_record_ids": job.before_record_ids,
                    "after_record_ids": after_record_ids or [],
                },
            )
        )
        return updated or job
