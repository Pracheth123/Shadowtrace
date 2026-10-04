#!/usr/bin/env python3
"""
Live journey smoke test — REAL providers, not mocks.

Drives the actual FastAPI app in-process through its HTTP API and WebSocket:

    guest → intake (real parsing) → text-lane interview with the Groq
    interviewer (ModelProposer) → background evaluation with the Groq
    evaluator → report → history

Requires GROQ_API_KEY in .env. Uses the text lane, so it does not exercise
Deepgram; tools/live_voice_smoke.py covers speech. Writes everything to
--out so the evidence can be inspected.

    python tools/live_journey_smoke.py --out logs/live_journey
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RESUME = """Priya Raman
priya@example.com

Experience
Payments platform, Acme Pay
I led the migration of our card settlement reconciliation from nightly batch jobs to a streaming pipeline on Kafka.
I chose idempotent consumers keyed on settlement id because duplicate bank files were our biggest source of mismatches.
I reduced reconciliation mismatches from about 2 percent to under 0.1 percent over one quarter.
Tech: Python, Kafka, PostgreSQL
"""

ANSWERS = [
    "I've spent five years on payments infrastructure. I moved from a generalist backend role into payments "
    "because I wanted to own systems where correctness is measurable, and I'm now looking for a senior role "
    "where I can own a platform end to end.",
    "The migration was mine end to end. The batch job ran nightly and mismatches were only found the next day. "
    "I proposed streaming settlement files through Kafka, wrote the design doc, and got sign-off from finance "
    "because they needed same-day visibility.",
    "We rejected exactly-once delivery in Kafka because it tied us to transactional producers across services we "
    "didn't own. Instead I made each consumer idempotent with a settlement-id key in Postgres, so a duplicate file "
    "became a no-op insert. I tested it by replaying a month of real files including the known duplicates.",
    "The first week in production we saw lag spikes at month end. I profiled it and found one hot partition for a "
    "large merchant, so we re-keyed by merchant and settlement date, and lag went back under a minute.",
    "Honestly I didn't build the alerting myself — a teammate did — but I defined the thresholds with finance.",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "logs" / "live_journey")
    parser.add_argument("--round", default="domain_specialist")
    parser.add_argument("--family", default="software")
    args = parser.parse_args()

    os.environ["MOCK_LLM"] = "0"
    os.environ.setdefault("APP_ENV", "dev")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(out / "data")

    from fastapi.testclient import TestClient

    from interview.config import get_settings

    settings = get_settings()
    if not settings.has_groq:
        print("GROQ_API_KEY is not set; this smoke test needs a real provider.")
        return 2
    import interview.server as server

    server.configure_data_dir(out / "data")
    print("interviewer model:", settings.model_live_interviewer)
    print("evaluator model:  ", settings.model_evaluator)

    evidence: dict = {"providers": settings.public_dict()["models"]}
    with TestClient(server.app) as client:
        token = client.post("/api/guest").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/api/intake",
            data={
                "target_role": "Senior backend engineer (payments)",
                "role_family": args.family,
                "seniority": "senior",
                "round": args.round,
                "lane": "text",
                "intensity": "realistic",
                "job_description": "Required skills\nPython, Kafka, Kubernetes, PostgreSQL",
            },
            files={"resume": ("priya.txt", RESUME.encode(), "text/plain")},
            headers=headers,
        )
        response.raise_for_status()
        intake_id = response.json()["intake_id"]
        while True:
            intake = client.get(f"/api/intake/{intake_id}", headers=headers).json()
            if intake["status"]["state"] in ("ready", "failed"):
                break
            time.sleep(0.2)
        evidence["intake"] = intake
        print("intake:", intake["status"]["state"], "claims:", len(intake.get("claims", [])))
        if intake["status"]["state"] != "ready":
            print(json.dumps(intake["status"], indent=2))
            return 1

        wire: list[dict] = []

        def read_until(ws, wanted):
            while True:
                data = ws.receive()
                if data.get("text") is None:
                    if data.get("type") == "websocket.close":
                        return
                    continue
                msg = json.loads(data["text"])
                wire.append(msg)
                if msg["type"] == "agent_utterance_start":
                    print(f"\n[{msg.get('speaker_label')}] {msg['text']}")
                if msg["type"] in ("provider_warning", "round_transition"):
                    print("  >>", msg)
                if msg["type"] in wanted:
                    return msg

        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_start", "auth_token": token, "intake_id": intake_id}))
            ready = read_until(ws, {"session_ready", "session_rejected"})
            print("session:", ready)
            session_id = ready["session_id"]
            read_until(ws, {"agent_utterance_end"})
            for answer in ANSWERS:
                print(f"\n[candidate] {answer}")
                ws.send_text(json.dumps({"type": "candidate_text", "text": answer}))
                last = read_until(ws, {"agent_utterance_end", "session_complete"})
                if last and last["type"] == "session_complete":
                    break
            else:
                ws.send_text(json.dumps({"type": "session_end"}))
                read_until(ws, {"session_complete"})

        started = time.monotonic()
        while True:
            meta = client.get(f"/api/sessions/{session_id}", headers=headers).json()
            if meta["state"] in ("complete", "failed"):
                break
            time.sleep(1.0)
        evidence["evaluation_seconds"] = round(time.monotonic() - started, 1)
        evidence["session_meta"] = meta
        print("\nevaluation:", meta["state"], f"in {evidence['evaluation_seconds']} s", meta.get("error") or "")
        if meta["state"] != "complete":
            (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            return 1
        report = client.get(f"/api/sessions/{session_id}/report", headers=headers).json()
        evidence["report"] = report
        evidence["wire"] = wire
        evidence["history"] = client.get("/api/history", headers=headers).json()
        print("evaluator:", report["evaluator"])
        print("overall:", report["overall"]["score"], "-", report["overall"]["note"])
        for result in report["rounds"]:
            print(f"\n== {result['label']} ({result['rubric_version']}) score={result['aggregate']['score']} attempts={result['attempts']} rejected={result['rejected_evidence']}")
            for dim in result["dimensions"]:
                quote = dim["citations"][0]["quote"][:90] if dim["citations"] else ""
                print(f"  {dim['label']:<40} {dim['level']:<22} {quote}")
            for finding in result["findings"]:
                print(f"  [{finding['polarity']}] {finding['dimension_label']}: {finding['explanation'][:120]}")
        print("\nclaims:")
        for claim in report["claims"]:
            print(f"  {claim['status']:<9} {claim['text'][:80]} | {claim['reason'][:100]}")
        print("\nlimitations:", *report["limitations"], sep="\n  ")
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"\nevidence written to {out / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
