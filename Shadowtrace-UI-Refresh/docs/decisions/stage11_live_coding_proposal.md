# Proposal — live coding pack (NOT BUILT)

Stage 11 item 6. The brief marks this **proposal only**: it needs explicit
approval before any of it is built. Nothing in this document is implemented.
No code, no pack file, no client editor, no schema field exists for it.

## What it would be

The candidate solves a short problem in an in-browser code editor while the
interviewer agent watches the buffer. If the solution is correct, the agent adds
a twist; if it is not, it probes the specific mistake rather than saying "that's
wrong".

## Two hard constraints from the brief

1. **Code is never executed on our server.** No sandbox, no container, no
   subprocess, no `eval`. Not "sandboxed carefully" — not run at all.
2. **Any correctness check is the model reading the code.** There is no test
   runner and no expected-output comparison, because both of those are a form of
   "score the answer by similarity to a model answer", which CLAUDE.md lists as
   removed and which contract 8 forbids.

Together these mean the pack cannot know whether code "passes". It can only
know what a model said when it read the code — which is a *finding with a
quote*, exactly like every other evaluator finding, and must be scored the same
way.

## How it would fit the existing architecture

It would be a **pack plus one new signal**, not a new layer.

- **Pack data.** A `live-coding` pack whose spine items carry a problem
  statement and a starting buffer. Still asked verbatim, still in order, still
  guard-enforced. Adding a format means adding a YAML file.
- **Candidate input.** The editor buffer arrives as a candidate turn on the same
  bus, the way `candidate_text` does in the text lane. The agent and guard below
  see the same shape they always do.
- **A new tool, not a new band.** `get_code_buffer()` — local, in-memory, under
  20 ms, no network — returning the current buffer as *data*. The live agent
  gets one more read tool; nothing else changes. Contract 7 applies: the buffer
  is delimited data and never an instruction, and the same
  instruction-stripping the Claims File uses would apply to comments in the
  code.
- **Probe depth.** Unchanged. A twist is a probe at depth+1 and the guard's
  intensity cap already bounds it.
- **Delivery.** Not assessed, for the same reason as the text lane: there is no
  spoken answer. The `assessed=False` machinery built this stage already covers
  it.

## What would need deciding before building

These are the questions that make this a proposal rather than a task:

1. **Is "the model read the code and thinks it is wrong" a fair input to a
   score at all?** It is a model judgement about correctness with no execution
   to check it against. The honest options are (a) it produces findings with
   quotes like any evaluator and feeds `technical` the same way, or (b) it only
   steers probes and never reaches the scorer, like keyword matching under
   contract 8. I would argue for (b) until there is evidence the judgement is
   reliable, which means the coding pack would improve the *interview* without
   changing the *score*. That is a product decision, not an implementation one.
2. **Buffer cadence on the live path.** Sending every keystroke would make
   `likely_next` fire constantly and defeat the speculative loop. A debounced
   snapshot (on pause, or on the candidate pressing "I'm ready") keeps the
   signal bus cheap, but changes what "think while the candidate talks" means
   when the candidate is typing rather than talking.
3. **Latency budget.** The ~450 ms target assumes the agent is responding to
   speech. A twist after a correct solution is not latency-critical in the same
   way; the budget for a coding turn probably needs its own number, measured.
4. **A new dependency.** An in-browser editor (CodeMirror or Monaco) is a
   client dependency with a real size cost. CLAUDE.md requires stating the
   reason in the commit that adds it, and requires asking first.
5. **Claim interrogation still has to be the point.** A coding problem from a
   pack is generic by definition, which cuts against "questions grounded in the
   candidate's own repository". The version worth building is probably a problem
   *derived from the candidate's own repo* via the stage-6 index — which is a
   considerably larger piece of work than a problem bank.

## Recommendation

Do not build it as specified. Question 5 is the substantive one: a generic
problem bank is the one part of this platform that would work the same for
everybody, which is the opposite of what makes the rest of it work. If the
coding pack is wanted, the version to approve is "a short problem derived from
the candidate's own repo, where correctness only steers probes and never
reaches the score" — and that should be scoped as its own stage, not as item 6
of a hardening stage.

Awaiting a decision.
