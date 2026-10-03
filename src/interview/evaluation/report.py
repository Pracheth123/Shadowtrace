"""JSON report, HTML report, and a transcript PDF written without a new library."""

from __future__ import annotations

import html
from pathlib import Path

from interview.evaluation.schema import Report, Turn

def write_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def render_html(report: Report) -> str:
    findings = []
    for finding in report.findings:
        findings.append(
            "<li><strong>"
            + html.escape(finding.dimension)
            + "</strong> ("
            + html.escape(finding.polarity)
            + ") "
            + html.escape(finding.summary)
            + " <q>"
            + html.escape(finding.quote)
            + "</q></li>"
        )
    claims = []
    for claim in report.claims:
        claims.append(
            "<li><code>"
            + html.escape(claim.id)
            + "</code> "
            + html.escape(claim.status)
            + (
                " — <q>" + html.escape(claim.quote) + "</q>"
                if claim.quote
                else ""
            )
            + "</li>"
        )
    dimensions = []
    for item in report.dimensions:
        dimensions.append(
            "<li>"
            + html.escape(item.dimension)
            + ": "
            + html.escape(f"{item.score:.2f}")
            + f" ({item.finding_count} findings)</li>"
        )
    body = "\n".join(
        [
            "<!DOCTYPE html>",
            "<html lang=\"en\"><head><meta charset=\"utf-8\">",
            f"<title>Feedback {html.escape(report.session_id)}</title>",
            "<style>body{font-family:Georgia,serif;max-width:42rem;margin:2rem auto;line-height:1.45}",
            "q{display:block;margin:.4rem 0 1rem;color:#333}</style></head><body>",
            f"<h1>Feedback</h1><p>Session {html.escape(report.session_id)}.</p>",
            f"<p>{html.escape(report.intensity_note)}</p>" if report.intensity_note else "",
            f"<p><a href=\"{html.escape(report.transcript_pdf)}\" download>Download transcript as PDF</a></p>",
            "<h2>Dimensions</h2><ul>",
            *dimensions,
            "</ul><h2>Claims</h2><ul>",
            *claims,
            "</ul><h2>Findings</h2><ul>",
            *findings,
            "</ul></body></html>",
        ]
    )
    return body


def write_html(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")


def _pdf_escape(text: str) -> str:
    safe = text.encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


_LINES_PER_PAGE = 45


def _wrap_turns(turns: list[Turn]) -> list[str]:
    lines: list[str] = []
    for turn in turns:
        prefix = "Candidate" if turn.speaker == "candidate" else "Interviewer"
        chunk = f"{prefix}: {turn.text}"
        while len(chunk) > 90:
            lines.append(chunk[:90])
            chunk = chunk[90:]
        lines.append(chunk)
    return lines or ["No transcript text."]


def _page_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "50 760 Td", "14 TL"]
    for line in lines:
        commands.append(f"({_pdf_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def write_transcript_pdf(turns: list[Turn], path: Path) -> None:
    """A text transcript PDF. WeasyPrint is not used; it needs Pango and Cairo."""
    lines = _wrap_turns(turns)
    chunks = [lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)]
    font_id = 3 + 2 * len(chunks)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids: list[str] = []
    next_id = 3
    for chunk in chunks:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        kids.append(f"{page_id} 0 R")
        stream = _page_stream(chunk)
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources<< /Font<< /F1 {font_id} 0 R >> >> >>"
        ).encode("ascii")
    objects[2] = (
        f"<< /Type /Pages /Count {len(chunks)} /Kids [{' '.join(kids)}] >>"
    ).encode("ascii")

    blob = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number in range(1, font_id + 1):
        offsets.append(len(blob))
        blob.extend(f"{number} 0 obj".encode("ascii"))
        blob.extend(objects[number])
        blob.extend(b"endobj\n")
    xref = len(blob)
    blob.extend(f"xref\n0 {font_id + 1}\n".encode("ascii"))
    blob.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        blob.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    blob.extend(
        f"trailer<< /Size {font_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
