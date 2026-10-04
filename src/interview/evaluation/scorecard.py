"""
Downloadable scorecard — one self-contained HTML file for a stage-15 report.

Distinct from the transcript download: this is the evaluation (rubric, levels,
evidence, claims, limitations); the transcript is what was said. Everything is
escaped — transcript quotes are candidate data, never markup.
"""

from __future__ import annotations

from html import escape


def _pct(value) -> str:
    return "not scored" if value is None else f"{round(float(value) * 100)}/100"


def render_scorecard(report: dict) -> str:
    cfg = report.get("config") or {}
    parts: list[str] = []
    add = parts.append
    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    add(f"<title>Scorecard {escape(report.get('session_id', ''))}</title>")
    add(
        "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;"
        "padding:0 1rem;color:#1d1d1f}h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem}"
        "table{border-collapse:collapse;width:100%;margin:.5rem 0}td,th{border:1px solid #ddd;"
        "padding:.35rem .5rem;text-align:left;vertical-align:top}blockquote{margin:.3rem 0;"
        "padding-left:.75rem;border-left:3px solid #bbb;color:#444}.muted{color:#666}"
        ".warn{background:#fff4e5;padding:.6rem .8rem;border-radius:6px}</style></head><body>"
    )
    add("<h1>Practice interview scorecard</h1>")
    add(
        f"<p class='muted'>Session {escape(report.get('session_id', ''))} · "
        f"{escape(str(cfg.get('target_role') or ''))} · {escape(str(cfg.get('role_family') or ''))} · "
        f"round: {escape(str(cfg.get('round') or ''))} · lane: {escape(str(report.get('lane') or ''))} · "
        f"difficulty: {escape(str(cfg.get('intensity') or ''))} · {escape(report.get('created_at', ''))}</p>"
    )
    evaluator = report.get("evaluator") or {}
    if not evaluator.get("is_assessment", True):
        add("<p class='warn'><strong>Development evaluation.</strong> Produced without a "
            "model; not an assessment.</p>")
    overall = report.get("overall") or {}
    add(f"<p><strong>Overall indicator:</strong> {_pct(overall.get('score'))}. "
        f"{escape(overall.get('note', ''))}</p>")
    add(f"<p class='muted'>{escape(overall.get('disclaimer', ''))}</p>")
    add(f"<p class='muted'>{escape(report.get('coverage_note', ''))} "
        f"{escape(report.get('evidence_note', ''))}</p>")

    for result in report.get("rounds", []):
        add(f"<h2>{escape(result['label'])} — {escape(result['perspective_label'])} perspective</h2>")
        cov = result.get("coverage") or {}
        add(f"<p class='muted'>Rubric {escape(result['rubric_version'])} · status "
            f"{escape(result['status'])} · core questions {cov.get('spine_covered', 0)} of "
            f"{cov.get('spine_total', '?')} · round score {_pct(result['aggregate'].get('score'))}</p>")
        add("<table><tr><th>Dimension</th><th>Weight</th><th>Level</th><th>Evidence</th></tr>")
        for dim in result.get("dimensions", []):
            quotes = "".join(
                f"<blockquote>{escape(c['quote'])} <span class='muted'>({escape(c['turn_id'])})</span></blockquote>"
                for c in dim.get("citations", [])
            )
            level = dim["level"].replace("_", " ")
            add(f"<tr><td>{escape(dim['label'])}</td><td>{round(dim['weight'] * 100)}%</td>"
                f"<td>{escape(level)}</td><td>{escape(dim.get('rationale', ''))}{quotes}</td></tr>")
        add("</table>")
        add(f"<p class='muted'>{escape(result['aggregate'].get('note', ''))}</p>")
        for finding in result.get("findings", []):
            add(f"<p><strong>{'Strength' if finding['polarity'] == 'strength' else 'Gap'} — "
                f"{escape(finding['dimension_label'])}</strong> ({escape(finding['confidence'])} "
                f"confidence): {escape(finding['explanation'])}</p>"
                f"<blockquote>{escape(finding['quote'])} <span class='muted'>({escape(finding['turn_id'])})</span></blockquote>"
                f"<p class='muted'>Practice: {escape(finding.get('practice', ''))}</p>")

    add("<h2>Claim findings</h2>")
    add("<p class='muted'>How each claim from your background fared under questioning in "
        "this session. Not lie detection and not authorship verification.</p>")
    add("<table><tr><th>Claim</th><th>Status</th><th>Why</th></tr>")
    for claim in report.get("claims", []):
        quote = (
            f"<blockquote>{escape(claim['quote'])}</blockquote>" if claim.get("quote") else ""
        )
        add(f"<tr><td>{escape(claim['text'])}<div class='muted'>{escape(claim['evidence_kind'])}</div></td>"
            f"<td>{escape(claim['status'])}</td><td>{escape(claim['reason'])}{quote}</td></tr>")
    add("</table>")

    if report.get("disagreements"):
        add("<h2>Evaluator disagreements</h2>")
        for item in report["disagreements"]:
            add(f"<p><strong>{escape(item['subject'])}</strong> — {escape(item['summary'])}</p><ul>")
            for pos in item["positions"]:
                add(f"<li>{escape(pos['perspective'])}: {escape(pos['status'])} — {escape(pos['reason'])}"
                    f"{' <blockquote>' + escape(pos['quote']) + '</blockquote>' if pos.get('quote') else ''}</li>")
            add("</ul>")

    delivery = report.get("delivery") or {}
    add("<h2>Delivery</h2>")
    add(f"<p>{escape(delivery.get('note', ''))}</p>")
    for line in delivery.get("observations", []):
        add(f"<p>• {escape(line)}</p>")

    add("<h2>Next practice</h2><ol>")
    for rec in report.get("recommendations", []):
        add(f"<li><strong>{escape(rec['title'])}</strong> — {escape(rec['why'])} "
            f"<em>{escape(rec['action'])}</em></li>")
    add("</ol><h2>Limitations</h2><ul>")
    for line in report.get("limitations", []):
        add(f"<li>{escape(line)}</li>")
    add("</ul></body></html>")
    return "".join(parts)


__all__ = ["render_scorecard"]
