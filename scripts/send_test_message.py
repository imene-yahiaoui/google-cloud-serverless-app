
#!/usr/bin/env python3
"""
Manual test-message publisher for local development.

Two send modes:
  --mode http
      Send the Pub/Sub envelope directly to FastAPI.
      No emulator required.

  --mode pubsub
      Publish a message to the Pub/Sub emulator.

Examples:
  python scripts/send_test_message.py --mode http

  python scripts/send_test_message.py \
      --file invoices/q1_2024.pdf \
      --type application/pdf

  python scripts/send_test_message.py \
      --mode pubsub \
      --file scan.png \
      --type image/png
"""

import argparse
import base64
import json
import os
import sys

from datetime import datetime, timezone

import requests


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

PROJECT_ID = os.environ.get(
    "GCP_PROJECT_ID",
    "local-project",
)

PUBSUB_HOST = os.environ.get(
    "PUBSUB_EMULATOR_HOST",
    "localhost:8085",
)

# FastAPI runs locally on port 8000.
# Do not include /process here.
APP_URL = os.environ.get(
    "APP_URL",
    "http://127.0.0.1:8000",
).rstrip("/")


# ---------------------------------------------------------
# Build GCS notification
# ---------------------------------------------------------

def _build_notification(
    bucket: str,
    name: str,
    size: int,
    content_type: str,
) -> dict:
    return {
        "bucket": bucket,
        "name": name,
        "size": str(size),
        "contentType": content_type,
        "timeCreated": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ---------------------------------------------------------
# Send directly to FastAPI
# ---------------------------------------------------------

def send_via_http(
    bucket: str,
    name: str,
    size: int,
    content_type: str,
) -> None:

    notification = _build_notification(
        bucket,
        name,
        size,
        content_type,
    )

    encoded = base64.b64encode(
        json.dumps(notification).encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": {
            "data": encoded,
            "messageId": (
                f"local-{datetime.now().timestamp():.0f}"
            ),
            "publishTime": datetime.now(
                timezone.utc
            ).isoformat(),
            "attributes": {},
        },
        "subscription": (
            f"projects/{PROJECT_ID}/subscriptions/"
            "document-processing-sub"
        ),
    }

    url = f"{APP_URL}/process"

    print(f"-> POST {url}")

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=30,
        )

        print(f"<- HTTP {response.status_code}")

        if response.content:
            print(response.text)

        if response.status_code == 204:
            print("SUCCESS: Message acknowledged.")

        elif response.status_code >= 400:
            print(
                "ERROR: Request failed. "
                "Check the FastAPI terminal logs."
            )
            sys.exit(1)

    except requests.exceptions.RequestException as exc:
        print(
            f"ERROR: Could not send request to {url}",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------
# Send via Pub/Sub emulator
# ---------------------------------------------------------

def send_via_pubsub(
    bucket: str,
    name: str,
    size: int,
    content_type: str,
) -> None:

    topic_path = (
        f"projects/{PROJECT_ID}/topics/"
        "document-processing"
    )

    url = (
        f"http://{PUBSUB_HOST}/v1/"
        f"{topic_path}:publish"
    )

    notification = _build_notification(
        bucket,
        name,
        size,
        content_type,
    )

    encoded = base64.b64encode(
        json.dumps(notification).encode("utf-8")
    ).decode("utf-8")

    body = {
        "messages": [
            {
                "data": encoded,
            }
        ]
    }

    print(f"-> Publish to {topic_path}")

    try:
        response = requests.post(
            url,
            json=body,
            timeout=10,
        )

        if response.ok:
            message_ids = response.json().get(
                "messageIds",
                [],
            )

            print(
                f"SUCCESS: Published message IDs: "
                f"{message_ids}"
            )

        else:
            print(
                f"ERROR: HTTP {response.status_code}",
                file=sys.stderr,
            )
            print(
                response.text,
                file=sys.stderr,
            )
            sys.exit(1)

    except requests.exceptions.RequestException as exc:
        print(
            "ERROR: Could not connect to "
            f"Pub/Sub emulator at {PUBSUB_HOST}",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Send a test GCS event to the "
            "document processing pipeline"
        ),
        formatter_class=(
            argparse.ArgumentDefaultsHelpFormatter
        ),
    )

    parser.add_argument(
        "--bucket",
        default="local-test-bucket",
        help="GCS bucket name",
    )

    parser.add_argument(
        "--file",
        default="documents/test_invoice.pdf",
        help="Object path in bucket",
    )

    parser.add_argument(
        "--size",
        type=int,
        default=102_400,
        help="File size in bytes",
    )

    parser.add_argument(
        "--type",
        dest="content_type",
        default="application/pdf",
        help="MIME type",
    )

    parser.add_argument(
        "--mode",
        choices=["http", "pubsub"],
        default="http",
        help="Send mode",
    )

    args = parser.parse_args()

    print(
        f"\nSending test event for: "
        f"gs://{args.bucket}/{args.file}"
    )

    print(
        f"MIME: {args.content_type} "
        f"| Size: {args.size:,} bytes "
        f"| Mode: {args.mode}\n"
    )

    if args.mode == "http":
        send_via_http(
            args.bucket,
            args.file,
            args.size,
            args.content_type,
        )

    else:
        send_via_pubsub(
            args.bucket,
            args.file,
            args.size,
            args.content_type,
        )


if __name__ == "__main__":
    main()
