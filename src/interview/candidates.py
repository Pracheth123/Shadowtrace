"""
Guest identity and per-candidate storage.

There is no account system in this repo, so a candidate is a *guest*: the
server issues an opaque bearer token once, the browser keeps it, and every API
call and session resolves the candidate from that token — never from an id the
client names. Its limits are real and stated plainly:

  - the token lives in one browser; clearing site data loses access (the data
    remains on disk until deleted by an operator or by "delete my data" first);
  - there is no recovery, no email, no cross-device login;
  - anyone holding the token is that candidate. It is a bearer secret.

Isolation follows from layout. Everything a candidate owns lives under
`<root>/<candidate_id>/`, and every path is built here from the *authenticated*
candidate id plus a validated id segment. Asking for another candidate's
session id therefore resolves inside your own directory and finds nothing —
the API answers 404, not 403, so ids cannot be probed for existence.

Only a SHA-256 of the token secret is stored. Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_ID = re.compile(r"^[a-f0-9]{32}$")
# Session ids predate this module and are uuid4 strings with dashes.
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")


class UnknownCandidate(LookupError):
    """No candidate for this token. Callers answer 401."""


class NotFound(LookupError):
    """The authenticated candidate has no such record. Callers answer 404."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def valid_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


def valid_session_id(value: str) -> bool:
    return bool(_SESSION_ID.match(value or "")) and ".." not in value


@dataclass(frozen=True)
class Guest:
    candidate_id: str
    token: str


class CandidateRegistry:
    """Issue and resolve guest tokens; build every per-candidate path."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def create_guest(self) -> Guest:
        candidate_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        directory = self.candidate_dir(candidate_id)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "identity.json").write_text(
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "secret_sha256": _hash(secret),
                    "created_at": utc_now(),
                    "kind": "guest",
                }
            ),
            encoding="utf-8",
        )
        return Guest(candidate_id=candidate_id, token=f"{candidate_id}.{secret}")

    def resolve(self, token: str | None) -> str:
        """Candidate id for a bearer token, or `UnknownCandidate`."""
        raw = (token or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        candidate_id, _, secret = raw.partition(".")
        if not valid_id(candidate_id) or not secret:
            raise UnknownCandidate("missing or malformed token")
        identity = self.candidate_dir(candidate_id) / "identity.json"
        if not identity.is_file():
            raise UnknownCandidate("unknown candidate")
        try:
            stored = json.loads(identity.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UnknownCandidate("unreadable identity") from exc
        if not hmac.compare_digest(str(stored.get("secret_sha256", "")), _hash(secret)):
            raise UnknownCandidate("token does not match")
        return candidate_id

    # ------------------------------------------------------------------
    # Paths — always from an authenticated candidate id
    # ------------------------------------------------------------------

    def candidate_dir(self, candidate_id: str) -> Path:
        if not valid_id(candidate_id):
            raise NotFound("invalid candidate id")
        return self.root / candidate_id

    def intake_dir(self, candidate_id: str, intake_id: str) -> Path:
        if not valid_id(intake_id):
            raise NotFound("invalid intake id")
        return self.candidate_dir(candidate_id) / "intake" / intake_id

    def session_dir(self, candidate_id: str, session_id: str) -> Path:
        if not valid_session_id(session_id):
            raise NotFound("invalid session id")
        return self.candidate_dir(candidate_id) / "sessions" / session_id

    def existing_intake(self, candidate_id: str, intake_id: str) -> Path:
        path = self.intake_dir(candidate_id, intake_id)
        if not path.is_dir():
            raise NotFound("no such intake")
        return path

    def existing_session(self, candidate_id: str, session_id: str) -> Path:
        path = self.session_dir(candidate_id, session_id)
        if not path.is_dir():
            raise NotFound("no such session")
        return path

    def session_ids(self, candidate_id: str) -> list[str]:
        sessions = self.candidate_dir(candidate_id) / "sessions"
        if not sessions.is_dir():
            return []
        return sorted(p.name for p in sessions.iterdir() if p.is_dir())

    def delete(self, candidate_id: str) -> list[str]:
        """Remove the candidate's whole directory. Returns what was removed."""
        directory = self.candidate_dir(candidate_id)
        if not directory.exists():
            return []
        removed = [str(p) for p in directory.rglob("*")]
        shutil.rmtree(directory)
        return removed


def utc_now() -> str:
    """ISO-8601 UTC with milliseconds, so two sessions minutes apart never tie."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    return uuid.uuid4().hex


def write_json(path: Path, payload) -> None:
    """Atomic-enough JSON write: temp file then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


__all__ = [
    "CandidateRegistry",
    "Guest",
    "NotFound",
    "UnknownCandidate",
    "new_id",
    "utc_now",
    "read_json",
    "valid_id",
    "valid_session_id",
    "write_json",
]
