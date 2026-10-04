import { lazy, Suspense, useState } from "react";
import { SendIcon, SquareIcon, VideoIcon, VideoOffIcon } from "lucide-react";

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

export function InterviewRoom({ session, onFinished }: InterviewRoomProps) {
  const [typed, setTyped] = useState("");
  const [videoOn, setVideoOn] = useState(false);
  const [videoStream, setVideoStream] = useState<MediaStream | null>(null);
  const [talking, setTalking] = useState(false);

  const { text: statusText, tone } = statusMessage(
    session.status,
    session.statusDetail,
  );
  const progress = session.progress;

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

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-xl">Interview in progress</h2>
          <Badge variant={session.lane === "text" ? "muted" : "secondary"}>
            {session.lane === "text" ? "text lane" : "voice"}
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
              development interviewer
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
            {progress.answers} answer{progress.answers === 1 ? "" : "s"} so far
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

      {session.voiceProblem && session.lane === "voice" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <p className="flex-1">Voice problem: {session.voiceProblem}</p>
          <Button variant="outline" size="sm" onClick={session.switchToText}>
            Continue by typing
          </Button>
        </div>
      )}

      {session.status === "complete" && (
        <div className="flex items-center gap-3 rounded-md bg-muted px-3 py-2 text-sm">
          <p className="flex-1">The interview has finished. Your feedback is being prepared.</p>
          <Button size="sm" onClick={() => onFinished(session.sessionId)}>
            See your feedback
          </Button>
        </div>
      )}

      {/* The question the interviewer is asking. In panel mode the speaker
          label names which voice holds the floor. */}
      <Card>
        <CardPanel className="pt-5">
          <span className="mb-1.5 block text-xs uppercase tracking-wide text-muted-foreground">
            {session.utterance?.speaker ?? "Interviewer"}
          </span>
          <p className="min-h-[3.5rem] text-base leading-relaxed">
            {session.utterance?.text ??
              "Waiting for the interviewer to open the session…"}
          </p>
        </CardPanel>
      </Card>

      {/* What the candidate is saying right now, straight from interim STT. */}
      {session.lane === "voice" && (session.interim || session.micActive) && (
        <div className="rounded-lg border border-dashed border-input px-4 py-3">
          <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
            You
          </span>
          <p className="min-h-[1.5rem] text-sm text-muted-foreground">
            {session.interim || "Listening…"}
          </p>
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
            >
              Interrupt
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
              >
                {talking ? "Listening…" : "Hold to talk"}
              </Button>
            )}
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
