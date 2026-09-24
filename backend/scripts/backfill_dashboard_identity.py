from __future__ import annotations

import argparse
import json

from pymongo import MongoClient, UpdateOne

from app.config import get_settings
from app.repositories import dashboard_identity_values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill compact dashboard identity fields without changing filing workflow data."
    )
    parser.add_argument("--apply", action="store_true", help="Write the calculated identity fields.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required.")

    client = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        socketTimeoutMS=60_000,
    )
    db_name = settings.mongodb_uri.rsplit("/", 1)[-1].split("?", 1)[0] or "erisapros_dashboard"
    collection = client[db_name].filings
    scanned = 0
    changed = 0
    updates: list[UpdateOne] = []

    projection = {
        "proposed_xml": 1,
        "package_documents": 1,
        "dashboard_client_name": 1,
        "dashboard_ein": 1,
        "dashboard_plan_number": 1,
        "dashboard_plan_name": 1,
    }
    for document in collection.find({}, projection):
        scanned += 1
        identity = dashboard_identity_values(document)
        delta = {key: value for key, value in identity.items() if document.get(key) != value}
        if not delta:
            continue
        changed += 1
        if args.apply:
            updates.append(UpdateOne({"_id": document["_id"]}, {"$set": delta}))
            if len(updates) == 500:
                collection.bulk_write(updates, ordered=False)
                updates.clear()

    if updates:
        collection.bulk_write(updates, ordered=False)

    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "scanned": scanned, "changed": changed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
