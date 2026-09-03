from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters.amazon_sp_api import AmazonSPAPIAdapter, REQUIRED_CREDENTIALS
from app.adapters.analyzer import DeterministicAnalyzer
from app.main import create_app


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload


class FakeInventories:
    def __init__(self):
        self.calls = []

    def get_inventory_summary_marketplace(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse({
            "inventorySummaries": [
                {"sellerSku": "mat-002", "totalQuantity": 9, "inventoryDetails": {"fulfillableQuantity": 3}},
                {"sellerSku": "MUG-001", "totalQuantity": 42},
                {"totalQuantity": 99},
            ]
        })


class FakeOrders:
    def __init__(self):
        self.calls = []

    def get_orders(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse({
            "Orders": [
                {"AmazonOrderId": "ORDER-1", "OrderStatus": "Unshipped", "PurchaseDate": "2026-08-29T00:00:00Z", "FulfillmentChannel": "AFN"},
                {"AmazonOrderId": "ORDER-2", "OrderStatus": "Shipped", "PurchaseDate": "2026-09-02T00:00:00Z", "FulfillmentChannel": "MFN"},
            ]
        })


def clear_credentials(monkeypatch):
    for name in REQUIRED_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


def set_credentials(monkeypatch):
    for name in REQUIRED_CREDENTIALS:
        monkeypatch.setenv(name, "test-only")


def test_status_without_credentials_is_offline_and_names_missing_values(monkeypatch):
    clear_credentials(monkeypatch)
    adapter = AmazonSPAPIAdapter()
    status = adapter.status()
    assert status["configured"] is False
    assert status["mode"] == "fixture_demo"
    assert status["missing_credentials"] == list(REQUIRED_CREDENTIALS)
    assert status["capabilities"] == ["fba_inventory_read", "orders_status_read", "no_buyer_pii"]


def test_preview_maps_official_response_shapes_without_buyer_pii(monkeypatch):
    set_credentials(monkeypatch)
    inventory, orders = FakeInventories(), FakeOrders()
    adapter = AmazonSPAPIAdapter(
        reorder_point=10,
        inventories_client=inventory,
        orders_client=orders,
        now=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    result = adapter.preview(7)
    assert result["read_only"] is True
    assert result["inventory"] == [
        {"sku": "MAT-002", "available": 3, "reorder_point": 10, "low_stock": True},
        {"sku": "MUG-001", "available": 42, "reorder_point": 10, "low_stock": False},
    ]
    assert result["orders"][0] == {
        "order_id": "ORDER-1",
        "status": "Unshipped",
        "fulfillment_channel": "AFN",
        "age_days": 5,
        "requires_attention": True,
    }
    assert "buyer" not in str(result).lower()
    assert inventory.calls == [{"details": True}]
    assert "CreatedAfter" in orders.calls[0]


def test_preview_refuses_network_path_when_credentials_are_missing(monkeypatch):
    clear_credentials(monkeypatch)
    adapter = AmazonSPAPIAdapter(
        inventories_client=pytest.fail,
        orders_client=pytest.fail,
    )
    with pytest.raises(RuntimeError, match="SP_API_REFRESH_TOKEN"):
        adapter.preview()


def test_integration_endpoints_expose_readiness_without_secrets(tmp_path, monkeypatch):
    clear_credentials(monkeypatch)
    app = create_app(
        database=tmp_path / "db.sqlite",
        artifacts=tmp_path / "artifacts",
        analyzer=DeterministicAnalyzer(),
        amazon=AmazonSPAPIAdapter(),
    )
    client = TestClient(app)
    status = client.get("/api/integrations/amazon/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
    rejected = client.post("/api/integrations/amazon/preview", json={"lookback_days": 7})
    assert rejected.status_code == 409
    assert "test-only" not in rejected.text
