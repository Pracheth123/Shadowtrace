"""
History, honest comparisons, recurring gaps and the next practice plan.

Comparisons are only ever drawn between *compatible* sessions (see
`roadmap/reports.compat_key`): same profession, round selection, lane, rubric
versions and evaluator kind. Within a compatible group:

  - one session → no comparison at all;
  - two sessions → "change since your previous comparable session", per
    dimension assessed in both, explicitly *not* called a trend;
  - three or more → a series per round score, still with the caveat that
    practice sessions vary in what they reach.

A difference in difficulty between compared sessions is stated beside the
comparison rather than silently adjusted for.

Stage 16: a finding the candidate disputed is not counted as a recurring gap
or a next-practice item, and a dimension whose only support was a disputed
finding is left out of the comparison (with a note) until the dispute is
settled. Practice attempts are listed separately and never join a trend.
"""

from __future__ import annotations

from collections import defaultdict

from interview.candidates import CandidateRegistry, read_json
from interview.roadmap.reports import ReportStore
from interview.services.feedback import EXCLUDING, contested, read_disputes

ROUND_LABEL = {
    "hr": "HR",
    "hiring_manager": "Hiring manager",
    "domain_specialist": "Domain specialist",
    "full": "Full interview",
}


def session_rows(registry: CandidateRegistry, store: ReportStore, candidate_id: str) -> list[dict]:
    """Every session the candidate has, newest first, whatever its state."""
    scored = {row["session_id"]: row for row in store.sessions(candidate_id, kind=None)}
    rows: list[dict] = []
    for session_id in registry.session_ids(candidate_id):
        meta = read_json(registry.session_dir(candidate_id, session_id) / "meta.json", {}) or {}
        cfg = meta.get("config") or {}
        row = scored.get(session_id)
        rows.append(
            {
                "session_id": session_id,
                "created_at": meta.get("created_at", ""),
                "state": meta.get("state", "unknown"),
                "target_role": cfg.get("target_role") or meta.get("pack_id") or "",
                "role_family": cfg.get("role_family") or "",
                "round": cfg.get("round") or "",
                "round_label": ROUND_LABEL.get(cfg.get("round") or "", cfg.get("round") or ""),
                "lane": meta.get("lane", ""),
                "intensity": meta.get("intensity", cfg.get("intensity", "")),
                "ended_reason": meta.get("ended_reason", ""),
                "overall_score": row["overall_score"] if row else None,
                "evaluator_provider": row["evaluator_provider"] if row else None,
                "compat_key": row["compat_key"] if row else None,
                "error": meta.get("error"),
                "kind": meta.get("kind") or "interview",
                "practice": meta.get("practice"),
                "open_disputes": sum(
                    1
                    for d in read_disputes(registry.session_dir(candidate_id, session_id)).values()
                    if d.get("status") in EXCLUDING
                ),
            }
        )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def _contested_by_session(
    registry: CandidateRegistry | None, candidate_id: str, session_ids: list[str]
) -> dict[str, dict]:
    """session id → contested findings/dimensions from that session's disputes."""
    if registry is None:
        return {}
    out: dict[str, dict] = {}
    for session_id in session_ids:
        directory = registry.session_dir(candidate_id, session_id)
        disputes = read_disputes(directory)
        if not disputes:
            continue
        report = read_json(directory / "evaluation" / "report.json", {}) or {}
        out[session_id] = contested(report, disputes)
    return out


def comparisons(
    store: ReportStore, candidate_id: str, registry: CandidateRegistry | None = None
) -> list[dict]:
    """One entry per compatible group that has at least two evaluated sessions."""
    sessions = store.sessions(candidate_id)
    excluded = _contested_by_session(registry, candidate_id, [s["session_id"] for s in sessions])
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in sessions:
        groups[row["compat_key"]].append(row)
    out: list[dict] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (r["created_at"], r["session_id"]))
        previous, latest = members[-2], members[-1]
        dims = store.dimensions([previous["session_id"], latest["session_id"]])
        by_session: dict[str, dict[tuple[str, str], dict]] = defaultdict(dict)
        for dim in dims:
            by_session[dim["session_id"]][(dim["round"], dim["dimension_id"])] = dim
        changes: list[dict] = []
        not_comparable: list[str] = []
        disputed: list[str] = []
        blocked: set = set()
        for sid in (previous["session_id"], latest["session_id"]):
            blocked |= set(excluded.get(sid, {}).get("dimensions", set()))
        for ref, now in sorted(by_session[latest["session_id"]].items()):
            before = by_session[previous["session_id"]].get(ref)
            if before is None:
                continue
            if ref in blocked:
                disputed.append(now["label"])
                continue
            if before["score"] is None or now["score"] is None:
                not_comparable.append(now["label"])
                continue
            changes.append(
                {
                    "round": ref[0],
                    "dimension_id": ref[1],
                    "label": now["label"],
                    "previous_level": before["level"],
                    "latest_level": now["level"],
                    "delta": round(now["score"] - before["score"], 4),
                }
            )
        notes = [
            "Practice sessions differ in which questions they reach, so a change "
            "between two sessions is not proof of improvement."
        ]
        if previous["intensity"] != latest["intensity"]:
            notes.append(
                f"Difficulty differed: {previous['intensity']} then {latest['intensity']}. "
                "Scores are not adjusted for difficulty."
            )
        if not_comparable:
            notes.append(
                "Not compared because one session did not assess them: "
                + ", ".join(sorted(set(not_comparable)))
                + "."
            )
        if disputed:
            notes.append(
                "Left out while you dispute the only feedback behind them: "
                + ", ".join(sorted(set(disputed)))
                + "."
            )
        series = (
            [
                {
                    "session_id": m["session_id"],
                    "created_at": m["created_at"],
                    "overall_score": m["overall_score"],
                    "intensity": m["intensity"],
                }
                for m in members
            ]
            if len(members) >= 3
            else []
        )
        out.append(
            {
                "compat_key": key,
                "label": (
                    f"{latest['target_role'] or latest['role_family']} · "
                    f"{ROUND_LABEL.get(latest['round_selection'], latest['round_selection'])} · "
                    f"{latest['lane']}"
                ),
                "sessions": len(members),
                "kind": "trend" if len(members) >= 3 else "comparison",
                "previous_session_id": previous["session_id"],
                "latest_session_id": latest["session_id"],
                "overall_previous": previous["overall_score"],
                "overall_latest": latest["overall_score"],
                "changes": changes,
                "series": series,
                "notes": notes,
            }
        )
    return out


def recurring_gaps(
    store: ReportStore, candidate_id: str, registry: CandidateRegistry | None = None
) -> list[dict]:
    """A gap dimension seen in two or more sessions of the same compatible group."""
    sessions = store.sessions(candidate_id)
    key_of = {row["session_id"]: row["compat_key"] for row in sessions}
    gaps = store.gaps(list(key_of))
    excluded = _contested_by_session(registry, candidate_id, list(key_of))
    seen: dict[tuple[str, str, str], dict] = {}
    for gap in gaps:
        blocked = excluded.get(gap["session_id"], {}).get("findings", set())
        if gap.get("finding_id") and gap["finding_id"] in blocked:
            continue
        ref = (key_of[gap["session_id"]], gap["round"], gap["dimension_id"])
        entry = seen.setdefault(
            ref,
            {
                "round": gap["round"],
                "dimension_id": gap["dimension_id"],
                "dimension_label": gap["dimension_label"],
                "sessions": [],
                "examples": [],
            },
        )
        if gap["session_id"] not in entry["sessions"]:
            entry["sessions"].append(gap["session_id"])
            entry["examples"].append(
                {
                    "session_id": gap["session_id"],
                    "turn_id": gap["turn_id"],
                    "quote": gap["quote"],
                    "explanation": gap["explanation"],
                }
            )
    return [entry for entry in seen.values() if len(entry["sessions"]) >= 2]


def practice_plan(
    registry: CandidateRegistry, store: ReportStore, candidate_id: str
) -> dict:
    """
    Next practice: recurring gaps first, then the latest report's
    recommendations, then preparation guidance from the latest intake. Every
    item names where it came from.
    """
    items: list[dict] = []
    for gap in recurring_gaps(store, candidate_id, registry):
        example = gap["examples"][-1]
        items.append(
            {
                "title": f"Recurring gap: {gap['dimension_label']}",
                "why": f"Flagged in {len(gap['sessions'])} comparable sessions.",
                "action": "Pick one story that shows this and rehearse it until it is specific.",
                "source": "recurring_gap",
                "evidence": [
                    {"session_id": e["session_id"], "turn_id": e["turn_id"], "quote": e["quote"]}
                    for e in gap["examples"][-2:]
                ],
                "session_id": example["session_id"],
            }
        )
    sessions = store.sessions(candidate_id)
    latest_report = None
    if sessions:
        latest = sessions[-1]
        latest_report = read_json(
            registry.session_dir(candidate_id, latest["session_id"]) / "evaluation" / "report.json",
            None,
        )
    if latest_report:
        latest_dir = registry.session_dir(candidate_id, latest_report["session_id"])
        blocked = contested(latest_report, read_disputes(latest_dir))["findings"]
        for rec in latest_report.get("recommendations", []):
            ids = {e.get("finding_id") for e in rec.get("evidence", []) if e.get("finding_id")}
            if ids & blocked:
                continue
            items.append({**rec, "session_id": latest_report["session_id"]})

    intake_items: list[dict] = []
    intake_root = registry.candidate_dir(candidate_id) / "intake"
    if intake_root.is_dir():
        ready = []
        for directory in intake_root.iterdir():
            status = read_json(directory / "status.json", {}) or {}
            if status.get("state") == "ready":
                ready.append((status.get("completed_at", ""), directory))
        if ready:
            ready.sort()
            prep = read_json(ready[-1][1] / "prep.json", {}) or {}
            intake_items = [
                {
                    "title": item["title"],
                    "why": item["detail"],
                    "action": item["action"],
                    "source": f"intake:{item['kind']}",
                    "evidence": item.get("evidence", []),
                }
                for item in prep.get("items", [])
            ]
    from interview.services.practice import list_practices, practice_view

    practice_items = []
    for record in list_practices(registry, candidate_id)[:6]:
        view = practice_view(registry, candidate_id, record)
        practice_items.append(
            {
                "practice_id": record["practice_id"],
                "title": f"Practice: {record['dimension_label']} ({record.get('round_label')})",
                "mode": record["mode"],
                "attempts": len(record.get("attempts", [])),
                "latest_outcome": view["latest_outcome"],
                "offer_unaided_variation": view["offer_unaided_variation"],
                "parent_session_id": record["parent"]["session_id"],
                "parent_finding_id": record["parent"]["finding_id"],
                "created_at": record.get("created_at"),
            }
        )
    return {
        "items": items[:10],
        "practice": practice_items,
        "preparation": intake_items,
        "based_on_sessions": len(sessions),
        "note": (
            "No evaluated sessions yet — this plan comes from your intake only."
            if not sessions
            else "Built from your evaluated sessions and your latest intake."
        ),
    }


__all__ = ["comparisons", "practice_plan", "recurring_gaps", "session_rows"]
