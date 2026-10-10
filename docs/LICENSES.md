# Licences and attribution

## This repository

There is **no `LICENSE` file** in the repository at the time of writing
(2026-10-06). Until the team adds one, the code is all-rights-reserved by its
authors by default. This document does not grant any rights; choosing a licence
is the team's decision.

## Runtime dependencies (Python)

Versions from the clean install recorded in `requirements-lock.txt`; licence as
declared in each package's metadata.

| Package | Version | Licence |
|---|---|---|
| pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| fastapi | 0.142.2 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| aiofiles | 25.1.0 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| websockets | 17.2 | BSD-3-Clause |
| numpy | 2.5.3 | BSD-3-Clause (bundles 0BSD, MIT, Zlib components) |
| openai | 3.24.0 | Apache-2.0 |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| pypdf | 6.19.0 | BSD-3-Clause |

Test only: pytest 9.1.1 (MIT), pytest-asyncio 1.4.0 (Apache-2.0).
Transitive dependencies are listed in `requirements-lock.txt`; check their
licences with `pip show <name>` before redistribution.

## Client dependencies (npm, direct)

| Package | Version | Licence |
|---|---|---|
| react, react-dom | 18.3.1 | MIT |
| lucide-react | 0.441.0 | ISC |
| recharts | 2.15.4 | MIT |
| wavesurfer.js | 7.12.12 | BSD-3-Clause |
| clsx | 2.1.1 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| tailwind-merge | 2.6.1 | MIT |
| motion (with framer-motion, motion-dom 13.4.1; motion-utils 13.3.0) | 13.4.1 | MIT |
| vite | 5.4.21 | MIT (dev) |
| typescript | 5.9.3 | Apache-2.0 (dev) |
| tailwindcss | 3.4.19 | MIT (dev) |
| @vitejs/plugin-react | 4.7.0 | MIT (dev) |
| autoprefixer | 10.6.1, postcss 8.5.28 | MIT (dev) |

## External services (not redistributed)

Groq (model inference) and Deepgram (speech) are used through their APIs under
the team's own accounts and those providers' terms. Their models are not
included in this repository.

## Code and assets whose origin the team should confirm

- `src/interview/intake/resume_parser.py`, `sanitize.py`, `fit.py`, `schema.py`
  describe themselves as "adapted from skill-sync". The origin and licence of
  skill-sync are not recorded in this repository: _unfilled — the team should
  confirm authorship or licence_.
- `fixtures/audio/cached_response.wav`: origin not recorded. _unfilled_.
- `evaluation_set/examples.jsonl`: synthetic, authored for this repository with
  a development assistant during stage 16; no third-party data.
- Pitch deck or other materials outside this repository: not inventoried.

## October 8 UI refresh references

The original homepage layout, SVG brand mark, favicon and application styling
were authored for this project. Design references: [Radix Themes](https://github.com/radix-ui/themes),
[Radix Themes playground](https://www.radix-ui.com/themes/playground), and
[shadcn/ui](https://github.com/shadcn-ui/ui). No source or artwork from these
references was copied. The existing upload component identifies Origin UI as
its source; the team should confirm its original provenance and licence.
No external font host is contacted. Font files are self-hosted (see "Fonts" below).

The optional browser test harness uses Playwright and axe-core. These test-only
packages are installed separately from the application; their upstream licences
apply. Browser binaries are not included in the source archive.

## October 8 landing-page motion and "Papers to Practice" story

- **Motion** (`motion@13.4.1`, https://github.com/motiondivision/motion), MIT,
  Copyright (c) 2024 Motion B.V. (from the package's LICENSE.md). Installed from
  npm; free core APIs only, with no Motion+ APIs. This is the only animation
  library in the client.
- **Codrops ScrollAnimationsGrid** (https://github.com/codrops/ScrollAnimationsGrid)
  and **Codrops ImageStackGrid** (https://github.com/codrops/ImageStackGrid), both
  declared MIT. These were references for scroll-linked card choreography and
  stack-to-composition movement. No code, images, fonts or brands were copied, and
  their GSAP/Lenis stack was not installed.
- **Magic UI** (https://github.com/magicuidesign/magicui), MIT, Copyright (c)
  Magic UI. Its Animated Beam was the reference for a restrained SVG connector.
  An earlier adaptation (`ProcessBeam.tsx`, which carried Magic UI's notice) has
  been removed. The story's connector in `ResumeScrollStory.tsx` is separate code
  (a cubic path between computed anchors, drawn with `pathLength`), and no Magic
  UI source remains in the repository. Nothing was installed through the registry CLI.
- **React Bits Tilted Card**: this was inspiration only for an earlier tilt
  effect, which has since been removed. No code was used.
- The paper artwork (HTML/CSS) and all example content in
  `client/src/components/home/story-data.ts` (statements, questions, answers,
  coaching) were written for this project. They are labelled as illustrative
  examples, and they contain no personal information, testimonials, metrics or scores.

See `docs/RESUME_SCROLL_STORY.md` and `docs/LANDING_PAGE_MOTION.md`.

## Fonts (self-hosted, October 8)

Bundled as WOFF2 in `client/src/assets/fonts/` from the pinned npm builds
`@fontsource-variable/newsreader@5.3.0` and
`@fontsource-variable/schibsted-grotesk@5.3.0` (latin subset, downloaded as
files; no npm dependency added). Each family's licence text sits beside it.

| Family | Files | Licence | Copyright |
|---|---|---|---|
| Newsreader (variable: wght 200–800, opsz 6–72; normal + italic) | `newsreader-latin-opsz-{normal,italic}.woff2` | SIL OFL 1.1 (`OFL-Newsreader.txt`) | 2020 The Newsreader Project Authors (Production Type) |
| Schibsted Grotesk (variable: wght 400–900; normal + italic) | `schibsted-grotesk-latin-wght-{normal,italic}.woff2` | SIL OFL 1.1 (`OFL-SchibstedGrotesk.txt`) | 2023 The Schibsted-Grotesk Project Authors |

Added October 10 (Stitch redesign), from `@fontsource-variable/syne@5.3.0` and
`@fontsource-variable/jetbrains-mono@5.3.0`, downloaded as files with
`npm pack` (no npm dependency added). Latin subset, normal style only.

| Family | Files | Licence | Copyright |
|---|---|---|---|
| Syne (variable: wght 400–800; normal) | `syne-latin-wght-normal.woff2` (34,608 bytes) | SIL OFL 1.1 (`OFL-Syne.txt`) | 2019 The Syne Project Authors (Bonjour Monde) |
| JetBrains Mono (variable: wght 100–800; normal) | `jetbrains-mono-latin-wght-normal.woff2` (40,404 bytes) | SIL OFL 1.1 (`OFL-JetBrainsMono.txt`) | 2020 The JetBrains Mono Project Authors |

SHA-256: syne `68b623f0…7048`, jetbrains mono `18be4527…6a7e`.

SHA-256: newsreader normal `6e4f2958…d101`, italic `5dfcd10d…7506`;
schibsted normal `4c8b93f4…5e05`, italic `4c72510c…65f7`. The OFL permits
bundling and redistribution with the licence; the fonts may not be sold on
their own, and modified versions may not use the reserved font names.
