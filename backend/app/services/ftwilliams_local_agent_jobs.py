import asyncio
import hashlib
import secrets
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    Filing,
    FilingStatus,
    FTWAutomationStatus,
    FTWilliamsReview,
    FTWilliamsQueryRequest,
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


# FT Williams may acknowledge the native copy before its ftwLink query sees
# the newly-created Schedule A records.  Retry the read-back only; never repeat
# the browser click for the same claimed operation.
_POST_BRING_FORWARD_VERIFY_ATTEMPTS = 5
_POST_BRING_FORWARD_VERIFY_DELAY_SECONDS = 3.0


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

    @staticmethod
    def _identity(value: str | None) -> str:
        return "".join(character for character in str(value or "").casefold() if character.isalnum())

    @staticmethod
    def _canonical_ein(value: str | None) -> str:
        digits = "".join(character for character in str(value or "") if character.isdigit())
        return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else str(value or "").strip()

    @staticmethod
    def _canonical_plan_number(value: str | None) -> str:
        text = str(value or "").strip()
        return text.zfill(3) if text.isdigit() and len(text) <= 3 else text

    def _device_is_ready(self, device: FTWLocalAgentDevice, now: datetime) -> bool:
        fresh_after = now - timedelta(seconds=max(15, self.settings.ftw_local_agent_heartbeat_ttl_seconds))
        return bool(
            not device.revoked_at
            and not device.pause_requested
            and device.status == FTWLocalAgentDeviceStatus.CONNECTED
            and device.browser_ready
            and device.last_seen_at
            and device.last_seen_at >= fresh_after
        )

    def _device_can_accept_queued_work(self, device: FTWLocalAgentDevice, now: datetime) -> bool:
        """Allow a fresh paired agent to finish login before claiming its job."""
        fresh_after = now - timedelta(seconds=max(15, self.settings.ftw_local_agent_heartbeat_ttl_seconds))
        return bool(
            self._device_is_ready(device, now)
            or device.pause_requested
            or (
                not device.revoked_at
                and device.status == FTWLocalAgentDeviceStatus.LOGIN_REQUIRED
                and device.last_seen_at
                and device.last_seen_at >= fresh_after
            )
        )

    async def _resolve_workspace_route(
        self,
        filing: Filing,
        review: FTWilliamsReview,
    ) -> tuple[str, str, FTWLocalAgentDevice]:
        workspace_id = str(filing.workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("Assign this filing to a client workspace before Bring Forward can run.")
        workspace = await self.repo.get_ftw_client_workspace(workspace_id)
        if not workspace or not workspace.enabled:
            raise ValueError("The filing's client workspace is missing or disabled.")
        lookup = review.plan_lookup
        if not lookup:
            raise ValueError("A confirmed FT Williams plan lookup is required for the local agent.")
        year = str(review.year or lookup.year or "").strip()
        # PlanIDs lookup already supplied the exact API/browser identity. A
        # separate manually saved workspace mapping must not block Bring Forward.
        if (
            not str(review.ftw_customer_id or "").strip()
            or not str(review.ftw_plan_id or "").strip()
            or not str(review.ftw_browser_customer_id or "").strip()
            or not str(review.ftw_browser_plan_id or "").strip()
            or not year
        ):
            raise ValueError("The current FT Williams lookup did not return a complete client and plan identity.")
        now = datetime.utcnow()
        devices = [
            device
            for device in await self.repo.list_ftw_local_agent_devices()
            if device.workspace_id == workspace_id
            and not device.revoked_at
            and self._identity(device.expected_account) == self._identity(workspace.expected_account)
            and self._device_can_accept_queued_work(device, now)
        ]
        if not devices:
            raise ValueError("No connected, signed-in FT Williams computer is available for this client workspace.")
        devices.sort(
            key=lambda item: (
                self._device_is_ready(item, now),
                not item.pause_requested,
                item.last_seen_at or datetime.min,
            ),
            reverse=True,
        )
        return workspace_id, workspace.expected_account, devices[0]

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
        device = await self._fresh_device(device)
        now = datetime.utcnow()
        status = (
            FTWLocalAgentDeviceStatus.PAUSED if device.pause_requested and payload.paused
            else FTWLocalAgentDeviceStatus.PAUSING if device.pause_requested
            else FTWLocalAgentDeviceStatus.WAITING if payload.waiting
            else FTWLocalAgentDeviceStatus.RESUMING if payload.paused
            else FTWLocalAgentDeviceStatus.LOGIN_REQUIRED
            if payload.login_required
            else FTWLocalAgentDeviceStatus.CONNECTED
        )
        updated = await self.repo.update_ftw_local_agent_device(
            str(device.id),
            {
                "status": status,
                "agent_version": str(payload.agent_version or "")[:40],
                "browser_ready": bool(payload.browser_ready and not payload.login_required and not device.pause_requested and not payload.paused and not payload.waiting),
                "last_error": str(payload.last_error or "")[:500] or None,
                "last_seen_at": now,
            },
        )
        if not updated:
            raise FTWLocalAgentAuthenticationError("The paired local-agent device no longer exists.")
        if payload.paused or payload.waiting:
            # Only release after the updated agent acknowledges browser closure.
            await self.repo.release_ftw_browser_lease(device.expected_account, str(device.id))
        return updated

    async def _fresh_device(self, device: FTWLocalAgentDevice) -> FTWLocalAgentDevice:
        current = await self.repo.get_ftw_local_agent_device_by_token_hash(device.token_hash)
        if not current or current.revoked_at:
            raise FTWLocalAgentAuthenticationError("The paired local-agent device is missing or revoked.")
        return current

    @staticmethod
    def _supports_browser_control(device: FTWLocalAgentDevice) -> bool:
        try:
            version = tuple(int(part) for part in str(device.agent_version or "").split("."))
        except ValueError:
            version = ()
        return version >= (0, 4, 0)

    async def _legacy_browser_present(self, device: FTWLocalAgentDevice) -> bool:
        fresh_after = datetime.utcnow() - timedelta(seconds=max(15, self.settings.ftw_local_agent_heartbeat_ttl_seconds))
        account_key = "".join(char for char in device.expected_account.casefold() if char.isalnum())
        return any(
            not item.revoked_at and item.last_seen_at and item.last_seen_at >= fresh_after
            and not self._supports_browser_control(item)
            and "".join(char for char in item.expected_account.casefold() if char.isalnum()) == account_key
            for item in await self.repo.list_ftw_local_agent_devices()
        )

    async def set_paused(self, device: FTWLocalAgentDevice, paused: bool) -> FTWLocalAgentDevice:
        device = await self._fresh_device(device)
        if not self._supports_browser_control(device):
            raise ValueError("Update this computer to FTW Agent 0.4.0 or newer before using Pause/Resume.")
        if device.pause_requested == paused:
            return device
        if not paused:
            # Pausing does not consume pending jobs' execution window.
            await self.repo.renew_ftw_pending_jobs(
                device, datetime.utcnow() + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds)),
            )
        updated = await self.repo.update_ftw_local_agent_device(str(device.id), {
            "pause_requested": paused,
            "paused_at": datetime.utcnow() if paused else None,
            "status": FTWLocalAgentDeviceStatus.PAUSING if paused else FTWLocalAgentDeviceStatus.RESUMING,
            "browser_ready": False,
        })
        if not updated:
            raise ValueError("Local-agent device not found.")
        await self.repo.add_audit(AuditLog(
            event="FTW_LOCAL_AGENT_PAUSE_REQUESTED" if paused else "FTW_LOCAL_AGENT_RESUME_REQUESTED",
            message="Agent pause requested; active work may finish safely." if paused else "Agent resume requested; only pending work can continue.",
            details={"device_id": device.id, "workspace_id": device.workspace_id},
        ))
        return updated

    async def control(self, device: FTWLocalAgentDevice) -> dict:
        device = await self._fresh_device(device)
        for uncertain in await self.repo.expire_ftw_agent_claims(device.expected_account, datetime.utcnow()):
            try:
                await self.continue_after_completion(uncertain)
            except ValueError:
                # Deleted filings remain non-replayable without blocking other work.
                pass
        allowed = False
        if not self.settings.ftw_local_agent_enabled:
            return {"pause_requested": device.pause_requested, "browser_allowed": False,
                    "reason": "The FTW local-agent rollout is disabled. Its dedicated browser stays closed."}
        if not device.pause_requested and await self._legacy_browser_present(device):
            return {"pause_requested": False, "browser_allowed": False,
                    "reason": "Update all active agents for this FTW account to 0.4.0, or stop the older agents, before this dedicated browser can resume safely."}
        if not device.pause_requested and self.settings.ftw_local_agent_enabled:
            pending = await self.repo.list_ftw_pending_agent_jobs(device.expected_account)
            devices = await self.repo.list_ftw_local_agent_devices()
            fresh_after = datetime.utcnow() - timedelta(seconds=max(15, self.settings.ftw_local_agent_heartbeat_ttl_seconds))
            available_ids = {str(item.id) for item in devices if not item.pause_requested and not item.revoked_at
                             and item.last_seen_at and item.last_seen_at >= fresh_after}
            priority = next((job.assigned_device_id for job in pending
                             if job.expires_at > datetime.utcnow() and job.assigned_device_id in available_ids), None)
            if priority and priority != str(device.id) and not device.active_job_id:
                # An idle account owner yields its browser before the assigned
                # device starts; queue routing must not starve behind idle login.
                return {"pause_requested": False, "browser_allowed": False}
            allowed = await self.repo.acquire_ftw_browser_lease(
                device.expected_account, str(device.id), datetime.utcnow(),
                datetime.utcnow() + timedelta(seconds=max(300, self.settings.ftw_local_agent_claim_ttl_seconds + 180)),
            )
        return {"pause_requested": device.pause_requested, "browser_allowed": allowed}

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

    async def status(
        self,
        *,
        paired_by: str | None = None,
        workspace_ids: set[str] | None = None,
    ) -> FTWLocalAgentStatusResponse:
        if not self.settings.ftw_local_agent_enabled:
            return FTWLocalAgentStatusResponse(enabled=False, connected=False)
        devices = [
            device
            for device in await self.repo.list_ftw_local_agent_devices()
            if not device.revoked_at and device.status != FTWLocalAgentDeviceStatus.REVOKED
            and (not paired_by or device.paired_by == paired_by)
            and (workspace_ids is None or device.workspace_id in workspace_ids)
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
            pause_requested=device.pause_requested,
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
        workspace_id = None
        assigned_device = None
        expected_account = self.settings.ftw_local_agent_expected_account
        target_url = review.ftw_plan_url
        if self.settings.ftw_local_agent_workspace_routing_enabled:
            workspace_id, expected_account, assigned_device = await self._resolve_workspace_route(filing, review)
            target_url = self.settings.ftw_plan_page_url_template.format(
                ftw_browser_customer_id=review.ftw_browser_customer_id,
                ftw_browser_plan_id=review.ftw_browser_plan_id,
                year=review.year,
            )
        target = LocalFTWTarget.from_dict(
            {
                "label": filing.file_name,
                "url": target_url,
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
        idempotency_prefix = f"{workspace_id}|" if workspace_id else ""
        job = await self.repo.create_or_get_ftw_local_agent_job(
            FTWLocalAgentJob(
                filing_id=str(filing.id),
                run_id=run_id,
                idempotency_key=f"{idempotency_prefix}{filing.id}|{target_key}",
                target_url=target.url,
                expected_account=expected_account,
                expected_plan_name=target.plan_name,
                expected_ein=target.ein,
                expected_plan_number=target.plan_number,
                expected_year=target.year,
                workspace_id=workspace_id,
                mapping_id=None,
                assigned_device_id=str(assigned_device.id) if assigned_device else None,
                before_record_ids=before_record_ids,
                expires_at=now + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds)),
            )
        )
        outcome_uncertain = job.result_state == "UNKNOWN_OUTCOME" or (
            job.result_state == "SUBMITTED" and job.status == FTWLocalAgentJobStatus.ACTION_NEEDED
        )
        # A stale sibling/process snapshot must never turn a completed native
        # operation back into queued browser work.
        if job.status == FTWLocalAgentJobStatus.VERIFIED or job.result_state == "NO_LONGER_REQUIRED":
            return job
        if not job.operation_dispatched_at and (outcome_uncertain or job.status == FTWLocalAgentJobStatus.SUBMITTED
                or (job.status == FTWLocalAgentJobStatus.FAILED and job.claimed_at)):
            # Preserve legacy operation evidence before any retry reset.
            job = await self.repo.update_ftw_local_agent_job(str(job.id), {
                "operation_dispatched_at": job.claimed_at or job.completed_at or job.created_at,
            }) or job
        if outcome_uncertain and not (
            review.current_query_success and review.current_query_complete
            and review.updated_at > (job.completed_at or job.claimed_at or job.created_at)
        ):
            raise ValueError("Refresh FTW current data successfully before retrying an operation with an unknown outcome.")
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
                    "expected_account": expected_account,
                    "expected_plan_name": target.plan_name,
                    "expected_ein": target.ein,
                    "expected_plan_number": target.plan_number,
                    "expected_year": target.year,
                    "workspace_id": workspace_id,
                    "mapping_id": None,
                    "assigned_device_id": str(assigned_device.id) if assigned_device else None,
                    "before_record_ids": before_record_ids,
                    "device_id": None,
                    "claim_token_hash": None,
                    "claim_expires_at": None,
                    "result_state": None,
                    "result_message": None,
                    "completed_at": None,
                    "verification_started_at": None,
                    "expires_at": now + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds)),
                },
            )
            if refreshed:
                job = refreshed
        return job

    async def cancel_undispatched_jobs_for_filing(self, filing_id: str) -> list[str]:
        """Cancel pre-click work when its dashboard filing is deleted.

        Jobs with browser-dispatch evidence remain in the journal because the
        FT Williams outcome may be uncertain. Queued and preflight-only work is
        safe to cancel and must not keep a computer reservation stuck.
        """
        cancelled: list[str] = []
        for job in await self.repo.list_ftw_local_agent_jobs_for_filing(filing_id):
            if (
                job.operation_dispatched_at is not None
                or job.status
                in {
                    FTWLocalAgentJobStatus.SUBMITTED,
                    FTWLocalAgentJobStatus.VERIFIED,
                }
            ):
                if job.device_id and job.status != FTWLocalAgentJobStatus.CLAIMED:
                    await self.repo.release_ftw_device_job(str(job.device_id), str(job.id))
                continue
            updated = await self.repo.update_ftw_local_agent_job(
                str(job.id),
                {
                    "status": FTWLocalAgentJobStatus.EXPIRED,
                    "result_state": "NO_LONGER_REQUIRED",
                    "result_message": "The dashboard filing was deleted before Bring Forward was dispatched.",
                    "claim_token_hash": None,
                    "claim_expires_at": None,
                    "completed_at": datetime.utcnow(),
                    "preflight_retry_at": None,
                },
            )
            if not updated:
                continue
            if job.device_id:
                await self.repo.release_ftw_device_job(str(job.device_id), str(job.id))
            cancelled.append(str(job.id))
        return cancelled

    async def claim(self, device: FTWLocalAgentDevice) -> FTWLocalAgentClaimResponse:
        device = await self._fresh_device(device)
        if not self.settings.ftw_local_agent_enabled or device.pause_requested or device.revoked_at:
            return FTWLocalAgentClaimResponse()
        if self._supports_browser_control(device) and await self._legacy_browser_present(device):
            return FTWLocalAgentClaimResponse()
        if self.settings.ftw_local_agent_workspace_routing_enabled:
            if not device.workspace_id or not self._device_is_ready(device, datetime.utcnow()):
                return FTWLocalAgentClaimResponse()
        if device.active_job_id:
            active = await self.repo.get_ftw_local_agent_job(device.active_job_id)
            if active and active.status == FTWLocalAgentJobStatus.CLAIMED and active.claim_expires_at and active.claim_expires_at <= datetime.utcnow():
                uncertain = await self.repo.update_ftw_local_agent_job(str(active.id), {
                    "status": FTWLocalAgentJobStatus.ACTION_NEEDED,
                    "result_state": "UNKNOWN_OUTCOME",
                    "result_message": "The agent stopped before confirming this operation. Refresh FTW data to verify its outcome before retrying; it was not repeated.",
                })
                await self.repo.release_ftw_device_job(str(device.id), str(active.id))
                if uncertain:
                    try:
                        await self.continue_after_completion(uncertain)
                    except ValueError:
                        # A deleted filing must not leave this computer locked.
                        pass
            elif active and active.status != FTWLocalAgentJobStatus.CLAIMED:
                # A crash can happen after a job is moved to a terminal/held
                # state but before the device reservation is released. The
                # browser action is still guarded by the job journal; clear
                # only the stale device pointer and keep polling in this call.
                await self.repo.release_ftw_device_job(str(device.id), str(active.id))
            elif not active and device.active_claim_expires_at and device.active_claim_expires_at <= datetime.utcnow():
                # A failed reservation never reached the browser; do not leave
                # the computer permanently stuck on an opaque reservation hash.
                await self.repo.release_ftw_device_job(str(device.id), device.active_job_id)
            else:
                # Never automatically reclaim an in-flight operation that may
                # already have clicked FT Williams.
                return FTWLocalAgentClaimResponse()
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
                now + timedelta(seconds=max(300, self.settings.ftw_local_agent_claim_ttl_seconds)),
                workspace_id=device.workspace_id if self.settings.ftw_local_agent_workspace_routing_enabled else None,
            )
            if not candidate:
                break
            review = await self.repo.get_ftwilliams_review(candidate.filing_id)
            if review and review.bring_forward_required and not review.current_year_exists:
                # A polling device with no eligible assigned work must not take
                # browser ownership away from the device that has the job.
                allowed = await self.repo.acquire_ftw_browser_lease(
                    device.expected_account, str(device.id), now,
                    now + timedelta(seconds=max(300, self.settings.ftw_local_agent_claim_ttl_seconds + 180)),
                )
                if not allowed:
                    await self.repo.update_ftw_local_agent_job(str(candidate.id), {
                        "status": FTWLocalAgentJobStatus.QUEUED,
                        "claim_token_hash": None, "claim_expires_at": None, "device_id": None,
                    })
                    await self.repo.release_ftw_device_job(str(device.id), str(candidate.id))
                    break
                if not await self._authorize_fresh_bring_forward(candidate, review):
                    await self.repo.release_ftw_device_job(str(device.id), str(candidate.id))
                    # One fresh comparison per poll bounds request latency.
                    # Remaining siblings stay queued for subsequent polls.
                    break
                fresh_device = await self._fresh_device(device)
                if fresh_device.pause_requested or fresh_device.revoked_at:
                    await self.repo.update_ftw_local_agent_job(str(candidate.id), {
                        "status": FTWLocalAgentJobStatus.QUEUED,
                        "claim_token_hash": None, "claim_expires_at": None, "device_id": None,
                    })
                    await self.repo.release_ftw_device_job(str(device.id), str(candidate.id))
                    break
                job = await self.repo.mark_ftw_job_dispatched(str(candidate.id), str(device.id),
                    self._hash_token(candidate_token), datetime.utcnow())
                if not job:
                    await self.repo.release_ftw_device_job(str(device.id), str(candidate.id))
                    break
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
            await self.repo.release_ftw_device_job(str(device.id), str(candidate.id))
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
                workspace_id=job.workspace_id,
                mapping_id=job.mapping_id,
                expires_at=job.expires_at,
            )
            if job
            else None
        )
        return FTWLocalAgentClaimResponse(job=payload, claim_token=claim_token if job else None)

    @staticmethod
    def _browser_plan_ids(url):
        matches = re.findall(r"[?&#]plan=([^&#]+)", unquote(str(url or "")), flags=re.IGNORECASE)
        identities = {tuple(part.strip() for part in value.split(",")) for value in matches}
        if len(identities) != 1:
            return None
        pair = next(iter(identities))
        return pair if len(pair) == 2 and all(pair) else None

    async def _hold_preflight(self, job, state, message):
        held = await self.repo.update_ftw_local_agent_job(str(job.id), {
            "status": FTWLocalAgentJobStatus.ACTION_NEEDED, "result_state": state,
            "result_message": message, "claim_token_hash": None,
            "claim_expires_at": None, "completed_at": datetime.utcnow(),
            "preflight_retry_at": datetime.utcnow() + timedelta(seconds=60)
                if state in {"CURRENT_QUERY_REQUIRED", "PRIOR_OPERATION_UNCONFIRMED"} else None,
            "expires_at": max(job.expires_at, datetime.utcnow() + timedelta(seconds=max(60, self.settings.ftw_local_agent_job_ttl_seconds))),
        })
        if held:
            await self.continue_after_completion(held)
        return False

    async def reconcile_bring_forward(self, filing_id: str, resolution: str, reason: str = "") -> dict:
        """Reconcile a stale Bring Forward journal entry before retrying.

        A prior SUBMITTED/UNKNOWN operation must never be retried implicitly. An
        operator can explicitly verify the current FTW state, or declare the
        prior attempt failed after checking FT Williams manually.
        """
        filing = await self.repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        review_service = self.review_service
        if review_service is None:
            from app.services.ftwilliams_review import FTWilliamsReviewService
            review_service = FTWilliamsReviewService()
        review = await review_service.prepare_review(
            filing_id,
            send_queries=True,
            reuse_current_snapshot=False,
        )
        await self.repo.upsert_ftwilliams_review(review)

        if resolution == "VERIFY_CURRENT":
            if not review.current_year_exists or review.bring_forward_required:
                raise ValueError("FT Williams still reports the current-year Schedule A as missing.")
            result_state = "VERIFIED_CURRENT"
            reconciled = []
        elif resolution == "RESET_FAILED":
            if review.current_year_exists and not review.bring_forward_required:
                raise ValueError("The current-year Schedule A already exists; verify it instead of resetting the operation.")
            pair = self._browser_plan_ids(review.ftw_plan_url)
            history = await self.repo.list_ftw_target_operation_history(
                self.settings.ftw_local_agent_expected_account,
                str(review.year or ""),
            )
            jobs = await self.repo.list_ftw_local_agent_jobs_for_filing(filing_id)
            seen = {str(job.id) for job in jobs if job.id}
            # Without a confirmed browser pair, limit recovery to this filing;
            # never reset another client's same-year operation by accident.
            candidates = jobs if not pair else jobs + [job for job in history if str(job.id) not in seen]
            reconciled = []
            for job in candidates:
                if pair and self._browser_plan_ids(job.target_url) != pair:
                    continue
                if job.expected_year != str(review.year or ""):
                    continue
                updated = await self.repo.update_ftw_local_agent_job(str(job.id), {
                    "status": FTWLocalAgentJobStatus.EXPIRED,
                    "result_state": "RECONCILED_FAILED",
                    "result_message": (reason.strip() or "Operator confirmed the previous Bring Forward attempt did not complete."),
                    "operation_dispatched_at": None,
                    "claim_token_hash": None,
                    "claim_expires_at": None,
                    "device_id": None,
                    "completed_at": datetime.utcnow(),
                    "preflight_retry_at": None,
                })
                if updated:
                    reconciled.append(str(updated.id))
            result_state = "RESET_FAILED"
        else:
            raise ValueError("Resolution must be VERIFY_CURRENT or RESET_FAILED.")

        from app.services.ftwilliams_automation import FTWAutomationService
        decision = await FTWAutomationService(
            repo=self.repo,
            review_service=review_service,
            settings=self.settings,
        ).run(filing_id, review=review)
        await self.repo.add_audit(AuditLog(
            filing_id=filing_id,
            event="FTW_BRING_FORWARD_RECONCILED",
            message="Operator reconciled the previous Bring Forward operation before continuing.",
            details={
                "resolution": result_state,
                "reason": reason.strip() or None,
                "reconciled_job_ids": reconciled,
                "automation_status": decision.status.value,
                "automation_next_action": decision.next_action,
            },
        ))
        return {
            "ftw_review": await self.repo.get_ftwilliams_review(filing_id) or review,
            "automation_status": decision.status.value,
            "automation_next_action": decision.next_action,
            "reconciled_job_ids": reconciled,
        }

    async def _authorize_fresh_bring_forward(self, job, review):
        """No cached missing-state can authorize a native plan/year copy.

        Called while owning the account browser lease and the device/job claim.
        A submitted/uncertain same-target journal entry also stops copies during
        vendor propagation delays, across filings, workspaces and API processes.
        """
        from app.services.ftwilliams import FTWilliamsService
        pair = self._browser_plan_ids(job.target_url)
        expected = (str(review.ftw_browser_customer_id or ""), str(review.ftw_browser_plan_id or ""))
        lookup = review.plan_lookup
        if (not pair or pair != expected
                or not review.ftw_customer_id or not review.ftw_plan_id
                or str(review.year) != job.expected_year or not lookup
                or self._canonical_ein(lookup.company_employer_id) != self._canonical_ein(job.expected_ein)
                or self._canonical_plan_number(lookup.plan_number) != self._canonical_plan_number(job.expected_plan_number)):
            return await self._hold_preflight(job, "INVALID_TARGET", "Bring Forward was not run: the saved API/browser plan mapping or identity is incomplete or inconsistent.")
        try:
            query_service = FTWilliamsService()
            try:
                fresh = await query_service.run_query(FTWilliamsQueryRequest(
                    operation="query_schedule_a", ftw_customer_id=review.ftw_customer_id,
                    ftw_plan_id=review.ftw_plan_id, year=job.expected_year, send=True))
            finally:
                # This preflight owns its client; do not leak a pool per poll or
                # close another review request's shared service/client.
                await query_service._close_client()
            codes = [str(s.error_code or "") for s in fresh.statuses]
            ready = fresh.configured and fresh.sent and fresh.http_status == 200 and bool(codes) and not fresh.error
            exists = ready and fresh.success and all(code == "0" for code in codes) and all(s.query_results for s in fresh.statuses)
            missing = ready and all(code == "59" for code in codes)
            if not exists and not missing:
                return await self._hold_preflight(job, "CURRENT_QUERY_REQUIRED", "Bring Forward was not run: a fresh FTW query did not conclusively confirm that the current-year Schedule A is missing.")
            if exists:
                from app.services.ftwilliams_review import FTWilliamsReviewService
                from app.services.ftwilliams_automation import FTWAutomationService
                review_service = self.review_service or FTWilliamsReviewService()
                refreshed = await review_service.prepare_review(job.filing_id, send_queries=True, reuse_current_snapshot=False)
                if (not refreshed.current_query_success or not refreshed.current_query_complete
                        or not refreshed.current_year_exists or refreshed.bring_forward_required
                        # prepare_review clears its action URL when no copy is
                        # needed. Validate the exact identity, not a saved mapping flag.
                        or (refreshed.ftw_browser_customer_id, refreshed.ftw_browser_plan_id) != pair
                        or str(refreshed.year) != str(job.expected_year)
                        or (refreshed.ftw_customer_id, refreshed.ftw_plan_id) != (review.ftw_customer_id, review.ftw_plan_id)):
                    return await self._hold_preflight(job, "CURRENT_QUERY_REQUIRED", "The current-year Schedule A exists, but full comparison or plan identity could not be confirmed. No copy was attempted.")
                await self.repo.upsert_ftwilliams_review(refreshed)
                await self.repo.update_ftw_local_agent_job(str(job.id), {
                    "status": FTWLocalAgentJobStatus.EXPIRED, "result_state": "NO_LONGER_REQUIRED",
                    "result_message": "A fresh FTW query confirmed current-year Schedule A records; comparison refreshed without repeating Bring Forward.",
                    "claim_token_hash": None, "claim_expires_at": None, "completed_at": datetime.utcnow(),
                })
                await self.repo.add_audit(AuditLog(filing_id=job.filing_id,
                    event="FTW_LOCAL_AGENT_COPY_SKIPPED_CURRENT_YEAR_EXISTS",
                    message="Current-year records were freshly confirmed; no native copy was attempted.", details={"job_id": str(job.id)}))
                await FTWAutomationService(repo=self.repo, review_service=review_service, settings=self.settings).run(job.filing_id, review=refreshed)
                return False
            history = await self.repo.list_ftw_target_operation_history(job.expected_account, job.expected_year)
            history_filing_ids = {
                str(getattr(previous, "filing_id", "") or "")
                for previous in history
                if getattr(previous, "filing_id", None) and previous.id != job.id
            }
            history_filings = (
                await self.repo.get_filings_by_ids(history_filing_ids)
                if history_filing_ids
                else []
            )
            deleted_filing_ids = {
                str(previous_filing.id)
                for previous_filing in history_filings
                if previous_filing.status == FilingStatus.DELETED
            }
            if any(
                self._browser_plan_ids(previous.target_url) == pair
                and str(getattr(previous, "filing_id", "") or "") not in deleted_filing_ids
                and (
                    previous.id != job.id
                    or previous.operation_dispatched_at
                    or previous.result_state in {"SUBMITTED", "UNKNOWN_OUTCOME"}
                )
                for previous in history
            ):
                return await self._hold_preflight(job, "PRIOR_OPERATION_UNCONFIRMED", "This plan/year already has a submitted, verified or uncertain Bring Forward operation. Fresh FTW data still reports it missing; no repeat copy was attempted.")
            return True
        except Exception:
            # Do not expose query credentials or RequestXML in diagnostics.
            return await self._hold_preflight(job, "CURRENT_QUERY_REQUIRED", "Bring Forward was not run: its fresh current-year query or comparison failed. Pending work needs a successful fresh check.")

    async def complete(
        self,
        device: FTWLocalAgentDevice,
        job_id: str,
        payload: FTWLocalAgentCompleteRequest,
    ) -> FTWLocalAgentJob:
        existing = await self.repo.get_ftw_local_agent_job(job_id)
        if (existing and existing.device_id == str(device.id)
                and existing.claim_token_hash == self._hash_token(payload.claim_token)
                and existing.completed_at and existing.result_state == payload.state):
            return existing
        state = payload.state.upper()
        status = (
            FTWLocalAgentJobStatus.SUBMITTED
            if state == "SUBMITTED"
            else FTWLocalAgentJobStatus.ACTION_NEEDED
            if state in {"LOGIN_REQUIRED", "INVALID_TARGET", "PAGE_LAYOUT_CHANGED", "UNKNOWN_OUTCOME"}
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
                **({"operation_dispatched_at": None} if state in {"LOGIN_REQUIRED", "INVALID_TARGET", "PAGE_LAYOUT_CHANGED"} else {}),
            },
        )
        if not completed:
            raise ValueError("The local-agent job claim is invalid, expired, or already completed.")
        await self.repo.release_ftw_device_job(str(device.id), job_id)
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
            next_action = "LOGIN_TO_FTW" if job.result_state == "LOGIN_REQUIRED" else "RETRY_AFTER_CURRENT_QUERY" if job.result_state in {"UNKNOWN_OUTCOME", "SUBMITTED", "CURRENT_QUERY_REQUIRED", "PRIOR_OPERATION_UNCONFIRMED"} else "RETRY"
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

        if not await self.repo.begin_ftw_job_verification(str(job.id)):
            # Retrying a lost HTTP completion response must not launch the
            # downstream automation/verification twice.
            return await self.repo.get_ftw_local_agent_job(str(job.id)) or job

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
        refreshed_review = None
        after_record_ids: list[str] = []
        new_record_ids: list[str] = []
        verification_error: Exception | None = None
        verified = False
        for attempt in range(_POST_BRING_FORWARD_VERIFY_ATTEMPTS):
            try:
                refreshed_review = await review_service.prepare_review(
                    job.filing_id,
                    send_queries=True,
                    reuse_current_snapshot=False,
                )
                await self.repo.upsert_ftwilliams_review(refreshed_review)
                after_record_ids = FTWAutomationService._schedule_a_record_ids(refreshed_review)
                new_record_ids = sorted(set(after_record_ids) - set(job.before_record_ids))
                verified = bool(
                    refreshed_review.current_year_exists
                    and not refreshed_review.bring_forward_required
                    and new_record_ids
                )
                verification_error = None
            except Exception as exc:
                verification_error = exc
                verified = False

            if verified:
                break
            if attempt + 1 < _POST_BRING_FORWARD_VERIFY_ATTEMPTS:
                await asyncio.sleep(_POST_BRING_FORWARD_VERIFY_DELAY_SECONDS)

        if verification_error is not None and refreshed_review is None:
            return await self._stop_after_submission(
                job,
                f"Bring Forward was submitted, but the ftwLink verification query failed after {_POST_BRING_FORWARD_VERIFY_ATTEMPTS} attempts: {verification_error}",
            )
        if not verified:
            return await self._stop_after_submission(
                job,
                f"Bring Forward was submitted, but ftwLink did not confirm a new current-year Schedule A record after {_POST_BRING_FORWARD_VERIFY_ATTEMPTS} checks.",
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
