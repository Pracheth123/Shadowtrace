# Planning

Architecture, contracts, stack, and stage order live in
[`claude files/CLAUDE.md`](claude%20files/CLAUDE.md). That file wins on conflict
with a stage brief.

Active work is tracked in [`TASK.md`](TASK.md). Stage briefs are in
[`claude files/`](claude%20files/).

## Current focus

Stage 11 is built. Panel mode, behind `PANEL_MODE`, puts two or three interviewer agents in the room sharing one guard: they are handed the same `SessionTools`, so the spine is still asked verbatim in pack order no matter who speaks. One voice speaks at a time, each persona has its own TTS voice from the pack roster, and the session still produces one evaluation rather than one per persona.

Alongside it: the text lane (same bus, agent, guard and evaluation, with delivery not assessed rather than scored from silence), six logged fallbacks, a display-only camera preview that never reaches the server, and hardening — WS reconnect onto the same log, a session cap, an intake rate limit, and delete-my-data that returns a manifest.

The live-coding pack is **not built**. It remains a proposal awaiting explicit approval; the recommendation is in `docs/decisions/stage11_live_coding_proposal.md`.

Open across stages 7 and 11: the live-vendor waterfall. Every latency number on record is from the mock path because `GROQ_API_KEY` is unset and no TTS vendor is configured. The ~450 ms single-voice and 1.2 s panel budgets are unverified against a vendor.
