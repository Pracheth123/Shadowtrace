"""
Uploaded document extraction — resume and work sample.

One place decides what an upload is, from its *content* as well as its name,
and one list of supported types is shared with the browser (the client mirrors
`SUPPORTED_UPLOAD_SUFFIXES` and `MAX_UPLOAD_BYTES`). A file is rejected with a
recovery message rather than accepted and silently read as empty.

DOCX is read with the standard library: a .docx is a zip whose
`word/document.xml` holds the paragraphs. That avoids a new dependency, and the
archive is size-checked before anything is inflated so a zip bomb cannot exhaust
memory. Legacy binary `.doc` is not supported and says so.

Extracted text is untrusted data. It is sanitised (instruction-like lines are
dropped, contract 7) before it is stored or turned into a claim.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from interview.intake.resume_parser import (
    RESUME_CHAR_LIMIT,
    ResumeExtractionError,
    collapse_ws,
    extract_pdf,
)
from interview.intake.sanitize import sanitize_text

SUPPORTED_UPLOAD_SUFFIXES = (".pdf", ".docx", ".txt", ".md")
SUPPORTED_UPLOAD_LABEL = "PDF, DOCX, plain text or Markdown"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Inflated size ceiling for the DOCX body. A real resume's document.xml is tens
# of kilobytes; this leaves two orders of magnitude of headroom.
_MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UploadRejected(ValueError):
    """An upload we will not read. `recovery` is written for the candidate."""

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.recovery = recovery


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    kind: str  # pdf | docx | txt | md
    text: str
    truncated: bool
    char_count: int


def _suffix(filename: str) -> str:
    lowered = (filename or "").strip().casefold()
    for suffix in SUPPORTED_UPLOAD_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    if lowered.endswith((".doc", ".pages", ".odt", ".rtf")):
        raise UploadRejected(
            f"{filename} is a word-processor format this build does not read.",
            recovery="Save it as DOCX or PDF, or paste your background as text.",
        )
    if lowered.endswith((".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp")):
        raise UploadRejected(
            f"{filename} is an image, and this build does not run OCR.",
            recovery="Upload a PDF exported from a word processor, a DOCX, or paste text.",
        )
    raise UploadRejected(
        f"{filename or 'That file'} is not a supported type.",
        recovery=f"Upload a {SUPPORTED_UPLOAD_LABEL} file, or paste text instead.",
    )


def _docx_text(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected(
            "This .docx file is not a valid Word document.",
            recovery="Re-save it from Word or Google Docs, or upload a PDF.",
        ) from exc
    with archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise UploadRejected(
                "This .docx file has no document body.",
                recovery="Re-save it from Word or Google Docs, or upload a PDF.",
            ) from exc
        if info.file_size > _MAX_DOCX_XML_BYTES:
            raise UploadRejected(
                "This .docx file expands to an implausibly large document.",
                recovery="Upload a PDF copy instead.",
            )
        raw = archive.read(info)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise UploadRejected(
            "This .docx file's body could not be read.",
            recovery="Re-save it from Word or Google Docs, or upload a PDF.",
        ) from exc
    paragraphs: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        pieces: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                pieces.append(" ")
            elif node.tag in (f"{_W_NS}br", f"{_W_NS}cr"):
                pieces.append("\n")
        paragraphs.append("".join(pieces))
    return "\n".join(paragraphs)


def extract_upload(filename: str, data: bytes) -> ExtractedDocument:
    """
    Validate and extract one uploaded document.

    Name, size and content must all agree: a `.pdf` that is not a PDF, a
    `.docx` that is not a zip, or a `.txt` with binary content is rejected.
    """
    suffix = _suffix(filename)
    if not data:
        raise UploadRejected(
            f"{filename} is empty.", recovery="Choose the file again and re-upload it."
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"{filename} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            recovery="Upload a smaller export, or paste the relevant text.",
        )

    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise UploadRejected(
                f"{filename} is named .pdf but is not a PDF.",
                recovery="Export a real PDF from your editor and upload that.",
            )
        try:
            extraction = extract_pdf(data)
        except ResumeExtractionError as exc:
            raise UploadRejected(str(exc), recovery=exc.recovery) from exc
        if extraction.image_only or extraction.is_empty:
            raise UploadRejected(
                f"No text could be read from {filename}; it looks like a scan.",
                recovery="Upload a PDF exported from a word processor, a DOCX, or paste text.",
            )
        text = extraction.text
    elif suffix == ".docx":
        if not data.startswith(b"PK"):
            raise UploadRejected(
                f"{filename} is named .docx but is not a Word document.",
                recovery="Re-save it as DOCX or PDF and upload again.",
            )
        text = collapse_ws(_docx_text(data))
    else:
        if b"\x00" in data[:4096]:
            raise UploadRejected(
                f"{filename} contains binary data, not text.",
                recovery="Upload a plain text, Markdown, PDF or DOCX file.",
            )
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            decoded = data.decode("latin-1")
        text = collapse_ws(decoded)

    text = sanitize_text(text)
    if len(re.sub(r"\s", "", text)) < 40:
        raise UploadRejected(
            f"{filename} contains almost no readable text.",
            recovery="Check it is the right file, or paste your background as text.",
        )
    truncated = len(text) > RESUME_CHAR_LIMIT
    if truncated:
        text = text[:RESUME_CHAR_LIMIT]
    return ExtractedDocument(
        filename=filename,
        kind=suffix.lstrip("."),
        text=text,
        truncated=truncated,
        char_count=len(text),
    )


__all__ = [
    "ExtractedDocument",
    "MAX_UPLOAD_BYTES",
    "SUPPORTED_UPLOAD_LABEL",
    "SUPPORTED_UPLOAD_SUFFIXES",
    "UploadRejected",
    "extract_upload",
]
