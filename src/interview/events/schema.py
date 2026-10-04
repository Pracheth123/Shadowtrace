"""
Event schemas for the AI mock interview platform — Stage 1 (+ agentic v2).

Every event carries: session_id, turn_id, seq, t_emit, producer, schema_version.
Audio-in events  carry: t_audio_in  (position in candidate audio stream, seconds).
Audio-out events carry: t_audio_out (position in agent audio stream, seconds).

turn_id is None only on session-scoped events (session_complete, and a
fallback_used that was taken outside any turn).
seq and t_emit are assigned by the bus if the producer leaves them unset;
replay passes them through untouched so the replayed log matches the fixture exactly.
t_emit is seconds since session start (not a raw monotonic value).

schema_version:
  1 — original vocabulary (fixtures/sessions/fake_session.jsonl)
  2 — additive agent events: agent_step, tool_call, tool_result, guard_override
      plus (stage 11, still additive) the fallback_used event, AgentStep.persona,
      and SessionComplete.lane / .personas — all defaulted, so a v1 or an
      early-v2 log parses and replays field-for-field unchanged.
New producers default to 2. v1 logs must still parse and replay byte-identically.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Nested types
# ---------------------------------------------------------------------------


class WordTiming(BaseModel):
    """Word-level timing entry in a final transcript."""
    word: str
    start_ms: int
    end_ms: int


class WordTimestamp(BaseModel):
    """Per-word offset within a single TTS chunk."""
    word: str
    offset_ms: int  # ms from the start of this chunk


class PauseStats(BaseModel):
    """Pause statistics computed over one candidate turn."""
    count: int      # number of distinct pauses
    max_ms: int     # longest single pause in ms
    total_ms: int   # cumulative pause time in ms


class DistressSignal(BaseModel):
    """
    Distress signal detected during a candidate turn.
    score is a float in [0, 1] so the guardrail can be tuned against a
    false-positive target.  A boolean cannot be tuned.
    triggers lists the named detectors that contributed (e.g. 'long_silence',
    'repeated_filler', 'low_confidence_cluster').
    """
    score: float
    triggers: list[str]


# ---------------------------------------------------------------------------
# Common envelope (all events inherit from this)
# ---------------------------------------------------------------------------


class EventBase(BaseModel):
    """Fields present on every event."""
    schema_version: int = 2
    session_id: str
    # turn_id is None only for session-scoped events (session_complete, and a
    # fallback_used taken outside any turn).
    turn_id: str | None
    # seq and t_emit may be left unset by the producer; the bus fills them in.
    # Replay passes both through untouched so replayed logs match the fixture.
    seq: int | None = None
    t_emit: float | None = None  # seconds since session start
    producer: str


# ---------------------------------------------------------------------------
# Individual event types
# ---------------------------------------------------------------------------


class SpeechStart(EventBase):
    """Candidate began speaking; this event assigns turn_id for the whole turn."""
    type: Literal["speech_start"] = "speech_start"
    t_audio_in: float  # position in candidate audio stream (seconds)


class Partial(EventBase):
    """Incremental STT hypothesis, may be revised."""
    type: Literal["partial"] = "partial"
    t_audio_in: float
    text: str
    stable_until_ms: int  # ms into the stream up to which text is stable
    revision: int          # increments each time a previous partial is revised
    # Provider confidence for this hypothesis, when the provider reports one.
    # None means "not reported", which is different from "zero confidence".
    confidence: float | None = None


class Endpoint(EventBase):
    """VAD/STT detected end of candidate speech."""
    type: Literal["endpoint"] = "endpoint"
    t_audio_in: float
    confidence: float  # [0, 1] endpoint confidence


class FinalTranscript(EventBase):
    """Committed, word-timed transcript for the completed turn."""
    type: Literal["final_transcript"] = "final_transcript"
    t_audio_in: float
    text: str
    word_timings: list[WordTiming]
    confidence: float | None = None
    # Which signal closed this turn. `speech_final` and `utterance_end` come
    # from provider endpointing; `client` is an explicit end-of-answer from the
    # UI (the text lane, or push-to-talk release); `timeout` is our own guard.
    # Recorded because "the provider thought you stopped" and "you pressed
    # send" are different claims about the same transcript.
    boundary: Literal["speech_final", "utterance_end", "client", "timeout"] | None = (
        None
    )
    # True when word_timings were derived rather than reported by the provider.
    # Truncation accuracy depends on this, so it is not left to inference.
    timings_estimated: bool = False


class Signals(EventBase):
    """
    Behavioural signals extracted from the candidate's turn.
    Feeds the live agent / guard only — never reaches the scorer (contract 8).
    """
    type: Literal["signals"] = "signals"
    claim_hits: list[str]       # claim IDs/labels from the Claims File (stage 6)
    pause_stats: PauseStats
    silence_ms: int             # total silence in the turn in ms
    distress: DistressSignal | None  # None when no distress detected


class LikelyNext(EventBase):
    """
    Speculative trigger: candidate speech looks complete enough to start the
    agent loop early. signal_snapshot is dict[str, Any] for now; stage 7
    replaces it with a typed SignalModel once the signal bus shape is fixed.
    """
    type: Literal["likely_next"] = "likely_next"
    signal_snapshot: dict[str, Any]
    persona: str | None = None  # None outside panel mode (stage 11)


class AgentStep(EventBase):
    """
    One reasoning/act step of an agent (live interviewer, indexer, evaluator, …).
    Replay re-emits these without calling a model (contract 5).
    """
    type: Literal["agent_step"] = "agent_step"
    step_index: int
    band: Literal["live", "intake", "eval", "roadmap"]
    phase: Literal["observe", "think", "act", "speak", "cancelled"]
    summary: str
    # Reason: barge-in / hard step-limit cancel must be visible in the log.
    cancelled: bool = False
    # Which panel interviewer took this step. None outside panel mode (stage 11).
    persona: str | None = None


class ToolCall(EventBase):
    """Agent invoked a local tool. args are data only (contract 7)."""
    type: Literal["tool_call"] = "tool_call"
    step_index: int
    tool: str
    args: dict[str, Any]


class ToolResult(EventBase):
    """
    Result of a tool call. On replay, this recorded payload is re-emitted
    so downstream layers run without the tool implementation live.
    """
    type: Literal["tool_result"] = "tool_result"
    step_index: int
    tool: str
    ok: bool
    result: dict[str, Any]
    latency_ms: float


class GuardOverride(EventBase):
    """
    The deterministic guard rejected or rewrote an agent intent.
    Every override must be logged (no silent coercion).
    """
    type: Literal["guard_override"] = "guard_override"
    step_index: int
    rule: Literal[
        "spine_order",
        "spine_verbatim",
        "time_budget",
        "probe_depth",
        "claims_scope",
        "question_repeat",
        "step_limit",
    ]
    agent_intent: str
    enforced_action: str


class DraftReady(EventBase):
    """First sentence of the speculative agent response is ready."""
    type: Literal["draft_ready"] = "draft_ready"
    first_sentence: str
    variant: Literal["plain", "concession"]
    utterance_id: str           # unique within the session; ties to tts_chunk / truncate
    persona: str | None = None  # None outside panel mode (stage 11)


class FloorGranted(EventBase):
    """The turn controller grants the floor to the agent."""
    type: Literal["floor_granted"] = "floor_granted"
    persona: str | None = None  # None outside panel mode (stage 11)


class RouteDecision(EventBase):
    """Turn controller verdict on how the candidate handled the probe."""
    type: Literal["route_decision"] = "route_decision"
    decision: Literal["defended", "conceded", "unclear"]


class TtsChunk(EventBase):
    """One audio chunk emitted by the TTS layer."""
    type: Literal["tts_chunk"] = "tts_chunk"
    t_audio_out: float          # position in agent audio stream (seconds)
    audio_ref: str              # pointer to audio data; never inline bytes
    word_timestamps: list[WordTimestamp]
    utterance_id: str           # matches the draft_ready that spawned this utterance
    # The browser cannot decode PCM without these. Defaults match the stage-3
    # mock (16 kHz linear16) so pre-stage-12 logs stay valid; a real provider
    # chunk carries whatever it actually produced.
    sample_rate: int = 16000
    encoding: str = "linear16"
    # Deepgram TTS does not document word alignment, so word_timestamps on a
    # real chunk are estimated from a speaking rate. Truncation is therefore
    # word-approximate, and this flag is what says so in the log.
    timings_estimated: bool = False


class PlaybackAck(EventBase):
    """Client acknowledgement that audio has been played up to played_ms."""
    type: Literal["playback_ack"] = "playback_ack"
    t_audio_out: float
    played_ms: int              # ms of agent audio confirmed AUDIBLE, from the
                                # browser audio clock — not merely received or
                                # scheduled. See docs/decisions/stage12.
    utterance_id: str
    # Total ms handed to the audio device for this utterance. played_ms <=
    # scheduled_ms always; the gap is audio queued but not yet heard, which is
    # exactly what a barge-in must discard.
    scheduled_ms: int | None = None


class BargeIn(EventBase):
    """Candidate interrupted agent playback."""
    type: Literal["barge_in"] = "barge_in"
    t_audio_in: float
    confidence: float           # [0, 1] barge-in confidence


class Truncate(EventBase):
    """
    Instructs the TTS/playback layer to truncate the current utterance.
    last_heard_word is the last word the client acknowledged playing.
    word_index indexes word_timestamps in the tts_chunk for utterance_id —
    NOT the candidate's transcript.  This is the truncation contract: the
    transcript records exactly what was heard.
    """
    type: Literal["truncate"] = "truncate"
    t_audio_out: float
    last_heard_word: str
    word_index: int             # index into tts_chunk.word_timestamps for utterance_id
    utterance_id: str


class QuestionPlanned(EventBase):
    """
    Guard-approved next question (spine or probe).
    Emitted after the agent proposes a move and the guard accepts (or overrides).
    Kept for waterfall compatibility; the agent decides, the guard enforces.
    """
    type: Literal["question_planned"] = "question_planned"
    kind: Literal["spine", "probe"]
    competency: str
    target_depth: int           # follow-up depth level (0 = top level, 1+ = nested)


class CoverageUpdate(EventBase):
    """Planner reports current coverage state."""
    type: Literal["coverage_update"] = "coverage_update"
    covered: list[str]          # competency/claim IDs marked done
    outstanding: list[str]      # still to address


class IntensityChange(EventBase):
    """Session intensity level changed."""
    type: Literal["intensity_change"] = "intensity_change"
    direction: Literal["up", "down"]
    signal: str                  # human-readable reason for the change
    from_level: Literal["coach", "realistic", "panel"]
    to_level: Literal["coach", "realistic", "panel"]


class SessionComplete(EventBase):
    """
    Session has ended.  This is the only session-scoped event; turn_id is None.
    """
    type: Literal["session_complete"] = "session_complete"
    turn_id: None = None         # explicitly None — session-scoped, no turn
    transcript_path: str
    log_path: str
    pack_id: str
    intensity_history: list[dict[str, Any]]
    # Stage 11. The text lane runs the same bus, agent, guard and evaluation;
    # only delivery is not assessed. "voice" keeps every pre-stage-11 log valid.
    lane: Literal["voice", "text"] = "voice"
    # Personas that held the floor, in first-speak order. Empty outside panel mode.
    personas: list[str] = Field(default_factory=list)
    # Stage 15, additive. Per-round coverage from the coordinator (empty for a
    # single-pack session) and why the session ended: "complete" (every round
    # finished), "limit" (time or turn cap), "client" (the candidate ended it),
    # "disconnect", "agent". Recorded so a report can say honestly that an
    # interview ended early instead of implying full coverage.
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    ended_reason: str = ""


class RoundTransition(EventBase):
    """
    One sequential round handed over to the next — stage 15.

    Session-scoped like session_complete. Carries the coordinator's coverage for
    the round that ended and the *size* of the handoff (statements and open
    questions), never its content's judgement: ratings do not cross rounds.
    """
    type: Literal["round_transition"] = "round_transition"
    turn_id: None = None
    from_round: str
    to_round: str
    reason: Literal["coverage", "time"]
    spine_covered: int
    spine_total: int
    handoff_statements: int
    handoff_unresolved: int


class FallbackUsed(EventBase):
    """
    A degraded path was taken instead of the primary one — stage 11.

    Logged so the hardening exit criterion is read off the event log rather
    than inferred from a summary string. `detail` is data, never an instruction.
    """
    type: Literal["fallback_used"] = "fallback_used"
    kind: Literal[
        "provider_failover",
        "push_to_talk",
        "resume_only",
        "fallback_repo",
        "text_lane",
        "ws_reconnect",
        "voice_to_text",
    ]
    detail: str


class ModelCall(EventBase):
    """
    One Groq (or real-model) API call. Per-session usage is the max call_index
    (or count of these events) in the log.
    """
    type: Literal["model_call"] = "model_call"
    role: str                   # live_interviewer | indexer | evaluator | roadmap
    model: str
    call_index: int             # 1-based session counter
    turn_call_index: int        # 1-based within turn (or session bucket)
    latency_ms: float
    ok: bool
    status_code: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

Event = Annotated[
    Union[
        SpeechStart,
        Partial,
        Endpoint,
        FinalTranscript,
        Signals,
        LikelyNext,
        AgentStep,
        ToolCall,
        ToolResult,
        GuardOverride,
        DraftReady,
        FloorGranted,
        RouteDecision,
        TtsChunk,
        PlaybackAck,
        BargeIn,
        Truncate,
        QuestionPlanned,
        CoverageUpdate,
        IntensityChange,
        SessionComplete,
        FallbackUsed,
        ModelCall,
        RoundTransition,
    ],
    Field(discriminator="type"),
]
