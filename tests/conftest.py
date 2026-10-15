"""
Shared pytest fixtures.

The ``test_client`` fixture patches ``stream_metadata`` and ``stream_error``
at the ``app.main`` module level.  This means every API test runs without real
BigQuery credentials, while the processor logic runs unpatched (it is pure
Python with no I/O).

Fixtures that require the real processor behaviour (e.g. processor unit tests)
import the helpers directly and need no fixtures from here.
"""
import base64
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models import DocumentMetadata, GCSNotification, ProcessingError


# ── Domain fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def sample_pdf_notification() -> GCSNotification:
    return GCSNotification(
        bucket="test-bucket",
        name="documents/invoice_2024.pdf",
        size="102400",
        contentType="application/pdf",
        timeCreated="2024-01-15T10:30:00Z",
    )


@pytest.fixture
def sample_image_notification() -> GCSNotification:
    return GCSNotification(
        bucket="test-bucket",
        name="images/scan_001.png",
        size="2048000",
        contentType="image/png",
        timeCreated="2024-01-15T11:00:00Z",
    )


@pytest.fixture
def sample_metadata() -> DocumentMetadata:
    return DocumentMetadata(
        filename="invoice_2024.pdf",
        file_size=102400,
        file_type="application/pdf",
        gcs_uri="gs://test-bucket/documents/invoice_2024.pdf",
        word_count=150,
        simulated_tags=["financial", "invoice"],
        upload_timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        processed_timestamp=datetime(2024, 1, 15, 10, 30, 5, tzinfo=timezone.utc),
        processing_status="success",
    )


@pytest.fixture
def sample_error() -> ProcessingError:
    return ProcessingError(
        event_id="msg-abc-123",
        gcs_uri="gs://test-bucket/documents/bad_file.pdf",
        error_message="Simulated failure",
        raw_message='{"bucket": "test-bucket", "name": "documents/bad_file.pdf"}',
        failed_at=datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
    )


# ── Pub/Sub payload factory ────────────────────────────────────────────────────


def _make_pubsub_payload(
    bucket: str = "test-bucket",
    name: str = "documents/report.pdf",
    size: str = "512000",
    content_type: str = "application/pdf",
    time_created: str = "2024-06-01T08:00:00Z",
    message_id: str = "test-message-001",
) -> dict:
    """Build a well-formed Pub/Sub push payload for a GCS notification."""
    notification = {
        "bucket": bucket,
        "name": name,
        "size": size,
        "contentType": content_type,
        "timeCreated": time_created,
    }
    encoded = base64.b64encode(json.dumps(notification).encode()).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": message_id,
            "publishTime": "2024-06-01T08:00:01Z",
            "attributes": {},
        },
        "subscription": "projects/test-project/subscriptions/document-processing-sub",
    }


@pytest.fixture
def pdf_pubsub_payload() -> dict:
    return _make_pubsub_payload()


@pytest.fixture
def image_pubsub_payload() -> dict:
    return _make_pubsub_payload(
        name="images/scan.png",
        content_type="image/png",
        message_id="test-message-img-001",
    )


# ── API test client ────────────────────────────────────────────────────────────


@pytest.fixture
def test_client():
    """
    FastAPI synchronous test client with BigQuery calls patched out.

    ``process_document`` runs for real — it is pure Python with no I/O, so it
    exercises the full OCR simulation path in API tests without any stubs.
    """
    with (
        patch("app.main.stream_metadata"),
        patch("app.main.stream_error"),
    ):
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
