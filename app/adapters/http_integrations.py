from __future__ import annotations

from time import sleep
from urllib.parse import urlparse

import httpx
from pydantic import TypeAdapter

from app.domain.models import Inventory, Order, Product


SOURCES = {"amazon", "shopee", "tiktok_shop", "erp", "logistics"}
RESOURCE_MODELS = {"products": Product, "inventory": Inventory, "orders": Order}


class LocalIntegrationGateway:
    """Uses configured normalized APIs and falls back to loopback fixtures."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None, max_attempts: int = 3, source_endpoints: dict[str, str] | None = None, source_tokens: dict[str, str] | None = None, feishu_webhook_url: str = "") -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Mock integration base URL must use an HTTP loopback host")
        self.base_url, self.token, self.client, self.max_attempts = base_url.rstrip("/"), token, client, max_attempts
        self.source_endpoints = {key: value.rstrip("/") for key, value in (source_endpoints or {}).items() if value}
        self.source_tokens = source_tokens or {}
        self.feishu_webhook_url = feishu_webhook_url
        for url in [*self.source_endpoints.values(), *([feishu_webhook_url] if feishu_webhook_url else [])]:
            target = urlparse(url)
            if target.scheme != "https" and not (target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost", "::1"}):
                raise ValueError("External integration URLs must use HTTPS or HTTP loopback")

    def sync(self, source: str, resource: str, idempotency_key: str, failures_before_success: int = 0) -> dict:
        if source not in SOURCES or resource not in RESOURCE_MODELS:
            raise ValueError("Unsupported source or resource")
        endpoint = self.source_endpoints.get(source)
        if endpoint:
            headers = {"X-Idempotency-Key": idempotency_key}
            if self.source_tokens.get(source):
                headers["Authorization"] = f"Bearer {self.source_tokens[source]}"
            payload, attempts = self._post(f"{endpoint}/{resource}", headers, {"idempotency_key": idempotency_key})
            mode = "external"
        else:
            headers = {"Authorization": f"Bearer {self.token}", "X-Scopes": "data:read"}
            payload, attempts = self._post(f"{self.base_url}/simulator/data/{source}/{resource}", headers, {"idempotency_key": idempotency_key, "failures_before_success": failures_before_success})
            mode = "fixture_demo"
        rows = TypeAdapter(list[RESOURCE_MODELS[resource]]).validate_python(payload.get("data"))
        return {"source": source, "resource": resource, "mode": mode, "records": [row.model_dump(mode="json") for row in rows], "attempts": attempts}

    def notify(self, task: dict) -> dict:
        payload = {"msg_type": "text", "content": {"text": f"RPA任务 {task['id']} 已完成，状态 {task['status']}，重试 {task['retry_count']} 次，处理时长 {task['duration_ms']}ms"}}
        if self.feishu_webhook_url:
            response, attempts = self._post(self.feishu_webhook_url, {}, payload)
            mode = "webhook"
        else:
            headers = {"Authorization": f"Bearer {self.token}", "X-Scopes": "notify:write"}
            response, attempts = self._post(f"{self.base_url}/simulator/api/feishu/webhook", headers, payload)
            mode = "fixture_demo"
        return {"delivery": response, "payload": payload, "attempts": attempts, "mode": mode}

    def status(self) -> dict:
        return {
            "sources": {
                source: {
                    "mode": "external" if source in self.source_endpoints else "fixture_demo",
                    "configured": source in self.source_endpoints,
                    "resources": sorted(RESOURCE_MODELS),
                    "required_env": [f"{source.upper()}_API_BASE_URL", f"{source.upper()}_API_TOKEN"],
                }
                for source in sorted(SOURCES)
            },
            "feishu": {"mode": "webhook" if self.feishu_webhook_url else "fixture_demo", "configured": bool(self.feishu_webhook_url), "required_env": ["FEISHU_WEBHOOK_URL"]},
        }

    def _post(self, url: str, headers: dict[str, str], payload: dict) -> tuple[dict, int]:
        owned_client = self.client is None
        client = self.client or httpx.Client(timeout=5)
        try:
            for attempt in range(1, self.max_attempts + 1):
                response = client.post(url, json=payload, headers=headers)
                if (response.status_code == 429 or response.status_code >= 500) and attempt < self.max_attempts:
                    sleep(min(float(response.headers.get("Retry-After", "0")), 1.0))
                    continue
                response.raise_for_status()
                return response.json(), attempt
        finally:
            if owned_client:
                client.close()
        raise RuntimeError("Integration request exhausted retries")
