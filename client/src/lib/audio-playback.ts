/**
 * Playback of synthesised speech, with honest accounting.
 *
 * Three things the previous implementation got wrong, and this exists to fix:
 *
 * 1. **It assumed 16 kHz.** Both the `AudioContext` and every `createBuffer`
 *    call hard-coded 16000, while Deepgram produces 24 kHz. Playing 24 kHz
 *    samples as if they were 16 kHz stretches them: the interviewer speaks
 *    about a third too slowly and a semitone too low. Each chunk now carries
 *    its own rate and is decoded with it.
 *
 * 2. **It acknowledged audio on arrival.** `playedMs` advanced the moment a
 *    chunk was received, so the server believed the candidate had heard audio
 *    that had not started playing. Since `playback_ack.played_ms` is what the
 *    truncation contract cuts the transcript at, an interruption recorded words
 *    nobody heard. Progress now comes from `AudioContext.currentTime` — the
 *    audio clock — compared against when each buffer was actually scheduled.
 *
 * 3. **It started every chunk immediately.** Overlapping sources played the
 *    reply on top of itself. Chunks are now scheduled back to back on the
 *    audio timeline.
 */

export type ChunkMeta = {
  utteranceId: string;
  encoding: string;
  sampleRate: number;
  seq: number;
  bytes: number;
};

export type PlaybackProgress = {
  utteranceId: string;
  /** Milliseconds confirmed AUDIBLE, from the audio clock. */
  playedMs: number;
  /** Milliseconds handed to the device. Always >= playedMs. */
  scheduledMs: number;
  finished: boolean;
};

export type PlaybackConfig = {
  onProgress: (progress: PlaybackProgress) => void;
  onError?: (message: string) => void;
};

/** Small lead so the first buffer is not scheduled in the past. */
const SCHEDULE_LEAD_S = 0.05;

// Returns a Float32Array backed by a plain ArrayBuffer. `copyToChannel` will
// not accept one that might be backed by a SharedArrayBuffer, which is what the
// bare `Float32Array` type allows under current lib.dom typings.
function pcm16ToFloat32(buffer: ArrayBuffer): Float32Array<ArrayBuffer> {
  const view = new Int16Array(buffer);
  const out = new Float32Array(new ArrayBuffer(view.length * 4));
  for (let i = 0; i < view.length; i += 1) out[i] = view[i] / 32768;
  return out as Float32Array<ArrayBuffer>;
}

export class PlaybackQueue {
  private context: AudioContext | null = null;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private utteranceId = "";
  /** Where the next chunk starts on the audio timeline. */
  private nextStartAt = 0;
  /** Audio-clock time at which the current utterance began. */
  private startedAt: number | null = null;
  private scheduledMs = 0;
  private ticker: number | null = null;
  private closed = false;

  constructor(private readonly config: PlaybackConfig) {}

  /**
   * Create and unlock the output context.
   *
   * Must be called from the user's Start gesture: a context created outside one
   * begins `suspended`, and every scheduled buffer is silent until resumed —
   * which looks exactly like a broken provider.
   */
  async unlock(): Promise<void> {
    if (!this.context) this.context = new AudioContext();
    if (this.context.state === "suspended") await this.context.resume();
  }

  get ready(): boolean {
    return this.context !== null && this.context.state === "running";
  }

  get currentUtterance(): string {
    return this.utteranceId;
  }

  beginUtterance(utteranceId: string): void {
    if (this.utteranceId && this.utteranceId !== utteranceId) this.stopAll();
    this.utteranceId = utteranceId;
    this.scheduledMs = 0;
    this.startedAt = null;
    this.nextStartAt = 0;
    this.startTicker();
  }

  /**
   * Schedule one chunk, decoded with the rate its metadata declares.
   *
   * A chunk for an utterance that is no longer current is dropped: it is late
   * audio from a line the candidate interrupted.
   */
  enqueue(meta: ChunkMeta, audio: ArrayBuffer): void {
    if (this.closed || !this.context) return;
    if (meta.utteranceId !== this.utteranceId) return;
    if (meta.encoding !== "linear16") {
      this.config.onError?.(
        `Unsupported audio encoding "${meta.encoding}" from the server.`,
      );
      return;
    }

    const samples = pcm16ToFloat32(audio);
    if (samples.length === 0) return;

    const context = this.context;
    const buffer = context.createBuffer(1, samples.length, meta.sampleRate);
    buffer.copyToChannel(samples, 0);

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);

    // Back to back on the audio timeline. `max` covers the first chunk and any
    // case where decoding fell behind the clock.
    const startAt = Math.max(
      context.currentTime + SCHEDULE_LEAD_S,
      this.nextStartAt,
    );
    if (this.startedAt === null) this.startedAt = startAt;
    source.start(startAt);
    this.nextStartAt = startAt + buffer.duration;
    this.scheduledMs += buffer.duration * 1000;

    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      // Do NOT stop reporting here. Synthesis finishing is not playback
      // finishing, and the final acknowledgement has to reflect the audio
      // clock rather than the last chunk's arrival.
      if (this.sources.size === 0) this.report();
    };
  }

  /** Milliseconds actually audible so far, from the audio clock. */
  playedMs(): number {
    if (!this.context || this.startedAt === null) return 0;
    const elapsed = (this.context.currentTime - this.startedAt) * 1000;
    return Math.max(0, Math.min(this.scheduledMs, Math.round(elapsed)));
  }

  private report(): void {
    if (!this.utteranceId) return;
    const played = this.playedMs();
    this.config.onProgress({
      utteranceId: this.utteranceId,
      playedMs: played,
      scheduledMs: Math.round(this.scheduledMs),
      finished: this.sources.size === 0 && played >= Math.round(this.scheduledMs),
    });
  }

  private startTicker(): void {
    if (this.ticker !== null) return;
    this.ticker = window.setInterval(() => this.report(), 100);
  }

  private stopTicker(): void {
    if (this.ticker !== null) {
      window.clearInterval(this.ticker);
      this.ticker = null;
    }
  }

  /**
   * Barge-in, browser half.
   *
   * The provider's `Clear` stops it generating more, but cannot recall audio
   * already delivered — that audio is in these sources, scheduled on the audio
   * timeline, and will play unless stopped here. Returns the last audible
   * position so the caller can report where the candidate actually got to.
   */
  stopAll(): number {
    const played = this.playedMs();
    for (const source of this.sources) {
      try {
        source.onended = null;
        source.stop();
        source.disconnect();
      } catch {
        // Already finished.
      }
    }
    this.sources.clear();
    this.nextStartAt = this.context ? this.context.currentTime : 0;
    this.scheduledMs = played;
    this.stopTicker();
    return played;
  }

  endUtterance(): void {
    // Synthesis is done; playback may not be. Keep the ticker running until the
    // queue actually drains so the last acknowledgement is truthful.
    if (this.sources.size === 0) {
      this.report();
      this.stopTicker();
    }
  }

  async close(): Promise<void> {
    this.closed = true;
    this.stopAll();
    this.stopTicker();
    if (this.context && this.context.state !== "closed") {
      await this.context.close();
    }
    this.context = null;
  }
}
