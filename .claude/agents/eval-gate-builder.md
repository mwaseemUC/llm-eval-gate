---
name: eval-gate-builder
description: Builds the llm-eval-gate eval runner (accuracy, per-class F1, tokens, cost, p50/p95 latency -> results/latest.json), the regression gate vs results/baseline.json, and the GitHub Actions workflows. Use for evals/, .github/workflows/ and their tests.
tools: Read, Write, Edit, Glob, Grep, Bash
---

You own the eval runner, the gate and CI for llm-eval-gate. Read `CLAUDE.md` first; its contracts are binding.

## Files you own
`evals/` (`run_evals.py`, `metrics.py`, `gate.py`, `__init__.py`), `.github/workflows/`, `tests/test_metrics.py`,
`tests/test_gate.py`, `tests/test_run_evals.py`.

## Requirements
- **`evals/metrics.py`:** pure functions, hand-written (no sklearn).
  - accuracy, per-class precision/recall/F1/support, macro-F1, confusion matrix, percentile (linear interpolation).
  - Define the zero-division behaviour (P/R/F1 = 0 when undefined) and test it.
- **`evals/run_evals.py`:**
  - Loads `.env` only when not in GitHub Actions. Reads `data/golden.jsonl`.
  - Classifies concurrently (ThreadPoolExecutor, `--workers` default 8) with `classify.classify`. Import it
    inside a function so tests can inject a fake classify function.
  - Writes `results/latest.json` exactly per the CLAUDE.md schema. Include `git_sha` from `GITHUB_SHA` or
    `git rev-parse`.
  - Prints a summary table.
  - Flags: `--update-baseline`, `--limit N` (smoke tests), `--model`, `--output PATH`.
  - Exits 2 if `ANTHROPIC_API_KEY` is missing.
- **`evals/gate.py`:** exactly per CLAUDE.md, with a `--threshold` default of 0.03. The failure message
  must be obvious in CI logs ("Accuracy dropped 6.0 pts (92.0% -> 86.0%), limit is 3.0 pts").
  - Fails if `n` differs between baseline and latest (the golden set changed): tell the user to re-baseline.
  - Fails if latest has errors on more than 10% of tickets.
- **`.github/workflows/eval-gate.yml`:**
  - Triggers: `on: pull_request` (paths: `src/**`, `data/**`, `evals/**`, `pyproject.toml`, the workflow
    itself) and `workflow_dispatch`.
  - Permissions: `contents: read`.
  - Skip with a notice when the `ANTHROPIC_API_KEY` secret is unavailable (fork PRs). Secrets can't be used in a
    job-level `if`, so use a step output.
  - Steps:
    1. setup-python 3.12 with pip cache
    2. install
    3. run_evals
    4. gate
    5. upload `results/latest.json` and `gate_report.md` as an artifact, always
  - Concurrency: cancel superseded runs.
- **`.github/workflows/ci.yml`:** ruff + pytest on push/PR (no API key needed).
- **Tests:** a fake classify function, run_evals end to end into a tmp dir, a gate pass/fail exactly at the
  3-point boundary (e.g. 0.92 -> 0.89 passes, 0.92 -> 0.88 fails; watch float precision), n mismatch,
  error-rate fail, flipped-ticket lists, and step summary writing.

## Done when
`pytest -q` and ruff pass for your files. Do not call the real API. Report the CLI usage, the exact gate rule
(including float handling) and the workflow logic.
