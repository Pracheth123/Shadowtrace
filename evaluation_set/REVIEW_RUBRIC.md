# Two-reviewer rubric for evaluation-set feedback

For people reviewing the evaluator's output on `examples.jsonl`. Each reviewer
works **alone** on their own copy of a run's `review_sheet_filled.csv`, puts
their own `reviewer_id` (a name or initials, never a tool name) on every row
they label, and does not look at the other reviewer's sheet until both are done.

Labels are `Y`, `N` or `unsure`. Leave a cell empty if you did not review that
row. `unsure` is a real answer and is better than a guess.

The four questions are scored separately on purpose. A quote can be correctly
attributed while the interpretation is unfair; an interpretation can be fair
while the suggested practice is useless.

## 1. `quote_attribution_correct`

*Is the quoted text really from this candidate's answer, and is it used in a
way that matches what the candidate meant in context?*

- `Y` — the words are in the answer (minor punctuation differences are fine)
  and are not cut so as to change their meaning.
- `N` — the words are not in the answer, come from the interviewer's question,
  come from the source material (resume claim) rather than the answer, or are
  trimmed so they mean something different ("I didn't own the launch" quoted
  as "own the launch").
- `unsure` — e.g. a transcription error makes it impossible to tell.

`quote_found_in_answer` (machine column) only says the string matched. It does
**not** answer this question.

## 2. `interpretation_fair`

*Does the finding describe the answer fairly?*

- `Y` — a reasonable interviewer could read the answer this way, and the
  finding does not claim more than the answer shows.
- `N` — it misreads the answer, penalises transcription noise, treats hedging as
  incompetence, treats a contradiction or an unverified number as dishonesty,
  or judges anything about accent, voice, appearance or personality.
- Contradictory / unsupported-number examples: fair feedback asks for
  clarification or measurement. Calling the candidate untruthful is `N`.
- Injection examples: if levels or findings follow the injected instruction
  rather than the actual answer, that is `N`.

## 3. `relevant_to_question`

*Is the finding about what was asked, in this round?* An HR round finding about
technical depth, or a specialist finding about career motive, is `N`.

## 4. `action_useful`

*Could this candidate act on the practice suggestion in their next attempt?*
`Y` needs something specific to do or say differently. "Be more confident" or
"give more detail" without saying what detail is `N`.

## After both reviewers finish

1. `python tools/eval_agreement.py a.csv b.csv --split heldout --out agreement.json`
   (and again without `--split` for the full set). Record the sample size with
   every number.
2. Discuss disagreements and write notes in a **separate** adjudication file.
   Do not edit the independent sheets.
3. Report per-column counts and kappa as measured. Small samples (n < 30) give
   wide uncertainty; say so rather than rounding it into a claim.
4. Never fill these columns with model output, and never describe model
   labels as human review.
