import json
from collections import Counter
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent.parent / "data" / "golden.jsonl"
CATEGORIES = {
    "billing",
    "bug_report",
    "feature_request",
    "account_access",
    "how_to",
    "cancellation",
}
FIELDS = {"id", "text", "label", "difficulty", "note"}


def _lines() -> list[str]:
    return [line for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return [json.loads(line) for line in _lines()]


def test_every_line_is_a_json_object() -> None:
    for n, line in enumerate(_lines(), start=1):
        assert isinstance(json.loads(line), dict), f"line {n} is not an object"


def test_fifty_rows_with_unique_ids(rows: list[dict]) -> None:
    ids = [r["id"] for r in rows]
    assert len(rows) == 50
    assert len(set(ids)) == 50
    assert set(ids) == {f"t{i:03d}" for i in range(1, 51)}


def test_schema_fields_present(rows: list[dict]) -> None:
    for r in rows:
        assert set(r) == FIELDS, r["id"]
        assert all(isinstance(r[f], str) and r[f].strip() for f in FIELDS), r["id"]
        assert r["text"].startswith("Subject: "), r["id"]


def test_labels_are_known_categories(rows: list[dict]) -> None:
    assert {r["label"] for r in rows} <= CATEGORIES


def test_category_balance(rows: list[dict]) -> None:
    counts = Counter(r["label"] for r in rows)
    assert set(counts) == CATEGORIES
    assert all(8 <= c <= 9 for c in counts.values()), counts


def test_difficulty_values_and_hard_count(rows: list[dict]) -> None:
    assert {r["difficulty"] for r in rows} <= {"easy", "hard"}
    assert 12 <= sum(r["difficulty"] == "hard" for r in rows) <= 18


def test_hard_rows_name_a_runner_up(rows: list[dict]) -> None:
    for r in rows:
        if r["difficulty"] == "hard":
            runner_up = r["note"].split("Runner-up: ")[-1].rstrip(".")
            assert runner_up in CATEGORIES - {r["label"]}, r["id"]


def test_no_duplicate_texts(rows: list[dict]) -> None:
    texts = [r["text"].strip().lower() for r in rows]
    assert len(set(texts)) == len(texts)


def test_ids_not_grouped_by_category(rows: list[dict]) -> None:
    labels = [r["label"] for r in sorted(rows, key=lambda r: r["id"])]
    longest = run = 1
    for prev, cur in zip(labels, labels[1:], strict=False):
        run = run + 1 if cur == prev else 1
        longest = max(longest, run)
    assert longest <= 2
