import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckIcon,
  CircleAlertIcon,
  DownloadIcon,
  FlagIcon,
  LoaderCircleIcon,
  RotateCwIcon,
  TargetIcon,
} from "lucide-react";

import { InterviewHistory, type InterviewRow } from "@/components/InterviewHistory";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { toastManager } from "@/components/ui/toast";
import {
  ApiError,
  OUTCOME_LABEL,
  ROUNDS,
  api,
  claimLabel,
  download,
  forgetGuest,
  jsonBody,
  levelLabel,
  percent,
  type ClaimFinding,
  type Dispute,
  type EvaluationJob,
  type History,
  type Plan,
  type Practice,
  type Recommendation,
  type ReportFinding,
  type RoundJob,
  type RoundResult,
  type SessionMeta,
  type SessionReport,
} from "@/lib/api";

/**
 * Results for one session, read from the server by its session id.
 *
 * No sample data. While rounds are being evaluated each one shows its own
 * state, and a finished round's feedback is readable before the others are
 * done; the overall report stays visibly incomplete until every round has
 * settled. A failed round can be retried on its own — finished rounds are
 * kept. Feedback leads with what to practise, each item showing the question,
 * your answer, why, what it cannot tell you, and what to do next; every item
 * can be disputed, and an assessed gap can be practised on its own.
 */

const DIFFICULTY: Record<string, string> = { coach: "Coach", realistic: "Realistic", panel: "Hard" };

const ROUND_STATE: Record<RoundJob["state"], { label: string; tone: string }> = {
  queued: { label: "Queued", tone: "text-muted-foreground" },
  running: { label: "Evaluating", tone: "text-warning" },
  complete: { label: "Complete", tone: "text-success" },
  failed: { label: "Failed", tone: "text-destructive" },
  not_assessed: { label: "Not assessed", tone: "text-muted-foreground" },
};

const DISPUTE_STATUS: Record<Dispute["status"], string> = {
  open: "You disputed this",
  reassessed: "Re-checked — still disputed",
  withdrawn: "Dispute withdrawn",
  accepted_revision: "You accepted the revised assessment",
};

export type ResultsDashboardProps = {
  sessionId: string | null;
  onOpenSession: (sessionId: string) => void;
  onOpenPractice: (practiceId: string) => void;
  onStartAnother: () => void;
};

export function ResultsDashboard({ sessionId, onOpenSession, onOpenPractice, onStartAnother }: ResultsDashboardProps) {
  return (
    <div className="results-layout mx-auto flex w-full flex-col gap-6">
      <header className="results-heading">
        <div><p className="eyebrow">KEEP THE CONVERSATION GOING</p><h1>{sessionId ? "Your answers, in perspective." : "Every attempt has a next step."}</h1><p>{sessionId ? "Review the evidence. Choose one thing to work on next." : "Revisit your interviews, compare attempts, and find your next practice."}</p></div>
        <Button variant="outline" onClick={onStartAnother}><RotateCwIcon /> New interview</Button>
      </header>
      <Tabs defaultValue={sessionId ? "report" : "history"}>
        <TabsList>
          {sessionId && <TabsTab value="report">This session</TabsTab>}
          <TabsTab value="history">History</TabsTab>
          <TabsTab value="plan">Next practice</TabsTab>
        </TabsList>
        {sessionId && (
          <TabsPanel value="report">
            {/* Keyed by id so a different session never shows the previous one's data. */}
            <SessionReportView key={sessionId} sessionId={sessionId} onOpenPractice={onOpenPractice} onOpenSession={onOpenSession} />
          </TabsPanel>
        )}
        <TabsPanel value="history">
          <HistoryView onOpenSession={onOpenSession} onOpenPractice={onOpenPractice} />
        </TabsPanel>
        <TabsPanel value="plan">
          <PlanView onOpenPractice={onOpenPractice} onOpenSession={onOpenSession} />
        </TabsPanel>
      </Tabs>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onStartAnother}>
          Start another interview
        </Button>
      </div>
      <section className="danger-zone" aria-labelledby="your-data-heading">
        <div className="max-w-xl">
          <h2 id="your-data-heading">Your data</h2>
          <p>
            Deleting removes your intakes, transcripts, reports, disputes and practice records from this app and
            ends this browser&apos;s guest key. Copies held by Groq or Deepgram under their own policies cannot be
            deleted from here.
          </p>
        </div>
        <DeleteMyData />
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One session: progress, then the report
// ---------------------------------------------------------------------------

function SessionReportView({
  sessionId,
  onOpenPractice,
  onOpenSession,
}: {
  sessionId: string;
  onOpenPractice: (id: string) => void;
  onOpenSession: (id: string) => void;
}) {
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [job, setJob] = useState<EvaluationJob | null>(null);
  const [report, setReport] = useState<SessionReport | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const timer = useRef(0);

  const load = useCallback(async (): Promise<boolean> => {
    try {
      const [current, progress] = await Promise.all([
        api<SessionMeta>(`/api/sessions/${sessionId}`),
        api<EvaluationJob>(`/api/sessions/${sessionId}/evaluation`),
      ]);
      setMeta(current);
      setJob(progress);
      if (current.state === "complete") {
        setReport(await api<SessionReport>(`/api/sessions/${sessionId}/report`));
        return true;
      }
      return current.state === "failed";
    } catch (err) {
      setError((err as ApiError).status === 404 ? "This session was not found." : (err as Error).message);
      return true;
    }
  }, [sessionId]);

  const poll = useCallback(() => {
    window.clearTimeout(timer.current);
    const tick = async () => {
      const done = await load();
      if (!done) timer.current = window.setTimeout(tick, 1500);
    };
    void tick();
  }, [load]);

  useEffect(() => {
    poll();
    return () => window.clearTimeout(timer.current);
  }, [poll]);

  const retry = async () => {
    setRetrying(true);
    try {
      await api(`/api/sessions/${sessionId}/evaluation/retry`, jsonBody({}));
      poll();
    } catch (err) {
      toastManager.add({ title: "Retry refused", description: (err as Error).message, tone: "error" });
    } finally {
      setRetrying(false);
    }
  };

  if (error) return <Notice text={error} />;
  if (!meta || !job) return <Notice text="Loading this session…" busy />;

  const practiceBanner = meta.kind === "practice" && meta.practice && (
    <p className="notice" data-tone="info">
      Practice attempt on <strong>{meta.practice.dimension_label}</strong>
      {meta.practice.coached ? " (coached — you saw a checklist)" : meta.practice.mode === "unaided" ? " (unaided variation)" : ""}.{" "}
      <Button variant="link" size="sm" className="h-auto p-0" onClick={() => onOpenPractice(meta.practice!.practice_id)}>
        See the before-and-after comparison
      </Button>
    </p>
  );

  if (meta.state === "complete" && report) {
    return (
      <div className="flex flex-col gap-4">
        {practiceBanner}
        <ReportBody
          report={report}
          meta={meta}
          onChange={setReport}
          onOpenPractice={onOpenPractice}
          onOpenSession={onOpenSession}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {practiceBanner}
      <EvaluationProgress job={job} meta={meta} onRetry={retry} retrying={retrying} sessionId={sessionId} />
      {job.rounds
        .filter((round) => round.result)
        .map((round) => (
          <RoundCard key={round.round} round={round.result!} />
        ))}
    </div>
  );
}

function EvaluationProgress({
  job,
  meta,
  onRetry,
  retrying,
  sessionId,
}: {
  job: EvaluationJob;
  meta: SessionMeta;
  onRetry: () => void;
  retrying: boolean;
  sessionId: string;
}) {
  const failed = job.rounds.filter((r) => r.state === "failed");
  const waiting = ["finalising", "evaluation_queued", "evaluating", "active"].includes(job.state);
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {waiting && <LoaderCircleIcon className="size-4 animate-spin" />}
          {job.state === "failed" ? "Some feedback could not be produced" : "Preparing your feedback"}
        </CardTitle>
        <CardDescription>
          {job.state === "active"
            ? "The interview is still in progress."
            : job.rounds_total
              ? `${job.rounds_settled} of ${job.rounds_total} round${job.rounds_total === 1 ? "" : "s"} finished`
              : "Saving your transcript…"}
          {job.elapsed_s != null && ` · ${Math.round(job.elapsed_s)} s since the interview ended`}
          . The overall report appears when every round has finished; finished rounds are shown below as they arrive.
        </CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3">
        {job.rounds.length > 0 && (
          <ul className="eval-rounds" aria-live="polite">
            {job.rounds.map((round) => (
              <li key={round.round} className="eval-round" data-state={round.state}>
                <span className="font-semibold">{round.label}</span>
                <span className={ROUND_STATE[round.state].tone}>
                  {round.state === "running" && <LoaderCircleIcon className="mr-1 inline size-3 animate-spin" />}
                  {ROUND_STATE[round.state].label}
                </span>
                {round.elapsed_s != null && round.state !== "running" && (
                  <span className="text-xs text-muted-foreground">{round.elapsed_s.toFixed(1)} s</span>
                )}
                {round.cache_hit && <span className="text-xs text-muted-foreground">(reused, no new model call)</span>}
                {round.fallback_used && <span className="text-xs text-warning">fallback model {round.model_used}</span>}
                {round.state === "failed" && (
                  <span className="w-full text-sm text-destructive">
                    {round.error} {round.recovery}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {job.state === "failed" && (
          <>
            <p className="text-sm text-muted-foreground">
              {job.error} {failed.length > 0 ? "Retrying runs only the failed round(s); finished rounds are kept." : job.recovery}
              {" "}No report is shown rather than a substitute.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={onRetry} disabled={retrying}>
                <RotateCwIcon /> {failed.length > 0 ? `Retry ${failed.length} failed round${failed.length === 1 ? "" : "s"}` : "Retry evaluation"}
              </Button>
              {job.transcript_available && <TranscriptButton sessionId={sessionId} />}
            </div>
          </>
        )}
        {job.state !== "failed" && job.transcript_available && (
          <div>
            <TranscriptButton sessionId={sessionId} />
          </div>
        )}
        {(meta.degraded_turns?.length ?? 0) > 0 && (
          <p className="text-xs text-warning">
            {meta.degraded_turns!.length} question(s) in this session came from the interview plan because the
            interviewer model did not respond usefully.
          </p>
        )}
      </CardPanel>
    </Card>
  );
}

function Notice({ text, busy }: { text: string; busy?: boolean }) {
  return (
    <p className="notice items-center" data-tone={busy ? "info" : "error"} role={busy ? "status" : "alert"}>
      {busy && <LoaderCircleIcon className="animate-spin" aria-hidden="true" />}
      {text}
    </p>
  );
}

function TranscriptButton({ sessionId }: { sessionId: string }) {
  return (
    <Button
      variant="outline"
      onClick={() =>
        download(`/api/sessions/${sessionId}/transcript`, `transcript-${sessionId}.txt`).catch((e) =>
          toastManager.add({ title: "Download failed", description: (e as Error).message, tone: "error" }),
        )
      }
    >
      <DownloadIcon /> Transcript
    </Button>
  );
}

// ---------------------------------------------------------------------------
// The report
// ---------------------------------------------------------------------------

function ReportBody({
  report,
  meta,
  onChange,
  onOpenPractice,
}: {
  report: SessionReport;
  meta: SessionMeta;
  onChange: (report: SessionReport) => void;
  onOpenPractice: (id: string) => void;
  onOpenSession: (id: string) => void;
}) {
  const cfg = report.config;
  const roundLabel = ROUNDS.find((r) => r.value === cfg.round)?.label ?? cfg.round;
  const [showAll, setShowAll] = useState(false);

  const findings = useMemo(() => report.rounds.flatMap((r) => r.findings), [report]);
  const gaps = findings.filter((f) => f.polarity === "gap");
  const strengths = findings.filter((f) => f.polarity === "strength");
  const priorityIds = report.priority_findings ?? [];
  const ranked = [...gaps].sort((a, b) => (a.priority || 99) - (b.priority || 99));
  const first = priorityIds.length
    ? ranked.filter((f) => f.finding_id && priorityIds.includes(f.finding_id))
    : ranked.slice(0, 3);
  const rest = [...ranked.filter((f) => !first.includes(f)), ...strengths];
  const isPractice = report.session_kind === "practice";

  const refresh = async () => {
    onChange(await api<SessionReport>(`/api/sessions/${report.session_id}/report`));
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="report-head">
        <h2 className="text-3xl">{isPractice ? "Practice feedback" : "Your feedback"}</h2>
        <p className="meta-line">
          {cfg.target_role} · {roundLabel} · {report.lane === "text" ? "typed" : "spoken"} ·{" "}
          {DIFFICULTY[cfg.intensity] ?? cfg.intensity} difficulty ·{" "}
          {new Date(report.session_started_at || report.created_at).toLocaleString()}
        </p>
        <p className="text-sm text-muted-foreground">{report.coverage_note}</p>
      </header>

      {!report.evaluator.is_assessment && (
        <p className="notice" data-tone="warning">
          <span><strong>Development evaluation.</strong> No evaluation model was configured, so
          this report was produced by a placeholder. It is not an assessment of you.</span>
        </p>
      )}
      {meta.interviewer === "deterministic" && (
        <p className="notice" data-tone="info">
          The questions in this session came from the plan-based interviewer, not an AI model.
        </p>
      )}
      <p className="text-sm text-muted-foreground">
        How to read this: each finding shows the question, <strong className="text-foreground">your own words</strong>{" "}
        (quoted from the transcript), the evaluator&apos;s <strong className="text-foreground">interpretation</strong> — which
        can be wrong and can be challenged — and one <strong className="text-foreground">practice step</strong>.
      </p>

      <Card className="report-section">
        <CardHeader>
          <CardTitle className="text-lg">What to practise first</CardTitle>
          <CardDescription>
            {gaps.length === 0
              ? "No specific gaps were identified with quoted evidence in this session."
              : `The ${Math.min(3, first.length)} most useful of ${gaps.length} gap${gaps.length === 1 ? "" : "s"}. Each shows the question, your answer, the reasoning, its limits and a next step.`}
          </CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-5">
          {first.map((finding) => (
            <FindingCard
              key={finding.finding_id || finding.quote}
              finding={finding}
              report={report}
              onChange={refresh}
              onOpenPractice={onOpenPractice}
            />
          ))}
          {rest.length > 0 && (
            <Button variant="ghost" className="self-start" onClick={() => setShowAll((on) => !on)} aria-expanded={showAll}>
              {showAll ? "Hide the other findings" : `Show the other ${rest.length} finding${rest.length === 1 ? "" : "s"} (including strengths)`}
            </Button>
          )}
          {showAll &&
            rest.map((finding) => (
              <FindingCard
                key={finding.finding_id || finding.quote}
                finding={finding}
                report={report}
                onChange={refresh}
                onOpenPractice={onOpenPractice}
              />
            ))}
        </CardPanel>
      </Card>

      {report.rounds.map((round) => (
        <RoundCard key={round.round} round={round} contested={report.contested?.dimensions ?? []} />
      ))}

      <Card className="report-section">
        <CardHeader>
          <CardTitle className="flex flex-wrap items-baseline gap-3 text-lg">
            Overall indicator <span className="meta-label">experimental</span>
            <span className="score-figure tabular-nums">
              {report.overall.score == null ? "Not scored" : `${percent(report.overall.score)}/100`}
            </span>
          </CardTitle>
          <CardDescription>{report.overall.note}</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            {report.overall.disclaimer} Read the dimension levels and the answers behind them first; this
            number only summarises them.
          </p>
          <p className="meta-label mt-2">Downloads</p>
          <div className="downloads">
            <TranscriptButton sessionId={report.session_id} />
            <Button
              variant="outline"
              onClick={() =>
                download(
                  `/api/sessions/${report.session_id}/scorecard`,
                  `scorecard-${report.session_id}.html`,
                ).catch((e) =>
                  toastManager.add({ title: "Download failed", description: (e as Error).message, tone: "error" }),
                )
              }
            >
              <DownloadIcon /> Scorecard
            </Button>
          </div>
        </CardPanel>
      </Card>

      <ClaimsCard claims={report.claims} meanings={report.claim_status_meanings} />

      {(report.revisions?.length ?? 0) > 0 && <RevisionsCard report={report} />}

      {report.disagreements.length > 0 && (
        <Card className="report-section">
          <CardHeader>
            <CardTitle className="text-lg">Where the evaluators disagreed</CardTitle>
            <CardDescription>Both positions are shown; neither is averaged away.</CardDescription>
          </CardHeader>
          <CardPanel className="flex flex-col gap-3 text-sm">
            {report.disagreements.map((item, index) => (
              <div key={index}>
                <p className="font-medium">{item.subject}</p>
                <p className="text-muted-foreground">{item.summary}</p>
                <ul className="mt-1 flex flex-col gap-1">
                  {item.positions.map((pos, i) => (
                    <li key={i}>
                      <span className="font-medium">{pos.perspective.replace("_", " ")}</span>: {claimLabel(pos.status)} — {pos.reason}
                      {pos.quote && <Quote text={pos.quote} turn={pos.turn_id} />}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardPanel>
        </Card>
      )}

      <Card className="report-section">
        <CardHeader>
          <CardTitle className="text-lg">Delivery</CardTitle>
          <CardDescription>{report.delivery.note}</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-1 text-sm">
          {report.delivery.observations.map((line) => (
            <p key={line}>{line}</p>
          ))}
          {report.delivery.measured.length > 0 && (
            <p className="text-xs text-muted-foreground">Measured: {report.delivery.measured.join("; ")}.</p>
          )}
          <p className="text-xs text-muted-foreground">Not measured: {report.delivery.not_measured.join("; ")}.</p>
        </CardPanel>
      </Card>

      <RecommendationList title="More practice ideas" items={report.recommendations} contested={report.contested?.findings ?? []} />

      <Card className="report-section">
        <CardHeader>
          <CardTitle className="text-lg">Limits of this report</CardTitle>
        </CardHeader>
        <CardPanel>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
            {report.limitations.map((line) => (
              <li key={line}>{line}</li>
            ))}
            <li>{report.evidence_note}</li>
          </ul>
        </CardPanel>
      </Card>
    </div>
  );
}

function FindingCard({
  finding,
  report,
  onChange,
  onOpenPractice,
}: {
  finding: ReportFinding;
  report: SessionReport;
  onChange: () => Promise<void>;
  onOpenPractice: (id: string) => void;
}) {
  const dispute = finding.finding_id ? report.disputes?.[finding.finding_id] : undefined;
  const disputed = dispute && dispute.status !== "withdrawn";
  const canPractise =
    report.session_kind !== "practice" && finding.eligible_for_practice && !disputed && Boolean(finding.finding_id);
  return (
    <article className="finding" data-disputed={disputed ? "true" : "false"}>
      <div className="finding-head">
        <span className="finding-kind" data-kind={finding.polarity}>
          {finding.polarity === "gap" ? "to practise" : "strength"}
        </span>
        <h4>{finding.dimension_label}</h4>
        <span className="text-sm text-muted-foreground">
          {finding.perspective_label} round · {finding.confidence} confidence
        </span>
        {dispute && (
          <span className={dispute.status === "withdrawn" ? "finding-kind" : "dispute-stamp"}>
            {DISPUTE_STATUS[dispute.status]}
          </span>
        )}
      </div>
      {finding.question && (
        <div className="finding-zone">
          <span className="meta-label">The question</span>
          <p className="finding-question">{finding.question}</p>
        </div>
      )}
      <div className="finding-zone">
        <span className="meta-label">Your words</span>
        <blockquote className="finding-quote">
          “{finding.quote}”{finding.turn_id && <span className="turn-ref">turn {finding.turn_id.slice(0, 8)}</span>}
        </blockquote>
        <p className="finding-provenance">
          {finding.source_match !== false
            ? "Quote found in your answer — this checks where the words came from, not whether the judgement is right."
            : "This quote could not be located in your answer."}
        </p>
      </div>
      <div className="finding-zone">
        <span className="meta-label">Interpretation · can be challenged</span>
        <div className="finding-interpretation">
          <p>{finding.explanation}</p>
          {finding.limitation && (
            <p className="finding-limit">
              <span className="font-semibold text-foreground">Limits:</span> {finding.limitation}
            </p>
          )}
        </div>
      </div>
      {finding.practice && (
        <div className="finding-zone">
          <span className="meta-label">Next step</span>
          <p className="finding-practice">{finding.practice}</p>
        </div>
      )}
      <div className="finding-actions">
        {canPractise && <PractiseButton finding={finding} report={report} onOpenPractice={onOpenPractice} />}
        {finding.finding_id && (
          <DisputeControl finding={finding} report={report} dispute={dispute} onChange={onChange} />
        )}
      </div>
    </article>
  );
}

function PractiseButton({
  finding,
  report,
  onOpenPractice,
}: {
  finding: ReportFinding;
  report: SessionReport;
  onOpenPractice: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState("5");
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      // Only ids are sent. The server derives everything else from the stored report.
      const practice = await api<Practice>(
        "/api/practice",
        jsonBody({ session_id: report.session_id, finding_id: finding.finding_id, minutes: Number(minutes) }),
      );
      onOpenPractice(practice.practice_id);
    } catch (err) {
      toastManager.add({ title: "Could not start practice", description: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)}>
        <TargetIcon /> Practise this gap
      </Button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md bg-muted px-3 py-2">
      <label className="text-xs" htmlFor={`minutes-${finding.finding_id}`}>
        Length
      </label>
      <Select
        id={`minutes-${finding.finding_id}`}
        className="h-8 w-28"
        items={[3, 5, 8, 10].map((m) => ({ label: `${m} minutes`, value: String(m) }))}
        value={minutes}
        onChange={(event) => setMinutes(event.target.value)}
      />
      <Button size="sm" onClick={create} disabled={busy}>
        {busy ? "Preparing…" : "Set up practice"}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </div>
  );
}

function DisputeControl({
  finding,
  report,
  dispute,
  onChange,
}: {
  finding: ReportFinding;
  report: SessionReport;
  dispute?: Dispute;
  onChange: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(dispute?.explanation ?? "");
  const [busy, setBusy] = useState(false);
  const base = `/api/sessions/${report.session_id}/findings/${finding.finding_id}`;
  const revisions = (report.revisions ?? []).filter((r) => r.finding_id === finding.finding_id);
  const pending = revisions.some((r) => r.state === "queued" || r.state === "running");
  const hasCompleted = revisions.some((r) => r.state === "complete");

  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => void onChange(), 1500);
    return () => window.clearInterval(id);
  }, [pending, onChange]);

  const call = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    try {
      await api(path, jsonBody(body));
      await onChange();
      toastManager.add({ title: done, tone: "success" });
    } catch (err) {
      toastManager.add({ title: "That did not work", description: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  if (dispute && dispute.status !== "withdrawn") {
    return (
      <div className="flex w-full flex-col gap-2 rounded-md bg-muted px-3 py-2 text-xs">
        <p>
          Disputed. It is left out of your progress comparisons and next-practice plan until you settle it. The
          original report is kept unchanged.
          {dispute.explanation && <> Your note: “{dispute.explanation}”</>}
        </p>
        <div className="flex flex-wrap gap-2">
          {dispute.explanation.trim().length >= 10 && !pending && revisions.length < 2 && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => call(`${base}/revision`, {}, "Re-check requested")}>
              Ask for a re-check with my correction
            </Button>
          )}
          {pending && (
            <span className="flex items-center gap-1 text-muted-foreground">
              <LoaderCircleIcon className="size-3 animate-spin" /> Re-checking…
            </span>
          )}
          {hasCompleted && dispute.status !== "accepted_revision" && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => call(`${base}/dispute/status`, { status: "accepted_revision" }, "Revision accepted")}>
              <CheckIcon /> Accept the revised assessment
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => call(`${base}/dispute/status`, { status: "withdrawn" }, "Dispute withdrawn")}>
            Withdraw dispute
          </Button>
        </div>
      </div>
    );
  }

  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        <FlagIcon /> This feedback seems wrong
      </Button>
    );
  }
  return (
    <div className="flex w-full flex-col gap-2 rounded-md bg-muted px-3 py-2">
      <label className="text-xs font-medium" htmlFor={`dispute-${finding.finding_id}`}>
        What is wrong? (optional — e.g. “I was misheard: I said I designed it”, or “I explained this in my next answer”)
      </label>
      <textarea
        id={`dispute-${finding.finding_id}`}
        className="min-h-16 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
        maxLength={1500}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => call(`${base}/dispute`, { explanation: text }, "Saved — this finding is now disputed")}>
          Dispute this finding
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function RevisionsCard({ report }: { report: SessionReport }) {
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="text-lg">Revised assessments after your corrections</CardTitle>
        <CardDescription>
          Automated re-checks by the same evaluator with your correction as context. They are not
          independent confirmation either way, and they never replace the original report.
        </CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3 text-sm">
        {(report.revisions ?? []).map((rev) => (
          <div key={rev.revision_id}>
            <p className="font-medium">
              {rev.label} — {rev.dimension_id.replace(/_/g, " ")} ({rev.round.replace("_", " ")})
            </p>
            {rev.state === "complete" ? (
              <p>
                Level before: {levelLabel(rev.dimension_before ?? "insufficient_evidence")} · after re-check:{" "}
                {levelLabel(rev.dimension_after ?? "insufficient_evidence")}. {rev.summary}
              </p>
            ) : rev.state === "failed" ? (
              <p className="text-destructive">
                {rev.error} {rev.recovery}
              </p>
            ) : (
              <p className="text-muted-foreground">Re-checking…</p>
            )}
            <p className="text-xs text-muted-foreground">{rev.note}</p>
          </div>
        ))}
      </CardPanel>
    </Card>
  );
}

function RoundCard({ round, contested = [] }: { round: RoundResult; contested?: [string, string][] }) {
  const cov = round.coverage;
  const status =
    round.status === "evaluated"
      ? `Core questions asked: ${cov.spine_covered ?? "?"} of ${cov.spine_total ?? "?"}. Rubric ${round.rubric_version}.`
      : round.status === "not_reached"
        ? "This round was not reached, so nothing in it was assessed."
        : round.status === "insufficient_evidence"
          ? "The answers in this round were too short to assess, so nothing in it was scored."
          : "This round had no answers to assess.";
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {round.label}
          <Badge variant="muted" size="sm">{round.perspective_label} perspective</Badge>
          <span className="ml-auto tabular-nums text-sm text-muted-foreground">
            round indicator {round.aggregate.score == null ? "not scored" : `${percent(round.aggregate.score)}/100`}
          </span>
        </CardTitle>
        <CardDescription>{status}</CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3">
        <table className="round-table w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1 pr-2 font-normal">Dimension</th>
              <th className="py-1 pr-2 font-normal">Weight</th>
              <th className="py-1 font-normal">Level and the answer behind it</th>
            </tr>
          </thead>
          <tbody>
            {round.dimensions.map((dim) => {
              const isContested = contested.some(([r, d]) => r === round.round && d === dim.dimension_id);
              return (
                <tr key={dim.dimension_id} className="border-t border-border align-top">
                  <td className="py-2 pr-2">{dim.label}</td>
                  <td className="py-2 pr-2 tabular-nums">{Math.round(dim.weight * 100)}%</td>
                  <td className="py-2">
                    <Badge variant={dim.assessed ? "secondary" : "muted"} size="sm">
                      {levelLabel(dim.level)}
                    </Badge>
                    {isContested && (
                      <Badge variant="outline" size="sm" className="ml-1">
                        excluded from comparisons while disputed
                      </Badge>
                    )}
                    {dim.assessed && <p className="mt-1 text-xs text-muted-foreground">{dim.rationale}</p>}
                    {dim.citations.slice(0, 1).map((c, i) => (
                      <Quote key={i} text={c.quote} turn={c.turn_id} />
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="text-xs text-muted-foreground">{round.aggregate.note}</p>
      </CardPanel>
    </Card>
  );
}

function ClaimsCard({ claims, meanings }: { claims: ClaimFinding[]; meanings: Record<string, string> }) {
  if (claims.length === 0) return null;
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="text-lg">Statements from your background</CardTitle>
        <CardDescription>
          How each statement came across under questioning in this session. Repository or work-sample
          material shows that content exists, not who wrote it, and an unclear answer does not establish
          dishonesty. This is not lie detection.
        </CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3 text-sm">
        {claims.map((claim) => (
          <div key={claim.claim_id}>
            <p className="flex flex-wrap items-center gap-2">
              <Badge variant={claim.status === "held" ? "secondary" : claim.status === "collapsed" ? "outline" : "muted"} size="sm" title={meanings[claim.status]}>
                {claim.status_label || claimLabel(claim.status)}
              </Badge>
              “{claim.text}”
              {claim.evidence_kind === "candidate_correction" && (
                <span className="text-xs text-muted-foreground">(your correction, not a quote from your file)</span>
              )}
            </p>
            <p className="text-muted-foreground">{claim.reason}</p>
            {claim.quote && <Quote text={claim.quote} turn={claim.turn_id} />}
          </div>
        ))}
        <dl className="mt-2 grid gap-1 text-xs text-muted-foreground">
          {Object.entries(meanings).map(([status, meaning]) => (
            <div key={status}>
              <dt className="inline font-medium">{claimLabel(status)}: </dt>
              <dd className="inline">{meaning}</dd>
            </div>
          ))}
        </dl>
      </CardPanel>
    </Card>
  );
}

function Quote({ text, turn }: { text: string; turn?: string }) {
  return (
    <blockquote className="quote-inline">
      “{text}” {turn && <span className="text-xs">({turn.slice(0, 8)})</span>}
    </blockquote>
  );
}

function RecommendationList({
  title,
  items,
  note,
  contested = [],
}: {
  title: string;
  items: Recommendation[];
  note?: string;
  contested?: string[];
}) {
  const visible = items.filter(
    (item) => !item.evidence.some((ev) => (ev as { finding_id?: string }).finding_id && contested.includes((ev as { finding_id?: string }).finding_id!)),
  );
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="text-lg">{title}</CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardPanel>
        {visible.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing to suggest from this source yet.</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {visible.map((item, index) => (
              <li key={`${item.title}-${index}`} className="flex gap-3 text-sm">
                <span className="meta-label mt-0.5 shrink-0">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <p className="font-medium">{item.title}</p>
                  <p className="text-muted-foreground">{item.why}</p>
                  <p>{item.action}</p>
                  {item.evidence.slice(0, 2).map((ev, i) =>
                    ev.quote ? (
                      <Quote key={i} text={ev.quote} turn={ev.turn_id} />
                    ) : ev.excerpt ? (
                      <p key={i} className="text-xs text-muted-foreground">From {ev.source}: “{ev.excerpt}”</p>
                    ) : null,
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardPanel>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History and plan
// ---------------------------------------------------------------------------

const ROW_STATUS: Record<string, InterviewRow["status"]> = {
  complete: "completed",
  failed: "failed",
  active: "in_progress",
};

function PracticeList({ items, onOpenPractice }: { items: Practice[]; onOpenPractice: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <Card className="report-section">
      <CardHeader>
        <CardTitle className="text-lg">Targeted practice</CardTitle>
        <CardDescription>One gap at a time, compared on that dimension only. Never on an interview trend.</CardDescription>
      </CardHeader>
      <CardPanel>
        <ul className="flex flex-col gap-2 text-sm">
          {items.map((item) => (
            <li key={item.practice_id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{item.dimension_label}</span>
              <span className="text-muted-foreground">({item.round_label}, {item.mode})</span>
              <Badge variant="muted" size="sm">
                {item.latest_outcome ? OUTCOME_LABEL[item.latest_outcome] ?? item.latest_outcome : "not attempted yet"}
              </Badge>
              <Button variant="ghost" size="sm" className="ml-auto" onClick={() => onOpenPractice(item.practice_id)}>
                Open
              </Button>
            </li>
          ))}
        </ul>
      </CardPanel>
    </Card>
  );
}

function HistoryView({
  onOpenSession,
  onOpenPractice,
}: {
  onOpenSession: (id: string) => void;
  onOpenPractice: (id: string) => void;
}) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<History>("/api/history").then(setHistory).catch((e) => setError((e as Error).message));
  }, []);
  if (error) return <Notice text={error} />;
  if (!history) return <Notice text="Loading your history…" busy />;
  const rows: InterviewRow[] = history.sessions.map((row) => ({
    id: row.session_id,
    interview: `${row.kind === "practice" ? "Practice · " : ""}${row.target_role || "Interview"} · ${row.round_label}${
      row.open_disputes ? ` · ${row.open_disputes} disputed` : ""
    }`,
    status: ROW_STATUS[row.state] ?? "processing",
    date: row.created_at ? new Date(row.created_at).toLocaleDateString() : "",
    score: row.kind === "practice" || row.overall_score == null ? null : Math.round(row.overall_score * 100),
    lane: row.lane,
  }));
  return (
    <div className="flex flex-col gap-4">
      <Card className="report-section">
        <CardPanel className="pt-5">
          <InterviewHistory rows={rows} onViewFeedback={(row) => onOpenSession(row.id)} />
        </CardPanel>
      </Card>
      <PracticeList items={history.practice ?? []} onOpenPractice={onOpenPractice} />
      {history.comparisons.map((cmp) => (
        <Card key={cmp.latest_session_id} className="report-section">
          <CardHeader>
            <CardTitle className="text-lg">
              {cmp.kind === "trend" ? `Trend across ${cmp.sessions} sessions` : "Change since your previous comparable session"}
            </CardTitle>
            <CardDescription>{cmp.label}</CardDescription>
          </CardHeader>
          <CardPanel className="flex flex-col gap-2 text-sm">
            <ul className="flex flex-col gap-1">
              {cmp.changes.map((change) => (
                <li key={`${change.round}-${change.label}`}>
                  {change.label}: {levelLabel(change.previous_level)} → {levelLabel(change.latest_level)}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted-foreground">
              Overall indicator: {percent(cmp.overall_previous)} → {percent(cmp.overall_latest)} (a summary only).
            </p>
            {cmp.notes.map((note) => (
              <p key={note} className="text-xs text-muted-foreground">{note}</p>
            ))}
          </CardPanel>
        </Card>
      ))}
      {history.sessions.length > 0 && history.comparisons.length === 0 && (
        <p className="text-xs text-muted-foreground">
          Comparisons appear once you have two evaluated interviews with the same
          profession, round selection and answer mode. One session is not a trend.
        </p>
      )}
      {history.recurring_gaps.length > 0 && (
        <Card className="report-section">
          <CardHeader>
            <CardTitle className="text-lg">Recurring gaps</CardTitle>
            <CardDescription>Disputed findings are not counted.</CardDescription>
          </CardHeader>
          <CardPanel>
            <ul className="text-sm">
              {history.recurring_gaps.map((gap) => (
                <li key={`${gap.round}-${gap.dimension_label}`}>
                  {gap.dimension_label} — flagged in {gap.sessions.length} comparable sessions
                </li>
              ))}
            </ul>
          </CardPanel>
        </Card>
      )}
    </div>
  );
}

function PlanView({
  onOpenPractice,
  onOpenSession,
}: {
  onOpenPractice: (id: string) => void;
  onOpenSession: (id: string) => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Plan>("/api/plan").then(setPlan).catch((e) => setError((e as Error).message));
  }, []);
  if (error) return <Notice text={error} />;
  if (!plan) return <Notice text="Loading your plan…" busy />;
  return (
    <div className="flex flex-col gap-4">
      {(plan.practice?.length ?? 0) > 0 && (
        <Card className="report-section">
          <CardHeader>
            <CardTitle className="text-lg">Your targeted practice</CardTitle>
          </CardHeader>
          <CardPanel>
            <ul className="flex flex-col gap-2 text-sm">
              {plan.practice!.map((item) => (
                <li key={item.practice_id} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{item.title}</span>
                  <Badge variant="muted" size="sm">
                    {item.latest_outcome ? OUTCOME_LABEL[item.latest_outcome] ?? item.latest_outcome : `${item.attempts} attempt(s)`}
                  </Badge>
                  {item.offer_unaided_variation && (
                    <span className="text-xs text-muted-foreground">Next: try an unaided variation to check it transfers.</span>
                  )}
                  <span className="ml-auto flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => onOpenPractice(item.practice_id)}>
                      Open
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => onOpenSession(item.parent_session_id)}>
                      Original report
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          </CardPanel>
        </Card>
      )}
      <RecommendationList title="Next practice" items={plan.items} note={plan.note} />
      <RecommendationList
        title="From your latest preparation"
        items={plan.preparation}
        note="Built at intake from your background, the job description and the selected rounds."
      />
    </div>
  );
}

function DeleteMyData() {
  const [armed, setArmed] = useState(false);
  const remove = async () => {
    try {
      const manifest = await api<{ session_ids: string[]; paths_deleted: number; practice_records_deleted: number; external_providers: string }>(
        "/api/me/delete",
        { method: "POST" },
      );
      forgetGuest();
      toastManager.add({
        title: "Your data was deleted",
        description: `${manifest.session_ids.length} session(s), ${manifest.practice_records_deleted} practice record(s), ${manifest.paths_deleted} file(s) removed. ${manifest.external_providers}`,
        tone: "success",
      });
      window.location.hash = "";
      window.setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      toastManager.add({ title: "Delete failed", description: (err as Error).message, tone: "error" });
    }
  };
  return armed ? (
    <div className="flex max-w-xl flex-wrap items-center gap-2" role="group" aria-label="Confirm deletion">
      <CircleAlertIcon className="size-4 text-destructive" aria-hidden="true" />
      <span className="text-sm">
        Delete every intake, transcript, report, dispute and practice record? Anything currently being
        evaluated is cancelled first. Copies held by Groq or Deepgram under their own policies cannot be
        deleted from here.
      </span>
      <Button variant="destructive" size="sm" onClick={remove}>
        Yes, delete
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setArmed(false)}>
        Cancel
      </Button>
    </div>
  ) : (
    <Button variant="outline" className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => setArmed(true)}>
      Delete my data
    </Button>
  );
}
