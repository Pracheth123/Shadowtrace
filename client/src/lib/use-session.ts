import { useCallback, useEffect, useRef, useState } from "react";

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
 */

const WS_HOST = import.meta.env.VITE_WS_HOST || `${location.hostname}:8000`;
const WS_URL = `${location.protocol === "https:" ? "wss://" : "ws://"}${WS_HOST}/ws/session`;
export const HTTP_BASE = `${location.protocol}//${WS_HOST}`;

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

export type SessionOptions = {
  lane: Lane;
  intensity: Intensity;
  panelMode: boolean;
  packId?: string;
};

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

  const wsRef = useRef<WebSocket | null>(null);
  const playedMsRef = useRef(0);
  const ackTimerRef = useRef<number | null>(null);
  const wakingTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const resumeTokenRef = useRef("");
  const endedRef = useRef(false);
  const triesRef = useRef(0);
  const utteranceIdRef = useRef("");
  const laneRef = useRef<Lane>("voice");

  const addLog = useCallback((type: string, detail: string) => {
    setLogs((previous) => [...previous.slice(-60), { type, detail }]);
  }, []);

  const stopAck = useCallback(() => {
    if (ackTimerRef.current != null) {
      window.clearInterval(ackTimerRef.current);
      ackTimerRef.current = null;
    }
  }, []);

  const clearWaking = useCallback(() => {
    if (wakingTimerRef.current != null) {
      window.clearTimeout(wakingTimerRef.current);
      wakingTimerRef.current = null;
    }
  }, []);

  const send = useCallback((payload: object) => {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }, []);

  /** Play a silence burst and advance the played-ms cursor. */
  const playPcm = useCallback(async (buffer: ArrayBuffer) => {
    const context =
      audioCtxRef.current ?? new AudioContext({ sampleRate: 16000 });
    audioCtxRef.current = context;
    const pcm = new Int16Array(buffer);
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i += 1) floats[i] = pcm[i] / 32768;
    const audioBuffer = context.createBuffer(1, floats.length || 1, 16000);
    audioBuffer.copyToChannel(floats, 0);
    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);
    source.start();
    playedMsRef.current += Math.round((floats.length / 16000) * 1000);
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
          await playPcm(event.data as ArrayBuffer);
          return;
        }
        const message = JSON.parse(event.data);
        addLog(message.type, JSON.stringify(message));

        if (message.type === "session_rejected") {
          setStatus("rejected");
          setStatusDetail(message.reason ?? "");
          setRunning(false);
          return;
        }

        if (message.type === "session_ready") {
          resumeTokenRef.current = message.resume_token ?? "";
          triesRef.current = 0;
          if (message.lane) {
            setLane(message.lane);
            laneRef.current = message.lane;
          }
          setStatus(message.resumed ? "resumed" : "ready");
          setStatusDetail("");
          return;
        }

        if (message.type === "turn_end") {
          setTurnCount((count) => count + 1);
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
          if (message.utterance_id) {
            utteranceIdRef.current = message.utterance_id;
            playedMsRef.current = 0;
            stopAck();
            if (laneRef.current === "voice") {
              ackTimerRef.current = window.setInterval(() => {
                send({
                  type: "playback_ack",
                  played_ms: playedMsRef.current,
                  utterance_id: message.utterance_id,
                });
              }, 100);
            }
          }
          return;
        }

        if (message.type === "agent_utterance_end") {
          stopAck();
          return;
        }

        if (message.type === "session_complete") {
          endedRef.current = true;
          stopAck();
          setStatus("complete");
          setRunning(false);
        }
      };

      socket.onclose = () => {
        clearWaking();
        stopAck();
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
    [addLog, clearWaking, playPcm, send, stopAck],
  );

  const start = useCallback(
    (options: SessionOptions) => {
      setLogs([]);
      setUtterance(null);
      setTurnCount(0);
      setStatus("connecting");
      setStatusDetail("");
      endedRef.current = false;
      resumeTokenRef.current = "";
      triesRef.current = 0;
      setLane(options.lane);
      laneRef.current = options.lane;
      setRunning(true);
      attach(new WebSocket(WS_URL), {
        type: "session_start",
        lane: options.lane,
        intensity: options.intensity,
        panel_mode: options.panelMode,
        ...(options.packId ? { pack_id: options.packId } : {}),
      });
    },
    [attach],
  );

  const end = useCallback(() => {
    endedRef.current = true;
    send({ type: "session_end" });
    stopAck();
    setRunning(false);
  }, [send, stopAck]);

  const bargeIn = useCallback(
    (held?: boolean) => {
      send({
        type: "barge_in",
        utterance_id: utteranceIdRef.current,
        played_ms: playedMsRef.current,
        // Only meaningful under push-to-talk; the server ignores an unheld
        // barge-in in that mode.
        held: pushToTalk ? held : undefined,
      });
      stopAck();
    },
    [pushToTalk, send, stopAck],
  );

  const togglePushToTalk = useCallback(() => {
    setPushToTalk((on) => {
      const next = !on;
      send({ type: "push_to_talk", on: next });
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
      stopAck();
      clearWaking();
      endedRef.current = true;
      wsRef.current?.close();
      void audioCtxRef.current?.close();
    },
    [clearWaking, stopAck],
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
    start,
    end,
    bargeIn,
    togglePushToTalk,
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
