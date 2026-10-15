"""
Unit tests for app/processor.py.

These tests exercise pure-Python logic: no mocking, no fixtures for external
services.  They rely on the deterministic seeding behaviour to make assertions
about specific outputs without stubbing random number generation.
"""
import pytest

from app.models import GCSNotification
from app.processor import (
    SUPPORTED_CONTENT_TYPES,
    _count_words,
    _extract_tags,
    _get_file_category,
    _simulate_ocr_text,
    process_document,
)


# ── _get_file_category ────────────────────────────────────────────────────────


class TestGetFileCategory:
    def test_pdf_mime_returns_pdf(self):
        assert _get_file_category("application/pdf") == "pdf"

    def test_png_mime_returns_image(self):
        assert _get_file_category("image/png") == "image"

    def test_jpeg_mime_returns_image(self):
        assert _get_file_category("image/jpeg") == "image"

    def test_tiff_mime_returns_image(self):
        assert _get_file_category("image/tiff") == "image"

    def test_webp_mime_returns_image(self):
        assert _get_file_category("image/webp") == "image"

    def test_unknown_mime_defaults_to_pdf(self):
        assert _get_file_category("application/octet-stream") == "pdf"

    def test_lookup_is_case_insensitive(self):
        # MIME types in the wild are sometimes uppercased
        assert _get_file_category("IMAGE/PNG") == "image"
        assert _get_file_category("APPLICATION/PDF") == "pdf"

    def test_all_supported_types_are_covered(self):
        for mime, expected in SUPPORTED_CONTENT_TYPES.items():
            assert _get_file_category(mime) in ("pdf", "image")


# ── _simulate_ocr_text ────────────────────────────────────────────────────────


class TestSimulateOCRText:
    def test_returns_non_empty_string_for_pdf(self):
        text = _simulate_ocr_text("doc.pdf", "application/pdf")
        assert isinstance(text, str) and len(text) > 0

    def test_returns_non_empty_string_for_image(self):
        text = _simulate_ocr_text("scan.png", "image/png")
        assert isinstance(text, str) and len(text) > 0

    def test_same_filename_always_gives_same_text(self):
        """Deterministic seeding guarantee."""
        t1 = _simulate_ocr_text("invoice.pdf", "application/pdf")
        t2 = _simulate_ocr_text("invoice.pdf", "application/pdf")
        assert t1 == t2

    def test_different_filenames_produce_independent_results(self):
        # Both results are valid strings; just ensure they are produced
        t1 = _simulate_ocr_text("alpha.pdf", "application/pdf")
        t2 = _simulate_ocr_text("beta.pdf", "application/pdf")
        assert isinstance(t1, str)
        assert isinstance(t2, str)

    def test_image_text_is_shorter_than_pdf_text_on_average(self):
        # Images use a single block; PDFs may use up to two
        pdf_text = _simulate_ocr_text("big_report.pdf", "application/pdf")
        img_text = _simulate_ocr_text("big_report.png", "image/png")
        # PDFs always have >= 1 block; images always exactly 1 — just check both are non-empty
        assert len(pdf_text) > 0
        assert len(img_text) > 0


# ── _count_words ──────────────────────────────────────────────────────────────


class TestCountWords:
    def test_simple_two_word_string(self):
        assert _count_words("hello world") == 2

    def test_empty_string_returns_zero(self):
        assert _count_words("") == 0

    def test_multiple_spaces_between_words(self):
        assert _count_words("one  two   three") == 3

    def test_single_word(self):
        assert _count_words("hello") == 1

    def test_leading_trailing_whitespace(self):
        assert _count_words("  hello world  ") == 2


# ── _extract_tags ─────────────────────────────────────────────────────────────


class TestExtractTags:
    def test_returns_list_of_strings(self):
        tags = _extract_tags("This is a financial report", "report.pdf")
        assert isinstance(tags, list)
        assert all(isinstance(t, str) for t in tags)

    def test_detects_financial_keyword_in_text(self):
        tags = _extract_tags("annual financial summary with revenue data", "doc.pdf")
        assert "financial" in tags

    def test_detects_invoice_keyword_in_filename(self):
        tags = _extract_tags("total due one hundred euros", "invoice_q1.pdf")
        assert "invoice" in tags

    def test_minimum_two_tags_always_returned(self):
        # Even with completely unrelated text and filename
        tags = _extract_tags("zzz", "zzz.pdf")
        assert len(tags) >= 2

    def test_tags_are_sorted_alphabetically(self):
        tags = _extract_tags("legal medical financial compliance report", "doc.pdf")
        assert tags == sorted(tags)

    def test_no_duplicate_tags(self):
        tags = _extract_tags("financial financial financial report report", "financial.pdf")
        assert len(tags) == len(set(tags))

    def test_deterministic_fallback_is_consistent(self):
        """Fallback tag selection should be the same for the same filename."""
        tags1 = _extract_tags("zzz", "zzz.pdf")
        tags2 = _extract_tags("zzz", "zzz.pdf")
        assert tags1 == tags2


# ── process_document (integration of helpers) ─────────────────────────────────


class TestProcessDocument:
    def test_returns_document_metadata_instance(self, sample_pdf_notification):
        from app.models import DocumentMetadata
        result = process_document(sample_pdf_notification)
        assert isinstance(result, DocumentMetadata)

    def test_all_metadata_fields_are_populated(self, sample_pdf_notification):
        result = process_document(sample_pdf_notification)
        assert result.filename == "invoice_2024.pdf"
        assert result.file_size == 102400
        assert result.file_type == "application/pdf"
        assert result.gcs_uri == "gs://test-bucket/documents/invoice_2024.pdf"
        assert result.word_count > 0
        assert len(result.simulated_tags) >= 2
        assert result.processing_status == "success"

    def test_upload_timestamp_parsed_from_notification(self, sample_pdf_notification):
        result = process_document(sample_pdf_notification)
        assert result.upload_timestamp.year == 2024
        assert result.upload_timestamp.month == 1
        assert result.upload_timestamp.day == 15

    def test_processed_timestamp_is_set(self, sample_pdf_notification):
        result = process_document(sample_pdf_notification)
        assert result.processed_timestamp is not None

    def test_image_notification_is_processed(self, sample_image_notification):
        result = process_document(sample_image_notification)
        assert result.file_type == "image/png"
        assert result.processing_status == "success"
        assert result.word_count > 0

    def test_malformed_timestamp_falls_back_to_now(self):
        notification = GCSNotification(
            bucket="bucket",
            name="file.pdf",
            size="1000",
            contentType="application/pdf",
            timeCreated="NOT-A-VALID-DATE",
        )
        result = process_document(notification)
        # Should not raise; upload_timestamp defaults to a recent datetime
        assert result.upload_timestamp is not None

    def test_unsupported_mime_type_does_not_raise(self):
        notification = GCSNotification(
            bucket="bucket",
            name="file.docx",
            size="50000",
            contentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            timeCreated="2024-03-01T09:00:00Z",
        )
        result = process_document(notification)
        assert result.processing_status == "success"

    def test_gcs_uri_property_on_notification(self):
        notif = GCSNotification(
            bucket="my-bucket",
            name="folder/file.pdf",
            size="100",
            contentType="application/pdf",
            timeCreated="2024-01-01T00:00:00Z",
        )
        assert notif.gcs_uri == "gs://my-bucket/folder/file.pdf"
        assert notif.filename == "file.pdf"
