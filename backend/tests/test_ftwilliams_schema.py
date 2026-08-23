import base64
import unittest
from datetime import datetime, timedelta

from app.models import FTWilliamsQueryResponse, FTWilliamsStatusItem, FormType
from app.services.ftwilliams_schema import FTWilliamsSchemaService


DOC_SCHEMA_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<ftwLinkResponse><Status><Type>DocSchema</Type><ErrorCode>0</ErrorCode><QueryResults>
  <row_1><VAR>GENERAL INFORMATION</VAR><Field_Type>Header</Field_Type></row_1>
  <row_2><VAR>SampleDate</VAR><Required>1</Required><Field_Type>Date</Field_Type><Format>MM/DD/YYYY</Format><PromptText>Sample date</PromptText><Max_Length>10</Max_Length></row_2>
  <row_3><VAR>SampleChoice</VAR><List_Values>1|Yes;2|No</List_Values><Required>0</Required><Field_Type>Select</Field_Type><PromptText>Sample choice</PromptText></row_3>
</QueryResults></Status></ftwLinkResponse>"""


class _Repository:
    def __init__(self):
        self.snapshots = {}

    async def get_ftwilliams_schema(self, cache_key):
        return self.snapshots.get(cache_key)

    async def upsert_ftwilliams_schema(self, snapshot):
        self.snapshots[snapshot.cache_key] = snapshot
        return snapshot


class _FTWilliams:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def send_xml(self, operation, request_xml):
        self.calls.append((operation, request_xml))
        return self.response


class _RaisingFTWilliams:
    async def send_xml(self, _operation, _request_xml):
        raise TimeoutError("schema endpoint timed out")


class FTWilliamsSchemaTests(unittest.IsolatedAsyncioTestCase):
    def test_doc_schema_decodes_ftw_csv_document_data(self):
        csv_data = (
            '"PromptText","Section","LineNo","VAR","Field_Type","List_Values","Max_Length","Default","Format","Disable_Logic","Required",\n'
            '"","1","","GENERAL INFORMATION","Heading","","","","","","No",\n'
            '"Plan Number","1","1","PlanNumber","Text","","3","","PNTYPE","","Yes",\n'
        )
        encoded = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")
        wrapped = f"{encoded[:20]}\n{encoded[20:]}"
        response = (
            "<ftwLinkResponse><Status><ErrorCode>0</ErrorCode>"
            f"<DocumentData>{wrapped}</DocumentData></Status></ftwLinkResponse>"
        )

        fields = FTWilliamsSchemaService.parse_doc_schema(response)

        self.assertEqual([field.var for field in fields], ["PlanNumber"])
        self.assertEqual(fields[0].prompt_text, "Plan Number")
        self.assertEqual(fields[0].max_length, 3)
        self.assertTrue(fields[0].required)

    async def test_doc_schema_is_retrieved_parsed_and_cached(self):
        response = FTWilliamsQueryResponse(
            operation="doc_schema",
            configured=True,
            sent=True,
            success=True,
            request_xml="<masked />",
            http_status=200,
            raw_response=DOC_SCHEMA_RESPONSE,
            statuses=[FTWilliamsStatusItem(type="DocSchema", error_code="0")],
        )
        repository = _Repository()
        ftwilliams = _FTWilliams(response)
        service = FTWilliamsSchemaService(ftwilliams=ftwilliams, repository=repository)

        first = await service.get_doc_schema("VolSub", "4KPT", "POST")
        second = await service.get_doc_schema("VolSub", "4KPT", "POST")

        self.assertEqual(first.status, "FRESH")
        self.assertEqual([field.var for field in first.fields], ["SampleDate", "SampleChoice"])
        self.assertEqual(first.fields[0].expected_format, "MM/DD/YYYY")
        self.assertEqual(first.fields[1].allowed_values, ["1", "2"])
        self.assertEqual(second.cache_key, first.cache_key)
        self.assertEqual(len(ftwilliams.calls), 1)
        self.assertIn("<Doc_Schema>", ftwilliams.calls[0][1])
        self.assertIn("<ChecklistVersion>POST</ChecklistVersion>", ftwilliams.calls[0][1])
        self.assertIn("<Format>CSV</Format>", ftwilliams.calls[0][1])

    async def test_expired_last_known_good_is_returned_when_refresh_fails(self):
        repository = _Repository()
        good_ftwilliams = _FTWilliams(
            FTWilliamsQueryResponse(
                operation="doc_schema",
                configured=True,
                sent=True,
                success=True,
                request_xml="<masked />",
                http_status=200,
                raw_response=DOC_SCHEMA_RESPONSE,
                statuses=[FTWilliamsStatusItem(type="DocSchema", error_code="0")],
            )
        )
        service = FTWilliamsSchemaService(ftwilliams=good_ftwilliams, repository=repository, ttl_seconds=1)
        snapshot = await service.get_doc_schema("VolSub", "4KPT", "POST")
        snapshot.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await repository.upsert_ftwilliams_schema(snapshot)

        failed_ftwilliams = _FTWilliams(
            FTWilliamsQueryResponse(
                operation="doc_schema",
                configured=True,
                sent=True,
                success=False,
                request_xml="<masked />",
                http_status=200,
                raw_response="",
                error="empty response",
            )
        )
        stale = await FTWilliamsSchemaService(
            ftwilliams=failed_ftwilliams,
            repository=repository,
            ttl_seconds=1,
        ).get_doc_schema("VolSub", "4KPT", "POST")

        self.assertEqual(stale.status, "STALE_LAST_KNOWN_GOOD")
        self.assertEqual(stale.fields[0].var, "SampleDate")
        self.assertIn("empty response", stale.last_error)

        timed_out = await FTWilliamsSchemaService(
            ftwilliams=_RaisingFTWilliams(),
            repository=repository,
            ttl_seconds=1,
        ).get_doc_schema("VolSub", "4KPT", "POST", force_refresh=True)
        self.assertEqual(timed_out.status, "STALE_LAST_KNOWN_GOOD")
        self.assertIn("timed out", timed_out.last_error)

    def test_outgoing_dol_xml_reports_exact_invalid_field_details(self):
        xml = """<ftwLink><DataBatch><DOLScheduleAData><TransactionType>2</TransactionType><Year>2025</Year><InsCarrierNAICCode>ABC</InsCarrierNAICCode><UnknownVendorTag>x</UnknownVendorTag></DOLScheduleAData></DataBatch></ftwLink>"""

        result = FTWilliamsSchemaService().validate_outgoing_xml(FormType.SCHEDULE_A, "2025", xml)

        self.assertFalse(result.valid)
        by_tag = {issue.tag: issue for issue in result.issues}
        self.assertEqual(by_tag["InsCarrierNAICCode"].value, "ABC")
        self.assertIn("5 digits", by_tag["InsCarrierNAICCode"].expected_format)
        self.assertIn("not verified as writable", by_tag["UnknownVendorTag"].reason)
        self.assertEqual(result.schema_source, "DOCUMENTED_STATIC_DOL")

    def test_outgoing_form_5500_blocks_schema_field_not_verified_for_update(self):
        xml = """<ftwLink><DataBatch><DOL5500Data><TransactionType>2</TransactionType><Year>2025</Year><ADMIN_NAME0>New Administrator</ADMIN_NAME0><TotActivePartcpCnt>125</TotActivePartcpCnt></DOL5500Data></DataBatch></ftwLink>"""

        result = FTWilliamsSchemaService().validate_outgoing_xml(
            FormType.FORM_5500,
            "2025",
            xml,
        )

        self.assertFalse(result.valid)
        self.assertEqual([issue.tag for issue in result.issues], ["ADMIN_NAME0"])
        issue = result.issues[0]
        self.assertIn("not verified as writable", issue.reason)
        self.assertIn("read-only", issue.correction)

    def test_outgoing_schedule_a_accepts_documented_repeatable_broker_tags(self):
        xml = """<ftwLink><DataBatch><DOLScheduleAData><TransactionType>2</TransactionType><Year>2025</Year><DOLSubPartData><Broker><NameXX>NFP Corporate Services</NameXX><AddressLine1XX>PO Box 786677</AddressLine1XX><CityXX>Philadelphia</CityXX><StateXX>PA</StateXX><ZipCodeXX>19178</ZipCodeXX><CommPdAmtXX>18603</CommPdAmtXX><FeesPdAmtXX>1397</FeesPdAmtXX><FeesPdTextXX>COMMISSIONS AND FEES</FeesPdTextXX><CodeXX>3</CodeXX></Broker></DOLSubPartData></DOLScheduleAData></DataBatch></ftwLink>"""

        result = FTWilliamsSchemaService().validate_outgoing_xml(
            FormType.SCHEDULE_A, "2025", xml
        )

        self.assertTrue(result.valid, result.issues)

    def test_outgoing_schedule_a_allows_only_exact_trusted_preserved_value(self):
        xml = """<ftwLink><DataBatch><DOLScheduleAData><TransactionType>2</TransactionType><Year>2025</Year><VendorReturnedField>unchanged</VendorReturnedField></DOLScheduleAData></DataBatch></ftwLink>"""
        service = FTWilliamsSchemaService()

        preserved = service.validate_outgoing_xml(
            FormType.SCHEDULE_A,
            "2025",
            xml,
            trusted_preserved_values={("VendorReturnedField", "unchanged")},
        )
        changed = service.validate_outgoing_xml(
            FormType.SCHEDULE_A,
            "2025",
            xml.replace("unchanged", "changed"),
            trusted_preserved_values={("VendorReturnedField", "unchanged")},
        )

        self.assertTrue(preserved.valid)
        self.assertFalse(changed.valid)


if __name__ == "__main__":
    unittest.main()
