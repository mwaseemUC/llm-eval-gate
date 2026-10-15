import pytest

from evals import metrics

Y_TRUE = ["a", "a", "a", "b", "b", "c"]
Y_PRED = ["a", "a", "b", "b", "c", "c"]


def test_accuracy() -> None:
    assert metrics.accuracy(Y_TRUE, Y_PRED) == pytest.approx(4 / 6)


def test_accuracy_empty_is_zero() -> None:
    assert metrics.accuracy([], []) == 0.0


def test_accuracy_counts_errors_as_wrong() -> None:
    assert metrics.accuracy(["a", "b"], ["a", None]) == 0.5


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        metrics.accuracy(["a"], ["a", "b"])


def test_class_stats_values() -> None:
    a = metrics.class_stats(Y_TRUE, Y_PRED, "a")
    assert a["precision"] == 1.0
    assert a["recall"] == pytest.approx(2 / 3)
    assert a["f1"] == pytest.approx(0.8)
    assert a["support"] == 3

    b = metrics.class_stats(Y_TRUE, Y_PRED, "b")
    assert b["precision"] == 0.5
    assert b["recall"] == 0.5
    assert b["f1"] == 0.5
    assert b["support"] == 2


def test_zero_division_never_predicted_class() -> None:
    stats = metrics.class_stats(["a", "b"], ["a", "a"], "b")
    assert stats == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 1}


def test_zero_division_class_absent_from_truth() -> None:
    stats = metrics.class_stats(["a", "a"], ["a", "z"], "z")
    assert stats == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}


def test_zero_division_class_absent_everywhere() -> None:
    stats = metrics.class_stats(["a"], ["a"], "q")
    assert stats == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}


def test_macro_f1_is_unweighted_mean() -> None:
    labels = ["a", "b", "c"]
    c_f1 = 2 * 0.5 * 1.0 / 1.5
    expected = (0.8 + 0.5 + c_f1) / 3
    assert metrics.macro_f1(Y_TRUE, Y_PRED, labels) == pytest.approx(expected)


def test_macro_f1_no_labels() -> None:
    assert metrics.macro_f1([], [], []) == 0.0


def test_perfect_predictions() -> None:
    labels = ["a", "b", "c"]
    assert metrics.macro_f1(Y_TRUE, Y_TRUE, labels) == 1.0
    assert metrics.accuracy(Y_TRUE, Y_TRUE) == 1.0


def test_confusion_matrix() -> None:
    cm = metrics.confusion_matrix(Y_TRUE, Y_PRED, ["a", "b", "c"])
    assert cm == {
        "a": {"a": 2, "b": 1, "c": 0},
        "b": {"a": 0, "b": 1, "c": 1},
        "c": {"a": 0, "b": 0, "c": 1},
    }


def test_confusion_matrix_error_column_only_when_errors() -> None:
    cm = metrics.confusion_matrix(["a", "b"], ["a", None], ["a", "b"])
    assert cm["b"] == {"a": 0, "b": 0, metrics.ERROR_LABEL: 1}
    assert metrics.ERROR_LABEL not in metrics.confusion_matrix(["a"], ["a"], ["a"])["a"]


def test_label_set_ignores_errors() -> None:
    assert metrics.label_set(["b", "a"], ["c", None]) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [
        ([1.0, 2.0, 3.0, 4.0], 50, 2.5),
        ([1.0, 2.0, 3.0, 4.0], 0, 1.0),
        ([1.0, 2.0, 3.0, 4.0], 100, 4.0),
        ([4.0, 1.0, 3.0, 2.0], 95, 3.85),
        ([10.0, 20.0, 30.0, 40.0, 50.0], 50, 30.0),
        ([7.0], 95, 7.0),
        ([], 50, 0.0),
    ],
)
def test_percentile_linear_interpolation(values: list[float], pct: float, expected: float) -> None:
    assert metrics.percentile(values, pct) == pytest.approx(expected)


def test_percentile_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        metrics.percentile([1.0], 101)
