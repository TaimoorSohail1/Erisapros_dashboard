from functools import lru_cache
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_environment: str = "development"
    mongodb_uri: str | None = None

    auth_enabled: bool = False
    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None
    field_rules_admin_emails: str = "support@highlandtech.ai"

    @property
    def field_rules_admin_email_set(self) -> set[str]:
        return {item.strip().lower() for item in self.field_rules_admin_emails.split(",") if item.strip()}

    aws_region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    s3_bucket_name: str | None = None

    eyelevel_api_key: str | None = None
    eyelevel_extract_url: str | None = None

    groundx_api_key: str | None = None
    groundx_bucket_id: int | None = None
    groundx_api_base_url: str = "https://api.groundx.ai/api/v1"
    groundx_poll_seconds: float = 3
    groundx_max_wait_seconds: int = 90
    # Browser requests pass through CloudFront, whose origin response timeout is
    # 60 seconds. Keep interactive field-rule QA below that boundary and fall
    # back to the deterministic document parser when GroundX is still working.
    field_rule_qa_timeout_seconds: float = 45
    allow_pdf_text_fallback: bool = False
    low_confidence_threshold: float = 0.8

    sharefile_subdomain: str | None = "erisapros"
    sharefile_client_id: str | None = None
    sharefile_client_secret: str | None = None
    sharefile_redirect_url: str | None = None
    sharefile_webhook_url: str | None = None
    sharefile_webhook_token: str | None = None
    sharefile_intake_folder_id: str | None = None
    sharefile_intake_folder_path: str | None = None
    sharefile_discover_shared_folders: bool = False
    sharefile_shared_root_folder_id: str | None = None
    sharefile_poll_enabled: bool = True
    sharefile_poll_interval_seconds: int = 1800
    sharefile_webhook_auto_register_enabled: bool = False
    sharefile_webhook_discovery_interval_seconds: int = 3600
    # Frequent polls run a quick top-level scan that only looks for folders
    # that appeared since the last sweep; the exhaustive deep sweep runs at
    # most this often. See ShareFileService.sync_changes.
    sharefile_deep_scan_interval_hours: int = 12
    # How many folder levels below a client the quick scan always lists
    # (minimum 1, so every client's own subfolders are always seen). Below
    # that it follows the filing structure only (5500 Filing > year folder >
    # Schedule A's). Raising this notices unusually named folders sooner at
    # a steep cost in listing calls per poll.
    sharefile_quick_scan_depth: int = 1
    # Production sends all ShareFile scans, webhooks, and extraction work to
    # a dedicated ECS worker through SQS. The API never executes that work.
    sharefile_work_queue_url: str | None = None

    ftwlink_key_id: str | None = None
    ftwlink_endpoint_url: str | None = None
    ftwlink_sandbox_customer_id: str | None = None
    ftwlink_sandbox_plan_id: str | None = None
    ftwlink_sandbox_year: str | None = None
    ftwlink_sandbox_ftw_customer_id: str | None = None
    ftwlink_sandbox_ftw_plan_id: str | None = None
    ftwlink_sandbox_year_end: str | None = None
    # FT Williams Schedule A slots are independent. Query a small batch in
    # parallel to reduce latency without flooding the upstream service.
    ftw_slot_query_concurrency: int = 5
    # Current-data snapshots are identical for every filing that belongs to
    # the same FT Williams plan and year.
    ftw_snapshot_ttl_seconds: int = 300
    # Keep extraction bounded so bulk ShareFile uploads remain responsive.
    filing_extraction_concurrency: int = 4
    ftw_plan_page_url_template: str = (
        "https://ftwilliam.com/cgi-bin/index.cgi?"
        "#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&"
        "plan={ftw_customer_id},{ftw_plan_id}&Year={year}"
    )

    @property
    def is_production(self) -> bool:
        return self.app_environment.strip().lower() == "production"

    def validate_runtime(self) -> None:
        if not self.is_production:
            return

        missing = []
        if not self.mongodb_uri:
            missing.append("MONGODB_URI")
        elif (urlsplit(self.mongodb_uri).hostname or "").lower() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise RuntimeError(
                "Production requires a remote MongoDB connection; localhost is not persistent or reachable from ECS."
            )
        if not self.aws_region:
            missing.append("AWS_REGION")
        if not self.s3_bucket_name:
            missing.append("S3_BUCKET_NAME")
        if not self.auth_enabled:
            missing.append("AUTH_ENABLED=true")
        if not self.cognito_region:
            missing.append("COGNITO_REGION")
        if not self.cognito_user_pool_id:
            missing.append("COGNITO_USER_POOL_ID")
        if not self.cognito_app_client_id:
            missing.append("COGNITO_APP_CLIENT_ID")
        if not self.sharefile_webhook_token:
            missing.append("SHAREFILE_WEBHOOK_TOKEN")
        if not self.sharefile_work_queue_url:
            missing.append("SHAREFILE_WORK_QUEUE_URL")
        if missing:
            raise RuntimeError(
                "Production configuration is incomplete. Missing: " + ", ".join(missing)
            )

    model_config = SettingsConfigDict(
        env_file=(".env.local", "../.env.local", "backend/.env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
