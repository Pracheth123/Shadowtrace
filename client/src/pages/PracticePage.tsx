import { useCallback, useEffect, useState } from "react";
import { ClipboardListIcon, LoaderCircleIcon, PlayIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";
import {
  RadioGroupPrimitive,
  RadioPrimitive,
} from "@/components/ui/radio-group";
import { toastManager } from "@/components/ui/toast";
import {
  ApiError,
  HTTP_BASE,
  OUTCOME_LABEL,
  api,
  jsonBody,
  levelLabel,
  type Lane,
  type Practice,
  type PracticeComparison,
} from "@/lib/api";
import {
  segmentedControlItemVariants,
  segmentedControlRootClassName,
} from "@/lib/segmented-control";
import type { SessionOptions } from "@/lib/use-session";
import { isCaptureSupported } from "@/lib/audio-capture";
import { canSpeak, readMicPermission, voiceMessage, voiceState, type MicPermission } from "@/lib/voice-availability";

const itemClassName = segmentedControlItemVariants({ className: "grow", state: "checked" });

/**
 * One targeted practice: a single gap from a stored report, a short attempt
 * through the ordinary interview runtime, and a before-and-after comparison on
 * that dimension only. Everything shown here is read back from the server, so
 * it survives a reload.
 */

export type PracticePageProps = {
  practiceId: string;
  onStart: (options: SessionOptions) => void;
  onOpenSession: (sessionId: string) => void;
  onOpenPractice: (practiceId: string) => void;
  onBack: () => void;
};

export function PracticePage({ practiceId, onStart, onOpenSession, onOpenPractice, onBack }: PracticePageProps) {
  const [practice, setPractice] = useState<Practice | null>(null);
  const [error, setError] = useState("");
  const [lane, setLane] = useState<Lane>("text");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [micPermission, setMicPermission] = useState<MicPermission>("unknown");

  useEffect(() => {
    let active = true;
    fetch(`${HTTP_BASE}/health`)
      .then((response) => { if (!response.ok) throw new Error("unavailable"); return response.json(); })
      .then((body) => { if (active) setHealth(body); })
      .catch(() => { if (active) setHealthFailed(true); });
    void readMicPermission().then((result) => { if (active) setMicPermission(result.state); });
    return () => { active = false; };
  }, []);

  const voice = voiceState(health, healthFailed, {
    captureSupported: isCaptureSupported(),
    secureContext: window.isSecureContext,
    micPermission,
  });
  const voiceAvailable = canSpeak(voice);
  useEffect(() => {
    if (!voiceAvailable && lane === "voice") setLane("text");
  }, [voiceAvailable, lane]);

  const load = useCallback(async () => {
    try {
      const view = await api<Practice>(`/api/practice/${practiceId}`);
      setPractice(view);
      return view;
    } catch (err) {
      setError((err as ApiError).status === 404 ? "This practice was not found." : (err as Error).message);
      return null;
    }
  }, [practiceId]);

  useEffect(() => {
    let timer = 0;
    const tick = async () => {
      const view = await load();
      if (view?.comparisons.some((c) => c.outcome === "pending")) timer = window.setTimeout(tick, 2000);
    };
    void tick();
    return () => window.clearTimeout(timer);
  }, [load]);

  if (error) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col items-start gap-3">
        <p className="notice w-full" data-tone="error" role="alert">{error}</p>
        <Button variant="outline" onClick={onBack}>Back to history</Button>
      </div>
    );
  }
  if (!practice) {
    return (
      <p className="notice mx-auto max-w-2xl items-center" data-tone="info" role="status">
        <LoaderCircleIcon className="animate-spin" aria-hidden="true" /> Loading your practice…
      </p>
    );
  }

  const showChecklist = async () => {
    setBusy(true);
    try {
      setPractice(await api<Practice>(`/api/practice/${practiceId}/coaching`, { method: "POST" }));
    } catch (err) {
      toastManager.add({ title: "Could not show the checklist", description: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const unaided = async () => {
    setBusy(true);
    try {
      const next = await api<Practice>("/api/practice", jsonBody({ parent_practice_id: practiceId, minutes: practice.minutes }));
      onOpenPractice(next.practice_id);
    } catch (err) {
      toastManager.add({ title: "Could not set up the variation", description: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
      <header className="workspace-title">
        <p className="eyebrow">ONE GAP. A FOCUSED ATTEMPT.</p>
        <div className="flex items-center justify-between gap-2">
          <h1>Practise: {practice.dimension_label}</h1>
          <Button variant="ghost" size="sm" onClick={onBack}>
            Back to history
          </Button>
        </div>
        <p className="mt-3 font-mono text-[13px] text-muted-foreground">
          {practice.round_label} round · {practice.minutes} minutes ·{" "}
          {practice.mode === "unaided" ? "unaided variation (no checklist, different question)" : "coached practice"} ·
          rubric {practice.rubric_version}
        </p>
      </header>

      <section className="practice-target" aria-labelledby="practice-target-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="practice-target-heading" className="text-xl">What you are practising</h2>
          <Button variant="link" size="sm" className="h-auto p-0" onClick={() => onOpenSession(practice.parent.session_id)}>
            Open the original report
          </Button>
        </div>
        {practice.source_question && (
          <div className="finding-zone">
            <span className="meta-label">The question</span>
            <p className="finding-question">{practice.source_question}</p>
          </div>
        )}
        <div className="finding-zone">
          <span className="meta-label">Your answer then</span>
          <blockquote className="finding-quote">“{practice.source_quote}”</blockquote>
        </div>
        <div className="finding-zone">
          <span className="meta-label">Feedback · interpretation, can be challenged</span>
          <p className="finding-interpretation">{practice.finding_explanation}</p>
        </div>
        <p className="text-sm text-muted-foreground">
          Level then: {levelLabel(practice.before.level ?? "insufficient_evidence")}. The attempt is evaluated against
          the same rubric and only this dimension is compared. A clearer answer here is about this one answer, not
          about readiness for a job.
        </p>
      </section>

      {practice.checklist_visible && (
        <Card className="report-section">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <ClipboardListIcon className="size-4" /> What to explain
            </CardTitle>
            <CardDescription>
              Built only from what you already said or supplied, plus prompts about how to explain. It adds no facts:
              use only what is true for you. Opening it labels your next attempt “coached”.
            </CardDescription>
          </CardHeader>
          <CardPanel>
            {practice.coached ? (
              <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
                {practice.checklist.map((item, index) => (
                  <li key={index}>
                    {item.text} <span className="text-xs text-muted-foreground">({item.source})</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Button variant="outline" onClick={showChecklist} disabled={busy}>
                Show the checklist
              </Button>
            )}
          </CardPanel>
        </Card>
      )}

      <Card className="report-section">
        <CardHeader>
          <CardTitle className="text-lg">Start an attempt</CardTitle>
          <CardDescription>
            A short session with the same interviewer role: two core questions on this competency, with at most two
            follow-ups each. {voiceAvailable ? "Choose typing or speaking." : "Typing is ready."}
          </CardDescription>
          <p id="practice-voice-status" className="voice-status" data-voice-state={voice.kind}>{voiceMessage(voice)}</p>
        </CardHeader>
        <CardPanel className="flex flex-col gap-3">
          <RadioGroupPrimitive
            aria-label="How you will answer"
            className={segmentedControlRootClassName}
            value={lane}
            onValueChange={(next) => setLane(next as Lane)}
            name="practice-lane"
          >
            <RadioPrimitive.Root className={itemClassName} value="text">
              Type
            </RadioPrimitive.Root>
            <RadioPrimitive.Root className={itemClassName} value="voice" disabled={!voiceAvailable} aria-describedby="practice-voice-status">
              Speak
            </RadioPrimitive.Root>
          </RadioGroupPrimitive>
          <label className="flex items-start gap-2 text-sm text-foreground">
            <input type="checkbox" className="mt-1 size-[18px] shrink-0 accent-[var(--cobalt)]" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            I understand my answers are sent to Groq (text model) and, if I speak, to Deepgram (speech), for processing.
          </label>
          <Button
            className="self-start"
            disabled={!consent}
            onClick={() =>
              onStart({ practiceId: practice.practice_id, lane, intensity: "realistic", consent: true })
            }
          >
            <PlayIcon /> Start {practice.coached ? "coached " : ""}attempt
          </Button>
        </CardPanel>
      </Card>

      {practice.comparisons.length > 0 && (
        <Card className="report-section">
          <CardHeader>
            <CardTitle className="text-lg">Before and after</CardTitle>
            <CardDescription>
              One dimension, one short attempt. This does not show general readiness, and overall scores are not compared.
            </CardDescription>
          </CardHeader>
          <CardPanel className="flex flex-col gap-4">
            {practice.comparisons.map((cmp, index) => (
              <ComparisonRow key={cmp.session_id ?? index} index={index} cmp={cmp} onOpenSession={onOpenSession} />
            ))}
            {practice.offer_unaided_variation && (
              <div className="notice flex-col" data-tone="info">
                <p>
                  Optional, later: try an <strong>unaided variation</strong> — a differently worded question on the
                  same competency, with no checklist — to check the improvement transfers rather than being memorised.
                </p>
                <Button className="mt-2" size="sm" variant="outline" onClick={unaided} disabled={busy}>
                  Set up an unaided variation
                </Button>
              </div>
            )}
          </CardPanel>
        </Card>
      )}
    </div>
  );
}

function ComparisonRow({
  cmp,
  index,
  onOpenSession,
}: {
  cmp: PracticeComparison;
  index: number;
  onOpenSession: (id: string) => void;
}) {
  return (
    <article className="flex flex-col gap-3 border-b border-border pb-4 text-sm last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base">Attempt {index + 1}</h3>
        <span className="outcome-tag" data-outcome={cmp.outcome}>
          {cmp.outcome_label || OUTCOME_LABEL[cmp.outcome]}
        </span>
        {cmp.coached && <Badge variant="outline" size="sm">coached — you saw a checklist</Badge>}
        {cmp.mode === "unaided" && <Badge variant="outline" size="sm">unaided</Badge>}
        <span className="meta-label">{cmp.lane === "text" ? "typed" : "spoken"}</span>
      </div>
      <p className="text-muted-foreground">{cmp.reason}</p>
      <div className="compare-grid">
        <section className="compare-side" data-side="before" aria-label={`Attempt ${index + 1}: before`}>
          <p className="meta-label">Before · {levelLabel(cmp.before?.level ?? "insufficient_evidence")}</p>
          {cmp.before?.quote ? <blockquote>“{cmp.before.quote}”</blockquote> : <p className="text-muted-foreground">No quote recorded.</p>}
        </section>
        <section className="compare-side" data-side="after" aria-label={`Attempt ${index + 1}: after`}>
          <p className="meta-label">
            After · {cmp.after ? levelLabel(cmp.after.level ?? "insufficient_evidence") : "not evaluated yet"}
          </p>
          {cmp.after?.citations?.slice(0, 1).map((c, i) => (
            <blockquote key={i}>“{c.quote}”</blockquote>
          ))}
          {cmp.after?.findings?.slice(0, 1).map((f, i) => (
            <p key={i} className="text-muted-foreground">{f.explanation}</p>
          ))}
          {!cmp.after && <p className="text-muted-foreground">The attempt’s feedback is still being prepared.</p>}
        </section>
      </div>
      <ul className="list-disc pl-5 text-sm text-muted-foreground">
        {cmp.limitations.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <Button variant="link" size="sm" className="h-auto self-start p-0" onClick={() => onOpenSession(cmp.session_id)}>
        Open this attempt's feedback
      </Button>
    </article>
  );
}
