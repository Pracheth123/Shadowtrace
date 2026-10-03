"""
Read-only repo indexer agent.

Tools are list_dir, read_file, grep, and git_log. There is no code-execution
tool. The exploration trace is JSONL on disk. It is not emitted on the live
session bus.

Shallow clone is time- and size-limited. A replay tool source returns recorded
tool_result payloads and never clones.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from interview.intake.schema import RepoSummary

ALLOWED_TOOLS = frozenset({"list_dir", "read_file", "grep", "git_log"})
MAX_FILE_BYTES = 32_000
MAX_LIST = 200
MAX_GREP_HITS = 20
MAX_CLONE_BYTES = 20_000_000
CLONE_TIMEOUT_S = 25


class ToolNotAllowed(RuntimeError):
    """Raised when a call is outside the read-only tool set."""


class IndexerTools(Protocol):
    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Evidence:
    path: str
    text: str


@dataclass
class IndexRun:
    summary: RepoSummary
    evidence: list[Evidence] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)


def _require_allowed(name: str) -> None:
    if name not in ALLOWED_TOOLS:
        raise ToolNotAllowed(name)


class FsTools:
    """Filesystem tools rooted at a cloned or pre-indexed repo."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        _require_allowed(name)
        if name == "list_dir":
            return self._list_dir(str(args.get("path", ".")))
        if name == "read_file":
            return self._read_file(str(args.get("path", "")))
        if name == "grep":
            return self._grep(str(args.get("pattern", "")), str(args.get("path", ".")))
        return self._git_log(int(args.get("n", 5)))

    def _resolve(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        candidate.relative_to(self.root)
        return candidate

    def _list_dir(self, rel: str) -> dict[str, Any]:
        path = self._resolve(rel or ".")
        if not path.is_dir():
            return {"path": rel, "entries": [], "error": "not a directory"}
        entries: list[str] = []
        for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            if child.name == ".git":
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= MAX_LIST:
                break
        return {"path": rel or ".", "entries": entries}

    def _read_file(self, rel: str) -> dict[str, Any]:
        path = self._resolve(rel)
        if not path.is_file():
            return {"path": rel, "text": "", "truncated": False, "error": "not a file"}
        data = path.read_bytes()[: MAX_FILE_BYTES + 1]
        truncated = len(data) > MAX_FILE_BYTES
        data = data[:MAX_FILE_BYTES]
        if b"\x00" in data:
            return {"path": rel, "text": "", "truncated": False, "error": "binary"}
        return {
            "path": rel,
            "text": data.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    def _grep(self, pattern: str, rel: str) -> dict[str, Any]:
        if not pattern:
            return {"hits": []}
        path = self._resolve(rel or ".")
        hits: list[dict[str, Any]] = []
        files = [path] if path.is_file() else [
            p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts
        ]
        for file in files:
            if len(hits) >= MAX_GREP_HITS:
                break
            try:
                file.relative_to(self.root)
            except ValueError:
                continue
            blob = file.read_bytes()[:MAX_FILE_BYTES]
            if b"\x00" in blob:
                continue
            text = blob.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    hits.append(
                        {
                            "path": str(file.relative_to(self.root)),
                            "line": lineno,
                            "text": line[:240],
                        }
                    )
                    if len(hits) >= MAX_GREP_HITS:
                        break
        return {"hits": hits}

    def _git_log(self, n: int) -> dict[str, Any]:
        count = max(1, min(n, 20))
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "log",
                    "-n",
                    str(count),
                    "--pretty=format:%h %s",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"commits": [], "error": str(exc)}
        if proc.returncode != 0:
            return {"commits": [], "error": (proc.stderr or "git log failed").strip()}
        commits = [line for line in proc.stdout.splitlines() if line.strip()]
        return {"commits": commits}


class ReplayTools:
    """Return recorded tool_result payloads. Does not touch git or the repo."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        _require_allowed(name)
        self.calls.append(name)
        if not self._results:
            return {"error": "no stub tool_result"}
        nxt = self._results.pop(0)
        if nxt.get("tool") not in (None, name):
            raise RuntimeError(f"stub tool {nxt.get('tool')!r} != {name!r}")
        result = nxt.get("result", {})
        return dict(result) if isinstance(result, dict) else {"value": result}


class TraceLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.rows: list[dict[str, Any]] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class RepoIndexer:
    """
    Fixed exploration policy. The agent proposes the next tool; the step
    limit stops the loop. It never selects a command outside ALLOWED_TOOLS.
    """

    def __init__(
        self,
        tools: IndexerTools,
        *,
        max_steps: int = 8,
        trace: TraceLog | None = None,
        summary: RepoSummary | None = None,
    ) -> None:
        self._tools = tools
        self._max_steps = max_steps
        self._trace = trace or TraceLog(None)
        self._summary = summary or RepoSummary()
        self._steps = 0
        self._limited = False

    def run(self) -> IndexRun:
        self._trace.write(
            {
                "type": "agent_step",
                "step_index": self._steps,
                "phase": "observe",
                "summary": "start read-only exploration",
            }
        )
        listing = self._act("list_dir", {"path": "."})
        entries = listing.get("entries") or []
        self._summary.files_listed = len(entries)
        readme = _first_readme(entries)
        evidence: list[Evidence] = []
        if readme and not self._limited:
            loaded = self._act("read_file", {"path": readme})
            text = str(loaded.get("text") or "")
            if text:
                evidence.append(Evidence(path=readme, text=text))
                self._summary.bytes_read += len(text.encode("utf-8"))
        if not self._limited:
            self._act("grep", {"pattern": "def ", "path": "."})
        if not self._limited:
            log = self._act("git_log", {"n": 5})
            commits = log.get("commits") or []
            if commits and not self._summary.commit:
                self._summary.commit = str(commits[0]).split()[0]
        if self._limited:
            self._summary.truncated = True
            self._trace.write(
                {
                    "type": "agent_step",
                    "step_index": self._steps,
                    "phase": "cancelled",
                    "summary": "step limit reached",
                    "limit": True,
                }
            )
        return IndexRun(
            summary=self._summary,
            evidence=evidence,
            tool_names=list(getattr(self._tools, "calls", []) or _names_from_trace(self._trace)),
        )

    def _act(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._steps >= self._max_steps:
            self._limited = True
            return {}
        _require_allowed(name)
        step = self._steps
        self._steps += 1
        self._trace.write(
            {
                "type": "agent_step",
                "step_index": step,
                "phase": "act",
                "summary": f"call {name}",
            }
        )
        self._trace.write(
            {"type": "tool_call", "step_index": step, "tool": name, "args": args}
        )
        try:
            result = self._tools.call(name, args)
            ok = "error" not in result
        except ToolNotAllowed:
            raise
        except Exception as exc:  # tool failure is a result, not a crash
            result = {"error": str(exc)}
            ok = False
        self._trace.write(
            {
                "type": "tool_result",
                "step_index": step,
                "tool": name,
                "ok": ok,
                "result": result,
            }
        )
        return result


def _first_readme(entries: list[str]) -> str | None:
    for name in entries:
        plain = name.rstrip("/")
        if plain.lower().startswith("readme"):
            return plain
    return None


def _names_from_trace(trace: TraceLog) -> list[str]:
    return [row["tool"] for row in trace.rows if row.get("type") == "tool_call"]


def repo_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        total += path.stat().st_size
        if total > MAX_CLONE_BYTES:
            return total
    return total


def shallow_clone(url: str, dest: Path, *, timeout_s: int = CLONE_TIMEOUT_S) -> None:
    """git clone --depth 1. Raises if git fails, times out, or the tree is too big."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git clone failed").strip())
    if repo_size_bytes(dest) > MAX_CLONE_BYTES:
        raise RuntimeError(f"clone exceeds {MAX_CLONE_BYTES} bytes")
