import { useCallback, useEffect, useState } from "react";
import { DownloadIcon, LoaderCircleIcon, RotateCwIcon } from "lucide-react";

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
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { toastManager } from "@/components/ui/toast";
import {
  ApiError,
  ROUNDS,
  api,
  download,
  forgetGuest,
  levelLabel,
  percent,
  type ClaimFinding,
  type History,
  type Plan,
  type Recommendation,
  type RoundResult,
  type SessionMeta,
  type SessionReport,
} from "@/lib/api";

/**
 * Results for one completed session, read from the server by its session id.
 *
 * There is no sample data on this page. While a report is being produced the
 * page says so; if evaluation fails it says that and offers a retry. Nothing
 * from another session or a fixture is shown in the meantime.
 */

const STATE_TEXT: Record<string, string> = {
  active: "The interview is still in progress.",
  finalising: "Saving your transcript…",
  evaluation_queued: "Your evaluation is queued…",
  evaluating: "Evaluating your answers…",
  complete: "Complete",
  failed: "Evaluation failed",
};

const DIFFICULTY: Record<string, string> = { coach: "Coach", realistic: "Realistic", panel: "Hard" };

const CLAIM_BADGE: Record<string, "secondary" | "muted" | "outline"> = {
  held: "secondary",
  collapsed: "outline",
  untested: "muted",
};

export type ResultsDashboardProps = {
  sessionId: string | null;
  onOpenSession: (sessionId: string) => void;
  onStartAnother: () => void;
};

export function ResultsDashboard({ sessionId, onOpenSession, onStartAnother }: ResultsDashboardProps) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <Tabs defaultValue={sessionId ? "report" : "history"}>
        <TabsList>
          {sessionId && <TabsTab value="report">This session</TabsTab>}
          <TabsTab value="history">History</TabsTab>
          <TabsTab value="plan">Next practice</TabsTab>
        </TabsList>
        {sessionId && (
          <TabsPanel value="report">
            {/* Keyed by id so a different session never shows the previous one's data. */}
            <SessionReportView key={sessionId} sessionId={sessionId} />
          </TabsPanel>
        )}
        <TabsPanel value="history">
          <HistoryView onOpenSession={onOpenSession} />
        </TabsPanel>
        <TabsPanel value="plan">
          <PlanView />
        </TabsPanel>
      </Tabs>
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onStartAnother}>
          Start another interview
        </Button>
        <DeleteMyData />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One session
// ---------------------------------------------------------------------------

function SessionReportView({ sessionId }: { sessionId: string }) {
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [report, setReport] = useState<SessionReport | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const current = await api<SessionMeta>(`/api/sessions/${sessionId}`);
      setMeta(current);
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

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    const tick = async () => {
      const done = await load();
      if (!done && !cancelled) timer = window.setTimeout(tick, 1500);
    };
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [load]);

  const retry = async () => {
    setReport(null);
    await api(`/api/sessions/${sessionId}/evaluation/retry`, { method: "POST" });
    setMeta((m) => (m ? { ...m, state: "evaluation_queued", error: null } : m));
    const poll = async () => {
      const done = await load();
      if (!done) window.setTimeout(poll, 1500);
    };
    void poll();
  };

  if (error) return <Notice text={error} />;
  if (!meta) return <Notice text="Loading this session…" busy />;

  if (meta.state === "failed") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Evaluation failed</CardTitle>
          <CardDescription>{meta.error}</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-wrap gap-2">
          <p className="w-full text-sm text-muted-foreground">
            No report is shown rather than a substitute. Your transcript is saved.
          </p>
          <Button onClick={retry}>
            <RotateCwIcon /> Retry evaluation
          </Button>
          <TranscriptButton sessionId={sessionId} />
        </CardPanel>
      </Card>
    );
  }

  if (meta.state !== "complete" || !report) {
    return <Notice text={STATE_TEXT[meta.state] ?? meta.state} busy />;
  }
  return <ReportBody report={report} />;
}

function Notice({ text, busy }: { text: string; busy?: boolean }) {
  return (
    <Card>
      <CardPanel className="flex items-center gap-2 pt-5 text-sm text-muted-foreground">
        {busy && <LoaderCircleIcon className="size-4 animate-spin" />}
        {text}
      </CardPanel>
    </Card>
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

function ReportBody({ report }: { report: SessionReport }) {
  const cfg = report.config;
  const roundLabel = ROUNDS.find((r) => r.value === cfg.round)?.label ?? cfg.round;
  const strengths = report.rounds.flatMap((r) => r.findings.filter((f) => f.polarity === "strength"));
  const gaps = report.rounds.flatMap((r) => r.findings.filter((f) => f.polarity === "gap"));
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1>Your feedback</h1>
        <p className="text-sm text-muted-foreground">
          {cfg.target_role} · {roundLabel} · {report.lane === "text" ? "typed" : "spoken"} ·{" "}
          {DIFFICULTY[cfg.intensity] ?? cfg.intensity} difficulty ·{" "}
          {new Date(report.session_started_at || report.created_at).toLocaleString()}
        </p>
        <p className="text-xs text-muted-foreground">{report.coverage_note}</p>
      </header>

      {!report.evaluator.is_assessment && (
        <p className="rounded-md border border-warning px-3 py-2 text-sm">
          <strong>Development evaluation.</strong> No evaluation model was configured, so
          this report was produced by a placeholder. It is not an assessment of you.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-baseline gap-2">
            Overall indicator
            <span className="text-2xl tabular-nums">
              {report.overall.score == null ? "Not scored" : `${percent(report.overall.score)}/100`}
            </span>
          </CardTitle>
          <CardDescription>{report.overall.note}</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">{report.overall.disclaimer}</p>
          <div className="flex flex-wrap gap-2">
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

      {report.rounds.map((round) => (
        <RoundCard key={round.round} round={round} />
      ))}

      {(strengths.length > 0 || gaps.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Strengths and gaps</CardTitle>
            <CardDescription>Each one quotes the answer it rests on.</CardDescription>
          </CardHeader>
          <CardPanel className="flex flex-col gap-4">
            {[...strengths, ...gaps].map((finding, index) => (
              <div key={index} className="text-sm">
                <p className="flex flex-wrap items-center gap-2 font-medium">
                  <Badge variant={finding.polarity === "gap" ? "outline" : "secondary"} size="sm">
                    {finding.polarity === "gap" ? "gap" : "strength"}
                  </Badge>
                  {finding.dimension_label}
                  <span className="text-xs font-normal text-muted-foreground">
                    {finding.perspective_label} · {finding.confidence} confidence
                  </span>
                </p>
                <p>{finding.explanation}</p>
                <Quote text={finding.quote} turn={finding.turn_id} />
                {finding.practice && <p className="text-muted-foreground">Practice: {finding.practice}</p>}
              </div>
            ))}
          </CardPanel>
        </Card>
      )}

      <ClaimsCard claims={report.claims} meanings={report.claim_status_meanings} />

      {report.disagreements.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Where the evaluators disagreed</CardTitle>
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
                      <span className="font-medium">{pos.perspective.replace("_", " ")}</span>: {pos.status} — {pos.reason}
                      {pos.quote && <Quote text={pos.quote} turn={pos.turn_id} />}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardPanel>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Delivery</CardTitle>
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

      <RecommendationList title="What to practise next" items={report.recommendations} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Limits of this report</CardTitle>
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

function RoundCard({ round }: { round: RoundResult }) {
  const cov = round.coverage;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {round.label}
          <Badge variant="muted" size="sm">{round.perspective_label} perspective</Badge>
          <span className="ml-auto tabular-nums">
            {round.aggregate.score == null ? "Not scored" : `${percent(round.aggregate.score)}/100`}
          </span>
        </CardTitle>
        <CardDescription>
          {round.status === "evaluated"
            ? `Core questions asked: ${cov.spine_covered ?? "?"} of ${cov.spine_total ?? "?"}. Rubric ${round.rubric_version}.`
            : round.status === "not_reached"
              ? "This round was not reached, so nothing in it was assessed."
              : "This round had no answers to assess."}
        </CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1 pr-2 font-normal">Dimension</th>
              <th className="py-1 pr-2 font-normal">Weight</th>
              <th className="py-1 font-normal">Result</th>
            </tr>
          </thead>
          <tbody>
            {round.dimensions.map((dim) => (
              <tr key={dim.dimension_id} className="border-t border-border align-top">
                <td className="py-2 pr-2">{dim.label}</td>
                <td className="py-2 pr-2 tabular-nums">{Math.round(dim.weight * 100)}%</td>
                <td className="py-2">
                  <Badge variant={dim.assessed ? "secondary" : "muted"} size="sm">
                    {levelLabel(dim.level)}
                  </Badge>
                  {dim.assessed && <p className="mt-1 text-xs text-muted-foreground">{dim.rationale}</p>}
                  {dim.citations.slice(0, 1).map((c, i) => (
                    <Quote key={i} text={c.quote} turn={c.turn_id} />
                  ))}
                </td>
              </tr>
            ))}
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
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Claims from your background</CardTitle>
        <CardDescription>
          How each claim fared under questioning in this session. Practice findings
          only — not lie detection and not a check of who wrote anything.
        </CardDescription>
      </CardHeader>
      <CardPanel className="flex flex-col gap-3 text-sm">
        {claims.map((claim) => (
          <div key={claim.claim_id}>
            <p className="flex flex-wrap items-center gap-2">
              <Badge variant={CLAIM_BADGE[claim.status]} size="sm" title={meanings[claim.status]}>
                {claim.status}
              </Badge>
              “{claim.text}”
            </p>
            <p className="text-muted-foreground">{claim.reason}</p>
            {claim.quote && <Quote text={claim.quote} turn={claim.turn_id} />}
          </div>
        ))}
        <dl className="mt-2 grid gap-1 text-xs text-muted-foreground">
          {Object.entries(meanings).map(([status, meaning]) => (
            <div key={status}>
              <dt className="inline font-medium">{status}: </dt>
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
    <blockquote className="mt-1 border-l-2 border-secondary pl-3 text-sm text-muted-foreground">
      “{text}” {turn && <span className="text-xs">({turn.slice(0, 8)})</span>}
    </blockquote>
  );
}

function RecommendationList({ title, items, note }: { title: string; items: Recommendation[]; note?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardPanel>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing to suggest from this source yet.</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {items.map((item, index) => (
              <li key={`${item.title}-${index}`} className="flex gap-3 text-sm">
                <Badge variant="primary" className="mt-0.5 shrink-0">{index + 1}</Badge>
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

function HistoryView({ onOpenSession }: { onOpenSession: (id: string) => void }) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<History>("/api/history").then(setHistory).catch((e) => setError((e as Error).message));
  }, []);
  if (error) return <Notice text={error} />;
  if (!history) return <Notice text="Loading your history…" busy />;
  const rows: InterviewRow[] = history.sessions.map((row) => ({
    id: row.session_id,
    interview: `${row.target_role || "Interview"} · ${row.round_label}`,
    status: ROW_STATUS[row.state] ?? "processing",
    date: row.created_at ? new Date(row.created_at).toLocaleDateString() : "",
    score: row.overall_score == null ? null : Math.round(row.overall_score * 100),
    lane: row.lane,
  }));
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardPanel className="pt-5">
          <InterviewHistory rows={rows} onViewFeedback={(row) => onOpenSession(row.id)} />
        </CardPanel>
      </Card>
      {history.comparisons.map((cmp) => (
        <Card key={cmp.latest_session_id}>
          <CardHeader>
            <CardTitle className="text-base">
              {cmp.kind === "trend" ? `Trend across ${cmp.sessions} sessions` : "Change since your previous comparable session"}
            </CardTitle>
            <CardDescription>{cmp.label}</CardDescription>
          </CardHeader>
          <CardPanel className="flex flex-col gap-2 text-sm">
            <p>
              Overall: {percent(cmp.overall_previous)} → {percent(cmp.overall_latest)}
            </p>
            <ul className="flex flex-col gap-1">
              {cmp.changes.map((change) => (
                <li key={`${change.round}-${change.label}`}>
                  {change.label}: {levelLabel(change.previous_level)} → {levelLabel(change.latest_level)}
                </li>
              ))}
            </ul>
            {cmp.notes.map((note) => (
              <p key={note} className="text-xs text-muted-foreground">{note}</p>
            ))}
          </CardPanel>
        </Card>
      ))}
      {history.sessions.length > 0 && history.comparisons.length === 0 && (
        <p className="text-xs text-muted-foreground">
          Comparisons appear once you have two evaluated sessions with the same
          profession, round selection and answer mode. One session is not a trend.
        </p>
      )}
      {history.recurring_gaps.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recurring gaps</CardTitle>
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

function PlanView() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Plan>("/api/plan").then(setPlan).catch((e) => setError((e as Error).message));
  }, []);
  if (error) return <Notice text={error} />;
  if (!plan) return <Notice text="Loading your plan…" busy />;
  return (
    <div className="flex flex-col gap-4">
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
      const manifest = await api<{ session_ids: string[]; paths_deleted: number }>("/api/me/delete", {
        method: "POST",
      });
      forgetGuest();
      toastManager.add({
        title: "Your data was deleted",
        description: `${manifest.session_ids.length} session(s), ${manifest.paths_deleted} file(s) removed.`,
        tone: "success",
      });
      window.location.hash = "";
      window.location.reload();
    } catch (err) {
      toastManager.add({ title: "Delete failed", description: (err as Error).message, tone: "error" });
    }
  };
  return armed ? (
    <div className="flex items-center gap-2">
      <span className="text-sm">Delete every intake, transcript and report?</span>
      <Button variant="destructive" size="sm" onClick={remove}>
        Yes, delete
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setArmed(false)}>
        Cancel
      </Button>
    </div>
  ) : (
    <Button variant="ghost" className="text-destructive" onClick={() => setArmed(true)}>
      Delete my data
    </Button>
  );
}
