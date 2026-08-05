import os
import re
import uuid
import boto3
from app.config import get_settings


class StorageService:
    def save_file(self, file_name: str, content_type: str, file_bytes: bytes) -> dict:
        return self.save_pdf(file_name, content_type, file_bytes)

    def save_pdf(self, file_name: str, content_type: str, file_bytes: bytes) -> dict:
        settings = get_settings()
        filing_id = str(uuid.uuid4())
        key = f"schedule-a/{filing_id}/{sanitize_filename(file_name)}"

        if all([settings.aws_region, settings.aws_access_key_id, settings.aws_secret_access_key, settings.s3_bucket_name]):
            client = boto3.client("s3", region_name=settings.aws_region)
            client.put_object(Bucket=settings.s3_bucket_name, Key=key, Body=file_bytes, ContentType=content_type)
            return {"id": filing_id, "key": key, "bucket": settings.s3_bucket_name, "uploaded": True}

        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", ".uploads", filing_id)
        os.makedirs(upload_dir, exist_ok=True)
        local_path = os.path.join(upload_dir, sanitize_filename(file_name))
        with open(local_path, "wb") as handle:
            handle.write(file_bytes)
        return {"id": filing_id, "key": f"local-demo/{key}", "bucket": None, "uploaded": False, "local_path": local_path}

    def load_pdf(self, key: str, bucket: str | None = None, local_path: str | None = None) -> bytes:
        settings = get_settings()
        if bucket and all([settings.aws_region, settings.aws_access_key_id, settings.aws_secret_access_key]):
            client = boto3.client("s3", region_name=settings.aws_region)
            response = client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()

        if local_path and os.path.exists(local_path):
            with open(local_path, "rb") as handle:
                return handle.read()

        raise FileNotFoundError("Stored PDF could not be found in local storage or S3.")


def sanitize_filename(file_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", file_name)
