"""
Centralised configuration loaded from environment variables.
Local overrides can be placed in a .env file (see .env.example).
"""
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google Cloud ─────────────────────────────────────────────────────────
    gcp_project_id: str = "local-project"
    gcp_region: str = "europe-west9"

    # ── BigQuery ─────────────────────────────────────────────────────────────
    bigquery_dataset: str = "document_pipeline"
    bigquery_metadata_table: str = "document_metadata"
    bigquery_errors_table: str = "processing_errors"

    # ── Pub/Sub ───────────────────────────────────────────────────────────────
    pubsub_topic: str = "document-processing"
    pubsub_dlq_topic: str = "document-processing-dlq"
    pubsub_subscription: str = "document-processing-sub"

    # ── Application ───────────────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"

    # ── Emulator endpoints (unset in production) ──────────────────────────────
    pubsub_emulator_host: Optional[str] = None
    bigquery_emulator_host: Optional[str] = None


settings = Settings()
