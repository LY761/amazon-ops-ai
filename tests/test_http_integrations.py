import httpx
import pytest
from pydantic import ValidationError

from app.adapters.http_integrations import LocalIntegrationGateway


def test_sync_retries_rate_limit_validates_data_and_sends_scope():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": [{"sku": "sku-1", "available": 8, "reorder_point": 10}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = LocalIntegrationGateway("http://127.0.0.1:8000", "test-token", client).sync("shopee", "inventory", "sync-001", 2)
    assert result["attempts"] == 3 and result["records"][0]["sku"] == "sku-1"
    assert calls[-1].headers["authorization"] == "Bearer test-token" and calls[-1].headers["x-scopes"] == "data:read"

    invalid = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"sku": "sku-1", "available": -1, "reorder_point": 10}]})))
    try:
        LocalIntegrationGateway("http://127.0.0.1:8000", "test-token", invalid).sync("erp", "inventory", "sync-002")
        raise AssertionError("invalid inventory must fail validation")
    except ValidationError:
        pass


def test_external_order_connector_and_feishu_webhook_are_configurable_without_exposing_tokens():
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "connector.example":
            return httpx.Response(200, json={"data": [{"order_id": "S-1", "sku": "sku-1", "status": "pending", "age_days": 1}]})
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    gateway = LocalIntegrationGateway(
        "http://127.0.0.1:8000", "fixture-token", httpx.Client(transport=httpx.MockTransport(handler)),
        source_endpoints={"shopee": "https://connector.example/api"}, source_tokens={"shopee": "secret-token"},
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
    )
    result = gateway.sync("shopee", "orders", "orders-001")
    notice = gateway.notify({"id": "task-1", "status": "succeeded", "retry_count": 0, "duration_ms": 1200})
    assert result["mode"] == "external" and result["records"][0]["order_id"] == "S-1"
    assert calls[0].headers["authorization"] == "Bearer secret-token" and calls[0].headers["x-idempotency-key"] == "orders-001"
    assert notice["mode"] == "webhook" and gateway.status()["sources"]["shopee"]["configured"] is True
    assert "secret-token" not in str(gateway.status())


def test_external_connectors_reject_insecure_non_loopback_urls():
    with pytest.raises(ValueError, match="HTTPS"):
        LocalIntegrationGateway("http://127.0.0.1:8000", "token", source_endpoints={"shopee": "http://example.com/api"})
