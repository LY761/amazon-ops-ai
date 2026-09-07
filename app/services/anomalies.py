from __future__ import annotations

from app.domain.models import Inventory, InventorySnapshot, ListingAdvice, Order, Product

_LISTING_PREFIXES = ("标题缺少关键规格", "五点描述数量不足")
PLATFORM_RULES = {
    "amazon": {"title_min": 45, "bullet_min": 5, "pending_order_days": 3},
    "shopee": {"title_min": 25, "bullet_min": 3, "pending_order_days": 2},
    "tiktok_shop": {"title_min": 30, "bullet_min": 3, "pending_order_days": 2},
}


def evaluate_platform_rules(platform: str, products: list[Product], inventory: list[Inventory], orders: list[Order], snapshots: list[InventorySnapshot] | None = None) -> list[dict[str, str | bool]]:
    """Apply auditable demo operating rules; production values must follow current platform policy."""
    if platform not in PLATFORM_RULES:
        raise ValueError(f"Unsupported platform: {platform}")
    rule = PLATFORM_RULES[platform]
    stock = {row.sku: row for row in inventory}
    by_sku = {product.sku: [order for order in orders if order.sku == product.sku] for product in products}
    history: dict[str, list[InventorySnapshot]] = {}
    for snapshot in snapshots or []:
        history.setdefault(snapshot.sku, []).append(snapshot)

    rows: list[dict[str, str | bool]] = []
    for product in products:
        issues = []
        if len(product.title) < rule["title_min"]:
            issues.append(f"标题长度 {len(product.title)} 低于 {platform} 运营阈值 {rule['title_min']}")
        if len(product.bullets) < rule["bullet_min"]:
            issues.append(f"卖点数量 {len(product.bullets)} 低于 {platform} 运营阈值 {rule['bullet_min']}")
        if issues:
            rows.append({"sku": product.sku, "anomaly_type": "listing", "detail": "；".join(issues), "executable": True})
        inv = stock[product.sku]
        if inv.available <= inv.reorder_point:
            values = sorted(history.get(product.sku, []), key=lambda row: row.snapshot_date)
            trend = f"；30天库存变化：{values[-1].available - values[0].available:+d}" if values else ""
            rows.append({"sku": product.sku, "anomaly_type": "inventory", "detail": f"低库存：剩余 {inv.available}，补货阈值 {inv.reorder_point}{trend}", "executable": False})
        aged = [order for order in by_sku[product.sku] if order.status != "shipped" and order.age_days >= rule["pending_order_days"]]
        if aged:
            rows.append({"sku": product.sku, "anomaly_type": "order", "detail": f"{len(aged)} 个订单待处理超过 {rule['pending_order_days']} 天", "executable": False})
    return rows


def platform_rule_summary(platform: str) -> dict:
    if platform not in PLATFORM_RULES:
        raise ValueError(f"Unsupported platform: {platform}")
    return {"platform": platform, "version": "demo-2026-09", "policy": PLATFORM_RULES[platform], "decision_layer": "deterministic_rules", "rag_role": "retrieve_and_explain_sop"}


def detected_anomalies(advice: list[ListingAdvice], snapshots: list[InventorySnapshot] | None = None) -> list[dict[str, str | bool]]:
    """Convert deterministic facts into one queue row per SKU and anomaly type."""
    history: dict[str, list[InventorySnapshot]] = {}
    for snapshot in snapshots or []:
        history.setdefault(snapshot.sku, []).append(snapshot)

    rows: list[dict[str, str | bool]] = []
    for item in advice:
        listing_issues = [issue for issue in item.issues if issue.startswith(_LISTING_PREFIXES)]
        if listing_issues:
            rows.append({"sku": item.sku, "anomaly_type": "listing", "detail": "；".join(listing_issues), "executable": True})
        if item.inventory_risk:
            values = sorted(history.get(item.sku, []), key=lambda row: row.snapshot_date)
            trend = f"；30天库存变化：{values[-1].available - values[0].available:+d}" if values else ""
            rows.append({"sku": item.sku, "anomaly_type": "inventory", "detail": item.inventory_risk + trend, "executable": False})
        if item.order_risk:
            rows.append({"sku": item.sku, "anomaly_type": "order", "detail": item.order_risk, "executable": False})
    return rows
