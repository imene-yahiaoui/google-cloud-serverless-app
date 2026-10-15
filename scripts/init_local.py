#!/usr/bin/env python3
"""
Local environment initialiser.

Creates Pub/Sub topics/subscriptions and BigQuery dataset/tables against
local emulators running in Docker Compose.  Designed to run once as an
``init`` service that exits 0 when everything is ready.

Run order (orchestrated by Docker Compose ``depends_on``):
  pubsub-emulator  ──┐
                     ├──> init (this script) ──> app
  bigquery-emulator ─┘
"""
import logging
import os
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "local-project")
PUBSUB_HOST = os.environ.get("PUBSUB_EMULATOR_HOST", "localhost:8085")
BQ_HOST = os.environ.get("BIGQUERY_EMULATOR_HOST", "localhost:9050")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "document_pipeline")
APP_URL = os.environ.get("APP_URL", "http://app:8080")

PUBSUB_BASE = f"http://{PUBSUB_HOST}"
BQ_BASE = f"http://{BQ_HOST}"


# ── Helpers ────────────────────────────────────────────────────────────────────


def wait_for(url: str, name: str, retries: int = 40, delay: float = 2.0) -> None:
    """Poll ``url`` until it returns a non-5xx status code or retries are exhausted."""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=3)
            if r.status_code < 500:
                log.info("✓ %s is ready (HTTP %s)", name, r.status_code)
                return
        except requests.exceptions.ConnectionError:
            pass
        log.info("  Waiting for %s … (%d/%d)", name, attempt, retries)
        time.sleep(delay)
    log.error("✗ %s did not become ready in time — aborting", name)
    sys.exit(1)


# ── Pub/Sub setup ──────────────────────────────────────────────────────────────


def create_topic(topic_id: str) -> None:
    path = f"{PUBSUB_BASE}/v1/projects/{PROJECT_ID}/topics/{topic_id}"
    r = requests.put(path, json={}, timeout=10)
    if r.status_code in (200, 409):  # 409 = already exists
        log.info("  Pub/Sub topic: %s", topic_id)
    else:
        log.error("Failed to create topic %s: %s %s", topic_id, r.status_code, r.text)
        sys.exit(1)


def create_push_subscription(sub_id: str, topic_id: str, push_endpoint: str) -> None:
    path = f"{PUBSUB_BASE}/v1/projects/{PROJECT_ID}/subscriptions/{sub_id}"
    body = {
        "topic": f"projects/{PROJECT_ID}/topics/{topic_id}",
        "pushConfig": {"pushEndpoint": push_endpoint},
        "ackDeadlineSeconds": 60,
        "deadLetterPolicy": {
            "deadLetterTopic": f"projects/{PROJECT_ID}/topics/document-processing-dlq",
            "maxDeliveryAttempts": 5,
        },
    }
    r = requests.put(path, json=body, timeout=10)
    if r.status_code in (200, 409):
        log.info("  Pub/Sub subscription: %s → %s", sub_id, push_endpoint)
    else:
        log.error("Failed to create subscription %s: %s %s", sub_id, r.status_code, r.text)
        sys.exit(1)


def setup_pubsub() -> None:
    log.info("── Setting up Pub/Sub ──")
    create_topic("document-processing")
    create_topic("document-processing-dlq")
    create_push_subscription(
        sub_id="document-processing-sub",
        topic_id="document-processing",
        push_endpoint=f"{APP_URL}/process",
    )


# ── BigQuery setup ─────────────────────────────────────────────────────────────


def bq_request(method: str, path: str, body: dict | None = None) -> requests.Response:
    url = f"{BQ_BASE}/bigquery/v2{path}"
    fn = getattr(requests, method)
    return fn(url, json=body, timeout=10)



def create_bq_dataset() -> None:
    # Check whether the dataset already exists.
    check = bq_request(
        "get",
        f"/projects/{PROJECT_ID}/datasets/{BQ_DATASET}",
    )

    if check.status_code == 200:
        log.info(
            "BigQuery dataset already exists: %s",
            BQ_DATASET,
        )
        return

    if check.status_code != 404:
        log.error(
            "Failed to check dataset: %s %s",
            check.status_code,
            check.text,
        )
        sys.exit(1)

    # Create the dataset only when it does not exist.
    response = bq_request(
        "post",
        f"/projects/{PROJECT_ID}/datasets",
        {
            "datasetReference": {
                "projectId": PROJECT_ID,
                "datasetId": BQ_DATASET,
            }
        },
    )

    if response.status_code in (200, 201, 409):
        log.info("BigQuery dataset: %s", BQ_DATASET)
    else:
        log.error(
            "Failed to create dataset: %s %s",
            response.status_code,
            response.text,
        )
        sys.exit(1)

def create_bq_table(table_id: str, schema_fields: list[dict]) -> None:
    body = {
        "tableReference": {
            "projectId": PROJECT_ID,
            "datasetId": BQ_DATASET,
            "tableId": table_id,
        },
        "schema": {"fields": schema_fields},
    }
    r = bq_request("post", f"/projects/{PROJECT_ID}/datasets/{BQ_DATASET}/tables", body)
    if r.status_code in (200, 409):
        log.info("  BigQuery table: %s.%s", BQ_DATASET, table_id)
    else:
        log.error("Failed to create table %s: %s %s", table_id, r.status_code, r.text)
        sys.exit(1)


def setup_bigquery() -> None:
    log.info("── Setting up BigQuery ──")
    create_bq_dataset()

    create_bq_table(
        "document_metadata",
        [
            {"name": "filename",            "type": "STRING",    "mode": "REQUIRED"},
            {"name": "file_size",           "type": "INTEGER",   "mode": "REQUIRED"},
            {"name": "file_type",           "type": "STRING",    "mode": "REQUIRED"},
            {"name": "gcs_uri",             "type": "STRING",    "mode": "REQUIRED"},
            {"name": "word_count",          "type": "INTEGER",   "mode": "REQUIRED"},
            {"name": "simulated_tags",      "type": "STRING",    "mode": "REPEATED"},
            {"name": "upload_timestamp",    "type": "TIMESTAMP", "mode": "REQUIRED"},
            {"name": "processed_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
            {"name": "processing_status",   "type": "STRING",    "mode": "REQUIRED"},
        ],
    )

    create_bq_table(
        "processing_errors",
        [
            {"name": "event_id",       "type": "STRING",    "mode": "REQUIRED"},
            {"name": "gcs_uri",        "type": "STRING",    "mode": "REQUIRED"},
            {"name": "error_message",  "type": "STRING",    "mode": "REQUIRED"},
            {"name": "raw_message",    "type": "STRING",    "mode": "REQUIRED"},
            {"name": "failed_at",      "type": "TIMESTAMP", "mode": "REQUIRED"},
        ],
    )


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    log.info("═══ Local environment initialiser ═══")
    log.info("Project : %s", PROJECT_ID)
    log.info("Pub/Sub : %s", PUBSUB_HOST)
    log.info("BigQuery: %s", BQ_HOST)

    wait_for(
        f"{PUBSUB_BASE}/v1/projects/{PROJECT_ID}/topics",
        "Pub/Sub emulator",
    )
    wait_for(
        f"{BQ_BASE}/bigquery/v2/projects",
        "BigQuery emulator",
    )

    setup_pubsub()
    setup_bigquery()

    log.info("═══ Local environment ready ═══")


if __name__ == "__main__":
    main()
