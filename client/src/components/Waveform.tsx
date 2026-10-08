import { useEffect, useRef } from "react";
import { MicIcon, MicOffIcon } from "lucide-react";
import WaveSurfer from "wavesurfer.js";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Live microphone level, drawn from the session's own stream.
 *
 * Rewritten at stage 14. It previously used WaveSurfer's RecordPlugin to open
 * its **own** `getUserMedia` capture purely to draw a meter — a second,
 * independent recording of the candidate that was never sent anywhere, while
 * the session socket sent nothing at all. Two captures also means two
 * permission prompts and two echo-cancellation contexts fighting each other.
 *
 * Now it is a pure visualiser: it receives the one `MediaStream` the session
 * already opened and renders its level. It never starts or stops capture, and
 * it never touches the socket.
 *
 * Colours come from the palette (canvas needs literal values): Prussian ink
 * for the waveform, the lighter pen tint for peaks. Keep in step with styles.css.
 */

const WAVE_COLOR = "#233c66"; // --pen-700
const PEAK_COLOR = "#6f86ab"; // --pen-400

export type WaveformProps = {
  /** The session's microphone stream. Null before capture starts. */
  stream: MediaStream | null;
  active: boolean;
  muted?: boolean;
  onToggleMute?: () => void;
  className?: string;
};

export function Waveform({
  stream,
  active,
  muted = false,
  onToggleMute,
  className,
}: WaveformProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const waveRef = useRef<WaveSurfer | null>(null);
  const rafRef = useRef<number | null>(null);
  const audioRef = useRef<{ context: AudioContext; analyser: AnalyserNode } | null>(
    null,
  );

  // The canvas. Created once per mount; WaveSurfer writes into the DOM node, so
  // building it during render would stack canvases on every update.
  useEffect(() => {
    if (!hostRef.current) return;
    const wavesurfer = WaveSurfer.create({
      container: hostRef.current,
      height: 72,
      waveColor: WAVE_COLOR,
      progressColor: PEAK_COLOR,
      cursorWidth: 0,
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      interact: false,
    });
    waveRef.current = wavesurfer;
    return () => {
      wavesurfer.destroy();
      waveRef.current = null;
    };
  }, []);

  // Analyse the shared stream. A separate AnalyserNode on the same stream is
  // read-only: it does not consume the audio or interfere with the capture
  // worklet that is feeding the socket.
  useEffect(() => {
    if (!stream || !active) return;

    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    audioRef.current = { context, analyser };

    const data = new Uint8Array(analyser.frequencyBinCount);
    const history: number[] = [];

    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i += 1) {
        const value = (data[i] - 128) / 128;
        sum += value * value;
      }
      const rms = Math.sqrt(sum / data.length);
      history.push(Math.min(1, rms * 3));
      if (history.length > 400) history.shift();
      // `interact: false` plus a peaks array renders the level history without
      // WaveSurfer trying to own any audio.
      waveRef.current?.load("", [history], 1);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      try {
        source.disconnect();
        analyser.disconnect();
      } catch {
        // Already torn down.
      }
      void context.close();
      audioRef.current = null;
    };
  }, [stream, active]);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div
        ref={hostRef}
        className={cn(
          "wave-host min-h-[72px] w-full overflow-hidden rounded-lg border border-border bg-background px-2",
          (!active || !stream || muted) && "opacity-50",
        )}
      />
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleMute}
          disabled={!active || !stream}
          aria-label={muted ? "Unmute microphone" : "Mute microphone"}
          className={cn(
            "rounded-full border border-border",
            !muted && active && stream && "animate-pulse-ring bg-secondary text-primary",
          )}
        >
          {muted ? <MicOffIcon /> : <MicIcon />}
        </Button>
        <p className="text-xs text-muted-foreground">
          {!stream
            ? "Microphone not started"
            : muted
              ? "Muted — nothing is being sent"
              : "Listening"}
        </p>
      </div>
    </div>
  );
}
