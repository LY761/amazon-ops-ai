from __future__ import annotations

from datetime import date, timedelta

from app.domain.models import AnomalyLabel, Inventory, InventorySnapshot, Order, Product

_START_DATE = date(2026, 1, 1)
_LISTING_SKUS = {f"SKU-{number:03d}" for number in range(1, 9)}
_INVENTORY_SKUS = {f"SKU-{number:03d}" for number in range(9, 17)}
_ORDER_SKUS = {f"SKU-{number:03d}" for number in range(17, 25)}


def _sku(number: int) -> str:
    return f"SKU-{number:03d}"


def _products() -> list[Product]:
    return [Product(sku=_sku(number), title=f"Travel Mug {number}" if _sku(number) in _LISTING_SKUS else f"Insulated Stainless Steel Travel Mug {number}, 20 oz Leakproof Lid for Daily Commute", bullets=["Non-slip grip", "Easy to clean"] if _sku(number) in _LISTING_SKUS else ["20 oz capacity for daily hydration", "Double wall insulation keeps drinks at temperature", "Leakproof lid fits standard cup holders", "BPA free materials for everyday use", "Easy care design for commuting and travel"], category="Travel Mug") for number in range(1, 101)]


def _inventory() -> list[Inventory]:
    return [Inventory(sku=_sku(number), available=5 if _sku(number) in _INVENTORY_SKUS else 40, reorder_point=10) for number in range(1, 101)]


def _orders() -> list[Order]:
    return [Order(order_id=f"ORDER-{number:03d}-{sequence:02d}", sku=_sku(number), status="pending" if _sku(number) in _ORDER_SKUS and sequence == 1 else "shipped", age_days=4 if _sku(number) in _ORDER_SKUS and sequence == 1 else 1) for number in range(1, 101) for sequence in range(1, 9)]


def load_catalog() -> tuple[list[Product], list[Inventory], list[Order]]:
    return _products(), _inventory(), _orders()


def load_inventory_snapshots() -> list[InventorySnapshot]:
    return [InventorySnapshot(sku=_sku(number), snapshot_date=_START_DATE + timedelta(days=offset), available=34 - offset if _sku(number) in _INVENTORY_SKUS else 40 - (offset % 3)) for number in range(1, 101) for offset in range(30)]


def load_anomaly_labels() -> list[AnomalyLabel]:
    return [*(AnomalyLabel(sku=sku, anomaly_type="listing") for sku in sorted(_LISTING_SKUS)), *(AnomalyLabel(sku=sku, anomaly_type="inventory") for sku in sorted(_INVENTORY_SKUS)), *(AnomalyLabel(sku=sku, anomaly_type="order") for sku in sorted(_ORDER_SKUS))]


def summary() -> dict:
    products, inventory, orders = load_catalog()
    snapshots, labels = load_inventory_snapshots(), load_anomaly_labels()
    return {"data_source":{"mode":"synthetic_batch_v2","contract":"Amazon SP-API shaped business data"}, "products":[row.model_dump() for row in products], "inventory":[row.model_dump() for row in inventory], "orders":[row.model_dump() for row in orders], "summary":{"product_count":len(products),"inventory_count":len(inventory),"order_count":len(orders),"inventory_snapshot_count":len(snapshots),"labeled_anomaly_count":len(labels),"low_stock_count":sum(row.available <= row.reorder_point for row in inventory),"attention_order_count":sum(row.status != "shipped" and row.age_days >= 3 for row in orders)}}
