from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str | None = None

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

    model_config = SettingsConfigDict(
        env_file=(".env.local", "../.env.local", "backend/.env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
