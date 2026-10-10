# Evaluation set (stage 16)

24 short question/answer examples for checking whether the evaluator's feedback
is a fair interpretation, relevant, and actionable. **No reviewer labels are
included.** Nothing here has been reviewed by a human yet; there is no measured
agreement, accuracy or user benefit.

## Provenance and licence

- **Authored for this repository** during stage 16 by the development assistant
  (Claude, used as a coding tool) at the team's request. The team should review
  and edit them before relying on them, and record that in
  `docs/hackathon/PROVENANCE.md`.
- **Synthetic.** No real candidate, employer, transcript or résumé was used. Any
  resemblance to real people or companies is unintended.
- Licensed with the rest of the repository. If the repository has no licence
  yet, these files have none either; see `docs/LICENSES.md`.

## Composition

30 examples: 17 software, 13 sales; HR, hiring-manager and specialist rounds.

| Category | Count | What it tests |
|---|---|---|
| strong | 4 | Specific, owned, reasoned answers — does feedback recognise them? |
| short | 2 | Correct but thin answers — asks for context without calling them wrong? |
| incomplete | 4 | Generic or "we" answers — are suggestions concrete and non-judgemental? |
| ambiguous | 4 | Hedged answers, a career gap — is "developing" kept distinct from "wrong"? |
| contradictory | 1 | Answer contradicts a resume statement — "needs clarification", never a lie/honesty judgement |
| unsupported_number | 2 | Big figures with no baseline or method — asks how it was measured, assumes neither true nor false |
| silent | 4 | Empty or "I don't know" — not assessed, no score, no invented finding |
| transcription_error | 4 | Mangled STT terms, self-corrections, spoken numbers — not penalised |
| malicious | 4 | Prompt injection in answers — no effect on levels, nothing leaked |
| source_injection | 1 | Prompt injection inside the *source material* (a resume claim) — treated as data, not obeyed |

Examples with a `claims` field pass those statements to the evaluator as the
candidate's source material, exactly as intake would.

### Splits

`split` is `heldout` for the first example of each category in file order (10
examples) and `dev` for the rest. The rule is mechanical and was applied before
any output was looked at. Use `dev` while changing prompts or models and
`heldout` only for before/after comparisons, so the comparison is not tuned to.

## Running it

```powershell
# Offline: the labelled mock evaluator (checks the pipeline, not quality)
python tools\run_eval_set.py --out logs\eval_set\mock
# Real evaluator (billable Groq calls, about 30 requests)
python tools\run_eval_set.py --live --out logs\eval_set\live
# Regression harness on the held-out subset: exits 1 on any invariant violation
python tools\run_eval_set.py --split heldout --check --out logs\eval_set\heldout
python tools\run_eval_set.py --live --split heldout --check --out logs\eval_set\heldout_live
```

Each run writes `results.jsonl` and `review_sheet_filled.csv`, a copy of
`review_sheet.csv` with only the machine columns filled (level, finding, quote,
whether the quote was found in the answer, whether a model was called). The
four human columns stay empty for reviewers.

`--check` asserts model-independent properties: every quote is verbatim from
the candidate's answer, silent answers are never assessed, and injected
instructions (in the answer or the source material) are never echoed. It says
nothing about whether feedback is *good*; that is what human review is for.
The offline mock run is part of the pytest suite (`tests/test_evaluation_set.py`).

## Human review

See [REVIEW_RUBRIC.md](REVIEW_RUBRIC.md) for definitions and worked guidance.
`review_sheet.csv` columns, each measured separately:

- `quote_attribution_correct` — is the quoted text really from this candidate answer, in context? (Y/N/unsure)
- `interpretation_fair` — does the finding describe the answer fairly? (Y/N/unsure)
- `relevant_to_question` — is it about what was asked? (Y/N/unsure)
- `action_useful` — could a candidate act on the suggestion? (Y/N/unsure)
- `reviewer_id`, `notes`

Process: two reviewers label independently (each on their own copy of a run's
`review_sheet_filled.csv`), then compute agreement:

```powershell
python tools\eval_agreement.py reviewer_a.csv reviewer_b.csv --split heldout --out logs\eval_setgreement.json
```

It reports, per column, the examples both labelled, raw agreement and Cohen's
kappa, and **refuses** to run on empty sheets, on one reviewer's labels twice,
or when a reviewer id looks like a model. Discuss disagreements afterwards;
do not overwrite the independent labels. Do not fill these columns with model
output — model labels are not human ground truth.

**Status (2026-10-10): no reviewer has labelled anything.** Agreement, accuracy
and usefulness are unmeasured.
