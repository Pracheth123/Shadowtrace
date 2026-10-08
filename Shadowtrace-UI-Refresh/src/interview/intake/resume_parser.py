"""
ATS resume parser.

Adapted from skill-sync's upload flow, not copied from it:
- collapse whitespace the way `extractPdfText` joins page items
- keep the same profile: skills, projects, certifications, courses
- cap the text (skill-sync uses 12_000 characters)
- leave a field empty when the resume does not contain it

The hiring-model prompt and the 1–100 skill rubric are not used. Sections
are read with rules so a resume cannot instruct the parser.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from interview.intake.sanitize import sanitize_text
from interview.intake.schema import (
    ResumeCertification,
    ResumeCourse,
    ResumeProfile,
    ResumeProject,
)

# skill-sync ResumeUploadPage.jsx — free-tier input cap.
RESUME_CHAR_LIMIT = 12_000

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_SECTION = re.compile(
    r"^(skills|projects|experience|certifications|courses|education)\s*$",
    re.I,
)
_NAME = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}$")
_TECH_LINE = re.compile(r"^(tech(?:\s*stack)?|stack|tools)\s*:\s*(.+)$", re.I)
_DURATION = re.compile(r"^(duration|dates?)\s*:\s*(.+)$", re.I)
_LINK = re.compile(r"^(link|url)\s*:\s*(\S+)$", re.I)


def collapse_ws(text: str) -> str:
    """Join broken PDF lines the way skill-sync joins `item.str` with spaces."""
    lines = []
    for line in text.splitlines():
        flat = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(flat)
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class PdfExtraction:
    """What extraction actually achieved, so callers need not guess."""

    text: str
    pages: int
    pages_with_text: int
    # True when the file parses as a PDF but carries essentially no text layer:
    # a scan or an exported image. The recovery path differs completely from a
    # corrupt file, so the two are not collapsed into one error.
    image_only: bool

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class ResumeExtractionError(ValueError):
    """
    Extraction produced nothing usable.

    Raised rather than returning "" because an empty profile silently becomes an
    interview with no claims to interrogate — which looks like a working session
    and is the failure most likely to go unnoticed. `recovery` is written for
    the candidate, not the log.
    """

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.recovery = recovery


# A text-bearing page yields far more than this. Below it, across every page,
# the file is a scan rather than a document with a thin text layer.
_MIN_CHARS_PER_PAGE = 24


def extract_pdf(data: bytes) -> PdfExtraction:
    """
    Extract text with pypdf.

    Replaces a hand-rolled regex over `(...) Tj` operators. That only ever
    matched *uncompressed* content streams, so an ordinary FlateDecode PDF — in
    practice almost every real resume — extracted to the empty string and was
    accepted as a valid, blank profile.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, OSError, ValueError) as exc:
        raise ResumeExtractionError(
            f"This PDF could not be opened: {exc}",
            recovery="Re-export the file from your editor, or upload a .txt copy.",
        ) from exc

    # An encrypted file is a distinct, fixable situation. Empty-password
    # decryption covers the common "protected but not really" export.
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ResumeExtractionError(
                "This PDF is password protected.",
                recovery="Upload an unprotected copy, or paste the text instead.",
            ) from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — one bad page must not lose the rest
            pages.append("")

    joined = collapse_ws("\n".join(pages))
    with_text = sum(1 for page in pages if len(page.strip()) >= _MIN_CHARS_PER_PAGE)
    page_count = len(pages) or 1
    image_only = len(joined.strip()) < _MIN_CHARS_PER_PAGE * page_count

    return PdfExtraction(
        text=joined,
        pages=len(pages),
        pages_with_text=with_text,
        image_only=image_only,
    )


def extract_pdf_text(data: bytes) -> str:
    """Text only. Kept for callers that do not need the diagnostics."""
    return extract_pdf(data).text


def load_resume_text(path: Path) -> tuple[str, bool]:
    """
    Read a resume as text, refusing to return an empty profile quietly.

    PDFs go through pypdf; anything else is decoded as UTF-8. A scanned PDF and
    a corrupt PDF raise `ResumeExtractionError` with different recovery advice,
    because "re-export it" and "this needs OCR" are not the same instruction.
    """
    raw = path.read_bytes()
    if raw[:5] == b"%PDF-" or raw[:4] == b"%PDF":
        extraction = extract_pdf(raw)
        if extraction.image_only or extraction.is_empty:
            raise ResumeExtractionError(
                f"No text could be read from this PDF "
                f"({extraction.pages_with_text} of {extraction.pages} pages had text). "
                "It looks like a scan or an image export.",
                recovery=(
                    "Upload a PDF exported from a word processor rather than a "
                    "scan, or paste your resume as plain text. This build does "
                    "not run OCR."
                ),
            )
        text = extraction.text
    else:
        decoded = raw.decode("utf-8", errors="replace")
        text = collapse_ws(decoded)
        if not text.strip():
            raise ResumeExtractionError(
                "This file contained no readable text.",
                recovery="Check the file is not empty, then upload it again.",
            )

    truncated = len(text) > RESUME_CHAR_LIMIT
    if truncated:
        text = text[:RESUME_CHAR_LIMIT]
    return sanitize_text(text), truncated


def _sections(text: str) -> dict[str, str]:
    current = "header"
    buckets: dict[str, list[str]] = {"header": []}
    for line in text.splitlines():
        match = _SECTION.match(line.strip())
        if match:
            current = match.group(1).lower()
            buckets.setdefault(current, [])
            continue
        buckets.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in buckets.items()}


def _split_skills(block: str) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n,;•·|]", block):
        skill = chunk.strip(" -\t")
        if not skill or len(skill) > 40:
            continue
        key = skill.casefold()
        if key in seen:
            continue
        seen.add(key)
        skills.append(skill)
    return skills


def _projects(block: str) -> list[ResumeProject]:
    if not block.strip():
        return []
    chunks = re.split(r"\n\s*\n", block.strip())
    projects: list[ResumeProject] = []
    for chunk in chunks:
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0]
        description: list[str] = []
        tech: list[str] = []
        link = ""
        duration = ""
        for line in lines[1:]:
            tech_m = _TECH_LINE.match(line)
            dur_m = _DURATION.match(line)
            link_m = _LINK.match(line)
            if tech_m:
                tech = _split_skills(tech_m.group(2))
            elif dur_m:
                duration = dur_m.group(2).strip()
            elif link_m:
                link = link_m.group(2).strip()
            else:
                description.append(line)
        projects.append(
            ResumeProject(
                title=title,
                description=" ".join(description).strip(),
                tech_stack=tech,
                link=link,
                duration=duration,
            )
        )
    return projects


def _certs(block: str) -> list[ResumeCertification]:
    certs: list[ResumeCertification] = []
    for line in block.splitlines():
        line = line.strip(" -•\t")
        if not line:
            continue
        parts = [p.strip() for p in line.split("—")]
        if len(parts) == 1:
            parts = [p.strip() for p in line.split(" - ")]
        name = parts[0] if parts else line
        issuer = parts[1] if len(parts) > 1 else ""
        year = parts[2] if len(parts) > 2 else ""
        covered = _split_skills(parts[3]) if len(parts) > 3 else []
        certs.append(
            ResumeCertification(name=name, issuer=issuer, year=year, skills_covered=covered)
        )
    return certs


def _courses(block: str) -> list[ResumeCourse]:
    courses: list[ResumeCourse] = []
    for line in block.splitlines():
        line = line.strip(" -•\t")
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"\s—\s|\s-\s", line)]
        name = parts[0] if parts else line
        institution = parts[1] if len(parts) > 1 else ""
        grade = parts[2] if len(parts) > 2 else ""
        covered = _split_skills(parts[3]) if len(parts) > 3 else []
        courses.append(
            ResumeCourse(
                name=name,
                institution=institution,
                grade=grade,
                skills_covered=covered,
            )
        )
    return courses


def _name(header: str) -> str:
    for line in header.splitlines():
        line = line.strip()
        if _NAME.match(line):
            return line
    return ""


def parse_resume(text: str, *, truncated: bool = False) -> ResumeProfile:
    body = sanitize_text(collapse_ws(text))
    if len(body) > RESUME_CHAR_LIMIT:
        body = body[:RESUME_CHAR_LIMIT]
        truncated = True
    sections = _sections(body)
    header = sections.get("header", "")
    email_m = _EMAIL.search(body)
    skills = _split_skills(sections.get("skills", ""))
    # skill-sync also counts skills named on projects, certs, and courses.
    projects = _projects(sections.get("projects", "") or sections.get("experience", ""))
    certs = _certs(sections.get("certifications", ""))
    courses = _courses(sections.get("courses", "") or sections.get("education", ""))
    extra: list[str] = []
    for project in projects:
        extra.extend(project.tech_stack)
    for cert in certs:
        extra.extend(cert.skills_covered)
    for course in courses:
        extra.extend(course.skills_covered)
    seen = {s.casefold() for s in skills}
    for skill in extra:
        if skill.casefold() not in seen:
            seen.add(skill.casefold())
            skills.append(skill)
    return ResumeProfile(
        name=_name(header),
        email=email_m.group(0) if email_m else "",
        skills=skills,
        projects=projects,
        certifications=certs,
        courses=courses,
        truncated=truncated,
    )


def parse_resume_file(path: Path) -> ResumeProfile:
    text, truncated = load_resume_text(path)
    return parse_resume(text, truncated=truncated)
