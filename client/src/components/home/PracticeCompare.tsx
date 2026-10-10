import { useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { ArrowLeftIcon, SparkleIcon } from "lucide-react";
import { DURATION, EASE_OUT } from "./motion";

const flip = {
  initial: { opacity: 0, y: 14, rotate: -2 },
  animate: { opacity: 1, y: 0, rotate: 0, transition: { duration: DURATION.reveal * 0.75, ease: EASE_OUT } },
  exit: { opacity: 0, y: -8, transition: { duration: DURATION.quick } },
};

// The practice note turns into an example before/after comparison. It is an
// illustration only and never opens setup; the real CTA lives beside it.
export function PracticeCompare() {
  const [open, setOpen] = useState(false);
  return (
    <div className="note-visual" data-open={open ? "true" : "false"}>
      <AnimatePresence mode="wait" initial={false}>
        {!open ? (
          <m.div key="note" className="note-paper" {...flip} initial={{ ...flip.initial, rotate: -5 }} animate={{ ...flip.animate, rotate: -5 }}>
            <p>YOUR NEXT PRACTICE</p>
            <h3>Make your<br />contribution<br /><em>specific.</em></h3>
            <span>Explain what you owned.<br />Describe a decision.<br />Connect it to the result.</span>
            <span className="note-rule" />
          </m.div>
        ) : (
          <m.div key="compare" id="practice-compare" className="note-compare" {...flip}>
            <p className="compare-label">Illustrative example · not your feedback</p>
            <div className="compare-row compare-before"><span>Original wording</span><p>“I led the launch. We worked closely as a team and it was a big success.”</p></div>
            <div className="compare-row compare-after"><span>Clearer example</span><p>“I owned the rollout plan. I released to a small group first, removed a confusing step that support flagged, then launched fully.”</p></div>
            <div className="compare-coach"><SparkleIcon size={14} aria-hidden="true" /><p><strong>Coaching point:</strong> keep the team context, and add one decision that was yours and what it changed.</p></div>
          </m.div>
        )}
      </AnimatePresence>
      <button type="button" className="note-toggle" aria-expanded={open} aria-controls={open ? "practice-compare" : undefined} onClick={() => setOpen((v) => !v)}>
        {open ? <><ArrowLeftIcon size={14} aria-hidden="true" /> Back to the practice note</> : <>See an example rewrite</>}
      </button>
      <span className="note-side-label">ONE GAP. ONE FOCUSED ATTEMPT.</span>
    </div>
  );
}
