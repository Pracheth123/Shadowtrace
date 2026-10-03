/**
 * Microphone capture: one stream, shared.
 *
 * The previous build had two independent captures. `use-session` never opened a
 * microphone at all, and `Waveform` opened its own via WaveSurfer's
 * RecordPlugin purely to draw a meter — so the visualiser was recording audio
 * that was never sent anywhere, and nothing was sent to the server. This owns a
 * single `MediaStream` and hands the same object to both the uploader and the
 * visualiser.
 *
 * Echo cancellation is on. Without it the interviewer's own voice comes back in
 * through the microphone, Deepgram transcribes it, endpointing fires, and the
 * agent interrupts itself in a loop.
 */

export type CaptureConfig = {
  /** Called with each ~20 ms PCM16 frame, ready to put on the socket. */
  onFrame: (frame: ArrayBuffer) => void;
  onError?: (message: string) => void;
  frameMs?: number;
};

export type CaptureHandle = {
  /** The device's real rate. Declared to the server; never assumed. */
  sampleRate: number;
  channels: number;
  /** Shared with the waveform so there is exactly one capture. */
  stream: MediaStream;
  context: AudioContext;
  setMuted: (muted: boolean) => void;
  readonly muted: boolean;
  stop: () => Promise<void>;
};

export class MicrophoneDenied extends Error {
  readonly recovery: string;
  constructor(message: string, recovery: string) {
    super(message);
    this.name = "MicrophoneDenied";
    this.recovery = recovery;
  }
}

function describeGetUserMediaError(error: unknown): MicrophoneDenied {
  const name = (error as { name?: string })?.name ?? "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return new MicrophoneDenied(
        "Microphone access was blocked.",
        "Allow the microphone for this site in your browser's address bar, then press Start again. You can also switch to the text lane.",
      );
    case "NotFoundError":
    case "OverconstrainedError":
      return new MicrophoneDenied(
        "No microphone was found.",
        "Connect a microphone or choose the text lane.",
      );
    case "NotReadableError":
      return new MicrophoneDenied(
        "The microphone is in use by another application.",
        "Close the other app using it (a call or recorder), then press Start again.",
      );
    default:
      return new MicrophoneDenied(
        "The microphone could not be opened.",
        "Check your browser's microphone settings, or use the text lane.",
      );
  }
}

export function isCaptureSupported(): boolean {
  return Boolean(
    typeof AudioWorkletNode !== "undefined" &&
      typeof navigator.mediaDevices?.getUserMedia === "function" &&
      typeof window.AudioContext !== "undefined",
  );
}

/**
 * Open the microphone and start emitting PCM16 frames.
 *
 * Must be called from a user gesture (the Start button). Browsers both gate
 * `getUserMedia` on one and start an `AudioContext` suspended until one.
 */
export async function startCapture(
  config: CaptureConfig,
): Promise<CaptureHandle> {
  if (!isCaptureSupported()) {
    throw new MicrophoneDenied(
      "This browser cannot capture audio for the interview.",
      "Use a current version of Chrome, Edge, Firefox or Safari, or choose the text lane.",
    );
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        // Without echo cancellation the interviewer interrupts itself.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    });
  } catch (error) {
    throw describeGetUserMediaError(error);
  }

  // No sampleRate constraint on the context: forcing one makes the browser
  // resample behind our back, and the server already converts from whatever the
  // device actually runs at.
  const context = new AudioContext();
  try {
    await context.audioWorklet.addModule("/pcm-capture-worklet.js");
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
    throw new MicrophoneDenied(
      "The audio processor could not be loaded.",
      "Reload the page. If it keeps happening, use the text lane.",
    );
  }

  const source = context.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(context, "pcm-capture", {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    processorOptions: { frameMs: config.frameMs ?? 20 },
  });

  worklet.port.onmessage = (event) => {
    const data = event.data;
    if (data?.type === "pcm" && data.frame) config.onFrame(data.frame as ArrayBuffer);
  };
  worklet.onprocessorerror = () => {
    config.onError?.("The audio processor stopped unexpectedly.");
  };

  // numberOfOutputs is 0, so the worklet is a sink: capture never routes back
  // to the speakers, which would be a feedback loop.
  source.connect(worklet);

  const track = stream.getAudioTracks()[0];
  const settings = track?.getSettings?.() ?? {};
  let muted = false;

  return {
    sampleRate: context.sampleRate,
    channels: (settings.channelCount as number) || 1,
    stream,
    context,
    get muted() {
      return muted;
    },
    setMuted(next: boolean) {
      muted = next;
      // Both halves: the worklet stops emitting frames, and the track itself is
      // disabled so the browser's own indicator reflects reality.
      worklet.port.postMessage({ type: "mute", muted: next });
      stream.getAudioTracks().forEach((item) => {
        item.enabled = !next;
      });
    },
    async stop() {
      try {
        worklet.port.onmessage = null;
        source.disconnect();
        worklet.disconnect();
      } catch {
        // Already torn down.
      }
      stream.getTracks().forEach((item) => item.stop());
      if (context.state !== "closed") await context.close();
    },
  };
}
