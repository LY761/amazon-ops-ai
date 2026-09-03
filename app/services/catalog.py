from __future__ import annotations

from app.domain.models import Inventory, Order, Product

PRODUCTS = [
    {"sku":"MUG-001","title":"Insulated Stainless Steel Travel Mug, 20 oz with Leakproof Lid","bullets":["20 oz capacity","Double wall insulation","Leakproof lid","Fits cup holders","BPA free"],"category":"Travel Mug"},
    {"sku":"MAT-002","title":"Yoga Mat","bullets":["Non-slip texture","Lightweight design"],"category":"Fitness Mat"},
    {"sku":"BOX-003","title":"Bamboo Storage Box with Lid for Desk and Home Organization","bullets":["Natural bamboo","Stackable lid","Smooth finish","Multi-room storage","Gift-ready"],"category":"Storage"},
]
INVENTORY = [{"sku":"MUG-001","available":42,"reorder_point":12},{"sku":"MAT-002","available":3,"reorder_point":10},{"sku":"BOX-003","available":25,"reorder_point":8}]
ORDERS = [{"order_id":"DEMO-1001","sku":"MUG-001","status":"shipped","age_days":1},{"order_id":"DEMO-1002","sku":"MAT-002","status":"pending","age_days":4},{"order_id":"DEMO-1003","sku":"BOX-003","status":"pending","age_days":1}]

def load_catalog() -> tuple[list[Product], list[Inventory], list[Order]]:
    return ([Product.model_validate(row) for row in PRODUCTS], [Inventory.model_validate(row) for row in INVENTORY], [Order.model_validate(row) for row in ORDERS])

def summary() -> dict:
    products, inventory, orders = load_catalog()
    return {"data_source":{"mode":"fixture_demo","contract":"Amazon SP-API shaped business data"}, "products":[row.model_dump() for row in products], "inventory":[row.model_dump() for row in inventory], "orders":[row.model_dump() for row in orders], "summary":{"product_count":len(products),"low_stock_count":sum(row.available <= row.reorder_point for row in inventory),"attention_order_count":sum(row.status != "shipped" and row.age_days >= 3 for row in orders)}}
