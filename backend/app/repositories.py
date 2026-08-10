from datetime import datetime
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import uuid4
from bson import ObjectId
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import PyMongoError
from pymongo.read_preferences import ReadPreference
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import get_settings
from app.models import (
    AuditLog,
    ExtractedField,
    ExtractedFieldStatus,
    ExtractionJob,
    FTWilliamsReview,
    FTWilliamsPlanMapping,
    RawExtraction,
    Filing,
    FieldRule,
    FieldRuleStatus,
    ReviewEvent,
    ShareFileOAuthToken,
)


def to_mongo(model):
    data = model.model_dump(mode="json", by_alias=False)
    data.pop("id", None)
    return data


def from_mongo(data: dict, model):
    data = dict(data)
    data["id"] = str(data.pop("_id"))
    return model(**data)


class Repository:
    async def ensure_indexes(self) -> None: ...
    async def create_filing(self, filing: Filing) -> Filing: ...
    async def list_filings(self) -> list[Filing]: ...
    async def list_dashboard_filings(self) -> list[Filing]: ...
    async def get_filing(self, filing_id: str) -> Filing | None: ...
    async def update_filing(self, filing_id: str, values: dict) -> Filing | None: ...
    async def add_fields(self, fields: list[ExtractedField]) -> list[ExtractedField]: ...
    async def replace_fields(self, filing_id: str, fields: list[ExtractedField]) -> list[ExtractedField]: ...
    async def list_fields(self, filing_id: str) -> list[ExtractedField]: ...
    async def update_field(
        self,
        filing_id: str,
        field_id: str,
        proposed_value: str,
        *,
        status: ExtractedFieldStatus = ExtractedFieldStatus.EDITED,
        status_reason: str | None = None,
    ) -> ExtractedField | None: ...
    async def add_event(self, event: ReviewEvent) -> ReviewEvent: ...
    async def list_events(self, filing_id: str) -> list[ReviewEvent]: ...
    async def add_audit(self, audit: AuditLog) -> None: ...
    async def list_audit_logs(self, filing_id: str) -> list[AuditLog]: ...
    async def list_ftwilliams_audit_logs(self, since: datetime, limit: int = 100) -> list[AuditLog]: ...
    async def list_failed_ftwilliams_reviews(self) -> list[FTWilliamsReview]: ...
    async def get_ftwilliams_review(self, filing_id: str) -> FTWilliamsReview | None: ...
    async def upsert_ftwilliams_review(self, review: FTWilliamsReview) -> FTWilliamsReview: ...
    async def get_ftwilliams_plan_mapping(self, company_employer_id: str, plan_number: str) -> FTWilliamsPlanMapping | None: ...
    async def upsert_ftwilliams_plan_mapping(self, mapping: FTWilliamsPlanMapping) -> FTWilliamsPlanMapping: ...
    async def create_extraction_job(self, job: ExtractionJob) -> ExtractionJob: ...
    async def update_extraction_job(self, job_id: str, values: dict) -> ExtractionJob | None: ...
    async def list_extraction_jobs(self, filing_id: str) -> list[ExtractionJob]: ...
    async def add_raw_extraction(self, raw: RawExtraction) -> RawExtraction: ...
    async def get_sharefile_token(self) -> ShareFileOAuthToken | None: ...
    async def upsert_sharefile_token(self, token: ShareFileOAuthToken) -> ShareFileOAuthToken: ...
    async def get_filing_by_sharefile_item_id(self, item_id: str) -> Filing | None: ...
    async def list_filings_by_sharefile_item_ids(self, item_ids: set[str]) -> list[Filing]: ...
    async def list_waiting_sharefile_filings(self) -> list[Filing]: ...
    async def get_sharefile_file(self, item_id: str) -> dict | None: ...
    async def upsert_sharefile_file(self, item_id: str, values: dict) -> dict: ...
    async def upsert_sharefile_files(self, records: dict[str, dict]) -> None: ...
    async def mark_sharefile_file_deleted(self, item_id: str, reason: str | None = None) -> dict | None: ...
    async def list_sharefile_files(self) -> list[dict]: ...
    async def list_sharefile_files_by_item_ids(self, item_ids: set[str]) -> list[dict]: ...
    async def list_sharefile_files_by_package_roots(self, package_root_keys: set[str]) -> list[dict]: ...
    async def list_active_sharefile_file_summaries(self) -> list[dict]: ...
    async def get_sharefile_state(self, key: str) -> dict | None: ...
    async def upsert_sharefile_state(self, key: str, values: dict) -> dict: ...
    async def list_field_rule_versions(self, key: str | None = None) -> list[FieldRule]: ...
    async def save_field_rule_version(self, rule: FieldRule) -> FieldRule: ...


class MongoRepository(Repository):
    def __init__(self, uri: str):
        # Never let a dead Atlas socket or exhausted pool leave an HTTP
        # request pending until CloudFront returns a 504. PyMongo reconnects
        # automatically; these bounds make it abandon stale topology/pool
        # state and retry on a healthy connection promptly.
        self.client = AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            socketTimeoutMS=15_000,
            waitQueueTimeoutMS=5_000,
            timeoutMS=18_000,
            read_preference=ReadPreference.SECONDARY_PREFERRED,
        )
        db_name = uri.rsplit("/", 1)[-1].split("?", 1)[0] or "erisapros_dashboard"
        self.db = self.client[db_name]

    async def ensure_indexes(self) -> None:
        await self.db.sharefile_file_index.create_index(
            "item_id", name="sharefile_item_id_idx"
        )
        await self.db.sharefile_file_index.create_index(
            "package_root_key", name="sharefile_package_root_idx"
        )
        await self.db.sharefile_file_index.create_index(
            "package_key", name="sharefile_package_idx"
        )
        await self.db.filings.create_index(
            "sharefile_item_id", name="filing_sharefile_item_idx"
        )
        await self.db.filings.create_index(
            "package_documents.sharefile_item_id",
            name="filing_package_sharefile_item_idx",
        )

    async def create_filing(self, filing: Filing) -> Filing:
        result = await self.db.filings.insert_one(to_mongo(filing))
        filing.id = str(result.inserted_id)
        return filing

    async def list_field_rule_versions(self, key: str | None = None) -> list[FieldRule]:
        query = {"key": key} if key else {}
        docs = await self.db.field_rule_versions.find(query).sort([("key", 1), ("version", -1), ("created_at", -1)]).to_list(2000)
        return [from_mongo(doc, FieldRule) for doc in docs]

    async def save_field_rule_version(self, rule: FieldRule) -> FieldRule:
        result = await self.db.field_rule_versions.insert_one(to_mongo(rule))
        rule.id = str(result.inserted_id)
        return rule

    async def list_filings(self) -> list[Filing]:
        docs = await self.db.filings.find().sort("created_at", -1).to_list(100)
        return [from_mongo(doc, Filing) for doc in docs]

    async def list_dashboard_filings(self) -> list[Filing]:
        # The dashboard does not need extraction payloads, worksheet tables,
        # broker rows, storage metadata, or approval timestamps. Avoid pulling
        # those potentially large fields across Atlas for every page refresh.
        projection = {
            "file_name": 1,
            "content_type": 1,
            "file_size": 1,
            "document_type": 1,
            "package_document_count": 1,
            "status": 1,
            "s3_key": 1,
            "package_documents": 1,
            "intake_source": 1,
            "extraction_provider": 1,
            "overall_confidence": 1,
            "missing_high_priority_count": 1,
            "missing_medium_priority_count": 1,
            "missing_low_priority_count": 1,
            "low_confidence_count": 1,
            "unmapped_count": 1,
            "review_field_count": 1,
            "found_field_count": 1,
            "excluded_field_count": 1,
            "schedule_a_contract_type": 1,
            "schedule_a_contract_type_reason": 1,
            "schedule_a_contract_type_confirmed": 1,
            "ftw_schedule_a_contract_type": 1,
            "ftw_schedule_a_contract_type_reason": 1,
            "proposed_xml": 1,
            "error_message": 1,
            "rejection_reason": 1,
            "created_at": 1,
            "updated_at": 1,
        }
        docs = await (
            self.db.filings.find(
                {"status": {"$nin": ["SUPERSEDED", "DELETED"]}},
                projection,
            )
            .sort("created_at", -1)
            .to_list(100)
        )
        return [from_mongo(doc, Filing) for doc in docs]

    async def get_filing(self, filing_id: str) -> Filing | None:
        if not ObjectId.is_valid(filing_id):
            return None
        doc = await self.db.filings.find_one({"_id": ObjectId(filing_id)})
        return from_mongo(doc, Filing) if doc else None

    async def update_filing(self, filing_id: str, values: dict) -> Filing | None:
        if not ObjectId.is_valid(filing_id):
            return None
        values["updated_at"] = datetime.utcnow()
        doc = await self.db.filings.find_one_and_update({"_id": ObjectId(filing_id)}, {"$set": values}, return_document=ReturnDocument.AFTER)
        return from_mongo(doc, Filing) if doc else None

    async def add_fields(self, fields: list[ExtractedField]) -> list[ExtractedField]:
        if not fields:
            return []
        docs = [to_mongo(field) for field in fields]
        result = await self.db.extracted_fields.insert_many(docs)
        for field, inserted_id in zip(fields, result.inserted_ids):
            field.id = str(inserted_id)
        return fields

    async def replace_fields(self, filing_id: str, fields: list[ExtractedField]) -> list[ExtractedField]:
        await self.db.extracted_fields.delete_many({"filing_id": filing_id})
        return await self.add_fields(fields)

    async def list_fields(self, filing_id: str) -> list[ExtractedField]:
        docs = await self.db.extracted_fields.find({"filing_id": filing_id}).sort("mapped_label", 1).to_list(500)
        return [from_mongo(doc, ExtractedField) for doc in docs]

    async def update_field(
        self,
        filing_id: str,
        field_id: str,
        proposed_value: str,
        *,
        status: ExtractedFieldStatus = ExtractedFieldStatus.EDITED,
        status_reason: str | None = None,
    ) -> ExtractedField | None:
        if not ObjectId.is_valid(field_id):
            return None
        updates = {
            "proposed_value": proposed_value,
            "status": status,
            "updated_at": datetime.utcnow(),
        }
        if status_reason is not None:
            updates["status_reason"] = status_reason
        doc = await self.db.extracted_fields.find_one_and_update(
            {"_id": ObjectId(field_id), "filing_id": filing_id},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        return from_mongo(doc, ExtractedField) if doc else None

    async def add_event(self, event: ReviewEvent) -> ReviewEvent:
        result = await self.db.review_events.insert_one(to_mongo(event))
        event.id = str(result.inserted_id)
        return event

    async def list_events(self, filing_id: str) -> list[ReviewEvent]:
        docs = await self.db.review_events.find({"filing_id": filing_id}).sort("created_at", -1).to_list(100)
        return [from_mongo(doc, ReviewEvent) for doc in docs]

    async def add_audit(self, audit: AuditLog) -> None:
        await self.db.audit_logs.insert_one(to_mongo(audit))

    async def list_audit_logs(self, filing_id: str) -> list[AuditLog]:
        docs = await self.db.audit_logs.find({"filing_id": filing_id}).sort("created_at", -1).to_list(200)
        return [from_mongo(doc, AuditLog) for doc in docs]

    async def list_ftwilliams_audit_logs(self, since: datetime, limit: int = 100) -> list[AuditLog]:
        events = list(FTWILLIAMS_HISTORY_EVENTS)
        docs = await self.db.audit_logs.find({"event": {"$in": events}}).sort("created_at", -1).to_list(max(limit * 3, 100))
        logs = [from_mongo(doc, AuditLog) for doc in docs]
        return [log for log in logs if log.created_at >= since][:limit]

    async def get_ftwilliams_review(self, filing_id: str) -> FTWilliamsReview | None:
        doc = await self.db.ftwilliams_reviews.find_one({"filing_id": filing_id})
        return from_mongo(doc, FTWilliamsReview) if doc else None

    async def list_failed_ftwilliams_reviews(self) -> list[FTWilliamsReview]:
        docs = await self.db.ftwilliams_reviews.find(
            {"status": "UPDATE_FAILED"}
        ).sort("updated_at", -1).to_list(100)
        return [from_mongo(doc, FTWilliamsReview) for doc in docs]

    async def upsert_ftwilliams_review(self, review: FTWilliamsReview) -> FTWilliamsReview:
        values = to_mongo(review)
        created_at = values.pop("created_at", review.created_at)
        values["updated_at"] = datetime.utcnow()
        doc = await self.db.ftwilliams_reviews.find_one_and_update(
            {"filing_id": review.filing_id},
            {"$set": self._mongo_safe_value(values), "$setOnInsert": {"created_at": created_at}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return from_mongo(doc, FTWilliamsReview)

    async def get_ftwilliams_plan_mapping(self, company_employer_id: str, plan_number: str) -> FTWilliamsPlanMapping | None:
        doc = await self.db.ftwilliams_plan_mappings.find_one(
            {"company_employer_id": company_employer_id, "plan_number": plan_number}
        )
        return from_mongo(doc, FTWilliamsPlanMapping) if doc else None

    async def upsert_ftwilliams_plan_mapping(self, mapping: FTWilliamsPlanMapping) -> FTWilliamsPlanMapping:
        values = to_mongo(mapping)
        created_at = values.pop("created_at", mapping.created_at)
        values["updated_at"] = datetime.utcnow()
        doc = await self.db.ftwilliams_plan_mappings.find_one_and_update(
            {"company_employer_id": mapping.company_employer_id, "plan_number": mapping.plan_number},
            {"$set": self._mongo_safe_value(values), "$setOnInsert": {"created_at": created_at}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return from_mongo(doc, FTWilliamsPlanMapping)

    async def create_extraction_job(self, job: ExtractionJob) -> ExtractionJob:
        result = await self.db.extraction_jobs.insert_one(to_mongo(job))
        job.id = str(result.inserted_id)
        return job

    async def update_extraction_job(self, job_id: str, values: dict) -> ExtractionJob | None:
        if not ObjectId.is_valid(job_id):
            return None
        values["updated_at"] = datetime.utcnow()
        doc = await self.db.extraction_jobs.find_one_and_update(
            {"_id": ObjectId(job_id)},
            {"$set": values},
            return_document=ReturnDocument.AFTER,
        )
        return from_mongo(doc, ExtractionJob) if doc else None

    async def list_extraction_jobs(self, filing_id: str) -> list[ExtractionJob]:
        docs = await self.db.extraction_jobs.find({"filing_id": filing_id}).sort("created_at", -1).to_list(50)
        return [from_mongo(doc, ExtractionJob) for doc in docs]

    async def add_raw_extraction(self, raw: RawExtraction) -> RawExtraction:
        result = await self.db.raw_extractions.insert_one(to_mongo(raw))
        raw.id = str(result.inserted_id)
        return raw

    async def get_sharefile_token(self) -> ShareFileOAuthToken | None:
        doc = await self.db.sharefile_tokens.find_one({"provider": "sharefile"})
        return from_mongo(doc, ShareFileOAuthToken) if doc else None

    async def upsert_sharefile_token(self, token: ShareFileOAuthToken) -> ShareFileOAuthToken:
        values = to_mongo(token)
        created_at = values.pop("created_at", token.created_at)
        values["updated_at"] = datetime.utcnow()
        doc = await self.db.sharefile_tokens.find_one_and_update(
            {"provider": "sharefile"},
            {"$set": values, "$setOnInsert": {"created_at": created_at}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return from_mongo(doc, ShareFileOAuthToken)

    async def get_filing_by_sharefile_item_id(self, item_id: str) -> Filing | None:
        doc = await self.db.filings.find_one(
            {
                "$or": [
                    {"sharefile_item_id": item_id},
                    {"package_documents.sharefile_item_id": item_id},
                ]
            }
        )
        return from_mongo(doc, Filing) if doc else None

    async def list_filings_by_sharefile_item_ids(self, item_ids: set[str]) -> list[Filing]:
        if not item_ids:
            return []
        values = sorted(item_ids)
        docs = await self.db.filings.find(
            {
                "$or": [
                    {"sharefile_item_id": {"$in": values}},
                    {"package_documents.sharefile_item_id": {"$in": values}},
                ]
            }
        ).sort("created_at", -1).to_list(5000)
        return [from_mongo(doc, Filing) for doc in docs]

    async def list_waiting_sharefile_filings(self) -> list[Filing]:
        docs = await self.db.filings.find(
            {
                "intake_source": "SHAREFILE",
                "status": {"$in": ["WAITING_FOR_WORKSHEET", "WAITING_FOR_SCHEDULE_A"]},
            }
        ).sort("created_at", -1).to_list(5000)
        return [from_mongo(doc, Filing) for doc in docs]

    async def get_sharefile_file(self, item_id: str) -> dict | None:
        doc = await self.db.sharefile_file_index.find_one({"item_id": item_id})
        return self._plain_mongo_doc(doc) if doc else None

    async def upsert_sharefile_file(self, item_id: str, values: dict) -> dict:
        payload = self._mongo_safe_value(dict(values))
        payload["item_id"] = item_id
        payload["updated_at"] = datetime.utcnow()
        doc = await self.db.sharefile_file_index.find_one_and_update(
            {"item_id": item_id},
            {"$set": payload, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._plain_mongo_doc(doc)

    async def upsert_sharefile_files(self, records: dict[str, dict]) -> None:
        if not records:
            return
        now = datetime.utcnow()
        operations = []
        for item_id, values in records.items():
            payload = self._mongo_safe_value(dict(values))
            payload["item_id"] = item_id
            payload["updated_at"] = now
            operations.append(
                UpdateOne(
                    {"item_id": item_id},
                    {"$set": payload, "$setOnInsert": {"created_at": now}},
                    upsert=True,
                )
            )
        await self.db.sharefile_file_index.bulk_write(operations, ordered=False)

    async def mark_sharefile_file_deleted(self, item_id: str, reason: str | None = None) -> dict | None:
        doc = await self.db.sharefile_file_index.find_one_and_update(
            {"item_id": item_id},
            {
                "$set": {
                    "status": "DELETED",
                    "deleted_at": datetime.utcnow(),
                    "delete_reason": reason,
                    "updated_at": datetime.utcnow(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return self._plain_mongo_doc(doc) if doc else None

    async def list_sharefile_files(self) -> list[dict]:
        docs = await self.db.sharefile_file_index.find().to_list(10000)
        return [self._plain_mongo_doc(doc) for doc in docs]

    async def list_sharefile_files_by_item_ids(self, item_ids: set[str]) -> list[dict]:
        if not item_ids:
            return []
        docs = await self.db.sharefile_file_index.find(
            {"item_id": {"$in": sorted(item_ids)}}
        ).to_list(len(item_ids))
        return [self._plain_mongo_doc(doc) for doc in docs]

    async def list_sharefile_files_by_package_roots(self, package_root_keys: set[str]) -> list[dict]:
        if not package_root_keys:
            return []
        docs = await self.db.sharefile_file_index.find(
            {"package_root_key": {"$in": sorted(package_root_keys)}}
        ).to_list(10000)
        return [self._plain_mongo_doc(doc) for doc in docs]

    async def list_active_sharefile_file_summaries(self) -> list[dict]:
        docs = await self.db.sharefile_file_index.find(
            {
                "status": {"$nin": ["DELETED", "IGNORED"]},
                "document_type": {"$in": ["SCHEDULE_A", "PLAN_WORKSHEET"]},
            },
            {"item_id": 1, "status": 1, "document_type": 1},
        ).to_list(10000)
        return [self._plain_mongo_doc(doc) for doc in docs]

    async def get_sharefile_state(self, key: str) -> dict | None:
        doc = await self.db.sharefile_sync_state.find_one({"key": key})
        return self._plain_mongo_doc(doc) if doc else None

    async def upsert_sharefile_state(self, key: str, values: dict) -> dict:
        payload = dict(values)
        payload["key"] = key
        payload["updated_at"] = datetime.utcnow()
        doc = await self.db.sharefile_sync_state.find_one_and_update(
            {"key": key},
            {"$set": payload, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._plain_mongo_doc(doc)

    def _plain_mongo_doc(self, doc: dict) -> dict:
        payload = dict(doc)
        payload["id"] = str(payload.pop("_id"))
        return payload

    def _mongo_safe_value(self, value):
        if isinstance(value, dict):
            safe = {}
            for key, item in value.items():
                safe_key = str(key).replace(".", "_")
                if safe_key.startswith("$"):
                    safe_key = "_" + safe_key[1:]
                safe[safe_key] = self._mongo_safe_value(item)
            return safe
        if isinstance(value, list):
            return [self._mongo_safe_value(item) for item in value]
        return value


class MemoryRepository(Repository):
    async def ensure_indexes(self) -> None:
        return None

    def __init__(self):
        self.filings: dict[str, Filing] = {}
        self.fields: dict[str, ExtractedField] = {}
        self.events: list[ReviewEvent] = []
        self.audit: list[AuditLog] = []
        self.jobs: dict[str, ExtractionJob] = {}
        self.raw_extractions: dict[str, RawExtraction] = {}
        self.ftwilliams_reviews: dict[str, FTWilliamsReview] = {}
        self.ftwilliams_plan_mappings: dict[tuple[str, str], FTWilliamsPlanMapping] = {}
        self.sharefile_files: dict[str, dict] = {}
        self.sharefile_sync_state: dict[str, dict] = {}
        self.field_rule_versions: dict[str, FieldRule] = {}

    async def list_field_rule_versions(self, key: str | None = None) -> list[FieldRule]:
        rules = [rule for rule in self.field_rule_versions.values() if key is None or rule.key == key]
        return sorted(rules, key=lambda item: (item.key, -item.version, -item.created_at.timestamp()))

    async def save_field_rule_version(self, rule: FieldRule) -> FieldRule:
        stored = rule.model_copy(deep=True)
        stored.id = stored.id or str(uuid4())
        self.field_rule_versions[stored.id] = stored
        return stored.model_copy(deep=True)

    async def create_filing(self, filing: Filing) -> Filing:
        filing.id = str(uuid4())
        self.filings[filing.id] = filing
        return filing

    async def list_filings(self) -> list[Filing]:
        return sorted(self.filings.values(), key=lambda item: item.created_at, reverse=True)

    async def list_dashboard_filings(self) -> list[Filing]:
        return await self.list_filings()

    async def get_filing(self, filing_id: str) -> Filing | None:
        return self.filings.get(filing_id)

    async def update_filing(self, filing_id: str, values: dict) -> Filing | None:
        filing = self.filings.get(filing_id)
        if not filing:
            return None
        for key, value in values.items():
            setattr(filing, key, value)
        filing.updated_at = datetime.utcnow()
        return filing

    async def add_fields(self, fields: list[ExtractedField]) -> list[ExtractedField]:
        for field in fields:
            field.id = str(uuid4())
            self.fields[field.id] = field
        return fields

    async def replace_fields(self, filing_id: str, fields: list[ExtractedField]) -> list[ExtractedField]:
        self.fields = {key: field for key, field in self.fields.items() if field.filing_id != filing_id}
        return await self.add_fields(fields)

    async def list_fields(self, filing_id: str) -> list[ExtractedField]:
        return [field for field in self.fields.values() if field.filing_id == filing_id]

    async def update_field(
        self,
        filing_id: str,
        field_id: str,
        proposed_value: str,
        *,
        status: ExtractedFieldStatus = ExtractedFieldStatus.EDITED,
        status_reason: str | None = None,
    ) -> ExtractedField | None:
        field = self.fields.get(field_id)
        if not field or field.filing_id != filing_id:
            return None
        field.proposed_value = proposed_value
        field.status = status
        if status_reason is not None:
            field.status_reason = status_reason
        field.updated_at = datetime.utcnow()
        return field

    async def add_event(self, event: ReviewEvent) -> ReviewEvent:
        event.id = str(uuid4())
        self.events.append(event)
        return event

    async def list_events(self, filing_id: str) -> list[ReviewEvent]:
        return [event for event in self.events if event.filing_id == filing_id]

    async def add_audit(self, audit: AuditLog) -> None:
        audit.id = str(uuid4())
        self.audit.append(audit)

    async def list_audit_logs(self, filing_id: str) -> list[AuditLog]:
        return [audit for audit in self.audit if audit.filing_id == filing_id]

    async def list_ftwilliams_audit_logs(self, since: datetime, limit: int = 100) -> list[AuditLog]:
        logs = [
            audit
            for audit in self.audit
            if audit.event in FTWILLIAMS_HISTORY_EVENTS and audit.filing_id and audit.created_at >= since
        ]
        return sorted(logs, key=lambda item: item.created_at, reverse=True)[:limit]

    async def get_ftwilliams_review(self, filing_id: str) -> FTWilliamsReview | None:
        return self.ftwilliams_reviews.get(filing_id)

    async def list_failed_ftwilliams_reviews(self) -> list[FTWilliamsReview]:
        return sorted(
            (
                review
                for review in self.ftwilliams_reviews.values()
                if review.status.value == "UPDATE_FAILED"
            ),
            key=lambda item: item.updated_at,
            reverse=True,
        )

    async def upsert_ftwilliams_review(self, review: FTWilliamsReview) -> FTWilliamsReview:
        existing = self.ftwilliams_reviews.get(review.filing_id)
        review.id = existing.id if existing and existing.id else review.id or str(uuid4())
        review.created_at = existing.created_at if existing else review.created_at
        review.updated_at = datetime.utcnow()
        self.ftwilliams_reviews[review.filing_id] = review
        return review

    async def get_ftwilliams_plan_mapping(self, company_employer_id: str, plan_number: str) -> FTWilliamsPlanMapping | None:
        return self.ftwilliams_plan_mappings.get((company_employer_id, plan_number))

    async def upsert_ftwilliams_plan_mapping(self, mapping: FTWilliamsPlanMapping) -> FTWilliamsPlanMapping:
        key = (mapping.company_employer_id, mapping.plan_number)
        existing = self.ftwilliams_plan_mappings.get(key)
        mapping.id = existing.id if existing and existing.id else mapping.id or str(uuid4())
        mapping.created_at = existing.created_at if existing else mapping.created_at
        mapping.updated_at = datetime.utcnow()
        self.ftwilliams_plan_mappings[key] = mapping
        return mapping

    async def create_extraction_job(self, job: ExtractionJob) -> ExtractionJob:
        job.id = str(uuid4())
        self.jobs[job.id] = job
        return job

    async def update_extraction_job(self, job_id: str, values: dict) -> ExtractionJob | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        return job

    async def list_extraction_jobs(self, filing_id: str) -> list[ExtractionJob]:
        return [job for job in self.jobs.values() if job.filing_id == filing_id]

    async def add_raw_extraction(self, raw: RawExtraction) -> RawExtraction:
        raw.id = str(uuid4())
        self.raw_extractions[raw.id] = raw
        return raw

    async def get_sharefile_token(self) -> ShareFileOAuthToken | None:
        return getattr(self, "sharefile_token", None)

    async def upsert_sharefile_token(self, token: ShareFileOAuthToken) -> ShareFileOAuthToken:
        token.id = token.id or str(uuid4())
        token.updated_at = datetime.utcnow()
        self.sharefile_token = token
        return token

    async def get_filing_by_sharefile_item_id(self, item_id: str) -> Filing | None:
        return next(
            (
                filing
                for filing in self.filings.values()
                if filing.sharefile_item_id == item_id
                or any(str(document.get("sharefile_item_id")) == item_id for document in filing.package_documents)
            ),
            None,
        )

    async def list_filings_by_sharefile_item_ids(self, item_ids: set[str]) -> list[Filing]:
        if not item_ids:
            return []
        return [
            filing
            for filing in self.filings.values()
            if str(filing.sharefile_item_id or "") in item_ids
            or any(str(document.get("sharefile_item_id") or "") in item_ids for document in filing.package_documents)
        ]

    async def list_waiting_sharefile_filings(self) -> list[Filing]:
        return [
            filing
            for filing in self.filings.values()
            if filing.intake_source == "SHAREFILE"
            and filing.status.value in {"WAITING_FOR_WORKSHEET", "WAITING_FOR_SCHEDULE_A"}
        ]

    async def get_sharefile_file(self, item_id: str) -> dict | None:
        return self.sharefile_files.get(item_id)

    async def upsert_sharefile_file(self, item_id: str, values: dict) -> dict:
        now = datetime.utcnow()
        existing = self.sharefile_files.get(item_id, {})
        record = {
            **existing,
            **values,
            "id": existing.get("id") or str(uuid4()),
            "item_id": item_id,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        self.sharefile_files[item_id] = record
        return record

    async def upsert_sharefile_files(self, records: dict[str, dict]) -> None:
        now = datetime.utcnow()
        for item_id, values in records.items():
            existing = self.sharefile_files.get(item_id, {})
            self.sharefile_files[item_id] = {
                **existing,
                **values,
                "id": existing.get("id") or str(uuid4()),
                "item_id": item_id,
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
            }

    async def mark_sharefile_file_deleted(self, item_id: str, reason: str | None = None) -> dict | None:
        record = self.sharefile_files.get(item_id)
        if not record:
            return None
        record.update(
            {
                "status": "DELETED",
                "deleted_at": datetime.utcnow(),
                "delete_reason": reason,
                "updated_at": datetime.utcnow(),
            }
        )
        return record

    async def list_sharefile_files(self) -> list[dict]:
        return list(self.sharefile_files.values())

    async def list_sharefile_files_by_item_ids(self, item_ids: set[str]) -> list[dict]:
        if not item_ids:
            return []
        return [
            record
            for item_id, record in self.sharefile_files.items()
            if str(item_id) in item_ids
        ]

    async def list_sharefile_files_by_package_roots(self, package_root_keys: set[str]) -> list[dict]:
        if not package_root_keys:
            return []
        return [
            record
            for record in self.sharefile_files.values()
            if str(record.get("package_root_key") or "") in package_root_keys
        ]

    async def list_active_sharefile_file_summaries(self) -> list[dict]:
        return [
            {
                "id": record.get("id"),
                "item_id": record.get("item_id"),
                "status": record.get("status"),
                "document_type": record.get("document_type"),
            }
            for record in self.sharefile_files.values()
            if record.get("status") not in {"DELETED", "IGNORED"}
            and record.get("document_type") in {"SCHEDULE_A", "PLAN_WORKSHEET"}
        ]

    async def get_sharefile_state(self, key: str) -> dict | None:
        return self.sharefile_sync_state.get(key)

    async def upsert_sharefile_state(self, key: str, values: dict) -> dict:
        now = datetime.utcnow()
        existing = self.sharefile_sync_state.get(key, {})
        record = {
            **existing,
            **values,
            "id": existing.get("id") or str(uuid4()),
            "key": key,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        self.sharefile_sync_state[key] = record
        return record


_repository: Repository | None = None


def get_repository() -> Repository:
    global _repository
    if _repository:
        return _repository
    settings = get_settings()
    if settings.is_production and not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required in production; in-memory storage is disabled.")
    _repository = MongoRepository(settings.mongodb_uri) if settings.mongodb_uri else MemoryRepository()
    return _repository


def reset_repository() -> None:
    """Discard the pool for new requests without breaking in-flight users."""
    global _repository
    _repository = None


T = TypeVar("T")


async def retry_repository_read(operation: Callable[[Repository], Awaitable[T]]) -> T:
    """Retry one idempotent repository read with a fresh connection pool."""
    try:
        return await operation(get_repository())
    except PyMongoError:
        reset_repository()
        return await operation(get_repository())


FTWILLIAMS_HISTORY_EVENTS = {
    "FTWILLIAMS_REVIEW_PREPARED",
    "FTWILLIAMS_MANUAL_MATCH_SAVED",
    "FTWILLIAMS_SCHEDULE_A_MATCH_SELECTED",
    "FTWILLIAMS_UPDATE_SENT",
    "FTWILLIAMS_UPDATE_FAILED",
}
