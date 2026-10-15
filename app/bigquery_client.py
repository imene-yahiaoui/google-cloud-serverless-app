"""
BigQuery streaming-insert client.

Design decisions
────────────────
1. **Emulator detection** – When ``BIGQUERY_EMULATOR_HOST`` is set the client
   uses ``AnonymousCredentials`` and redirects the API endpoint to the local
   goccy emulator, so no real GCP credentials are required for local dev/tests.

2. **Fresh client per call** – Each streaming insert creates a new BQ client.
   This keeps the module stateless and avoids stale connections inside
   long-running Cloud Run instances.  The overhead is negligible compared to
   the network round-trip.

3. **Error escalation** – ``stream_metadata`` raises on BQ insert errors so the
   caller (the FastAPI endpoint) can return HTTP 500, which causes Pub/Sub to
   redeliver the message (and eventually dead-letter it after max_attempts).
   ``stream_error`` swallows its own BQ errors to avoid an infinite error loop.
"""
import os
from typing import Any

from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

from app.config import settings
from app.logging_config import get_logger
from app.models import DocumentMetadata, ProcessingError

logger = get_logger(__name__)


# ── Client factory ─────────────────────────────────────────────────────────────


def _get_bq_client() -> bigquery.Client:
    """
    Build a BigQuery client.

    Uses anonymous credentials + local endpoint when ``BIGQUERY_EMULATOR_HOST``
    is present; otherwise falls back to ADC (Application Default Credentials).
    """
    emulator_host = os.environ.get("BIGQUERY_EMULATOR_HOST")
    if emulator_host:
        logger.debug("Connecting to BigQuery emulator", extra={"host": emulator_host})
        return bigquery.Client(
            project=settings.gcp_project_id,
            credentials=AnonymousCredentials(),
            client_options={"api_endpoint": f"http://{emulator_host}"},
        )
    return bigquery.Client(project=settings.gcp_project_id)


# ── Row serialisers ────────────────────────────────────────────────────────────


def _metadata_to_row(metadata: DocumentMetadata) -> dict[str, Any]:
    return {
        "filename": metadata.filename,
        "file_size": metadata.file_size,
        "file_type": metadata.file_type,
        "gcs_uri": metadata.gcs_uri,
        "word_count": metadata.word_count,
        "simulated_tags": metadata.simulated_tags,
        "upload_timestamp": metadata.upload_timestamp.isoformat(),
        "processed_timestamp": metadata.processed_timestamp.isoformat(),
        "processing_status": metadata.processing_status,
    }


def _error_to_row(error: ProcessingError) -> dict[str, Any]:
    return {
        "event_id": error.event_id,
        "gcs_uri": error.gcs_uri,
        "error_message": error.error_message,
        "raw_message": error.raw_message,
        "failed_at": error.failed_at.isoformat(),
    }


# ── Public helpers ─────────────────────────────────────────────────────────────


def stream_metadata(metadata: DocumentMetadata) -> None:
    """
    Stream one ``DocumentMetadata`` row to the ``document_metadata`` BQ table.

    Raises ``RuntimeError`` if BigQuery reports insertion errors, which causes
    the FastAPI endpoint to return HTTP 500 and triggers Pub/Sub redelivery.
    """
    client = _get_bq_client()
    table_id = (
        f"{settings.gcp_project_id}"
        f".{settings.bigquery_dataset}"
        f".{settings.bigquery_metadata_table}"
    )
    row = _metadata_to_row(metadata)
    errors = client.insert_rows_json(table_id, [row])

    if errors:
        logger.error(
            "BigQuery insert failed for document metadata",
            extra={"table": table_id, "bq_errors": errors, "gcs_uri": metadata.gcs_uri},
        )
        raise RuntimeError(f"BigQuery streaming insert errors: {errors}")

    logger.info(
        "Metadata streamed to BigQuery",
        extra={"table": table_id, "gcs_uri": metadata.gcs_uri},
    )


def stream_error(error: ProcessingError) -> None:
    """
    Stream one ``ProcessingError`` row to the ``processing_errors`` BQ table.

    Errors are swallowed (logged only) to prevent an infinite error cascade.
    """
    client = _get_bq_client()
    table_id = (
        f"{settings.gcp_project_id}"
        f".{settings.bigquery_dataset}"
        f".{settings.bigquery_errors_table}"
    )
    row = _error_to_row(error)
    bq_errors = client.insert_rows_json(table_id, [row])

    if bq_errors:
        logger.error(
            "BigQuery insert failed for error record — losing error event",
            extra={"table": table_id, "bq_errors": bq_errors, "event_id": error.event_id},
        )
    else:
        logger.info(
            "Error record streamed to BigQuery",
            extra={"table": table_id, "event_id": error.event_id},
        )
