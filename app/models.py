"""
Pydantic domain models shared across the application.

Three distinct layers are modelled:
- PubSubPushRequest / PubSubMessage  →  the HTTP envelope sent by Pub/Sub
- GCSNotification                    →  the decoded payload inside the message
- DocumentMetadata / ProcessingError →  records written to BigQuery
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Pub/Sub push envelope ─────────────────────────────────────────────────────


class PubSubMessage(BaseModel):
    """Single Pub/Sub message as delivered via a push subscription."""

    data: str  # base64-encoded JSON string
    messageId: str
    publishTime: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class PubSubPushRequest(BaseModel):
    """Outer envelope POSTed by Pub/Sub to the push endpoint."""

    message: PubSubMessage
    subscription: str


# ── GCS event payload ─────────────────────────────────────────────────────────


class GCSNotification(BaseModel):
    """
    Represents the GCS object-finalise notification published to Pub/Sub.

    Field names match the GCS Pub/Sub notification format
    (https://cloud.google.com/storage/docs/pubsub-notifications#payload).
    """

    bucket: str
    name: str        # object path within the bucket, e.g. "docs/invoice.pdf"
    size: str        # GCS sends size as a string
    contentType: str
    timeCreated: str  # RFC 3339 timestamp string

    @property
    def gcs_uri(self) -> str:
        """Full gs:// URI, e.g. gs://my-bucket/docs/invoice.pdf."""
        return f"gs://{self.bucket}/{self.name}"

    @property
    def filename(self) -> str:
        """Basename of the object, e.g. invoice.pdf."""
        return self.name.split("/")[-1]


# ── BigQuery record models ────────────────────────────────────────────────────


class DocumentMetadata(BaseModel):
    """Metadata record written to the ``document_metadata`` BigQuery table."""

    filename: str
    file_size: int
    file_type: str
    gcs_uri: str
    word_count: int
    simulated_tags: list[str]
    upload_timestamp: datetime
    processed_timestamp: datetime
    processing_status: str  # "success" | "error"


class ProcessingError(BaseModel):
    """Error record written to the ``processing_errors`` BigQuery table."""

    event_id: str
    gcs_uri: str
    error_message: str
    raw_message: str   # raw JSON string of the GCS notification
    failed_at: datetime
