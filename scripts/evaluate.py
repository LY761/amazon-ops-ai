from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.analyzer import DeterministicAnalyzer
from app.services.catalog import load_anomaly_labels, load_catalog, load_inventory_snapshots
from app.services.evaluation import evaluate_analyzer


def main() -> None:
    products, inventory, orders = load_catalog()
    labels = load_anomaly_labels()
    result = {
        "dataset": {
            "products": len(products),
            "inventory_rows": len(inventory),
            "orders": len(orders),
            "inventory_snapshots": len(load_inventory_snapshots()),
            "labeled_anomalies": len(labels),
        },
        "metrics": evaluate_analyzer(DeterministicAnalyzer(), products, inventory, orders, labels),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
