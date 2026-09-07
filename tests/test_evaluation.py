from collections import Counter

from app.adapters.analyzer import DeterministicAnalyzer
from app.domain.models import Inventory, Order, Product
from app.services.anomalies import evaluate_platform_rules
from app.services.catalog import load_anomaly_labels, load_catalog, load_inventory_snapshots, summary
from app.services.evaluation import evaluate_analyzer


def test_deterministic_fixture_and_analyzer_evaluation():
    products, inventory, orders = load_catalog()
    labels = load_anomaly_labels()
    assert (len(products), len(inventory), len(orders), len(load_inventory_snapshots())) == (100, 100, 800, 3000)
    assert Counter(label.anomaly_type for label in labels) == {"listing": 8, "inventory": 8, "order": 8}
    assert summary()["summary"] == {"product_count": 100, "inventory_count": 100, "order_count": 800, "inventory_snapshot_count": 3000, "labeled_anomaly_count": 24, "low_stock_count": 8, "attention_order_count": 8}
    result = evaluate_analyzer(DeterministicAnalyzer(), products, inventory, orders, labels)
    assert {metric: result[metric] for metric in ("tp", "fp", "fn")} == {"tp": 24, "fp": 0, "fn": 0}
    assert {metric: result[metric] for metric in ("precision", "recall", "f1")} == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert all(metrics["f1"] == 1.0 for metrics in result["per_type"].values())


def test_platform_listing_thresholds_change_the_deterministic_decision():
    product = Product(sku="SKU-X", title="A" * 35, bullets=["a", "b", "c", "d"], category="demo")
    inventory = [Inventory(sku="SKU-X", available=20, reorder_point=10)]
    orders = [Order(order_id="O-1", sku="SKU-X", status="shipped", age_days=1)]
    assert any(row["anomaly_type"] == "listing" for row in evaluate_platform_rules("amazon", [product], inventory, orders))
    assert evaluate_platform_rules("shopee", [product], inventory, orders) == []
