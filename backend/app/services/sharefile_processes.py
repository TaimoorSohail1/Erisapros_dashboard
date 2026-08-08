"""Run long ShareFile maintenance work outside the API server process.

ShareFile discovery can take many minutes for large client trees.  Running it
inside Uvicorn competes with dashboard requests for the same event loop and
memory, which previously caused CloudFront 504 responses.  A spawned process
also gets fresh Mongo/http clients instead of inheriting event-loop-bound
connections from the web process.
"""

import asyncio
import logging
import multiprocessing
import threading
from multiprocessing.process import BaseProcess


logger = logging.getLogger(__name__)
_process_lock = threading.Lock()
_maintenance_process: BaseProcess | None = None


def _run_sharefile_poll() -> None:
    from app.services.sharefile import ShareFileService

    asyncio.run(ShareFileService().poll_folder(None))


def _run_sharefile_deep_sync() -> None:
    from app.services.sharefile import ShareFileService

    asyncio.run(ShareFileService().sync_folder(None))


def _run_webhook_registration() -> None:
    from app.services.sharefile import ShareFileService

    asyncio.run(ShareFileService().auto_register_relevant_webhooks())


def _start_maintenance_process(target, name: str) -> bool:
    """Start one ShareFile maintenance process, or refuse an overlap."""
    global _maintenance_process

    with _process_lock:
        if _maintenance_process is not None:
            if _maintenance_process.is_alive():
                logger.info("Skipping %s because ShareFile maintenance is already running.", name)
                return False
            _maintenance_process.join(timeout=0)

        context = multiprocessing.get_context("spawn")
        process = context.Process(target=target, name=name, daemon=True)
        process.start()
        _maintenance_process = process
        logger.info("Started %s in child process %s.", name, process.pid)
        return True


def start_sharefile_poll_process() -> bool:
    return _start_maintenance_process(_run_sharefile_poll, "sharefile-poll")


def start_sharefile_sync_process() -> bool:
    return _start_maintenance_process(_run_sharefile_deep_sync, "sharefile-deep-sync")


def start_sharefile_webhook_registration_process() -> bool:
    return _start_maintenance_process(_run_webhook_registration, "sharefile-webhook-registration")
