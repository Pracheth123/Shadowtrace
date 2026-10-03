import { lazy, Suspense, useEffect, useState } from "react";

import {
  InterviewHistory,
  type InterviewRow,
} from "@/components/InterviewHistory";
import type { DimensionScore } from "@/components/ScoreChart";
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

// Recharts is ~300 kB. Only this tab needs it, so it loads on demand rather
// than blocking first paint on the setup page.
const ScoreChart = lazy(() =>
  import("@/components/ScoreChart").then((module) => ({
    default: module.ScoreChart,
  })),
);

/**
 * Results dashboard.
 *
 * Reads the same `public/dashboard.json` the previous dashboard did, so the
 * backend contract is unchanged. The trend file stores scores per dimension
 * per session on 0–1; the newest session feeds the chart and the one before it
 * becomes the comparison series.
 */

type TrendRow = {
  started_at: string;
  session_id: string;
  dimension: string;
  score: number;
};
type Prep = { text: string; evidence: string; session_id: string };
type Finding = {
  dimension: string;
  summary: string;
  quote: string;
  polarity: string;
};
type DashboardData = {
  candidate_id: string;
  pack_id: string;
  fake_eval: { session_id: string; findings: Finding[] };
  trend: TrendRow[];
  roadmap: { items: Prep[] };
};

function toDimensions(rows: TrendRow[]): DimensionScore[] {
  return rows.map((row) => ({
    dimension: row.dimension,
    score: row.score,
    finding_count: 0,
  }));
}

function formatDate(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function ResultsDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/dashboard.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(setData)
      .catch(() => setError("Dashboard data is not available yet."));
  }, []);

  if (error) {
    return (
      <div className="mx-auto w-full max-w-2xl">
        <Card>
          <CardPanel className="pt-5">
            <p className="text-sm text-muted-foreground">{error}</p>
          </CardPanel>
        </Card>
      </div>
    );
  }

  if (!data) {
    return (
      <p className="mx-auto max-w-2xl text-sm text-muted-foreground">
        Loading your practice trend…
      </p>
    );
  }

  const sessionIds = [...new Set(data.trend.map((row) => row.session_id))];
  // Index arithmetic rather than `.at(-1)`: the project targets ES2020.
  const latestId = sessionIds[sessionIds.length - 1];
  const previousId =
    sessionIds.length > 1 ? sessionIds[sessionIds.length - 2] : undefined;
  const latest = toDimensions(
    data.trend.filter((row) => row.session_id === latestId),
  );
  const previous = previousId
    ? toDimensions(data.trend.filter((row) => row.session_id === previousId))
    : null;

  const rows: InterviewRow[] = sessionIds
    .map((sessionId) => {
      const sessionRows = data.trend.filter(
        (row) => row.session_id === sessionId,
      );
      const average =
        sessionRows.reduce((total, row) => total + row.score, 0) /
        Math.max(1, sessionRows.length);
      return {
        id: sessionId,
        interview: sessionId,
        status: "completed" as const,
        date: formatDate(sessionRows[0]?.started_at ?? ""),
        score: Math.round(average * 100),
      };
    })
    .reverse();

  const headline = data.fake_eval.findings[0];

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1>Your results</h1>
        <p className="text-sm text-muted-foreground">
          {data.candidate_id} · pack {data.pack_id}. Sessions from other packs
          are not comparable and are not on this chart.
        </p>
      </header>

      <Tabs defaultValue="scores">
        <TabsList>
          <TabsTab value="scores">Scores</TabsTab>
          <TabsTab value="history">History</TabsTab>
          <TabsTab value="plan">Prep plan</TabsTab>
        </TabsList>

        <TabsPanel value="scores">
          <Card>
            <CardHeader>
              <CardTitle>Four dimensions</CardTitle>
              <CardDescription>
                Equal weight by default. Delivery is measured against your own
                first answer, never against other candidates.
              </CardDescription>
            </CardHeader>
            <CardPanel>
              <Suspense
                fallback={
                  <p className="py-12 text-center text-sm text-muted-foreground">
                    Loading chart…
                  </p>
                }
              >
                <ScoreChart dimensions={latest} previous={previous} />
              </Suspense>
            </CardPanel>
          </Card>

          {headline && (
            <Card className="mt-4">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  Most recent finding
                  <Badge
                    variant={
                      headline.polarity === "gap" ? "muted" : "secondary"
                    }
                  >
                    {headline.polarity === "gap" ? "gap" : "strength"}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardPanel>
                <p className="text-sm">{headline.summary}</p>
                <blockquote className="mt-2 border-l-2 border-secondary pl-3 text-sm text-muted-foreground">
                  {headline.quote}
                </blockquote>
              </CardPanel>
            </Card>
          )}
        </TabsPanel>

        <TabsPanel value="history">
          <Card>
            <CardPanel className="pt-5">
              <InterviewHistory
                rows={rows}
                onViewFeedback={(row) =>
                  toastManager.add({
                    title: "Opening feedback",
                    description: row.interview,
                  })
                }
              />
            </CardPanel>
          </Card>
        </TabsPanel>

        <TabsPanel value="plan">
          <Card>
            <CardHeader>
              <CardTitle>What to work on next</CardTitle>
              <CardDescription>
                Drawn from your own sessions, each with the moment it came from.
              </CardDescription>
            </CardHeader>
            <CardPanel>
              <ol className="flex flex-col gap-3">
                {data.roadmap.items.map((item, index) => (
                  <li key={item.text} className="flex gap-3">
                    <Badge variant="primary" className="mt-0.5 shrink-0">
                      {index + 1}
                    </Badge>
                    <div>
                      <p className="text-sm">{item.text}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.evidence} · {item.session_id}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            </CardPanel>
          </Card>
        </TabsPanel>
      </Tabs>

      <div>
        <Button variant="ghost" onClick={() => (window.location.hash = "")}>
          Start another interview
        </Button>
      </div>
    </div>
  );
}
