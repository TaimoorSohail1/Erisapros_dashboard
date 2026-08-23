import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.models import FTWilliamsQueryRequest
from app.services.ftwilliams import FTWilliamsService


def run_async(coro):
    return asyncio.run(coro)


class FTWilliamsServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = FTWilliamsService()

    def test_builds_schedule_a_query_xml(self):
        xml = self.service.build_request_xml(
            FTWilliamsQueryRequest(
                operation="query_schedule_a",
                customer_id="SOAPDEMO",
                plan_id="SDPLAN",
                year="2004",
            )
        )

        self.assertIn("<DOLScheduleAData>", xml)
        self.assertIn("<TransactionType>Q</TransactionType>", xml)
        self.assertIn("<CustomerID>SOAPDEMO</CustomerID>", xml)
        self.assertIn("<PlanID>SDPLAN</PlanID>", xml)
        self.assertIn("<Year>2004</Year>", xml)

    def test_prefers_ftw_ids_when_both_identifier_sets_are_present(self):
        xml = self.service.build_request_xml(
            FTWilliamsQueryRequest(
                operation="query_5500",
                customer_id="33-0574214",
                plan_id="33-0574214501",
                ftw_customer_id="987654",
                ftw_plan_id="123456",
                year="2024",
            )
        )

        self.assertIn("<FTWCustomerID>987654</FTWCustomerID>", xml)
        self.assertIn("<FTWPlanID>123456</FTWPlanID>", xml)
        self.assertNotIn("<CustomerID>33-0574214</CustomerID>", xml)
        self.assertNotIn("<PlanID>33-0574214501</PlanID>", xml)

    def test_builds_compliance_run_all_query_xml_with_ftw_ids(self):
        xml = self.service.build_request_xml(
            FTWilliamsQueryRequest(
                operation="run_all_tests",
                ftw_customer_id="1234",
                ftw_plan_id="3456",
                year_end="2024-12-31",
            )
        )

        self.assertIn("<Compliance_TasksRunAll>", xml)
        self.assertIn("<FTWCustomerID>1234</FTWCustomerID>", xml)
        self.assertIn("<FTWPlanID>3456</FTWPlanID>", xml)
        self.assertIn("<TransactionType>Q</TransactionType>", xml)
        self.assertIn("<YearEnd>2024-12-31</YearEnd>", xml)

    def test_builds_dol_schedule_a_pdf_request_and_decodes_pdf(self):
        xml = self.service.build_generate_dol_request(
            ftw_customer_id="1234",
            ftw_plan_id="5678",
            year="2025",
            document="A",
            ftw_seq_no="3",
        )
        encoded = base64.b64encode(b"%PDF-1.7\nverified").decode("ascii")
        wrapped_encoded = f"{encoded[:8]}\n{encoded[8:]}"
        response = (
            "<ftwLinkResponse><Status><Type>DOL</Type><Document>A</Document>"
            f"<ErrorCode>0</ErrorCode><DocumentData>{wrapped_encoded}</DocumentData>"
            "</Status></ftwLinkResponse>"
        )

        self.assertIn("<GenerateDocument>", xml)
        self.assertIn("<FTWCustomerID>1234</FTWCustomerID>", xml)
        self.assertIn("<FTWPlanID>5678</FTWPlanID>", xml)
        self.assertIn("<Type>DOL</Type><Document>ScheduleA</Document><Year>2025</Year>", xml)
        self.assertIn("<FTWSeqNo>3</FTWSeqNo>", xml)
        self.assertEqual(self.service.parse_document_data(response), b"%PDF-1.7\nverified")

    def test_builds_archive_5500_get_data_lookup_xml(self):
        xml = self.service.build_request_xml(
            FTWilliamsQueryRequest(
                operation="archive_5500_get_data",
                company_employer_id="73-0759701",
                plan_number="501",
            )
        )

        self.assertIn("<Archive5500>", xml)
        self.assertIn("<TransactionType>GetData</TransactionType>", xml)
        self.assertIn("<CompanyEmployerID>73-0759701</CompanyEmployerID>", xml)
        self.assertIn("<PlanNumber>501</PlanNumber>", xml)

    def test_builds_plan_ids_batch_query_without_guessing_user_ids(self):
        xml = self.service.build_request_xml(
            FTWilliamsQueryRequest(operation="plan_ids_batch")
        )

        self.assertIn("<PlanIDs_Batch>", xml)
        self.assertIn("<TransactionType>Q</TransactionType>", xml)
        self.assertNotIn("<CustomerID>", xml)
        self.assertNotIn("<PlanID>", xml)
        self.assertNotIn("<FTWCustomerID>", xml)

    def test_parses_plan_ids_batch_records(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>PlanIDs_Batch</Type>
    <ErrorCode>0</ErrorCode>
    <CustomerID>OHIO-COMPANY</CustomerID>
    <PlanID>OHIO-PLAN</PlanID>
    <FTWCustomerID>387302687</FTWCustomerID>
    <FTWPlanID>987654321</FTWPlanID>
    <QueryResults><CompanyEmployerID>34-1655024</CompanyEmployerID><PlanNumber>501</PlanNumber></QueryResults>
  </Status>
  <Status>
    <Type>PlanIDs_Batch</Type>
    <ErrorCode>0</ErrorCode>
    <CustomerID>OTHER-COMPANY</CustomerID>
    <PlanID>OTHER-PLAN</PlanID>
    <FTWCustomerID>111</FTWCustomerID>
    <FTWPlanID>222</FTWPlanID>
  </Status>
</ftwLinkResponse>"""

        records = self.service.parse_plan_ids_batch_response(response_xml)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["CustomerID"], "OHIO-COMPANY")
        self.assertEqual(records[0]["FTWPlanID"], "987654321")
        self.assertEqual(records[0]["CompanyEmployerID"], "34-1655024")

    def test_parse_schedule_a_query_response(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>DOLScheduleA</Type>
    <FTWCustomerID>4115</FTWCustomerID>
    <FTWPlanID>4467</FTWPlanID>
    <Year>2004</Year>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <INS_CARRIER_NAME>Jonathan Financial Services</INS_CARRIER_NAME>
      <INS_CARRIER_EIN>881231313</INS_CARRIER_EIN>
      <INS_CONTRACT_NUM>ABC-123</INS_CONTRACT_NUM>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].type, "DOLScheduleA")
        self.assertEqual(statuses[0].error_code, "0")
        self.assertEqual(statuses[0].query_results["INS_CARRIER_NAME"], "Jonathan Financial Services")
        self.assertTrue(self.service.response_success(statuses))

    def test_parse_failure_and_success_field_list_response(self):
        response_xml = """<?xml version='1.0' encoding='UTF-8'?>
<ftwLinkResponse>
  <Status>
    <Type>DOL5500</Type>
    <ErrorCode>17</ErrorCode>
    <ErrorDesc>SPONS_DFE_STR_ADDRESS=&gt;812,N.,Wisconsin,Ave.</ErrorDesc>
  </Status>
  <Status>
    <Type>DOL5500</Type>
    <ErrorCode>0</ErrorCode>
    <StatusSuccess>SPONS_DFE_STATE,SPONSOR_DFE_NAME0,ADMIN_NAME0</StatusSuccess>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual([status.error_code for status in statuses], ["17", "0"])
        self.assertEqual(statuses[1].successful_fields, ["SPONS_DFE_STATE", "SPONSOR_DFE_NAME0", "ADMIN_NAME0"])
        self.assertFalse(self.service.response_success(statuses))

    def test_parse_archive_lookup_response(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>Archive5500</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <CompanyName>Crest Discount Foods, Inc.</CompanyName>
      <PlanLine1>Crest Discount Foods, Inc. Flexible Benefits Plan</PlanLine1>
      <CompanyEmployerID>73-0759701</CompanyEmployerID>
      <PlanNumber>501</PlanNumber>
      <FTWCustomerID>1234</FTWCustomerID>
      <FTWPlanID>3456</FTWPlanID>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        matches = self.service.parse_archive_lookup_response(response_xml)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["CompanyEmployerID"], "73-0759701")
        self.assertEqual(matches[0]["PlanNumber"], "501")
        self.assertEqual(matches[0]["FTWCustomerID"], "1234")

    def test_parse_response_flattens_nested_query_results(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>ScheduleA</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <InsCarrierName>Carrier</InsCarrierName>
      <Broker>
        <Name1>NFP LLC</Name1>
        <CommPdAmt01>111893</CommPdAmt01>
      </Broker>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual(statuses[0].query_results["InsCarrierName"], "Carrier")
        self.assertEqual(statuses[0].query_results["Name1"], "NFP LLC")
        self.assertEqual(statuses[0].query_results["CommPdAmt01"], "111893")

    def test_parse_response_preserves_each_schedule_a_broker_row(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>ScheduleA</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <InsCarrierName>Carrier</InsCarrierName>
      <Broker>
        <Name1>First Broker</Name1>
        <CommPdAmt01>100</CommPdAmt01>
      </Broker>
      <Broker>
        <Name02>Second Broker</Name02>
        <FeesPdAmt02>200</FeesPdAmt02>
      </Broker>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual(
            statuses[0].query_subparts,
            {
                "Broker": [
                    {"Name1": "First Broker", "CommPdAmt01": "100"},
                    {"Name02": "Second Broker", "FeesPdAmt02": "200"},
                ]
            },
        )

    def test_parse_response_marks_combined_schedule_a_query_results_as_multiple_records(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>ScheduleA</Type>
    <ErrorCode>0</ErrorCode>
    <FTWSeqNo>2</FTWSeqNo>
    <QueryResults>
      <ScheduleDesc>CIGNA</ScheduleDesc>
      <InsCarrierName>CIGNA HEALTH AND LIFE INSURANCE COMPANY</InsCarrierName>
      <Broker><Name1>First Broker</Name1></Broker>
      <ScheduleDesc>GUARDIAN</ScheduleDesc>
      <InsCarrierName>GUARDIAN LIFE INSURANCE COMPANY</InsCarrierName>
      <Broker><Name1>Second Broker</Name1></Broker>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual(statuses[0].query_result_record_count, 2)

    def test_parse_edit_checks_ignores_nested_result_status_elements(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>DOL5500Data</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults><Status>OK</Status></QueryResults>
  </Status>
  <Status>
    <Type>DOLScheduleA_1_Data</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults><Status>NOT-OK</Status><FW-651>Warning text</FW-651></QueryResults>
  </Status>
</ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertEqual(len(statuses), 2)
        self.assertTrue(self.service.response_success(statuses))
        self.assertFalse(self.service.edit_checks_success(statuses))
        self.assertEqual(statuses[1].query_results["Status"], "NOT-OK")
        self.assertEqual(statuses[1].query_results["FW-651"], "Warning text")

    def test_parse_edit_check_issues_keeps_schedule_field_and_correction(self):
        response_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>DOLScheduleA_10_Data</Type><ErrorCode>0</ErrorCode>
    <QueryResults>
      <ScheduleDesc>3341244</ScheduleDesc><SeqNo>10</SeqNo>
      <FW-117>Warning:::Number of Persons Covered may not be blank.</FW-117>
      <FW-617>Warning:::Part 1, Line 3 Brokers must start with the highest amount paid and work in descending order.</FW-617>
      <Status>NOT-OK</Status>
    </QueryResults>
  </Status>
</ftwLinkResponse>"""

        issues = self.service.parse_edit_check_issues(self.service.parse_response(response_xml))

        self.assertEqual(len(issues), 2)
        persons_covered = next(issue for issue in issues if issue.code == "FW-117")
        self.assertEqual(persons_covered.schedule_seq_no, "10")
        self.assertEqual(persons_covered.schedule_desc, "3341244")
        self.assertEqual(persons_covered.field_label, "1e. Persons Covered (End of Policy Year)")
        self.assertEqual(persons_covered.current_value, "Blank")
        self.assertIn("Enter", persons_covered.correction)
        broker_order = next(issue for issue in issues if issue.code == "FW-617")
        self.assertEqual(broker_order.field_line, "3")
        self.assertIn("descending", broker_order.correction.lower())

    def test_edit_checks_succeed_when_every_result_is_ok(self):
        response_xml = """<ftwLinkResponse><Status>
          <Type>DOL5500Data</Type><ErrorCode>0</ErrorCode>
          <QueryResults><Status>OK</Status></QueryResults>
        </Status></ftwLinkResponse>"""

        statuses = self.service.parse_response(response_xml)

        self.assertTrue(self.service.edit_checks_success(statuses))

    def test_run_query_reuses_http_client_across_requests(self):
        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.post_calls = 0
                self.closed = False

            async def post(self, *args, **kwargs):
                self.post_calls += 1
                return httpx.Response(200, text="""<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>DOL5500</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults><PLAN_NAME0>Test Plan</PLAN_NAME0></QueryResults>
  </Status>
</ftwLinkResponse>""")

            async def aclose(self):
                self.closed = True

        clients = []

        def fake_client_factory(*args, **kwargs):
            client = FakeAsyncClient(*args, **kwargs)
            clients.append(client)
            return client

        settings = SimpleNamespace(
            ftwlink_key_id="test-key-id",
            ftwlink_endpoint_url="https://example.test/ftw",
            ftwlink_sandbox_customer_id=None,
            ftwlink_sandbox_plan_id=None,
            ftwlink_sandbox_year=None,
            ftwlink_sandbox_ftw_customer_id=None,
            ftwlink_sandbox_ftw_plan_id=None,
            ftwlink_sandbox_year_end=None,
        )
        payload = FTWilliamsQueryRequest(operation="query_5500", customer_id="73-1185740", plan_id="73-1185740501", year="2024", send=True)

        with patch("app.services.ftwilliams.get_settings", return_value=settings), patch(
            "app.services.ftwilliams.httpx.AsyncClient", side_effect=fake_client_factory
        ):
            first = run_async(self.service.run_query(payload))
            second = run_async(self.service.run_query(payload))

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].post_calls, 2)

    def test_run_query_retries_connect_errors_without_closing_shared_client(self):
        class FakeAsyncClient:
            def __init__(self, behavior):
                self.behavior = behavior
                self.closed = False

            async def post(self, *args, **kwargs):
                outcome = self.behavior.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            async def aclose(self):
                self.closed = True

        first_response = httpx.ConnectError("getaddrinfo failed")
        second_response = httpx.Response(200, text="""<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>DOL5500</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults><PLAN_NAME0>Recovered Plan</PLAN_NAME0></QueryResults>
  </Status>
</ftwLinkResponse>""")
        client_behaviors = [[first_response], [second_response]]
        clients = []

        def fake_client_factory(*args, **kwargs):
            client = FakeAsyncClient(client_behaviors[len(clients)])
            clients.append(client)
            return client

        async def fake_sleep(*args, **kwargs):
            return None

        settings = SimpleNamespace(
            ftwlink_key_id="test-key-id",
            ftwlink_endpoint_url="https://example.test/ftw",
            ftwlink_sandbox_customer_id=None,
            ftwlink_sandbox_plan_id=None,
            ftwlink_sandbox_year=None,
            ftwlink_sandbox_ftw_customer_id=None,
            ftwlink_sandbox_ftw_plan_id=None,
            ftwlink_sandbox_year_end=None,
        )
        payload = FTWilliamsQueryRequest(operation="query_5500", customer_id="73-1185740", plan_id="73-1185740501", year="2024", send=True)

        with patch("app.services.ftwilliams.get_settings", return_value=settings), patch(
            "app.services.ftwilliams.httpx.AsyncClient", side_effect=fake_client_factory
        ), patch("app.services.ftwilliams.asyncio.sleep", side_effect=fake_sleep):
            result = run_async(self.service.run_query(payload))

        self.assertTrue(result.success)
        self.assertEqual(len(clients), 2)
        self.assertFalse(clients[0].closed)
        self.assertTrue(clients[1].closed)


if __name__ == "__main__":
    unittest.main()
