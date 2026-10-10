import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { m, useMotionValue, useMotionValueEvent, useReducedMotion, useScroll, useSpring, useTransform, type MotionValue } from "motion/react";
import { ArrowDownIcon } from "lucide-react";
import { ResumePaper } from "./ResumePaper";
import { FeedbackCard, InterviewCard } from "./StoryCards";
import { StoryCaption } from "./StoryCaption";
import { COMPACT_CARDS, COMPACT_PAPERS, PAPER_RATIO, PAPER_WIDTH, PINNED_CARDS, PINNED_PAPERS, STAGES, STAGE_TARGET, stageOf, type PaperKind, type StageIndex, type StoryMode, type Track } from "./story-data";
import { poseAt, usePose, useWindow, type StageSize } from "./story-motion";

// "Papers to Practice": one person's papers fly in, gather into a background,
// become an interview question, then a quoted-answer coaching note.
//
//  pinned  – desktop with room (≥1024 × 760): a 200svh section whose stage stays
//            sticky for one viewport of scroll; transforms follow LOCAL section
//            progress directly (no spring, so nothing lags the scrollbar).
//  compact – phones, tablets and short desktops: no pinning, three papers,
//            smaller travel; the labelled step buttons drive the same composition.
//  static  – reduced motion: four readable panels, no flight, rotation or pinning.
//
// Per-frame work is motion values only. React state changes when the stage
// index changes, the role tab changes, or the stage is resized.

const PINNED_QUERY = "(min-width: 1024px) and (min-height: 760px)";
const SUPPORTING: PaperKind[] = ["projects", "skills", "experience", "sample"];

function useMedia(query: string) {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener?.("change", update);
    return () => list.removeEventListener?.("change", update);
  }, [query]);
  return matches;
}

function PaperFlight({ kind, track, index, progress, size, width, highlights }: { kind: PaperKind; track: Track; index: number; progress: MotionValue<number>; size: StageSize; width: number; highlights?: [MotionValue<number>, MotionValue<number>] }) {
  const pose = usePose(progress, size, track);
  // Scroll transforms on this wrapper; the one-time load entrance on the inner one.
  return (
    <m.div className="paper-flight" style={{ ...pose, "--pw": String(width) } as unknown as CSSProperties}>
      <div className="paper-enter" style={{ "--enter-i": index } as CSSProperties}>
        <ResumePaper kind={kind} highlights={highlights} />
      </div>
    </m.div>
  );
}

/**
 * A card that is faded out must also leave the tab order and the accessibility
 * tree: opacity and pointer-events alone left the compact stage-3 interviewer
 * tabs focusable while invisible. `inert` is toggled only when the card crosses
 * the visibility threshold, never per frame. If focus is inside a card as it
 * hides, it moves to the current step button first, so it never drops to <body>.
 */
function useInertWhenHidden(opacity: MotionValue<number>) {
  const ref = useRef<HTMLDivElement>(null);
  const apply = (value: number) => {
    const el = ref.current;
    if (!el) return;
    const hidden = value <= 0.5;
    if (hidden === el.inert) return;
    if (hidden && el.contains(document.activeElement)) {
      const step = el.closest(".story")?.querySelector<HTMLElement>(".story-steps button[aria-current=step]");
      step?.focus({ preventScroll: true });
    }
    el.inert = hidden;
    if (hidden) el.setAttribute("aria-hidden", "true");
    else el.removeAttribute("aria-hidden");
  };
  useMotionValueEvent(opacity, "change", apply);
  // Mount and layout-mode changes: sync with the value as it is now. Idempotent.
  useLayoutEffect(() => apply(opacity.get()));
  return ref;
}

interface Geometry { w: number; h: number; statementY: number; paperH: number; questionY: number }

function Stage({ mode, progress, role, onRole, onCardFocus }: { mode: "pinned" | "compact"; progress: MotionValue<number>; role: number; onRole: (index: number) => void; onCardFocus: () => void }) {
  const papers = mode === "pinned" ? PINNED_PAPERS : COMPACT_PAPERS;
  const cards = mode === "pinned" ? PINNED_CARDS : COMPACT_CARDS;
  const widths = PAPER_WIDTH[mode];
  const stageRef = useRef<HTMLDivElement>(null);
  const size = { w: useMotionValue(0), h: useMotionValue(0) };
  const [geo, setGeo] = useState<Geometry | null>(null);

  // Measure on resize only (never per scroll frame). offsetTop/offsetHeight are
  // unaffected by transforms, so this is stable mid-animation.
  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => {
      const w = stage.clientWidth, h = stage.clientHeight;
      size.w.set(w); size.h.set(h);
      const resume = stage.querySelector<HTMLElement>(".paper-resume");
      const statement = resume?.querySelector<HTMLElement>(".paper-statement");
      const question = stage.querySelector<HTMLElement>(".story-interview [data-anchor=question]");
      setGeo({
        w, h,
        paperH: resume?.offsetHeight ?? 0,
        statementY: statement ? statement.offsetTop + statement.offsetHeight / 2 : 0,
        questionY: question ? question.offsetTop + Math.min(question.offsetHeight / 2, 14) : 60,
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
    // size.w/size.h are stable motion values; re-measure when the layout mode changes.
  }, [mode]);

  const highlights: [MotionValue<number>, MotionValue<number>] = [useWindow(progress, 0.27, 0.34), useWindow(progress, 0.31, 0.38)];
  const interview = usePose(progress, size, cards.interview);
  const feedback = usePose(progress, size, cards.feedback);
  const interviewEvents = useTransform(interview.opacity, (o) => (o > 0.5 ? "auto" : "none"));
  const feedbackEvents = useTransform(feedback.opacity, (o) => (o > 0.5 ? "auto" : "none"));
  const interviewRef = useInertWhenHidden(interview.opacity);
  const feedbackRef = useInertWhenHidden(feedback.opacity);
  const connectorLength = useWindow(progress, 0.52, 0.6);
  const connectorOpacity = useTransform(progress, [0.5, 0.53, 0.74, 0.79], [0, 1, 1, 0]);

  // Connector: background statement (resume at stage C) → interviewer question.
  let connector: { d: string; x: number; y: number } | null = null;
  if (geo && geo.w) {
    const { w, h } = geo;
    const resume = poseAt(papers.resume!, 2), card = poseAt(cards.interview, 2);
    const cq = Math.min(w, h) / 100, pw = (widths.resume ?? 44) * cq;
    const ph = geo.paperH || pw * PAPER_RATIO;
    const ax = w / 2 + resume[0] * w + (pw / 2) * resume[3] - 6;
    const ay = h / 2 + resume[1] * h + (geo.statementY - ph / 2) * resume[3];
    const left = w / 2 + card[0] * w, top = h / 2 + card[1] * h;
    if (mode === "pinned") {
      const bx = left - 4, by = top + geo.questionY;
      connector = { d: `M ${ax},${ay} C ${ax + 56},${ay} ${bx - 56},${by} ${bx},${by}`, x: ax, y: ay };
    } else {
      const bx = Math.min(w - 24, ax + w * 0.3), by = top - 2;
      connector = { d: `M ${ax},${ay} C ${ax + 40},${ay} ${bx},${by - 40} ${bx},${by}`, x: ax, y: ay };
    }
  }

  const kinds = [...SUPPORTING.filter((kind) => papers[kind]), "resume" as const];
  return (
    <div ref={stageRef} className="story-stage">
      <p className="story-chip">Illustrative example — not a live assessment</p>
      <div className="story-clip" aria-hidden="true">
        {kinds.map((kind, index) => (
          <PaperFlight key={kind} kind={kind} index={index} track={papers[kind]!} progress={progress} size={size} width={widths[kind] ?? 34} highlights={kind === "resume" ? highlights : undefined} />
        ))}
        {connector && (
          <svg className="story-connector" width={geo!.w} height={geo!.h} viewBox={`0 0 ${geo!.w} ${geo!.h}`} fill="none">
            <m.path d={connector.d} strokeWidth={1.5} strokeLinecap="round" style={{ pathLength: connectorLength, opacity: connectorOpacity }} />
            <m.circle cx={connector.x} cy={connector.y} r={3.5} style={{ opacity: connectorOpacity }} />
          </svg>
        )}
      </div>
      {/* Interactive layer: cards sit above the decorative papers and stay out of the aria-hidden subtree.
          A card is inert (unfocusable, hidden from assistive technology) whenever it is faded out. */}
      <m.div ref={interviewRef} className="story-card-wrap" data-card="interview" style={{ ...interview, pointerEvents: interviewEvents }}>
        <InterviewCard selected={role} onSelect={onRole} onFocusWithin={onCardFocus} />
      </m.div>
      <m.div ref={feedbackRef} className="story-card-wrap" data-card="feedback" style={{ ...feedback, pointerEvents: feedbackEvents }}>
        <FeedbackCard selected={role} />
      </m.div>
    </div>
  );
}

/** Reduced motion: the same four steps as static, readable panels. */
function StaticStory({ role, onRole }: { role: number; onRole: (index: number) => void }) {
  return (
    <div className="story-static">
      <p className="story-chip">Illustrative example — not a live assessment</p>
      <ol className="story-static-list">
        {STAGES.map((stage, index) => (
          <li key={stage.short} className="story-static-panel">
            <p className="story-step-number">{String(index + 1).padStart(2, "0")}</p>
            <h2 className="story-static-title">{stage.title}</h2>
            <p className="story-static-text">{stage.text}</p>
            <div className="story-static-art" data-art={index}>
              {index === 0 && <div className="static-papers" aria-hidden="true">{(["projects", "experience", "resume"] as const).map((kind) => <ResumePaper key={kind} kind={kind} />)}</div>}
              {index === 1 && <div className="static-resume" aria-hidden="true"><ResumePaper kind="resume" /></div>}
              {index === 2 && <InterviewCard selected={role} onSelect={onRole} />}
              {index === 3 && <FeedbackCard selected={role} />}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function ResumeScrollStory({ copy, nextId }: { copy: ReactNode; nextId: string }) {
  const reduce = useReducedMotion();
  const roomy = useMedia(PINNED_QUERY);
  const mode: StoryMode = reduce ? "static" : roomy ? "pinned" : "compact";
  const sectionRef = useRef<HTMLElement>(null);
  const [active, setActive] = useState<StageIndex>(0);
  const [role, setRole] = useState(1);

  // Pinned: local progress through this section only. It is mirrored into a plain
  // motion value: Motion would otherwise hand values derived from useScroll to a
  // native ViewTimeline, whose range did not match this sticky section's progress.
  const { scrollYProgress } = useScroll({ target: sectionRef, offset: ["start start", "end end"] });
  const local = useMotionValue(0);
  // Compact: buttons set a target; a modest spring carries the papers there.
  const target = useMotionValue(0);
  const manual = useSpring(target, { stiffness: 70, damping: 18, mass: 0.9 });
  const progress = mode === "pinned" ? local : manual;
  const hint = useTransform(local, [0, 0.06], [1, 0]);

  const activeRef = useRef(active);
  useMotionValueEvent(scrollYProgress, "change", (value) => {
    local.set(value);
    if (mode !== "pinned") return;
    const next = stageOf(value);
    if (next !== activeRef.current) { activeRef.current = next; setActive(next); }
  });
  useEffect(() => {
    // Entering compact: show the current stage there. Entering pinned: follow scroll.
    local.set(scrollYProgress.get());
    activeRef.current = mode === "pinned" ? stageOf(scrollYProgress.get()) : active;
    setActive(activeRef.current);
    if (mode === "compact") target.jump(STAGE_TARGET[activeRef.current]);
  }, [mode]); // Mode changes only; the rest is read through refs/motion values.

  const select = (stage: StageIndex) => {
    if (mode === "compact") { activeRef.current = stage; setActive(stage); target.set(STAGE_TARGET[stage]); return; }
    const section = sectionRef.current;
    if (!section) return;
    const top = section.getBoundingClientRect().top + window.scrollY;
    const span = section.offsetHeight - window.innerHeight;
    window.scrollTo({ top: stage === 0 ? 0 : top + STAGE_TARGET[stage] * span, behavior: "smooth" });
  };
  // Hidden cards are inert, so keyboard users reach them through the step buttons.
  // This only matters at the visibility threshold, while the stage index catches up.
  const onCardFocus = () => { if (activeRef.current < 2) select(2); };
  const skip = () => {
    const next = document.getElementById(nextId);
    if (!next) return;
    next.scrollIntoView({ behavior: mode === "pinned" ? "smooth" : "auto", block: "start" });
    next.focus({ preventScroll: true });
  };

  return (
    <section ref={sectionRef} className="story" data-mode={mode} data-stage={active} aria-labelledby="hero-title">
      <div className="story-sticky">
        <div className="story-grid content-width">
          <div className="story-copy">
            {copy}
            {mode !== "static" && <>
              <StoryCaption active={active} onSelect={select} label="The story, step by step" />
              <div className="story-hint">
                {mode === "pinned" && <m.span className="story-scroll-hint" style={{ opacity: hint }} aria-hidden="true"><ArrowDownIcon size={14} /> Scroll to see it come together</m.span>}
                <button type="button" className="story-skip" onClick={skip}>Skip visual story</button>
              </div>
            </>}
          </div>
          {mode !== "static" && <Stage mode={mode} progress={progress} role={role} onRole={setRole} onCardFocus={onCardFocus} />}
        </div>
        {mode === "static" && <div className="content-width"><StaticStory role={role} onRole={setRole} /></div>}
      </div>
    </section>
  );
}
