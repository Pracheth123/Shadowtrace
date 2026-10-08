import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { MicIcon, SendIcon, SquareIcon, VideoIcon, VideoOffIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardPanel } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { toastManager } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { statusMessage, type useSession } from "@/lib/use-session";

// WaveSurfer only matters in the voice lane, so it is not in the entry bundle.
const Waveform = lazy(() =>
  import("@/components/Waveform").then((module) => ({
    default: module.Waveform,
  })),
);

const DIFFICULTY_LABEL: Record<string, string> = {
  coach: "Coach",
  realistic: "Realistic",
  panel: "Hard",
};

const toneClasses = {
  idle: "text-muted-foreground",
  busy: "text-warning",
  ok: "text-success",
  bad: "text-destructive",
} as const;

export type InterviewRoomProps = {
  session: ReturnType<typeof useSession>;
  onFinished: (sessionId: string) => void;
};

/** True when a key press is aimed at a text field, so shortcuts stay out of the way. */
function typingInField(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null;
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

export function InterviewRoom({ session, onFinished }: InterviewRoomProps) {
  const [typed, setTyped] = useState("");
  const [videoOn, setVideoOn] = useState(false);
  const [videoStream, setVideoStream] = useState<MediaStream | null>(null);
  const [talking, setTalking] = useState(false);
  const [showTranscript, setShowTranscript] = useState(true);
  const answerRef = useRef<HTMLInputElement | null>(null);
  const transcriptEnd = useRef<HTMLDivElement | null>(null);

  const { text: statusText, tone } = statusMessage(
    session.status,
    session.statusDetail,
  );
  const progress = session.progress;

  // Keyboard controls. Esc interrupts the interviewer, M mutes, T switches to
  // typing, / focuses the answer box. Ignored while typing in a field (except
  // Esc), so they never swallow an answer.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!session.running) return;
      if (event.key === "Escape" && session.lane === "voice") {
        event.preventDefault();
        session.bargeIn(talking);
        return;
      }
      if (typingInField(event) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "m" || event.key === "M") {
        if (session.lane === "voice") session.toggleMute();
      } else if (event.key === "t" || event.key === "T") {
        if (session.lane === "voice") session.switchToText();
      } else if (event.key === "/") {
        event.preventDefault();
        answerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session, talking]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [session.lines.length, session.interim]);

  // Local preview only. The stream is attached to a <video> element and
  // nothing else: no frame is encoded, sent to the server, or stored. Video is
  // never an input to any model or score.
  const toggleVideo = async () => {
    if (videoOn) {
      videoStream?.getTracks().forEach((track) => track.stop());
      setVideoStream(null);
      setVideoOn(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      setVideoStream(stream);
      setVideoOn(true);
    } catch {
      toastManager.add({
        title: "Camera unavailable",
        description: "The interview continues without it.",
        tone: "error",
      });
    }
  };

  const sendTyped = () => {
    if (!typed.trim()) return;
    session.answer(typed);
    setTyped("");
  };

  const finish = () => {
    videoStream?.getTracks().forEach((track) => track.stop());
    if (session.status !== "complete") session.end();
    onFinished(session.sessionId);
  };

  const voiceTrouble = session.lane === "voice" && (session.voiceProblem || session.micError);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xl">{session.isPractice ? "Targeted practice" : "Interview in progress"}</h2>
          <Badge variant={session.lane === "text" ? "muted" : "secondary"}>
            {session.lane === "text" ? "typing" : "voice"}
          </Badge>
          {session.lane === "voice" &&
            session.voiceInfo &&
            session.voiceInfo.provider !== "deepgram" && (
              <Badge variant="outline" title="Not a real provider session">
                mock audio
              </Badge>
            )}
          {session.interviewer === "deterministic" && (
            <Badge
              variant="outline"
              title="No interviewer model is configured; questions come from the interview plan"
            >
              plan-based interviewer (no AI model)
            </Badge>
          )}
          <Badge variant="muted">
            {DIFFICULTY_LABEL[session.intensity] ?? session.intensity} difficulty
          </Badge>
        </div>
        <p className={cn("text-sm", toneClasses[tone])} aria-live="polite">
          {statusText}
        </p>
      </header>

      {progress && progress.round_count > 1 && (
        <ol className="flex flex-wrap gap-2 text-xs" aria-label="Interview rounds">
          {progress.rounds.map((item, index) => (
            <li
              key={item.round}
              className={cn(
                "rounded-full border px-3 py-1",
                item.state === "active" && "border-primary text-primary",
                item.state === "done" && "border-border text-muted-foreground line-through",
                item.state === "pending" && "border-dashed border-border text-muted-foreground",
              )}
            >
              {index + 1}. {item.label}
            </li>
          ))}
        </ol>
      )}

      {progress ? (
        <div className="flex flex-col gap-2">
          <Progress
            value={progress.core_asked}
            max={Math.max(1, progress.core_total)}
            label={`${progress.round_label}: core question ${progress.core_asked} of ${progress.core_total}`}
          />
          <p className="text-xs text-muted-foreground">
            {progress.round_label}
            {progress.round_count > 1
              ? ` (round ${progress.round_index + 1} of ${progress.round_count})`
              : ""}
            : core question {progress.core_asked} of {progress.core_total} ·{" "}
            {progress.follow_ups} follow-up{progress.follow_ups === 1 ? "" : "s"} ·{" "}
            {progress.answers} answer{progress.answers === 1 ? "" : "s"} so far · about{" "}
            {Math.max(0, Math.round(progress.round_seconds_remaining / 60))} min left in this round
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Waiting for the first question…</p>
      )}

      {session.roundNotice && (
        <p className="rounded-md bg-muted px-3 py-2 text-sm" aria-live="polite">
          Handover: {session.roundNotice.fromLabel} → {session.roundNotice.toLabel}.{" "}
          <span className="text-muted-foreground">
            Carried forward: {session.roundNotice.carried} of your statements and{" "}
            {session.roundNotice.open} open question
            {session.roundNotice.open === 1 ? "" : "s"} — no ratings.
          </span>
        </p>
      )}

      {session.providerWarning && (
        <p className="rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">
          {session.providerWarning}
        </p>
      )}

      {voiceTrouble && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <p className="flex-1">
            {session.micError ? `Microphone problem: ${session.micError}` : `Voice problem: ${session.voiceProblem}`}
          </p>
          {session.micError && (
            <Button variant="outline" size="sm" onClick={() => void session.retryMic()}>
              <MicIcon /> Try the microphone again
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={session.switchToText}>
            Continue by typing
          </Button>
        </div>
      )}

      {session.status === "complete" && (
        <div className="flex items-center gap-3 rounded-md bg-muted px-3 py-2 text-sm">
          <p className="flex-1">
            {session.isPractice
              ? "The practice attempt has finished. Its feedback is being prepared."
              : "The interview has finished. Your feedback is being prepared."}
          </p>
          <Button size="sm" onClick={() => onFinished(session.sessionId)}>
            See your feedback
          </Button>
        </div>
      )}

      {/* The question the interviewer is asking. */}
      <Card>
        <CardPanel className="pt-5">
          <span className="mb-1.5 block text-xs uppercase tracking-wide text-muted-foreground">
            {session.utterance?.speaker ?? "Interviewer"}
          </span>
          <p className="min-h-[3.5rem] text-base leading-relaxed" aria-live="polite">
            {session.utterance?.text ??
              "Waiting for the interviewer to open the session…"}
          </p>
        </CardPanel>
      </Card>

      {/* Whose turn it is, and what the candidate is saying right now. */}
      {session.lane === "voice" && session.running && session.micActive && (
        <div
          className={cn(
            "rounded-lg border px-4 py-3",
            session.floor === "listening"
              ? "border-primary/60 bg-primary/5"
              : "border-dashed border-input",
          )}
          aria-live="polite"
        >
          {session.floor === "interviewer" && (
            <p className="text-sm text-muted-foreground">
              The interviewer is speaking. Your microphone is off until the question finishes.
            </p>
          )}

          {session.floor === "countdown" && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">
                  Transcript starts in{" "}
                  <span className="tabular-nums text-primary">{session.countdown}</span>…
                </p>
                <p className="text-xs text-muted-foreground">
                  Take a moment to gather your thoughts.
                </p>
              </div>
              <div className="flex items-center gap-2">
                {[5, 4, 3, 2, 1].map((n) => (
                  <span
                    key={n}
                    className={cn(
                      "flex h-7 w-7 items-center justify-center rounded-full border text-xs tabular-nums",
                      n === session.countdown
                        ? "border-primary bg-primary text-primary-foreground"
                        : n > session.countdown
                          ? "border-border text-muted-foreground/50"
                          : "border-border text-muted-foreground",
                    )}
                  >
                    {n}
                  </span>
                ))}
                <Button variant="outline" size="sm" onClick={session.startNow}>
                  Start now
                </Button>
              </div>
            </div>
          )}

          {session.floor === "listening" && (
            <>
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-xs uppercase tracking-wide text-primary">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                  {session.muted ? "Muted" : "Listening — you"}
                </span>
                <Button size="sm" onClick={session.finishAnswer} disabled={!session.interim}>
                  Done answering
                </Button>
              </div>
              <p className="min-h-[1.5rem] text-sm">
                {session.interim || (
                  <span className="text-muted-foreground">Start speaking whenever you're ready…</span>
                )}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Pausing to think is fine. Your answer ends after a few seconds of silence, or
                when you press Done answering.
              </p>
            </>
          )}
        </div>
      )}

      {session.lane === "voice" ? (
        <Card>
          <CardPanel className="pt-5">
            <Suspense
              fallback={
                <p className="py-6 text-center text-sm text-muted-foreground">
                  Loading microphone view…
                </p>
              }
            >
              <Waveform
                stream={session.micStream}
                active={session.running}
                muted={session.muted}
                onToggleMute={session.toggleMute}
              />
            </Suspense>
          </CardPanel>
        </Card>
      ) : (
        <Card>
          <CardPanel className="flex flex-col gap-2 pt-5">
            <label className="text-sm font-medium" htmlFor="typed-answer">
              Your answer
            </label>
            <div className="flex gap-2">
              <Input
                id="typed-answer"
                ref={answerRef}
                value={typed}
                placeholder="Type your answer, then press Enter"
                disabled={!session.running}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") sendTyped();
                }}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Send answer"
                disabled={!session.running || !typed.trim()}
                onClick={sendTyped}
                className="border border-border"
              >
                <SendIcon />
              </Button>
            </div>
          </CardPanel>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {session.lane === "voice" && (
          <>
            <Button
              variant="ghost"
              className="border border-border"
              disabled={!session.running}
              onClick={() => session.bargeIn(talking)}
              title="Interrupt the interviewer (Esc)"
            >
              Interrupt <kbd className="ml-1 text-xs text-muted-foreground">Esc</kbd>
            </Button>
            <Button
              variant={session.pushToTalk ? "secondary" : "ghost"}
              className="border border-border"
              disabled={!session.running}
              onClick={session.togglePushToTalk}
            >
              Push-to-talk {session.pushToTalk ? "on" : "off"}
            </Button>
            {session.pushToTalk && (
              <Button
                variant="outline"
                disabled={!session.running}
                onMouseDown={() => {
                  setTalking(true);
                  session.setTalking(true);
                }}
                onMouseUp={() => {
                  setTalking(false);
                  session.setTalking(false);
                }}
                onTouchStart={() => {
                  setTalking(true);
                  session.setTalking(true);
                }}
                onTouchEnd={() => {
                  setTalking(false);
                  session.setTalking(false);
                }}
                onKeyDown={(event) => {
                  if (event.key === " " && !talking) {
                    event.preventDefault();
                    setTalking(true);
                    session.setTalking(true);
                  }
                }}
                onKeyUp={(event) => {
                  if (event.key === " ") {
                    setTalking(false);
                    session.setTalking(false);
                  }
                }}
              >
                {talking ? "Listening…" : "Hold to talk (or hold Space)"}
              </Button>
            )}
            <Button
              variant="ghost"
              className="border border-border"
              disabled={!session.running}
              onClick={session.switchToText}
              title="Switch to typing (T)"
            >
              Switch to typing <kbd className="ml-1 text-xs text-muted-foreground">T</kbd>
            </Button>
          </>
        )}

        <Button
          variant="ghost"
          size="icon"
          aria-label={videoOn ? "Turn camera off" : "Turn camera on"}
          onClick={toggleVideo}
          className="border border-border"
        >
          {videoOn ? <VideoOffIcon /> : <VideoIcon />}
        </Button>

        <Button
          variant="ghost"
          className="ml-auto border border-border text-destructive hover:bg-destructive/10"
          onClick={finish}
        >
          <SquareIcon />
          {session.status === "complete" ? "Leave" : "End interview"}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Keys: <kbd>Esc</kbd> interrupt · <kbd>M</kbd> mute · <kbd>T</kbd> switch to typing ·{" "}
        <kbd>/</kbd> focus the answer box · <kbd>Enter</kbd> send a typed answer.
      </p>

      <Card>
        <CardPanel className="pt-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Transcript so far</h3>
            <Button variant="ghost" size="sm" onClick={() => setShowTranscript((on) => !on)} aria-expanded={showTranscript}>
              {showTranscript ? "Hide" : "Show"}
            </Button>
          </div>
          {showTranscript && (
            <div className="mt-2 flex max-h-72 flex-col gap-2 overflow-y-auto text-sm" aria-live="polite">
              {session.lines.length === 0 && !session.interim ? (
                <p className="text-muted-foreground">Nothing yet.</p>
              ) : (
                session.lines.map((line) => (
                  <p key={line.key}>
                    <span className={cn("font-medium", line.speaker === "you" ? "text-primary" : "")}>
                      {line.label}:
                    </span>{" "}
                    {line.text}
                  </p>
                ))
              )}
              {/* The answer in progress, phrase by phrase as it is recognised.
                  Replaced by the recorded line when the answer is accepted. */}
              {session.interim && (
                <p className="italic text-muted-foreground">
                  <span className="font-medium not-italic text-primary">You (speaking):</span>{" "}
                  {session.interim}
                </p>
              )}
              <div ref={transcriptEnd} />
              <p className="text-xs text-muted-foreground">
                Your lines are exactly what was recorded for feedback. If you interrupt
                the interviewer, the saved transcript keeps only what you heard.
              </p>
            </div>
          )}
        </CardPanel>
      </Card>

      {videoOn && (
        <div className="flex items-start gap-3">
          <video
            ref={(node) => {
              if (node && videoStream) node.srcObject = videoStream;
            }}
            autoPlay
            playsInline
            muted
            className="w-56 rounded-lg border border-border"
          />
          <p className="max-w-xs text-xs text-muted-foreground">
            Display only. Nothing is sent to the server, and video is never an
            input to any model or score.
          </p>
        </div>
      )}
    </div>
  );
}
