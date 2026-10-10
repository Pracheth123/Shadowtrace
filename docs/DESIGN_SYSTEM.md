# Design system (Stitch identity, October 10, 2026)

This replaces the October 8 "editor's desk" palette described in
[VISUAL_IDENTITY.md](VISUAL_IDENTITY.md); that file is kept as history.

**Source of truth:** the `:root` block in `client/src/styles.css`. Tailwind's
colour, font, radius and shadow tokens (`client/tailwind.config.js`) read those
CSS variables. Change a value there, never in a page rule. Older variable
names (`--paper`, `--pen-*`, `--mark-*`, `--graphite-*`) still exist as aliases
mapped onto the tokens below, so no surface keeps the previous palette.

The Stitch export itself was not available in this checkout. The tokens come
from the values the brief specifies; the homepage's existing four-stage
"papers" scroll story was restyled rather than replaced (see "Motion").

## Colour

| Token | Value | Use |
|---|---|---|
| `--charcoal` | `#111215` | Navigation shell, footers, the homepage hero stage, the interview stage, the setup summary |
| `--charcoal-raised` / `--charcoal-line` | `#1b1c20` / `#2c2d33` | Panels and fine rules on charcoal |
| `--on-charcoal` / `--on-charcoal-muted` | `#f5f3ed` / `#b9b6ad` | Text on charcoal (both AA on `#111215`) |
| `--ivory` | `#fbf9f3` | Workspace background |
| `--surface-1/2/3` | `#f5f3ed` `#f0eee8` `#eae8e2` | Sunk panels, source excerpts, controls |
| `--text` | `#1b1c18` | Main text on light surfaces |
| `--cobalt` / `--cobalt-deep` | `#2451b2` / `#003994` | Primary actions / hover and active text |
| `--brass` `--brass-light` `--brass-detail` | `#f2be63` `#fec96d` `#d4a34b` | **Annotation only**: quote rules, highlighted source spans, active-step markers, the focus ring on charcoal |
| `--brass-tint` / `--brass-ink` | `#fbf0d8` / `#6e4e0c` | Warning surfaces and their text |
| `--red-600` | `#a8381f` | Destructive actions and errors |

Rules:

- Brass is never text on ivory, and never the only signal of a state.
  Selected controls are also charcoal-filled and bold; the active nav link and
  tab are bold with an underline; the voice-availability states use different
  marker shapes and words; disputed findings get a stamp and a hatched surface.
- Every text pairing used is WCAG AA. axe colour-contrast checks run on home,
  setup, source review, the room, feedback and practice in `tools/ui-test`.

## Type

| Role | Family | Where |
|---|---|---|
| Headings and the brand | **Syne** 600–750 (variable 400–800) | `h1`–`h3`, the wordmark, finding titles, FAQ questions |
| Quotes and expressive narrative | **Newsreader** (variable, optical size) | Candidate quotes, extracted statements, the interviewer's question, hero emphasis |
| Short labels and metadata | **JetBrains Mono** 500–600 | Eyebrows, step numbers, `meta-label`, status tags, keyboard hints. Never paragraphs or forms. |
| Interface and body text | **Schibsted Grotesk** | Forms, buttons, body copy (kept for readability) |

All four are self-hosted WOFF2 with `font-display: swap`, latin subset. Licences
and checksums: [LICENSES.md](LICENSES.md#fonts-self-hosted-october-8).

## Shape, spacing, elevation

- Radius: `--radius` 8px for large surfaces; 4px controls and slips; 3px stamps.
- Spacing scale `--space-1…8` (4–72px). Sections use 24–32px internal padding.
- Two shadows only: `--shadow-raised` (setup sheet) and `--shadow-paper`
  (evidence slips, homepage papers). Most surfaces rely on a 1px rule instead.
- Focus: a 2px cobalt ring offset on light surfaces, brass on charcoal
  (`.on-charcoal`). Outlines are never removed from controls.

## Workspace compositions

| Page | Composition |
|---|---|
| Shell | Charcoal header (brand, real destinations, "Browser guest" chip), ivory main, charcoal footer. The room shows "Practice room" instead of navigation. |
| Home | Charcoal hero stage with ivory papers and cards; charcoal profession strip; ivory reading sections; charcoal closing band. |
| Setup | A numbered preparation sheet beside a sticky charcoal "ticket" summarising the choices; consent and the primary action at the foot of the sheet. |
| Source review | Extracted statements as **evidence slips** labelled "Extracted statement · unverified", with the source excerpt and its brass-highlighted match; keep / correct / exclude per slip; a charcoal action bar after the list. |
| Interview room | A quiet charcoal stage: status (dot + words), three-round indicator, progress, and the question on an ivory panel with a brass rule. Ivory answer, controls and transcript below. No decorative motion. |
| Feedback | Editorial report: context line in mono, notices, then findings split into **The question → Your words → Interpretation · can be challenged → Next step**. Disputed findings carry a stamp. Downloads grouped; data deletion in its own zone. |
| Practice | The target gap with the original quote, the checklist, start controls, then before/after as two labelled columns (stacked on phones) with an outcome tag. |

## Motion

- Homepage: the existing scroll story (pinned ≥1024×760, compact otherwise,
  static under reduced motion), restyled only. Hidden cards are inert; no
  320vh track; the illustrative scene makes no API calls (checked).
- Workspace: hover/focus colour changes (150ms), disclosure rotation (240ms),
  nothing on transcript lines or polling. No pulse on the microphone button;
  a steady ring marks an open microphone.
- `prefers-reduced-motion: reduce` collapses every transition and animation.

## Code splitting

Pages load per route (`React.lazy` in `App.tsx`). The homepage and the Motion
library never load on setup, the room, feedback or practice.
