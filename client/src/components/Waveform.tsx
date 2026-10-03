import { useEffect, useRef, useState } from "react";
import { MicIcon, PauseIcon } from "lucide-react";
import WaveSurfer from "wavesurfer.js";
import RecordPlugin from "wavesurfer.js/dist/plugins/record.esm.js";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Live mic waveform, converted from the vanilla WaveSurfer example.
 *
 * Two deliberate departures from that example:
 *
 *  1. **Visualisation only.** The original recorded a blob, rendered it back
 *     and offered a download link. Candidate audio already travels over the
 *     session WebSocket, so recording here would be a second, independent
 *     capture — and the download would hand out a copy of the interview. So
 *     `renderRecordedAudio: false`, and the `record-end` blob is dropped.
 *  2. **No mic <select>.** The example enumerated devices on mount, which
 *     triggers a permission prompt before the candidate has pressed anything.
 *     The browser's own picker handles device choice at grant time.
 *
 * Colours come from the palette: primary for the waveform, accent for played
 * progress.
 */

const WAVE_COLOR = "#3d27ce"; // primary
const PROGRESS_COLOR = "#443dff"; // accent

export type WaveformProps = {
  /** Mirrors the parent's run state; false tears the recorder down. */
  active: boolean;
  onError?: (message: string) => void;
  className?: string;
};

function formatElapsed(ms: number) {
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return [minutes, seconds]
    .map((unit) => String(unit).padStart(2, "0"))
    .join(":");
}

export function Waveform({ active, onError, className }: WaveformProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const waveRef = useRef<WaveSurfer | null>(null);
  const recordRef = useRef<ReturnType<typeof RecordPlugin.create> | null>(null);
  const [recording, setRecording] = useState(false);
  const [paused, setPaused] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [starting, setStarting] = useState(false);

  // Build the instance once per mount. WaveSurfer writes into the DOM node, so
  // creating it inside the render path would stack canvases on every update.
  useEffect(() => {
    if (!hostRef.current) return;

    const wavesurfer = WaveSurfer.create({
      container: hostRef.current,
      height: 72,
      waveColor: WAVE_COLOR,
      progressColor: PROGRESS_COLOR,
      cursorWidth: 0,
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
    });

    const record = wavesurfer.registerPlugin(
      RecordPlugin.create({
        renderRecordedAudio: false,
        continuousWaveform: true,
        continuousWaveformDuration: 30,
      }),
    );

    record.on("record-progress", setElapsedMs);
    record.on("record-end", () => {
      // The blob is intentionally not kept. See the note above.
      setRecording(false);
      setPaused(false);
    });

    waveRef.current = wavesurfer;
    recordRef.current = record;

    return () => {
      try {
        if (record.isRecording() || record.isPaused()) record.stopRecording();
      } catch {
        // Already torn down; nothing to stop.
      }
      wavesurfer.destroy();
      waveRef.current = null;
      recordRef.current = null;
    };
  }, []);

  // Stop cleanly when the parent ends the session.
  useEffect(() => {
    if (active) return;
    const record = recordRef.current;
    if (record && (record.isRecording() || record.isPaused())) {
      record.stopRecording();
    }
    setRecording(false);
    setPaused(false);
    setElapsedMs(0);
  }, [active]);

  const toggleRecording = async () => {
    const record = recordRef.current;
    if (!record) return;

    if (record.isRecording() || record.isPaused()) {
      record.stopRecording();
      return;
    }

    setStarting(true);
    try {
      await record.startRecording();
      setRecording(true);
      setPaused(false);
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Could not access the microphone.";
      onError?.(message);
    } finally {
      setStarting(false);
    }
  };

  const togglePause = () => {
    const record = recordRef.current;
    if (!record) return;
    if (record.isPaused()) {
      record.resumeRecording();
      setPaused(false);
    } else {
      record.pauseRecording();
      setPaused(true);
    }
  };

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div
        ref={hostRef}
        className={cn(
          "wave-host min-h-[72px] w-full overflow-hidden rounded-lg border border-border bg-background px-2",
          !recording && "opacity-50",
        )}
      />

      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-lg"
          onClick={toggleRecording}
          disabled={!active || starting}
          aria-label={recording ? "Stop recording" : "Start recording"}
          className={cn(
            "rounded-full border border-border",
            recording && "animate-pulse-ring bg-secondary text-primary",
          )}
        >
          <MicIcon />
        </Button>

        <Button
          variant="ghost"
          size="icon"
          onClick={togglePause}
          disabled={!recording}
          aria-label={paused ? "Resume recording" : "Pause recording"}
          className="rounded-full border border-border"
        >
          <PauseIcon />
        </Button>

        <p
          aria-live="off"
          className="font-mono text-sm tabular-nums text-muted-foreground"
        >
          {formatElapsed(elapsedMs)}
        </p>

        <p className="ml-auto text-xs text-muted-foreground">
          {starting
            ? "Requesting microphone…"
            : paused
              ? "Paused"
              : recording
                ? "Listening"
                : "Mic idle"}
        </p>
      </div>
    </div>
  );
}
