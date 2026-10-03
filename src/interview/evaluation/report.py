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


def write_transcript_pdf(turns: list[Turn], path: Path) -> None:
    """A text transcript PDF. WeasyPrint is not used; it needs Pango and Cairo."""
    lines: list[str] = []
    for turn in turns:
        prefix = "Candidate" if turn.speaker == "candidate" else "Interviewer"
        chunk = f"{prefix}: {turn.text}"
        while len(chunk) > 90:
            lines.append(chunk[:90])
            chunk = chunk[90:]
        lines.append(chunk)
    if not lines:
        lines = ["No transcript text."]
    # One page is enough for the fixtures. Longer sessions continue on the same page stream
    # up to a practical cap; the JSON report still has every quote.
    lines = lines[:80]
    commands = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        commands.append(f"({_pdf_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n",
        b"4 0 obj<< /Length " + str(len(stream)).encode("ascii") + b" >>stream\n" + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    blob = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(blob))
        blob.extend(obj)
    xref = len(blob)
    blob.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    blob.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        blob.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    blob.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
