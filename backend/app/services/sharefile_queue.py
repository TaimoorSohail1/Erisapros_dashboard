import asyncio
import json
from functools import lru_cache

import boto3

from app.config import get_settings


class ShareFileWorkQueue:
    def __init__(self, queue_url: str | None = None):
        settings = get_settings()
        self.queue_url = queue_url or settings.sharefile_work_queue_url
        self.client = boto3.client("sqs", region_name=settings.aws_region) if self.queue_url else None

    @property
    def configured(self) -> bool:
        return bool(self.queue_url and self.client)

    async def enqueue(self, work_type: str, payload: dict | None = None) -> bool:
        if not self.configured:
            return False
        body = json.dumps({"type": work_type, "payload": payload or {}})
        await asyncio.to_thread(
            self.client.send_message,
            QueueUrl=self.queue_url,
            MessageBody=body,
        )
        return True

    async def receive(self) -> list[dict]:
        if not self.configured:
            raise RuntimeError("SHAREFILE_WORK_QUEUE_URL is not configured.")
        response = await asyncio.to_thread(
            self.client.receive_message,
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=43_200,
        )
        return response.get("Messages", [])

    async def delete(self, receipt_handle: str) -> None:
        if not self.configured:
            raise RuntimeError("SHAREFILE_WORK_QUEUE_URL is not configured.")
        await asyncio.to_thread(
            self.client.delete_message,
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )


@lru_cache
def get_sharefile_work_queue() -> ShareFileWorkQueue:
    return ShareFileWorkQueue()


async def enqueue_sharefile_work(work_type: str, payload: dict | None = None) -> bool:
    return await get_sharefile_work_queue().enqueue(work_type, payload)
