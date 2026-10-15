"""Choose which baseline the CI gate compares against.

A PR must not be able to lower its own bar, so on pull requests the gate uses the baseline from the
base branch. Rules, in order:
1. PR labelled `update-baseline`: use the PR's own baseline (loud warning; for golden-set changes).
2. No base ref (workflow_dispatch / local): use the checked-out baseline.
3. Base branch has no baseline yet (bootstrap): use the PR's baseline (warning).
4. Otherwise: use the base branch's baseline, and warn if the PR's copy differs from it.

Writes `path=<selected baseline>` to $GITHUB_OUTPUT, `::warning::` lines to stdout and warnings to
$GITHUB_STEP_SUMMARY.

Usage:
    python evals/select_baseline.py --base-ref main [--use-pr-baseline] [--pr-baseline PATH]
                                    [--out results/base_baseline.json]
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REL = "results/baseline.json"
DEFAULT_PR_BASELINE = ROOT / BASELINE_REL
DEFAULT_OUT = ROOT / "results" / "base_baseline.json"
LABEL = "update-baseline"

ReadBase = Callable[[str], str | None]


@dataclass
class Selection:
    path: Path
    source: str
    warnings: list[str] = field(default_factory=list)


def git_show_base(base_ref: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "show", f"origin/{base_ref}:{BASELINE_REL}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _same_json(a: str, b: str) -> bool:
    try:
        return json.loads(a) == json.loads(b)
    except json.JSONDecodeError:
        return a == b


def select(
    base_ref: str,
    *,
    use_pr_baseline: bool,
    pr_baseline: Path,
    out: Path,
    read_base: ReadBase = git_show_base,
) -> Selection:
    if use_pr_baseline:
        return Selection(
            pr_baseline,
            "pr",
            [
                f"PR has the '{LABEL}' label: gating against the PR's own {BASELINE_REL}, not "
                "the base branch's. Reviewers must check the new baseline numbers."
            ],
        )
    if not base_ref:
        return Selection(pr_baseline, "checkout")

    base_text = read_base(base_ref)
    if base_text is None:
        return Selection(
            pr_baseline,
            "pr",
            [
                f"Base branch '{base_ref}' has no {BASELINE_REL} yet (bootstrap): "
                "gating against the PR's own baseline."
            ],
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(base_text, encoding="utf-8")
    selection = Selection(out, "base")
    pr_text = pr_baseline.read_text(encoding="utf-8") if pr_baseline.exists() else None
    if pr_text is None or not _same_json(base_text, pr_text):
        selection.warnings.append(
            f"{BASELINE_REL} changed in this PR; the gate still uses the baseline from "
            f"'{base_ref}'. Add the '{LABEL}' label if the golden set legitimately changed."
        )
    return selection


def publish(selection: Selection) -> None:
    for warning in selection.warnings:
        print(f"::warning title=Eval baseline::{warning}")
    print(f"Gate baseline: {selection.path} (source: {selection.source})")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"path={selection.path}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            for warning in selection.warnings:
                f.write(f"> **Warning:** {warning}\n\n")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select the baseline the eval gate uses.")
    parser.add_argument("--base-ref", default="", help="PR base branch; empty outside PRs")
    parser.add_argument("--use-pr-baseline", action="store_true", help=f"PR has '{LABEL}' label")
    parser.add_argument("--pr-baseline", type=Path, default=DEFAULT_PR_BASELINE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, read_base: ReadBase = git_show_base) -> int:
    args = parse_args(argv)
    selection = select(
        args.base_ref,
        use_pr_baseline=args.use_pr_baseline,
        pr_baseline=args.pr_baseline,
        out=args.out,
        read_base=read_base,
    )
    publish(selection)
    return 0


if __name__ == "__main__":
    sys.exit(main())
