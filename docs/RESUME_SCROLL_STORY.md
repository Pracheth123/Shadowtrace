# "Papers to Practice" scroll story

Updated October 8, 2026. This is the homepage hero:
`client/src/components/home/ResumeScrollStory.tsx`. It replaced the earlier
hero demo (tilt card, play/pause interview demo) and the sticky "How it works"
workflow with its beam connector. Those told the same story a second time, and
they have been removed.

**Concept: your experience becomes a conversation.** One person's papers (a
resume, plus projects, skills, work experience and a work sample) fly in, gather
into a background, become an interview question, and end as a coaching note that
quotes the answer. The papers are one candidate's own material, not competing
candidates. All content is a prewritten example. The label "Illustrative example
— not a live assessment" is visible in every state. The scene makes no API
calls, creates no guest, requests no microphone or camera, and processes no
documents.

## Files

| File | Role |
|---|---|
| `ResumeScrollStory.tsx` | Mode selection, section scroll progress, stage measurement, connector, step jumps, skip control, static fallback |
| `story-data.ts` | Stage ranges, captions, role-specific example questions/answers/coaching, and the paper/card trajectories (data, not code) |
| `story-motion.ts` | `usePose`: keyframes for each element → `x / y / rotate / scale / opacity` motion values |
| `ResumePaper.tsx` | Original HTML/CSS paper artwork (resume, projects, skills, experience, work sample) |
| `StoryCards.tsx` | Interview card (semantic tabs) and feedback card |
| `StoryCaption.tsx` | The four steps as an ordered list of buttons |
| `home.css` | Layout, paper artwork in paper-relative units, cards, responsive and reduced-motion rules |

## Stage ranges

Progress is **local to the story section**: `useScroll({ target: section,
offset: ["start start", "end end"] })`. It is 0 when the section's top reaches
the top of the viewport, and 1 when its bottom reaches the bottom. Each stage
holds still for most of its range, so captions have time to be read. Movement
happens in the short windows between holds.

| Stage | Range | Hold | What happens |
|---|---|---|---|
| A — scattered experience | 0.00–0.22 | 0.00–0.08 | Five angled sheets at different positions and scales. A one-time entrance on load (≈1.2 s) brings them in. |
| B — a background takes shape | 0.22–0.48 | 0.26–0.44 | The sheets converge into a neat stack with the resume on top (scaled up as the focal sheet). Two example statements get marigold highlighter marks (0.27–0.34, 0.31–0.38). |
| C — experience becomes a question | 0.48–0.75 | 0.56–0.72 | The stack moves up-left. The interview card rises in, and a thin SVG connector draws (0.52–0.60) from "I helped launch a new service." to the interviewer question. HR / Hiring manager / Specialist tabs give three different questions from the same statement, each with an example answer. |
| D — answers become coaching | 0.75–1.00 | 0.84–1.00 | The supporting sheets leave the stage. The interview card moves up, and the feedback card comes forward. It quotes words from the **example answer** (e.g. "checked support requests after launch") and gives one prewritten coaching point. There are no scores, metrics or outcomes. |

Every move has a midpoint keyframe. At the midpoint, x has covered 66% of the
distance and y 34%, with ease-in into the midpoint and ease-out out of it. Moves
are therefore gently curved paths rather than straight slides. The keyframes are
fixed, so the same scroll position always produces the same composition: in
either direction, after a fast scroll, or when landing halfway. Ranges are
tunable in `story-data.ts` (`HOLDS`, `stageOf`, `STAGE_TARGET`).

## Modes

| Mode | When | Behaviour |
|---|---|---|
| **pinned** | `(min-width: 1024px) and (min-height: 760px)`, normal motion | The section is 200svh tall, and its inner viewport is `position: sticky` for one screen of scroll, then releases into the page. Transforms follow scroll progress directly (no spring, so nothing lags the scrollbar). Five papers. |
| **compact** | Phones, tablets and short desktops (for example 1440×700) | No pinning and no tall empty section. Three papers with smaller travel, and full-width cards. The step list is the set of labelled manual controls. Tapping a step moves the same composition there with a modest spring. |
| **static** | `prefers-reduced-motion: reduce` (any size) | No paper flight, rotation, parallax or pinning. The four steps render as readable panels (papers, highlighted resume, interview card with working tabs, feedback card). |

Shared details:

- The headline, navigation and "Prepare my interview" stay in their own column,
  above the stage layer, and are clickable from the first screen. The browser
  check confirms that `elementFromPoint` hits the CTA at every stage.
- Decorative papers and the connector are in an `aria-hidden`,
  `pointer-events: none` layer clipped by `.story-clip`. The sticky ancestors
  have no overflow or transform, so sticky positioning works. Cards, tabs,
  captions and controls sit outside that layer.
- The step buttons jump to a stage, smoothly scrolling to its hold on desktop.
  Focusing the interview tabs before stage C brings the card on stage.
- **Skip visual story** scrolls to "How it works" and focuses its heading. It
  does not change `location.hash`, so hash routing is unaffected.
- The scroll hint fades out within the first 6% of the section and nudges twice
  at most. There are no endless animations.
- Native wheel, touch, Page Down, End and keyboard scrolling are untouched. There
  is no snapping, scroll hijacking or loading gate.

## Performance notes

- React state changes only when the stage index changes (`useMotionValueEvent`
  with a threshold), when a tab is chosen, or when the stage is resized. Pointer
  and scroll frames update motion values only.
- Stage size is measured with a ResizeObserver. Paper and card sizes use
  container query units (`cqmin`, `cqw`), and each paper's internal artwork is
  sized in a paper-relative unit (`--u`), so the composition is not tied to one
  viewport.
- Each paper has its scroll transform on `.paper-flight` and its load entrance on
  `.paper-enter`, so the two never fight over `transform`. Shadows are static.
  There is no `will-change`, blur animation, WebGL or video.
- **Motion and native scroll timelines.** Motion 13 can hand values derived from
  `useScroll` to a native `ViewTimeline`. For this sticky section, that timeline's
  range did not match local progress: the hint and card opacities sat at about
  11% while the JS progress was 0.35. The story therefore mirrors
  `scrollYProgress` into a plain motion value, and all transforms derive from
  that.
- `domAnimation` is now loaded with the page instead of as an async chunk. The
  story is the first screen, and a lazily attached renderer missed the first
  stage measurement, so papers started at the centre.

### Bundle (production build, gzip)

| | Original (before both motion tasks) | Before this task | After this task |
|---|---|---|---|
| Main JS | 90.20 kB | 121.15 kB | **128.70 kB** |
| Async motion chunk | — | 10.78 kB | — (merged) |
| **Total JS** | **90.20 kB** | **131.93 kB** | **128.70 kB** (−3.23 kB vs before; +38.50 kB vs original) |
| CSS | 9.07 kB | 11.85 kB | 11.20 kB |

No dependency was added in this task. It uses the existing `motion@13.4.1`.

## Verifying

```powershell
cd client
npm ci
npm run build
cd ..\tools\ui-test
npm install
npx playwright install chromium
npm run homepage   # 26 story checks; recordings and screenshots, no backend
npm test           # full journey (needs the root .venv; see UI_REFRESH.md)
```

`npm run homepage` writes the following to `logs/ui-smoke/homepage/`:

- **Recordings:** `story-desktop-scroll.webm` (scroll down through A→D, tab
  changes, release, then reverse scroll), `story-desktop-checks.webm` and
  `story-mobile.webm`.
- **Desktop stages:** `desktop-A-scattered.png`, `desktop-B-gathered.png`,
  `desktop-C-interview.png`, `desktop-D-feedback.png`.
- **Other screenshots:** `w{width}x{height}-stage{A–D}.png` for 320×700, 390×844,
  768×1024, 1024×768, 1440×900 and 1440×700, plus `mobile-stage-1…7.png` and
  `reduced-motion-*.png`.
- `results.json` with measurements.

What it checks:

- **Load and first screen:** papers enter once with no endless animations. The
  headline, nav and CTA are clear at every stage.
- **Pinned scrolling:** a section 2.0 viewports tall with a pinned stage.
  Scattered → stacked spread of 278 → 53 px, with a curved midpoint (x leads y).
- **Stages C and D:** the connector at C, then supporting papers leaving at D.
  The quote comes from the answer, not the resume, and there are no
  metrics/scores.
- **Tabs:** pointer, arrows, Home/End, and the tab↔panel ids.
- **Scroll behaviour:** exact restoration on reverse scroll, rapid jumps and
  mid-range landing, and wheel/Page Down/End with no snapping.
- **Controls:** step jumps, focus bringing the card on stage, and skip control
  focus with the hash unchanged.
- **Layout:** all widths plus the short desktop with no overflow, cards inside
  the stage and no clipped controls, and resizing across modes.
- **Mobile and reduced motion:** tap-through on mobile, and the static
  reduced-motion story at 1440 and 390 px.
- **Network and devices:** zero network requests and zero device requests from
  the scene.
- **Accessibility:** axe WCAG 2 A/AA at desktop stage C, mobile, and reduced motion.

## References and licences

The references were studied for ideas; no code, images, fonts or brands were copied.

- **Codrops ScrollAnimationsGrid** (https://github.com/codrops/ScrollAnimationsGrid,
  MIT): coordinated scroll-linked card movement with per-item depth. The demo
  uses GSAP ScrollTrigger and Lenis, which were *not* installed.
- **Codrops ImageStackGrid** (https://github.com/codrops/ImageStackGrid, MIT):
  choreographing several cards into an ordered composition. Its intro-animation
  idea was adapted to a scroll-driven stack.
- **Motion** (`motion@13.4.1`, MIT): `useScroll`, `useTransform`,
  `useMotionValueEvent`, `useSpring` and `useReducedMotion`, using the free core
  only.
- **Magic UI** (MIT): its Animated Beam was the reference for a restrained SVG
  connector. The story's connector is its own code: a cubic path between two
  computed anchors, drawn with `pathLength`. The earlier adapted `ProcessBeam.tsx`
  has been removed.

The paper artwork, example statements, questions, answers and coaching points
are original to this project. See `docs/LICENSES.md`.

## Limitations

- Only headless Chromium was tested. Safari and Firefox were not, and nor were
  real touch devices or trackpads (Playwright's synthetic wheel and touch were used).
- The pinned breakpoint (1024 × 760) is a judgement call. Desktops shorter than
  760 px get the compact stepper rather than a pinned stage.
- Stage ranges were tuned visually at 1440×900 and 1024×768 and checked at the
  other sizes. Very wide or unusual aspect ratios may want their own poses in
  `story-data.ts`.
