import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL =
  (location.protocol === "https:" ? "wss://" : "ws://") +
  (import.meta.env.VITE_WS_HOST || location.hostname + ":8000") +
  "/ws/session";

const HTTP_BASE =
  location.protocol +
  "//" +
  (import.meta.env.VITE_WS_HOST || location.hostname + ":8000");

type LogLine = { type: string; msg: string };
type Lane = "voice" | "text";

export default function App() {
  const [status, setStatus] = useState("Idle");
  const [caption, setCaption] = useState("Agent caption appears here.");
  const [speaker, setSpeaker] = useState("Agent");
  const [level, setLevel] = useState(0);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [utteranceId, setUtteranceId] = useState<string>("");
  const [intensity, setIntensity] = useState<"coach" | "realistic" | "panel">("realistic");

  // Stage 11
  const [lane, setLane] = useState<Lane>("voice");
  const [panelMode, setPanelMode] = useState(false);
  const [pushToTalk, setPushToTalk] = useState(false);
  const [talking, setTalking] = useState(false);
  const [videoOn, setVideoOn] = useState(false);
  const [typed, setTyped] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const playedMsRef = useRef(0);
  const ackTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const recognizerRef = useRef<SpeechRecognition | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const videoStreamRef = useRef<MediaStream | null>(null);
  // Reconnect state. The token is single-use; the server issues a fresh one
  // every time a socket attaches.
  const resumeTokenRef = useRef<string>("");
  const endedRef = useRef(false);
  const reconnectTriesRef = useRef(0);

  const addLog = useCallback((type: string, msg: string) => {
    setLogs((prev) => [...prev.slice(-80), { type, msg }]);
  }, []);

  const stopAck = () => {
    if (ackTimerRef.current != null) {
      window.clearInterval(ackTimerRef.current);
      ackTimerRef.current = null;
    }
  };

  const sendJson = (obj: object) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  };

  const playPcm = async (buf: ArrayBuffer) => {
    const ctx = audioCtxRef.current || new AudioContext({ sampleRate: 16000 });
    audioCtxRef.current = ctx;
    const int16 = new Int16Array(buf);
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
    const ab = ctx.createBuffer(1, f32.length, 16000);
    ab.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = ab;
    src.connect(ctx.destination);
    src.start();
    const chunkMs = Math.round((f32.length / 16000) * 1000);
    playedMsRef.current += chunkMs;
  };

  const startMicMeter = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      setLevel(Math.min(100, Math.round(rms * 220)));
      if (running) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return stream;
  };

  const startRecognition = () => {
    const SR =
      window.SpeechRecognition ||
      (window as unknown as { webkitSpeechRecognition?: typeof SpeechRecognition })
        .webkitSpeechRecognition;
    if (!SR) {
      addLog("warn", "Web Speech API unavailable — switch to the text lane");
      return;
    }
    const rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.onresult = (ev: SpeechRecognitionEvent) => {
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
      }
      if (finalText.trim()) {
        addLog("candidate_final", finalText.trim());
        sendJson({ type: "candidate_final", text: finalText.trim() });
      }
    };
    rec.onerror = (e) => addLog("speech_error", String(e.error));
    rec.start();
    recognizerRef.current = rec;
  };

  // Local camera preview only. The stream is attached to a <video> element and
  // nothing else: no frame is encoded, sent over the socket, or stored. Video
  // is never a model or score input (contract 9).
  const toggleVideo = async () => {
    if (videoOn) {
      videoStreamRef.current?.getTracks().forEach((t) => t.stop());
      videoStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setVideoOn(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      videoStreamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
      setVideoOn(true);
      addLog("video", "local preview on — not sent to the server");
    } catch (e) {
      addLog("video_error", String(e));
    }
  };

  const attach = (ws: WebSocket, opening: object) => {
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("Connected");
      ws.send(JSON.stringify(opening));
    };

    ws.onmessage = async (ev) => {
      if (typeof ev.data !== "string") {
        await playPcm(ev.data as ArrayBuffer);
        return;
      }
      const msg = JSON.parse(ev.data);
      addLog(msg.type, JSON.stringify(msg));

      if (msg.type === "session_rejected") {
        setStatus(`Rejected: ${msg.reason}`);
        setRunning(false);
        return;
      }
      if (msg.type === "session_ready") {
        resumeTokenRef.current = msg.resume_token || "";
        reconnectTriesRef.current = 0;
        setStatus(msg.resumed ? "Resumed" : "Session ready");
        // No session_start here: the opening frame this socket already sent was
        // the session_start (or the session_resume), and the server acts on it
        // before replying with session_ready.
      }
      if (msg.type === "caption" || msg.type === "agent_utterance_start") {
        setCaption(msg.text || caption);
        if (msg.speaker_label) setSpeaker(msg.speaker_label);
        else if (msg.persona) setSpeaker(msg.persona);
        if (msg.utterance_id) {
          setUtteranceId(msg.utterance_id);
          playedMsRef.current = 0;
          stopAck();
          // The text lane emits no audio, so there is nothing to acknowledge
          // and nothing that could be truncated.
          if (lane === "voice") {
            ackTimerRef.current = window.setInterval(() => {
              sendJson({
                type: "playback_ack",
                played_ms: playedMsRef.current,
                utterance_id: msg.utterance_id,
              });
            }, 100);
          }
        }
      }
      if (msg.type === "agent_utterance_end") stopAck();
      if (msg.type === "session_complete") {
        endedRef.current = true;
        setStatus("Complete");
        setRunning(false);
        stopAck();
        recognizerRef.current?.stop();
      }
    };

    ws.onclose = () => {
      stopAck();
      if (endedRef.current || !resumeTokenRef.current) {
        setStatus("Disconnected");
        setRunning(false);
        return;
      }
      // The server parks a dropped session for its resume window. Reconnect
      // with the token so the interview continues on the same log.
      if (reconnectTriesRef.current >= 5) {
        setStatus("Could not reconnect");
        setRunning(false);
        return;
      }
      const wait = Math.min(8000, 500 * 2 ** reconnectTriesRef.current);
      reconnectTriesRef.current += 1;
      setStatus(`Reconnecting in ${Math.round(wait / 1000)}s…`);
      window.setTimeout(() => {
        attach(new WebSocket(WS_URL), {
          type: "session_resume",
          token: resumeTokenRef.current,
        });
      }, wait);
    };
  };

  const startSession = async () => {
    setLogs([]);
    setStatus("Connecting…");
    endedRef.current = false;
    resumeTokenRef.current = "";
    reconnectTriesRef.current = 0;
    attach(new WebSocket(WS_URL), {
      type: "session_start",
      intensity,
      lane,
      panel_mode: panelMode,
    });
    setRunning(true);
    if (lane === "voice") {
      await startMicMeter();
      startRecognition();
    }
  };

  const endSession = () => {
    endedRef.current = true;
    sendJson({ type: "session_end" });
    recognizerRef.current?.stop();
    stopAck();
    setRunning(false);
  };

  const bargeIn = () => {
    sendJson({
      type: "barge_in",
      utterance_id: utteranceId,
      played_ms: playedMsRef.current,
      held: pushToTalk ? talking : undefined,
    });
    addLog("barge_in", `played_ms=${playedMsRef.current}`);
    stopAck();
  };

  const togglePushToTalk = () => {
    const next = !pushToTalk;
    setPushToTalk(next);
    sendJson({ type: "push_to_talk", on: next });
  };

  const sendTyped = () => {
    const text = typed.trim();
    if (!text) return;
    sendJson({ type: "candidate_text", text });
    addLog("candidate_text", text);
    setTyped("");
  };

  const deleteMyData = async () => {
    const candidate = window.prompt("Delete everything for which candidate id?");
    if (!candidate) return;
    const res = await fetch(`${HTTP_BASE}/me/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidate }),
    });
    addLog("delete_my_data", JSON.stringify(await res.json()));
  };

  useEffect(
    () => () => {
      stopAck();
      endedRef.current = true;
      wsRef.current?.close();
      recognizerRef.current?.stop();
      videoStreamRef.current?.getTracks().forEach((t) => t.stop());
    },
    []
  );

  return (
    <>
      <h1>Shadowtrace</h1>
      <p className="sub">
        Pick a hardness and a lane before you start. Panel mode puts two or
        three interviewer voices in the room; one speaks at a time.
      </p>

      <div className="row">
        {(["coach", "realistic", "panel"] as const).map((item) => (
          <button
            key={item}
            disabled={running}
            className={intensity === item ? "primary" : ""}
            onClick={() => setIntensity(item)}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="row">
        {(["voice", "text"] as const).map((item) => (
          <button
            key={item}
            disabled={running}
            className={lane === item ? "primary" : ""}
            onClick={() => setLane(item)}
          >
            {item} lane
          </button>
        ))}
        <button
          disabled={running}
          className={panelMode ? "primary" : ""}
          onClick={() => setPanelMode(!panelMode)}
          title="Needs PANEL_MODE=1 on the server"
        >
          panel voices {panelMode ? "on" : "off"}
        </button>
      </div>

      <div className="row">
        <button className="primary" disabled={running} onClick={startSession}>
          Start
        </button>
        <button disabled={!running || lane === "text"} onClick={bargeIn}>
          Barge in
        </button>
        <button
          disabled={!running || lane === "text"}
          className={pushToTalk ? "primary" : ""}
          onClick={togglePushToTalk}
        >
          push-to-talk {pushToTalk ? "on" : "off"}
        </button>
        {pushToTalk && (
          <button
            disabled={!running}
            onMouseDown={() => setTalking(true)}
            onMouseUp={() => setTalking(false)}
            onTouchStart={() => setTalking(true)}
            onTouchEnd={() => setTalking(false)}
          >
            {talking ? "listening…" : "hold to talk"}
          </button>
        )}
        <button disabled={!running} onClick={endSession}>
          End
        </button>
        <div className="meter" title="Mic level">
          <span style={{ width: `${level}%` }} />
        </div>
        <span className={`status ${running ? "ok" : ""}`}>{status}</span>
      </div>

      <div className="caption">
        <span className="label">{speaker}</span>
        {caption}
      </div>

      {lane === "text" && (
        <div className="row">
          <input
            style={{ flex: 1, minWidth: "18rem" }}
            value={typed}
            disabled={!running}
            placeholder="Type your answer, then press Enter"
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendTyped();
            }}
          />
          <button disabled={!running || !typed.trim()} onClick={sendTyped}>
            Send
          </button>
        </div>
      )}

      <div className="row">
        <button onClick={toggleVideo}>
          camera preview {videoOn ? "on" : "off"}
        </button>
        <span className="sub">
          Display only. Nothing is sent to the server, and video is never an
          input to any model or score.
        </span>
      </div>
      <video
        ref={videoRef}
        hidden={!videoOn}
        autoPlay
        playsInline
        muted
        style={{ width: "16rem", borderRadius: "0.5rem" }}
      />

      <div className="row">
        <button onClick={deleteMyData}>Delete my data</button>
        <span className="sub">
          Removes stored sessions, reports and intake artifacts, and shows you
          the manifest.
        </span>
      </div>

      <div className="log">
        {logs.map((l, i) => (
          <div key={i}>
            [{l.type}] {l.msg}
          </div>
        ))}
      </div>
    </>
  );
}
