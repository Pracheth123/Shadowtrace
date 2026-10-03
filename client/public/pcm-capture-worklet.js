/**
 * Microphone capture worklet: Float32 in, 16-bit PCM frames out.
 *
 * Runs on the audio render thread, so conversion never competes with React for
 * the main thread and a slow render cannot drop audio.
 *
 * Why a worklet and not MediaRecorder: MediaRecorder produces WebM/Opus
 * containers. The session socket is configured for raw PCM16 and tells Deepgram
 * `encoding=linear16`, so pushing Opus bytes down it would declare one format
 * and send another — the transcript would come back empty with no error.
 *
 * Resampling is deliberately *not* done here. The worklet emits PCM16 at the
 * device's own rate and the server converts, because the server already has a
 * stateful converter that holds resampler phase and partial frames across chunk
 * seams. Doing it in two places would mean two implementations to keep correct.
 *
 * Frames are batched to ~20 ms so the socket sees a steady, bounded message
 * rate instead of one message per 128-sample render quantum (~2.7 ms at 48 kHz,
 * which is ~375 messages/second).
 */

const FRAME_MS = 20;

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const frameMs = options?.processorOptions?.frameMs ?? FRAME_MS;
    // `sampleRate` is a global inside the worklet scope: the real device rate.
    this._frameSamples = Math.max(128, Math.round((sampleRate * frameMs) / 1000));
    this._buffer = new Int16Array(this._frameSamples);
    this._offset = 0;
    this._muted = false;

    this.port.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === "mute") {
        this._muted = Boolean(data.muted);
        // Drop whatever is half-assembled so unmuting does not emit a frame
        // that is partly from before the mute.
        this._offset = 0;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Fold channels to mono here: one channel is what gets declared, and
    // averaging keeps both mics' signal rather than discarding one.
    const channels = input.length;
    const samples = input[0].length;

    if (this._muted) {
      // Keep the node alive but emit nothing. Muting has to stop audio
      // leaving the machine, not merely stop it being acted on.
      return true;
    }

    for (let i = 0; i < samples; i += 1) {
      let value = 0;
      for (let c = 0; c < channels; c += 1) value += input[c][i];
      value /= channels;

      // Clamp before scaling: Int16Array wraps on overflow, turning a loud
      // sample into a large negative one — an audible click, and a word the
      // transcriber will miss.
      if (value > 1) value = 1;
      else if (value < -1) value = -1;

      this._buffer[this._offset] = value * 32767;
      this._offset += 1;

      if (this._offset === this._frameSamples) {
        // Transfer the buffer so there is no copy, then allocate a fresh one.
        const frame = this._buffer;
        this.port.postMessage({ type: "pcm", frame: frame.buffer }, [frame.buffer]);
        this._buffer = new Int16Array(this._frameSamples);
        this._offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
