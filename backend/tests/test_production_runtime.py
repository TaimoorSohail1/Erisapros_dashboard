import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from starlette.requests import Request

from app.config import Settings
from app.api.sharefile import valid_webhook_token
from app.services.storage import StorageService


class ProductionRuntimeTests(unittest.TestCase):
    def test_production_enables_authoritative_schedule_a_semantic_validation(self):
        template = Path(__file__).resolve().parents[2] / "deploy" / "aws" / "cloudformation.yaml"
        contents = template.read_text(encoding="utf-8")

        self.assertEqual(contents.count("- Name: SCHEDULE_A_CANONICAL_VALIDATION_ENABLED"), 2)
        self.assertEqual(contents.count('- Name: SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED'), 2)
        self.assertEqual(contents.count('Value: "true" # authoritative Schedule A semantic validation'), 2)
        self.assertEqual(contents.count('Value: "false" # shadow mode disabled after authoritative release'), 2)

    def test_production_proxy_timeouts_allow_slow_ftw_current_queries(self):
        template = Path(__file__).resolve().parents[2] / "deploy" / "aws" / "cloudformation.yaml"
        contents = template.read_text(encoding="utf-8")

        self.assertIn("idle_timeout.timeout_seconds", contents)
        self.assertIn('Value: "120"', contents)
        self.assertIn("OriginReadTimeout: 120", contents)
        self.assertIn("OriginKeepaliveTimeout: 60", contents)

    def test_production_uses_plan_specific_ftw_deep_link_template(self):
        template = Path(__file__).resolve().parents[2] / "deploy" / "aws" / "cloudformation.yaml"
        contents = template.read_text(encoding="utf-8")
        expected = (
            'Value: "https://ftwilliam.com/cgi-bin/index.cgi?'
            '#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&'
            'plan={ftw_customer_id},{ftw_plan_id}&Year={year}"'
        )

        self.assertEqual(contents.count(expected), 2)

    def test_production_container_does_not_log_webhook_query_secrets(self):
        dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"

        self.assertIn("--no-access-log", dockerfile.read_text(encoding="utf-8"))

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
            sharefile_webhook_token="webhook-token",
            sharefile_work_queue_url="https://sqs.eu-north-1.amazonaws.com/123/work",
            _env_file=None,
        )

        settings.validate_runtime()

    def test_production_settings_reject_localhost_database(self):
        settings = Settings(
            app_environment="production",
            mongodb_uri="mongodb://localhost:27017/erisapros",
            aws_region="eu-north-1",
            s3_bucket_name="erisapros-files",
            auth_enabled=True,
            cognito_region="eu-north-1",
            cognito_user_pool_id="eu-north-1_example",
            cognito_app_client_id="client-id",
            sharefile_webhook_token="webhook-token",
            _env_file=None,
        )

        with self.assertRaisesRegex(RuntimeError, "remote MongoDB"):
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
            sharefile_webhook_token="webhook-token",
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
            sharefile_webhook_token="webhook-token",
            _env_file=None,
        )

        with self.assertRaisesRegex(RuntimeError, "S3_BUCKET_NAME"):
            StorageService().save_pdf("plan.pdf", "application/pdf", b"pdf")

    @patch("app.api.sharefile.get_settings")
    def test_production_webhook_requires_matching_token(self, get_settings):
        get_settings.return_value = Settings(
            app_environment="production",
            sharefile_webhook_token="expected-token",
            _env_file=None,
        )

        missing = Request({"type": "http", "method": "POST", "path": "/api/sharefile/webhook", "query_string": b"", "headers": []})
        matching = Request({"type": "http", "method": "POST", "path": "/api/sharefile/webhook", "query_string": b"token=expected-token", "headers": []})

        self.assertFalse(valid_webhook_token(missing))
        self.assertTrue(valid_webhook_token(matching))


if __name__ == "__main__":
    unittest.main()
