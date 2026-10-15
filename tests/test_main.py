"""
Integration-style tests for the FastAPI application (app/main.py).

Strategy
────────
- ``test_client`` fixture patches only the two BigQuery functions at the
  ``app.main`` module level.  The processor runs for real so these tests
  exercise the full OCR path without any GCP credentials.
- Tests that need to control processor output patch ``app.main.process_document``
  directly (e.g., to inject a RuntimeError for the error-path tests).
- All assertions are on HTTP status codes and response body shape — not on
  internal implementation details.
"""
import base64
import json
from unittest.mock import MagicMock, patch

import pytest


class TestHealthEndpoint:
    def test_returns_200(self, test_client):
        response = test_client.get("/health")
        assert response.status_code == 200

    def test_body_contains_status_healthy(self, test_client):
        body = test_client.get("/health").json()
        assert body["status"] == "healthy"

    def test_body_contains_service_name(self, test_client):
        body = test_client.get("/health").json()
        assert body["service"] == "document-processor"

    def test_body_contains_version(self, test_client):
        body = test_client.get("/health").json()
        assert "version" in body

    def test_body_contains_environment(self, test_client):
        body = test_client.get("/health").json()
        assert "environment" in body


class TestProcessEndpoint:
    # ── Happy paths ────────────────────────────────────────────────────────────

    def test_valid_pdf_message_returns_204(self, test_client, pdf_pubsub_payload):
        response = test_client.post("/process", json=pdf_pubsub_payload)
        assert response.status_code == 204

    def test_valid_image_message_returns_204(self, test_client, image_pubsub_payload):
        response = test_client.post("/process", json=image_pubsub_payload)
        assert response.status_code == 204

    def test_stream_metadata_is_called_on_success(self, test_client, pdf_pubsub_payload):
        """Verify the BigQuery helper is invoked exactly once per message."""
        with patch("app.main.stream_metadata") as mock_stream:
            test_client.post("/process", json=pdf_pubsub_payload)
            mock_stream.assert_called_once()

    def test_stream_metadata_receives_correct_gcs_uri(self, test_client, pdf_pubsub_payload):
        with patch("app.main.stream_metadata") as mock_stream:
            test_client.post("/process", json=pdf_pubsub_payload)
            metadata = mock_stream.call_args[0][0]
            assert metadata.gcs_uri == "gs://test-bucket/documents/report.pdf"

    def test_no_response_body_on_success(self, test_client, pdf_pubsub_payload):
        response = test_client.post("/process", json=pdf_pubsub_payload)
        assert response.content == b""

    # ── Client-error paths (4xx) ───────────────────────────────────────────────

    def test_missing_message_field_returns_400(self, test_client):
        response = test_client.post("/process", json={"subscription": "sub"})
        assert response.status_code == 400

    def test_completely_empty_body_returns_400(self, test_client):
        response = test_client.post("/process", json={})
        assert response.status_code == 400

    def test_invalid_base64_data_returns_400(self, test_client):
        payload = {
            "message": {
                "data": "!!!NOT_VALID_BASE64!!!",
                "messageId": "msg-1",
                "publishTime": "2024-01-01T00:00:00Z",
            },
            "subscription": "projects/test/subscriptions/sub",
        }
        response = test_client.post("/process", json=payload)
        assert response.status_code == 400

    def test_valid_base64_but_invalid_gcs_schema_returns_422(self, test_client):
        """Correct Pub/Sub envelope but GCS notification is missing required fields."""
        bad_notification = {"not": "a valid gcs notification"}
        encoded = base64.b64encode(json.dumps(bad_notification).encode()).decode()
        payload = {
            "message": {
                "data": encoded,
                "messageId": "msg-2",
                "publishTime": "2024-01-01T00:00:00Z",
            },
            "subscription": "projects/test/subscriptions/sub",
        }
        response = test_client.post("/process", json=payload)
        assert response.status_code == 422

    # ── Server-error paths (5xx) ───────────────────────────────────────────────

    def test_processor_exception_returns_500(self, test_client, pdf_pubsub_payload):
        with patch("app.main.process_document") as mock_proc:
            mock_proc.side_effect = RuntimeError("Simulated OCR failure")
            response = test_client.post("/process", json=pdf_pubsub_payload)
        assert response.status_code == 500

    def test_bq_insert_failure_returns_500(self, test_client, pdf_pubsub_payload):
        """BigQuery streaming insert failure should also return 500."""
        with patch("app.main.stream_metadata") as mock_stream:
            mock_stream.side_effect = RuntimeError("BQ insert rejected")
            response = test_client.post("/process", json=pdf_pubsub_payload)
        assert response.status_code == 500

    def test_processing_failure_calls_stream_error(self, test_client, pdf_pubsub_payload):
        """On failure the error should be persisted to the DLQ table."""
        with (
            patch("app.main.process_document") as mock_proc,
            patch("app.main.stream_error") as mock_err,
        ):
            mock_proc.side_effect = RuntimeError("Processing crashed")
            test_client.post("/process", json=pdf_pubsub_payload)
        mock_err.assert_called_once()

    def test_stream_error_record_contains_event_id(self, test_client, pdf_pubsub_payload):
        with (
            patch("app.main.process_document") as mock_proc,
            patch("app.main.stream_error") as mock_err,
        ):
            mock_proc.side_effect = RuntimeError("crash")
            test_client.post("/process", json=pdf_pubsub_payload)

        error_record = mock_err.call_args[0][0]
        assert error_record.event_id == "test-message-001"

    def test_stream_error_failure_does_not_cascade(self, test_client, pdf_pubsub_payload):
        """Even if recording the error fails, we still return 500 (not an unhandled exception)."""
        with (
            patch("app.main.process_document") as mock_proc,
            patch("app.main.stream_error") as mock_err,
        ):
            mock_proc.side_effect = RuntimeError("processor crash")
            mock_err.side_effect = RuntimeError("BQ error recording also crashed")
            response = test_client.post("/process", json=pdf_pubsub_payload)
        # Should still return 500, not an unhandled 500 from the error handler
        assert response.status_code == 500
