from __future__ import annotations

from app.adapters.analyzer import Analyzer
from app.domain.models import AnomalyLabel, Inventory, ListingAdvice, Order, Product

_TYPES = ("listing", "inventory", "order")


def _metrics(predicted: set[tuple[str, str]], expected: set[tuple[str, str]]) -> dict[str, float | int]:
    tp, fp, fn = len(predicted & expected), len(predicted - expected), len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def evaluate_advice(advice: list[ListingAdvice], labels: list[AnomalyLabel]) -> dict:
    predicted = {
        (row.sku, anomaly_type)
        for row in advice
        for anomaly_type, detected in (
            ("listing", any(issue.startswith(("标题缺少关键规格", "五点描述数量不足")) for issue in row.issues)),
            ("inventory", row.inventory_risk is not None),
            ("order", row.order_risk is not None),
        )
        if detected
    }
    expected = {(label.sku, label.anomaly_type) for label in labels}
    result = _metrics(predicted, expected)
    result["per_type"] = {
        anomaly_type: _metrics(
            {item for item in predicted if item[1] == anomaly_type},
            {item for item in expected if item[1] == anomaly_type},
        )
        for anomaly_type in _TYPES
    }
    return result


def evaluate_analyzer(analyzer: Analyzer, products: list[Product], inventory: list[Inventory], orders: list[Order], labels: list[AnomalyLabel]) -> dict:
    return evaluate_advice(analyzer.analyze(products, inventory, orders), labels)
