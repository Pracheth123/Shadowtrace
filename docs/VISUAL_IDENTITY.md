# Visual identity

Updated October 8, 2026. This replaces the earlier system-font, warm-white and
forest-green treatment. The tokens are defined once, in `client/src/styles.css`
(`:root`). Tailwind's colour tokens map onto them. Change a value there, not in
individual rules.

## Idea: an editor's desk

Shadowtrace reviews your papers, marks the evidence and helps you rewrite an
answer. The identity borrows the tools of that work:

- **graphite on newsprint** for text and surfaces
- **Prussian ink** for actions and the interviewer's voice
- a **marigold highlighter** for evidence (highlighted statements, quoted words,
  the selected round)
- a **red pencil** for annotation marks only

It deliberately avoids the stock looks of generated sites: no purple or indigo
gradients, no emerald SaaS green, no cream with terracotta, no glow, glass or neon.

## Type

| Role | Family | Notes |
|---|---|---|
| Headlines, quotes, statements on paper | **Newsreader** (Production Type) | An editorial serif with an optical-size axis. Display sizes get the fine high-contrast cut, and small quotes get the sturdier text cut, automatically. Italic is used for one emphasised phrase per heading. |
| Interface and body text | **Schibsted Grotesk** | A newspaper grotesk with crisp, slightly condensed forms. It stays legible at the small sizes the app uses for labels and metadata. |
| Small numbers (step and section numbers) | System monospace | Kept to tiny numerals only. |

Both are self-hosted variable WOFF2 files (`client/src/assets/fonts`,
SIL OFL 1.1), loaded with `font-display: swap`. No third-party font host is
contacted. Weight: about 375 KB uncompressed for all four files, cached after
the first visit. Tracking is tightened only on large serif headlines (−0.02em),
because serifs need far less tightening than the previous sans.

## Palette

| Token | Hex | Use |
|---|---|---|
| `--paper` | `#f3f2ee` | Page background |
| `--paper-raised` | `#fbfaf7` | Cards, sheets, inputs |
| `--paper-sunk` / `--paper-deep` | `#eae8e2` / `#e1dfd7` | Quiet panels, stage background |
| `--rule-soft` / `--rule` / `--rule-strong` | `#e4e1d9` / `#dad7ce` / `#c3bfb4` | Borders and dividers |
| `--graphite-500` | `#7b776e` | Icons and decoration only (3.98:1, **not for text**) |
| `--graphite-600` / `--graphite-700` | `#5d5a53` / `#45433e` | Secondary text (6.1:1 / 8.8:1 on paper) |
| `--ink` | `#1a1b1e` | Primary text (15.4:1) |
| `--pen-50` … `--pen-200` | `#edf0f5` … `#bfcadb` | Selected states; light text on dark ink |
| `--pen-400` | `#6f86ab` | Borders on dark ink, peaks in the live waveform |
| `--pen-600` | `#2e4a7a` | Links, emphasised italics, active text (7.9:1 on paper) |
| `--pen-700` | `#233c66` | Primary buttons, brand mark |
| `--pen-800` / `--pen-900` | `#1b2f50` / `#13223a` | Dark surfaces (interviewer card, live question, closing band); shadow tint |
| `--mark-100` / `--mark-200` / `--mark-400` | `#fbf1cc` / `#f5e19a` / `#e3be48` | Highlighter: fills, the selected round tab, the closing CTA; `-400` for underlines and rules |
| `--mark-700` | `#7d5b06` | Warning text |
| `--red-100` / `--red-600` | `#f7e3dd` / `#a93a26` | Red pencil: the "original wording" note, the status dot, the wordmark's full stop, destructive actions |

Every text pairing in use is WCAG AA or better. The automated axe checks pass on
all journey pages and the homepage at desktop, mobile and reduced-motion sizes.

## Rules of use

1. Prussian ink carries action and the interviewer's voice. Don't use it as a
   decorative wash.
2. Marigold marks **evidence or selection** only. If nothing is being pointed at,
   don't highlight it.
3. Red pencil is for annotation marks. It is never used for headlines, never
   used as a large fill, and never used to mean "you failed".
4. Surfaces are flat paper. Depth comes from short, ink-tinted shadows on sheets
   and cards, not from gradients or glows.
5. Use one italic phrase per heading, in `--pen-600`.

`Waveform.tsx` and `public/favicon.svg` need literal colour values (canvas and
SVG file). Keep them in step with `--pen-700`, `--pen-400` and `--paper`.
