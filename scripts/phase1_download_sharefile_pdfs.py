import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pymongo import MongoClient


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:180] or "document.pdf"


async def refresh_access_token(client: httpx.AsyncClient, token: dict) -> dict:
    expires_at = token.get("expires_at")
    now = datetime.now(timezone.utc)
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not token.get("refresh_token"):
        return token
    response = await client.post(
        f"https://{token['subdomain']}.{token.get('appcp') or 'sharefile.com'}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": os.environ["ERISAPROS_AUDIT_SHAREFILE_CLIENT_ID"],
            "client_secret": os.environ["ERISAPROS_AUDIT_SHAREFILE_CLIENT_SECRET"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    payload = response.json()
    token = dict(token)
    token["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        token["refresh_token"] = payload["refresh_token"]
    return token


async def download_one(client, token, record, output_dir, semaphore):
    item_id = str(record.get("item_id") or "")
    name = str(record.get("file_name") or "document.pdf")
    destination = output_dir / f"{item_id}__{safe_name(name)}"
    if destination.exists() and destination.stat().st_size > 0:
        return {"item_id": item_id, "local_path": str(destination), "result": "cached", "bytes": destination.stat().st_size}
    if not item_id or int(record.get("file_size") or 0) <= 0:
        return {"item_id": item_id, "local_path": "", "result": "skipped_zero_size", "bytes": 0}
    api = token.get("apicp") or "sf-api.com"
    url = f"https://{token['subdomain']}.{api}/sf/v3/Items({item_id})/Download"
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    async with semaphore:
        last_error = ""
        for attempt in range(1, 4):
            try:
                response = await client.get(url, headers=headers, follow_redirects=False)
                if response.status_code == 401:
                    return {"item_id": item_id, "local_path": "", "result": "unauthorized", "bytes": 0}
                location = response.headers.get("Location")
                if response.is_redirect and location:
                    response = await client.get(location, follow_redirects=True)
                    response.raise_for_status()
                else:
                    response.raise_for_status()
                if "application/json" in response.headers.get("Content-Type", ""):
                    payload = response.json()
                    nested = next((payload.get(key) for key in ("DownloadUrl", "downloadUrl", "url", "Url", "value") if isinstance(payload.get(key), str)), None)
                    if nested:
                        response = await client.get(nested, follow_redirects=True)
                        response.raise_for_status()
                content = response.content
                if not content:
                    raise RuntimeError("empty response")
                destination.write_bytes(content)
                return {"item_id": item_id, "local_path": str(destination), "result": "downloaded", "bytes": len(content)}
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    await asyncio.sleep(attempt)
        return {"item_id": item_id, "local_path": "", "result": "failed", "bytes": 0, "error": last_error}


async def run(args):
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    records = inventory["records"][: args.limit or None]
    uri = os.environ["ERISAPROS_AUDIT_MONGO_URI"]
    db_name = uri.rsplit("/", 1)[-1].split("?", 1)[0] or "erisapros_dashboard"
    mongo = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    token = mongo[db_name].sharefile_tokens.find_one({"provider": "sharefile"}, {"_id": 0})
    if not token:
        raise RuntimeError("ShareFile token is not available")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(120.0, connect=30.0)
    limits = httpx.Limits(max_connections=args.concurrency + 2, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        token = await refresh_access_token(client, token)
        semaphore = asyncio.Semaphore(args.concurrency)
        tasks = [download_one(client, token, record, output_dir, semaphore) for record in records]
        results = []
        for index, future in enumerate(asyncio.as_completed(tasks), start=1):
            results.append(await future)
            if index % 50 == 0 or index == len(tasks):
                failed = sum(1 for item in results if item["result"] in {"failed", "unauthorized"})
                print(f"progress {index}/{len(tasks)} failed={failed}", flush=True)
    by_id = {item["item_id"]: item for item in results}
    merged = []
    for record in records:
        merged.append({**record, **by_id.get(str(record.get("item_id") or ""), {})})
    Path(args.manifest).write_text(json.dumps(merged, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    failed = [item for item in merged if item.get("result") in {"failed", "unauthorized"}]
    print(json.dumps({"records": len(merged), "downloaded_or_cached": sum(1 for i in merged if i.get("result") in {"downloaded", "cached"}), "failed": len(failed), "skipped_zero_size": sum(1 for i in merged if i.get("result") == "skipped_zero_size")}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
