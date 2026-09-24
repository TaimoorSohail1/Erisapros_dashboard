import json
import os
import re
import sys
from collections import Counter

from pymongo import MongoClient


def clean(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def probable_schedule_a(doc: dict) -> bool:
    name = str(doc.get("file_name") or "")
    path = str(doc.get("folder_path") or doc.get("path") or "")
    text = f"{path} {name}".lower()
    is_pdf = name.lower().endswith(".pdf")
    schedule_marker = bool(
        re.search(r"\bschedule\s*[-_]?\s*a\b", text)
        or re.search(r"\bsched(?:ule)?\s*[-_]?\s*a\b", text)
        or "schedulea" in re.sub(r"[^a-z0-9]", "", text)
    )
    return is_pdf and (doc.get("document_type") == "SCHEDULE_A" or schedule_marker)


def main() -> None:
    uri = os.environ["ERISAPROS_AUDIT_MONGO_URI"]
    db_name = uri.rsplit("/", 1)[-1].split("?", 1)[0] or "erisapros_dashboard"
    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    db = client[db_name]
    collection = db.sharefile_file_index
    projection = {
        "_id": 0,
        "item_id": 1,
        "status": 1,
        "document_type": 1,
        "file_name": 1,
        "folder_path": 1,
        "path": 1,
        "path_parts": 1,
        "client_name": 1,
        "filing_year": 1,
        "file_size": 1,
        "modified_at": 1,
        "created_at_sharefile": 1,
        "version": 1,
        "hash": 1,
        "filing_id": 1,
        "package_key": 1,
        "package_root_key": 1,
    }
    docs = list(collection.find({}, projection))
    active = [d for d in docs if d.get("status") not in {"DELETED", "IGNORED"}]
    candidates = [d for d in active if probable_schedule_a(d)]
    payload = {
        "index_total": len(docs),
        "active_total": len(active),
        "status_counts": dict(Counter(str(d.get("status")) for d in docs)),
        "document_type_counts": dict(Counter(str(d.get("document_type")) for d in docs)),
        "probable_schedule_a_pdf_count": len(candidates),
        "probable_schedule_a_client_count": len({str(d.get("client_name") or "") for d in candidates}),
        "records": [
            {key: clean(value) for key, value in d.items()}
            for d in sorted(candidates, key=lambda x: (str(x.get("client_name")), str(x.get("folder_path")), str(x.get("file_name"))))
        ],
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
