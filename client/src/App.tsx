import { lazy, Suspense, useEffect, useState } from "react";
import { LoaderCircleIcon, LockKeyholeIcon } from "lucide-react";
import { Brand } from "@/components/layout/Brand";
import { Toaster } from "@/components/ui/toast";
import { useSession, type SessionOptions } from "@/lib/use-session";

// Route-level code splitting: the homepage (and its scroll-animation library)
// never loads on the setup, interview, feedback or practice paths, and those
// workspace pages load only when opened.
const HomePage = lazy(() => import("@/pages/HomePage").then((m) => ({ default: m.HomePage })));
const SetupPage = lazy(() => import("@/pages/SetupPage").then((m) => ({ default: m.SetupPage })));
const InterviewRoom = lazy(() => import("@/pages/InterviewRoom").then((m) => ({ default: m.InterviewRoom })));
const ResultsDashboard = lazy(() => import("@/pages/ResultsDashboard").then((m) => ({ default: m.ResultsDashboard })));
const PracticePage = lazy(() => import("@/pages/PracticePage").then((m) => ({ default: m.PracticePage })));

function PageLoading() {
  return (
    <p className="flex items-center gap-2 py-16 text-sm text-muted-foreground" role="status">
      <LoaderCircleIcon className="size-4 animate-spin" aria-hidden="true" /> Loading…
    </p>
  );
}

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
      {route.view === "home" ? <Suspense fallback={<div className="content-width"><PageLoading /></div>}><HomePage onStart={toSetup} onHistory={openHistory} /></Suspense> : (
        <>
          <header className="site-header workspace-header on-charcoal">
            <div className="site-header-inner">
              {route.view === "room" ? <Brand /> : <a href="#" aria-label="Shadowtrace home"><Brand /></a>}
              <nav className="workspace-nav" aria-label="Workspace navigation">
                {route.view === "room" ? <span className="meta-label">Practice room</span> : <>
                  <a href="#setup" aria-current={route.view === "setup" ? "page" : undefined}>Prepare</a>
                  <a href="#history" aria-current={route.view === "results" ? "page" : undefined}>History & feedback</a>
                  {route.view === "practice" && <a href={window.location.hash} aria-current="page">Focused practice</a>}
                </>}
              </nav>
              <span className="guest-status" title="Your guest key is stored in this browser; there is no account recovery. Your sessions are stored on this app's server until deleted."><LockKeyholeIcon aria-hidden="true" /> Browser guest</span>
            </div>
          </header>
          <main id="main-content" tabIndex={-1} className="app-main content-width outline-none">
            <Suspense fallback={<PageLoading />}>
            {route.view === "setup" && <SetupPage onStart={start} onOpenHistory={openHistory} />}
            {route.view === "room" && <InterviewRoom session={session} onFinished={finish} />}
            {route.view === "results" && <ResultsDashboard key={route.sessionId ?? "history"} sessionId={route.sessionId} onOpenSession={openSession} onOpenPractice={openPractice} onStartAnother={toSetup} />}
            {route.view === "practice" && <PracticePage key={route.practiceId} practiceId={route.practiceId} onStart={start} onOpenSession={openSession} onOpenPractice={openPractice} onBack={openHistory} />}
            </Suspense>
          </main>
          <footer className="workspace-footer on-charcoal"><div className="content-width"><span>Shadowtrace · Interview practice grounded in you.</span><span>Feedback is guidance from your quoted answers, not a hiring prediction.</span></div></footer>
        </>
      )}
      <Toaster />
    </>
  );
}
