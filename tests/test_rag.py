from __future__ import annotations

from qdrant_client import QdrantClient

from app.adapters.knowledge import KnowledgeAdvisor
from app.domain.models import ListingAdvice
from app.services.rag import PlatformRuleRAG, load_rule_chunks


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            lowered = text.lower()
            yield [float(any(word in lowered for word in ("标题", "五点", "listing"))), float(any(word in lowered for word in ("库存", "补货"))), float(any(word in lowered for word in ("订单", "发货", "退款"))), float(any(word in lowered for word in ("审批", "草稿", "rpa")))]


def rerank(query, passages):
    return [{"id": passage["id"], "score": 1.0 - index / 10} for index, passage in enumerate(passages)]


def make_rag(generator=None):
    return PlatformRuleRAG(embedder=FakeEmbedder(), reranker=rerank, client=QdrantClient(":memory:"), generator=generator, vector_store_label="qdrant_in_memory_test")


def test_markdown_split_keeps_heading_and_rule_metadata():
    chunks = load_rule_chunks()
    chunk = next(item for item in chunks if item.document_id == "amazon_listing" and item.heading == "发布前完整度检查")
    assert chunk.platform == "amazon" and chunk.topic == "listing" and chunk.rule_version == "demo-2026-09"
    assert "标题应覆盖品牌" in chunk.text and "不得自动提交Listing修改" not in chunk.text


def test_hybrid_qdrant_rrf_and_rerank_return_retrieval_evidence():
    rag = make_rag()
    hits = rag.search("标题缺少核心规格，应该先保存草稿吗", "amazon", top_k=2)
    assert hits and hits[0]["document_id"] == "amazon_listing"
    assert hits[0]["sparse_rank"] and hits[0]["dense_rank"] and hits[0]["rrf_score"] > 0 and hits[0]["rerank_score"] is not None
    assert rag.status()["vector_store"] == "qdrant_in_memory_test" and rag.status()["initialized"] is True


def test_generation_rejects_unknown_citation_and_uses_evidence_only_fallback():
    rag = make_rag(generator=lambda query, hits: {"answer": "无证据的结论", "citation_ids": ["invented"], "confidence": 0.99, "requires_human_review": False})
    result = rag.answer("出现敏感词应该怎么处理", "amazon")
    assert result["citations"] and "无证据的结论" not in result["answer"]
    assert result["requires_human_review"] is True


def test_generation_blocks_actions_outside_the_approval_boundary():
    def unsafe_generator(query, hits):
        return {"answer": "允许跳过人工审核后立即给买家退钱", "citation_ids": [hits[0]["id"]], "confidence": 0.99, "requires_human_review": False, "recommended_action": "save_listing_draft"}

    result = make_rag(generator=unsafe_generator).answer("退款是否自动执行", "amazon")
    assert result["recommended_action"] == "save_listing_draft" and result["requires_human_review"] is True
    assert result["source"] == "local_qdrant_rag_injected" and "跳过人工审核" not in result["answer"] and "退钱" not in result["answer"]


def test_no_evidence_requires_human_review():
    rag = make_rag()
    for query in ("zxqv token unrelated", "员工今天请假怎么审批", "网站标题SEO怎么修改", "餐厅订单催菜应该找谁", "商品如何参加秒杀活动", "Amazon广告关键词怎么竞价", "Shopee发票税率如何设置"):
        result = rag.answer(query, "amazon")
        assert result["citations"] == [] and result["requires_human_review"] is True and result["recommended_action"] == "create_manual_review"


def test_short_ecommerce_intents_are_not_rejected_by_domain_gate():
    rag = make_rag()
    for query in ("退款怎么办", "订单异常怎么办", "物流丢件怎么办", "库存不足怎么办"):
        assert rag.search(query, "amazon", 3)


def test_advisor_preserves_empty_citations_for_no_evidence():
    class NoEvidenceRAG:
        def answer(self, query, platform):
            return {"answer": "没有证据，转人工。", "citations": [], "confidence": 0.0, "requires_human_review": True, "recommended_action": "create_manual_review", "source": "local_qdrant_rag_no_evidence"}

    advice = ListingAdvice(sku="SKU-1", score=50, issues=["未知异常"], suggested_title="测试标题", suggested_bullets=["a", "b", "c", "d", "e"])
    result = KnowledgeAdvisor(rag_url="", local_rag=NoEvidenceRAG()).recommend(advice)
    assert result.citations == [] and result.requires_human_review is True and result.recommended_action == "create_manual_review"


def test_index_hash_changes_when_rule_metadata_changes(tmp_path):
    first = tmp_path / "first"; second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    template = "# 规则\n\n平台：amazon\n主题：listing\n版本：{version}\n\n## 标题\n\n- 商品标题和规格需要一致。\n"
    (first / "rule.md").write_text(template.format(version="v1"), encoding="utf-8")
    (second / "rule.md").write_text(template.format(version="v2"), encoding="utf-8")
    assert PlatformRuleRAG(knowledge_dir=first)._corpus_hash != PlatformRuleRAG(knowledge_dir=second)._corpus_hash
