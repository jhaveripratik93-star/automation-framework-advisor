"""Text extraction for uploaded reference/architecture/design documents
(Team Profiles — see src/team_profiles.py).

Supports the same plain text formats the Advisor's existing case-study
upload already handles (.txt, .md), plus .pdf via pypdf — architecture
and design docs are very commonly PDFs in real organisations, so txt/md
alone would leave out the most likely real-world input.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["SUPPORTED_EXTENSIONS", "extract_text"]

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")


def extract_text(filename: str, raw_bytes: bytes) -> str:
    """Extract plain text from an uploaded document's raw bytes.

    Returns an empty string (logging a warning, never raising) for an
    unsupported extension or a file that fails to parse — a bad upload
    should degrade gracefully, not crash the Settings page.
    """
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

    if ext in (".txt", ".md"):
        try:
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("doc_extraction: failed to decode %s: %s", filename, exc)
            return ""

    if ext == ".pdf":
        return _extract_pdf(filename, raw_bytes)

    logger.warning("doc_extraction: unsupported file type '%s' for %s", ext, filename)
    return ""


def _extract_pdf(filename: str, raw_bytes: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        logger.warning(
            "doc_extraction: pypdf not installed — cannot extract text from %s. "
            "Install it with `pip install pypdf` (see requirements.txt).",
            filename,
        )
        return ""

    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p.strip() for p in pages if p.strip())
    except Exception as exc:
        logger.warning("doc_extraction: failed to parse PDF %s: %s", filename, exc)
        return ""
