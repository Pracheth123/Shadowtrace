import { useEffect, useState } from "react";

import { InterviewRoom } from "@/pages/InterviewRoom";
import { ResultsDashboard } from "@/pages/ResultsDashboard";
import { SetupPage } from "@/pages/SetupPage";
import { Toaster } from "@/components/ui/toast";
import { useSession, type SessionOptions } from "@/lib/use-session";

/**
 * App shell.
 *
 * Hash routes, no router dependency:
 *   #            setup → intake → preparation review
 *   (room)       the live interview, entered from setup only
 *   #results/ID  the report for one session, fetched by its id
 *   #history     history and next practice, no session selected
 */

type Route =
  | { view: "setup" }
  | { view: "room" }
  | { view: "results"; sessionId: string | null };

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash.startsWith("results/")) {
    return { view: "results", sessionId: decodeURIComponent(hash.slice(8)) || null };
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

  const finish = (sessionId: string) => {
    if (sessionId) openSession(sessionId);
    else {
      window.location.hash = "history";
      setRoute({ view: "results", sessionId: null });
    }
  };

  const toSetup = () => {
    window.location.hash = "";
    setRoute({ view: "setup" });
  };

  return (
    <div className="min-h-screen px-5 py-10 sm:px-8">
      {route.view === "setup" && (
        <SetupPage
          onStart={start}
          onOpenHistory={() => {
            window.location.hash = "history";
            setRoute({ view: "results", sessionId: null });
          }}
        />
      )}
      {route.view === "room" && <InterviewRoom session={session} onFinished={finish} />}
      {route.view === "results" && (
        <ResultsDashboard
          key={route.sessionId ?? "history"}
          sessionId={route.sessionId}
          onOpenSession={openSession}
          onStartAnother={toSetup}
        />
      )}
      <Toaster />
    </div>
  );
}
