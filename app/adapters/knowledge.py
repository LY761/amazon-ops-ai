from __future__ import annotations
import os
from typing import Any
import httpx
from pydantic import BaseModel, Field
from app.domain.models import ListingAdvice

class KnowledgeCitation(BaseModel):
    source: str
    title: str
    excerpt: str

class Recommendation(BaseModel):
    """Stable response contract for an optional EcomPilot RAG endpoint."""
    answer: str = Field(min_length=1)
    citations: list[KnowledgeCitation] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool
    recommended_action: str = Field(min_length=1)
    source: str

class KnowledgeAdvisor:
    def __init__(self, rag_url: str | None = None, timeout: float = 4.0) -> None:
        self.rag_url = rag_url if rag_url is not None else os.getenv("AMAZONOPS_RAG_URL", "").strip()
        self.timeout = timeout
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
        return self._local(advice, platform, rule_evidence or [])
    @staticmethod
    def _local(advice: ListingAdvice, platform: str, rule_evidence: list[str]) -> Recommendation:
        issues = "；".join(rule_evidence or advice.issues) or "未发现Listing完整度问题"
        return Recommendation(answer=f"{advice.sku}存在：{issues}。建议先由运营确认，再保存为Seller Central草稿，不直接发布。", citations=[
            KnowledgeCitation(source="local_knowledge", title=f"{platform} Listing运营规则SOP", excerpt="规则引擎负责判定异常；知识库只提供当前规则来源、解释和处理步骤。"),
            KnowledgeCitation(source="local_knowledge", title="运营自动化审批规则", excerpt="涉及后台写入的动作必须进入人工审批队列；审批后只保存草稿，不执行发布。"),
        ], confidence=0.86 if advice.issues else 0.72, requires_human_review=True, recommended_action="save_listing_draft", source="local_knowledge")
