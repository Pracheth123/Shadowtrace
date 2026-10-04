import { useCallback, useEffect, useRef, useState } from "react";

import {
  MicrophoneDenied,
  isCaptureSupported,
  startCapture,
  type CaptureHandle,
} from "@/lib/audio-capture";
import { PlaybackQueue, type ChunkMeta } from "@/lib/audio-playback";
import { currentToken, ensureGuest } from "@/lib/api";

/**
 * The live session socket, lifted out of the old single-file App.
 *
 * This is the working protocol the backend already speaks, moved behind a hook
 * so three pages can share it. Behaviour is deliberately unchanged:
 *
 *   - the server hands back a single-use `resume_token`; a dropped socket
 *     reconnects with it and continues the *same* session on the same log;
 *   - `playback_ack` is polled only in the voice lane, because the text lane
 *     emits no audio and so has nothing to acknowledge;
 *   - `barge_in` carries `played_ms`, which is what lets the server truncate
 *     the transcript at the last word actually heard;
 *   - while push-to-talk is on, a barge-in reports whether the key was held,
 *     so the server can ignore room noise.
 *
 * Env contract is untouched (`VITE_WS_HOST`): switching to `VITE_API_URL` is
 * part of the separate, still-unapproved deployment task.
 *
 * Stage 14 connects the voice path that previously did not exist:
 *
 *   - one microphone, opened on the Start gesture, shared with the waveform,
 *     converted to PCM16 by an AudioWorklet and streamed as binary frames;
 *   - synthesised speech arriving as an `audio_chunk` metadata frame followed
 *     by its binary frame, decoded at the rate the metadata declares;
 *   - `playback_ack` driven by the browser audio clock rather than by arrival,
 *     because that value is where the transcript gets truncated on barge-in;
 *   - barge-in stopping local playback as well as provider synthesis.
 */

const WS_HOST = import.meta.env.VITE_WS_HOST || `${location.hostname}:8000`;
const WS_URL = `${location.protocol === "https:" ? "wss://" : "ws://"}${WS_HOST}/ws/session`;

/** How long the backend may take to wake before we say so on screen. */
const WAKING_AFTER_MS = 2500;
const MAX_RECONNECTS = 5;

export type Lane = "voice" | "text";
export type Intensity = "coach" | "realistic" | "panel";

export type SessionStatus =
  | "idle"
  | "connecting"
  | "waking"
  | "ready"
  | "resumed"
  | "reconnecting"
  | "complete"
  | "rejected"
  | "unreachable";

export type Utterance = {
  utteranceId: string;
  text: string;
  speaker: string;
  persona: string | null;
};

/** A session always starts from a completed intake; the server holds the rest. */
export type SessionOptions = {
  intakeId: string;
  lane: Lane;
  intensity: Intensity;
};

/** Server-reported progress. Core (spine) questions and follow-ups are separate. */
export type SessionProgress = {
  mode: "rounds" | "single";
  round: string | null;
  round_label: string;
  round_index: number;
  round_count: number;
  core_asked: number;
  core_total: number;
  follow_ups: number;
  answers: number;
  intensity: string;
  round_seconds_remaining: number;
  rounds: { round: string; label: string; state: "done" | "active" | "pending"; core_asked: number; core_total: number }[];
};

export type RoundNotice = { fromLabel: string; toLabel: string; carried: number; open: number };

export type LogLine = { type: string; detail: string };

export function useSession() {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [statusDetail, setStatusDetail] = useState("");
  const [running, setRunning] = useState(false);
  const [utterance, setUtterance] = useState<Utterance | null>(null);
  const [turnCount, setTurnCount] = useState(0);
  const [pushToTalk, setPushToTalk] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [lane, setLane] = useState<Lane>("voice");
  // Live interim transcript, so the candidate can see they are being heard.
  const [interim, setInterim] = useState("");
  const [micActive, setMicActive] = useState(false);
  const [muted, setMuted] = useState(false);
  const [micStream, setMicStream] = useState<MediaStream | null>(null);
  const [progress, setProgress] = useState<SessionProgress | null>(null);
  const [roundNotice, setRoundNotice] = useState<RoundNotice | null>(null);
  const [providerWarning, setProviderWarning] = useState("");
  // A voice problem the candidate can recover from by switching to text.
  const [voiceProblem, setVoiceProblem] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [intensity, setIntensity] = useState<Intensity>("realistic");
  const [interviewer, setInterviewer] = useState("");
  const [voiceInfo, setVoiceInfo] = useState<{
    enabled: boolean;
    provider: string;
    targetSampleRate: number;
  } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const wakingTimerRef = useRef<number | null>(null);
  const resumeTokenRef = useRef("");
  const endedRef = useRef(false);
  const triesRef = useRef(0);
  const utteranceIdRef = useRef("");
  const laneRef = useRef<Lane>("voice");
  const captureRef = useRef<CaptureHandle | null>(null);
  const playbackRef = useRef<PlaybackQueue | null>(null);
  // The metadata frame that arrived immediately before the next binary frame.
  // A WebSocket preserves send order, so this pairing is unambiguous.
  const pendingMetaRef = useRef<ChunkMeta | null>(null);
  const bargedRef = useRef(false);

  const addLog = useCallback((type: string, detail: string) => {
    setLogs((previous) => [...previous.slice(-60), { type, detail }]);
  }, []);
  const addLogRef = useRef<typeof addLog | null>(null);
  addLogRef.current = addLog;

  const send = useCallback((payload: object) => {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }, []);

  /** Lazily build the playback queue; its acks come from the audio clock. */
  const playback = useCallback((): PlaybackQueue => {
    if (!playbackRef.current) {
      playbackRef.current = new PlaybackQueue({
        onProgress: (progress) => {
          send({
            type: "playback_ack",
            utterance_id: progress.utteranceId,
            played_ms: progress.playedMs,
            scheduled_ms: progress.scheduledMs,
          });
        },
        onError: (message) => addLogRef.current?.("playback_error", message),
      });
    }
    return playbackRef.current;
  }, [send]);

  const clearWaking = useCallback(() => {
    if (wakingTimerRef.current != null) {
      window.clearTimeout(wakingTimerRef.current);
      wakingTimerRef.current = null;
    }
  }, []);

  /** Open the microphone and start streaming frames. User-gesture only. */
  const beginCapture = useCallback(async () => {
    if (captureRef.current) return;
    const handle = await startCapture({
      onFrame: (frame) => {
        const socket = wsRef.current;
        if (socket && socket.readyState === WebSocket.OPEN) {
          // Binary frame: raw PCM16 at the device rate. The server converts
          // and declares the result to the provider.
          socket.send(frame);
        }
      },
      onError: (message) => addLog("audio_error", message),
    });
    captureRef.current = handle;
    setMicStream(handle.stream);
    setMicActive(true);
    // Tell the server the real device rate rather than letting it assume.
    send({
      type: "audio_start",
      sample_rate: handle.sampleRate,
      channels: handle.channels,
    });
    addLog("audio_start", `mic ${handle.sampleRate} Hz, ${handle.channels} ch`);
  }, [addLog, send]);

  const endCapture = useCallback(async () => {
    const handle = captureRef.current;
    captureRef.current = null;
    setMicActive(false);
    setMicStream(null);
    if (handle) await handle.stop();
  }, []);

  const attach = useCallback(
    (socket: WebSocket, opening: object) => {
      socket.binaryType = "arraybuffer";
      wsRef.current = socket;

      // If the host is cold-starting, the socket sits in CONNECTING for a
      // while. Say so rather than showing a dead button.
      clearWaking();
      wakingTimerRef.current = window.setTimeout(() => {
        if (socket.readyState === WebSocket.CONNECTING) setStatus("waking");
      }, WAKING_AFTER_MS);

      socket.onopen = () => {
        clearWaking();
        socket.send(JSON.stringify(opening));
      };

      socket.onmessage = async (event) => {
        if (typeof event.data !== "string") {
          // Synthesised audio. Its format came in the metadata frame sent
          // immediately before it; without that we would be guessing the rate,
          // which is exactly the bug that made the interviewer sound slow.
          const meta = pendingMetaRef.current;
          pendingMetaRef.current = null;
          if (!meta) {
            addLog("audio_orphan", "audio frame arrived with no metadata");
            return;
          }
          playback().enqueue(meta, event.data as ArrayBuffer);
          return;
        }
        const message = JSON.parse(event.data);
        addLog(message.type, JSON.stringify(message));

        if (message.type === "session_rejected") {
          setStatus("rejected");
          setStatusDetail(message.reason ?? "");
          setRunning(false);
          void endCapture();
          return;
        }

        if (message.type === "session_ready") {
          resumeTokenRef.current = message.resume_token ?? "";
          triesRef.current = 0;
          if (message.session_id) setSessionId(String(message.session_id));
          if (message.intensity) setIntensity(message.intensity);
          if (message.interviewer) setInterviewer(String(message.interviewer));
          if (message.lane) {
            setLane(message.lane);
            laneRef.current = message.lane;
          }
          if (message.voice) {
            setVoiceInfo({
              enabled: Boolean(message.voice.enabled),
              provider: String(message.voice.provider ?? "mock"),
              targetSampleRate: Number(message.voice.target_sample_rate ?? 16000),
            });
          }
          if (message.degraded) addLog("degraded", String(message.degraded));
          setStatus(message.resumed ? "resumed" : "ready");
          setStatusDetail("");
          // The microphone opens only once the server has confirmed a voice
          // session, so a text-lane or rejected session never prompts for it.
          if (laneRef.current === "voice" && !captureRef.current) {
            try {
              await beginCapture();
            } catch (error) {
              const denied = error as MicrophoneDenied;
              addLog("mic_denied", denied.message);
              setStatus("unreachable");
              setStatusDetail(
                `${denied.message} ${denied.recovery ?? ""}`.trim(),
              );
            }
          }
          return;
        }

        if (message.type === "progress") {
          setProgress(message as SessionProgress);
          return;
        }

        if (message.type === "round_transition") {
          setRoundNotice({
            fromLabel: String(message.from_label ?? ""),
            toLabel: String(message.to_label ?? ""),
            carried: Number(message.carried_statements ?? 0),
            open: Number(message.carried_open_questions ?? 0),
          });
          return;
        }

        if (message.type === "provider_warning") {
          setProviderWarning(String(message.detail ?? ""));
          return;
        }

        if (message.type === "lane_changed") {
          setLane(message.lane);
          laneRef.current = message.lane;
          setVoiceProblem("");
          playbackRef.current?.stopAll();
          void endCapture();
          return;
        }

        if (message.type === "turn_end") {
          setTurnCount((count) => count + 1);
          setInterim("");
          return;
        }

        // Live transcription, so the candidate can see they are being heard.
        if (message.type === "partial") {
          setInterim(String(message.text ?? ""));
          return;
        }

        if (message.type === "audio_chunk") {
          pendingMetaRef.current = {
            utteranceId: String(message.utterance_id ?? ""),
            encoding: String(message.encoding ?? "linear16"),
            sampleRate: Number(message.sample_rate ?? 24000),
            seq: Number(message.seq ?? 0),
            bytes: Number(message.bytes ?? 0),
          };
          return;
        }

        if (message.type === "utterance_begin") {
          bargedRef.current = false;
          utteranceIdRef.current = String(message.utterance_id ?? "");
          playback().beginUtterance(utteranceIdRef.current);
          return;
        }

        if (message.type === "utterance_end") {
          // Synthesis finished. Playback may not have: the queue keeps
          // reporting until the audio clock says it drained.
          playback().endUtterance();
          return;
        }

        if (message.type === "stop_playback") {
          // The server cleared the provider; this is the browser half.
          playback().stopAll();
          pendingMetaRef.current = null;
          return;
        }

        if (message.type === "voice_error") {
          // Not a dead end: the session is still open and the candidate can
          // choose to continue by typing. Nothing is substituted silently.
          addLog("voice_error", String(message.detail ?? message.reason ?? ""));
          setVoiceProblem(String(message.detail ?? "Voice is unavailable."));
          return;
        }

        if (message.type === "voice_warning") {
          addLog("voice_warning", String(message.detail ?? ""));
          return;
        }

        if (
          message.type === "caption" ||
          message.type === "agent_utterance_start"
        ) {
          if (message.text) {
            setUtterance({
              utteranceId: message.utterance_id ?? "",
              text: message.text,
              speaker: message.speaker_label ?? message.persona ?? "Interviewer",
              persona: message.persona ?? null,
            });
          }
          if (message.utterance_id) utteranceIdRef.current = message.utterance_id;
          return;
        }

        if (message.type === "agent_utterance_end") {
          // Acknowledgements are driven by the playback queue's audio clock,
          // not by this event: the server finishing a line says nothing about
          // whether the candidate has heard it yet.
          return;
        }

        if (message.type === "session_complete") {
          endedRef.current = true;
          if (message.session_id) setSessionId(String(message.session_id));
          playbackRef.current?.stopAll();
          void endCapture();
          setStatus("complete");
          setRunning(false);
        }
      };

      socket.onclose = () => {
        clearWaking();
        playbackRef.current?.stopAll();
        if (endedRef.current || !resumeTokenRef.current) {
          if (!endedRef.current) setStatus("unreachable");
          setRunning(false);
          return;
        }
        if (triesRef.current >= MAX_RECONNECTS) {
          setStatus("unreachable");
          setStatusDetail("Could not reconnect to the interview server.");
          setRunning(false);
          return;
        }
        const wait = Math.min(8000, 500 * 2 ** triesRef.current);
        triesRef.current += 1;
        setStatus("reconnecting");
        setStatusDetail(`Retrying in ${Math.round(wait / 1000)}s…`);
        window.setTimeout(() => {
          attach(new WebSocket(WS_URL), {
            type: "session_resume",
            token: resumeTokenRef.current,
          });
        }, wait);
      };

      socket.onerror = () => {
        if (socket.readyState !== WebSocket.OPEN) setStatus("waking");
      };
    },
    [addLog, beginCapture, clearWaking, endCapture, playback, send],
  );

  const start = useCallback(
    async (options: SessionOptions) => {
      setLogs([]);
      setProgress(null);
      setRoundNotice(null);
      setProviderWarning("");
      setVoiceProblem("");
      setSessionId("");
      setIntensity(options.intensity);
      setUtterance(null);
      setTurnCount(0);
      setStatus("connecting");
      setStatusDetail("");
      endedRef.current = false;
      resumeTokenRef.current = "";
      triesRef.current = 0;
      setLane(options.lane);
      laneRef.current = options.lane;
      setInterim("");
      setMuted(false);
      bargedRef.current = false;
      setRunning(true);
      if (options.lane === "voice") {
        // Must happen inside the click. An AudioContext created outside a user
        // gesture starts suspended and every scheduled buffer is silent, which
        // is indistinguishable from a broken provider.
        void playback().unlock();
      }
      const authToken = currentToken() || (await ensureGuest());
      attach(new WebSocket(WS_URL), {
        type: "session_start",
        auth_token: authToken,
        intake_id: options.intakeId,
        lane: options.lane,
      });
    },
    [attach, playback],
  );

  /** The explicit text fallback after a voice problem. */
  const switchToText = useCallback(() => {
    send({ type: "switch_to_text", reason: "candidate chose text after a voice problem" });
  }, [send]);

  const end = useCallback(() => {
    endedRef.current = true;
    send({ type: "session_end" });
    playbackRef.current?.stopAll();
    void endCapture();
    setRunning(false);
  }, [endCapture, send]);

  const bargeIn = useCallback(
    (held?: boolean) => {
      if (bargedRef.current) return; // one interruption per utterance
      bargedRef.current = true;
      // Stop local audio *first* and use the audio clock's answer as the
      // played position. Reporting a scheduled figure here would truncate the
      // transcript past what the candidate actually heard.
      const audible = playbackRef.current?.stopAll() ?? 0;
      pendingMetaRef.current = null;
      send({
        type: "barge_in",
        utterance_id: utteranceIdRef.current,
        played_ms: audible,
        // Only meaningful under push-to-talk; the server ignores an unheld
        // barge-in in that mode.
        held: pushToTalk ? held : undefined,
      });
      addLog("barge_in", `stopped playback at ${audible} ms audible`);
    },
    [addLog, pushToTalk, send],
  );

  const togglePushToTalk = useCallback(() => {
    setPushToTalk((on) => {
      const next = !on;
      send({ type: "push_to_talk", on: next });
      // Turning push-to-talk on mutes the stream until the key is held, so the
      // button controls what actually leaves the machine.
      captureRef.current?.setMuted(next);
      setMuted(next);
      return next;
    });
  }, [send]);

  /** Hold-to-talk: unmute while held, re-mute on release and close the turn. */
  const setTalking = useCallback(
    (talking: boolean) => {
      if (!pushToTalk) return;
      captureRef.current?.setMuted(!talking);
      setMuted(!talking);
      send({ type: "mute", muted: !talking });
      if (!talking) send({ type: "answer_done" });
    },
    [pushToTalk, send],
  );

  const toggleMute = useCallback(() => {
    setMuted((on) => {
      const next = !on;
      captureRef.current?.setMuted(next);
      send({ type: "mute", muted: next });
      return next;
    });
  }, [send]);

  const answer = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      send({
        type: laneRef.current === "text" ? "candidate_text" : "candidate_final",
        text: trimmed,
      });
    },
    [send],
  );

  useEffect(
    () => () => {
      clearWaking();
      endedRef.current = true;
      wsRef.current?.close();
      // Release the microphone track, the worklet and both audio contexts.
      // Leaving them open keeps the browser's recording indicator lit after the
      // interview has finished.
      void playbackRef.current?.close();
      void captureRef.current?.stop();
      captureRef.current = null;
      playbackRef.current = null;
    },
    [clearWaking],
  );

  return {
    status,
    statusDetail,
    running,
    utterance,
    turnCount,
    pushToTalk,
    logs,
    lane,
    // Stage 14 voice surface.
    interim,
    micActive,
    muted,
    micStream,
    voiceInfo,
    progress,
    roundNotice,
    providerWarning,
    voiceProblem,
    sessionId,
    intensity,
    interviewer,
    captureSupported: isCaptureSupported(),
    start,
    switchToText,
    end,
    bargeIn,
    togglePushToTalk,
    setTalking,
    toggleMute,
    answer,
  };
}

/** Human-readable status, including the cold-start message. */
export function statusMessage(
  status: SessionStatus,
  detail: string,
): { text: string; tone: "idle" | "busy" | "ok" | "bad" } {
  switch (status) {
    case "idle":
      return { text: "Ready when you are", tone: "idle" };
    case "connecting":
      return { text: "Connecting…", tone: "busy" };
    case "waking":
      return {
        text: "Starting the interview server, this can take up to a minute",
        tone: "busy",
      };
    case "ready":
      return { text: "Session live", tone: "ok" };
    case "resumed":
      return { text: "Reconnected — same session", tone: "ok" };
    case "reconnecting":
      return {
        text: detail ? `Connection lost. ${detail}` : "Connection lost. Retrying…",
        tone: "busy",
      };
    case "complete":
      return { text: "Interview complete", tone: "ok" };
    case "rejected":
      return {
        text: detail || "The server is at capacity. Try again shortly.",
        tone: "bad",
      };
    case "unreachable":
      return {
        text: detail || "Cannot reach the interview server.",
        tone: "bad",
      };
  }
}
