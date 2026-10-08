import { useEffect, useState } from "react";

import { InterviewRoom } from "@/pages/InterviewRoom";
import { PracticePage } from "@/pages/PracticePage";
import { ResultsDashboard } from "@/pages/ResultsDashboard";
import { SetupPage } from "@/pages/SetupPage";
import { Toaster } from "@/components/ui/toast";
import { useSession, type SessionOptions } from "@/lib/use-session";

/**
 * App shell.
 *
 * Hash routes, no router dependency:
 *   #              setup → intake → source review
 *   (room)         the live interview or practice, entered from setup/practice only
 *   #results/ID    the report for one session, fetched by its id
 *   #practice/ID   one targeted practice and its before/after comparisons
 *   #history       history and next practice, no session selected
 *
 * Every view reads its data from the server by id, so a reload lands on the
 * same thing.
 */

type Route =
  | { view: "setup" }
  | { view: "room" }
  | { view: "results"; sessionId: string | null }
  | { view: "practice"; practiceId: string };

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash.startsWith("results/")) {
    return { view: "results", sessionId: decodeURIComponent(hash.slice(8)) || null };
  }
  if (hash.startsWith("practice/") && hash.length > 9) {
    return { view: "practice", practiceId: decodeURIComponent(hash.slice(9)) };
  }
  if (hash === "history" || hash === "results") return { view: "results", sessionId: null };
  return { view: "setup" };
}

export default function App() {
  const session = useSession();
  const [route, setRoute] = useState<Route>(parseHash);

  useEffect(() => {
    const onHash = () => {
      setRoute((current) => (current.view === "room" ? current : parseHash()));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const start = (options: SessionOptions) => {
    void session.start(options);
    setRoute({ view: "room" });
  };

  const openSession = (sessionId: string) => {
    window.location.hash = `results/${encodeURIComponent(sessionId)}`;
    setRoute({ view: "results", sessionId });
  };

  const openPractice = (practiceId: string) => {
    window.location.hash = `practice/${encodeURIComponent(practiceId)}`;
    setRoute({ view: "practice", practiceId });
  };

  const openHistory = () => {
    window.location.hash = "history";
    setRoute({ view: "results", sessionId: null });
  };

  const finish = (sessionId: string) => {
    if (sessionId) openSession(sessionId);
    else openHistory();
  };

  const toSetup = () => {
    window.location.hash = "";
    setRoute({ view: "setup" });
  };

  return (
    <div className="min-h-screen px-5 py-10 sm:px-8">
      {route.view === "setup" && <SetupPage onStart={start} onOpenHistory={openHistory} />}
      {route.view === "room" && <InterviewRoom session={session} onFinished={finish} />}
      {route.view === "results" && (
        <ResultsDashboard
          key={route.sessionId ?? "history"}
          sessionId={route.sessionId}
          onOpenSession={openSession}
          onOpenPractice={openPractice}
          onStartAnother={toSetup}
        />
      )}
      {route.view === "practice" && (
        <PracticePage
          key={route.practiceId}
          practiceId={route.practiceId}
          onStart={start}
          onOpenSession={openSession}
          onOpenPractice={openPractice}
          onBack={openHistory}
        />
      )}
      <Toaster />
    </div>
  );
}
