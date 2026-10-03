"""
Live session runtime — Stage 4 speak path, Stage 5 guard loop.

Owns conversation history, opener/closer, transcript writer, and the interviewer
speak path. Speaks only through a SpeakPort callback so this module never imports
transport (contract 1). Transport and TTS are wired in server.py via the bus /
injected ports.

`pack_id=stage4-freeform` keeps the stage-4 freeform interviewer. Any other pack
id loads the stage-5 agent: tools, guard, verbatim spine.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from interview.events.schema import (
    AgentStep,
    DraftReady,
    FinalTranscript,
    SessionComplete,
    Truncate,
)
from interview.packs.model import PackLoadError, load_pack
from interview.session.agent import LiveAgent
from interview.session.guard import Intent
from interview.session.interviewer import LeadInterviewer
from interview.session.tools import SessionTools, load_claims_fixture
from interview.session.transcript import TranscriptWriter

if TYPE_CHECKING:
    from interview.events.bus import EventBus

log = logging.getLogger(__name__)

OPENER = (
    "Thanks for joining. Let's start with a technical deep-dive — "
    "tell me about a challenging project you've led recently."
)
CLOSER = (
    "That's all the time we have. Thanks for walking me through your work — "
    "you'll get a feedback report shortly."
)


class SpeakPort(Protocol):
    """Injected by the composition root; typically wraps FakeTts or TtsAdapter."""

    async def synthesise(self, text: str, utterance_id: str, turn_id: str) -> None: ...


OnWire = Callable[[dict], Awaitable[None]]


@dataclass
class SessionConfig:
    max_turns: int = 6
    max_minutes: float = 12.0
    pack_id: str = "stage4-freeform"
    intensity: str = "realistic"
    use_mock_llm: bool = True  # tests / replay: True. Live: False → Groq
    # Turn index → intent the agent proposes instead of the default policy.
    # The guard still rules. Used to record overrides; production leaves this empty.
    scripted_intents: dict[int, Intent] | None = None
    max_tool_calls: int | None = None
    # Stage 6 claims.json. Unset keeps the stage-5 fixture.
    claims_path: str | None = None


@dataclass
class LiveSession:
    bus: "EventBus"
    session_id: str
    log_path: str
    transcript_path: str
    speak: SpeakPort
    config: SessionConfig = field(default_factory=SessionConfig)
    on_wire: OnWire | None = None

    def __post_init__(self) -> None:
        self.history: list[dict] = []
        self.transcript = TranscriptWriter()
        self.turn_count = 0
        self._started_at = time.monotonic()
        self._intensity_history: list[dict] = []
        self._gen_task: asyncio.Task | None = None
        self._closed = False
        self._end_after_turn = False
        self._agent: LiveAgent | None = None
        self._tools: SessionTools | None = None
        self._model_client = None
        if not self.config.use_mock_llm:
            from interview.llm.client import GroqModelClient

            self._model_client = GroqModelClient(
                bus=self.bus, session_id=self.session_id
            )

    def attach(self) -> None:
        self.bus.subscribe("final_transcript", self._on_final)
        self.bus.subscribe("truncate", self._on_truncate)
        self.bus.subscribe("barge_in", self._on_barge_in)
        self.bus.subscribe("signals", self._on_signals)

    def _bind_pack(self) -> None:
        """Load a stage-5 pack. stage4-freeform keeps the freeform interviewer."""
        self._agent = None
        self._tools = None
        if self.config.pack_id in ("", "stage4-freeform"):
            return
        try:
            pack = load_pack(self.config.pack_id)
        except PackLoadError as exc:
            log.warning("Pack %s unavailable (%s); using freeform interviewer", self.config.pack_id, exc)
            return
        claims_file = (
            Path(self.config.claims_path) if self.config.claims_path else None
        )
        tools = SessionTools(
            pack,
            load_claims_fixture(claims_file),
            intensity=self.config.intensity,
        )
        kwargs: dict = {
            "scripted_intents": self.config.scripted_intents,
        }
        if self.config.max_tool_calls is not None:
            kwargs["max_tool_calls"] = self.config.max_tool_calls
        self._tools = tools
        self._agent = LiveAgent(self.bus, self.session_id, tools, **kwargs)

    async def start(self) -> None:
        self._started_at = time.monotonic()
        self._bind_pack()
        await self._speak_fixed(OPENER, turn_id="turn-opener")

    async def end(self, reason: str = "client") -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_generation("session_end")
        if reason != "closer_already_spoken":
            await self._speak_fixed(CLOSER, turn_id="turn-closer")
        Path(self.transcript_path).parent.mkdir(parents=True, exist_ok=True)
        self.transcript.write(Path(self.transcript_path))
        await self.bus.emit(
            SessionComplete(
                session_id=self.session_id,
                turn_id=None,
                producer="session_runtime",
                transcript_path=self.transcript_path,
                log_path=self.log_path,
                pack_id=self.config.pack_id,
                intensity_history=self._intensity_history,
            )
        )
        if self.on_wire:
            await self.on_wire(
                {
                    "type": "session_complete",
                    "transcript_path": self.transcript_path,
                    "log_path": self.log_path,
                    "pack_id": self.config.pack_id,
                    "intensity_history": self._intensity_history,
                }
            )

    async def _on_final(self, event: FinalTranscript) -> None:
        if self._closed:
            return
        self.turn_count += 1
        self.history.append({"role": "user", "content": event.text})
        self.transcript.add_candidate(
            turn_id=event.turn_id or "",
            text=event.text,
            t_start=float(event.t_emit or 0.0),
            t_end=float(event.t_audio_in),
        )
        if self._tools is not None:
            self._tools.add_transcript(event.text)
        if self.on_wire:
            await self.on_wire({"type": "turn_end", "turn_id": event.turn_id})

        if self._should_close():
            await self.end(reason="limit")
            return

        # Await prior reply so FakeStt / rapid finals cannot overlap generations.
        if self._gen_task and not self._gen_task.done():
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass

        self._gen_task = asyncio.create_task(
            self._generate_reply(event.turn_id or str(uuid.uuid4()))
        )
        # Reason: bus handlers are awaited; finishing the reply before the next
        # final_transcript keeps turn timelines waterfall-clean. Barge-in still
        # cancels via _gen_task.cancel().
        try:
            await self._gen_task
        except asyncio.CancelledError:
            pass
        if self._end_after_turn and not self._closed:
            await self.end(reason="agent")

    async def _generate_reply(self, turn_id: str) -> None:
        utterance_id = str(uuid.uuid4())
        try:
            if self._agent is not None:
                outcome = await self._agent.run(
                    turn_id=turn_id,
                    turn_index=self.turn_count - 1,
                )
                if self._closed:
                    return
                if outcome.kind == "end":
                    self._end_after_turn = True
                    return
                await self._speak_question(outcome.text, turn_id, utterance_id)
                return

            iv = LeadInterviewer(
                self.bus,
                self.session_id,
                turn_id,
                utterance_id,
                model_client=self._model_client,
                provider="mock" if self.config.use_mock_llm else "groq",
            )
            full = (await iv.generate(list(self.history))).strip()
            if self._closed:
                return
            self.history.append({"role": "assistant", "content": full})
            await self._deliver(full, turn_id, utterance_id)
        except asyncio.CancelledError:
            await self.bus.emit(
                AgentStep(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    producer="session_runtime",
                    step_index=0,
                    band="live",
                    phase="cancelled",
                    summary="barge-in or end cancelled generation",
                    cancelled=True,
                )
            )
            raise

    async def _deliver(self, text: str, turn_id: str, utterance_id: str) -> None:
        t0 = time.monotonic() - self._started_at
        self.transcript.begin_agent(turn_id, utterance_id, text, t0, text.split())
        if self.on_wire:
            await self.on_wire(
                {
                    "type": "agent_utterance_start",
                    "utterance_id": utterance_id,
                    "turn_id": turn_id,
                    "text": text,
                }
            )
            await self.on_wire(
                {"type": "caption", "utterance_id": utterance_id, "text": text}
            )
        await self.speak.synthesise(text, utterance_id, turn_id)
        t1 = time.monotonic() - self._started_at
        self.transcript.end_agent(utterance_id, t1)
        if self.on_wire:
            await self.on_wire(
                {"type": "agent_utterance_end", "utterance_id": utterance_id}
            )

    async def _speak_question(self, text: str, turn_id: str, utterance_id: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        first = text.strip()
        if "." in first:
            first_sentence = first.split(".")[0].strip() + "."
        else:
            first_sentence = first
        await self.bus.emit(
            DraftReady(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="session_runtime",
                first_sentence=first_sentence,
                variant="plain",
                utterance_id=utterance_id,
            )
        )
        await self._deliver(text, turn_id, utterance_id)

    async def _on_signals(self, event) -> None:
        if self._tools is None:
            return
        distress = event.distress.model_dump() if event.distress else None
        self._tools.note_signals(
            {
                "claim_hits": list(event.claim_hits),
                "pause_stats": event.pause_stats.model_dump(),
                "silence_ms": event.silence_ms,
                "distress": distress,
            }
        )

    async def _speak_fixed(self, text: str, *, turn_id: str) -> None:
        utterance_id = str(uuid.uuid4())
        self.history.append({"role": "assistant", "content": text})
        await self.bus.emit(
            DraftReady(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="session_runtime",
                first_sentence=text.split(".")[0].strip() + ("." if "." in text else ""),
                variant="plain",
                utterance_id=utterance_id,
            )
        )
        await self._deliver(text, turn_id, utterance_id)

    async def _on_truncate(self, event: Truncate) -> None:
        self.transcript.truncate(
            event.utterance_id, event.last_heard_word, event.word_index
        )

    async def _on_barge_in(self, _event) -> None:
        await self._cancel_generation("barge_in")

    async def _cancel_generation(self, reason: str) -> None:
        task = self._gen_task
        self._gen_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            log.info("Cancelled generation (%s)", reason)

    def _should_close(self) -> bool:
        elapsed_min = (time.monotonic() - self._started_at) / 60.0
        return (
            self.turn_count >= self.config.max_turns
            or elapsed_min >= self.config.max_minutes
        )

    async def wait_idle(self) -> None:
        """Wait for in-flight generation + bus subscribers (tests / shutdown)."""
        task = self._gen_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.bus.drain()

    async def ingest_final(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        t_audio_in: float | None = None,
    ) -> None:
        """Test / mock helper: inject endpoint + final_transcript onto the bus."""
        from interview.events.schema import Endpoint, FinalTranscript, Partial

        tid = turn_id or str(uuid.uuid4())
        t = t_audio_in if t_audio_in is not None else (time.monotonic() - self._started_at)
        # Minimal partial so waterfall has last_partial → endpoint
        await self.bus.emit(
            Partial(
                session_id=self.session_id,
                turn_id=tid,
                producer="runtime_ingest",
                t_audio_in=max(0.0, t - 0.15),
                text=text,
                stable_until_ms=int(max(0.0, t - 0.15) * 1000),
                revision=0,
            )
        )
        await self.bus.emit(
            Endpoint(
                session_id=self.session_id,
                turn_id=tid,
                producer="runtime_ingest",
                t_audio_in=t,
                confidence=0.9,
            )
        )
        await self.bus.emit(
            FinalTranscript(
                session_id=self.session_id,
                turn_id=tid,
                producer="runtime_ingest",
                t_audio_in=t,
                text=text,
                word_timings=[],
            )
        )
        await self.wait_idle()
