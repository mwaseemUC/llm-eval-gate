import json
from pathlib import Path

import pytest

from evals import gate, select_baseline


def make_results(
    accuracy: float,
    *,
    n: int = 100,
    n_errors: int = 0,
    predictions: list[dict] | None = None,
    pricing_known: bool = True,
) -> dict:
    return {
        "model": "fake-model",
        "served_models": ["fake-model"],
        "prompt_version": "v1",
        "git_sha": "0123456789abcdef",
        "timestamp": "2026-10-15T00:00:00+00:00",
        "n": n,
        "n_errors": n_errors,
        "accuracy": accuracy,
        "macro_f1": accuracy - 0.01,
        "per_class": {
            "billing": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 2},
            "how_to": {"precision": 0.5, "recall": 0.5, "f1": 0.5, "support": 2},
        },
        "confusion": {},
        "tokens": {"input": 1000, "output": 100},
        "cost_usd": {"total": 0.01, "per_ticket": 0.0001},
        "latency_s": {"p50": 0.8, "p95": 1.5},
        "predictions": predictions or [],
        "pricing_known": pricing_known,
    }


def pred(id_: str, label: str, p: str | None, error: str | None = None) -> dict:
    return {"id": id_, "label": label, "pred": p, "confidence": 0.9, "error": error}


@pytest.mark.parametrize(
    ("baseline", "latest", "passed"),
    [
        (0.92, 0.89, True),
        (0.92, 0.88, False),
        (0.92, 0.92, True),
        (0.92, 0.95, True),
        (0.90, 0.87, True),
        (0.93, 0.90, True),
        (0.97, 0.94, True),
        (0.96, 0.92, False),
        (1.0, 0.97, True),
        (0.92, 0.8899, False),
    ],
)
def test_threshold_boundary(baseline: float, latest: float, passed: bool) -> None:
    result = gate.evaluate(make_results(baseline), make_results(latest))
    assert result.passed is passed


def test_boundary_exercises_float_noise() -> None:
    assert 0.92 - 0.89 > 0.03
    assert gate.evaluate(make_results(0.92), make_results(0.89)).passed


def test_boundary_with_fractional_accuracies() -> None:
    base = make_results(46 / 50, n=50)
    assert gate.evaluate(base, make_results(44.5 / 50, n=50)).passed
    assert not gate.evaluate(base, make_results(44 / 50, n=50)).passed


def test_failure_message_is_explicit() -> None:
    result = gate.evaluate(make_results(0.92), make_results(0.86))
    assert result.failures == ["Accuracy dropped 6.0 pts (92.0% -> 86.0%), limit is 3.0 pts"]
    assert "## Eval gate: FAILED" in result.report
    assert "Accuracy dropped 6.0 pts" in result.report


def test_custom_threshold() -> None:
    assert not gate.evaluate(make_results(0.92), make_results(0.90), threshold=0.01).passed
    assert gate.evaluate(make_results(0.92), make_results(0.86), threshold=0.06).passed


def test_n_mismatch_fails_and_says_rebaseline() -> None:
    result = gate.evaluate(make_results(0.92, n=50), make_results(0.92, n=60))
    assert not result.passed
    assert len(result.failures) == 1
    assert "baseline n=50, latest n=60" in result.failures[0]
    assert "--update-baseline" in result.failures[0]


def test_n_mismatch_skips_accuracy_comparison() -> None:
    result = gate.evaluate(make_results(0.92, n=50), make_results(0.50, n=60))
    assert len(result.failures) == 1


def test_error_rate_above_ten_percent_fails() -> None:
    result = gate.evaluate(make_results(0.92), make_results(0.92, n_errors=11))
    assert not result.passed
    assert "11/100 tickets errored (11.0%), limit is 10%" in result.failures[0]


def test_error_rate_exactly_ten_percent_passes() -> None:
    assert gate.evaluate(make_results(0.92), make_results(0.92, n_errors=10)).passed


def test_error_rate_and_accuracy_both_reported() -> None:
    result = gate.evaluate(make_results(0.92), make_results(0.70, n_errors=20))
    assert len(result.failures) == 2


def test_empty_latest_fails() -> None:
    assert not gate.evaluate(make_results(0.0, n=0), make_results(0.0, n=0)).passed


def test_flips() -> None:
    baseline = make_results(
        0.5,
        n=5,
        predictions=[
            pred("t1", "billing", "billing"),
            pred("t2", "how_to", "billing"),
            pred("t3", "how_to", "how_to"),
            pred("t4", "billing", None, error="ClassifyError: boom"),
            pred("t5", "billing", "billing"),
        ],
    )
    latest = make_results(
        0.5,
        n=5,
        predictions=[
            pred("t1", "billing", "how_to"),
            pred("t2", "how_to", "how_to"),
            pred("t3", "how_to", None, error="ClassifyError: timeout"),
            pred("t4", "billing", "billing"),
            pred("t5", "billing", "billing"),
        ],
    )
    regressed, fixed = gate.flips(baseline, latest)
    assert [e["id"] for e in regressed] == ["t1", "t3"]
    assert [e["id"] for e in fixed] == ["t2", "t4"]

    report = gate.evaluate(baseline, latest).report
    assert "### Right -> wrong (2)" in report
    assert "### Wrong -> right (2)" in report
    assert "- `t1` (label `billing`): `billing` -> `how_to`" in report
    assert "`how_to` -> `error (ClassifyError: timeout)`" in report


def test_flips_ignore_unmatched_ids() -> None:
    baseline = make_results(1.0, n=1, predictions=[pred("t1", "billing", "billing")])
    latest = make_results(0.0, n=1, predictions=[pred("t9", "billing", "how_to")])
    assert gate.flips(baseline, latest) == ([], [])


def test_report_contents() -> None:
    report = gate.evaluate(make_results(0.92), make_results(0.93)).report
    assert report.startswith("## Eval gate: PASSED")
    for text in ("| Accuracy | 92.0% | 93.0% | +1.0 pts |", "Macro-F1", "Cost / ticket",
                 "Latency p95", "### Per-class F1", "| billing | 1.000 | 1.000 | +0.000 | 2 |",
                 "_none_", "### Runs"):  # fmt: skip
        assert text in report


def test_report_warns_on_unknown_pricing() -> None:
    report = gate.evaluate(make_results(0.92), make_results(0.92, pricing_known=False)).report
    assert "pricing" in report.lower()


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_main(tmp_path: Path, base_acc: float, latest_acc: float) -> tuple[int, Path]:
    baseline = write(tmp_path / "baseline.json", make_results(base_acc))
    latest = write(tmp_path / "latest.json", make_results(latest_acc))
    report = tmp_path / "out" / "gate_report.md"
    argv = ["--baseline", str(baseline), "--latest", str(latest), "--report", str(report)]
    return gate.main(argv), report


def test_main_pass_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code, report = run_main(tmp_path, 0.92, 0.89)
    assert code == 0
    assert "## Eval gate: PASSED" in report.read_text(encoding="utf-8")


def test_main_fail_exit_1_and_ci_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    code, _ = run_main(tmp_path, 0.92, 0.88)
    assert code == 1
    captured = capsys.readouterr()
    assert "::error title=Eval gate failed::Accuracy dropped 4.0 pts" in captured.out
    assert "GATE FAILED: Accuracy dropped 4.0 pts (92.0% -> 88.0%)" in captured.err


def test_main_appends_step_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, _ = run_main(tmp_path, 0.92, 0.80)
    assert code == 1
    content = summary.read_text(encoding="utf-8")
    assert content.startswith("existing\n")
    assert "## Eval gate: FAILED" in content


def test_main_missing_baseline_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    latest = write(tmp_path / "latest.json", make_results(0.9))
    report = tmp_path / "gate_report.md"
    argv = [
        "--baseline", str(tmp_path / "nope.json"), "--latest", str(latest),
        "--report", str(report),
    ]  # fmt: skip
    assert gate.main(argv) == 1
    assert "nope.json not found" in report.read_text(encoding="utf-8")


def _main_with_latest_text(tmp_path: Path, latest_text: str) -> tuple[int, str]:
    baseline = write(tmp_path / "baseline.json", make_results(0.9))
    latest = tmp_path / "latest.json"
    latest.write_text(latest_text, encoding="utf-8")
    report = tmp_path / "gate_report.md"
    argv = ["--baseline", str(baseline), "--latest", str(latest), "--report", str(report)]
    return gate.main(argv), report.read_text(encoding="utf-8")


def test_main_malformed_json_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, report = _main_with_latest_text(tmp_path, '{"accuracy": 0.9,')
    assert code == 1
    assert "Malformed results JSON" in report
    assert "## Eval gate: FAILED" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ['{"accuracy": 0.9}', "[1, 2]", '{"n": 5, "n_errors": "x"}'])
def test_main_schema_mismatch_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code, report = _main_with_latest_text(tmp_path, text)
    assert code == 1
    assert "does not match the expected schema" in report


BASE_TEXT = json.dumps(make_results(0.92))


def _select(tmp_path: Path, base_ref: str, *, label: bool = False, base: str | None = BASE_TEXT,
            pr_text: str | None = BASE_TEXT) -> select_baseline.Selection:  # fmt: skip
    pr = tmp_path / "baseline.json"
    if pr_text is not None:
        pr.write_text(pr_text, encoding="utf-8")
    refs: list[str] = []

    def read_base(ref: str) -> str | None:
        refs.append(ref)
        return base

    selection = select_baseline.select(
        base_ref,
        use_pr_baseline=label,
        pr_baseline=pr,
        out=tmp_path / "out" / "base_baseline.json",
        read_base=read_base,
    )
    assert refs in ([], [base_ref])
    return selection


def test_select_uses_base_branch_baseline(tmp_path: Path) -> None:
    sel = _select(tmp_path, "main", pr_text=json.dumps(make_results(0.92), indent=2))
    assert sel.source == "base"
    assert sel.path == tmp_path / "out" / "base_baseline.json"
    assert sel.path.read_text(encoding="utf-8") == BASE_TEXT
    assert sel.warnings == []


def test_select_warns_when_pr_changes_baseline_but_keeps_base(tmp_path: Path) -> None:
    sel = _select(tmp_path, "main", pr_text=json.dumps(make_results(0.50)))
    assert sel.source == "base"
    assert json.loads(sel.path.read_text(encoding="utf-8"))["accuracy"] == 0.92
    assert len(sel.warnings) == 1
    assert "changed in this PR" in sel.warnings[0]


def test_select_bootstrap_falls_back_to_pr_baseline(tmp_path: Path) -> None:
    sel = _select(tmp_path, "main", base=None)
    assert sel.source == "pr"
    assert sel.path == tmp_path / "baseline.json"
    assert "bootstrap" in sel.warnings[0]


def test_select_label_uses_pr_baseline_loudly(tmp_path: Path) -> None:
    sel = _select(tmp_path, "main", label=True, pr_text=json.dumps(make_results(0.5)))
    assert sel.source == "pr"
    assert sel.path == tmp_path / "baseline.json"
    assert "update-baseline" in sel.warnings[0]


def test_select_without_base_ref_uses_checkout(tmp_path: Path) -> None:
    sel = _select(tmp_path, "")
    assert sel.source == "checkout"
    assert sel.warnings == []


def test_select_main_writes_output_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    output, summary = tmp_path / "gh_output", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    pr = write(tmp_path / "baseline.json", make_results(0.5))
    out = tmp_path / "base_baseline.json"
    argv = ["--base-ref", "main", "--pr-baseline", str(pr), "--out", str(out)]
    assert select_baseline.main(argv, read_base=lambda _: BASE_TEXT) == 0
    assert output.read_text(encoding="utf-8") == f"path={out}\n"
    assert "changed in this PR" in summary.read_text(encoding="utf-8")
    assert "::warning title=Eval baseline::" in capsys.readouterr().out


def test_selected_base_baseline_gates_a_lowered_pr_baseline(tmp_path: Path) -> None:
    pr = write(tmp_path / "baseline.json", make_results(0.80))
    sel = select_baseline.select(
        "main", use_pr_baseline=False, pr_baseline=pr, out=tmp_path / "b.json",
        read_base=lambda _: BASE_TEXT,
    )  # fmt: skip
    latest = write(tmp_path / "latest.json", make_results(0.81))
    result = gate.evaluate_files(sel.path, latest, gate.DEFAULT_THRESHOLD, 0.1)
    assert not result.passed
    assert "Accuracy dropped 11.0 pts" in result.failures[0]
