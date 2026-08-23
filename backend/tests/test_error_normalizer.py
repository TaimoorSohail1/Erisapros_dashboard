from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.error_normalizer import normalize_client_error


class ErrorNormalizerTests(unittest.TestCase):
    def test_documented_permission_error_is_mapped_with_specific_recovery(self):
        error = normalize_client_error("DOLScheduleAData error 53: No permission for TransactionType: 2")

        self.assertEqual(error.code, "FTW_DOL_53")
        self.assertIn("permission", error.title.lower())
        self.assertIn("KeyID", error.next_action)

    def test_document_generation_error_is_not_reported_as_unknown(self):
        error = normalize_client_error("DOL error 79: Could not locate the specified DOL schedule.")

        self.assertEqual(error.code, "FTW_DOCUMENT_79")
        self.assertIn("schedule", error.message.lower())

    def test_locked_ftw_error_gets_actionable_message(self):
        error = normalize_client_error("Error 68: Transaction type 2 is not allowed if the filing is locked.")

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_LOCKED")
        self.assertIn("locked", error.title.lower())
        self.assertIn("Amend Filing", error.next_action)

    def test_invalid_xml_field_gets_xml_message(self):
        error = normalize_client_error("DOLScheduleAData error 60: invalid field req: FTWSeqNo")

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_INVALID_XML_FIELD")
        self.assertIn("XML", error.title)
        self.assertEqual(error.rejected_fields[0].tag, "FTWSeqNo")

    def test_invalid_xml_field_accepts_ftw_reg_wording(self):
        error = normalize_client_error("DOL5500Data error 60: ; invalid field reg: ADMIN_NAME0")

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_INVALID_XML_FIELD")
        self.assertEqual(error.rejected_fields[0].tag, "ADMIN_NAME0")

    def test_rejected_field_value_gets_specific_details(self):
        error = normalize_client_error("DOLScheduleAData error 62: InsCarrierNAICCode:000-69019")

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_FIELD_VALUE_REJECTED")
        self.assertEqual(error.rejected_fields[0].tag, "InsCarrierNAICCode")
        self.assertEqual(error.rejected_fields[0].value, "000-69019")
        self.assertEqual(error.rejected_fields[0].suggested_value, "69019")
        self.assertIn("NAIC", error.rejected_fields[0].reason)

    def test_schedule_a_match_error_is_warning(self):
        error = normalize_client_error(
            "Multiple FT Williams Schedule A records were found, but none clearly matched the extracted carrier/contract."
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_SCHEDULE_A_MATCH_REQUIRED")
        self.assertEqual(error.severity, "warning")
        self.assertIn("Select", error.next_action)

    def test_readback_mismatch_gets_verification_message(self):
        error = normalize_client_error(
            "FT Williams accepted the update, but read-back verification did not match the sent values (TotActivePartcpCnt)."
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_UPDATE_VERIFICATION_FAILED")
        self.assertIn("Query FTW Current", error.next_action)

    def test_empty_ftw_response_gets_specific_diagnostic_message(self):
        error = normalize_client_error(
            "FT Williams received the update request, but its response was empty or malformed, so the update "
            "could not be confirmed. FTW error PARSE_ERROR: no element found: line 1, column 0"
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "FTW_EMPTY_OR_MALFORMED_RESPONSE")
        self.assertIn("no usable response", error.title.lower())
        self.assertIn("Query FTW Current", error.next_action)


if __name__ == "__main__":
    unittest.main()
