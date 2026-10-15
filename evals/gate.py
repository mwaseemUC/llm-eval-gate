"""Regression gate: compare results/latest.json against results/baseline.json.

Exit 1 when any check fails:
- accuracy dropped by strictly more than --threshold (default 0.03 = 3 points)
- the golden set size changed (baseline n != latest n)
- more than --max-error-rate (default 10%) of latest tickets errored

Usage:
    python evals/gate.py [--latest PATH] [--baseline PATH] [--report PATH]
                         [--threshold 0.03] [--max-error-rate 0.10]
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LATEST = ROOT / "results" / "latest.json"
DEFAULT_BASELINE = ROOT / "results" / "baseline.json"
DEFAULT_REPORT = ROOT / "results" / "gate_report.md"
DEFAULT_THRESHOLD = 0.03
DEFAULT_MAX_ERROR_RATE = 0.10
# Accuracies are k/n fractions, so real differences are >= 1/n; this only absorbs float noise
# such as 0.92 - 0.89 == 0.030000000000000027.
EPSILON = 1e-9
REBASELINE_CMD = "python evals/run_evals.py --update-baseline"


@dataclass
class GateResult:
    passed: bool
    failures: list[str]
    report: str


def exceeds(value: float, limit: float) -> bool:
    return value - limit > EPSILON


def accuracy_failure(baseline: dict, latest: dict, threshold: float) -> str | None:
    drop = baseline["accuracy"] - latest["accuracy"]
    if not exceeds(drop, threshold):
        return None
    return (
        f"Accuracy dropped {drop * 100:.1f} pts "
        f"({baseline['accuracy']:.1%} -> {latest['accuracy']:.1%}), "
        f"limit is {threshold * 100:.1f} pts"
    )


def size_failure(baseline: dict, latest: dict) -> str | None:
    if baseline["n"] == latest["n"]:
        return None
    return (
        f"Golden set size changed (baseline n={baseline['n']}, latest n={latest['n']}); "
        f"results are not comparable. Re-baseline with `{REBASELINE_CMD}` and commit "
        "results/baseline.json."
    )


def error_rate_failure(latest: dict, max_error_rate: float) -> str | None:
    n = latest["n"]
    if n == 0:
        return "Latest results contain no tickets."
    rate = latest["n_errors"] / n
    if not exceeds(rate, max_error_rate):
        return None
    return (
        f"{latest['n_errors']}/{n} tickets errored ({rate:.1%}), "
        f"limit is {max_error_rate:.0%}; check the API key, model name and classify.py."
    )


def check(baseline: dict, latest: dict, threshold: float, max_error_rate: float) -> list[str]:
    size = size_failure(baseline, latest)
    candidates = [
        size,
        error_rate_failure(latest, max_error_rate),
        None if size else accuracy_failure(baseline, latest, threshold),
    ]
    return [f for f in candidates if f]


def _is_correct(pred: dict) -> bool:
    return pred["error"] is None and pred["pred"] == pred["label"]


def flips(baseline: dict, latest: dict) -> tuple[list[dict], list[dict]]:
    """Tickets present in both runs that went right->wrong and wrong->right."""
    before = {p["id"]: p for p in baseline["predictions"]}
    regressed, fixed = [], []
    for after in latest["predictions"]:
        prev = before.get(after["id"])
        if prev is None:
            continue
        entry = {
            "id": after["id"],
            "label": after["label"],
            "before": prev["pred"],
            "after": after["pred"],
            "error": after["error"],
        }
        if _is_correct(prev) and not _is_correct(after):
            regressed.append(entry)
        elif not _is_correct(prev) and _is_correct(after):
            fixed.append(entry)
    return regressed, fixed


def _pts(delta: float) -> str:
    return f"{delta * 100:+.1f} pts"


def _signed(delta: float, fmt: str) -> str:
    return format(delta, "+" + fmt)


def _metrics_table(b: dict, la: dict) -> list[str]:
    rows = [
        (
            "Accuracy",
            f"{b['accuracy']:.1%}",
            f"{la['accuracy']:.1%}",
            _pts(la["accuracy"] - b["accuracy"]),
        ),
        (
            "Macro-F1",
            f"{b['macro_f1']:.3f}",
            f"{la['macro_f1']:.3f}",
            _signed(la["macro_f1"] - b["macro_f1"], ".3f"),
        ),
        ("Tickets (errors)", f"{b['n']} ({b['n_errors']})", f"{la['n']} ({la['n_errors']})", ""),
        (
            "Cost total",
            f"${b['cost_usd']['total']:.4f}",
            f"${la['cost_usd']['total']:.4f}",
            _signed(la["cost_usd"]["total"] - b["cost_usd"]["total"], ".4f"),
        ),
        (
            "Cost / ticket",
            f"${b['cost_usd']['per_ticket']:.5f}",
            f"${la['cost_usd']['per_ticket']:.5f}",
            _signed(la["cost_usd"]["per_ticket"] - b["cost_usd"]["per_ticket"], ".5f"),
        ),
        (
            "Latency p50",
            f"{b['latency_s']['p50']:.2f}s",
            f"{la['latency_s']['p50']:.2f}s",
            _signed(la["latency_s"]["p50"] - b["latency_s"]["p50"], ".2f") + "s",
        ),
        (
            "Latency p95",
            f"{b['latency_s']['p95']:.2f}s",
            f"{la['latency_s']['p95']:.2f}s",
            _signed(la["latency_s"]["p95"] - b["latency_s"]["p95"], ".2f") + "s",
        ),
        (
            "Tokens in / out",
            f"{b['tokens']['input']} / {b['tokens']['output']}",
            f"{la['tokens']['input']} / {la['tokens']['output']}",
            "",
        ),
    ]
    lines = ["| Metric | Baseline | Latest | Delta |", "|---|---:|---:|---:|"]
    lines += [f"| {m} | {bv} | {lv} | {d} |" for m, bv, lv, d in rows]
    return lines


def _per_class_table(b: dict, la: dict) -> list[str]:
    lines = ["| Class | Baseline F1 | Latest F1 | Delta | Support |", "|---|---:|---:|---:|---:|"]
    for label in dict.fromkeys([*b["per_class"], *la["per_class"]]):
        bf = b["per_class"].get(label, {}).get("f1", 0.0)
        lf = la["per_class"].get(label, {}).get("f1", 0.0)
        support = la["per_class"].get(label, {}).get("support", 0)
        lines.append(f"| {label} | {bf:.3f} | {lf:.3f} | {lf - bf:+.3f} | {support} |")
    return lines


def _flip_lines(title: str, entries: list[dict]) -> list[str]:
    lines = [f"### {title} ({len(entries)})", ""]
    if not entries:
        return [*lines, "_none_"]
    for e in entries:
        after = e["after"] if e["error"] is None else f"error ({e['error']})"
        lines.append(f"- `{e['id']}` (label `{e['label']}`): `{e['before']}` -> `{after}`")
    return lines


def _run_line(name: str, r: dict) -> str:
    return (
        f"- {name}: model `{r['model']}`, prompt `{r['prompt_version']}`, "
        f"sha `{str(r['git_sha'])[:12]}`, {r['timestamp']}"
    )


def build_report(baseline: dict, latest: dict, failures: Sequence[str], threshold: float) -> str:
    status = "FAILED" if failures else "PASSED"
    regressed, fixed = flips(baseline, latest)
    lines = [f"## Eval gate: {status}", ""]
    if failures:
        lines += [f"- **{f}**" for f in failures]
    else:
        lines.append(
            f"Accuracy {latest['accuracy']:.1%} vs baseline {baseline['accuracy']:.1%} "
            f"(limit: drop of more than {threshold * 100:.1f} pts)."
        )
    for name, run in (("baseline", baseline), ("latest", latest)):
        if run.get("pricing_known") is False:
            lines.append(
                f"- Warning: {name} used a model missing from PRICING; cost is understated."
            )
    lines += ["", *_metrics_table(baseline, latest), "", "### Per-class F1", ""]
    lines += _per_class_table(baseline, latest)
    lines += ["", *_flip_lines("Right -> wrong", regressed), ""]
    lines += [*_flip_lines("Wrong -> right", fixed), ""]
    lines += ["### Runs", "", _run_line("Baseline", baseline), _run_line("Latest", latest), ""]
    return "\n".join(lines)


def evaluate(
    baseline: dict,
    latest: dict,
    threshold: float = DEFAULT_THRESHOLD,
    max_error_rate: float = DEFAULT_MAX_ERROR_RATE,
) -> GateResult:
    failures = check(baseline, latest, threshold, max_error_rate)
    return GateResult(not failures, failures, build_report(baseline, latest, failures, threshold))


def _failed(message: str) -> GateResult:
    return GateResult(False, [message], f"## Eval gate: FAILED\n\n- **{message}**\n")


def _missing_report(path: Path) -> GateResult:
    return _failed(
        f"{path} not found. Run `{REBASELINE_CMD}` (baseline) or `python evals/run_evals.py`."
    )


def evaluate_files(
    baseline_path: Path, latest_path: Path, threshold: float, max_error_rate: float
) -> GateResult:
    missing = next((p for p in (baseline_path, latest_path) if not p.exists()), None)
    if missing is not None:
        return _missing_report(missing)
    try:
        baseline, latest = _load(baseline_path), _load(latest_path)
        return evaluate(baseline, latest, threshold, max_error_rate)
    except json.JSONDecodeError as exc:
        return _failed(f"Malformed results JSON: {exc}. Re-run `python evals/run_evals.py`.")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failed(
            f"Results file does not match the expected schema ({type(exc).__name__}: {exc}). "
            "Re-run `python evals/run_evals.py` and re-baseline if the schema changed."
        )


def publish(report: str, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail when eval accuracy regresses vs baseline.")
    parser.add_argument("--latest", type=Path, default=DEFAULT_LATEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--max-error-rate", type=float, default=DEFAULT_MAX_ERROR_RATE)
    return parser.parse_args(argv)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = evaluate_files(args.baseline, args.latest, args.threshold, args.max_error_rate)
    publish(result.report, args.report)
    print(result.report)
    for failure in result.failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Eval gate failed::{failure}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
