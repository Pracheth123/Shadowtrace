# Shadowtrace UI refresh and project review

Updated October 8, 2026. Based on `Shadowtrace-main (5).zip`.

## What changed

The app previously opened straight into a long setup form. It now opens with a
complete homepage explaining the product and leading into the working interview
journey. The current visual identity (Stitch, October 10) is described in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md); the October 8 identity is in
[VISUAL_IDENTITY.md](VISUAL_IDENTITY.md); it replaced the original warm-white /
forest-green / system-font treatment on October 8.

The homepage includes an interactive HR / hiring manager / specialist preview,
a three-step workflow, targeted-practice explanation, expandable questions and
clear start/history actions. Preview questions and the practice note are labelled
as illustrative; they are not presented as actual results or customer evidence.

Setup groups background, target role and interview preferences into numbered
sections. Optional context is collapsed, a live summary reflects the current
choices, and source review has a visible step indicator. Processing consent
remains required. Typing is the default, and speaking is unavailable when the
speech provider is not configured, including on the targeted-practice screen.
Provider configuration is described accurately; a configured key is not a
promise of verified connectivity.

The practice room has a prominent question panel, readable transcript bubbles,
clearer controls and consistent branding. Feedback, history and targeted
practice share the same layout, navigation, colours and typography. Existing
source review, evidence, disputes, retries, practice and comparisons are retained.

Responsive layouts were checked at 320, 390 and 768 pixels. Keyboard navigation,
visible focus, connected input/upload labels, disabled radio navigation and
contrast were checked. Camera streams now stop when the room unmounts; holding
push-to-talk uses pointer capture and handles release outside the button.
Those voice-control changes still need a live microphone/playback test.

## Functional fixes found during testing

1. **Feedback could wait indefinitely after natural completion.** A final reply
   could end the runtime while the WebSocket receive loop was waiting for the
   browser. The browser displayed completion but evaluation was not queued until
   another message or disconnect. The receive loop now listens for completion,
   finalises the session and queues evaluation immediately, then closes normally.
   A WebSocket regression test verifies this without a client `session_end`.
   This fixes a handoff delay; it does not eliminate provider rate limits or
   real evaluation latency.
2. **Guest identity could race or disappear during temporary outages.** Concurrent
   requests now share one guest-resolution promise. A temporary `/api/me` error
   preserves the stored key instead of creating a new guest and hiding history.
3. **CORS ignored the configured origins.** Middleware now uses `ALLOWED_ORIGINS`
   rather than accepting every origin. Allowed/refused preflights are tested.
4. **Built clients assumed the API lived on port 8000.** Production defaults now
   use the page's origin for HTTP and WebSocket connections. Development still
   uses backend port 8000; `VITE_WS_HOST` remains an explicit override. A reverse
   proxy must serve the frontend and forward `/api`, `/health`, and `/ws`.
5. **Unnecessary downstream import failed an architecture check.** Removed the
   roadmap-store import used only for a type annotation in the live server.

## Validation

- Offline backend suite: **328 passed, 1 live test deselected**.
- TypeScript check and Vite production build: passed.
- Headless Chromium journey: homepage → setup → intake → source review → all
  three interview rounds, 24 typed answers → feedback → dispute/withdrawal →
  targeted practice/coaching → practice attempt/comparison → history → reload →
  authenticated transcript download. Guest creation and simulated 503 recovery
  were also exercised.
- Automated axe checks: no violations of the selected WCAG 2 A/AA and WCAG 2.1 AA
  rules on homepage desktop/mobile, setup, source review, room, feedback and
  practice. This is automated coverage, not a complete accessibility audit.
- The same journey is also checked against the built frontend served on the
  backend origin, to exercise production URL selection.

AI was explicitly mocked in browser tests. The API, WebSocket, persisted reports,
disputes and practice used the real application code. Mock reports are visibly
labelled as placeholders. No provider keys, real personal documents or billable
model calls were used. Real Groq/Deepgram latency, speech accuracy, microphone,
playback/barge-in and AWS deployment were **not** validated here.

## Run locally on Windows

Extract the new project to a separate folder. Keep your original `.env` and
candidate data as a backup. Do not copy an old `.venv` or `node_modules` into it.

In PowerShell, from the extracted repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
notepad .env
```

For a free local UI preview, set `APP_ENV=dev`, `ALLOW_MOCK_PROVIDERS=1`,
`MOCK_LLM=1`, and leave both provider keys empty. The interface will identify
this as development mode and label the feedback as a placeholder.

For real AI, set `MOCK_LLM=0`, fill `GROQ_API_KEY`, and fill `DEEPGRAM_API_KEY`
for speaking. These remain backend secrets. This release adds no required API
provider or paid design service. Keep these allowed frontend origins:

```dotenv
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Terminal 1, repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn interview.server:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
cd client
npm ci
npm run dev
Start-Process "http://localhost:5173"
```

In the UI: click **Prepare my interview**, enter your target role and background,
choose a round and typing/speaking, accept processing consent, then prepare.
Review the extracted statements and click **Save and start the interview**.
Answer the questions, then open feedback. A shorter typed interview is a good
first check before testing the live voice path.

## Repeat the browser check

The optional test tools are separate from application dependencies. Close other
local servers on ports 8000/5173 first. Install them once:

```powershell
cd tools\ui-test
npm install
npx playwright install chromium
npm test
```

The script locates the root `.venv`, explicitly blanks provider keys, starts its
own backend/frontend, uses temporary candidate storage and saves screenshots
and a machine-readable result under `logs/ui-smoke`. It does not use your stored
candidate data. `UI_TEST_PYTHON` can override the Python interpreter.

To check the production bundle, first run `npm run build` inside `client`, then
from `tools/ui-test`:

```powershell
$env:UI_TEST_PRODUCTION="1"
npm test
Remove-Item Env:UI_TEST_PRODUCTION
```

The temporary static mount is a test harness, not a production deployment setup.

The homepage scroll story has a separate check that needs no backend: build the
client, then run `npm run homepage` from `tools/ui-test`. See
[RESUME_SCROLL_STORY.md](RESUME_SCROLL_STORY.md) and
[LANDING_PAGE_MOTION.md](LANDING_PAGE_MOTION.md).

## Remaining changes before public AWS deployment

- `MAX_SESSION_SECONDS` is validated in settings but is not currently a server
  hard wall-clock limit. Round/turn budgets do not close an idle socket. Enforce
  an operator deadline across reconnects before opening a credit-funded demo to
  unrestricted traffic.
- Active sessions, reconnect state and evaluation jobs live in the web process.
  Use one worker for the initial deployment; do not claim horizontal scaling or
  durable background work. Restarts can interrupt sessions/jobs.
- Serve the built client and proxy HTTP/WebSocket routes with HTTPS. Keep data
  on persistent storage, apply the configured origin list, and test a real voice
  session through the deployed proxy before judging.
- Feedback indicators still need human-labelled validation. Preserve the
  coaching limitations and the ability to dispute evidence.
- Confirm the repository licence and the previously noted asset/code provenance
  in `docs/LICENSES.md` before public redistribution.

## Design references

- [Radix Themes source](https://github.com/radix-ui/themes)
- [Radix Themes playground](https://www.radix-ui.com/themes/playground)
- [shadcn/ui source](https://github.com/shadcn-ui/ui)

These informed accessible control patterns, spacing and restrained visual
hierarchy. The new layout and artwork are original. No paid template is required.
