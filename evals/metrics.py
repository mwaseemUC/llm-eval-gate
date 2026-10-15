"""Hand-written classification metrics.

Conventions:
- A prediction of ``None`` means the classifier errored; it never matches any label.
- Precision, recall and F1 are 0.0 whenever their denominator is zero.
- Macro-F1 is the unweighted mean of per-class F1 over ``labels``.
"""

from collections.abc import Sequence

ERROR_LABEL = "error"


def accuracy(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float:
    _check_lengths(y_true, y_pred)
    if not y_true:
        return 0.0
    correct = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p)
    return correct / len(y_true)


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def class_stats(y_true: Sequence[str], y_pred: Sequence[str | None], label: str) -> dict:
    _check_lengths(y_true, y_pred)
    pairs = list(zip(y_true, y_pred, strict=True))
    tp = sum(1 for t, p in pairs if t == label and p == label)
    fp = sum(1 for t, p in pairs if t != label and p == label)
    fn = sum(1 for t, p in pairs if t == label and p != label)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}


def per_class(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]
) -> dict[str, dict]:
    return {label: class_stats(y_true, y_pred, label) for label in labels}


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]) -> float:
    if not labels:
        return 0.0
    stats = per_class(y_true, y_pred, labels)
    return sum(s["f1"] for s in stats.values()) / len(labels)


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Rows are true labels, columns predicted labels. Errored predictions go to ``ERROR_LABEL``,
    a column that only appears when at least one prediction errored."""
    _check_lengths(y_true, y_pred)
    columns = list(labels)
    if any(p is None for p in y_pred):
        columns.append(ERROR_LABEL)
    rows = list(labels) + [t for t in dict.fromkeys(y_true) if t not in labels]
    matrix = {t: dict.fromkeys(columns, 0) for t in rows}
    for t, p in zip(y_true, y_pred, strict=True):
        col = ERROR_LABEL if p is None else p
        matrix[t][col] = matrix[t].get(col, 0) + 1
    return matrix


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear interpolation between closest ranks (numpy's default method). Empty input -> 0.0."""
    if not 0 <= pct <= 100:
        raise ValueError(f"pct must be in [0, 100], got {pct}")
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def label_set(y_true: Sequence[str], y_pred: Sequence[str | None]) -> list[str]:
    """Sorted union of true and (non-error) predicted labels."""
    return sorted(set(y_true) | {p for p in y_pred if p is not None})


def _check_lengths(y_true: Sequence[str], y_pred: Sequence[str | None]) -> None:
    if len(y_true) != len(y_pred):
        raise ValueError(f"length mismatch: {len(y_true)} labels vs {len(y_pred)} predictions")
