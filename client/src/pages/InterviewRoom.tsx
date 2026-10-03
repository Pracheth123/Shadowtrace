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

const TOTAL_QUESTIONS = 10;

const toneClasses = {
  idle: "text-muted-foreground",
  busy: "text-warning",
  ok: "text-success",
  bad: "text-destructive",
} as const;

export type InterviewRoomProps = {
  session: ReturnType<typeof useSession>;
  onFinished: () => void;
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
  const answered = Math.min(TOTAL_QUESTIONS, session.turnCount);

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
    session.end();
    onFinished();
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-xl">Interview in progress</h2>
          <Badge variant={session.lane === "text" ? "muted" : "secondary"}>
            {session.lane === "text" ? "text lane" : "voice"}
          </Badge>
        </div>
        <p className={cn("text-sm", toneClasses[tone])} aria-live="polite">
          {statusText}
        </p>
      </header>

      <div className="flex flex-col gap-2">
        <Progress
          value={answered}
          max={TOTAL_QUESTIONS}
          label={`${answered} of ${TOTAL_QUESTIONS} questions completed`}
        />
        <p className="text-xs text-muted-foreground">
          {answered} of {TOTAL_QUESTIONS} questions completed
        </p>
      </div>

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
                active={session.running}
                onError={(message) =>
                  toastManager.add({
                    title: "Microphone unavailable",
                    description: message,
                    tone: "error",
                  })
                }
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
                onMouseDown={() => setTalking(true)}
                onMouseUp={() => setTalking(false)}
                onTouchStart={() => setTalking(true)}
                onTouchEnd={() => setTalking(false)}
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
          End interview
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
