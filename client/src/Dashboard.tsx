import { useEffect, useState } from "react";

type Score = { started_at: string; session_id: string; dimension: string; score: number };
type Prep = { text: string; evidence: string; session_id: string };
type Finding = { dimension: string; summary: string; quote: string; polarity: string };
type DashboardData = {
  candidate_id: string;
  pack_id: string;
  fake_eval: { session_id: string; findings: Finding[] };
  trend: Score[];
  roadmap: { items: Prep[] };
};

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/dashboard.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(setData)
      .catch(() => setError("Dashboard data is not available."));
  }, []);

  if (error) return <p className="status">{error}</p>;
  if (!data) return <p className="status">Loading practice trend…</p>;

  const sessions = [...new Set(data.trend.map((row) => row.session_id))];

  return (
    <>
      <h1>Practice trend</h1>
      <p className="sub">
        {data.candidate_id} · pack {data.pack_id}. Sessions in other packs are not on this chart.
      </p>
      <section className="card">
        <span className="label">Latest card from fake_eval</span>
        <p>{data.fake_eval.findings[0]?.summary}</p>
        <p className="evidence">“{data.fake_eval.findings[0]?.quote}”</p>
      </section>
      {sessions.map((sessionId) => (
        <section className="card" key={sessionId}>
          <strong>{sessionId}</strong>
          {data.trend
            .filter((row) => row.session_id === sessionId)
            .map((row) => (
              <div className="bar" key={row.dimension}>
                <span>{row.dimension}</span>
                <span>
                  <i style={{ width: `${Math.round(row.score * 100)}%` }} />
                </span>
                <span>{row.score.toFixed(2)}</span>
              </div>
            ))}
        </section>
      ))}
      <h2>Prep plan</h2>
      <ol>
        {data.roadmap.items.map((item) => (
          <li key={item.text}>
            {item.text}
            <div className="evidence">
              {item.evidence} · {item.session_id}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}
