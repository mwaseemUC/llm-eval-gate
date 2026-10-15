"""QA-reviewer tests: cross-module contracts, SDK request shape, workflow and dataset hygiene."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import message_create_params
from anthropic.types.cache_control_ephemeral_param import CacheControlEphemeralParam
from anthropic.types.json_output_format_param import JSONOutputFormatParam
from anthropic.types.output_config_param import OutputConfigParam
from anthropic.types.text_block_param import TextBlockParam

import classify
from evals import gate, run_evals

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


@dataclass
class Block:
    type: str
    text: str = ""


@dataclass
class SdkUsage:
    input_tokens: int = 500
    output_tokens: int = 40
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 600


@dataclass
class SdkResponse:
    content: list[Block]
    model: str = "claude-haiku-5-5"
    stop_reason: str = "end_turn"
    usage: SdkUsage = field(default_factory=SdkUsage)


class KeywordClient:
    """Fake Anthropic client: answers by keyword, always with a thinking block first."""

    def __init__(self, rules: dict[str, str]) -> None:
        self.rules = rules
        self.messages = self
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> SdkResponse:
        self.requests.append(request)
        ticket = request["messages"][0]["content"]
        category = next((c for k, c in self.rules.items() if k in ticket), "how_to")
        payload = {"category": category, "confidence": 0.8, "reasoning": "Keyword match."}
        return SdkResponse(content=[Block("thinking"), Block("text", json.dumps(payload))])


GOLDEN = [
    {"id": "q1", "text": "charged twice", "label": "billing", "difficulty": "easy"},
    {"id": "q2", "text": "app crashes", "label": "bug_report", "difficulty": "easy"},
    {"id": "q3", "text": "please cancel", "label": "cancellation", "difficulty": "easy"},
    {"id": "q4", "text": "how do I export", "label": "how_to", "difficulty": "easy"},
]
GOOD_RULES = {"charged": "billing", "crashes": "bug_report", "cancel": "cancellation"}
BAD_RULES = {"charged": "billing", "crashes": "how_to", "cancel": "billing"}


def real_classifier(client: KeywordClient) -> SimpleNamespace:
    def classify_fn(ticket: str, *, model: str | None = None) -> tuple[Any, Any]:
        return classify.classify(ticket, client=client, model=model, sleep=lambda _: None)

    return SimpleNamespace(
        classify=classify_fn,
        resolve_model=classify.resolve_model,
        Category=classify.Category,
        PROMPT_VERSION=classify.PROMPT_VERSION,
        PRICING=classify.PRICING,
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GITHUB_SHA", "feedfacecafe")
    monkeypatch.delenv("CLASSIFIER_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / "golden.jsonl").write_text(
        "\n".join(json.dumps(r) for r in GOLDEN) + "\n", encoding="utf-8"
    )
    return tmp_path


def run_pipeline(ws: Path, rules: dict[str, str], output: str, *extra: str) -> dict:
    out = ws / output
    code = run_evals.main(
        ["--golden", str(ws / "golden.jsonl"), "--output", str(out), "--workers", "2", *extra],
        classifier=real_classifier(KeywordClient(rules)),
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def gate_main(ws: Path) -> int:
    return gate.main(
        [
            "--baseline",
            str(ws / "baseline.json"),
            "--latest",
            str(ws / "latest.json"),
            "--report",
            str(ws / "gate_report.md"),
        ]
    )


def test_e2e_real_classify_to_gate_pass(workspace: Path) -> None:
    run_pipeline(
        workspace, GOOD_RULES, "latest.json", "--update-baseline",
        "--baseline", str(workspace / "baseline.json"),
    )  # fmt: skip
    latest = run_pipeline(workspace, GOOD_RULES, "latest.json")

    assert latest["accuracy"] == 1.0
    assert latest["n_errors"] == 0
    assert latest["served_models"] == ["claude-haiku-5-5"]
    assert latest["pricing_known"] is True
    assert latest["tokens"]["input"] == 4 * (500 + 600)
    assert set(latest["per_class"]) == {c.value for c in classify.Category}
    assert gate_main(workspace) == 0
    assert "## Eval gate: PASSED" in (workspace / "gate_report.md").read_text(encoding="utf-8")


def test_e2e_real_classify_to_gate_regression(workspace: Path) -> None:
    run_pipeline(
        workspace, GOOD_RULES, "latest.json", "--update-baseline",
        "--baseline", str(workspace / "baseline.json"),
    )  # fmt: skip
    latest = run_pipeline(workspace, BAD_RULES, "latest.json")

    assert latest["accuracy"] == 0.5
    assert gate_main(workspace) == 1
    report = (workspace / "gate_report.md").read_text(encoding="utf-8")
    assert "## Eval gate: FAILED" in report
    assert "Accuracy dropped 50.0 pts" in report
    assert "### Right -> wrong (2)" in report
    assert "`q2` (label `bug_report`): `bug_report` -> `how_to`" in report


def test_request_keys_are_accepted_by_installed_sdk() -> None:
    request = classify._build_request("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    params = message_create_params.MessageCreateParamsNonStreaming
    allowed = params.__required_keys__ | params.__optional_keys__

    assert set(request) <= allowed
    assert set(request["output_config"]) <= OutputConfigParam.__optional_keys__
    assert request["output_config"]["effort"] in {"low", "medium", "high", "xhigh", "max"}
    fmt = request["output_config"]["format"]
    assert (
        set(fmt)
        == JSONOutputFormatParam.__required_keys__ | JSONOutputFormatParam.__optional_keys__
    )
    assert fmt["type"] == "json_schema"
    system_block = request["system"][0]
    assert set(system_block) <= TextBlockParam.__required_keys__ | TextBlockParam.__optional_keys__
    assert set(system_block["cache_control"]) <= (
        CacheControlEphemeralParam.__required_keys__ | CacheControlEphemeralParam.__optional_keys__
    )


def test_validation_retry_ends_on_user_turn_and_drops_thinking() -> None:
    bad = SdkResponse(content=[Block("thinking"), Block("text", '{"category": "nope"}')])
    good_payload = {"category": "billing", "confidence": 0.7, "reasoning": "Charge."}
    good = SdkResponse(content=[Block("thinking"), Block("text", json.dumps(good_payload))])
    outcomes = [bad, good]
    calls: list[dict[str, Any]] = []

    def create(**request: Any) -> SdkResponse:
        calls.append({**request, "messages": list(request["messages"])})
        return outcomes.pop(0)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result, usage = classify.classify("charged twice", client=client)

    assert result.category == classify.Category.BILLING
    retry_messages = calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert retry_messages[1]["content"] == '{"category": "nope"}'
    assert calls[0]["system"] == calls[1]["system"]
    assert usage.output_tokens == 80


def test_two_extra_errors_on_fifty_fail_gate_below_error_rate_limit() -> None:
    baseline = {"accuracy": 0.90, "n": 50, "n_errors": 0}
    latest = {"accuracy": 0.86, "n": 50, "n_errors": 2}

    failures = gate.check(baseline, latest, gate.DEFAULT_THRESHOLD, gate.DEFAULT_MAX_ERROR_RATE)

    assert len(failures) == 1
    assert failures[0].startswith("Accuracy dropped 4.0 pts")


def test_one_ticket_swing_on_fifty_passes_gate() -> None:
    baseline = {"accuracy": 0.90, "n": 50, "n_errors": 0}
    latest = {"accuracy": 0.88, "n": 50, "n_errors": 1}

    assert gate.check(baseline, latest, gate.DEFAULT_THRESHOLD, gate.DEFAULT_MAX_ERROR_RATE) == []


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["ci.yml", "eval-gate.yml"])
def test_workflows_least_privilege_and_no_pull_request_target(name: str) -> None:
    text = _workflow(name)
    assert "pull_request_target" not in text
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    assert "write" not in text.split("permissions:")[1].split("\n\n")[0]
    assert "timeout-minutes:" in text


def test_eval_gate_never_echoes_the_secret_and_skips_without_it() -> None:
    text = _workflow("eval-gate.yml")
    for line in text.splitlines():
        if "echo" in line:
            assert "ANTHROPIC_API_KEY}" not in line and "$ANTHROPIC_API_KEY" not in line, line
    assert 'if [ -n "$ANTHROPIC_API_KEY" ]' in text
    gated_steps = text.count("if: steps.key.outputs.available == 'true'")
    gated_steps += text.count("if: always() && steps.key.outputs.available == 'true'")
    assert gated_steps == text.count("- name:") + text.count("- uses:") - 1


def test_dotenv_is_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored


def test_golden_has_no_real_looking_secrets_or_non_example_emails() -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "data" / "golden.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    secret = re.compile(r"sk-ant-|sk_live_|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}")
    for r in rows:
        text = r["text"]
        assert not secret.search(text), r["id"]
        for domain in re.findall(r"[\w.+-]+@([\w.-]+)", text):
            domain = domain.rstrip(".")
            assert domain == "example.com" or domain.endswith(".example.com"), (r["id"], domain)
        assert not re.search(r"\b\d{13,16}\b", text), r["id"]
