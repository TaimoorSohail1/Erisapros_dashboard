import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from app.config import get_settings
from app.services.sharefile import ShareFileService
from app.services.sharefile_processes import (
    start_sharefile_poll_process,
    start_sharefile_sync_process,
    start_sharefile_webhook_registration_process,
)
from app.services.sharefile_queue import enqueue_sharefile_work

router = APIRouter(prefix="/sharefile", tags=["sharefile"])
logger = logging.getLogger(__name__)


def valid_webhook_token(request: Request) -> bool:
    settings = get_settings()
    expected = settings.sharefile_webhook_token
    if not expected:
        return not settings.is_production
    supplied = request.query_params.get("token") or request.headers.get("x-sharefile-webhook-token", "")
    return hmac.compare_digest(supplied, expected)


@router.get("/status")
async def sharefile_status():
    return await ShareFileService().status()


@router.get("/scan-status")
async def sharefile_scan_status():
    return await ShareFileService().scan_status()


@router.post("/sync-folder")
async def sync_sharefile_folder():
    settings = get_settings()
    queued = (
        await enqueue_sharefile_work("deep_sync")
        if settings.sharefile_work_queue_url
        else start_sharefile_sync_process()
    )
    return {
        "connected": True,
        "folder_access": True,
        "found": 0,
        "supported": 0,
        "packages": 0,
        "synced": 0,
        "skipped": 0,
        "queued": queued,
        "message": "ShareFile deep sync started." if queued else "A ShareFile scan is already running.",
    }


@router.post("/poll")
async def poll_sharefile_folder():
    settings = get_settings()
    queued = (
        await enqueue_sharefile_work("poll")
        if settings.sharefile_work_queue_url
        else start_sharefile_poll_process()
    )
    return {
        "connected": True,
        "folder_access": True,
        "found": 0,
        "supported": 0,
        "packages": 0,
        "synced": 0,
        "skipped": 0,
        "queued": queued,
        "message": "ShareFile poll started." if queued else "A ShareFile scan is already running.",
    }


@router.post("/poll-scheduled", status_code=202)
async def poll_sharefile_folder_scheduled(request: Request):
    """Machine-to-machine poll trigger for the external scheduler (EventBridge).

    Authenticated with the shared ShareFile webhook token instead of a user
    login, so AWS can call it every few minutes as a reliable backstop for
    missed webhooks and brand-new client folders.
    """
    if not valid_webhook_token(request):
        raise HTTPException(status_code=401, detail="Invalid scheduler token.")
    settings = get_settings()
    queued = (
        await enqueue_sharefile_work("poll")
        if settings.sharefile_work_queue_url
        else start_sharefile_poll_process()
    )
    return {
        "accepted": True,
        "queued": queued,
        "message": (
            "ShareFile scan accepted for background processing."
            if queued
            else "ShareFile scan is already running."
        ),
    }


@router.post("/webhook")
async def sharefile_webhook(request: Request, background_tasks: BackgroundTasks):
    if not valid_webhook_token(request):
        raise HTTPException(status_code=401, detail="Invalid ShareFile webhook token.")
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    if get_settings().sharefile_work_queue_url:
        queued = await enqueue_sharefile_work("webhook", payload)
        return {
            "accepted": queued,
            "queued": 1 if queued else 0,
            "message": "ShareFile webhook queued for isolated processing.",
        }
    return await ShareFileService().handle_webhook(payload, background_tasks)


@router.get("/webhooks")
async def list_sharefile_webhooks():
    return await ShareFileService().list_webhooks()


@router.post("/webhooks/register")
async def register_sharefile_webhooks():
    return await ShareFileService().register_webhooks()


@router.post("/webhooks/auto-register")
async def auto_register_sharefile_webhooks():
    settings = get_settings()
    queued = (
        await enqueue_sharefile_work("auto_register")
        if settings.sharefile_work_queue_url
        else start_sharefile_webhook_registration_process()
    )
    return {
        "queued": queued,
        "message": (
            "ShareFile webhook discovery started."
            if queued
            else "A ShareFile maintenance task is already running."
        ),
    }


@router.get("/oauth/start")
async def sharefile_oauth_start():
    return ShareFileService().authorization_url()


@router.get("/oauth/callback")
async def sharefile_oauth_callback(
    code: str | None = Query(default=None),
    subdomain: str | None = Query(default=None),
    apicp: str | None = Query(default=None),
    appcp: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"ShareFile OAuth denied: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing ShareFile OAuth code.")
    return await ShareFileService().complete_oauth(
        code=code,
        subdomain=subdomain,
        apicp=apicp,
        appcp=appcp,
    )
