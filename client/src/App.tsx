import { useEffect, useState } from "react";

import { InterviewRoom } from "@/pages/InterviewRoom";
import { ResultsDashboard } from "@/pages/ResultsDashboard";
import { SetupPage } from "@/pages/SetupPage";
import { Toaster } from "@/components/ui/toast";
import { useSession, type SessionOptions } from "@/lib/use-session";

/**
 * App shell.
 *
 * Routing stays on the existing hash scheme rather than pulling in a router:
 * three views and one transition do not justify the dependency. `#results`
 * is linkable; setup and room follow session state.
 */

type View = "setup" | "room" | "results";

export default function App() {
  const session = useSession();
  const [view, setView] = useState<View>(
    window.location.hash.replace("#", "") === "results" ? "results" : "setup",
  );

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace("#", "");
      if (hash === "results") setView("results");
      else if (hash === "") setView("setup");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const start = (options: SessionOptions) => {
    session.start(options);
    setView("room");
  };

  const finish = () => {
    window.location.hash = "results";
    setView("results");
  };

  return (
    <div className="min-h-screen px-5 py-10 sm:px-8">
      {view === "setup" && <SetupPage onStart={start} />}
      {view === "room" && (
        <InterviewRoom session={session} onFinished={finish} />
      )}
      {view === "results" && <ResultsDashboard />}
      <Toaster />
    </div>
  );
}
