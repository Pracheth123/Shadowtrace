"""
Deepgram streaming speech-to-text.

Talks the documented `/v1/listen` WebSocket protocol directly over `websockets`
(already a dependency) rather than through `deepgram-sdk`. That is a deliberate
change from the stage-2 adapter, for three reasons:

  1. `deepgram-sdk` was declared in pyproject but never installed, and the old
     adapter caught `ImportError` and continued in "no-op mode" — so a session
     emitted no transcripts and still looked successful. A missing dependency
     should not be able to do that.
  2. The SDK's async surface has moved repeatedly across versions
     (`listen.asyncwebsocket.v("1")` and friends). Pinning a version we cannot
     exercise against a live key buys confidence we do not have.
  3. The wire protocol is documented, stable, and — crucially — can be tested
     offline against a local fake server that speaks the same JSON. Those tests
     are in tests/test_stage12_deepgram.py and run with no credentials.

Turn boundaries are the part most easily got wrong, so to be explicit:

  - `is_final: true` arrives **many times inside one answer**. Each one
     finalises a *segment*, not the answer. Treating each as a completed answer
     is what makes the interviewer reply mid-sentence, and it is what the
     previous adapter did.
  - `speech_final: true` means provider endpointing detected a pause (300 ms).
  - `UtteranceEnd` arrives after ~1 s with no new words. It needs
     `vad_events=true`.

Neither is the end of an answer: candidates pause to think for longer than
both, and ending the answer on them moved the interview to the next question
mid-thought. So both only start a silence clock. Segments are buffered, and
exactly one `final_transcript` is emitted when the candidate has been silent
for ANSWER_END_SILENCE_MS after their last recognised word (longer,
ANSWER_END_EXTENDED_MS, when the answer sounds unfinished), or when they press
"Done answering".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable
from urllib.parse import urlencode

from interview.events.schema import FinalTranscript, Partial, WordTiming

if TYPE_CHECKING:
    from interview.events.bus import EventBus

log = logging.getLogger(__name__)

# Deepgram closes an idle socket. A KeepAlive well inside that window costs
# nothing and avoids a reconnect in the middle of a candidate's thinking pause.
KEEPALIVE_INTERVAL_S = 5.0
CONNECT_TIMEOUT_S = 10.0
# An answer that ends on one of these is probably mid-thought ("...and", "so
# um"), so it gets the longer silence allowance before it is treated as over.
TRAILING_WORDS = frozenset(
    {
        "and", "so", "because", "but", "or", "that", "which", "if", "then",
        "when", "although", "however", "like", "the", "a", "an", "to", "of",
        "with", "um", "uh", "erm", "hmm", "mm", "well", "basically", "also",
    }
)
# Answers shorter than this are usually a start ("So I think..."), not an end.
SHORT_ANSWER_WORDS = 4
# "Done answering" asks Deepgram to finalise buffered audio; wait this long for
# the last words before closing the answer without them.
CLIENT_FINALIZE_WAIT_S = 1.0
_ANSWER_WORDS = re.compile(r"[\w']+")
# Across all keyterms, per the keyterm documentation.
KEYTERM_TOKEN_BUDGET = 500


class SttUnavailable(RuntimeError):
    """
    The speech provider could not be reached or refused the connection.

    Raised rather than degraded, so the session surfaces an honest unavailable
    state instead of transcribing nothing and calling the interview complete.
    """


def build_keyterms(terms: list[str], *, max_terms: int, budget: int = KEYTERM_TOKEN_BUDGET) -> list[str]:
    """
    Trim a candidate's vocabulary to what the provider will accept.

    Keyterms raise recognition of technical words the model would otherwise
    mangle ("Pydantic", "SystemVerilog", "backpressure"). They are a
    *recognition* aid only: nothing downstream scores a keyterm hit, which is
    why this list can be derived from the Claims File without becoming a way to
    score points by saying a word.

    Weighted syntax (`term:0.15`) is Nova-2 `keywords` and is rejected by
    `keyterm` — it would be accepted as a literal phrase including the colon, so
    it is stripped here instead.
    """
    seen: set[str] = set()
    chosen: list[str] = []
    tokens_used = 0
    for raw in terms:
        term = " ".join(str(raw).split())
        if not term:
            continue
        # Strip any Nova-2 weight suffix rather than sending it as literal text.
        if ":" in term:
            term = term.split(":", 1)[0].strip()
        folded = term.casefold()
        if not term or folded in seen:
            continue
        cost = len(term.split())
        if tokens_used + cost > budget or len(chosen) >= max_terms:
            break
        seen.add(folded)
        chosen.append(term)
        tokens_used += cost
    return chosen


@dataclass
class SttStats:
    """What actually happened on the socket, for the log and for tests."""

    partials: int = 0
    segments_finalised: int = 0
    turns_completed: int = 0
    boundary_counts: dict[str, int] = field(default_factory=dict)
    bytes_sent: int = 0
    reconnects: int = 0
    errors: list[str] = field(default_factory=list)

    def note_boundary(self, kind: str) -> None:
        self.boundary_counts[kind] = self.boundary_counts.get(kind, 0) + 1


class DeepgramStt:
    """
    One streaming STT connection for one session.

    Usage::

        stt = DeepgramStt(bus, session_id, settings, keyterms=[...],
                          get_turn_id=lambda: current_turn)
        await stt.start()
        await stt.send_audio(pcm16_bytes)
        ...
        await stt.close()

    Emits `partial` and exactly one `final_transcript` per candidate turn.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        settings,
        *,
        keyterms: list[str] | None = None,
        get_turn_id: Callable[[], str | None] | None = None,
        producer: str = "deepgram_stt",
        url_override: str | None = None,
        on_speech_started: Callable[[], Awaitable[None]] | None = None,
        on_lost: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._settings = settings
        self._keyterms = build_keyterms(
            keyterms or [], max_terms=settings.deepgram_max_keyterms
        )
        self._get_turn_id = get_turn_id or (lambda: None)
        self._producer = producer
        self._url_override = url_override
        self._on_speech_started = on_speech_started
        # Called once if the provider stream dies while the session is open,
        # so the candidate is told instead of talking to a deaf socket.
        self._on_lost = on_lost
        self._lost_reported = False

        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._closed = False
        self._started_at = 0.0

        # Turn accumulation state.
        self._segments: list[str] = []
        self._segment_words: list[WordTiming] = []
        self._segment_confidences: list[float] = []
        self._revision = 0
        self._last_partial_text = ""
        self._emitting = asyncio.Lock()
        # Ends the answer after enough silence; see `_watch_answer_end`.
        self._answer_task: asyncio.Task | None = None
        # Monotonic time the candidate was last heard saying a word.
        self._last_speech_at = 0.0
        # Set when a finalised segment arrives; "Done answering" waits on it.
        self._final_seen = asyncio.Event()
        # The provider socket died and has not been replaced yet.
        self._dead = False

        self.stats = SttStats()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def query_params(self) -> list[tuple[str, str]]:
        """
        The exact query string sent to Deepgram.

        A list of pairs, not a dict: `keyterm` is repeated once per term, which
        a dict cannot express. Encoding and sample_rate are always declared —
        the server converts the browser's audio itself, so it knows precisely
        what it is sending and never has to guess.
        """
        s = self._settings
        params: list[tuple[str, str]] = [
            ("model", s.deepgram_stt_model),
            ("language", s.deepgram_language),
            ("encoding", s.deepgram_encoding),
            ("sample_rate", str(s.deepgram_sample_rate)),
            ("channels", str(s.deepgram_channels)),
            ("interim_results", "true" if s.deepgram_interim_results else "false"),
            ("smart_format", "true" if s.deepgram_smart_format else "false"),
            ("punctuate", "true"),
            ("endpointing", str(s.deepgram_endpointing_ms)),
            # UtteranceEnd and SpeechStarted are only delivered with vad_events.
            ("vad_events", "true" if s.deepgram_vad_events else "false"),
        ]
        if s.deepgram_utterance_end_ms:
            params.append(("utterance_end_ms", str(s.deepgram_utterance_end_ms)))
        for term in self._keyterms:
            params.append(("keyterm", term))
        return params

    def url(self) -> str:
        base = self._url_override or self._settings.deepgram_stt_url
        return f"{base}?{urlencode(self.query_params())}"

    async def start(self) -> None:
        import websockets

        key = self._settings.deepgram_api_key.get_secret_value().strip()
        if not key and not self._url_override:
            raise SttUnavailable(
                "DEEPGRAM_API_KEY is not set, so speech-to-text cannot start. "
                "Set it in the server environment, or use the text lane."
            )

        # Only the server ever holds the key. It travels in a request header to
        # Deepgram and is never sent to, or derivable by, the browser.
        headers = [("Authorization", f"Token {key}")] if key else []
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url(), additional_headers=headers),
                timeout=CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            raise SttUnavailable(
                f"Speech-to-text did not connect within {CONNECT_TIMEOUT_S:.0f}s."
            ) from exc
        except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
            raise SttUnavailable(f"Speech-to-text could not connect: {exc}") from exc

        self._started_at = time.monotonic()
        self._closed = False
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        log.info(
            "Deepgram STT open: model=%s rate=%s keyterms=%d",
            self._settings.deepgram_stt_model,
            self._settings.deepgram_sample_rate,
            len(self._keyterms),
        )

    async def send_audio(self, pcm: bytes) -> None:
        """Forward converted PCM. Silently ignored once closed."""
        if self._closed or self._dead or self._ws is None or not pcm:
            return
        try:
            await self._ws.send(pcm)
            self.stats.bytes_sent += len(pcm)
        except Exception as exc:  # noqa: BLE001
            self.stats.errors.append(f"send: {exc}")
            self._dead = True
            await self._report_lost(f"send failed: {exc}")

    async def finalise_turn(self, boundary: str = "client") -> None:
        """
        Close the current turn from our side.

        Used by "Done answering" and push-to-talk release. Deepgram is asked to
        finalise what it has buffered first, so the last words spoken before
        the click are part of the answer. If nothing is buffered this is a
        no-op, so it cannot produce a second answer.
        """
        if self._ws is not None and not (self._closed or self._dead):
            self._final_seen.clear()
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "Finalize"}))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._final_seen.wait(), CLIENT_FINALIZE_WAIT_S)
        await self._emit_turn(boundary)

    async def reconnect(self) -> None:
        """
        Replace a dead provider socket, keeping the answer in progress.

        Raises `SttUnavailable` if the provider cannot be reached; the caller
        decides whether to retry or tell the candidate.
        """
        if self._closed:
            return
        current = asyncio.current_task()
        for task in (self._keepalive_task, self._recv_task):
            if task and task is not current and not task.done():
                task.cancel()
        old, self._ws = self._ws, None
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
        await self.start()
        self._dead = False
        self._lost_reported = False
        self.stats.reconnects += 1
        log.info("Deepgram STT reconnected for session %s", self._session_id)

    async def close(self) -> None:
        """
        Finish cleanly: stop keepalive, tell Deepgram we are done, drain, close.

        `CloseStream` lets the provider flush any trailing transcript rather
        than dropping the last word of the final answer.
        """
        if self._closed and self._ws is None:
            return
        self._closed = True
        self._cancel_answer_watch()
        for task in (self._keepalive_task, self._recv_task):
            if task and not task.done():
                task.cancel()
        for task in (self._keepalive_task, self._recv_task):
            if task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._keepalive_task = None
        self._recv_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        while not self._closed and self._ws is not None:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            if self._closed or self._ws is None:
                return
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue  # the listen socket sends no binary
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.stats.errors.append(f"recv: {exc}")
            log.warning("Deepgram STT receive loop ended: %s", exc)
            self._dead = True
            await self._report_lost(f"connection lost: {exc}")
            return
        if self._closed:
            return
        # The provider closed the stream without an error frame (for example
        # its idle timeout). Unexpected unless we asked for it in close().
        log.warning("Deepgram STT stream closed by the provider")
        self._dead = True
        await self._report_lost("connection closed by the provider")

    async def _report_lost(self, reason: str) -> None:
        """Tell the session once that transcription has stopped. Not on our own close()."""
        if self._closed or self._lost_reported:
            return
        self._lost_reported = True
        log.error("Deepgram STT lost for session %s: %s", self._session_id, reason)
        if self._on_lost is not None:
            with contextlib.suppress(Exception):
                await self._on_lost(reason)

    async def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "Results":
            await self._on_results(message)
        elif kind == "UtteranceEnd":
            # A hint, not a decision: the provider fires this after ~1 s of
            # quiet, which is a thinking pause, not the end of an answer.
            self._ensure_answer_watch()
        elif kind == "SpeechStarted":
            # Voice activity before any words are recognised: keep the answer
            # open while the candidate starts their next sentence.
            self._last_speech_at = time.monotonic()
            if self._on_speech_started:
                await self._on_speech_started()
        elif kind == "Metadata":
            log.debug("Deepgram metadata: %s", message.get("request_id"))
        elif kind == "Error":
            detail = message.get("description") or message.get("message") or str(message)
            self.stats.errors.append(f"provider: {detail}")
            log.error("Deepgram STT error: %s", detail)

    async def _on_results(self, message: dict) -> None:
        channel = message.get("channel") or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            return
        alt = alternatives[0]
        text = (alt.get("transcript") or "").strip()
        is_final = bool(message.get("is_final"))
        confidence = alt.get("confidence")
        start = float(message.get("start") or 0.0)
        duration = float(message.get("duration") or 0.0)

        if text:
            # New words mean the candidate is still talking. Empty results
            # (Deepgram sends them through silence) must not hold the turn open.
            self._last_speech_at = time.monotonic()
        if is_final:
            self._final_seen.set()
            if text:
                self._segments.append(text)
                if confidence is not None:
                    self._segment_confidences.append(float(confidence))
                for word in alt.get("words") or []:
                    self._segment_words.append(
                        WordTiming(
                            word=str(word.get("punctuated_word") or word.get("word") or ""),
                            start_ms=int(float(word.get("start", 0.0)) * 1000),
                            end_ms=int(float(word.get("end", 0.0)) * 1000),
                        )
                    )
                self.stats.segments_finalised += 1
                # A finalised segment supersedes the interim text, so the next
                # partial starts a fresh revision series.
                self._revision = 0
                self._last_partial_text = ""
            # `speech_final` is the provider noticing a ~300 ms gap. That is a
            # breath, so it only starts the silence clock; it does not end the
            # answer (that cut candidates off mid-thought).
            self._ensure_answer_watch()
            return

        # Interim hypothesis. These are what drive speculation, so they go out
        # even when identical text is re-reported — the revision number is what
        # tells the turn controller whether the text is stable.
        if not text:
            return
        combined = " ".join([*self._segments, text]).strip()
        await self._bus.emit(
            Partial(
                session_id=self._session_id,
                turn_id=self._get_turn_id(),
                producer=self._producer,
                t_audio_in=start + duration,
                text=combined,
                stable_until_ms=int(start * 1000),
                revision=self._revision,
                confidence=float(confidence) if confidence is not None else None,
            )
        )
        self._revision += 1
        self._last_partial_text = combined
        self.stats.partials += 1

    def answer_wait_s(self) -> float:
        """
        How much silence ends the answer now.

        Longer when what was said so far sounds unfinished: a trailing
        conjunction or filler, a trailing comma, or only a few words.
        """
        text = (self._last_partial_text or " ".join(self._segments)).strip()
        words = _ANSWER_WORDS.findall(text.casefold())
        unfinished = (
            len(words) < SHORT_ANSWER_WORDS
            or (bool(words) and words[-1] in TRAILING_WORDS)
            or text.endswith((",", "-", "\u2026", "..."))
        )
        ms = (
            self._settings.answer_end_extended_ms
            if unfinished
            else self._settings.answer_end_silence_ms
        )
        return ms / 1000.0

    def _ensure_answer_watch(self) -> None:
        if self._closed or not self._segments:
            return
        if self._answer_task is None or self._answer_task.done():
            self._answer_task = asyncio.create_task(self._watch_answer_end())

    def _cancel_answer_watch(self) -> None:
        task = self._answer_task
        self._answer_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _watch_answer_end(self) -> None:
        """
        End the answer once the candidate has been silent long enough.

        The deadline is measured from the last recognised word and re-read
        every time it is reached, so speaking again simply pushes it out.
        """
        while not self._closed and self._segments:
            remaining = self._last_speech_at + self.answer_wait_s() - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            # Detach before emitting, so a result arriving mid-emit cannot
            # cancel the task after the segments were taken and lose the answer.
            if self._answer_task is asyncio.current_task():
                self._answer_task = None
            await self._emit_turn("timeout")
            return

    async def _emit_turn(self, boundary: str) -> None:
        """
        Emit exactly one `final_transcript` for the accumulated segments.

        Guarded by a lock and by the empty check: `speech_final` and
        `UtteranceEnd` can both arrive for the same utterance, and the second
        one must not produce a second answer.
        """
        self._cancel_answer_watch()
        async with self._emitting:
            if not self._segments:
                return
            text = " ".join(self._segments).strip()
            words = list(self._segment_words)
            confidence = (
                sum(self._segment_confidences) / len(self._segment_confidences)
                if self._segment_confidences
                else None
            )
            self._segments = []
            self._segment_words = []
            self._segment_confidences = []
            self._revision = 0
            self._last_partial_text = ""

            if not text:
                return

            t_audio_in = (words[-1].end_ms / 1000.0) if words else 0.0
            await self._bus.emit(
                FinalTranscript(
                    session_id=self._session_id,
                    turn_id=self._get_turn_id(),
                    producer=self._producer,
                    t_audio_in=t_audio_in,
                    text=text,
                    word_timings=words,
                    confidence=confidence,
                    boundary=boundary,  # type: ignore[arg-type]
                    # Deepgram reports real per-word start/end on finalised
                    # segments, so these are measured, not estimated.
                    timings_estimated=False,
                )
            )
            self.stats.turns_completed += 1
            self.stats.note_boundary(boundary)

    @property
    def keyterms(self) -> list[str]:
        return list(self._keyterms)

    @property
    def pending_segments(self) -> list[str]:
        return list(self._segments)
