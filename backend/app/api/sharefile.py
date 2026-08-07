import asyncio
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from app.config import get_settings
from app.services.sharefile import ShareFileService

router = APIRouter(prefix="/sharefile", tags=["sharefile"])
logger = logging.getLogger(__name__)
_scheduled_poll_lock = asyncio.Lock()


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
async def sync_sharefile_folder(background_tasks: BackgroundTasks):
    return await ShareFileService().sync_folder(background_tasks)


@router.post("/poll")
async def poll_sharefile_folder(background_tasks: BackgroundTasks):
    return await ShareFileService().poll_folder(background_tasks)


async def run_scheduled_sharefile_poll() -> None:
    if _scheduled_poll_lock.locked():
        logger.info("Skipping scheduled ShareFile poll because the previous scan is still running.")
        return
    async with _scheduled_poll_lock:
        try:
            await ShareFileService().poll_folder(None)
        except Exception:
            logger.exception("Scheduled ShareFile poll failed.")


@router.post("/poll-scheduled", status_code=202)
async def poll_sharefile_folder_scheduled(request: Request, background_tasks: BackgroundTasks):
    """Machine-to-machine poll trigger for the external scheduler (EventBridge).

    Authenticated with the shared ShareFile webhook token instead of a user
    login, so AWS can call it every few minutes as a reliable backstop for
    missed webhooks and brand-new client folders.
    """
    if not valid_webhook_token(request):
        raise HTTPException(status_code=401, detail="Invalid scheduler token.")
    background_tasks.add_task(run_scheduled_sharefile_poll)
    return {
        "accepted": True,
        "queued": True,
        "message": "ShareFile scan accepted for background processing.",
    }


@router.post("/webhook")
async def sharefile_webhook(request: Request, background_tasks: BackgroundTasks):
    if not valid_webhook_token(request):
        raise HTTPException(status_code=401, detail="Invalid ShareFile webhook token.")
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    return await ShareFileService().handle_webhook(payload, background_tasks)


@router.get("/webhooks")
async def list_sharefile_webhooks():
    return await ShareFileService().list_webhooks()


@router.post("/webhooks/register")
async def register_sharefile_webhooks():
    return await ShareFileService().register_webhooks()


@router.post("/webhooks/auto-register")
async def auto_register_sharefile_webhooks():
    return await ShareFileService().auto_register_relevant_webhooks()


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
