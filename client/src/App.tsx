import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL =
  (location.protocol === "https:" ? "wss://" : "ws://") +
  (import.meta.env.VITE_WS_HOST || location.hostname + ":8000") +
  "/ws/session";

type LogLine = { type: string; msg: string };

export default function App() {
  const [status, setStatus] = useState("Idle");
  const [caption, setCaption] = useState("Agent caption appears here.");
  const [level, setLevel] = useState(0);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [utteranceId, setUtteranceId] = useState<string>("");

  const wsRef = useRef<WebSocket | null>(null);
  const playedMsRef = useRef(0);
  const ackTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const recognizerRef = useRef<SpeechRecognition | null>(null);

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
      addLog("warn", "Web Speech API unavailable — type answers via console candidate_final");
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

  const startSession = async () => {
    setLogs([]);
    setStatus("Connecting…");
    const ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("Connected");
      sendJson({ type: "session_start", intensity: "realistic" });
    };
    ws.onmessage = async (ev) => {
      if (typeof ev.data !== "string") {
        await playPcm(ev.data as ArrayBuffer);
        return;
      }
      const msg = JSON.parse(ev.data);
      addLog(msg.type, JSON.stringify(msg));
      if (msg.type === "session_ready") setStatus("Session ready");
      if (msg.type === "caption" || msg.type === "agent_utterance_start") {
        setCaption(msg.text || caption);
        if (msg.utterance_id) {
          setUtteranceId(msg.utterance_id);
          playedMsRef.current = 0;
          stopAck();
          ackTimerRef.current = window.setInterval(() => {
            sendJson({
              type: "playback_ack",
              played_ms: playedMsRef.current,
              utterance_id: msg.utterance_id,
            });
          }, 100);
        }
      }
      if (msg.type === "agent_utterance_end") stopAck();
      if (msg.type === "session_complete") {
        setStatus("Complete");
        setRunning(false);
        stopAck();
        recognizerRef.current?.stop();
      }
    };
    ws.onclose = () => {
      setStatus("Disconnected");
      setRunning(false);
      stopAck();
    };

    setRunning(true);
    await startMicMeter();
    startRecognition();
  };

  const endSession = () => {
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
    });
    addLog("barge_in", `played_ms=${playedMsRef.current}`);
    stopAck();
  };

  useEffect(() => () => {
    stopAck();
    wsRef.current?.close();
    recognizerRef.current?.stop();
  }, []);

  return (
    <>
      <h1>Shadowtrace</h1>
      <p className="sub">Stage 4 — talk with one interviewer (mock LLM/TTS by default).</p>

      <div className="row">
        <button className="primary" disabled={running} onClick={startSession}>
          Start
        </button>
        <button disabled={!running} onClick={bargeIn}>
          Barge in
        </button>
        <button disabled={!running} onClick={endSession}>
          End
        </button>
        <div className="meter" title="Mic level">
          <span style={{ width: `${level}%` }} />
        </div>
        <span className={`status ${running ? "ok" : ""}`}>{status}</span>
      </div>

      <div className="caption">
        <span className="label">Agent</span>
        {caption}
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
