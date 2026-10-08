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
System fonts replace the external font requests; no new font assets are bundled.

The optional browser test harness uses Playwright and axe-core. These test-only
packages are installed separately from the application; their upstream licences
apply. Browser binaries are not included in the source archive.
