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

| Category | Count | What it tests |
|---|---|---|
| strong | 4 | Specific, owned, reasoned answers — does feedback recognise them? |
| incomplete | 4 | Generic or "we" answers — are suggestions concrete and non-judgemental? |
| ambiguous | 4 | Hedged answers, a career gap — is "developing" kept distinct from "wrong"? |
| silent | 4 | Empty or "I don't know" — not assessed, no score, no invented finding |
| transcription_error | 4 | Mangled STT terms, self-corrections, spoken numbers — not penalised |
| malicious | 4 | Prompt injection in answers — no effect on levels, nothing leaked |

13 software and 11 sales; 5 HR, 8 hiring-manager and 11 specialist-round
examples.
`reviewer_focus` says what a reviewer should look at. It is an authoring note,
**not** an expected label.

## Running it

```powershell
# Offline: the labelled mock evaluator (checks the pipeline, not quality)
python tools\run_eval_set.py --out logs\eval_set\mock
# Real evaluator (billable Groq calls, about 24 requests)
python tools\run_eval_set.py --live --out logs\eval_set\live
```

Each run writes `results.jsonl` and `review_sheet_filled.csv`, a copy of
`review_sheet.csv` with only the machine columns filled (level, finding, quote,
whether the quote was found in the answer, whether a model was called). The
three human columns stay empty for reviewers.

## Human review

`review_sheet.csv` columns:

- `interpretation_fair` — does the finding describe the answer fairly? (Y/N/unsure)
- `relevant_to_question` — is it about what was asked? (Y/N/unsure)
- `action_useful` — could a candidate act on the suggestion? (Y/N/unsure)
- `reviewer_id`, `notes`

Suggested process: two reviewers independently, then discuss disagreements.
Report the number of examples reviewed, per-column counts, and agreement only
once it has actually been computed. Do not fill these columns with model output.
