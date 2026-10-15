"""
Simulated OCR document processor.

Design decisions
────────────────
1. **Deterministic seeding** – The random text/tag selection is seeded from the
   MD5 hash of the filename.  This means the same file always produces the same
   word count and tags, which makes unit-tests predictable without mocking.

2. **No real OCR calls** – Text is selected from a pre-written corpus split by
   file category (PDF vs. image).  In production you would replace
   ``_simulate_ocr_text`` with a call to Cloud Vision API or Document AI.

3. **Tag extraction order** – The function first performs a keyword scan of the
   mock text, then uses deterministic fallback sampling so that every document
   always has at least two tags.
"""
import hashlib
import random
from datetime import datetime, timezone

from app.logging_config import get_logger
from app.models import DocumentMetadata, GCSNotification

logger = get_logger(__name__)

# ── Corpus ────────────────────────────────────────────────────────────────────

_PDF_TEXTS = [
    (
        "This document contains important financial records for fiscal year 2024. "
        "Revenue increased by 15% compared to the previous year. "
        "Operating costs were reduced through automation and process optimisation. "
        "The board of directors approved the new strategic plan for 2025. "
        "Key performance indicators show positive trends across all business units. "
        "Employee satisfaction surveys indicate improved morale and engagement. "
        "Research and development investments totalled 8% of annual revenue. "
        "Customer acquisition costs decreased while lifetime value increased significantly."
    ),
    (
        "Patient medical records indicate normal vital signs across all measurements. "
        "Laboratory results show no abnormalities in blood work or urinalysis. "
        "Recommended treatment plan includes physical therapy twice per week. "
        "Follow-up appointment scheduled three months post-procedure. "
        "Medication dosage adjusted based on recent test results and patient feedback. "
        "Allergies noted: penicillin and sulfa drugs must be avoided at all times. "
        "Insurance pre-authorisation obtained for all planned procedures."
    ),
    (
        "Legal contract for software development services between the named parties. "
        "Term of agreement commences on the first day of the calendar month. "
        "Payment terms require net-thirty-day settlement upon invoice receipt. "
        "Intellectual property rights transfer upon full payment completion. "
        "Confidentiality provisions apply for five years post termination of contract. "
        "Dispute resolution through binding arbitration in the applicable jurisdiction. "
        "Governing law of the agreement is subject to local statutory requirements."
    ),
    (
        "Annual compliance report submitted to the regulatory authority as required. "
        "All internal audit findings have been remediated within the prescribed timelines. "
        "Risk register updated to reflect current threat landscape and control posture. "
        "Data protection officer reviewed personal data processing activities thoroughly. "
        "Third-party vendor assessments completed for all critical service providers. "
        "Business continuity plan tested successfully with a recovery time under two hours."
    ),
]

_IMAGE_TEXTS = [
    (
        "Invoice total amount due: one thousand two hundred fifty euros. "
        "Account reference ending in four digits. "
        "Payment due date end of current month. Tax included at standard rate."
    ),
    (
        "Certificate of completion awarded to the named individual. "
        "Course duration forty hours completed successfully with distinction. "
        "Issued by an accredited training institution on the above date."
    ),
    (
        "Shipping label: destination address parsed successfully. "
        "Package weight three point five kilograms. Fragile — handle with care. "
        "Tracking number generated for outbound shipment."
    ),
    (
        "Technical schematic showing component layout for circuit board revision three. "
        "Operating voltage range twelve to twenty-four volts DC. "
        "All tolerances within approved engineering specifications."
    ),
]

_AVAILABLE_TAGS = [
    "audit", "certificate", "compliance", "contract", "financial",
    "hr", "invoice", "legal", "marketing", "medical",
    "operations", "report", "research", "shipping", "technical",
]

# Maps MIME type → processing category
SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
    "image/tiff": "image",
    "image/webp": "image",
}

# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_file_category(content_type: str) -> str:
    """Return 'pdf' or 'image'.  Unknown types default to 'pdf'."""
    return SUPPORTED_CONTENT_TYPES.get(content_type.lower(), "pdf")


def _make_rng(filename: str) -> random.Random:
    """Return a seeded RNG so that the same filename always gives the same output."""
    seed = int(hashlib.md5(filename.encode()).hexdigest(), 16)
    return random.Random(seed)


def _simulate_ocr_text(filename: str, content_type: str) -> str:
    """
    Return mock OCR text for a document.

    PDFs get 1–2 text blocks concatenated; images get one shorter block.
    Selection is deterministic: same filename → same text.
    """
    category = _get_file_category(content_type)
    rng = _make_rng(filename)

    if category == "pdf":
        num_blocks = rng.randint(1, 2)
        blocks = rng.sample(_PDF_TEXTS, min(num_blocks, len(_PDF_TEXTS)))
        return " ".join(blocks)
    else:
        return rng.choice(_IMAGE_TEXTS)


def _count_words(text: str) -> int:
    """Return word count of a string."""
    return len(text.split()) if text else 0


def _extract_tags(text: str, filename: str) -> list[str]:
    """
    Return a sorted, deduplicated list of tags for a document.

    Strategy:
    1. Scan extracted text and filename for known keywords.
    2. If fewer than 2 tags were found, pad with deterministic fallback tags.
    """
    combined = f"{text} {filename}".lower()
    matched = [tag for tag in _AVAILABLE_TAGS if tag in combined]

    if len(matched) < 2:
        rng = _make_rng(filename)
        remaining = [t for t in _AVAILABLE_TAGS if t not in matched]
        extras = rng.sample(remaining, min(2 - len(matched), len(remaining)))
        matched.extend(extras)

    return sorted(set(matched))


# ── Public API ────────────────────────────────────────────────────────────────


def process_document(notification: GCSNotification) -> DocumentMetadata:
    """
    Core processing function.

    Orchestrates OCR simulation, metadata extraction, and model construction.
    Raises an exception on any unhandled error so the caller can route it to
    the dead-letter path.
    """
    logger.info(
        "Starting document processing",
        extra={
            "gcs_uri": notification.gcs_uri,
            "content_type": notification.contentType,
            "file_size": notification.size,
        },
    )

    if notification.contentType not in SUPPORTED_CONTENT_TYPES:
        logger.warning(
            "Unsupported content type — applying default PDF processing",
            extra={"content_type": notification.contentType, "gcs_uri": notification.gcs_uri},
        )

    extracted_text = _simulate_ocr_text(notification.filename, notification.contentType)
    word_count = _count_words(extracted_text)
    tags = _extract_tags(extracted_text, notification.filename)

    logger.debug(
        "OCR simulation complete",
        extra={"word_count": word_count, "tags": tags, "gcs_uri": notification.gcs_uri},
    )

    # Parse upload timestamp — fall back to now() if the string is malformed.
    try:
        upload_ts = datetime.fromisoformat(notification.timeCreated.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        logger.warning(
            "Could not parse timeCreated; using current UTC time",
            extra={"raw_time": notification.timeCreated},
        )
        upload_ts = datetime.now(timezone.utc)

    metadata = DocumentMetadata(
        filename=notification.filename,
        file_size=int(notification.size),
        file_type=notification.contentType,
        gcs_uri=notification.gcs_uri,
        word_count=word_count,
        simulated_tags=tags,
        upload_timestamp=upload_ts,
        processed_timestamp=datetime.now(timezone.utc),
        processing_status="success",
    )

    logger.info(
        "Document processing complete",
        extra={
            "gcs_uri": notification.gcs_uri,
            "word_count": word_count,
            "tags": tags,
            "processing_status": "success",
        },
    )

    return metadata
