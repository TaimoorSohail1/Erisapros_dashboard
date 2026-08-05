from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from app.services.sharefile import ShareFileService

router = APIRouter(prefix="/sharefile", tags=["sharefile"])


@router.get("/status")
async def sharefile_status():
    return await ShareFileService().status()


@router.post("/sync-folder")
async def sync_sharefile_folder(background_tasks: BackgroundTasks):
    return await ShareFileService().sync_folder(background_tasks)


@router.post("/poll")
async def poll_sharefile_folder(background_tasks: BackgroundTasks):
    return await ShareFileService().poll_folder(background_tasks)


@router.post("/webhook")
async def sharefile_webhook(request: Request, background_tasks: BackgroundTasks):
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
