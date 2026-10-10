# Landing page motion and interaction

Updated October 8, 2026. Scope: the homepage only (`client/src/pages/HomePage.tsx`
and `client/src/components/home/`). Setup, the interview room, feedback, history
and practice are unchanged.

The hero is the **"Papers to Practice" scroll story**. It has its own document:
[RESUME_SCROLL_STORY.md](RESUME_SCROLL_STORY.md), which covers stage ranges,
pinned/compact/static modes, the role tabs, the connector, reduced motion,
bundle sizes and references. That story replaced an earlier hero (tilt card and
play/pause interview demo) and a sticky "How it works" workflow with a beam
connector, which told the same story twice. This page covers everything else on
the homepage.

## Principles

- **One curve, three durations.** Everything uses `cubic-bezier(0.22, 1, 0.36, 1)`
  (`EASE_OUT` in `components/home/motion.ts`, `--home-ease` in `home.css`):
  200 ms for small responses, 320 ms for swaps, and 600–950 ms for entrances
  and reveals.
- **Transform and opacity only.** No layout-shifting entrances, scroll
  hijacking, endless loops, custom cursor or hover-only information.
- **Content first.** The hero copy entrance is CSS keyframes, so it never waits
  for JavaScript. Section reveals use a native `IntersectionObserver` and only hide
  sections that start below the fold. Printing shows everything.
- **No per-frame React state.** Scroll-linked values are Motion values. React
  state changes only on discrete events.

## Library

[Motion](https://github.com/motiondivision/motion) `motion@13.4.1` (MIT), pinned
exactly. `package.json` overrides pin `framer-motion`/`motion-dom` 13.4.1 and
`motion-utils` 13.3.0. The homepage uses `LazyMotion` with `domAnimation`,
loaded with the page, and `m` components. `MotionConfig reducedMotion="user"`
applies the OS setting. No Motion+ APIs are used.

## Other homepage motion, and what reduced motion does

| Experience | What moves | Reduced motion |
|---|---|---|
| **Hero copy entrance** | Eyebrow, three headline lines (80 ms stagger), description and actions rise 0.45 em and fade in; done by ~1.1 s. | No animation. |
| **Buttons and arrows** | Arrow icons nudge 3 px on hover/focus, and buttons press down 1 px. Text-only actions have 32 px+ hit areas. | No movement. |
| **How it works** | A plain three-step list, revealed once. | Present immediately. |
| **Practice note → comparison** (`PracticeCompare.tsx`) | The tilted note lifts away. An "Illustrative example · not your feedback" card shows the original wording, a clearer example and one coaching point. The dashed toggle is distinct from the "Find my starting point" CTA, which opens real setup. | Instant swap. |
| **FAQ** | Native `<details>`. Where `::details-content` and `interpolate-size` are supported (current Chromium), the height animates over 320 ms, the answer fades in and the chevron rotates. Elsewhere it opens instantly. | Instant. |
| **Section reveals** (`Reveal.tsx`) | Sections below the fold rise 22 px and fade in once. | Fade only (instant under the global rule). |

The global `prefers-reduced-motion` rule in `styles.css` shortens any other CSS
transition to effectively zero.

## Verifying

`npm run homepage` in `tools/ui-test` (after `npm run build` in `client`) runs
the story check, which also covers the practice comparison, FAQ, CTAs,
overflow, accessibility and network isolation. See
[RESUME_SCROLL_STORY.md](RESUME_SCROLL_STORY.md#verifying).
