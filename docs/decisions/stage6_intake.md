# Stage 6 — Intake run

**Date:** 2026-10-03  
**Command:** `python tools/intake.py --resume fixtures/resumes/stage6_ada.txt --repo-url https://github.com/pypa/sampleproject --jd fixtures/jd/stage6_backend.txt --out fixtures/intake/stage6_public`

The shallow clone was removed after the JSON files were written. Commit recorded: `621e497`.

Resume parsing follows skill-sync's profile (skills, projects, certifications, courses), the 12_000 character cap, and whitespace collapse. The hiring rubric and model prompt were not ported. Instruction lines are stripped (contract 7).

## claims.json (8)

1. resume — Built a Flask middleware that records request latency and writes it to PostgreSQL.
2. README — A sample project that exists as an aid to the Python Packaging User Guide's tutorial.
3. README — This project does not aim to cover best practices for Python project development as a whole.
4. README — For example, it does not provide guidance or tool recommendations for version control, documentation, or testing.
5. README — The metadata for a Python project is defined in the `pyproject.toml` file, an example of which is included in this project.
6. README — You should edit this file accordingly to adapt this sample project to your needs.
7. README — This is the README file for the project.
8. README — The file should use UTF-8 encoding and can be written using reStructuredText or markdown.

Full file: `fixtures/intake/stage6_public/claims.json`.

## fit_gap

2 of 3 required skills overlap. 1 missing. 0 unsure.

- matched: Python, Flask
- missing: Kubernetes
- nice to have: Redis
- score_pct: 67

## Exploration trace (tool use)

```
tool_call list_dir path=.
tool_result entries: .github, .gitignore, LICENSE.txt, noxfile.py, pyproject.toml, README.md, src, tests
tool_call read_file path=README.md
tool_call grep pattern="def " path=.
tool_result hits include src/sample/simple.py def add_one(number) and noxfile.py def tests(session)
tool_call git_log n=5
```

Full log: `fixtures/intake/stage6_public/exploration.jsonl`.
