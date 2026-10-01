# Continuous integration

The workflow in `.github/workflows/ci.yml` runs on pull requests (including
description edits), pushes to `main`, and manual dispatch. It performs nine
application checks:

1. Python lint, compilation, unit tests, and coverage (minimum 60%).
2. Prompt organization and documentation.
3. Response validation and output guardrails.
4. Retry and timeout configuration.
5. Agent and tool contracts.
6. RAG context checks.
7. Secret scanning and `.env` protection.
8. PR template and commit convention validation.
9. Accepted RAG source types.

The PR description validator checks the headings and checkbox labels in
`.github/pull_request_template.md`, following the team's shared format. It does
not require every box to be checked: an inapplicable claim should stay unchecked.
PR text is read as event data and is never executed as shell code.

The secret scanning job uses Gitleaks with the repository's documented
placeholder allowlist. `.gitleaksignore` contains one previously reviewed
README fingerprint; new findings are not covered by it. No live LLM, database,
or external service is needed for the unit tests.

Run the main checks locally with:

```bash
python -m unittest discover -s tests/ci -v
ruff check app config interfaces scripts
python -m compileall -q app config interfaces tests scripts
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report -m --fail-under=60
```

Failed checks block a merge only when the repository's branch rules require
them. The workflow itself does not change repository rules.
