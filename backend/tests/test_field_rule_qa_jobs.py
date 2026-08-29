import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.field_rule_qa_jobs import (
    _reset_qa_jobs_for_tests,
    get_qa_job,
    submit_qa_job,
)


class FieldRuleQAJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        _reset_qa_jobs_for_tests()

    async def test_long_extraction_completes_outside_the_submit_request(self):
        gate = asyncio.Event()

        async def work():
            await gate.wait()
            return {"provider": "EyeLevel/GroundX"}

        submitted = submit_qa_job(work)
        await asyncio.sleep(0)

        self.assertEqual(get_qa_job(submitted["job_id"])["status"], "PROCESSING")
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        completed = get_qa_job(submitted["job_id"])
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["result"], {"provider": "EyeLevel/GroundX"})

    async def test_failed_extraction_is_reported_to_the_poller(self):
        async def work():
            raise RuntimeError("EyeLevel rejected the document")

        submitted = submit_qa_job(work)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        failed = get_qa_job(submitted["job_id"])
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["error"], "EyeLevel rejected the document")


if __name__ == "__main__":
    unittest.main()
