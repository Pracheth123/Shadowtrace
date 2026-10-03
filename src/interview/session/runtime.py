"""
Live session runtime — Stage 4 speak path, Stage 5 guard loop.

Owns conversation history, opener/closer, transcript writer, and the interviewer
speak path. Speaks only through a SpeakPort callback so this module never imports
transport (contract 1). Transport and TTS are wired in server.py via the bus /
injected ports.

`pack_id=stage4-freeform` keeps the stage-4 freeform interviewer. Any other pack
id loads the stage-5 agent: tools, guard, verbatim spine.

Stage 11 adds two orthogonal switches:

  - `panel_mode` — 2-3 interviewer agents, one per pack panel role, all sharing
    the single `SessionTools` and therefore the single guard. The panel decides
    only who speaks; the guard still decides what may be asked. One voice at a
    time, because the speak path is serialised through `_gen_task` exactly as it
    was with one agent.
  - `lane` — "voice" or "text". The text lane is the same bus, agent, guard and
    evaluation with no audio; the injected SpeakPort is what differs.
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
    FallbackUsed,
    FinalTranscript,
    IntensityChange,
    RouteDecision,
    SessionComplete,
    Truncate,
)
from interview.packs.model import PackLoadError, load_pack
from interview.session.agent import LiveAgent, Outcome, phrase_probe, propose
from interview.session.guard import Intent
from interview.session.intensity import apply_tone, step_down, step_up, with_hint
from interview.session.interviewer import LeadInterviewer
from interview.session.panel import Panel
from interview.session.router import concession_line, draft_is_stale, route_answer
from interview.session.signals import ClaimHint, SignalExtractor, SignalReading
from interview.session.tools import SessionTools, load_claims_fixture
from interview.session.transcript import TranscriptWriter
from interview.session.turn_controller import TurnController

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

    async def synthesise(
        self,
        text: str,
        utterance_id: str,
        turn_id: str,
        voice: str = "",
    ) -> None: ...


OnWire = Callable[[dict], Awaitable[None]]


@dataclass
class PreparedDraft:
    basis: str
    outcome: Outcome
    plain: str
    concession: str
    claim_hits: list[str]
    # Who drafted it. The same voice speaks it, so a follow-up does not change
    # speaker between the draft and the line.
    persona: str | None = None


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
    # Stage 11. Panel mode needs a pack with a panel roster; without one the
    # session runs single-voice and `panel.enabled` stays False.
    panel_mode: bool = False
    lane: str = "voice"  # "voice" | "text"


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
        # persona → agent. One entry outside panel mode; all entries share one
        # SessionTools, so they share one guard.
        self._agents: dict[str, LiveAgent] = {}
        self._panel: Panel | None = None
        self._tools: SessionTools | None = None
        self._controller: TurnController | None = None
        self._extractor: SignalExtractor | None = None
        self._drafts: dict[str, PreparedDraft] = {}
        self._finished: set[str] = set()
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._spec_task: asyncio.Task | None = None
        self.valid_drafts = 0
        self.stale_drafts = 0
        self._accepted_turn: str | None = None
        self._dropped_intensity = False
        self._recovered_intensity = False
        self._steady_turns = 0
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
        panel = Panel.build(pack, enabled=self.config.panel_mode)
        self._panel = panel
        if self.config.panel_mode and not panel.enabled:
            log.warning(
                "Pack %s has no panel roster; running single-voice",
                self.config.pack_id,
            )
        # One agent per voice in the room. They are handed the *same* tools
        # object, which is what "several interviewer agents sharing one guard"
        # means concretely: there is one coverage list, one depth counter and
        # one claims scope for the whole panel.
        self._agents = {
            role.persona: LiveAgent(
                self.bus,
                self.session_id,
                tools,
                persona=panel.persona_for_log(role.persona),
                **kwargs,
            )
            for role in panel.roles
        }
        self._agent = self._agents[panel.lead.persona]
        self._extractor = SignalExtractor(
            claims=[ClaimHint(id=claim.id, text=claim.text) for claim in tools.claims]
        )
        self._controller = TurnController(
            self.bus,
            self.session_id,
            self._extractor,
            on_likely=self._speculate,
            on_barge=self._cancel_speculative,
            persona=self._current_persona,
        )

    # ------------------------------------------------------------------
    # Panel floor
    # ------------------------------------------------------------------

    def _current_persona(self) -> str | None:
        """
        The persona about to hold the floor, or None outside panel mode.

        Deterministic and idempotent for a given guard state, so the
        speculative loop, `likely_next`, `floor_granted` and the spoken line
        all name the same voice.
        """
        if self._panel is None or not self._panel.enabled or self._tools is None:
            return None
        # `propose` is the same pure function the agent uses, over the same
        # shared state, so asking it here costs nothing and cannot disagree
        # with what the agent is about to do.
        probing = propose(self._tools).action == "probe"
        role = self._panel.floor_for_state(self._tools.guard_state(), probing=probing)
        return self._panel.persona_for_log(role.persona)

    def _agent_for(self, persona: str | None) -> LiveAgent | None:
        """The panel member holding the floor; the lead outside panel mode."""
        if persona and persona in self._agents:
            return self._agents[persona]
        return self._agent

    def _voice_for(self, persona: str | None) -> str:
        if self._panel is None or not self._panel.enabled:
            return ""
        return self._panel.role(persona).voice

    async def note_fallback(self, kind: str, detail: str, turn_id: str | None = None) -> None:
        """
        Record that a degraded path was taken — stage 11.

        Logged rather than inferred so "each fallback exercised once" is read
        straight off the event log. `detail` is data, never an instruction.
        """
        await self.bus.emit(
            FallbackUsed(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="hardening",
                kind=kind,  # type: ignore[arg-type]
                detail=detail,
            )
        )

    def _turn_lock(self, turn_id: str) -> asyncio.Lock:
        lock = self._turn_locks.get(turn_id)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_locks[turn_id] = lock
        return lock

    async def start(self) -> None:
        self._started_at = time.monotonic()
        self._bind_pack()
        if self._controller is not None:
            self._controller.attach()
        if self.config.lane == "text":
            await self.note_fallback(
                "text_lane",
                "no audio lane: delivery is not assessed for this session",
            )
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
                lane="text" if self.config.lane == "text" else "voice",
                personas=self._panel.spoken_order if self._panel else [],
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
                    "lane": self.config.lane,
                    "personas": self._panel.spoken_order if self._panel else [],
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
        if self._extractor is not None:
            reading = self._extractor.update(event.text, looks_complete=True)
            await self._note_distress(event.turn_id or "", list(reading.distress_triggers))
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

        turn_id = event.turn_id or str(uuid.uuid4())
        self._accepted_turn = turn_id
        if self._agent is not None:
            async with self._turn_lock(turn_id):
                self._finished.add(turn_id)
                draft = self._drafts.pop(turn_id, None)
            if draft is not None:
                self._gen_task = asyncio.create_task(
                    self._speak_prepared(draft, event, turn_id)
                )
            else:
                self._gen_task = asyncio.create_task(self._generate_reply(turn_id))
        else:
            self._gen_task = asyncio.create_task(self._generate_reply(turn_id))
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
                persona = self._current_persona()
                agent = self._agent_for(persona) or self._agent
                outcome = await agent.run(
                    turn_id=turn_id,
                    turn_index=self.turn_count - 1,
                )
                if self._closed:
                    return
                if outcome.kind == "end":
                    self._end_after_turn = True
                    return
                await self._speak_question(
                    outcome.text, turn_id, utterance_id, persona=persona
                )
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

    async def _deliver(
        self,
        text: str,
        turn_id: str,
        utterance_id: str,
        persona: str | None = None,
    ) -> None:
        raw = text
        text, rude = apply_tone(raw)
        if rude and self.history and self.history[-1].get("role") == "assistant":
            if self.history[-1].get("content") == raw:
                self.history[-1]["content"] = text
            await self.bus.emit(
                AgentStep(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    producer="tone_guardrail",
                    step_index=0,
                    band="live",
                    phase="speak",
                    summary="tone guardrail rewrote the line",
                    persona=persona,
                )
            )
        t0 = time.monotonic() - self._started_at
        self.transcript.begin_agent(turn_id, utterance_id, text, t0, text.split())
        if self.on_wire:
            await self.on_wire(
                {
                    "type": "agent_utterance_start",
                    "utterance_id": utterance_id,
                    "turn_id": turn_id,
                    "text": text,
                    "persona": persona,
                    "speaker_label": (
                        self._panel.role(persona).label
                        if self._panel and self._panel.enabled
                        else None
                    ),
                }
            )
            await self.on_wire(
                {
                    "type": "caption",
                    "utterance_id": utterance_id,
                    "text": text,
                    "persona": persona,
                }
            )
        # One voice at a time: this await is the serialisation point, and
        # `_gen_task` is awaited per turn, so two personas cannot overlap.
        await self.speak.synthesise(
            text, utterance_id, turn_id, self._voice_for(persona)
        )
        t1 = time.monotonic() - self._started_at
        self.transcript.end_agent(utterance_id, t1)
        if self.on_wire:
            await self.on_wire(
                {"type": "agent_utterance_end", "utterance_id": utterance_id}
            )

    async def _speculate(self, turn_id: str, text: str, reading: SignalReading) -> None:
        """Tool loop while the candidate is still talking. Do not speak yet."""
        if self._agent is None or self._closed:
            return
        async with self._turn_lock(turn_id):
            if turn_id in self._finished or turn_id in self._drafts:
                return
            self._spec_task = asyncio.current_task()
            persona = self._current_persona()
            agent = self._agent_for(persona) or self._agent
            try:
                if self._accepted_turn == turn_id:
                    turn_index = max(0, self.turn_count - 1)
                else:
                    turn_index = self.turn_count
                outcome = await agent.run(
                    turn_id=turn_id,
                    turn_index=turn_index,
                    commit=False,
                )
            except asyncio.CancelledError:
                await self.bus.emit(
                    AgentStep(
                        session_id=self.session_id,
                        turn_id=turn_id,
                        producer="session_runtime",
                        step_index=0,
                        band="live",
                        phase="cancelled",
                        summary="barge-in cancelled speculative step",
                        cancelled=True,
                        persona=persona,
                    )
                )
                raise
            finally:
                self._spec_task = None
            if turn_id in self._finished:
                return
            question = outcome.text
            self._drafts[turn_id] = PreparedDraft(
                basis=text,
                outcome=outcome,
                plain=question,
                concession=concession_line(question) if question else "",
                claim_hits=list(reading.claim_hits),
                persona=persona,
            )

    async def _cancel_speculative(self, _turn_id: str) -> None:
        task = self._spec_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _speak_prepared(
        self,
        draft: PreparedDraft,
        event: FinalTranscript,
        turn_id: str,
    ) -> None:
        if self._agent is None:
            return
        route = route_answer(event.text)
        await self.bus.emit(
            RouteDecision(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="turn_controller",
                decision=route,
            )
        )
        # The voice that drafted the line is the voice that speaks it.
        persona = draft.persona
        agent = self._agent_for(persona) or self._agent
        stale = draft_is_stale(draft.basis, event.text)
        if stale:
            self.stale_drafts += 1
            await self.bus.emit(
                AgentStep(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    producer="live_agent",
                    step_index=0,
                    band="live",
                    phase="think",
                    summary="stale draft; one short refresh",
                    persona=persona,
                )
            )
            if draft.outcome.kind == "probe":
                text = phrase_probe(claim_text=None, transcript_anchor=event.text[:80])
            else:
                text = draft.plain
            if route == "conceded":
                text = concession_line(text)
            variant = "concession" if route == "conceded" else "plain"
        else:
            self.valid_drafts += 1
            variant = "concession" if route == "conceded" else "plain"
            text = draft.concession if variant == "concession" else draft.plain
        if draft.outcome.kind == "end" or not text:
            self._end_after_turn = True
            return
        await (agent or self._agent).commit_outcome(turn_id, draft.outcome.decision)
        await self._note_claim(turn_id, route, event.text, draft.claim_hits)
        utterance_id = str(uuid.uuid4())
        await self._speak_question(
            text, turn_id, utterance_id, variant=variant, persona=persona
        )

    async def _note_claim(
        self,
        turn_id: str,
        route: str,
        quote: str,
        claim_hits: list[str],
    ) -> None:
        if self._tools is None or not claim_hits or route == "unclear":
            return
        status = "held" if route == "defended" else "collapsed"
        await self._tools.call(
            self.bus,
            session_id=self.session_id,
            turn_id=turn_id,
            step_index=0,
            name="note_claim_status",
            args={"id": claim_hits[0], "status": status, "quote": quote[:160]},
        )

    async def _speak_question(
        self,
        text: str,
        turn_id: str,
        utterance_id: str,
        variant: str = "plain",
        persona: str | None = None,
    ) -> None:
        text = with_hint(self.config.intensity, text)
        self.history.append({"role": "assistant", "content": text})
        first = text.strip()
        if "." in first:
            first_sentence = first.split(".")[0].strip() + "."
        else:
            first_sentence = first
        spoken_variant = variant if variant == "concession" else "plain"
        await self.bus.emit(
            DraftReady(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="session_runtime",
                first_sentence=first_sentence,
                variant=spoken_variant,
                utterance_id=utterance_id,
                persona=persona,
            )
        )
        await self._deliver(text, turn_id, utterance_id, persona=persona)

    async def _note_distress(self, turn_id: str, triggers: list[str]) -> None:
        """One step down on distress, one step back after two calm turns."""
        if triggers:
            if self._dropped_intensity:
                return
            self._dropped_intensity = True
            self._steady_turns = 0
            new_level = step_down(self.config.intensity)
            await self._change_intensity(new_level, "down", ",".join(triggers), turn_id)
            return
        if not self._dropped_intensity or self._recovered_intensity:
            return
        self._steady_turns += 1
        if self._steady_turns < 2:
            return
        self._recovered_intensity = True
        await self._change_intensity(
            step_up(self.config.intensity),
            "up",
            "two steady turns",
            turn_id,
        )

    async def _change_intensity(self, new_level: str, direction: str, signal: str, turn_id: str) -> None:
        old = self.config.intensity
        if old == new_level:
            return
        self.config.intensity = new_level
        if self._tools is not None:
            self._tools.intensity = new_level
        self._intensity_history.append(
            {
                "from_level": old,
                "to_level": new_level,
                "direction": direction,
                "signal": signal,
                "turn_id": turn_id,
            }
        )
        await self.bus.emit(
            IntensityChange(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="confidence_guardrail",
                direction=direction,  # type: ignore[arg-type]
                signal=signal,
                from_level=old,  # type: ignore[arg-type]
                to_level=new_level,  # type: ignore[arg-type]
            )
        )

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
        # Opener and closer always come from the lead: they are the room
        # speaking, not one panel member's question.
        persona = (
            self._panel.persona_for_log(self._panel.lead.persona)
            if self._panel
            else None
        )
        self.history.append({"role": "assistant", "content": text})
        await self.bus.emit(
            DraftReady(
                session_id=self.session_id,
                turn_id=turn_id,
                producer="session_runtime",
                first_sentence=text.split(".")[0].strip() + ("." if "." in text else ""),
                variant="plain",
                utterance_id=utterance_id,
                persona=persona,
            )
        )
        await self._deliver(text, turn_id, utterance_id, persona=persona)

    async def _on_truncate(self, event: Truncate) -> None:
        self.transcript.truncate(
            event.utterance_id, event.last_heard_word, event.word_index
        )

    async def _on_barge_in(self, event) -> None:
        await self._cancel_speculative(getattr(event, "turn_id", "") or "")
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
