import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories import MongoRepository


def test_mongo_account_lease_collision_is_safe_and_release_is_owner_scoped():
    lease = SimpleNamespace(
        find_one_and_update=AsyncMock(side_effect=DuplicateKeyError("lease held")),
        delete_one=AsyncMock(),
    )
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_browser_leases=lease)
    now = datetime.utcnow()
    assert not asyncio.run(repo.acquire_ftw_browser_lease("HighlandTech", "device-2", now, now + timedelta(minutes=5)))
    asyncio.run(repo.release_ftw_browser_lease("HighlandTech", "device-2"))
    assert lease.delete_one.await_args.args[0] == {"_id": "highlandtech", "device_id": "device-2"}


def test_mongo_pause_wins_atomic_reservation_without_claiming_queue():
    devices = SimpleNamespace(find_one_and_update=AsyncMock(return_value=None))
    jobs = SimpleNamespace(find_one_and_update=AsyncMock())
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_devices=devices, ftw_local_agent_jobs=jobs)
    now = datetime.utcnow()
    assert asyncio.run(repo.claim_ftw_local_agent_job(str(ObjectId()), "Account", "hash", now, now + timedelta(minutes=5))) is None
    selector = devices.find_one_and_update.await_args.args[0]
    assert selector["pause_requested"] == {"$ne": True}
    assert selector["active_job_id"] is None
    jobs.find_one_and_update.assert_not_awaited()


def test_mongo_queue_claim_excludes_uncertain_expired_claims_and_clears_empty_reservation():
    device_id = ObjectId()
    devices = SimpleNamespace(find_one_and_update=AsyncMock(return_value={"_id": device_id}), update_one=AsyncMock())
    jobs = SimpleNamespace(find_one_and_update=AsyncMock(return_value=None))
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_devices=devices, ftw_local_agent_jobs=jobs)
    now = datetime.utcnow()
    assert asyncio.run(repo.claim_ftw_local_agent_job(str(device_id), "Account", "hash", now, now + timedelta(minutes=5))) is None
    selector = jobs.find_one_and_update.await_args.args[0]
    assert all(item["status"] != "CLAIMED" for item in selector["$or"])
    assert devices.update_one.await_args.args[0]["active_job_id"] == "hash"
    assert devices.update_one.await_args.args[1]["$set"]["active_job_id"] is None


def test_mongo_readback_verification_has_atomic_single_starter():
    jobs = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)))
    repo = MongoRepository.__new__(MongoRepository)
    repo.db = SimpleNamespace(ftw_local_agent_jobs=jobs)
    assert not asyncio.run(repo.begin_ftw_job_verification(str(ObjectId())))
    selector = jobs.update_one.await_args.args[0]
    assert selector["status"] == "SUBMITTED"
    assert selector["verification_started_at"] is None
