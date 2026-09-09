from __future__ import annotations
import os
from typing import Any
import httpx
from pydantic import BaseModel, Field, model_validator
from app.domain.models import ListingAdvice
from app.services.rag import PlatformRuleRAG

class KnowledgeCitation(BaseModel):
    source: str
    title: str
    excerpt: str
    id: str | None = None
    heading: str | None = None
    platform: str | None = None
    topic: str | None = None
    rule_version: str | None = None
    sparse_rank: int | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    final_score: float | None = None

class Recommendation(BaseModel):
    """Stable response contract for an optional EcomPilot RAG endpoint."""
    answer: str = Field(min_length=1)
    citations: list[KnowledgeCitation]
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool
    recommended_action: str = Field(min_length=1)
    source: str

    @model_validator(mode="after")
    def empty_citations_are_only_valid_for_manual_review(self) -> "Recommendation":
        if not self.citations and not (self.confidence == 0 and self.requires_human_review and self.recommended_action == "create_manual_review"):
            raise ValueError("recommendations without citations must be zero-confidence manual reviews")
        return self

class KnowledgeAdvisor:
    def __init__(self, rag_url: str | None = None, timeout: float = 4.0, local_rag: PlatformRuleRAG | None = None) -> None:
        self.rag_url = rag_url if rag_url is not None else os.getenv("AMAZONOPS_RAG_URL", "").strip()
        self.timeout = timeout
        self.local_rag = local_rag or PlatformRuleRAG()
    def status(self) -> dict[str, Any]:
        return self.local_rag.status()
    def search(self, query: str, platform: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self.local_rag.search(query, platform, top_k)
    def close(self) -> None:
        self.local_rag.close()
    def recommend(self, advice: ListingAdvice, platform: str = "amazon", rule_evidence: list[str] | None = None) -> Recommendation:
        if self.rag_url:
            try:
                response = httpx.post(self.rag_url, json={"platform": platform, "sku": advice.sku, "rule_evidence": rule_evidence or [], "issues": advice.issues, "suggested_title": advice.suggested_title}, timeout=self.timeout)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                payload["source"] = "external_rag"
                return Recommendation.model_validate(payload)
            except (httpx.HTTPError, ValueError):
                pass
        query = "；".join([f"SKU {advice.sku}", *(rule_evidence or advice.issues), advice.suggested_title])
        try:
            payload = self.local_rag.answer(query, platform)
            return Recommendation.model_validate(payload)
        except Exception:
            return self._safe_fallback(advice, platform, rule_evidence or [])

    @staticmethod
    def _safe_fallback(advice: ListingAdvice, platform: str, rule_evidence: list[str]) -> Recommendation:
        issues = "；".join(rule_evidence or advice.issues) or "未发现Listing完整度问题"
        return Recommendation(answer=f"{advice.sku}存在：{issues}。知识检索不可用，已转人工审核。", citations=[], confidence=0.0, requires_human_review=True, recommended_action="create_manual_review", source="local_qdrant_rag_unavailable")
