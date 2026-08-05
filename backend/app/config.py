from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_environment: str = "development"
    mongodb_uri: str | None = None

    auth_enabled: bool = False
    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None

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
    allow_pdf_text_fallback: bool = False
    low_confidence_threshold: float = 0.8

    sharefile_subdomain: str | None = "erisapros"
    sharefile_client_id: str | None = None
    sharefile_client_secret: str | None = None
    sharefile_redirect_url: str | None = None
    sharefile_webhook_url: str | None = None
    sharefile_intake_folder_id: str | None = None
    sharefile_intake_folder_path: str | None = None
    sharefile_discover_shared_folders: bool = False
    sharefile_shared_root_folder_id: str | None = None
    sharefile_poll_enabled: bool = True
    sharefile_poll_interval_seconds: int = 1800
    sharefile_webhook_auto_register_enabled: bool = False
    sharefile_webhook_discovery_interval_seconds: int = 3600

    ftwlink_key_id: str | None = None
    ftwlink_endpoint_url: str | None = None
    ftwlink_sandbox_customer_id: str | None = None
    ftwlink_sandbox_plan_id: str | None = None
    ftwlink_sandbox_year: str | None = None
    ftwlink_sandbox_ftw_customer_id: str | None = None
    ftwlink_sandbox_ftw_plan_id: str | None = None
    ftwlink_sandbox_year_end: str | None = None

    @property
    def is_production(self) -> bool:
        return self.app_environment.strip().lower() == "production"

    def validate_runtime(self) -> None:
        if not self.is_production:
            return

        missing = []
        if not self.mongodb_uri:
            missing.append("MONGODB_URI")
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
