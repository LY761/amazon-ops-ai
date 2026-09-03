from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.domain.models import Inventory, ListingAdvice, Order, Product

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_API_MODE = "responses"


def load_local_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without replacing values inherited from the shell."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


class ListingAdviceBatch(BaseModel):
    advice: list[ListingAdvice]


class Analyzer(ABC):
    mode = "mock"
    provider = "deterministic"

    @abstractmethod
    def analyze(self, products: list[Product], inventory: list[Inventory], orders: list[Order]) -> list[ListingAdvice]: ...


class DeterministicAnalyzer(Analyzer):
    def analyze(self, products: list[Product], inventory: list[Inventory], orders: list[Order]) -> list[ListingAdvice]:
        stock = {row.sku: row for row in inventory}
        by_sku = {product.sku: [order for order in orders if order.sku == product.sku] for product in products}
        results = []
        for product in products:
            issues: list[str] = []
            score = 100
            if len(product.title) < 45:
                score -= 25; issues.append("标题缺少关键规格，建议补充材质、尺寸或使用场景")
            if len(product.bullets) < 5:
                score -= 20; issues.append("五点描述数量不足，未覆盖完整卖点")
            inv = stock[product.sku]
            inventory_risk = None
            if inv.available <= inv.reorder_point:
                inventory_risk = f"低库存：剩余 {inv.available}，补货阈值 {inv.reorder_point}"; issues.append(inventory_risk); score -= 10
            aged = [order for order in by_sku[product.sku] if order.status != "shipped" and order.age_days >= 3]
            order_risk = f"{len(aged)} 个订单待处理超过 3 天" if aged else None
            if order_risk: issues.append(order_risk); score -= 10
            title = product.title if len(product.title) >= 45 else f"{product.title} | Premium {product.category} for Everyday Use"
            bullets = (product.bullets + ["Durable design for daily use", "Clear product specifications", "Reliable seller support", "Optimized for gifting", "Easy care instructions"])[:5]
            results.append(ListingAdvice(sku=product.sku, score=max(score, 0), issues=issues, suggested_title=title, suggested_bullets=bullets, inventory_risk=inventory_risk, order_risk=order_risk))
        return results


class OpenAIResponsesAnalyzer(Analyzer):
    mode = "openai"
    provider = "openai_responses"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None, api_mode: str | None = None, timeout: float = 30.0, client: Any | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.api_mode = (api_mode or os.getenv("OPENAI_API_MODE", DEFAULT_OPENAI_API_MODE)).lower()
        if self.api_mode not in {"responses", "chat_completions"}:
            raise ValueError("OPENAI_API_MODE must be responses or chat_completions")
        self.provider = f"openai_{self.api_mode}"
        self.timeout, self.client = timeout, client

    def analyze(self, products: list[Product], inventory: list[Inventory], orders: list[Order]) -> list[ListingAdvice]:
        if not self.api_key:
            raise RuntimeError("OpenAI analyzer requires OPENAI_API_KEY")
        client = self.client or self._client()
        payload = {"products":[row.model_dump() for row in products], "inventory":[row.model_dump() for row in inventory], "orders":[row.model_dump() for row in orders]}
        instructions = "Analyze the supplied mock Amazon catalog. Return exactly one ListingAdvice per input SKU. Do not invent SKUs."
        try:
            if self.api_mode == "responses":
                response = client.responses.parse(model=self.model, instructions=instructions, input=json.dumps(payload, ensure_ascii=False), text_format=ListingAdviceBatch)
                parsed = getattr(response, "output_parsed", None)
            else:
                response = client.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    response_format=ListingAdviceBatch,
                )
                parsed = response.choices[0].message.parsed
        except Exception as error:
            raise RuntimeError(f"OpenAI {self.api_mode} request failed: {type(error).__name__}") from error
        if parsed is None:
            raise RuntimeError(f"OpenAI {self.api_mode} returned no structured output")
        try:
            batch = parsed if isinstance(parsed, ListingAdviceBatch) else ListingAdviceBatch.model_validate(parsed)
        except Exception as error:
            raise RuntimeError(f"OpenAI {self.api_mode} returned invalid structured output") from error
        expected = [product.sku for product in products]
        actual = [item.sku for item in batch.advice]
        if len(actual) != len(expected) or set(actual) != set(expected) or len(set(actual)) != len(actual):
            raise RuntimeError(f"OpenAI {self.api_mode} must return exactly one ListingAdvice for every input SKU")
        return batch.advice

    def _client(self) -> Any:
        from openai import OpenAI
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=0)


def create_analyzer(mode: str | None = None) -> Analyzer:
    selected = (mode or os.getenv("AMAZONOPS_ANALYZER", "deterministic")).lower()
    if selected in {"deterministic", "mock"}:
        return DeterministicAnalyzer()
    if selected == "openai":
        return OpenAIResponsesAnalyzer()
    raise ValueError("AMAZONOPS_ANALYZER must be deterministic or openai")


OpenAICompatibleAnalyzer = OpenAIResponsesAnalyzer
