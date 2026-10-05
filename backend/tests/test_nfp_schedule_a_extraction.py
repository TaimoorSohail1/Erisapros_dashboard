import asyncio
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import NormalizedExtractionField, NormalizedExtractionResult
from app.services.extractor import (
    ExtractionService,
    _extract_fields_from_pages,
    extract_ameritas_schedule_a_broker_rows,
    extract_ameritas_schedule_a_fields,
    extract_continental_american_broker_rows,
    extract_continental_american_schedule_a_fields,
    extract_curalinc_broker_rows,
    extract_curalinc_schedule_a_fields,
    extract_email_schedule_a_fields,
    extract_filled_irs_schedule_a_fields,
    has_blank_schedule_a_form_layer,
    extract_schedule_a_acroform_data,
    remove_inapplicable_experience_rated_fields,
    supplement_schedule_a_result_with_local,
)


def values(fields):
    return {field.field_name: field.value for field in fields}


class NFPScheduleAExtractionTests(unittest.TestCase):
    def test_acroform_values_are_authoritative_over_hidden_sample_layer(self):
        fields, brokers = extract_schedule_a_acroform_data(
            {
                "insCarrierName": "PRE-PAID LEGAL SERVICES INC dba LEGALSHIELD",
                "insCarrierEIN": "73-1016728",
                "insCarrierNAICCode": "00000",
                "insContractNum": "203812",
                "insPrsnCoveredEoyCnt": "1252",
                "insPolicyFromDate": "01.01.2025",
                "insPolicyToDate": "12.31.2025",
                "insBrokerCommTotAmt": "33,193.53",
                "insBrokerName_01": "NFP CORPORATE SERVICES NY LLC",
                "insBrokerAddress_01": "200 PARK AVE\rRM 3202\rNEW YORK NY 10166",
                "insBrokerCommPdAmt_01": "33,193.53",
                "insBrokerCode_01": "4",
                "wlfrTotChargesPaidAmt": "195,256.06",
            }
        )
        extracted = values(fields)
        self.assertEqual(extracted["1a. Name of Insurance Company"], "PRE-PAID LEGAL SERVICES INC dba LEGALSHIELD")
        self.assertEqual(extracted["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(extracted["10a. Total premiums or subscription charges paid to carrier"], "195,256.06")
        self.assertEqual(len(brokers), 1)
        self.assertEqual((brokers[0].city, brokers[0].state, brokers[0].zip_code), ("NEW YORK", "NY", "10166"))

    def test_visible_overlay_removes_hidden_sample_candidates_during_merge(self):
        remote = NormalizedExtractionResult(
            provider="remote",
            fields=[
                NormalizedExtractionField(
                    field_name="9a. Premiums: (1) Amount Received",
                    value="12345",
                    confidence=0.99,
                ),
                NormalizedExtractionField(
                    field_name="1a. Name of Insurance Company",
                    value="ABCDEFGHI",
                    confidence=0.99,
                ),
            ],
        )
        local = NormalizedExtractionResult(
            provider="AcroForm",
            fields=[
                NormalizedExtractionField(
                    field_name="1a. Name of Insurance Company",
                    value="PRE-PAID LEGAL SERVICES INC dba LEGALSHIELD",
                    confidence=0.995,
                ),
                NormalizedExtractionField(
                    field_name="10a. Total premiums or subscription charges paid to carrier",
                    value="195,256.06",
                    confidence=0.995,
                ),
            ],
            raw={"authoritative_visible_overlay": True},
        )

        merged = values(supplement_schedule_a_result_with_local(remote, local).fields)

        self.assertEqual(
            merged["1a. Name of Insurance Company"],
            "PRE-PAID LEGAL SERVICES INC dba LEGALSHIELD",
        )
        self.assertNotIn("9a. Premiums: (1) Amount Received", merged)

    def test_filled_irs_ocr_layout_does_not_return_labels_as_values(self):
        page_texts = [(1, """
            SCHEDULE A (Form 5500) Insurance Information
            (a) Name of insurance company (b) EIN (c) NAIC code (d) Contract number
            Tuned Care 853889665 525120 8,625 01/01/2025 12/31/2025
            Total amount of commissions paid 0 Total amount of fees paid 0
            10 Nonexperience-rated contracts
            Total premiums or subscription charges paid to carrier 98,796.44
        """)]
        extracted = values(extract_filled_irs_schedule_a_fields(page_texts))
        self.assertEqual(extracted["1a. Name of Insurance Company"], "Tuned Care")
        self.assertEqual(extracted["1b. Insurance Carrier EIN"], "85-3889665")
        self.assertEqual(extracted["1c. NAIC Code"], "525120")
        self.assertEqual(extracted["1e. Persons Covered (End of Policy Year)"], "8625")
        self.assertEqual(extracted["10a. Total premiums or subscription charges paid to carrier"], "98,796.44")
        self.assertNotIn("3d. Purpose", extracted)

    def test_filled_irs_ocr_layout_supports_separate_carrier_and_value_rows(self):
        page_texts = [(1, """
            SCHEDULE A (Form 5500) Insurance Information
            1 Coverage Information:
            (a) Name of insurance carrier Tuned Care
            (b) EIN (c) NAIC code (d) Contract or identification number
            853889665 525120 8,625 1/1/2025 12/31/2025
            2 Insurance fee and commission information
            Total amount of commissions paid $0 Total amount of fees paid $0
            10 Nonexperience-rated contracts
            Total premiums or subscription charges paid to carrier 98,796.44
        """)]

        extracted = values(extract_filled_irs_schedule_a_fields(page_texts))

        self.assertEqual(extracted["1a. Name of Insurance Company"], "Tuned Care")
        self.assertEqual(extracted["1b. Insurance Carrier EIN"], "85-3889665")
        self.assertEqual(extracted["1e. Persons Covered (End of Policy Year)"], "8625")

    def test_hidden_sample_layer_uses_visible_ocr_overlay(self):
        hidden_template = [(1, """
            SCHEDULE A FORM 5500 INSURANCE INFORMATION
            ABCDEFGHI ABCDEFGHI ABCDEFGHI ABCDEFGHI ABCDEFGHI ABCDEFGHI ABCDEFGHI ABCDEFGHI
            123456789012345 123456789012345 123456789012345 YYYY-MM-DD YYYY-MM-DD 012345678
        """)]
        visible_overlay = [(1, """
            SCHEDULE A (Form 5500) Insurance Information
            (a) Name of insurance company (b) EIN (c) NAIC code
            Tuned Care 85-3889665 525120 8625 01/01/2025 12/31/2025
            10 Nonexperience-rated contracts
            Total premiums or subscription charges paid to carrier 98,796.44
        """)]
        service = ExtractionService()
        unresolved = NormalizedExtractionResult(
            provider="local OCR",
            fields=extract_filled_irs_schedule_a_fields(visible_overlay),
            raw={"local_ocr_pages": [{"page": 1, "text": visible_overlay[0][1]}]},
        )
        settings = SimpleNamespace(
            schedule_a_canonical_validation_enabled=False,
            schedule_a_canonical_validation_shadow_enabled=True,
        )
        with (
            patch("app.services.extractor.extract_document_text_pages", return_value=hidden_template),
            patch("app.services.extractor.extract_image_only_pdf_ocr_pages", return_value=visible_overlay),
            patch("app.services.extractor.extract_pdf_layout_text_pages", return_value=visible_overlay),
            patch.object(service, "_extract_schedule_a_unresolved", AsyncMock(return_value=unresolved)),
            patch("app.services.extractor.get_settings", return_value=settings),
        ):
            result = asyncio.run(service.extract_schedule_a(b"%PDF flattened", "Tuned.pdf"))
        self.assertEqual(values(result.fields)["1a. Name of Insurance Company"], "Tuned Care")
        self.assertNotIn("UNFILLED_SCHEDULE_A_TEMPLATE", result.classification_signals)

    def test_clean_blank_irs_layer_requires_visual_overlay(self):
        blank_layer = [(1, """
            SCHEDULE A (Form 5500) Insurance Information
            Name of insurance carrier (b) EIN (c) NAIC code
            Total amount of commissions paid Total amount of fees paid
            10 Nonexperience-rated contracts
        """)]

        self.assertTrue(has_blank_schedule_a_form_layer(blank_layer))

    def test_ameritas_letter_extracts_complete_identity_and_fee_broker(self):
        pages = [(1, """
            Policy #026-202629
            Re: Schedule A (Form 5500) Information
            Schedule A (Form 5500) information For January 1, 2025 Through December 31, 2025
            Gross Premium Paid: $657,749.00
            Benefit: Vision
            Tax ID: 13-3758127 NAIC code: 60033
            Total 4352 9574
            Broker Name and Address Commission Fees
            NFP CORPORATE SERVICES NY LLC $0.00 $6,289.00
            PO BOX 9101
            PLAINVIEW NY 11803 9001
        """)]
        extracted = values(extract_ameritas_schedule_a_fields(pages))
        self.assertEqual(extracted["1a. Name of Insurance Company"], "Ameritas Life Insurance Corp. of New York")
        self.assertEqual(extracted["1d. Contract/Policy Number"], "026-202629")
        self.assertEqual(extracted["1e. Persons Covered (End of Policy Year)"], "9574")
        self.assertEqual(extracted["3c. Amount of Fees"], "6,289.00")
        brokers = extract_ameritas_schedule_a_broker_rows(pages)
        self.assertEqual((brokers[0].name, brokers[0].fee_total), ("NFP CORPORATE SERVICES NY LLC", "6,289.00"))

    def test_curalinc_letter_extracts_identity_period_participants_and_fee(self):
        pages = [(1, """
            CuraLinc LLC Schedule A Information Form 5500
            Employer/Plan Sponsor Name NFP
            Contract ID#: 01804
            Plan Year 1/1/25 - 4/30/25
            Total US Participants at End of Plan Year 6,045
            Name of Service Provider CuraLinc LLC
            Service Provider's Employer Identification Number (EIN) 33-1206383
            Actual Service Provided EAP
            Fees Paid by Employer/Plan Sponsor for US Participants $21,762.00
            Carrier NAIC 624190
        """)]
        extracted = values(extract_curalinc_schedule_a_fields(pages))
        self.assertEqual(extracted["1d. Contract/Policy Number"], "01804")
        self.assertEqual(extracted["1e. Persons Covered (End of Policy Year)"], "6,045")
        self.assertEqual(extracted["1g. Policy Year Ending Date"], "04/30/2025")
        self.assertEqual(extracted["3c. Amount of Fees"], "21,762.00")
        self.assertEqual(extract_curalinc_broker_rows(pages)[0].name, "CuraLinc LLC")

    def test_continental_report_extracts_end_date_and_broker_row(self):
        pages = [(1, """
            Carrier Name : Continental American Insurance Company
            Carrier EIN : 57-0514130 Carrier NAIC Code : 71730
            Contract Number : 0000024819
            For The FY/CY Beginning : 1/1/2025 Ending : 12/31/2025
            ESTIMATED NUMBER OF COVERED EMPLOYEES @ YEAR END :1777
            Gross Premiums Paid : $943,913.44
            Total Commissions Paid : $212,581.85
            AAP01 NFP CORPORATE SERVICES (NY) LLC 200 Park Avenue 32nd Floor New York, NY 10166 $212,581.85
        """)]
        extracted = values(extract_continental_american_schedule_a_fields(pages))
        self.assertEqual(extracted["1g. Policy Year Ending Date"], "12/31/2025")
        brokers = extract_continental_american_broker_rows(pages)
        self.assertEqual((brokers[0].city, brokers[0].commission_total), ("New York", "212,581.85"))

    def test_ansel_email_extracts_full_identity_and_preserves_policy_prefix(self):
        pages = [(1, """
            Premium Amount Paid - $737,398.05
            Premium paid to: Ansel Services, Inc. - EIN - 84-4726657
            FSL NAIC (Underwriting Company) - 71870
            Enrollment count: 2180
            Policy Number: LB-10000116
            Benefit Type: Supplemental Health
            Commission paid to - N/A
            Commission Amount Paid - N/A
        """)]
        extracted = values(extract_email_schedule_a_fields(pages))
        self.assertEqual(extracted["1a. Name of Insurance Company"], "Ansel Services, Inc.")
        self.assertEqual(extracted["1b. Insurance Carrier EIN"], "84-4726657")
        self.assertEqual(extracted["1c. NAIC Code"], "71870")
        self.assertEqual(extracted["1d. Contract/Policy Number"], "LB-10000116")
        self.assertEqual(extracted["1e. Persons Covered (End of Policy Year)"], "2180")
        self.assertEqual(extracted["10a. Total premiums or subscription charges paid to carrier"], "737,398.05")

    def test_bcbs_extract_supports_unhyphenated_ein_and_named_dates(self):
        pages = [(1, """
            EXTRACT FROM SCHEDULE A (Form 5500)
            a. Name of insurance carrier: Blue Cross and Blue Shield of Vermont
            b. Employer Identification Number: 030277307
            c. NAIC Code: 00053295
            d. Contract or identification number: 369027555
            e. Approximate number of persons covered at end of policy or contract year: 116
            f. From: 01-Jan-25 g. To: 31-Dec-25
            Total amount of commissions paid $0.00
            Total amount of fees paid $0.00
            10. Nonexperience-rated contracts:
            Total premiums or subscription charges paid to carrier $1,096,314
        """)]
        extracted = values(_extract_fields_from_pages(pages))
        self.assertEqual(extracted["1b. Insurance Carrier EIN"], "03-0277307")
        self.assertEqual(extracted["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(extracted["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertNotIn("3d. Purpose", extracted)

    def test_zero_experience_section_is_removed_when_line_10_has_premium(self):
        result = NormalizedExtractionResult(
            provider="test",
            fields=[
                NormalizedExtractionField(field_name="9a. Premiums: (1) Amount Received", value="0.00", confidence=0.9),
                NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value="1,096,314", confidence=0.9),
            ],
        )
        cleaned = remove_inapplicable_experience_rated_fields(
            result,
            [(1, "9. Experience-rated contracts Amount received $0.00 10. Nonexperience-rated contracts Total premiums $1,096,314")],
        )
        self.assertEqual([field.field_name for field in cleaned.fields], ["10a. Total premiums or subscription charges paid to carrier"])


if __name__ == "__main__":
    unittest.main()
