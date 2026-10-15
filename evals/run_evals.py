"""Run the classifier over the golden set and write results/latest.json.

Usage:
    python evals/run_evals.py [--limit N] [--model M] [--workers 8] [--output PATH]
                              [--golden PATH] [--update-baseline] [--baseline PATH]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from evals import metrics  # noqa: E402

DEFAULT_GOLDEN = ROOT / "data" / "golden.jsonl"
DEFAULT_OUTPUT = ROOT / "results" / "latest.json"
DEFAULT_BASELINE = ROOT / "results" / "baseline.json"
MAX_ERROR_CHARS = 300

ClassifyFn = Callable[..., tuple[Any, Any]]


@dataclass
class TicketResult:
    id: str
    label: str
    pred: str | None
    confidence: float | None
    error: str | None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float | None = None


def load_golden(path: Path, limit: int | None = None) -> list[dict]:
    tickets = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                tickets.append(json.loads(line))
    return tickets[:limit] if limit is not None else tickets


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def classify_one(ticket: dict, classify_fn: ClassifyFn, model: str) -> TicketResult:
    start = time.perf_counter()
    try:
        result, usage = classify_fn(ticket["text"], model=model)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        return TicketResult(ticket["id"], ticket["label"], None, None, message)
    elapsed = time.perf_counter() - start
    return TicketResult(
        id=ticket["id"],
        label=ticket["label"],
        pred=str(result.category),
        confidence=float(result.confidence),
        error=None,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_s=usage.latency_s or elapsed,
    )


def classify_all(
    tickets: Sequence[dict], classify_fn: ClassifyFn, model: str, workers: int
) -> list[TicketResult]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda t: classify_one(t, classify_fn, model), tickets))


def build_results(
    rows: Sequence[TicketResult],
    *,
    model: str,
    prompt_version: str,
    git_sha: str,
    timestamp: str,
    labels: Sequence[str] | None = None,
) -> dict:
    y_true = [r.label for r in rows]
    y_pred = [r.pred for r in rows]
    labels = list(labels) if labels else metrics.label_set(y_true, y_pred)
    ok = [r for r in rows if r.error is None]
    latencies = [r.latency_s for r in ok if r.latency_s is not None]
    total_cost = sum(r.cost_usd for r in ok)
    n = len(rows)
    return {
        "model": model,
        "served_models": sorted({r.model for r in ok if r.model}),
        "prompt_version": prompt_version,
        "git_sha": git_sha,
        "timestamp": timestamp,
        "n": n,
        "n_errors": n - len(ok),
        "accuracy": metrics.accuracy(y_true, y_pred),
        "macro_f1": metrics.macro_f1(y_true, y_pred, labels),
        "per_class": metrics.per_class(y_true, y_pred, labels),
        "confusion": metrics.confusion_matrix(y_true, y_pred, labels),
        "tokens": {
            "input": sum(r.input_tokens for r in ok),
            "output": sum(r.output_tokens for r in ok),
        },
        "cost_usd": {
            "total": round(total_cost, 6),
            "per_ticket": round(total_cost / n, 6) if n else 0.0,
        },
        "latency_s": {
            "p50": round(metrics.percentile(latencies, 50), 3),
            "p95": round(metrics.percentile(latencies, 95), 3),
        },
        "predictions": [
            {
                "id": r.id,
                "label": r.label,
                "pred": r.pred,
                "confidence": r.confidence,
                "error": r.error,
            }
            for r in rows
        ],
    }


def unpriced_models(results: dict, pricing: dict) -> list[str]:
    models = dict.fromkeys([results["model"], *results["served_models"]])
    return [m for m in models if m not in pricing]


def format_summary(results: dict) -> str:
    lines = [
        f"model={results['model']} served={','.join(results['served_models']) or '-'} "
        f"prompt={results['prompt_version']} sha={results['git_sha'][:12]}",
        f"n={results['n']}  errors={results['n_errors']}  "
        f"accuracy={results['accuracy']:.1%}  macro_f1={results['macro_f1']:.3f}",
        "",
        f"{'class':<18}{'prec':>7}{'recall':>8}{'f1':>7}{'support':>9}",
    ]
    for label, s in results["per_class"].items():
        lines.append(
            f"{label:<18}{s['precision']:>7.2f}{s['recall']:>8.2f}{s['f1']:>7.2f}{s['support']:>9}"
        )
    tokens, cost, latency = results["tokens"], results["cost_usd"], results["latency_s"]
    lines += [
        "",
        f"tokens in/out: {tokens['input']}/{tokens['output']}  "
        f"cost: ${cost['total']:.4f} (${cost['per_ticket']:.5f}/ticket)",
        f"latency p50/p95: {latency['p50']:.2f}s / {latency['p95']:.2f}s",
    ]
    errors = [p for p in results["predictions"] if p["error"]]
    for p in errors[:5]:
        lines.append(f"error {p['id']}: {p['error']}")
    return "\n".join(lines)


def current_git_sha() -> str:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def load_classifier() -> Any:
    import classify

    return classify


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the ticket classifier on the golden set."
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update-baseline", action="store_true", help="copy output to baseline")
    parser.add_argument("--limit", type=positive_int, default=None, help="only the first N tickets")
    parser.add_argument("--model", default=None, help="override the classifier model")
    parser.add_argument("--workers", type=positive_int, default=8)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, classifier: Any | None = None) -> int:
    args = parse_args(argv)
    if classifier is None:
        if not os.environ.get("GITHUB_ACTIONS"):
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set (env or .env); cannot run evals.", file=sys.stderr)
            return 2
        classifier = load_classifier()

    model = classifier.resolve_model(args.model)
    tickets = load_golden(args.golden, args.limit)
    category = getattr(classifier, "Category", None)
    labels = [c.value for c in category] if category is not None else None

    rows = classify_all(tickets, classifier.classify, model, args.workers)
    results = build_results(
        rows,
        model=model,
        prompt_version=classifier.PROMPT_VERSION,
        git_sha=current_git_sha(),
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        labels=labels,
    )
    unpriced = unpriced_models(results, getattr(classifier, "PRICING", {}))
    results["pricing_known"] = not unpriced

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(format_summary(results))
    if unpriced:
        print(
            f"\nWARNING: no pricing for {', '.join(unpriced)} in classify.PRICING; "
            "cost figures are understated (pricing_known=false)."
        )
    print(f"\nwrote {args.output}")
    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.output, args.baseline)
        print(f"updated baseline {args.baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
