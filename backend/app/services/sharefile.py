import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks

from app.config import get_settings
from app.models import AuditLog, DocumentType, ExtractionJob, Filing, FilingStatus, ShareFileOAuthToken, ShareFileStatus
from app.repositories import get_repository
from app.services.extractor import extract_docx_text, extract_pdf_text_pages
from app.services.intake_formats import is_supported_intake_file, normalize_intake_documents
from app.services.filing_pipeline import process_extraction_batch
from app.services.storage import StorageService


MAX_SHAREFILE_SCAN_DEPTH = 8
# Files larger than this are never downloaded for content classification.
# Real Schedule A documents are well under 1 MB; multi-MB files are carrier
# booklets, scans, or templates that would make the scan take minutes each.
MAX_CONTENT_SNIFF_BYTES = 10 * 1024 * 1024
SHAREFILE_INCREMENTAL_STATE_KEY = "sharefile_incremental_scan"
# A deep scan walks every folder of every client - on a large ShareFile
# account that is thousands of folder listings and takes the best part of an
# hour. Running it every few minutes keeps the account permanently busy and
# still leaves a brand-new client folder unnoticed for up to a full sweep.
#
# The quick scan instead lists only the top folder levels
# (client > 5500 Filing > year folder). That is a couple of hundred listings,
# it finishes in seconds, and it answers the only question a frequent poll
# needs to answer: did a folder appear that we have never scanned? Anything
# new is then deep-scanned on its own, and the full deep sweep runs on a
# slower cadence as the safety net.
MAX_KNOWN_FOLDER_IDS = 20000
SCAN_MODE_AUTO = "auto"
SCAN_MODE_QUICK = "quick"
SCAN_MODE_DEEP = "deep"
SUPPORTED_SHAREFILE_DOCUMENT_TYPES = {DocumentType.PLAN_WORKSHEET, DocumentType.SCHEDULE_A}


class ShareFileService:
    def authorization_url(self) -> dict:
        settings = get_settings()
        missing = [
            name
            for name, value in {
                "SHAREFILE_CLIENT_ID": settings.sharefile_client_id,
                "SHAREFILE_REDIRECT_URL": settings.sharefile_redirect_url,
            }.items()
            if not value
        ]
        if missing:
            return {
                "configured": False,
                "message": "ShareFile OAuth settings are incomplete.",
                "missing": missing,
            }
        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.sharefile_client_id,
                "redirect_uri": settings.sharefile_redirect_url,
            }
        )
        return {
            "configured": True,
            "authorization_url": f"https://secure.sharefile.com/oauth/authorize?{query}",
            "redirect_uri": settings.sharefile_redirect_url,
        }

    async def status(self) -> ShareFileStatus:
        settings = get_settings()
        configured_folder_ids = self._configured_folder_ids()
        configured = bool(
            settings.sharefile_subdomain
            and settings.sharefile_client_id
            and settings.sharefile_client_secret
            and self._scan_roots_configured()
        )
        if not configured:
            return ShareFileStatus(
                configured=False,
                message="ShareFile is not fully configured. Live intake sync is disabled.",
                subdomain=settings.sharefile_subdomain,
                intake_folder_id=settings.sharefile_intake_folder_id,
                configured_folder_ids=configured_folder_ids,
                discover_shared_folders=settings.sharefile_discover_shared_folders,
                shared_root_folder_id=settings.sharefile_shared_root_folder_id,
                scan_scope=self._scan_scope_label(),
            )
        token = await get_repository().get_sharefile_token()
        if token:
            return ShareFileStatus(
                configured=True,
                connected=True,
                message="ShareFile is connected. Sync can discover shared client folders, classify filing documents, and queue extraction.",
                subdomain=settings.sharefile_subdomain,
                intake_folder_id=settings.sharefile_intake_folder_id,
                configured_folder_ids=configured_folder_ids,
                discover_shared_folders=settings.sharefile_discover_shared_folders,
                shared_root_folder_id=settings.sharefile_shared_root_folder_id,
                scan_scope=self._scan_scope_label(),
            )
        return ShareFileStatus(
            configured=True,
            connected=False,
            message="ShareFile settings are present. Complete OAuth before live sync.",
            subdomain=settings.sharefile_subdomain,
            intake_folder_id=settings.sharefile_intake_folder_id,
            configured_folder_ids=configured_folder_ids,
            discover_shared_folders=settings.sharefile_discover_shared_folders,
            shared_root_folder_id=settings.sharefile_shared_root_folder_id,
            scan_scope=self._scan_scope_label(),
        )

    async def poll_folder(self, background_tasks: BackgroundTasks | None = None) -> dict:
        # Scheduled polls decide for themselves: a quick top-level check most
        # of the time, a full deep sweep when one is due.
        return await self.sync_changes(background_tasks, process_new_files=True, scan_mode=SCAN_MODE_AUTO)

    async def list_webhooks(self) -> dict:
        status = await self.status()
        if not status.configured:
            return {"configured": False, "connected": False, "subscriptions": [], "message": status.message}

        repo = get_repository()
        token = await repo.get_sharefile_token()
        if not token:
            return {
                "configured": True,
                "connected": False,
                "subscriptions": [],
                "message": "ShareFile OAuth is not connected yet.",
            }

        async with httpx.AsyncClient(timeout=30) as client:
            token = await self._ensure_access_token(client, token)
            subscriptions = await self._list_webhook_subscriptions(client, token)
        return {
            "configured": True,
            "connected": True,
            "webhook_url": self._webhook_callback_url(),
            "subscriptions": subscriptions,
        }

    async def register_webhooks(self) -> dict:
        status = await self.status()
        if not status.configured:
            return {"registered": 0, "message": status.message}

        repo = get_repository()
        token = await repo.get_sharefile_token()
        if not token:
            return {"registered": 0, "connected": False, "message": "ShareFile OAuth is not connected yet."}

        webhook_url = self._webhook_callback_url()
        async with httpx.AsyncClient(timeout=90) as client:
            token = await self._ensure_access_token(client, token)
            scan_roots = await self._resolve_scan_roots(client, token)
            webhook_roots = await self._resolve_webhook_subscription_roots(client, token, scan_roots)
            existing = await self._list_webhook_subscriptions(client, token)
            registered = []
            skipped = []
            failed = []
            for root in webhook_roots:
                if self._has_matching_webhook(existing, webhook_url, root["id"]):
                    skipped.append({"folder_id": root["id"], "path": " > ".join(root["path_parts"]) or root["name"], "reason": "already registered"})
                    continue
                try:
                    subscription = await self._create_webhook_subscription(client, token, webhook_url, root["id"])
                    registered.append(
                        {
                            "folder_id": root["id"],
                            "path": " > ".join(root["path_parts"]) or root["name"],
                            "subscription": subscription,
                        }
                    )
                except httpx.HTTPStatusError as exc:
                    failed.append(
                        {
                            "folder_id": root["id"],
                            "path": " > ".join(root["path_parts"]) or root["name"],
                            "status_code": exc.response.status_code,
                            "response": exc.response.text[:500],
                        }
                    )

        await repo.add_audit(
            AuditLog(
                event="SHAREFILE_WEBHOOK_REGISTERED",
                message="ShareFile webhook registration attempted for configured scan roots.",
                details={
                    "webhook_url": webhook_url,
                    "registered": len(registered),
                    "skipped": len(skipped),
                    "failed": len(failed),
                },
            )
        )
        return {
            "webhook_url": webhook_url,
            "scan_roots": len(scan_roots),
            "webhook_roots": len(webhook_roots),
            "registered": len(registered),
            "skipped": len(skipped),
            "failed": len(failed),
            "registered_roots": registered,
            "skipped_roots": skipped,
            "failed_roots": failed,
            "message": "ShareFile webhook registration completed. Upload/update should now POST to the ERISAPros webhook URL for registered roots.",
        }

    async def auto_register_relevant_webhooks(self) -> dict:
        status = await self.status()
        if not status.configured:
            return {"registered": 0, "message": status.message}

        repo = get_repository()
        token = await repo.get_sharefile_token()
        if not token:
            return {"registered": 0, "connected": False, "message": "ShareFile OAuth is not connected yet."}

        webhook_url = self._webhook_callback_url()
        async with httpx.AsyncClient(timeout=90) as client:
            token = await self._ensure_access_token(client, token)
            scan_roots = await self._resolve_scan_roots(client, token)
            webhook_roots = await self._discover_relevant_webhook_roots(client, token, scan_roots)
            existing = await self._list_webhook_subscriptions(client, token)
            registered, skipped, failed = await self._register_missing_webhook_roots(
                client,
                token,
                webhook_url,
                webhook_roots,
                existing,
            )

        await repo.add_audit(
            AuditLog(
                event="SHAREFILE_WEBHOOK_AUTO_REGISTERED",
                message="ShareFile relevant filing-folder webhook auto-registration completed.",
                details={
                    "webhook_url": webhook_url,
                    "scan_roots": len(scan_roots),
                    "webhook_roots": len(webhook_roots),
                    "registered": len(registered),
                    "skipped": len(skipped),
                    "failed": len(failed),
                },
            )
        )
        return {
            "webhook_url": webhook_url,
            "scan_roots": len(scan_roots),
            "webhook_roots": len(webhook_roots),
            "registered": len(registered),
            "skipped": len(skipped),
            "failed": len(failed),
            "registered_roots": registered,
            "skipped_roots": skipped,
            "failed_roots": failed,
            "message": "Relevant ShareFile filing-folder webhook registration completed.",
        }

    async def handle_webhook(self, payload: dict, background_tasks: BackgroundTasks | None = None) -> dict:
        event_type = self._webhook_event_type(payload)
        item_id = self._webhook_item_id(payload)
        if not item_id:
            return {
                "accepted": True,
                "queued": 0,
                "message": "ShareFile webhook received, but no item ID was present. Backup polling will catch the change.",
                "event": event_type,
            }

        status = await self.status()
        if not status.configured:
            return {"accepted": False, "queued": 0, "message": status.message, "event": event_type, "item_id": item_id}

        repo = get_repository()
        token = await repo.get_sharefile_token()
        if not token:
            return {
                "accepted": False,
                "queued": 0,
                "message": "ShareFile webhook received, but OAuth is not connected yet.",
                "event": event_type,
                "item_id": item_id,
            }

        if self._is_delete_event(event_type):
            marked = await self._mark_deleted_sharefile_item(item_id, "ShareFile webhook reported this source item was deleted.")
            return {
                "accepted": True,
                "queued": 0,
                "deleted": 1 if marked else 0,
                "message": "ShareFile delete webhook processed. Existing filing records were marked deleted where applicable.",
                "event": event_type,
                "item_id": item_id,
            }

        async with httpx.AsyncClient(timeout=90) as client:
            token = await self._ensure_access_token(client, token)
            try:
                item = await self._get_item(client, token, item_id)
            except httpx.HTTPStatusError as exc:
                return {
                    "accepted": False,
                    "queued": 0,
                    "message": "ShareFile webhook item could not be fetched.",
                    "event": event_type,
                    "item_id": item_id,
                    "status_code": exc.response.status_code,
                    "response": exc.response.text[:300],
                }

            if self._is_folder(item):
                name = item.get("Name") or item_id
                scanned_files = await self._scan_folder(
                    client,
                    token,
                    item_id,
                    [name],
                    root_folder_id=item_id,
                )
                return await self._process_changed_sharefile_files(
                    client,
                    token,
                    self._dedupe_scanned_files(scanned_files),
                    background_tasks,
                    first_scan=False,
                    process_new_files=True,
                    source="SHAREFILE_WEBHOOK_FOLDER",
                    event=event_type,
                )

            file_item = await self._normalize_sharefile_item(client, token, item)
            if file_item["document_type"] not in {DocumentType.PLAN_WORKSHEET, DocumentType.SCHEDULE_A}:
                await repo.upsert_sharefile_file(
                    file_item["id"],
                    self._sharefile_index_record(file_item, status="IGNORED"),
                )
                return {
                    "accepted": True,
                    "queued": 0,
                    "message": "ShareFile webhook item was not a Schedule A PDF or 5500 Plan Worksheet, so it was ignored.",
                    "event": event_type,
                    "item_id": item_id,
                    "document_type": file_item["document_type"].value if file_item["document_type"] else None,
                }

            return await self._process_changed_sharefile_files(
                client,
                token,
                [file_item],
                background_tasks,
                first_scan=False,
                process_new_files=True,
                source="SHAREFILE_WEBHOOK_FILE",
                event=event_type,
            )

    async def sync_changes(
        self,
        background_tasks: BackgroundTasks | None = None,
        process_new_files: bool = True,
        scan_mode: str = SCAN_MODE_AUTO,
    ) -> dict:
        status = await self.status()
        if not status.configured:
            return {"synced": 0, "message": status.message}

        repo = get_repository()
        token = await repo.get_sharefile_token()
        if not token:
            return {
                "synced": 0,
                "skipped": 0,
                "connected": False,
                "message": "ShareFile OAuth is not connected yet. Complete OAuth first, then run change polling.",
            }

        settings = get_settings()
        scan_started_at = datetime.utcnow()
        await repo.upsert_sharefile_state(
            SHAREFILE_INCREMENTAL_STATE_KEY,
            {"last_scan_started_at": scan_started_at},
        )
        async with httpx.AsyncClient(timeout=90) as client:
            token = await self._ensure_access_token(client, token)
            scan_roots = await self._resolve_scan_roots(client, token)
            if not scan_roots:
                return {
                    "connected": True,
                    "folder_access": False,
                    "found": 0,
                    "useful": 0,
                    "supported": 0,
                    "packages": 0,
                    "synced": 0,
                    "skipped": 0,
                    "failed": 0,
                    "message": "ShareFile connected, but no shared scan roots were found.",
                    "scan_scope": self._scan_scope_label(),
                }

            scanned_files: list[dict] = []
            scan_errors: list[dict] = []

            async def scan_one_root(root: dict) -> list[dict]:
                try:
                    return await self._scan_folder(
                        client,
                        token,
                        root["id"],
                        root["path_parts"],
                        root_folder_id=root["id"],
                        scan_errors=scan_errors,
                    )
                except Exception as exc:
                    # Belt and braces: a failure inside one client's folder tree
                    # must never abort the scan of the remaining clients.
                    scan_errors.append(
                        {
                            "folder_id": root["id"],
                            "path": " > ".join(root["path_parts"]) or root["name"],
                            "status_code": None,
                            "response": f"{type(exc).__name__}: {exc}"[:300],
                        }
                    )
                    return []

            state = await repo.get_sharefile_state(SHAREFILE_INCREMENTAL_STATE_KEY) or {}
            first_scan = not bool(state.get("baseline_completed"))
            known_folder_ids = set(state.get("known_folder_ids") or [])

            # Cheap pass on every scan: walk the filing structure only. It
            # tells us what documents are there now and whether any folder
            # appeared that has never been scanned.
            quick_files, folder_index = await self._quick_scan(client, token, scan_roots, scan_errors)
            scanned_files.extend(quick_files)
            new_folder_ids = [folder_id for folder_id in folder_index if folder_id not in known_folder_ids]

            deep = self._deep_scan_due(scan_mode, state, first_scan, known_folder_ids)
            if deep:
                scan_targets = list(scan_roots)
            else:
                # Only walk the subtrees that are actually new. Everything
                # else was scanned before and is kept current by webhooks.
                scan_targets = self._new_scan_targets(folder_index, new_folder_ids)

            # Client folder trees are independent - scan them concurrently.
            # The shared semaphore inside _scan_folder bounds ShareFile load.
            if scan_targets:
                for root_files in await asyncio.gather(*(scan_one_root(root) for root in scan_targets)):
                    scanned_files.extend(root_files)

            result = await self._process_changed_sharefile_files(
                client,
                token,
                self._dedupe_scanned_files(scanned_files),
                background_tasks,
                first_scan=first_scan,
                process_new_files=process_new_files,
                source="SHAREFILE_INCREMENTAL_POLL" if deep else "SHAREFILE_QUICK_POLL",
                scan_errors=scan_errors,
                # A quick scan only looked at part of the account, so it must
                # never conclude that the folders it did not visit are gone.
                partial_scan=not deep,
            )
            scan_finished_at = datetime.utcnow()
            scan_error_list = list(result.get("scan_errors") or [])
            state_update = {
                "last_scan_at": scan_finished_at,
                "last_scan_completed_at": scan_finished_at,
                "last_scan_duration_seconds": round((scan_finished_at - scan_started_at).total_seconds(), 1),
                "last_scan_error_count": len(scan_error_list),
                "last_scan_errors": scan_error_list[:5],
                "baseline_completed": True,
                "baseline_completed_at": state.get("baseline_completed_at") or datetime.utcnow(),
                "last_scan_found": result.get("found", 0),
                "last_scan_supported": result.get("supported", 0),
                "last_scan_synced": result.get("synced", 0),
                "last_scan_deleted": result.get("deleted", 0),
                "last_scan_mode": SCAN_MODE_DEEP if deep else SCAN_MODE_QUICK,
                "last_scan_new_folders": len(new_folder_ids),
                "known_folder_ids": self._merged_known_folder_ids(known_folder_ids, folder_index),
                "scan_scope": self._scan_scope_label(),
            }
            if deep:
                state_update["last_deep_scan_at"] = scan_finished_at
                state_update["last_deep_scan_duration_seconds"] = state_update["last_scan_duration_seconds"]
            else:
                state_update["last_quick_scan_at"] = scan_finished_at
            await repo.upsert_sharefile_state(SHAREFILE_INCREMENTAL_STATE_KEY, state_update)

        result.update(
            {
                "connected": True,
                "folder_access": True,
                "folder_id": settings.sharefile_intake_folder_id,
                "folder_ids": [root["id"] for root in scan_roots],
                "scan_mode": SCAN_MODE_DEEP if deep else SCAN_MODE_QUICK,
                "new_folders": len(new_folder_ids),
                "scanned_targets": len(scan_targets),
                "scan_scope": self._scan_scope_label(),
                "scan_roots": [
                    {
                        "id": root["id"],
                        "name": root["name"],
                        "source": root["source"],
                        "path": " > ".join(root["path_parts"]) or root["name"],
                    }
                    for root in scan_roots
                ],
            }
        )
        return result

    def _deep_scan_due(
        self,
        scan_mode: str,
        state: dict,
        first_scan: bool,
        known_folder_ids: set[str],
    ) -> bool:
        """A deep sweep is the safety net, not the every-poll default."""
        if scan_mode == SCAN_MODE_DEEP:
            return True
        if scan_mode == SCAN_MODE_QUICK:
            return False
        if first_scan or not known_folder_ids:
            # Nothing has ever been walked, so there is no "known" set to
            # compare against - the quick scan would be meaningless.
            return True
        last_deep = self._as_datetime(state.get("last_deep_scan_at"))
        if last_deep is None:
            return True
        interval_hours = max(1, get_settings().sharefile_deep_scan_interval_hours)
        return datetime.utcnow() - last_deep >= timedelta(hours=interval_hours)

    def _as_datetime(self, value) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None
        return None

    async def _quick_scan(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        scan_roots: list[dict],
        scan_errors: list[dict] | None = None,
    ) -> tuple[list[dict], dict[str, dict]]:
        """The cheap half of the two-speed scan.

        It visits the top folder levels of every client plus the filing
        structure itself (5500 Filing > year folder > Schedule A's) and skips
        everything else a client folder happens to contain. That is a few
        hundred listings instead of thousands, so it can run on every poll
        while still seeing new folders and new documents where they matter.

        Returns the files it saw and an index of every folder it saw.
        """
        folder_sink: list[dict] = []
        files: list[dict] = []

        async def quick_scan_root(root: dict) -> list[dict]:
            try:
                return await self._scan_folder(
                    client,
                    token,
                    root["id"],
                    root["path_parts"],
                    root_folder_id=root["id"],
                    scan_errors=scan_errors,
                    descend=self._quick_scan_descend,
                    folder_sink=folder_sink,
                )
            except Exception as exc:
                if scan_errors is not None:
                    scan_errors.append(
                        {
                            "folder_id": root["id"],
                            "path": " > ".join(root["path_parts"]) or root["name"],
                            "status_code": None,
                            "response": f"{type(exc).__name__}: {exc}"[:300],
                        }
                    )
                return []

        for root_files in await asyncio.gather(*(quick_scan_root(root) for root in scan_roots)):
            files.extend(root_files)

        index: dict[str, dict] = {}
        for root in scan_roots:
            root_path = list(root.get("path_parts") or [])
            if not root_path and root.get("name"):
                root_path = [root["name"]]
            index[root["id"]] = {
                "id": root["id"],
                "name": root.get("name") or root["id"],
                "source": root.get("source") or "ShareFile scan root",
                "path_parts": root_path,
                "parent_id": None,
            }
        for folder in folder_sink:
            index.setdefault(folder["id"], folder)
        return files, index

    def _quick_scan_descend(self, name: str, path_parts: list[str], depth: int) -> bool:
        """Should the quick scan walk into this subfolder?

        Yes for the first couple of levels (that is how a brand-new client
        folder or a new filing year is spotted), and yes for the filing
        structure at any depth. No for everything else - the payroll,
        correspondence and archive folders under a client are exactly what
        makes the deep sweep take an hour.
        """
        if depth > MAX_SHAREFILE_SCAN_DEPTH:
            return False
        if depth <= max(1, get_settings().sharefile_quick_scan_depth):
            return True
        return (
            self._is_5500_filing_folder_segment(name)
            or self._is_year_filing_segment(name)
            or self._is_schedule_a_folder_segment(name)
        )

    def _new_scan_targets(self, folder_index: dict[str, dict], new_folder_ids: list[str]) -> list[dict]:
        """Reduce the new folders to the outermost ones.

        If a whole new client folder appeared, its child folders are new too -
        scanning the client folder already covers them, so only the topmost
        new folder of each branch is worth walking.
        """
        new_ids = set(new_folder_ids)
        targets: list[dict] = []
        for folder_id in new_folder_ids:
            node = folder_index.get(folder_id)
            if not node:
                continue
            parent_id = node.get("parent_id")
            has_new_ancestor = False
            seen: set[str] = set()
            while parent_id and parent_id not in seen:
                seen.add(parent_id)
                if parent_id in new_ids:
                    has_new_ancestor = True
                    break
                parent = folder_index.get(parent_id)
                parent_id = parent.get("parent_id") if parent else None
            if not has_new_ancestor:
                targets.append(node)
        return targets

    def _merged_known_folder_ids(self, known_folder_ids: set[str], folder_index: dict[str, dict]) -> list[str]:
        """Remember every folder we have seen.

        Union rather than replace: if a listing fails on one poll, the folders
        under it must not silently become "new" again and trigger a redundant
        deep scan on the next poll.
        """
        merged = set(known_folder_ids) | set(folder_index)
        if len(merged) > MAX_KNOWN_FOLDER_IDS:
            # Keep the folders we can still see; drop the oldest remembered
            # ids that no longer exist in ShareFile.
            keep = set(folder_index)
            remaining = MAX_KNOWN_FOLDER_IDS - len(keep)
            if remaining > 0:
                keep |= set(sorted(merged - keep)[:remaining])
            merged = keep
        return sorted(merged)

    async def scan_status(self) -> dict:
        """Visibility into the background scan so nobody has to guess whether
        scans are running, finishing, or failing."""
        state = await get_repository().get_sharefile_state(SHAREFILE_INCREMENTAL_STATE_KEY) or {}
        started = state.get("last_scan_started_at")
        completed = state.get("last_scan_completed_at") or state.get("last_scan_at")
        running = bool(started and (not completed or completed < started))
        return {
            "scan_running": running,
            "last_scan_started_at": started,
            "last_scan_completed_at": completed,
            "last_scan_duration_seconds": state.get("last_scan_duration_seconds"),
            "last_scan_found": state.get("last_scan_found"),
            "last_scan_supported": state.get("last_scan_supported"),
            "last_scan_synced": state.get("last_scan_synced"),
            "last_scan_error_count": state.get("last_scan_error_count"),
            "last_scan_errors": state.get("last_scan_errors") or [],
            "baseline_completed": state.get("baseline_completed", False),
            "scan_scope": state.get("scan_scope"),
            "sniff_total": state.get("sniff_total"),
            "sniff_done": state.get("sniff_done"),
            "last_scan_mode": state.get("last_scan_mode"),
            "last_scan_new_folders": state.get("last_scan_new_folders"),
            "last_deep_scan_at": state.get("last_deep_scan_at"),
            "last_deep_scan_duration_seconds": state.get("last_deep_scan_duration_seconds"),
            "last_quick_scan_at": state.get("last_quick_scan_at"),
            "known_folder_count": len(state.get("known_folder_ids") or []),
            "deep_scan_interval_hours": get_settings().sharefile_deep_scan_interval_hours,
        }

    async def sync_folder(self, background_tasks: BackgroundTasks | None = None) -> dict:
        # The manual "Sync ShareFile" button means "look at everything now".
        return await self.sync_changes(background_tasks, process_new_files=True, scan_mode=SCAN_MODE_DEEP)

    async def complete_oauth(
        self,
        code: str,
        subdomain: str | None,
        apicp: str | None,
        appcp: str | None,
    ) -> dict:
        settings = get_settings()
        missing = [
            name
            for name, value in {
                "SHAREFILE_CLIENT_ID": settings.sharefile_client_id,
                "SHAREFILE_CLIENT_SECRET": settings.sharefile_client_secret,
                "SHAREFILE_REDIRECT_URL": settings.sharefile_redirect_url,
            }.items()
            if not value
        ]
        if missing:
            return {
                "connected": False,
                "message": "ShareFile OAuth settings are incomplete.",
                "missing": missing,
            }
        if not self._scan_roots_configured():
            return {
                "connected": False,
                "message": "ShareFile OAuth settings are present, but no scan scope is configured.",
                "missing": [
                    "Enable SHAREFILE_DISCOVER_SHARED_FOLDERS or configure SHAREFILE_SHARED_ROOT_FOLDER_ID / SHAREFILE_INTAKE_FOLDER_ID.",
                ],
            }

        resolved_subdomain = subdomain or settings.sharefile_subdomain or "erisapros"
        resolved_appcp = appcp or "sharefile.com"
        resolved_apicp = apicp or "sf-api.com"
        token_url = f"https://{resolved_subdomain}.{resolved_appcp}/oauth/token"

        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.sharefile_client_id,
                    "client_secret": settings.sharefile_client_secret,
                    "redirect_uri": settings.sharefile_redirect_url,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.status_code >= 400:
                return {
                    "connected": False,
                    "message": "ShareFile accepted the redirect but token exchange failed.",
                    "status_code": token_response.status_code,
                    "response": token_response.text[:500],
                }

            token_payload = token_response.json()
            token = self._token_from_payload(token_payload, resolved_subdomain, resolved_apicp, resolved_appcp)
            if not token.access_token:
                return {
                    "connected": False,
                    "message": "ShareFile token response did not include an access token.",
                }
            await get_repository().upsert_sharefile_token(token)

            try:
                scan_roots = await self._resolve_scan_roots(client, token)
            except httpx.HTTPStatusError as exc:
                return {
                    "connected": True,
                    "folder_access": False,
                    "message": "OAuth completed, but the token could not list the configured ShareFile scan roots.",
                    "status_code": exc.response.status_code,
                    "response": exc.response.text[:500],
                    "scan_scope": self._scan_scope_label(),
                }
            if not scan_roots:
                return {
                    "connected": True,
                    "folder_access": False,
                    "message": "OAuth completed, but no shared client folders were found for this token.",
                    "scan_scope": self._scan_scope_label(),
                }

            return {
                "connected": True,
                "folder_access": True,
                "message": "ShareFile OAuth is connected and the configured scan roots can be listed.",
                "subdomain": resolved_subdomain,
                "folder_id": settings.sharefile_intake_folder_id,
                "folder_ids": [root["id"] for root in scan_roots],
                "scan_scope": self._scan_scope_label(),
                "root_count": len(scan_roots),
                "roots": [
                    {
                        "id": root["id"],
                        "name": root["name"],
                        "source": root["source"],
                        "path": " > ".join(root["path_parts"]) or root["name"],
                    }
                    for root in scan_roots[:20]
                ],
                "next_step": "POST /api/sharefile/sync-folder to run the baseline metadata scan. Later syncs only download new or changed useful files.",
            }

    async def _process_changed_sharefile_files(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        scanned_files: list[dict],
        background_tasks: BackgroundTasks | None,
        first_scan: bool,
        process_new_files: bool,
        source: str,
        event: str | None = None,
        scan_errors: list[dict] | None = None,
        partial_scan: bool = False,
    ) -> dict:
        repo = get_repository()
        scanned_files = self._dedupe_scanned_files(scanned_files)
        scanned_files = await self._resolve_deferred_content_sniffs(client, token, scanned_files)
        useful_files = [item for item in scanned_files if item.get("document_type")]
        extractable_files = [
            item
            for item in useful_files
            if item.get("document_type") in SUPPORTED_SHAREFILE_DOCUMENT_TYPES
        ]
        active_sharefile_item_ids = {item["id"] for item in extractable_files if item.get("id")}

        await repo.add_audit(
            AuditLog(
                event="SHAREFILE_SCAN_STARTED",
                message="ShareFile scan started.",
                details={
                    "source": source,
                    "event": event,
                    "first_scan": first_scan,
                    "process_new_files": process_new_files,
                    "found": len(scanned_files),
                },
            )
        )

        if first_scan:
            await self._index_scanned_files(scanned_files, baseline=True)
            await repo.add_audit(
                AuditLog(
                    event="SHAREFILE_BASELINE_COMPLETED",
                    message="ShareFile baseline scan indexed metadata only. No files were downloaded or extracted.",
                    details={
                        "source": source,
                        "found": len(scanned_files),
                        "useful": len(useful_files),
                        "supported": len(extractable_files),
                    },
                )
            )
            return {
                "baseline": True,
                "found": len(scanned_files),
                "useful": len(useful_files),
                "supported": len(extractable_files),
                "packages": 0,
                "synced": 0,
                "skipped": len(extractable_files),
                "failed": 0,
                "deleted": 0,
                "files": [],
                "skipped_files": [
                    self._sharefile_file_summary(item, "INDEXED")
                    for item in extractable_files
                ],
                "failed_files": [],
                "scan_errors": scan_errors or [],
                "message": "ShareFile baseline scan completed. Existing files were indexed only; extraction will start on new or changed files.",
            }

        changed_files: list[dict] = []
        skipped_files: list[dict] = []
        ignored_files: list[dict] = []
        new_count = 0
        updated_count = 0
        unchanged_count = 0

        scanned_item_ids = {str(item["id"]) for item in scanned_files if item.get("id")}
        existing_records = await repo.list_sharefile_files_by_item_ids(scanned_item_ids)
        existing_by_item_id = {
            str(record.get("item_id")): record
            for record in existing_records
            if record.get("item_id")
        }
        existing_filings = await repo.list_waiting_sharefile_filings()
        filing_by_item_id: dict[str, Filing] = {}
        for filing in existing_filings:
            if filing.sharefile_item_id:
                filing_by_item_id.setdefault(str(filing.sharefile_item_id), filing)
            for document in filing.package_documents:
                item_id = document.get("sharefile_item_id")
                if item_id:
                    filing_by_item_id.setdefault(str(item_id), filing)

        index_updates: dict[str, dict] = {}

        for file_item in scanned_files:
            document_type = file_item.get("document_type")
            is_supported = document_type in SUPPORTED_SHAREFILE_DOCUMENT_TYPES
            item_id = str(file_item["id"])
            existing = existing_by_item_id.get(item_id)
            existing_filing = filing_by_item_id.get(item_id)

            if not is_supported:
                ignored_files.append(self._sharefile_file_summary(file_item, "IGNORED"))
                if (
                    not existing
                    or existing.get("status") != "IGNORED"
                    or str(existing.get("metadata_signature") or "")
                    != self._sharefile_metadata_signature(file_item)
                ):
                    index_updates[item_id] = self._sharefile_index_record(
                        file_item, status="IGNORED", change_type="IGNORED", source=source
                    )
                continue

            change_type = self._sharefile_change_type(existing, file_item)
            if existing_filing and existing_filing.status in {FilingStatus.WAITING_FOR_WORKSHEET, FilingStatus.WAITING_FOR_SCHEDULE_A}:
                change_type = existing_filing.status.value
            if change_type == "NEW":
                new_count += 1
            elif change_type == "UPDATED":
                updated_count += 1
            else:
                unchanged_count += 1

            if change_type != "UNCHANGED":
                index_updates[item_id] = self._sharefile_index_record(
                    file_item, status=change_type, change_type=change_type, source=source
                )

            summary = self._sharefile_file_summary(file_item, change_type)
            if change_type in {"NEW", "UPDATED", FilingStatus.WAITING_FOR_WORKSHEET.value, FilingStatus.WAITING_FOR_SCHEDULE_A.value} and process_new_files:
                changed_files.append(file_item)
            else:
                skipped_files.append(summary)

        await repo.upsert_sharefile_files(index_updates)

        packages = await self._resolve_package_candidates(client, token, changed_files)
        synced: list[dict] = []
        queue_skipped: list[dict] = []
        failed: list[dict] = []
        if changed_files and process_new_files:
            synced, queue_skipped, failed = await self._queue_sharefile_packages(
                client,
                token,
                packages,
                background_tasks,
                source=source,
            )

        repair_package_count = 0
        if process_new_files:
            repair_package_count, repair_synced, repair_skipped, repair_failed = await self._repair_waiting_sharefile_packages(
                client,
                token,
                background_tasks,
                source=source,
            )
            synced.extend(repair_synced)
            queue_skipped.extend(repair_skipped)
            failed.extend(repair_failed)

        duplicate_cleanup_count = await self._cleanup_duplicate_active_packages(source)
        duplicate_cleanup_count += await self._cleanup_redundant_waiting_for_schedule_rows(source)

        deleted = 0
        # Deletion reconciliation compares what we just saw against everything
        # on record, so it is only safe after a scan that saw the whole
        # account with no errors.
        if not partial_scan and scan_errors is not None and not scan_errors:
            deleted = await self._mark_deleted_sharefile_files(active_sharefile_item_ids)

        await repo.add_audit(
            AuditLog(
                event="SHAREFILE_SCAN_COMPLETED",
                message="ShareFile incremental scan completed.",
                details={
                    "source": source,
                    "found": len(scanned_files),
                    "useful": len(useful_files),
                    "supported": len(extractable_files),
                    "new": new_count,
                    "updated": updated_count,
                    "unchanged": unchanged_count,
                    "queued": len(synced),
                    "duplicate_cleanup": duplicate_cleanup_count,
                    "deleted": deleted,
                    "failed": len(failed),
                },
            )
        )

        skipped = [*skipped_files, *queue_skipped, *ignored_files]
        return {
            "baseline": False,
            "found": len(scanned_files),
            "useful": len(useful_files),
            "supported": len(extractable_files),
            "packages": len(packages) + repair_package_count,
            "new": new_count,
            "updated": updated_count,
            "unchanged": unchanged_count,
            "synced": len(synced),
            "skipped": len(skipped),
            "failed": len(failed),
            "duplicate_cleanup": duplicate_cleanup_count,
            "deleted": deleted,
            "files": synced,
            "skipped_files": skipped,
            "failed_files": failed,
            "scan_errors": scan_errors or [],
            "message": "ShareFile incremental sync completed. Only new or changed useful files were queued for extraction.",
        }

    async def _queue_sharefile_packages(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        packages: dict[str, list[dict]],
        background_tasks: BackgroundTasks | None,
        source: str,
        prefer_changed: bool = True,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        repo = get_repository()
        synced: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        pending_extractions: list[tuple[str, str, list[dict]]] = []

        for package_key, package_files in packages.items():
            if not package_files:
                continue
            try:
                package_files = self._select_package_files_for_processing(package_files, prefer_changed=prefer_changed)
                package_has_schedule_a = any(
                    file_item.get("document_type") == DocumentType.SCHEDULE_A
                    for file_item in package_files
                )
                suppressed_items = [
                    (file_item, await repo.get_sharefile_suppression(str(file_item.get("id") or "")))
                    for file_item in package_files
                    if file_item.get("id")
                ]
                suppressed_items = [(item, record) for item, record in suppressed_items if record]
                if package_has_schedule_a:
                    # Legacy dashboard deletes also suppressed the shared worksheet.
                    # Keep that worksheet eligible when a new Schedule A needs it.
                    suppressed_items = [
                        (item, record)
                        for item, record in suppressed_items
                        if item.get("document_type") != DocumentType.PLAN_WORKSHEET
                    ]
                if suppressed_items:
                    for file_item, suppression in suppressed_items:
                        await repo.upsert_sharefile_file(
                            file_item["id"],
                            self._sharefile_index_record(
                                file_item,
                                status="SUPPRESSED",
                                change_type="DASHBOARD_DELETE_SUPPRESSED",
                                source=source,
                                filing_id=suppression.get("filing_id"),
                                package_key=package_key,
                            ),
                        )
                    suppressed_ids = {str(item.get("id")) for item, _ in suppressed_items}
                    package_files = [item for item in package_files if str(item.get("id")) not in suppressed_ids]
                    skipped.append(
                        {
                            "package_key": package_key,
                            "reason": "DASHBOARD_DELETE_SUPPRESSED",
                            "item_ids": sorted(suppressed_ids),
                            "message": "Previously deleted ShareFile items were ignored. New source items remain eligible for intake.",
                        }
                    )
                    if any(
                        item.get("document_type") == DocumentType.SCHEDULE_A
                        for item, _ in suppressed_items
                    ):
                        # This is the deleted filing's own Schedule A package. Do
                        # not turn its remaining shared worksheet into a new row.
                        continue
                    if not package_files:
                        continue
                completeness = self._package_completeness(package_files)
                if not completeness["complete"]:
                    filing = await self._upsert_waiting_filing_package(
                        package_key,
                        package_files,
                        completeness["status"],
                        completeness["message"],
                    )
                    for file_item in package_files:
                        await repo.upsert_sharefile_file(
                            file_item["id"],
                            self._sharefile_index_record(
                                file_item,
                                status=completeness["status"].value,
                                change_type=file_item.get("change_type") or "PACKAGE_WAITING",
                                source=source,
                                filing_id=filing.id,
                                package_key=package_key,
                            ),
                        )
                    skipped.append(
                        {
                            "filing_id": filing.id,
                            "package_key": package_key,
                            "reason": completeness["status"].value,
                            "message": completeness["message"],
                        }
                    )
                    continue

                existing = await self._find_active_filing_by_package_key(package_key) or await self._find_existing_package(package_files)
                if existing and not self._package_changed(existing, package_files):
                    await self._supersede_active_filings_for_package(
                        package_key,
                        package_files,
                        "Duplicate ShareFile package row was superseded by the existing active package.",
                        source,
                        exclude_filing_id=existing.id,
                    )
                    for file_item in package_files:
                        await repo.upsert_sharefile_file(
                            file_item["id"],
                            self._sharefile_index_record(
                                file_item,
                                status="UNCHANGED",
                                change_type="UNCHANGED",
                                source=source,
                                filing_id=existing.id,
                                package_key=package_key,
                            ),
                        )
                    skipped.append({"package_key": package_key, "reason": "UNCHANGED"})
                    continue

                if existing and existing.id and existing.status in {FilingStatus.WAITING_FOR_WORKSHEET, FilingStatus.WAITING_FOR_SCHEDULE_A}:
                    await repo.update_filing(
                        existing.id,
                        {
                            "status": FilingStatus.SUPERSEDED,
                            "error_message": "Matching Schedule A and Plan Worksheet are now present; complete package was queued.",
                        },
                    )
                    await repo.add_audit(
                        AuditLog(
                            filing_id=existing.id,
                            event="SHAREFILE_WAITING_PACKAGE_COMPLETED",
                            message="Waiting package was completed by a matching document and superseded by an extraction package.",
                            details={"package_key": package_key, "source": source},
                        )
                    )
                elif existing and existing.id:
                    await repo.update_filing(
                        existing.id,
                        {
                            "status": FilingStatus.SUPERSEDED,
                            "error_message": "ShareFile source document changed; a replacement filing package was queued.",
                        },
                    )
                    await repo.add_audit(
                        AuditLog(
                            filing_id=existing.id,
                            event="SHAREFILE_PACKAGE_SUPERSEDED",
                            message="Existing filing package was superseded by a new or updated ShareFile document.",
                            details={"package_key": package_key, "source": source},
                        )
                    )

                await self._supersede_active_filings_for_package(
                    package_key,
                    package_files,
                    "ShareFile package was replaced by a newer package for the same client/year.",
                    source,
                    exclude_filing_id=existing.id if existing else None,
                )
                await self._supersede_waiting_filings_for_package(
                    package_key,
                    package_files,
                    "Matching Schedule A and Plan Worksheet are now present; complete package was queued.",
                    source,
                )

                filing, job, processing_documents = await self._create_filing_package(
                    client,
                    token,
                    package_key,
                    package_files,
                )

                for file_item in package_files:
                    await repo.upsert_sharefile_file(
                        file_item["id"],
                        self._sharefile_index_record(
                            file_item,
                            status="QUEUED",
                            change_type=file_item.get("change_type") or "CHANGED",
                            source=source,
                            filing_id=filing.id,
                            package_key=package_key,
                            downloaded=True,
                        ),
                    )

                await repo.add_audit(
                    AuditLog(
                        filing_id=filing.id,
                        event="SHAREFILE_PACKAGE_QUEUED",
                        message="New or changed ShareFile package was downloaded and queued for extraction.",
                        details={
                            "package_key": package_key,
                            "source": source,
                            "document_count": len(processing_documents),
                            "sharefile_item_ids": [item["id"] for item in package_files],
                        },
                    )
                )

                pending_extractions.append((filing.id, job.id, processing_documents))

                synced.append(
                    {
                        "filing_id": filing.id,
                        "job_id": job.id,
                        "package_key": package_key,
                        "file_name": filing.file_name,
                        "document_count": len(processing_documents),
                        "sharefile_item_ids": [item["id"] for item in package_files],
                    }
                )
            except Exception as exc:
                for file_item in package_files:
                    await repo.upsert_sharefile_file(
                        file_item["id"],
                        self._sharefile_index_record(
                            file_item,
                            status="FAILED",
                            change_type=file_item.get("change_type") or "CHANGED",
                            source=source,
                            package_key=package_key,
                        ),
                    )
                failed.append(
                    {
                        "package_key": package_key,
                        "sharefile_item_ids": [item.get("id") for item in package_files],
                        "error": str(exc),
                    }
                )

        # All dashboard rows are persisted as QUEUED before any extractor is
        # started. Four packages then advance to EXTRACTING while the rest stay
        # visibly queued and begin automatically as capacity becomes available.
        if pending_extractions:
            if background_tasks is not None:
                background_tasks.add_task(process_extraction_batch, pending_extractions)
            else:
                # The dedicated worker must not acknowledge the SQS message
                # until every package in this batch has finished persistence.
                await process_extraction_batch(pending_extractions)

        return synced, skipped, failed

    def _select_package_files_for_processing(self, package_files: list[dict], prefer_changed: bool = True) -> list[dict]:
        supported = [
            file_item
            for file_item in self._dedupe_scanned_files(package_files)
            if file_item.get("document_type") in SUPPORTED_SHAREFILE_DOCUMENT_TYPES
        ]
        if not supported:
            return []

        changed_statuses = {
            "NEW",
            "UPDATED",
            "CHANGED",
            "PACKAGE_WAITING",
            FilingStatus.WAITING_FOR_WORKSHEET.value,
            FilingStatus.WAITING_FOR_SCHEDULE_A.value,
        }
        changed = (
            [
                file_item
                for file_item in supported
                if str(file_item.get("change_type") or "").upper() in changed_statuses
            ]
            if prefer_changed
            else []
        )
        selected: dict[str, dict] = {
            file_item["id"]: file_item
            for file_item in changed
            if file_item.get("id")
        }

        if not selected:
            selected = {
                file_item["id"]: file_item
                for file_item in self._latest_files_by_document_type(supported)
                if file_item.get("id")
            }
        else:
            selected_types = {file_item.get("document_type") for file_item in selected.values()}
            for document_type in (DocumentType.SCHEDULE_A, DocumentType.PLAN_WORKSHEET):
                if document_type in selected_types:
                    continue
                latest = self._latest_file_for_document_type(supported, document_type)
                if latest and latest.get("id"):
                    selected[latest["id"]] = latest

        return sorted(selected.values(), key=self._document_sort_key)

    def _latest_files_by_document_type(self, package_files: list[dict]) -> list[dict]:
        latest: list[dict] = []
        for document_type in (DocumentType.SCHEDULE_A, DocumentType.PLAN_WORKSHEET):
            file_item = self._latest_file_for_document_type(package_files, document_type)
            if file_item:
                latest.append(file_item)
        return latest

    def _latest_file_for_document_type(self, package_files: list[dict], document_type: DocumentType) -> dict | None:
        candidates = [file_item for file_item in package_files if file_item.get("document_type") == document_type]
        if not candidates:
            return None
        return max(candidates, key=self._file_recency_key)

    def _file_recency_key(self, file_item: dict) -> tuple[str, str, str]:
        return (
            str(file_item.get("modified_at") or ""),
            str(file_item.get("created_at") or ""),
            str(file_item.get("id") or ""),
        )

    def _is_changed_package_file(self, file_item: dict) -> bool:
        return str(file_item.get("change_type") or "").upper() in {
            "NEW",
            "UPDATED",
            "CHANGED",
            "PACKAGE_WAITING",
            FilingStatus.WAITING_FOR_WORKSHEET.value,
            FilingStatus.WAITING_FOR_SCHEDULE_A.value,
        }

    async def _repair_waiting_sharefile_packages(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        background_tasks: BackgroundTasks | None,
        source: str,
    ) -> tuple[int, list[dict], list[dict], list[dict]]:
        repo = get_repository()
        waiting_filings = await repo.list_waiting_sharefile_filings()
        if not waiting_filings:
            return 0, [], [], []

        waiting_item_ids = {
            str(document.get("sharefile_item_id"))
            for filing in waiting_filings
            for document in filing.package_documents
            if document.get("sharefile_item_id")
        }
        waiting_item_ids.update(str(filing.sharefile_item_id) for filing in waiting_filings if filing.sharefile_item_id)
        if not waiting_item_ids:
            return 0, [], [], []

        packages_by_root: dict[str, dict[str, dict]] = defaultdict(dict)
        waiting_records = await repo.list_sharefile_files_by_item_ids(waiting_item_ids)
        waiting_roots = {
            str(record.get("package_root_key") or "")
            for record in waiting_records
            if record.get("package_root_key")
        }
        if not waiting_roots:
            return 0, [], [], []
        skipped: list[dict] = []
        for record in await repo.list_sharefile_files_by_package_roots(waiting_roots):
            item_id = str(record.get("item_id") or "")
            if not item_id:
                continue
            if record.get("status") in {"DELETED", "IGNORED"}:
                continue
            if record.get("document_type") not in {DocumentType.SCHEDULE_A.value, DocumentType.PLAN_WORKSHEET.value}:
                continue
            file_item = self._file_item_from_index_record(record)
            file_item["change_type"] = "UNCHANGED"
            file_item["active_waiting_item"] = item_id in waiting_item_ids
            root_key = str(record.get("package_root_key") or self._package_root_key(file_item) or "")
            if not root_key:
                continue
            packages_by_root[root_key][item_id] = file_item

        repair_packages: dict[str, list[dict]] = {}
        for root_key in sorted(waiting_roots):
            package_files = list(packages_by_root.get(root_key, {}).values())
            expanded_packages = self._expand_schedule_a_packages(root_key, package_files)
            complete_count = 0
            for package_key, files in expanded_packages.items():
                completeness = self._package_completeness(files) if files else {"complete": False, "status": None}
                if completeness["complete"]:
                    repair_packages[package_key] = self._select_package_files_for_processing(files, prefer_changed=False)
                    complete_count += 1
            if complete_count == 0:
                skipped.append(
                    {
                        "package_key": root_key,
                        "reason": "WAITING_PACKAGE_STILL_INCOMPLETE",
                        "message": "Waiting package does not yet have both Schedule A and Plan Worksheet.",
                    }
                )

        if not repair_packages:
            return 0, [], skipped, []

        synced, queue_skipped, failed = await self._queue_sharefile_packages(
            client,
            token,
            repair_packages,
            background_tasks,
            source=f"{source}_WAITING_REPAIR",
            prefer_changed=False,
        )
        return len(repair_packages), synced, [*skipped, *queue_skipped], failed

    async def _cleanup_duplicate_active_packages(self, source: str) -> int:
        repo = get_repository()
        grouped: dict[str, list[Filing]] = defaultdict(list)
        for filing in await repo.list_filings():
            if not filing.id or filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                continue
            for package_key in self._filing_package_keys(filing):
                grouped[package_key].append(filing)

        cleaned = 0
        for package_key, filings in grouped.items():
            unique_filings = {filing.id: filing for filing in filings if filing.id}
            if len(unique_filings) <= 1:
                continue
            ordered = sorted(
                unique_filings.values(),
                key=lambda filing: (filing.updated_at, filing.created_at, filing.id or ""),
                reverse=True,
            )
            keep = ordered[0]
            for duplicate in ordered[1:]:
                if not duplicate.id:
                    continue
                await repo.update_filing(
                    duplicate.id,
                    {
                        "status": FilingStatus.SUPERSEDED,
                        "error_message": f"Duplicate active ShareFile package row was superseded by {keep.id}.",
                    },
                )
                await repo.add_audit(
                    AuditLog(
                        filing_id=duplicate.id,
                        event="SHAREFILE_DUPLICATE_PACKAGE_SUPERSEDED",
                        message="Duplicate active ShareFile package row was superseded.",
                        details={"package_key": package_key, "kept_filing_id": keep.id, "source": source},
                    )
                )
                cleaned += 1
        return cleaned

    async def _cleanup_redundant_waiting_for_schedule_rows(self, source: str) -> int:
        repo = get_repository()
        active_filings = [
            filing
            for filing in await repo.list_filings()
            if filing.id and filing.status not in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}
        ]
        complete_roots = {
            root_key
            for filing in active_filings
            if self._filing_has_schedule_and_worksheet(filing)
            for root_key in self._filing_package_root_keys(filing)
        }
        if not complete_roots:
            return 0

        cleaned = 0
        for filing in active_filings:
            if filing.status != FilingStatus.WAITING_FOR_SCHEDULE_A or not filing.id:
                continue
            if self._filing_package_root_keys(filing).isdisjoint(complete_roots):
                continue
            await repo.update_filing(
                filing.id,
                {
                    "status": FilingStatus.SUPERSEDED,
                    "error_message": "Temporary worksheet-only row was superseded by a complete Schedule A package.",
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="SHAREFILE_REDUNDANT_WORKSHEET_WAITING_SUPERSEDED",
                    message="Temporary worksheet-only row was superseded by a complete Schedule A package.",
                    details={"source": source, "complete_roots": sorted(complete_roots)},
                )
            )
            cleaned += 1
        return cleaned

    def _filing_has_schedule_and_worksheet(self, filing: Filing) -> bool:
        document_types = {str(document.get("document_type")) for document in filing.package_documents}
        return DocumentType.SCHEDULE_A.value in document_types and DocumentType.PLAN_WORKSHEET.value in document_types

    def _filing_package_root_keys(self, filing: Filing) -> set[str]:
        roots = {
            str(document.get("package_root_key"))
            for document in filing.package_documents
            if document.get("package_root_key")
        }
        roots.update(
            self._package_root_key_from_package_key(package_key)
            for package_key in self._filing_package_keys(filing)
            if package_key
        )
        return {root for root in roots if root}

    def _package_completeness(self, package_files: list[dict]) -> dict:
        has_schedule_a = any(item.get("document_type") == DocumentType.SCHEDULE_A for item in package_files)
        has_worksheet = any(item.get("document_type") == DocumentType.PLAN_WORKSHEET for item in package_files)
        if has_schedule_a and has_worksheet:
            return {"complete": True, "status": FilingStatus.QUEUED, "message": "Package has Schedule A and Plan Worksheet."}
        if has_schedule_a:
            return {
                "complete": False,
                "status": FilingStatus.WAITING_FOR_WORKSHEET,
                "message": "Schedule A was received. Waiting for matching 5500 Plan Worksheet.",
            }
        return {
            "complete": False,
            "status": FilingStatus.WAITING_FOR_SCHEDULE_A,
            "message": "5500 Plan Worksheet was received. Waiting for matching Schedule A PDF.",
        }

    async def _upsert_waiting_filing_package(
        self,
        package_key: str,
        package_files: list[dict],
        status: FilingStatus,
        message: str,
    ) -> Filing:
        repo = get_repository()
        existing = await self._find_waiting_filing_for_package(package_key, package_files) or await self._find_existing_package(package_files)
        package_documents = [
            self._metadata_package_document(file_item, package_key)
            for file_item in sorted(package_files, key=self._document_sort_key)
        ]
        total_size = sum(int(item.get("size") or 0) for item in package_files)
        primary_file = next(
            (item for item in package_files if item.get("document_type") == DocumentType.SCHEDULE_A),
            package_files[0],
        )
        values = {
            "file_name": self._filing_name_for_package(package_key, package_files),
            "content_type": "application/vnd.erisapros.filing-package",
            "file_size": total_size,
            "document_type": primary_file.get("document_type") or DocumentType.UNKNOWN,
            "package_document_count": len(package_documents),
            "status": status,
            "s3_key": f"sharefile-package/{package_key}",
            "s3_bucket": None,
            "storage_path": None,
            "package_documents": package_documents,
            "intake_source": "SHAREFILE",
            "sharefile_item_id": primary_file.get("id"),
            "sharefile_parent_id": primary_file.get("parent_id"),
            "error_message": message,
        }
        if existing and existing.id and existing.status in {FilingStatus.WAITING_FOR_WORKSHEET, FilingStatus.WAITING_FOR_SCHEDULE_A}:
            updated = await repo.update_filing(existing.id, values)
            if updated:
                await repo.add_audit(
                    AuditLog(
                        filing_id=updated.id,
                        event="SHAREFILE_PACKAGE_WAITING",
                        message=message,
                        details={"package_key": package_key, "document_count": len(package_documents)},
                    )
                )
                return updated

        filing = Filing(**values)
        filing = await repo.create_filing(filing)
        await repo.add_audit(
            AuditLog(
                filing_id=filing.id,
                event="SHAREFILE_PACKAGE_WAITING",
                message=message,
                details={"package_key": package_key, "document_count": len(package_documents)},
            )
        )
        return filing

    def _metadata_package_document(self, file_item: dict, package_key: str) -> dict:
        document_type = file_item.get("document_type")
        return {
            "file_name": file_item.get("name"),
            "content_type": self._content_type_for(file_item.get("name") or ""),
            "file_size": int(file_item.get("size") or 0),
            "document_type": document_type.value if document_type else DocumentType.UNKNOWN.value,
            "s3_key": None,
            "s3_bucket": None,
            "storage_path": None,
            "intake_source": "SHAREFILE",
            "sharefile_item_id": file_item.get("id"),
            "sharefile_parent_id": file_item.get("parent_id"),
            "sharefile_path": file_item.get("path"),
            "sharefile_modified_at": file_item.get("modified_at"),
            "sharefile_created_at": file_item.get("created_at"),
            "package_key": package_key,
            "package_root_key": self._package_root_key(file_item),
            "client_name": self._client_name_for(file_item),
            "filing_year": self._filing_year_for(file_item),
            "metadata_signature": self._sharefile_metadata_signature(file_item),
        }

    async def _resolve_package_candidates(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        changed_files: list[dict],
    ) -> dict[str, list[dict]]:
        packages = self._group_files_by_package(changed_files)
        if not packages:
            return {}

        repo = get_repository()
        package_root_keys = {
            self._package_root_key_from_package_key(package_key)
            for package_key in packages
        }
        indexed_files = await repo.list_sharefile_files_by_package_roots(package_root_keys)
        indexed_by_id = {str(record.get("item_id")): record for record in indexed_files if record.get("item_id")}
        relevant_item_ids = {
            str(record.get("item_id"))
            for record in indexed_files
            if record.get("item_id")
        }
        relevant_item_ids.update(
            str(file_item.get("id"))
            for package_files in packages.values()
            for file_item in package_files
            if file_item.get("id")
        )
        active_schedule_item_ids = self._active_schedule_item_ids(
            await repo.list_filings_by_sharefile_item_ids(relevant_item_ids)
        )
        resolved_packages: dict[str, list[dict]] = {}
        for package_key, package_files in list(packages.items()):
            by_id = {file_item["id"]: file_item for file_item in package_files if file_item.get("id")}
            root_key = self._package_root_key_from_package_key(package_key)
            for record in indexed_files:
                record_root_key = str(record.get("package_root_key") or "")
                if not record_root_key:
                    record_root_key = self._package_root_key_from_package_key(str(record.get("package_key") or ""))
                if record_root_key != root_key:
                    continue
                if record.get("status") in {"DELETED", "IGNORED"}:
                    continue
                if record.get("document_type") not in {DocumentType.SCHEDULE_A.value, DocumentType.PLAN_WORKSHEET.value}:
                    continue
                item_id = record.get("item_id")
                if item_id and item_id not in by_id:
                    indexed_file = self._file_item_from_index_record(record)
                    indexed_file["change_type"] = "UNCHANGED"
                    indexed_file["active_schedule_filing"] = str(item_id) in active_schedule_item_ids
                    by_id[item_id] = indexed_file
            package_root = self._package_root_for_files(package_files)
            if package_root:
                root_files = await self._scan_package_root(client, token, package_root)
                for root_file in root_files:
                    item_id = root_file.get("id")
                    if item_id and item_id not in by_id:
                        indexed_record = indexed_by_id.get(str(item_id))
                        if indexed_record:
                            root_file["indexed_status"] = indexed_record.get("status")
                            root_file["indexed_filing_id"] = indexed_record.get("filing_id")
                            root_file["indexed_package_key"] = indexed_record.get("package_key")
                            root_file["indexed_package_root_key"] = indexed_record.get("package_root_key")
                            root_file["active_schedule_filing"] = str(item_id) in active_schedule_item_ids
                        root_file["change_type"] = "UNCHANGED"
                        by_id[item_id] = root_file
            else:
                sibling_files = await self._find_package_siblings_from_index(package_key)
                for sibling in sibling_files:
                    item_id = sibling.get("id")
                    if item_id and item_id not in by_id:
                        by_id[item_id] = sibling
            resolved_packages.update(self._expand_schedule_a_packages(package_key, list(by_id.values())))
        return self._drop_redundant_worksheet_waiting_packages(resolved_packages)

    def _drop_redundant_worksheet_waiting_packages(self, packages: dict[str, list[dict]]) -> dict[str, list[dict]]:
        complete_roots = {
            self._package_root_key_from_package_key(package_key)
            for package_key, package_files in packages.items()
            if self._package_completeness(package_files).get("complete")
        }
        if not complete_roots:
            return packages

        filtered: dict[str, list[dict]] = {}
        for package_key, package_files in packages.items():
            root_key = self._package_root_key_from_package_key(package_key)
            if root_key in complete_roots and package_key.endswith(" > Waiting for Schedule A"):
                continue
            filtered[package_key] = package_files
        return filtered

    def _expand_schedule_a_packages(
        self,
        package_key: str,
        package_files: list[dict],
    ) -> dict[str, list[dict]]:
        supported = [
            item
            for item in self._dedupe_scanned_files(package_files)
            if item.get("document_type") in SUPPORTED_SHAREFILE_DOCUMENT_TYPES
        ]
        if not supported:
            return {}

        schedules = [item for item in supported if item.get("document_type") == DocumentType.SCHEDULE_A]
        worksheets = [item for item in supported if item.get("document_type") == DocumentType.PLAN_WORKSHEET]
        latest_worksheet = self._latest_file_for_document_type(supported, DocumentType.PLAN_WORKSHEET)
        changed_schedules = [item for item in schedules if self._is_changed_package_file(item)]
        changed_worksheets = [item for item in worksheets if self._is_changed_package_file(item)]
        expanded: dict[str, list[dict]] = {}

        if changed_worksheets:
            target_schedules = [schedule for schedule in schedules if self._is_tracked_schedule_candidate(schedule)]
            if target_schedules and latest_worksheet:
                for schedule in target_schedules:
                    expanded[self._schedule_package_key(schedule)] = [schedule, latest_worksheet]
                return expanded
            worksheet = latest_worksheet or changed_worksheets[0]
            expanded[self._worksheet_waiting_package_key(worksheet)] = [worksheet]
            return expanded

        target_schedules = changed_schedules or (schedules if " > Schedule A::" in package_key else [])
        for schedule in target_schedules:
            files = [schedule]
            if latest_worksheet:
                files.append(latest_worksheet)
            expanded[self._schedule_package_key(schedule)] = files

        if expanded:
            return expanded

        if latest_worksheet:
            expanded[self._worksheet_waiting_package_key(latest_worksheet)] = [latest_worksheet]
        return expanded

    def _is_tracked_schedule_candidate(self, file_item: dict) -> bool:
        if file_item.get("document_type") != DocumentType.SCHEDULE_A:
            return False
        if self._is_changed_package_file(file_item):
            return True
        if file_item.get("active_schedule_filing") or file_item.get("active_waiting_item"):
            return True
        if file_item.get("filing_id") and not file_item.get("indexed_status"):
            return True
        indexed_status = str(file_item.get("indexed_status") or "").upper()
        return indexed_status in {
            FilingStatus.WAITING_FOR_WORKSHEET.value,
        }

    def _active_schedule_item_ids(self, filings: list[Filing]) -> set[str]:
        active_ids: set[str] = set()
        for filing in filings:
            if filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                continue
            if filing.document_type == DocumentType.SCHEDULE_A and filing.sharefile_item_id:
                active_ids.add(str(filing.sharefile_item_id))
            for document in filing.package_documents:
                if document.get("document_type") == DocumentType.SCHEDULE_A.value and document.get("sharefile_item_id"):
                    active_ids.add(str(document["sharefile_item_id"]))
        return active_ids

    async def _find_package_siblings_from_index(self, package_key: str) -> list[dict]:
        repo = get_repository()
        siblings = []
        root_key = self._package_root_key_from_package_key(package_key)
        for record in await repo.list_sharefile_files_by_package_roots({root_key}):
            if record.get("package_key") != package_key:
                continue
            if record.get("status") in {"DELETED", "IGNORED"}:
                continue
            if record.get("document_type") not in {DocumentType.SCHEDULE_A.value, DocumentType.PLAN_WORKSHEET.value}:
                continue
            sibling = self._file_item_from_index_record(record)
            sibling["change_type"] = "UNCHANGED"
            siblings.append(sibling)
        return siblings

    def _package_root_for_files(self, package_files: list[dict]) -> dict | None:
        for file_item in package_files:
            root = self._package_root_for_file(file_item)
            if root:
                return root
        return None

    def _package_root_for_file(self, file_item: dict) -> dict | None:
        path_parts = list(file_item.get("path_parts") or [])
        if not path_parts:
            return None
        folder_ids = dict(file_item.get("folder_ids_by_depth") or {})
        folders = path_parts[:-1]
        for index in range(len(folders) - 1, -1, -1):
            if re.search(r"\b20\d{2}\b", folders[index].lower()) and "filing" in folders[index].lower():
                folder_id = folder_ids.get(str(index)) or folder_ids.get(index)
                if folder_id:
                    return {
                        "id": folder_id,
                        "path_parts": path_parts[: index + 1],
                    }
        parent_id = file_item.get("parent_id")
        if parent_id:
            return {
                "id": parent_id,
                "path_parts": folders,
            }
        return None

    async def _scan_package_root(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        package_root: dict,
    ) -> list[dict]:
        files = await self._scan_folder(
            client,
            token,
            package_root["id"],
            package_root["path_parts"],
            root_folder_id=package_root["id"],
        )
        useful = [
            item
            for item in self._dedupe_scanned_files(files)
            if item.get("document_type") in SUPPORTED_SHAREFILE_DOCUMENT_TYPES
            and not self._is_excluded_package_document(item)
        ]
        return useful

    def _is_excluded_package_document(self, file_item: dict) -> bool:
        name = str(file_item.get("name") or "").lower()
        path = str(file_item.get("path") or "").lower()
        excluded_terms = ("acknowledgement", "acknowledgment", "sar", "draft", "no longer filing", "signature", "signed", "cover")
        return any(term in name or term in path for term in excluded_terms)

    def _file_item_from_index_record(self, record: dict) -> dict:
        document_type = record.get("document_type")
        return {
            "id": record.get("item_id"),
            "name": record.get("file_name") or "",
            "path": record.get("path") or "",
            "path_parts": record.get("path_parts") or [],
            "root_folder_id": record.get("root_folder_id"),
            "parent_id": record.get("folder_id"),
            "folder_ids_by_depth": record.get("folder_ids_by_depth") or {},
            "document_type": DocumentType(document_type) if document_type else None,
            "size": int(record.get("file_size") or 0),
            "modified_at": record.get("modified_at"),
            "created_at": record.get("created_at_sharefile"),
            "raw": record.get("raw") or {},
            "change_type": record.get("change_type"),
            "indexed_status": record.get("status"),
            "indexed_filing_id": record.get("filing_id"),
            "indexed_package_key": record.get("package_key"),
            "indexed_package_root_key": record.get("package_root_key"),
        }

    async def _index_scanned_files(self, files: list[dict], baseline: bool = False) -> None:
        repo = get_repository()
        records: dict[str, dict] = {}
        for file_item in files:
            document_type = file_item.get("document_type")
            status = "INDEXED" if document_type in SUPPORTED_SHAREFILE_DOCUMENT_TYPES else "IGNORED"
            records[str(file_item["id"])] = self._sharefile_index_record(
                file_item,
                status=status,
                change_type="BASELINE" if baseline else status,
                source="SHAREFILE_BASELINE_SCAN" if baseline else "SHAREFILE_SCAN",
            )
        await repo.upsert_sharefile_files(records)

    async def _resolve_deferred_content_sniffs(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        scanned_files: list[dict],
    ) -> list[dict]:
        """Classify by file content only where it is actually needed.

        Downloading and parsing files during the recursive folder walk made a
        full scan of all client folders take tens of minutes, so scans never
        finished within a poll window. Instead, sniff after the walk and only
        for files that are new or changed since the last scan; unchanged files
        reuse the document type recorded in the ShareFile index.
        """
        repo = get_repository()
        sniffable_files: list[dict] = []
        for file_item in scanned_files:
            if file_item.get("document_type") or not file_item.get("needs_content_sniff"):
                continue
            if not self._sniff_path_is_relevant(file_item.get("path_parts") or []):
                continue
            if int(file_item.get("size") or 0) > MAX_CONTENT_SNIFF_BYTES:
                continue
            sniffable_files.append(file_item)

        existing_records = await repo.list_sharefile_files_by_item_ids(
            {str(item["id"]) for item in sniffable_files if item.get("id")}
        )
        existing_by_item_id = {
            str(record.get("item_id")): record
            for record in existing_records
            if record.get("item_id")
        }
        candidates: list[dict] = []
        for file_item in sniffable_files:
            existing = existing_by_item_id.get(str(file_item["id"]))
            if existing and self._sharefile_change_type(existing, file_item) == "UNCHANGED":
                indexed_type = existing.get("document_type")
                if indexed_type:
                    try:
                        file_item["document_type"] = DocumentType(indexed_type)
                    except ValueError:
                        pass
                # Unchanged and previously classified (possibly as not useful):
                # never download it again.
                continue
            candidates.append(file_item)

        if not candidates:
            return scanned_files

        # Sniff concurrently (bounded) and publish progress so scan-status
        # shows what a long-running scan is actually doing.
        semaphore = asyncio.Semaphore(6)
        done_count = 0

        async def sniff_one(file_item: dict) -> None:
            nonlocal done_count
            async with semaphore:
                file_item["document_type"] = await self._classify_sharefile_document_by_content(
                    client, token, file_item["id"], file_item["name"]
                )
            done_count += 1
            if done_count % 10 == 0 or done_count == len(candidates):
                try:
                    await repo.upsert_sharefile_state(
                        SHAREFILE_INCREMENTAL_STATE_KEY,
                        {"sniff_total": len(candidates), "sniff_done": done_count},
                    )
                except Exception:
                    pass

        await repo.upsert_sharefile_state(
            SHAREFILE_INCREMENTAL_STATE_KEY,
            {"sniff_total": len(candidates), "sniff_done": 0},
        )
        await asyncio.gather(*(sniff_one(item) for item in candidates))
        return scanned_files

    def _sniff_path_is_relevant(self, path_parts: list[str]) -> bool:
        for part in path_parts[:-1]:
            segment = str(part)
            if (
                self._is_schedule_a_folder_segment(segment)
                or self._is_year_filing_segment(segment)
                or self._is_5500_filing_folder_segment(segment)
            ):
                return True
        return False

    def _sharefile_index_record(
        self,
        file_item: dict,
        status: str,
        change_type: str | None = None,
        source: str | None = None,
        filing_id: str | None = None,
        package_key: str | None = None,
        downloaded: bool = False,
    ) -> dict:
        path_parts = list(file_item.get("path_parts") or [])
        resolved_package_key = package_key or self._package_key(file_item)
        now = datetime.utcnow()
        record = {
            "status": status,
            "change_type": change_type or status,
            "source": source,
            "file_name": file_item.get("name"),
            "folder_id": file_item.get("parent_id"),
            "folder_path": " > ".join(path_parts[:-1]),
            "path": file_item.get("path"),
            "path_parts": path_parts,
            "root_folder_id": file_item.get("root_folder_id"),
            "folder_ids_by_depth": self._mongo_safe_string_keyed_dict(file_item.get("folder_ids_by_depth") or {}),
            "client_name": self._client_name_for(file_item),
            "filing_year": self._filing_year_for(file_item),
            "package_key": resolved_package_key,
            "package_root_key": self._package_root_key(file_item),
            "document_type": file_item["document_type"].value if file_item.get("document_type") else None,
            "file_size": int(file_item.get("size") or 0),
            "modified_at": file_item.get("modified_at"),
            "created_at_sharefile": file_item.get("created_at"),
            "version": self._sharefile_version(file_item),
            "hash": self._sharefile_hash(file_item),
            "metadata_signature": self._sharefile_metadata_signature(file_item),
            "last_seen_at": now,
            "raw": file_item.get("raw"),
        }
        if filing_id:
            record["filing_id"] = filing_id
        if downloaded:
            record["last_downloaded_at"] = now
        return record

    def _mongo_safe_string_keyed_dict(self, value: dict) -> dict:
        return {str(key): item for key, item in value.items()}

    def _sharefile_change_type(self, existing: dict | None, file_item: dict) -> str:
        if not existing or existing.get("status") == "DELETED":
            file_item["change_type"] = "NEW"
            return "NEW"
        if existing.get("status") in {"NEW", "UPDATED", "FAILED", FilingStatus.WAITING_FOR_WORKSHEET.value, FilingStatus.WAITING_FOR_SCHEDULE_A.value}:
            change_type = "UPDATED" if existing.get("status") == "FAILED" else str(existing.get("status"))
            file_item["change_type"] = change_type
            return change_type
        existing_signature = str(existing.get("metadata_signature") or "")
        current_signature = self._sharefile_metadata_signature(file_item)
        if existing_signature != current_signature:
            file_item["change_type"] = "UPDATED"
            return "UPDATED"
        file_item["change_type"] = "UNCHANGED"
        return "UNCHANGED"

    def _sharefile_metadata_signature(self, file_item: dict) -> str:
        parts = [
            str(file_item.get("id") or ""),
            str(file_item.get("size") or 0),
            str(file_item.get("modified_at") or ""),
            str(self._sharefile_version(file_item) or ""),
            str(self._sharefile_hash(file_item) or ""),
        ]
        return "|".join(parts)

    def _sharefile_version(self, file_item: dict) -> str | None:
        raw = file_item.get("raw") if isinstance(file_item.get("raw"), dict) else {}
        value = (
            raw.get("Version")
            or raw.get("version")
            or raw.get("FileVersion")
            or raw.get("fileVersion")
            or raw.get("ETag")
            or raw.get("etag")
        )
        return str(value) if value is not None else None

    def _sharefile_hash(self, file_item: dict) -> str | None:
        raw = file_item.get("raw") if isinstance(file_item.get("raw"), dict) else {}
        value = (
            raw.get("Hash")
            or raw.get("hash")
            or raw.get("SHA256")
            or raw.get("sha256")
            or raw.get("MD5")
            or raw.get("md5")
        )
        return str(value) if value is not None else None

    def _sharefile_file_summary(self, file_item: dict, status: str) -> dict:
        return {
            "item_id": file_item.get("id"),
            "file_name": file_item.get("name"),
            "document_type": file_item["document_type"].value if file_item.get("document_type") else None,
            "status": status,
            "package_key": self._package_key(file_item) if file_item.get("path_parts") else None,
            "path": file_item.get("path"),
        }

    async def _mark_deleted_sharefile_files(self, active_sharefile_item_ids: set[str]) -> int:
        repo = get_repository()
        deleted = 0
        for record in await repo.list_active_sharefile_file_summaries():
            item_id = str(record.get("item_id") or "")
            if not item_id or item_id in active_sharefile_item_ids:
                continue
            if record.get("status") in {"DELETED", "IGNORED"}:
                continue
            if record.get("document_type") not in {DocumentType.PLAN_WORKSHEET.value, DocumentType.SCHEDULE_A.value}:
                continue
            marked = await self._mark_deleted_sharefile_item(item_id, "ShareFile source file was not found during the latest complete scan.")
            if marked:
                deleted += 1
        deleted += await self._mark_deleted_sharefile_filings(active_sharefile_item_ids)
        return deleted

    async def _mark_deleted_sharefile_item(self, item_id: str, reason: str | None = None) -> dict | None:
        repo = get_repository()
        marked = await repo.mark_sharefile_file_deleted(item_id, reason)
        filing = await repo.get_filing_by_sharefile_item_id(item_id)
        if filing and filing.id and filing.status not in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
            await repo.update_filing(
                filing.id,
                {
                    "status": FilingStatus.DELETED,
                    "error_message": reason or "ShareFile source file was deleted.",
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="SHAREFILE_SOURCE_DELETED",
                    message=reason or "ShareFile source file was deleted.",
                    details={"sharefile_item_id": item_id},
                )
            )
        return marked

    async def _get_item(self, client: httpx.AsyncClient, token: ShareFileOAuthToken, item_id: str) -> dict:
        response = await self._authorized_request(
            client,
            "GET",
            f"https://{token.subdomain}.{token.apicp}/sf/v3/Items({item_id})",
            token,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()

    async def _list_webhook_subscriptions(self, client: httpx.AsyncClient, token: ShareFileOAuthToken) -> list[dict]:
        response = await self._authorized_request(
            client,
            "GET",
            f"https://{token.subdomain}.{token.apicp}/sf/v3/WebhookSubscriptions",
            token,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_items(payload)

    async def _resolve_webhook_subscription_roots(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        scan_roots: list[dict],
    ) -> list[dict]:
        folders: list[dict] = []
        for root in scan_roots:
            folders.append(root)
            folders.extend(
                await self._scan_folder_contexts(
                    client,
                    token,
                    root["id"],
                    root["path_parts"],
                )
            )

        deduped: list[dict] = []
        seen: set[str] = set()
        for folder in folders:
            folder_id = folder.get("id")
            if not folder_id or folder_id in seen:
                continue
            seen.add(folder_id)
            deduped.append(folder)
        return deduped

    async def _scan_folder_contexts(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        folder_id: str,
        path_parts: list[str],
        depth: int = 0,
    ) -> list[dict]:
        if not folder_id or depth > MAX_SHAREFILE_SCAN_DEPTH:
            return []
        try:
            children = await self._list_folder(client, token, folder_id)
        except httpx.HTTPError:
            # Includes network-level failures (timeouts, dropped connections),
            # which must not abort folder discovery.
            return []

        folders: list[dict] = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if not item_id or not name or not self._is_folder(item):
                continue
            item_path = path_parts + [name]
            folder = self._scan_root(item_id, "Discovered nested ShareFile folder", item_path)
            folders.append(folder)
            folders.extend(await self._scan_folder_contexts(client, token, item_id, item_path, depth + 1))
        return folders

    async def _discover_relevant_webhook_roots(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        scan_roots: list[dict],
    ) -> list[dict]:
        roots: list[dict] = []
        for root in scan_roots:
            roots.extend(await self._discover_relevant_webhook_roots_for_root(client, token, root))

        deduped: list[dict] = []
        seen: set[str] = set()
        for root in roots:
            root_id = str(root.get("id") or "")
            if not root_id or root_id in seen:
                continue
            seen.add(root_id)
            deduped.append(root)
        return deduped

    async def _discover_relevant_webhook_roots_for_root(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        root: dict,
    ) -> list[dict]:
        root_id = str(root.get("id") or "")
        if not root_id:
            return []
        path_parts = list(root.get("path_parts") or [])
        roots = [root]

        if path_parts and self._is_schedule_a_folder_segment(path_parts[-1]):
            roots.extend(await self._discover_webhook_descendant_roots(client, token, root_id, path_parts))
            return roots
        if path_parts and self._is_year_filing_segment(path_parts[-1]):
            roots.extend(await self._discover_schedule_a_children(client, token, root_id, path_parts))
            return roots
        if path_parts and self._is_5500_filing_folder_segment(path_parts[-1]):
            roots.extend(await self._discover_year_filing_children(client, token, root_id, path_parts))
            return roots

        try:
            children = await self._list_folder(client, token, root_id)
        except httpx.HTTPError:
            return roots
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if not item_id or not name or not self._is_folder(item):
                continue
            item_path = path_parts + [str(name)]
            if self._is_5500_filing_folder_segment(str(name)):
                filing_root = self._scan_root(item_id, "Discovered ShareFile 5500 filing folder", item_path)
                roots.append(filing_root)
                roots.extend(await self._discover_year_filing_children(client, token, item_id, item_path))
        return roots

    async def _discover_year_filing_children(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        folder_id: str,
        path_parts: list[str],
    ) -> list[dict]:
        try:
            children = await self._list_folder(client, token, folder_id)
        except httpx.HTTPError:
            # Includes network-level failures (timeouts, dropped connections),
            # which must not abort folder discovery.
            return []

        roots: list[dict] = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if not item_id or not name or not self._is_folder(item):
                continue
            item_path = path_parts + [str(name)]
            if self._is_year_filing_segment(str(name)):
                year_root = self._scan_root(item_id, "Discovered ShareFile year filing folder", item_path)
                roots.append(year_root)
                roots.extend(await self._discover_schedule_a_children(client, token, item_id, item_path))
        return roots

    async def _discover_schedule_a_children(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        folder_id: str,
        path_parts: list[str],
    ) -> list[dict]:
        try:
            children = await self._list_folder(client, token, folder_id)
        except httpx.HTTPError:
            # Includes network-level failures (timeouts, dropped connections),
            # which must not abort folder discovery.
            return []

        roots: list[dict] = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if not item_id or not name or not self._is_folder(item):
                continue
            if self._is_schedule_a_folder_segment(str(name)):
                item_path = path_parts + [str(name)]
                roots.append(self._scan_root(item_id, "Discovered ShareFile Schedule A folder", item_path))
                roots.extend(await self._discover_webhook_descendant_roots(client, token, item_id, item_path))
        return roots

    async def _discover_webhook_descendant_roots(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        folder_id: str,
        path_parts: list[str],
        depth: int = 0,
        max_depth: int = 3,
    ) -> list[dict]:
        if depth >= max_depth:
            return []
        try:
            children = await self._list_folder(client, token, folder_id)
        except httpx.HTTPError:
            # Includes network-level failures (timeouts, dropped connections),
            # which must not abort folder discovery.
            return []

        roots: list[dict] = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if not item_id or not name or not self._is_folder(item):
                continue
            item_path = path_parts + [str(name)]
            roots.append(self._scan_root(item_id, "Discovered nested ShareFile webhook folder", item_path))
            roots.extend(
                await self._discover_webhook_descendant_roots(
                    client,
                    token,
                    item_id,
                    item_path,
                    depth + 1,
                    max_depth,
                )
            )
        return roots

    async def _register_missing_webhook_roots(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        webhook_url: str,
        webhook_roots: list[dict],
        existing: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        registered = []
        skipped = []
        failed = []
        for root in webhook_roots:
            root_id = root["id"]
            path = " > ".join(root.get("path_parts") or []) or root.get("name") or root_id
            if self._has_matching_webhook(existing, webhook_url, root_id):
                skipped.append({"folder_id": root_id, "path": path, "reason": "already registered"})
                continue
            try:
                subscription = await self._create_webhook_subscription(client, token, webhook_url, root_id)
                registered.append({"folder_id": root_id, "path": path, "subscription": subscription})
            except httpx.HTTPStatusError as exc:
                failed.append(
                    {
                        "folder_id": root_id,
                        "path": path,
                        "status_code": exc.response.status_code,
                        "response": exc.response.text[:500],
                    }
                )
            except Exception as exc:
                failed.append({"folder_id": root_id, "path": path, "error": str(exc)})
        return registered, skipped, failed

    async def _create_webhook_subscription(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        webhook_url: str,
        folder_id: str,
    ) -> dict:
        body = {
            "SubscriptionContext": {
                "ResourceType": "Folder",
                "ResourceId": folder_id,
            },
            "WebhookUrl": webhook_url,
            "Events": [
                {"ResourceType": "File", "OperationName": "Upload"},
                {"ResourceType": "File", "OperationName": "Update"},
                {"ResourceType": "File", "OperationName": "Delete"},
                {"ResourceType": "Folder", "OperationName": "Delete"},
            ],
        }
        response = await self._authorized_request(
            client,
            "POST",
            f"https://{token.subdomain}.{token.apicp}/sf/v3/WebhookSubscriptions",
            token,
            headers={"Content-Type": "application/json"},
            json=body,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()

    def _webhook_callback_url(self) -> str:
        settings = get_settings()
        if settings.sharefile_webhook_url:
            return settings.sharefile_webhook_url
        redirect_url = settings.sharefile_redirect_url or ""
        if "/oauth/callback" in redirect_url:
            return redirect_url.replace("/oauth/callback", "/webhook")
        return redirect_url.rstrip("/") + "/api/sharefile/webhook"

    def _has_matching_webhook(self, subscriptions: list[dict], webhook_url: str, folder_id: str) -> bool:
        for subscription in subscriptions:
            url = subscription.get("WebhookUrl") or subscription.get("webhookUrl") or subscription.get("Url")
            context = subscription.get("SubscriptionContext") or subscription.get("subscriptionContext") or {}
            resource_id = context.get("ResourceId") or context.get("resourceId")
            resource_type = context.get("ResourceType") or context.get("resourceType")
            if str(url).rstrip("/") == webhook_url.rstrip("/") and str(resource_id) == str(folder_id) and str(resource_type).lower() == "folder":
                return True
        return False

    async def _normalize_sharefile_item(self, client: httpx.AsyncClient, token: ShareFileOAuthToken, item: dict) -> dict:
        item_id = item.get("Id") or item.get("id")
        name = item.get("Name") or item.get("FileName") or item.get("fileName") or ""
        parent = item.get("Parent") if isinstance(item.get("Parent"), dict) else {}
        parent_id = item.get("ParentId") or item.get("parentId") or parent.get("Id")
        path_parts, folder_ids_by_depth = await self._resolve_item_path_context(client, token, item, name, parent_id)
        document_type = self._classify_sharefile_document(name, path_parts)
        if item_id and not document_type and self._should_content_sniff(name):
            document_type = await self._classify_sharefile_document_by_content(client, token, item_id, name)
        root_folder_id = item.get("root_folder_id")
        if not root_folder_id and folder_ids_by_depth:
            root_folder_id = folder_ids_by_depth.get(0) or folder_ids_by_depth.get("0")
        return {
            "id": item_id,
            "name": name,
            "path": " > ".join(path_parts),
            "path_parts": path_parts,
            "root_folder_id": root_folder_id or parent_id,
            "parent_id": parent_id,
            "folder_ids_by_depth": folder_ids_by_depth,
            "document_type": document_type,
            "size": int(item.get("FileSizeBytes") or item.get("size") or item.get("Size") or 0),
            "modified_at": item.get("ClientModifiedDate") or item.get("CreationDate") or item.get("modified_at"),
            "created_at": item.get("CreationDate") or item.get("created_at"),
            "raw": item,
        }

    async def _resolve_item_path_context(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        item: dict,
        name: str,
        parent_id: str | None,
    ) -> tuple[list[str], dict[int, str]]:
        path_parts = self._path_parts_from_item(item, name)
        folder_ids_by_depth = self._folder_ids_from_item(item, path_parts, parent_id)
        if self._has_year_filing_segment(path_parts) or not parent_id:
            return path_parts, folder_ids_by_depth

        ancestors: list[dict] = []
        seen: set[str] = set()
        current_id = parent_id
        while current_id and current_id not in seen and len(ancestors) < 25:
            seen.add(current_id)
            try:
                folder = await self._get_item(client, token, current_id)
            except Exception:
                break
            folder_name = folder.get("Name") or folder.get("FileName") or folder.get("fileName")
            if folder_name:
                ancestors.append({"id": current_id, "name": folder_name})
            parent = folder.get("Parent") if isinstance(folder.get("Parent"), dict) else {}
            current_id = folder.get("ParentId") or folder.get("parentId") or parent.get("Id")

        if not ancestors:
            return path_parts, folder_ids_by_depth

        ancestors.reverse()
        resolved_path_parts = [ancestor["name"] for ancestor in ancestors] + [name]
        resolved_folder_ids = {
            index: ancestor["id"]
            for index, ancestor in enumerate(ancestors)
            if ancestor.get("id")
        }
        return resolved_path_parts, resolved_folder_ids

    def _path_parts_from_item(self, item: dict, name: str) -> list[str]:
        for key in ("Path", "path", "FilePath", "filePath"):
            value = item.get(key)
            if isinstance(value, str) and value:
                parts = [part.strip() for part in re.split(r"[>/\\]+", value) if part.strip()]
                if parts and parts[-1].lower() != name.lower():
                    parts.append(name)
                return parts or [name]
        parent = item.get("Parent") if isinstance(item.get("Parent"), dict) else {}
        parent_name = parent.get("Name") or parent.get("FileName")
        return [part for part in [parent_name, name] if part]

    def _folder_ids_from_item(self, item: dict, path_parts: list[str], parent_id: str | None) -> dict[int, str]:
        if not parent_id or len(path_parts) < 2:
            return {}
        return {len(path_parts) - 2: parent_id}

    def _webhook_event_type(self, payload: dict) -> str:
        event = payload.get("Event") if isinstance(payload.get("Event"), dict) else {}
        value = (
            payload.get("EventType")
            or payload.get("event_type")
            or payload.get("event")
            or event.get("OperationName")
            or event.get("operationName")
            or payload.get("Type")
            or payload.get("type")
            or ""
        )
        return str(value)

    def _webhook_item_id(self, payload: dict) -> str | None:
        event = payload.get("Event") if isinstance(payload.get("Event"), dict) else {}
        resource = event.get("Resource") if isinstance(event.get("Resource"), dict) else {}
        candidates = [
            payload.get("ItemId"),
            payload.get("item_id"),
            payload.get("ItemID"),
            payload.get("id"),
            payload.get("ResourceId"),
            payload.get("resource_id"),
            resource.get("Id"),
            resource.get("id"),
            resource.get("ResourceId"),
            resource.get("resourceId"),
        ]
        item = payload.get("Item") if isinstance(payload.get("Item"), dict) else {}
        candidates.extend([item.get("Id"), item.get("id")])
        for value in candidates:
            if value:
                return str(value)
        return None

    def _is_delete_event(self, event_type: str | None) -> bool:
        text = str(event_type or "").lower()
        return any(marker in text for marker in ("delete", "deleted", "remove", "removed", "trash"))

    async def _create_filing_package(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        package_key: str,
        package_files: list[dict],
    ) -> tuple[Filing, ExtractionJob, list[dict]]:
        repo = get_repository()
        package_documents = []
        processing_documents = []
        total_size = 0

        for file_item in sorted(package_files, key=self._document_sort_key):
            downloaded_bytes = await self._download_item(client, token, file_item["id"])
            # An email is a wrapper around the real document, and legacy .xls
            # is a format the extractor cannot read - resolve both here so
            # everything downstream sees an ordinary readable file.
            for intake in normalize_intake_documents(file_item["name"], downloaded_bytes):
                file_bytes = intake.file_bytes
                content_type = self._content_type_for(intake.file_name)
                storage = StorageService().save_file(intake.file_name, content_type, file_bytes)
                document_type = file_item["document_type"]
                total_size += len(file_bytes)
                package_document = {
                    "file_name": intake.file_name,
                    "source_file_name": file_item["name"],
                    "intake_conversion": intake.conversion,
                    "intake_note": intake.note,
                    "content_type": content_type,
                    "file_size": len(file_bytes),
                    "document_type": document_type.value,
                    "s3_key": storage["key"],
                    "s3_bucket": storage.get("bucket"),
                    "storage_path": storage.get("local_path"),
                    "intake_source": "SHAREFILE",
                    "sharefile_item_id": file_item["id"],
                    "sharefile_parent_id": file_item["parent_id"],
                    "sharefile_path": file_item["path"],
                    "sharefile_modified_at": file_item.get("modified_at"),
                    "sharefile_created_at": file_item.get("created_at"),
                    "sharefile_downloaded_at": datetime.utcnow().isoformat(),
                    "package_key": package_key,
                    "package_root_key": self._package_root_key(file_item),
                    "client_name": self._client_name_for(file_item),
                    "filing_year": self._filing_year_for(file_item),
                    "metadata_signature": self._sharefile_metadata_signature(file_item),
                }
                package_documents.append(package_document)
                processing_documents.append(
                    {
                        "file_bytes": file_bytes,
                        # The extractor keys off the file name, so it must be
                        # the resolved attachment/body/converted workbook.
                        "file_name": intake.file_name,
                        "source_file_name": file_item["name"],
                        "file_size": len(file_bytes),
                        "content_type": content_type,
                        "document_type": document_type,
                        "sharefile_item_id": file_item["id"],
                        "sharefile_path": file_item["path"],
                    }
                )

        primary_file = next(
            (item for item in package_files if item["document_type"] == DocumentType.SCHEDULE_A),
            package_files[0],
        )
        primary_document = package_documents[0]
        filing_name = self._filing_name_for_package(package_key, package_files)
        filing = Filing(
            file_name=filing_name,
            content_type="application/vnd.erisapros.filing-package" if len(package_files) > 1 else primary_document["content_type"],
            file_size=total_size,
            document_type=primary_file["document_type"],
            package_document_count=len(package_documents),
            status=FilingStatus.QUEUED,
            s3_key=primary_document["s3_key"],
            s3_bucket=primary_document.get("s3_bucket"),
            storage_path=primary_document.get("storage_path"),
            package_documents=package_documents,
            intake_source="SHAREFILE",
            sharefile_item_id=primary_file["id"],
            sharefile_parent_id=primary_file["parent_id"],
            sharefile_downloaded_at=datetime.utcnow(),
        )
        filing = await repo.create_filing(filing)
        job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id))
        return filing, job, processing_documents

    async def _find_existing_package(self, package_files: list[dict]) -> Filing | None:
        repo = get_repository()
        identity_files = [item for item in package_files if item.get("document_type") == DocumentType.SCHEDULE_A] or package_files
        for file_item in identity_files:
            existing = await repo.get_filing_by_sharefile_item_id(file_item["id"])
            if existing and existing.status not in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                return existing
        return None

    async def _find_active_filing_by_package_key(self, package_key: str) -> Filing | None:
        repo = get_repository()
        for filing in await repo.list_filings():
            if not filing.id or filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                continue
            if package_key in self._filing_package_keys(filing):
                return filing
        return None

    def _filing_package_keys(self, filing: Filing) -> set[str]:
        keys = {
            str(document.get("package_key"))
            for document in filing.package_documents
            if document.get("package_key")
        }
        s3_key = str(filing.s3_key or "")
        if s3_key.startswith("sharefile-package/"):
            keys.add(s3_key.removeprefix("sharefile-package/"))
        return keys

    async def _find_waiting_filing_for_package(self, package_key: str, package_files: list[dict]) -> Filing | None:
        repo = get_repository()
        package_item_ids = {str(file_item.get("id")) for file_item in package_files if file_item.get("id")}
        for filing in await repo.list_filings():
            if not filing.id or filing.status not in {FilingStatus.WAITING_FOR_WORKSHEET, FilingStatus.WAITING_FOR_SCHEDULE_A}:
                continue
            filing_item_ids = {
                str(document.get("sharefile_item_id"))
                for document in filing.package_documents
                if document.get("sharefile_item_id")
            }
            filing_package_keys = {
                str(document.get("package_key"))
                for document in filing.package_documents
                if document.get("package_key")
            }
            if package_key in filing_package_keys or not filing_item_ids.isdisjoint(package_item_ids):
                return filing
        return None

    async def _supersede_active_filings_for_package(
        self,
        package_key: str,
        package_files: list[dict],
        message: str,
        source: str,
        exclude_filing_id: str | None = None,
    ) -> None:
        repo = get_repository()
        identity_files = [item for item in package_files if item.get("document_type") == DocumentType.SCHEDULE_A] or package_files
        package_item_ids = {str(file_item.get("id")) for file_item in identity_files if file_item.get("id")}
        for filing in await repo.list_filings():
            if not filing.id or filing.id == exclude_filing_id:
                continue
            if filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                continue
            filing_item_ids = {
                str(document.get("sharefile_item_id"))
                for document in filing.package_documents
                if document.get("sharefile_item_id")
            }
            if package_key not in self._filing_package_keys(filing) and filing_item_ids.isdisjoint(package_item_ids):
                continue
            await repo.update_filing(
                filing.id,
                {
                    "status": FilingStatus.SUPERSEDED,
                    "error_message": message,
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="SHAREFILE_PACKAGE_SUPERSEDED",
                    message=message,
                    details={"package_key": package_key, "source": source},
                )
            )

    async def _supersede_waiting_filings_for_package(
        self,
        package_key: str,
        package_files: list[dict],
        message: str,
        source: str,
    ) -> None:
        repo = get_repository()
        package_item_ids = {str(file_item.get("id")) for file_item in package_files if file_item.get("id")}
        for filing in await repo.list_filings():
            if not filing.id or filing.status not in {FilingStatus.WAITING_FOR_WORKSHEET, FilingStatus.WAITING_FOR_SCHEDULE_A}:
                continue
            filing_item_ids = {
                str(document.get("sharefile_item_id"))
                for document in filing.package_documents
                if document.get("sharefile_item_id")
            }
            filing_package_keys = {
                str(document.get("package_key"))
                for document in filing.package_documents
                if document.get("package_key")
            }
            if package_key not in filing_package_keys and filing_item_ids.isdisjoint(package_item_ids):
                continue
            await repo.update_filing(
                filing.id,
                {
                    "status": FilingStatus.SUPERSEDED,
                    "error_message": message,
                },
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="SHAREFILE_WAITING_PACKAGE_COMPLETED",
                    message="Waiting package was completed by a matching document and superseded by an extraction package.",
                    details={"package_key": package_key, "source": source},
                )
            )

    def _package_changed(self, existing: Filing, package_files: list[dict]) -> bool:
        existing_documents = {
            str(document.get("sharefile_item_id")): document
            for document in existing.package_documents
            if document.get("sharefile_item_id")
        }
        if len(existing_documents) != len(package_files):
            return True
        for file_item in package_files:
            existing_document = existing_documents.get(file_item["id"])
            if not existing_document:
                return True
            existing_signature = str(existing_document.get("metadata_signature") or "")
            current_signature = self._sharefile_metadata_signature(file_item)
            if existing_signature and existing_signature != current_signature:
                return True
            if int(existing_document.get("file_size") or 0) != int(file_item.get("size") or 0):
                return True
            existing_modified = str(existing_document.get("sharefile_modified_at") or "")
            current_modified = str(file_item.get("modified_at") or "")
            if existing_modified and current_modified and existing_modified != current_modified:
                return True
        return False

    async def _mark_deleted_sharefile_filings(self, active_sharefile_item_ids: set[str]) -> int:
        repo = get_repository()
        deleted = 0
        for filing in await repo.list_filings():
            if filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
                continue
            if filing.intake_source != "SHAREFILE" and not filing.sharefile_item_id:
                continue

            filing_item_ids = {
                str(document.get("sharefile_item_id"))
                for document in filing.package_documents
                if document.get("sharefile_item_id")
            }
            if filing.sharefile_item_id:
                filing_item_ids.add(filing.sharefile_item_id)

            if filing.id and filing_item_ids and filing_item_ids.isdisjoint(active_sharefile_item_ids):
                await repo.update_filing(
                    filing.id,
                    {
                        "status": FilingStatus.DELETED,
                        "error_message": "ShareFile source file is no longer present in the scanned folder tree.",
                    },
                )
                await repo.add_audit(
                    AuditLog(
                        filing_id=filing.id,
                        event="SHAREFILE_SOURCE_DELETED",
                        message="ShareFile source document is no longer present. Filing package was marked deleted.",
                        details={"sharefile_item_ids": sorted(filing_item_ids)},
                    )
                )
                deleted += 1
        return deleted

    async def _scan_folder(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        folder_id: str,
        path_parts: list[str],
        depth: int = 0,
        root_folder_id: str | None = None,
        scan_errors: list[dict] | None = None,
        folder_ids_by_depth: dict[int, str] | None = None,
        descend=None,
        folder_sink: list[dict] | None = None,
    ) -> list[dict]:
        """Walk a folder tree and return the files in it.

        ``descend`` lets a caller walk only part of the tree: it is asked, for
        each subfolder, whether to go into it. The quick scan uses this to
        visit the filing structure only, instead of every folder a client has.
        ``folder_sink`` collects every folder seen along the way.
        """
        if not folder_id or depth > MAX_SHAREFILE_SCAN_DEPTH:
            return []
        root_folder_id = root_folder_id or folder_id
        folder_ids_by_depth = dict(folder_ids_by_depth or {})
        if path_parts:
            folder_ids_by_depth[len(path_parts) - 1] = folder_id

        semaphore = getattr(self, "_scan_semaphore", None)
        if semaphore is None:
            semaphore = self._scan_semaphore = asyncio.Semaphore(8)
        try:
            async with semaphore:
                children = await self._list_folder(client, token, folder_id)
        except httpx.HTTPError as exc:
            # One slow or broken folder must never abort the whole scan.
            # HTTPError covers both ShareFile error responses (HTTPStatusError)
            # and network-level failures such as timeouts and dropped
            # connections. Record the folder and continue with the rest.
            if scan_errors is not None:
                is_status_error = isinstance(exc, httpx.HTTPStatusError)
                scan_errors.append(
                    {
                        "folder_id": folder_id,
                        "path": " > ".join(path_parts) or folder_id,
                        "status_code": exc.response.status_code if is_status_error else None,
                        "response": exc.response.text[:300] if is_status_error else f"{type(exc).__name__}: {exc}"[:300],
                    }
                )
            return []

        files: list[dict] = []
        subfolder_scans = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or ""
            if not item_id or not name:
                continue
            item_path = path_parts + [name]
            if self._is_folder(item):
                if folder_sink is not None:
                    folder_sink.append(
                        {
                            "id": item_id,
                            "name": name,
                            "source": "ShareFile quick scan",
                            "path_parts": item_path,
                            "parent_id": folder_id,
                        }
                    )
                if descend is not None and not descend(name, item_path, depth + 1):
                    continue
                # Walk sibling subfolders concurrently - a sequential walk of
                # hundreds of folders took many minutes per scan.
                subfolder_scans.append(
                    self._scan_folder(
                        client,
                        token,
                        item_id,
                        item_path,
                        depth + 1,
                        root_folder_id,
                        scan_errors,
                        folder_ids_by_depth,
                        descend,
                        folder_sink,
                    )
                )
                continue

            document_type = self._classify_sharefile_document(name, item_path)
            # Content sniffing (download + parse) is deferred until after the
            # scan so a full recursive walk of every client folder stays fast.
            # See _resolve_deferred_content_sniffs.
            needs_content_sniff = bool(not document_type and self._should_content_sniff(name))
            files.append(
                {
                    "id": item_id,
                    "name": name,
                    "needs_content_sniff": needs_content_sniff,
                    "path": " > ".join(item_path),
                    "path_parts": item_path,
                    "root_folder_id": root_folder_id,
                    "parent_id": folder_id,
                    "folder_ids_by_depth": folder_ids_by_depth,
                    "document_type": document_type,
                    "size": int(item.get("FileSizeBytes") or 0),
                    "modified_at": item.get("ClientModifiedDate") or item.get("CreationDate"),
                    "created_at": item.get("CreationDate"),
                    "raw": item,
                }
            )

        if subfolder_scans:
            results = await asyncio.gather(*subfolder_scans, return_exceptions=True)
            for scanned in results:
                if isinstance(scanned, BaseException):
                    if scan_errors is not None:
                        scan_errors.append(
                            {
                                "folder_id": folder_id,
                                "path": " > ".join(path_parts) or folder_id,
                                "status_code": None,
                                "response": f"{type(scanned).__name__}: {scanned}"[:300],
                            }
                        )
                    continue
                files.extend(scanned)
        return files

    def _group_files_by_package(self, files: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for file_item in files:
            groups[self._package_key(file_item)].append(file_item)
        return dict(groups)

    def _dedupe_scanned_files(self, files: list[dict]) -> list[dict]:
        deduped: dict[str, dict] = {}
        for file_item in files:
            item_id = file_item.get("id")
            if not item_id or item_id in deduped:
                continue
            deduped[item_id] = file_item
        return list(deduped.values())

    def _package_key(self, file_item: dict) -> str:
        document_type = file_item.get("document_type")
        if document_type == DocumentType.SCHEDULE_A:
            return self._schedule_package_key(file_item)
        if document_type == DocumentType.PLAN_WORKSHEET:
            return self._worksheet_waiting_package_key(file_item)
        return self._package_root_key(file_item)

    def _package_root_key(self, file_item: dict) -> str:
        folders = list(file_item["path_parts"][:-1])
        if not folders:
            return file_item.get("root_folder_id") or get_settings().sharefile_intake_folder_id or "sharefile-root"

        for index in range(len(folders) - 1, -1, -1):
            segment = folders[index].lower()
            if self._is_year_filing_segment(segment) or segment.endswith(" filing"):
                return " > ".join(folders[: index + 1])

        for index in range(len(folders) - 1, -1, -1):
            segment = folders[index].lower()
            if "schedule" in segment and "a" in segment:
                return " > ".join(folders[:index]) or " > ".join(folders)

        return " > ".join(folders)

    def _schedule_package_key(self, file_item: dict) -> str:
        item_id = str(file_item.get("id") or self._sharefile_metadata_signature(file_item) or file_item.get("name") or "schedule-a")
        return f"{self._package_root_key(file_item)} > Schedule A::{item_id}"

    def _worksheet_waiting_package_key(self, file_item: dict) -> str:
        return f"{self._package_root_key(file_item)} > Waiting for Schedule A"

    def _package_root_key_from_package_key(self, package_key: str) -> str:
        if " > Schedule A::" in package_key:
            return package_key.split(" > Schedule A::", 1)[0]
        if package_key.endswith(" > Waiting for Schedule A"):
            return package_key.removesuffix(" > Waiting for Schedule A")
        return package_key

    def _package_display_name(self, package_key: str) -> str:
        root_key = self._package_root_key_from_package_key(package_key)
        return root_key.split(" > ")[-1] if root_key else "ShareFile filing package"

    def _filing_name_for_package(self, package_key: str, package_files: list[dict]) -> str:
        schedule = next((item for item in package_files if item.get("document_type") == DocumentType.SCHEDULE_A), None)
        document_label = "document" if len(package_files) == 1 else "documents"
        if schedule:
            return f"{schedule.get('name') or 'Schedule A'} ({len(package_files)} {document_label})"
        return f"{self._package_display_name(package_key)} ({len(package_files)} {document_label})"

    def _client_name_for(self, file_item: dict) -> str | None:
        path_parts = [part for part in file_item.get("path_parts", []) if part]
        if not path_parts:
            return None
        year_indexes = [
            index
            for index, part in enumerate(path_parts)
            if self._is_year_filing_segment(part)
        ]
        if year_indexes:
            index = year_indexes[-1]
            if index > 1 and "5500" in path_parts[index - 1].lower():
                return path_parts[index - 2]
            return path_parts[index - 1] if index > 0 else path_parts[0]
        for index, part in enumerate(path_parts):
            if "5500" in part.lower() and index > 0:
                return path_parts[index - 1]
        return path_parts[0]

    def _filing_year_for(self, file_item: dict) -> str | None:
        for part in reversed(file_item.get("path_parts", [])):
            match = re.search(r"\b(20\d{2})\b", str(part))
            if match:
                return match.group(1)
        return None

    def _has_year_filing_segment(self, path_parts: list[str]) -> bool:
        return any(self._is_year_filing_segment(part) for part in path_parts)

    def _is_year_filing_segment(self, value: str) -> bool:
        text = str(value or "").lower()
        return bool(re.search(r"\b20\d{2}\b", text) and "filing" in text)

    def _is_5500_filing_folder_segment(self, value: str) -> bool:
        text = str(value or "").lower()
        return "5500" in text and "filing" in text

    def _is_schedule_a_folder_segment(self, value: str) -> bool:
        compact = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
        return compact in {"schedulea", "scheduleas"}

    def _configured_folder_ids(self) -> list[str]:
        raw_value = get_settings().sharefile_intake_folder_id or ""
        return [part.strip() for part in re.split(r"[,;\s]+", raw_value) if part.strip()]

    def _configured_folder_path_parts(self) -> list[str]:
        raw_value = get_settings().sharefile_intake_folder_path or ""
        return [part.strip() for part in raw_value.split(">") if part.strip()]

    def _scan_roots_configured(self) -> bool:
        settings = get_settings()
        return bool(
            settings.sharefile_discover_shared_folders
            or settings.sharefile_shared_root_folder_id
            or self._configured_folder_ids()
        )

    def _scan_scope_label(self) -> str:
        settings = get_settings()
        if settings.sharefile_discover_shared_folders:
            return "All shared client folders"
        if settings.sharefile_shared_root_folder_id:
            return "Configured shared root folder"
        if self._configured_folder_ids():
            return "Configured folder IDs"
        return "Not configured"

    async def _resolve_scan_roots(self, client: httpx.AsyncClient, token: ShareFileOAuthToken) -> list[dict]:
        settings = get_settings()
        roots: list[dict] = []

        if settings.sharefile_discover_shared_folders:
            roots.extend(await self._discover_shared_folder_roots(client, token))

        if settings.sharefile_shared_root_folder_id:
            roots.append(
                self._scan_root(
                    settings.sharefile_shared_root_folder_id,
                    "Configured shared root",
                    self._configured_folder_path_parts(),
                )
            )

        fallback_path = self._configured_folder_path_parts()
        for folder_id in self._configured_folder_ids():
            roots.append(self._scan_root(folder_id, "Configured fallback folder", fallback_path))

        deduped: list[dict] = []
        seen: set[str] = set()
        for root in roots:
            root_id = root["id"]
            if root_id in seen:
                continue
            seen.add(root_id)
            deduped.append(root)
        return deduped

    async def _discover_shared_folder_roots(self, client: httpx.AsyncClient, token: ShareFileOAuthToken) -> list[dict]:
        try:
            children = await self._list_folder(client, token, "allshared")
        except httpx.HTTPError:
            # A hiccup listing the shared root must not abort the whole sync;
            # the configured fallback folder IDs still provide scan roots.
            return []
        roots: list[dict] = []
        for item in children:
            item_id = item.get("Id")
            name = item.get("Name") or item.get("FileName") or item_id
            if item_id and name and self._is_folder(item):
                roots.append(self._scan_root(item_id, "ShareFile allshared", [name]))
        return roots

    def _scan_root(self, folder_id: str, source: str, path_parts: list[str] | None = None) -> dict:
        cleaned_path = [part for part in (path_parts or []) if part]
        name = cleaned_path[-1] if cleaned_path else ("Shared Folders" if folder_id == "allshared" else folder_id)
        return {
            "id": folder_id,
            "name": name,
            "source": source,
            "path_parts": [] if folder_id == "allshared" else cleaned_path,
        }

    async def _ensure_access_token(self, client: httpx.AsyncClient, token: ShareFileOAuthToken) -> ShareFileOAuthToken:
        if token.expires_at and token.expires_at > datetime.utcnow() + timedelta(minutes=2):
            return token
        if not token.refresh_token:
            return token

        return await self._refresh_access_token(client, token)

    async def _refresh_access_token(self, client: httpx.AsyncClient, token: ShareFileOAuthToken) -> ShareFileOAuthToken:
        if not token.refresh_token:
            return token

        token_response = await client.post(
            f"https://{token.subdomain}.{token.appcp}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": get_settings().sharefile_client_id,
                "client_secret": get_settings().sharefile_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response.raise_for_status()
        refreshed = self._token_from_payload(token_response.json(), token.subdomain, token.apicp, token.appcp)
        refreshed = await get_repository().upsert_sharefile_token(refreshed)

        # Keep the caller's token object current. Several ShareFile operations reuse
        # the same model after a request, and ShareFile may revoke an access token
        # before its advertised expiry time.
        token.access_token = refreshed.access_token
        token.refresh_token = refreshed.refresh_token
        token.token_type = refreshed.token_type
        token.expires_at = refreshed.expires_at
        token.updated_at = refreshed.updated_at
        return token

    async def _authorized_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        token: ShareFileOAuthToken,
        **kwargs,
    ) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})

        async def send() -> httpx.Response:
            return await client.request(
                method,
                url,
                headers={**self._auth_headers(token), **extra_headers},
                **kwargs,
            )

        response = await send()
        if response.status_code == 401 and token.refresh_token:
            await self._refresh_access_token(client, token)
            response = await send()
        return response

    async def _list_folder(self, client: httpx.AsyncClient, token: ShareFileOAuthToken, folder_id: str) -> list[dict]:
        folder_url = f"https://{token.subdomain}.{token.apicp}/sf/v3/Items({folder_id})/Children"
        items: list[dict] = []
        next_url: str | None = folder_url
        while next_url:
            response = await self._authorized_request(
                client,
                "GET",
                next_url,
                token,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            items.extend(self._extract_items(payload))
            next_link = self._extract_next_link(payload)
            next_url = self._absolute_sharefile_url(token, next_link) if next_link else None
        return items

    def _extract_items(self, payload: dict | list) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("value", "Values", "Items", "Children"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _extract_next_link(self, payload: dict | list) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("odata.nextLink", "__next", "nextLink", "NextLink"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        metadata = payload.get("__metadata")
        if isinstance(metadata, dict):
            value = metadata.get("nextLink") or metadata.get("NextLink")
            if isinstance(value, str) and value:
                return value
        return None

    def _absolute_sharefile_url(self, token: ShareFileOAuthToken, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not url.startswith("/"):
            url = "/" + url
        return f"https://{token.subdomain}.{token.apicp}{url}"

    async def _download_item(self, client: httpx.AsyncClient, token: ShareFileOAuthToken, item_id: str) -> bytes:
        download_url = f"https://{token.subdomain}.{token.apicp}/sf/v3/Items({item_id})/Download"
        response = await self._authorized_request(
            client,
            "GET",
            download_url,
            token,
            follow_redirects=False,
        )
        redirect_url = response.headers.get("Location")
        if response.is_redirect and redirect_url:
            download_response = await client.get(redirect_url, follow_redirects=True)
            download_response.raise_for_status()
            return download_response.content

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            payload = response.json()
            nested_url = self._extract_download_url(payload)
            if nested_url:
                download_response = await client.get(nested_url, follow_redirects=True)
                download_response.raise_for_status()
                return download_response.content

        return response.content

    def _classify_sharefile_document(self, file_name: str, path_parts: list[str]) -> DocumentType | None:
        name = file_name.lower()
        compact_name = re.sub(r"[^a-z0-9]+", "", name)
        folder_text = " ".join(path_parts[:-1]).lower()
        context_text = f"{name} {folder_text}"
        is_pdf = name.endswith(".pdf")
        is_word = name.endswith((".doc", ".docx"))
        # Clients send Schedule A documents as spreadsheets, scans and email
        # attachments too, not only as PDFs. Anything the extractor can read
        # (after conversion where needed) is a candidate; the file still has
        # to look like a Schedule A by name or by the folder it sits in.
        is_intake_document = is_supported_intake_file(name)

        # An explicit Schedule A filename is stronger evidence than a nearby
        # folder or suffix containing the generic word "worksheet".
        looks_like_schedule = "schedulea" in compact_name or "schedule a" in name
        if is_intake_document and looks_like_schedule:
            return DocumentType.SCHEDULE_A

        looks_like_plan_worksheet = (
            ("worksheet" in context_text and ("plan" in context_text or "5500" in context_text))
            or "planworksheet" in compact_name
            or "5500planworksheet" in compact_name
        )
        if (is_word or is_pdf) and looks_like_plan_worksheet:
            return DocumentType.PLAN_WORKSHEET

        excluded_schedule_names = ("cover", "dnu", "signature", "signed", "sar", "acknowledgement", "draft")
        in_schedule_folder = "schedule" in folder_text and "a" in folder_text
        if is_intake_document and in_schedule_folder and not any(term in name for term in excluded_schedule_names):
            return DocumentType.SCHEDULE_A

        if is_pdf and ("form5500" in compact_name or "form 5500" in name or "5500" in name):
            return DocumentType.UNKNOWN
        return None

    def _should_content_sniff(self, file_name: str) -> bool:
        # Only formats we can cheaply read text from locally. Spreadsheets,
        # scans and emails are classified by name and folder instead of being
        # downloaded during the scan.
        name = file_name.lower()
        return name.endswith((".pdf", ".doc", ".docx"))

    async def _classify_sharefile_document_by_content(
        self,
        client: httpx.AsyncClient,
        token: ShareFileOAuthToken,
        item_id: str,
        file_name: str,
    ) -> DocumentType | None:
        try:
            file_bytes = await self._download_item(client, token, item_id)
        except Exception:
            return None

        text = ""
        name = file_name.lower()
        try:
            if name.endswith(".pdf"):
                pages = extract_pdf_text_pages(file_bytes)
                text = "\n".join(page_text for _, page_text in pages[:2])
            elif name.endswith((".doc", ".docx")):
                text = extract_docx_text(file_bytes)
        except Exception:
            # A malformed or mislabeled document must not abort the recursive
            # ShareFile scan and prevent later valid Schedule A files from intake.
            return None
        return self._classify_document_text(text)

    def _classify_document_text(self, text: str) -> DocumentType | None:
        normalized = re.sub(r"\s+", " ", text or "").lower()
        if not normalized:
            return None

        worksheet_markers = [
            "plan sponsor name",
            "plan sponsor address",
            "plan sponsor phone",
            "plan number(s)",
            "original erisa plan effective date",
            "total number of participants",
            "individual signing as plan administrator",
            "fully-insured benefits",
        ]
        schedule_a_markers = [
            "schedule a",
            "insurance information",
            "name of insurance carrier",
            "name of insurance company",
            "persons receiving commissions",
            "total premiums or subscription charges",
            "policy or contract year",
            "naic code",
        ]
        worksheet_score = sum(1 for marker in worksheet_markers if marker in normalized)
        schedule_score = sum(1 for marker in schedule_a_markers if marker in normalized)

        if worksheet_score >= 2 and worksheet_score > schedule_score:
            return DocumentType.PLAN_WORKSHEET
        if schedule_score >= 2 and schedule_score >= worksheet_score:
            return DocumentType.SCHEDULE_A
        return None

    def _document_sort_key(self, file_item: dict) -> tuple[int, str]:
        document_type = file_item["document_type"]
        rank = 0 if document_type == DocumentType.PLAN_WORKSHEET else 1
        return rank, file_item["name"].lower()

    def _token_from_payload(self, payload: dict, subdomain: str, apicp: str, appcp: str) -> ShareFileOAuthToken:
        expires_in = payload.get("expires_in")
        expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None
        return ShareFileOAuthToken(
            subdomain=subdomain,
            apicp=apicp,
            appcp=appcp,
            token_type=payload.get("token_type") or "Bearer",
            access_token=payload.get("access_token") or "",
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
        )

    def _auth_headers(self, token: ShareFileOAuthToken) -> dict:
        return {"Authorization": f"Bearer {token.access_token}"}

    def _is_folder(self, item: dict) -> bool:
        item_type = str(item.get("ItemType") or item.get("Type") or item.get("__type") or item.get("odata.type") or "").lower()
        if "folder" in item_type:
            return True
        name = item.get("Name") or item.get("FileName") or ""
        return bool(name and "." not in name.rsplit("/", 1)[-1])

    def _content_type_for(self, file_name: str) -> str:
        name = file_name.lower()
        if name.endswith(".docx"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if name.endswith(".doc"):
            return "application/msword"
        if name.endswith(".xlsx"):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if name.endswith(".xls"):
            return "application/vnd.ms-excel"
        if name.endswith(".csv"):
            return "text/csv"
        if name.endswith(".txt"):
            return "text/plain"
        if name.endswith(".png"):
            return "image/png"
        if name.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if name.endswith((".tif", ".tiff")):
            return "image/tiff"
        if name.endswith(".msg"):
            return "application/vnd.ms-outlook"
        if name.endswith(".eml"):
            return "message/rfc822"
        return "application/pdf"

    def _extract_download_url(self, payload: dict | list | str | None) -> str | None:
        if isinstance(payload, str):
            return payload if payload.startswith("http") else None
        if isinstance(payload, list):
            return next((url for item in payload if (url := self._extract_download_url(item))), None)
        if not isinstance(payload, dict):
            return None
        for key in ("DownloadUrl", "downloadUrl", "DownloadURL", "url", "Url", "uri", "Uri", "value"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, (dict, list)):
                nested = self._extract_download_url(value)
                if nested:
                    return nested
        return None
