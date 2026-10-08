// One easing curve and a small set of durations for the homepage's discrete
// transitions (the practice comparison). The CSS in home.css uses the same curve
// (--home-ease). Scroll-linked motion is in story-motion.ts.
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

export const DURATION = {
  quick: 0.2,
  swap: 0.32,
  reveal: 0.6,
} as const;
