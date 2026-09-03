from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable


REQUIRED_CREDENTIALS = ("SP_API_REFRESH_TOKEN", "LWA_APP_ID", "LWA_CLIENT_SECRET")
SUPPORTED_MARKETPLACES = {"US", "CA", "MX", "BR", "UK", "DE", "FR", "IT", "ES", "JP", "AU"}


class AmazonSPAPIAdapter:
    """Read-only Amazon SP-API boundary backed by python-amazon-sp-api.

    The SDK import and client construction are lazy. Merely starting the app or
    reading integration status never sends a request to Amazon.
    """

    def __init__(
        self,
        marketplace: str | None = None,
        reorder_point: int | None = None,
        inventories_client: Any | None = None,
        orders_client: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        selected = (marketplace or os.getenv("SP_API_MARKETPLACE", "US")).upper()
        if selected not in SUPPORTED_MARKETPLACES:
            raise ValueError(f"Unsupported SP_API_MARKETPLACE: {selected}")
        self.marketplace = selected
        self.reorder_point = reorder_point if reorder_point is not None else int(os.getenv("SP_API_REORDER_POINT", "10"))
        self.inventories_client = inventories_client
        self.orders_client = orders_client
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def missing_credentials(self) -> list[str]:
        return [name for name in REQUIRED_CREDENTIALS if not os.getenv(name)]

    @property
    def configured(self) -> bool:
        return not self.missing_credentials

    def status(self) -> dict[str, Any]:
        try:
            sdk_version = version("python-amazon-sp-api")
        except PackageNotFoundError:
            sdk_version = None
        return {
            "provider": "saleweaver/python-amazon-sp-api",
            "sdk_version": sdk_version,
            "sdk_installed": sdk_version is not None,
            "mode": "live_read_only" if self.configured else "fixture_demo",
            "configured": self.configured,
            "marketplace": self.marketplace,
            "missing_credentials": self.missing_credentials,
            "capabilities": ["fba_inventory_read", "orders_status_read", "no_buyer_pii"],
        }

    def preview(self, lookback_days: int = 7) -> dict[str, Any]:
        self._require_credentials()
        inventory_response = self._inventory_client().get_inventory_summary_marketplace(details=True)
        created_after = (self.now() - timedelta(days=lookback_days)).isoformat()
        orders_response = self._orders_client().get_orders(CreatedAfter=created_after)
        return {
            "provider": "saleweaver/python-amazon-sp-api",
            "marketplace": self.marketplace,
            "read_only": True,
            "inventory": self.parse_inventory_payload(inventory_response.payload, self.reorder_point),
            "orders": self.parse_orders_payload(orders_response.payload, self.now()),
        }

    @staticmethod
    def parse_inventory_payload(payload: dict[str, Any], reorder_point: int = 10) -> list[dict[str, Any]]:
        rows = []
        for item in payload.get("inventorySummaries", []):
            sku = item.get("sellerSku")
            if not sku:
                continue
            details = item.get("inventoryDetails") or {}
            available = details.get("fulfillableQuantity", item.get("totalQuantity", 0))
            rows.append({
                "sku": str(sku).upper(),
                "available": max(int(available or 0), 0),
                "reorder_point": reorder_point,
                "low_stock": int(available or 0) <= reorder_point,
            })
        return rows

    @staticmethod
    def parse_orders_payload(payload: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        rows = []
        for item in payload.get("Orders", []):
            purchased = item.get("PurchaseDate")
            try:
                purchased_at = datetime.fromisoformat(str(purchased).replace("Z", "+00:00"))
                age_days = max((current - purchased_at).days, 0)
            except (TypeError, ValueError):
                age_days = 0
            rows.append({
                "order_id": item.get("AmazonOrderId", "unknown"),
                "status": item.get("OrderStatus", "Unknown"),
                "fulfillment_channel": item.get("FulfillmentChannel"),
                "age_days": age_days,
                "requires_attention": item.get("OrderStatus") not in {"Shipped", "Canceled"} and age_days >= 3,
            })
        return rows

    def _require_credentials(self) -> None:
        if self.missing_credentials:
            names = ", ".join(self.missing_credentials)
            raise RuntimeError(f"Amazon SP-API credentials are incomplete: {names}")

    def _client_kwargs(self) -> dict[str, Any]:
        from sp_api.base import Marketplaces

        return {
            "marketplace": Marketplaces[self.marketplace],
            "credentials": {
                "refresh_token": os.environ["SP_API_REFRESH_TOKEN"],
                "lwa_app_id": os.environ["LWA_APP_ID"],
                "lwa_client_secret": os.environ["LWA_CLIENT_SECRET"],
            },
        }

    def _inventory_client(self) -> Any:
        if self.inventories_client is None:
            from sp_api.api import Inventories

            self.inventories_client = Inventories(**self._client_kwargs())
        return self.inventories_client

    def _orders_client(self) -> Any:
        if self.orders_client is None:
            from sp_api.api import Orders

            self.orders_client = Orders(**self._client_kwargs())
        return self.orders_client
