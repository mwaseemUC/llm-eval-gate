import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import run_evals

TICKETS = [
    {"id": "t001", "text": "charged twice", "label": "billing", "difficulty": "easy"},
    {"id": "t002", "text": "app crashes", "label": "bug_report", "difficulty": "easy"},
    {
        "id": "t003",
        "text": "please add dark mode",
        "label": "feature_request",
        "difficulty": "hard",
    },
    {"id": "t004", "text": "cannot log in", "label": "account_access", "difficulty": "easy"},
    {"id": "t005", "text": "BOOM", "label": "how_to", "difficulty": "hard"},
]
ANSWERS = {
    "charged twice": "billing",
    "app crashes": "bug_report",
    "please add dark mode": "bug_report",
    "cannot log in": "account_access",
}
LATENCIES = {"charged twice": 1.0, "app crashes": 2.0, "please add dark mode": 3.0}


class FakeClassifyError(Exception):
    pass


def make_classifier(
    served_model: str | None = None, pricing: dict | None = None
) -> SimpleNamespace:
    calls: list[dict] = []
    lock = threading.Lock()

    def classify(ticket: str, *, client: object = None, model: str | None = None):
        with lock:
            calls.append({"ticket": ticket, "model": model})
        if ticket not in ANSWERS:
            raise FakeClassifyError("invalid output after 3 attempts")
        result = SimpleNamespace(category=ANSWERS[ticket], confidence=0.9, reasoning="x")
        usage = SimpleNamespace(
            model=served_model or model,
            input_tokens=100,
            output_tokens=10,
            cost_usd=0.001,
            latency_s=LATENCIES.get(ticket, 4.0),
        )
        return result, usage

    def resolve_model(model: str | None = None) -> str:
        return model or "fake-default"

    return SimpleNamespace(
        classify=classify,
        resolve_model=resolve_model,
        PROMPT_VERSION="v-test",
        DEFAULT_MODEL="fake-default",
        PRICING=pricing if pricing is not None else {"fake-default": (1.0, 5.0), "m2": (1, 1)},
        calls=calls,
    )


@pytest.fixture
def golden(tmp_path: Path) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(json.dumps(t) for t in TICKETS) + "\n\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fixed_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "abc123def456")
    monkeypatch.delenv("CLASSIFIER_MODEL", raising=False)


def run(golden: Path, tmp_path: Path, classifier: SimpleNamespace, *extra: str) -> dict:
    out = tmp_path / "out" / "latest.json"
    code = run_evals.main(
        ["--golden", str(golden), "--output", str(out), "--workers", "3", *extra],
        classifier=classifier,
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_end_to_end_schema_and_metrics(
    golden: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    results = run(golden, tmp_path, make_classifier())

    expected_keys = {
        "model", "served_models", "prompt_version", "git_sha", "timestamp", "n", "n_errors",
        "accuracy", "macro_f1", "per_class", "confusion", "tokens", "cost_usd", "latency_s",
        "predictions", "pricing_known",
    }  # fmt: skip
    assert set(results) == expected_keys
    assert results["model"] == "fake-default"
    assert results["served_models"] == ["fake-default"]
    assert results["prompt_version"] == "v-test"
    assert results["git_sha"] == "abc123def456"
    assert results["n"] == 5
    assert results["n_errors"] == 1
    assert results["accuracy"] == pytest.approx(3 / 5)
    assert results["per_class"]["billing"] == {
        "precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1,
    }  # fmt: skip
    assert results["per_class"]["bug_report"]["precision"] == 0.5
    assert results["confusion"]["how_to"]["error"] == 1
    assert results["tokens"] == {"input": 400, "output": 40}
    assert results["cost_usd"]["total"] == pytest.approx(0.004)
    assert results["cost_usd"]["per_ticket"] == pytest.approx(0.0008)
    assert results["latency_s"]["p50"] == pytest.approx(2.5)
    assert results["latency_s"]["p95"] == pytest.approx(3.85)
    assert results["pricing_known"] is True

    preds = results["predictions"]
    assert [p["id"] for p in preds] == ["t001", "t002", "t003", "t004", "t005"]
    assert preds[0] == {
        "id": "t001", "label": "billing", "pred": "billing", "confidence": 0.9, "error": None,
    }  # fmt: skip
    assert preds[4]["pred"] is None
    assert preds[4]["confidence"] is None
    assert preds[4]["error"].startswith("FakeClassifyError: invalid output")

    printed = capsys.readouterr().out
    assert "accuracy=60.0%" in printed
    assert "WARNING" not in printed


def test_model_flag_passes_through(golden: Path, tmp_path: Path) -> None:
    classifier = make_classifier()
    results = run(golden, tmp_path, classifier, "--model", "m2")
    assert results["model"] == "m2"
    assert {c["model"] for c in classifier.calls} == {"m2"}


def test_limit(golden: Path, tmp_path: Path) -> None:
    classifier = make_classifier()
    results = run(golden, tmp_path, classifier, "--limit", "2")
    assert results["n"] == 2
    assert len(classifier.calls) == 2


def test_unknown_pricing_warns(golden: Path, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    results = run(golden, tmp_path, make_classifier(served_model="mystery-model"))
    assert results["pricing_known"] is False
    assert results["served_models"] == ["mystery-model"]
    printed = capsys.readouterr().out
    assert "WARNING: no pricing for mystery-model" in printed


def test_unknown_requested_model_warns(golden: Path, tmp_path: Path) -> None:
    results = run(golden, tmp_path, make_classifier(pricing={}))
    assert results["pricing_known"] is False


def test_update_baseline_copies_output(golden: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "base" / "baseline.json"
    results = run(
        golden, tmp_path, make_classifier(), "--update-baseline", "--baseline", str(baseline)
    )
    assert json.loads(baseline.read_text(encoding="utf-8")) == results


def test_no_baseline_written_by_default(golden: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    run(golden, tmp_path, make_classifier(), "--baseline", str(baseline))
    assert not baseline.exists()


def test_missing_api_key_exits_2(
    golden: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out = tmp_path / "latest.json"
    assert run_evals.main(["--golden", str(golden), "--output", str(out)]) == 2
    assert not out.exists()


def test_build_results_empty_rows() -> None:
    results = run_evals.build_results(
        [], model="m", prompt_version="v", git_sha="s", timestamp="t", labels=["billing"]
    )
    assert results["accuracy"] == 0.0
    assert results["cost_usd"]["per_ticket"] == 0.0
    assert results["latency_s"] == {"p50": 0.0, "p95": 0.0}


def test_git_sha_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    assert run_evals.current_git_sha() == "deadbeef"


@pytest.mark.parametrize("bad", ["0", "-3", "abc"])
def test_limit_must_be_positive(bad: str) -> None:
    with pytest.raises(SystemExit):
        run_evals.parse_args(["--limit", bad])


def test_load_golden_limit_none_returns_all(golden: Path) -> None:
    assert len(run_evals.load_golden(golden, None)) == len(TICKETS)
