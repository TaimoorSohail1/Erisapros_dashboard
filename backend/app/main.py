import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api import field_rules, filings, ftwilliams, sharefile
from app.auth import AuthenticationError, verify_cognito_id_token
from app.config import get_settings
from app.services.sharefile import ShareFileService


logger = logging.getLogger(__name__)


async def sharefile_poll_loop(interval_seconds: int) -> None:
    interval = max(interval_seconds, 60)
    while True:
        await asyncio.sleep(interval)
        try:
            await ShareFileService().poll_folder()
        except Exception:
            logger.exception("ShareFile background poll failed.")


async def sharefile_webhook_auto_register_loop(interval_seconds: int) -> None:
    interval = max(interval_seconds, 300)
    while True:
        try:
            await ShareFileService().auto_register_relevant_webhooks()
        except Exception:
            logger.exception("ShareFile webhook auto-registration failed.")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_runtime()
    poll_task = None
    webhook_auto_register_task = None
    if settings.sharefile_poll_enabled:
        poll_task = asyncio.create_task(sharefile_poll_loop(settings.sharefile_poll_interval_seconds))
        app.state.sharefile_poll_task = poll_task
        logger.info("ShareFile background polling enabled every %s seconds.", settings.sharefile_poll_interval_seconds)
    if settings.sharefile_webhook_auto_register_enabled:
        webhook_auto_register_task = asyncio.create_task(
            sharefile_webhook_auto_register_loop(settings.sharefile_webhook_discovery_interval_seconds)
        )
        app.state.sharefile_webhook_auto_register_task = webhook_auto_register_task
        logger.info(
            "ShareFile webhook auto-registration enabled every %s seconds.",
            settings.sharefile_webhook_discovery_interval_seconds,
        )
    try:
        yield
    finally:
        for task in (poll_task, webhook_auto_register_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="ERISAPros Schedule A API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_PATHS = {
    "/health",
    "/api/health",
    "/api/sharefile/oauth/callback",
    "/api/sharefile/webhook",
}


@app.middleware("http")
async def require_login(request: Request, call_next):
    settings = get_settings()
    if not settings.auth_enabled or request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return JSONResponse(status_code=401, content={"detail": "Login is required."})

    try:
        request.state.user = await verify_cognito_id_token(token, settings)
    except AuthenticationError as exc:
        return JSONResponse(status_code=401, content={"detail": str(exc)})
    return await call_next(request)

app.include_router(filings.router)
app.include_router(field_rules.router)
app.include_router(ftwilliams.router)
app.include_router(sharefile.router)
app.include_router(filings.router, prefix="/api")
app.include_router(field_rules.router, prefix="/api")
app.include_router(ftwilliams.router, prefix="/api")
app.include_router(sharefile.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "stack": "react-python-mongodb"}


@app.get("/api/health")
async def api_health():
    return await health()
