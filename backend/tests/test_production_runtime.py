import os
import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.storage import StorageService


class ProductionRuntimeTests(unittest.TestCase):
    def test_production_settings_require_persistent_services(self):
        settings = Settings(app_environment="production", _env_file=None)

        with self.assertRaisesRegex(RuntimeError, "MONGODB_URI"):
            settings.validate_runtime()

    def test_production_settings_accept_database_and_s3(self):
        settings = Settings(
            app_environment="production",
            mongodb_uri="mongodb://example/erisapros",
            aws_region="eu-north-1",
            s3_bucket_name="erisapros-files",
            auth_enabled=True,
            cognito_region="eu-north-1",
            cognito_user_pool_id="eu-north-1_example",
            cognito_app_client_id="client-id",
            _env_file=None,
        )

        settings.validate_runtime()

    @patch("app.services.storage.boto3.client")
    @patch("app.services.storage.get_settings")
    def test_s3_uses_ecs_role_without_static_access_keys(self, get_settings, boto_client):
        get_settings.return_value = Settings(
            app_environment="production",
            mongodb_uri="mongodb://example/erisapros",
            aws_region="eu-north-1",
            s3_bucket_name="erisapros-files",
            auth_enabled=True,
            cognito_region="eu-north-1",
            cognito_user_pool_id="eu-north-1_example",
            cognito_app_client_id="client-id",
            _env_file=None,
        )
        s3 = MagicMock()
        boto_client.return_value = s3

        result = StorageService().save_pdf("plan.pdf", "application/pdf", b"pdf")

        boto_client.assert_called_once_with("s3", region_name="eu-north-1")
        s3.put_object.assert_called_once()
        self.assertTrue(result["uploaded"])
        self.assertEqual(result["bucket"], "erisapros-files")

    @patch("app.services.storage.get_settings")
    def test_production_storage_never_falls_back_to_local_disk(self, get_settings):
        get_settings.return_value = Settings(
            app_environment="production",
            mongodb_uri="mongodb://example/erisapros",
            aws_region="eu-north-1",
            auth_enabled=True,
            cognito_region="eu-north-1",
            cognito_user_pool_id="eu-north-1_example",
            cognito_app_client_id="client-id",
            _env_file=None,
        )

        with self.assertRaisesRegex(RuntimeError, "S3_BUCKET_NAME"):
            StorageService().save_pdf("plan.pdf", "application/pdf", b"pdf")


if __name__ == "__main__":
    unittest.main()
