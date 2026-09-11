import asyncio
from types import SimpleNamespace
import unittest

from scripts.verify_ftw_demo_update_restore import (
    _canonical_schedule_record,
    _canonical_schedule_set,
    _matching_schedule_record,
    _query_all_schedule_records,
)


def schedule_record(sequence: str, carrier: str, contract: str, brokers: list[dict[str, str]]) -> dict:
    query_results = {
        "InsCarrierName": carrier,
        "InsContractNum": contract,
    }
    for row in brokers:
        query_results.update(row)
    return {
        "ftw_seq_no": sequence,
        "query_results": query_results,
        "query_subparts": {"Broker": brokers},
    }


class FTWDemoCanarySnapshotTests(unittest.TestCase):
    def test_full_slot_query_collects_every_successful_schedule(self):
        class FakeService:
            async def run_query(self, request):
                sequence = str(request.ftw_seq_no)
                statuses = []
                if sequence in {"1", "3"}:
                    statuses.append(SimpleNamespace(
                        error_code="0",
                        ftw_seq_no=None,
                        query_results={"InsContractNum": f"C-{sequence}"},
                        query_subparts={},
                    ))
                else:
                    statuses.append(SimpleNamespace(error_code="59"))
                return SimpleNamespace(statuses=statuses, http_status=200)

        records, summary = asyncio.run(_query_all_schedule_records(
            FakeService(),
            ftw_customer_id="customer",
            ftw_plan_id="plan",
            year="2025",
            slot_count=4,
        ))

        self.assertEqual([record["ftw_seq_no"] for record in records], ["1", "3"])
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["queried_slots"], 4)

    def test_snapshot_ignores_sequence_and_response_order(self):
        first = schedule_record("1", "Carrier A", "A-1", [{"Name1": "Broker A", "CommPdAmt01": "100"}])
        second = schedule_record("2", "Carrier B", "B-1", [{"Name1": "Broker B", "CommPdAmt01": "200"}])
        renumbered_first = {**first, "ftw_seq_no": "9"}
        renumbered_second = {**second, "ftw_seq_no": "8"}

        self.assertEqual(
            _canonical_schedule_set([first, second]),
            _canonical_schedule_set([renumbered_second, renumbered_first]),
        )

    def test_snapshot_detects_a_dropped_broker_row(self):
        baseline = schedule_record(
            "1",
            "Carrier A",
            "A-1",
            [
                {"Name1": "Broker A", "CommPdAmt01": "100"},
                {"Name2": "Broker B", "CommPdAmt02": "200"},
            ],
        )
        changed = schedule_record("1", "Carrier A", "A-1", [{"Name1": "Broker A", "CommPdAmt01": "100"}])

        self.assertNotEqual(_canonical_schedule_set([baseline]), _canonical_schedule_set([changed]))

    def test_snapshot_coalesces_fragmented_broker_fields(self):
        complete = schedule_record(
            "1",
            "Carrier A",
            "A-1",
            [
                {"Name1": "Broker A", "CommPdAmt01": "100"},
                {"Name2": "Broker B", "CommPdAmt02": "200"},
            ],
        )
        fragmented = schedule_record(
            "1",
            "Carrier A",
            "A-1",
            [
                {"Name1": "Broker A"},
                {"CommPdAmt01": "100"},
                {"Name2": "Broker B"},
                {"CommPdAmt02": "200"},
            ],
        )

        self.assertEqual(_canonical_schedule_record(complete), _canonical_schedule_record(fragmented))

    def test_matching_record_uses_business_identity_when_sequence_changes(self):
        baseline = schedule_record("1", "Carrier A", "A-1", [])
        other = schedule_record("8", "Carrier B", "B-1", [])
        renumbered = schedule_record("9", "Carrier A", "A-1", [])

        self.assertIs(_matching_schedule_record(baseline, [other, renumbered]), renumbered)

    def test_snapshot_can_ignore_only_the_controlled_broker_canary_field(self):
        baseline = schedule_record(
            "1", "Carrier A", "A-1", [{"Name1": "Broker A", "CommPdAmt01": "100", "FeesPdAmt01": "20"}]
        )
        changed = schedule_record(
            "1", "Carrier A", "A-1", [{"Name1": "Broker A", "CommPdAmt01": "101", "FeesPdAmt01": "20"}]
        )

        self.assertNotEqual(_canonical_schedule_record(baseline), _canonical_schedule_record(changed))
        self.assertEqual(
            _canonical_schedule_record(baseline, ignored_broker_tags={"CommPdAmtXX"}),
            _canonical_schedule_record(changed, ignored_broker_tags={"CommPdAmtXX"}),
        )


if __name__ == "__main__":
    unittest.main()
