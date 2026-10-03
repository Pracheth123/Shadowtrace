# Stage 9 — Longitudinal store, roadmap, dashboard

**Date:** 2026-10-03

## Schema

Append-only SQLite. A repeat insert of the same `session_id` fails and leaves the first row. Trends always filter `candidate_id` and `pack_id`. A blank pack is rejected.

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE dimension_scores (
    session_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    finding_count INTEGER NOT NULL,
    PRIMARY KEY (session_id, dimension),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE TABLE claim_history (
    session_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    status TEXT NOT NULL,
    quote TEXT NOT NULL,
    PRIMARY KEY (session_id, claim_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE TABLE gaps (
    session_id TEXT NOT NULL,
    gap_index INTEGER NOT NULL,
    dimension TEXT NOT NULL,
    summary TEXT NOT NULL,
    quote TEXT NOT NULL,
    PRIMARY KEY (session_id, gap_index),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

## Trend query

```sql
SELECT s.started_at, s.session_id, d.dimension, d.score
FROM sessions s
JOIN dimension_scores d ON d.session_id = s.session_id
WHERE s.candidate_id = ? AND s.pack_id = ?
ORDER BY s.started_at, s.session_id, d.dimension
```

Seed is four `behavioral-core` sessions for `ada`, plus one `public-company` session that this query does not return.

```
2026-09-01T10:00:00Z  ada-1  competency  0.3
2026-09-01T10:00:00Z  ada-1  delivery    0.5
2026-09-01T10:00:00Z  ada-1  structure   0.4
2026-09-01T10:00:00Z  ada-1  technical   0.25
2026-09-08T10:00:00Z  ada-2  competency  0.4
2026-09-08T10:00:00Z  ada-2  delivery    0.55
2026-09-08T10:00:00Z  ada-2  structure   0.45
2026-09-08T10:00:00Z  ada-2  technical   0.4
2026-09-15T10:00:00Z  ada-3  competency  0.6
2026-09-15T10:00:00Z  ada-3  delivery    0.5
2026-09-15T10:00:00Z  ada-3  structure   0.55
2026-09-15T10:00:00Z  ada-3  technical   0.6
2026-09-22T10:00:00Z  ada-4  competency  0.75
2026-09-22T10:00:00Z  ada-4  delivery    0.65
2026-09-22T10:00:00Z  ada-4  structure   0.7
2026-09-22T10:00:00Z  ada-4  technical   0.8
```

Technical rises 0.25 → 0.80 inside the pack.

## Roadmap

```
- Redo the technical answer that stalled: The Kafka answer was retracted.
  evidence: I didn't build the Kafka pipeline. (ada-1)
- Redo the structure answer that stalled: The answer is too short to show how the decision was made.
  evidence: We shipped it. (ada-2)
- Redo the delivery answer that stalled: This answer hedges more than the first.
  evidence: I think maybe the lag was fine. (ada-3)
- Spend the next practice on delivery. It is the lowest score in the latest session.
  evidence: delivery 0.65 in ada-4 (ada-4)
```

The dashboard reads `client/public/dashboard.json` at `#dashboard`. The first card is the canned `fake_eval` report. The bars are the pack trend above.

```bash
python tools/roadmap.py
```
