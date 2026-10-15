
"""
FastAPI application — Document Processing Service.

HTTP contract:
GET  /health  -> 200
POST /process -> 204 on success
              -> 400 on malformed JSON or Pub/Sub envelope
              -> 422 on invalid GCS notification
              -> 500 on processing/storage failure
"""

import base64
import binascii
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import ValidationError

from app.bigquery_client import stream_error, stream_metadata
from app.config import settings
from app.logging_config import get_logger, setup_logging
from app.models import GCSNotification, ProcessingError, PubSubPushRequest
from app.processor import process_document


# Logging
_is_cloud_run = os.environ.get("K_SERVICE") is not None

setup_logging(
    log_level=settings.log_level,
    use_json=_is_cloud_run or settings.environment == "production",
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Document Processing Service starting",
        extra={
            "environment": settings.environment,
            "gcp_project": settings.gcp_project_id,
            "region": settings.gcp_region,
            "cloud_run": _is_cloud_run,
        },
    )

    yield

    logger.info("Document Processing Service shutting down")


app = FastAPI(
    title="Document Processing Service",
    description=(
        "Serverless document processing pipeline.\n\n"
        "Receives Pub/Sub push notifications triggered by GCS uploads, "
        "runs simulated OCR, and streams metadata to BigQuery."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get(
    "/health",
    tags=["Observability"],
    summary="Liveness / readiness probe",
)
async def health_check() -> dict:
    return {
        "status": "healthy",
        "service": "document-processor",
        "version": "1.0.0",
        "environment": settings.environment,
    }


@app.post(
    "/process",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Processing"],
    summary="Pub/Sub push endpoint",
    response_description="Empty body — message acknowledged",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "$ref": "#/components/schemas/PubSubPushRequest"
                    }
                }
            },
        }
    },
)
async def process_document_endpoint(request: Request) -> None:
    """
    Receive a Pub/Sub push delivery and process a GCS notification.
    """

    # Step 1 — Parse JSON safely
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid or empty JSON request body")

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain valid JSON",
        )

    # Step 2 — Validate Pub/Sub envelope
    try:
        push_request = PubSubPushRequest.model_validate(body)

    except ValidationError as exc:
        logger.error(
            "Invalid Pub/Sub envelope",
            extra={"error": str(exc)},
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Pub/Sub envelope: {exc}",
        )

    message = push_request.message
    event_id = message.messageId

    # Step 3 — Decode Base64 payload
    try:
        decoded = base64.b64decode(
            message.data,
            validate=True,
        ).decode("utf-8")

        notification_json = json.loads(decoded)

    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        logger.error(
            "Failed to decode Pub/Sub message",
            extra={
                "event_id": event_id,
                "error": str(exc),
            },
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not decode message data: {exc}",
        )

    # Step 4 — Validate GCS notification
    try:
        notification = GCSNotification.model_validate(
            notification_json
        )

    except ValidationError as exc:
        logger.error(
            "GCS notification validation failed",
            extra={
                "event_id": event_id,
                "error": str(exc),
            },
        )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GCS notification schema: {exc}",
        )

    logger.info(
        "Processing event received",
        extra={
            "event_id": event_id,
            "gcs_uri": notification.gcs_uri,
            "content_type": notification.contentType,
            "subscription": push_request.subscription,
        },
    )

    # Step 5 — Process document and store metadata
    try:
        metadata = process_document(notification)

        stream_metadata(metadata)

    except Exception as exc:
        logger.error(
            "Document processing or storage failed",
            extra={
                "event_id": event_id,
                "gcs_uri": notification.gcs_uri,
                "error": str(exc),
            },
            exc_info=True,
        )

        error_record = ProcessingError(
            event_id=event_id,
            gcs_uri=notification.gcs_uri,
            error_message=str(exc),
            raw_message=json.dumps(notification_json),
            failed_at=datetime.now(timezone.utc),
        )

        try:
            stream_error(error_record)

        except Exception as bq_exc:
            logger.error(
                "Could not record failure in BigQuery",
                extra={
                    "event_id": event_id,
                    "bq_error": str(bq_exc),
                },
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Document processing failed — "
                "message will be redelivered"
            ),
        )
