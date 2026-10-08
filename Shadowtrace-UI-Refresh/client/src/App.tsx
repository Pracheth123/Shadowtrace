import { useEffect, useState } from "react";
import { LockKeyholeIcon } from "lucide-react";
import { Brand } from "@/components/layout/Brand";
import { HomePage } from "@/pages/HomePage";
import { InterviewRoom } from "@/pages/InterviewRoom";
import { PracticePage } from "@/pages/PracticePage";
import { ResultsDashboard } from "@/pages/ResultsDashboard";
import { SetupPage } from "@/pages/SetupPage";
import { Toaster } from "@/components/ui/toast";
import { useSession, type SessionOptions } from "@/lib/use-session";

// Hash routing keeps the application deployable as static files. The live room
// remains entered only through a reviewed intake or a stored practice record.
type Route =
  | { view: "home" }
  | { view: "setup" }
  | { view: "room" }
  | { view: "results"; sessionId: string | null }
  | { view: "practice"; practiceId: string };

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  try {
    if (hash.startsWith("results/")) return { view: "results", sessionId: decodeURIComponent(hash.slice(8)) || null };
    if (hash.startsWith("practice/") && hash.length > 9) return { view: "practice", practiceId: decodeURIComponent(hash.slice(9)) };
  } catch { return { view: "home" }; }
  if (hash === "history" || hash === "results") return { view: "results", sessionId: null };
  if (hash === "setup") return { view: "setup" };
  return { view: "home" };
}

export default function App() {
  const session = useSession();
  const [route, setRoute] = useState<Route>(parseHash);
  useEffect(() => {
    const onHash = () => setRoute((current) => current.view === "room" ? current : parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    window.scrollTo(0, 0);
    const title = { home: "Your experience. Your next chapter.", setup: "Prepare your interview", room: "Practice room", results: "Your feedback", practice: "Focused practice" }[route.view];
    document.title = `Shadowtrace — ${title}`;
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [route]);

  const start = (options: SessionOptions) => { void session.start(options); setRoute({ view: "room" }); };
  const openSession = (sessionId: string) => {
    window.location.hash = `results/${encodeURIComponent(sessionId)}`;
    setRoute({ view: "results", sessionId });
  };
  const openPractice = (practiceId: string) => {
    window.location.hash = `practice/${encodeURIComponent(practiceId)}`;
    setRoute({ view: "practice", practiceId });
  };
  const openHistory = () => { window.location.hash = "history"; setRoute({ view: "results", sessionId: null }); };
  const toSetup = () => { window.location.hash = "setup"; setRoute({ view: "setup" }); };
  const finish = (sessionId: string) => sessionId ? openSession(sessionId) : openHistory();

  return (
    <>
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>Skip to content</a>
      {route.view === "home" ? <HomePage onStart={toSetup} onHistory={openHistory} /> : (
        <>
          <header className="site-header workspace-header">
            <div className="site-header-inner">
              {route.view === "room" ? <Brand /> : <a href="#" aria-label="Shadowtrace home"><Brand /></a>}
              <nav className="workspace-nav" aria-label="Workspace navigation">
                {route.view === "room" ? <span className="eyebrow">PRACTICE ROOM</span> : <>
                  <a href="#setup" aria-current={route.view === "setup" ? "page" : undefined}>Prepare</a>
                  <a href="#history" aria-current={route.view === "results" ? "page" : undefined}>History & feedback</a>
                  {route.view === "practice" && <span className="eyebrow">FOCUSED PRACTICE</span>}
                </>}
              </nav>
              <span className="guest-status" title="Your guest key is stored in this browser. There is no account recovery."><LockKeyholeIcon aria-hidden="true" /> Browser guest</span>
            </div>
          </header>
          <main id="main-content" tabIndex={-1} className="app-main content-width outline-none">
            {route.view === "setup" && <SetupPage onStart={start} onOpenHistory={openHistory} />}
            {route.view === "room" && <InterviewRoom session={session} onFinished={finish} />}
            {route.view === "results" && <ResultsDashboard key={route.sessionId ?? "history"} sessionId={route.sessionId} onOpenSession={openSession} onOpenPractice={openPractice} onStartAnother={toSetup} />}
            {route.view === "practice" && <PracticePage key={route.practiceId} practiceId={route.practiceId} onStart={start} onOpenSession={openSession} onOpenPractice={openPractice} onBack={openHistory} />}
          </main>
          <footer className="workspace-footer"><div className="content-width"><span>Shadowtrace · Interview practice grounded in you.</span><span>Feedback is guidance, not a hiring prediction.</span></div></footer>
        </>
      )}
      <Toaster />
    </>
  );
}
