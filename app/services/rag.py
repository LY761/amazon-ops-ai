from __future__ import annotations

import hashlib
import json
import math
import os
import re
from threading import Lock
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_DIR = ROOT / "knowledge"
DEFAULT_VECTOR_PATH = ROOT / "data" / "qdrant"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
RERANKER_MODEL = "ms-marco-MultiBERT-L-12"
COLLECTION = "platform_rules"
INDEX_VERSION = "heading-clause-v1"
SUPPORTED_RULE_TERMS = ("listing", "sku", "seller central", "标题", "五点", "主图", "变体", "文案", "敏感词", "违规词", "绝对化承诺", "夸大承诺", "夸大疗效", "图片文字", "商品描述", "核心属性", "草稿", "库存", "补货", "可售", "安全库存", "库存覆盖", "断货", "入库", "订单", "发货", "履约", "退款", "买家", "售后", "物流", "丢件", "赔付", "投诉", "rpa", "自动化", "审批", "审核", "调价", "发布", "权限", "审计证据")
OUT_OF_DOMAIN_TERMS = ("员工", "请假", "感冒", "发烧", "月球", "采矿", "量子", "芯片", "网站", "seo", "短视频", "播放量", "餐厅", "催菜")
UNSAFE_ANSWER_PATTERNS = ("无需审批", "绕过审批", "允许未经审批", "可以自动发布", "可以自动退款", "可以自动调价", "直接发布", "直接退款", "直接调价", "直接补货下单")


class GroundedOutput(BaseModel):
    answer: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool
    recommended_action: str


@dataclass(frozen=True)
class RuleChunk:
    id: str
    document_id: str
    title: str
    platform: str
    topic: str
    rule_version: str
    heading: str
    text: str

    def payload(self) -> dict[str, str]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "platform": self.platform,
            "topic": self.topic,
            "rule_version": self.rule_version,
            "heading": self.heading,
            "text": self.text,
        }


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower())
    chars = [text[index:index + 2].lower() for index in range(len(text) - 1) if "\u4e00" <= text[index] <= "\u9fff" and "\u4e00" <= text[index + 1] <= "\u9fff"]
    return words + chars


def _metadata(lines: list[str]) -> tuple[dict[str, str], int]:
    values: dict[str, str] = {}
    for index, line in enumerate(lines[1:], 1):
        match = re.match(r"^(平台|主题|版本)：\s*(.+)$", line.strip())
        if match:
            values[{"平台": "platform", "主题": "topic", "版本": "rule_version"}[match.group(1)]] = match.group(2).strip()
            continue
        if line.strip().startswith("标签：") or not line.strip():
            continue
        return values, index
    return values, len(lines)


def load_rule_chunks(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR, target_size: int = 420) -> list[RuleChunk]:
    """Split Markdown on headings and bullet/paragraph clauses, never raw characters."""
    chunks: list[RuleChunk] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), path.stem)
        metadata, start = _metadata(lines)
        platform = metadata.get("platform", "all")
        topic = metadata.get("topic", "governance")
        version = metadata.get("rule_version", "unversioned")
        heading = title
        clauses: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            if stripped.startswith("## "):
                if clauses:
                    chunks.extend(_pack_clauses(path.stem, title, platform, topic, version, heading, clauses, target_size))
                    clauses = []
                heading = stripped[3:].strip()
            elif stripped.startswith("- ") or stripped:
                clauses.append(stripped[2:].strip() if stripped.startswith("- ") else stripped)
        if clauses:
            chunks.extend(_pack_clauses(path.stem, title, platform, topic, version, heading, clauses, target_size))
    return chunks


def _pack_clauses(document_id: str, title: str, platform: str, topic: str, version: str, heading: str, clauses: list[str], target_size: int) -> list[RuleChunk]:
    packed: list[RuleChunk] = []
    current: list[str] = []
    for clause in clauses:
        if current and len("\n".join(current)) + len(clause) + 1 > target_size:
            packed.append(_make_chunk(document_id, title, platform, topic, version, heading, current, len(packed)))
            current = [current[-1], clause]  # clause overlap preserves context without splitting a rule.
        else:
            current.append(clause)
    if current:
        packed.append(_make_chunk(document_id, title, platform, topic, version, heading, current, len(packed)))
    return packed


def _make_chunk(document_id: str, title: str, platform: str, topic: str, version: str, heading: str, clauses: list[str], index: int) -> RuleChunk:
    heading_id = hashlib.sha1(heading.encode("utf-8")).hexdigest()[:8]
    return RuleChunk(f"{document_id}:{heading_id}:{index}", document_id, title, platform, topic, version, heading, "\n".join(clauses))


class PlatformRuleRAG:
    """Small local RAG: BM25 + Qdrant dense retrieval -> RRF -> FlashRank."""

    def __init__(
        self,
        knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
        vector_path: Path | str = DEFAULT_VECTOR_PATH,
        *,
        embedder: Any | None = None,
        reranker: Any | None = None,
        client: Any | None = None,
        generator: Callable[[str, list[dict[str, Any]]], dict[str, Any]] | None = None,
        vector_store_label: str | None = None,
    ) -> None:
        self.knowledge_dir, self.vector_path = Path(knowledge_dir), vector_path
        self._embedder, self._reranker, self._client, self.generator = embedder, reranker, client, generator
        self.vector_store_label = vector_store_label or ("qdrant_local_persistent" if client is None else "qdrant_injected")
        self._init_lock = Lock()
        self.chunks = load_rule_chunks(self.knowledge_dir)
        self._tokens = [tokenize(chunk.text + " " + chunk.heading + " " + chunk.title) for chunk in self.chunks]
        index_manifest = {"index_version": INDEX_VERSION, "embedding_model": EMBEDDING_MODEL, "chunks": [{"id": chunk.id, **chunk.payload()} for chunk in self.chunks]}
        self._corpus_hash = hashlib.sha256(json.dumps(index_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        self._ready = False
        self._last_generation: str | None = None

    def status(self) -> dict[str, Any]:
        openai_configured = os.getenv("AMAZONOPS_RAG_GENERATION", "").lower() == "openai" and bool(os.getenv("OPENAI_API_KEY"))
        return {"vector_store": self.vector_store_label, "embedding_model": EMBEDDING_MODEL, "reranker": RERANKER_MODEL, "documents": len({chunk.document_id for chunk in self.chunks}), "chunks": len(self.chunks), "chunk_strategy": "markdown_heading_complete_clause_with_clause_overlap", "retrieval": "BM25 + BGE dense -> RRF -> Cross-Encoder/RRF final score", "generation_configured": "openai_grounded" if openai_configured else "evidence_template", "last_generation": self._last_generation, "initialized": self._ready}

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            if not self.chunks:
                raise RuntimeError("No Markdown rule documents found")
            if self._embedder is None:
                from fastembed import TextEmbedding
                self._embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
            if self._client is None:
                from qdrant_client import QdrantClient
                self._client = QdrantClient(path=str(self.vector_path))
            if not self._collection_current():
                vectors = [list(map(float, vector)) for vector in self._embed(list(self._index_text(chunk) for chunk in self.chunks))]
                self._rebuild_collection(vectors)
            if self._reranker is None:
                from flashrank import Ranker
                self._reranker = Ranker(model_name=RERANKER_MODEL)
            self._ready = True

    @staticmethod
    def _index_text(chunk: RuleChunk) -> str:
        return f"{chunk.title}\n{chunk.heading}\n{chunk.text}"

    def _embed(self, texts: Iterable[str]) -> list[Any]:
        return list(self._embedder.embed(list(texts)))

    def _collection_current(self) -> bool:
        try:
            if not self._client.collection_exists(COLLECTION) or self._client.count(COLLECTION, exact=True).count != len(self.chunks):
                return False
            points, _ = self._client.scroll(COLLECTION, limit=1, with_payload=True, with_vectors=False)
            return bool(points and points[0].payload.get("corpus_hash") == self._corpus_hash)
        except Exception:
            return False

    def _rebuild_collection(self, vectors: list[list[float]]) -> None:
        from qdrant_client import models
        if self._client.collection_exists(COLLECTION):
            self._client.delete_collection(COLLECTION)
        self._client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE))
        self._client.upsert(COLLECTION, [models.PointStruct(id=str(uuid5(NAMESPACE_URL, chunk.id)), vector=vector, payload={"chunk_id": chunk.id, "corpus_hash": self._corpus_hash, **chunk.payload()}) for chunk, vector in zip(self.chunks, vectors)])

    def _bm25(self, query: str, candidates: list[int]) -> dict[int, float]:
        corpus = [self._tokens[index] for index in candidates]
        query_tokens = tokenize(query)
        if not corpus or not query_tokens:
            return {}
        average = sum(map(len, corpus)) / len(corpus)
        df = Counter(token for tokens in corpus for token in set(tokens))
        scores: dict[int, float] = {}
        for index, terms in zip(candidates, corpus):
            tf, score = Counter(terms), 0.0
            for token in query_tokens:
                if not tf[token]:
                    continue
                idf = math.log(1 + (len(corpus) - df[token] + 0.5) / (df[token] + 0.5))
                score += idf * tf[token] * 2.5 / (tf[token] + 1.5 * (0.25 + 0.75 * len(terms) / average))
            if score > 0:
                scores[index] = score
        return scores

    def search(self, query: str, platform: str = "amazon", top_k: int = 3) -> list[dict[str, Any]]:
        if not self._in_domain(query):
            return []
        self._ensure_ready()
        candidates = [index for index, chunk in enumerate(self.chunks) if chunk.platform in {platform, "all"}]
        if not candidates:
            return []
        sparse = self._bm25(query, candidates)
        from qdrant_client import models
        dense_points = self._client.query_points(
            COLLECTION,
            query=list(map(float, self._embed([query])[0])),
            query_filter=models.Filter(should=[models.FieldCondition(key="platform", match=models.MatchValue(value=platform)), models.FieldCondition(key="platform", match=models.MatchValue(value="all"))]),
            limit=min(max(top_k * 4, 8), len(candidates)),
        ).points
        chunk_index = {chunk.id: index for index, chunk in enumerate(self.chunks)}
        dense = {chunk_index[point.payload["chunk_id"]]: float(point.score) for point in dense_points}
        sparse_order = sorted(sparse, key=sparse.get, reverse=True)
        dense_order = sorted(dense, key=dense.get, reverse=True)
        sparse_rank = {index: rank + 1 for rank, index in enumerate(sparse_order)}
        dense_rank = {index: rank + 1 for rank, index in enumerate(dense_order)}
        fused = sorted(set(sparse_rank) | set(dense_rank), key=lambda index: 1 / (60 + sparse_rank.get(index, 10_000)) + 1 / (60 + dense_rank.get(index, 10_000)), reverse=True)[: max(top_k * 3, 6)]
        hits = [self._hit(index, sparse_rank.get(index), dense_rank.get(index), 1 / (60 + sparse_rank.get(index, 10_000)) + 1 / (60 + dense_rank.get(index, 10_000))) for index in fused]
        return self._rerank(query, hits, top_k)

    @staticmethod
    def _in_domain(query: str) -> bool:
        # ponytail: scope gate mirrors the small rule corpus; replace with a labeled intent classifier when topics expand.
        lowered = query.lower()
        if any(term in lowered for term in OUT_OF_DOMAIN_TERMS):
            return False
        return any(term in lowered for term in SUPPORTED_RULE_TERMS)

    def _hit(self, index: int, sparse_rank: int | None, dense_rank: int | None, rrf_score: float) -> dict[str, Any]:
        chunk = self.chunks[index]
        return {"id": chunk.id, **chunk.payload(), "excerpt": chunk.text, "sparse_rank": sparse_rank, "dense_rank": dense_rank, "rrf_score": round(rrf_score, 6), "rerank_score": None}

    def _rerank(self, query: str, hits: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if not hits:
            return []
        passages = [{"id": hit["id"], "text": f"{hit['title']} {hit['heading']} {hit['excerpt']}", "meta": hit} for hit in hits]
        if hasattr(self._reranker, "rerank"):
            from flashrank import RerankRequest
            ranked = self._reranker.rerank(RerankRequest(query=query, passages=passages))
        else:
            ranked = self._reranker(query, passages)
        by_id = {hit["id"]: hit for hit in hits}
        result: list[dict[str, Any]] = []
        max_rrf = max(hit["rrf_score"] for hit in hits)
        for row in ranked:
            hit = dict(by_id[row["id"]])
            hit["rerank_score"] = round(float(row.get("score", 0.0)), 6)
            hit["final_score"] = round(0.5 * hit["rerank_score"] + 0.5 * hit["rrf_score"] / max_rrf, 6)
            result.append(hit)
        return sorted(result, key=lambda hit: hit["final_score"], reverse=True)[:top_k]

    def answer(self, query: str, platform: str = "amazon", top_k: int = 3) -> dict[str, Any]:
        hits = self.search(query, platform, top_k)
        if not hits:
            self._last_generation = "no_evidence_refusal"
            return {"answer": "未检索到可支撑该处理建议的内部规则，请补充异常信息并转人工审核。", "citations": [], "confidence": 0.0, "requires_human_review": True, "recommended_action": "create_manual_review", "source": "local_qdrant_rag_no_evidence"}
        generated, generation_mode = self._generate(query, hits)
        try:
            generated = GroundedOutput.model_validate(generated).model_dump()
        except Exception:
            generated = self._fallback(hits)
            generation_mode = "evidence_template"
        valid_ids = {hit["id"] for hit in hits}
        cited_ids = [value for value in generated.get("citation_ids", []) if value in valid_ids]
        if not cited_ids or generated.get("recommended_action") not in {"save_listing_draft", "create_manual_review"} or any(pattern in generated.get("answer", "") for pattern in UNSAFE_ANSWER_PATTERNS):
            generated = self._fallback(hits)
            cited_ids = generated["citation_ids"]
            generation_mode = "evidence_template"
        self._last_generation = generation_mode
        citations = [{key: value for key, value in hit.items() if key not in {"text", "document_id"}} | {"source": hit["document_id"]} for hit in hits if hit["id"] in cited_ids]
        answer = self._render_grounded_answer([hit for hit in hits if hit["id"] in cited_ids], generated["recommended_action"])
        return {"answer": answer, "citations": citations, "confidence": max(0.0, min(1.0, float(generated.get("confidence", 0.7)))), "requires_human_review": True, "recommended_action": str(generated.get("recommended_action", "save_listing_draft")), "source": f"local_qdrant_rag_{generation_mode}"}

    @staticmethod
    def _render_grounded_answer(hits: list[dict[str, Any]], action: str) -> str:
        evidence = "；".join(f"《{hit['title']}》{hit['heading']}：{hit['excerpt']}" for hit in hits)
        next_step = "建议创建人工审核任务。" if action == "create_manual_review" else "建议人工审核后仅保存Listing草稿。"
        return f"根据检索到的内部演示规则，{evidence}。{next_step}"

    def _generate(self, query: str, hits: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
        if self.generator:
            try:
                return self.generator(query, hits), "injected"
            except Exception:
                return self._fallback(hits), "evidence_template"
        if os.getenv("AMAZONOPS_RAG_GENERATION", "").lower() == "openai" and os.getenv("OPENAI_API_KEY"):
            try:
                return self._openai_generate(query, hits), "openai"
            except Exception:
                pass
        return self._fallback(hits), "evidence_template"

    def _openai_generate(self, query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
        from openai import OpenAI
        model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"), timeout=45, max_retries=0)
        evidence = [{"id": hit["id"], "title": hit["title"], "text": hit["excerpt"]} for hit in hits]
        instructions = "只基于给定证据回答运营异常，不得补充证据中未出现的规则。citation_ids只能使用给定ID。recommended_action只能是save_listing_draft或create_manual_review，且requires_human_review必须为true。"
        prompt = "问题：" + query + "\n证据：" + json.dumps(evidence, ensure_ascii=False)
        if os.getenv("OPENAI_API_MODE", "responses").lower() == "responses":
            response = client.responses.parse(model=model, instructions=instructions, input=prompt, text_format=GroundedOutput)
            parsed = response.output_parsed
        else:
            response = client.chat.completions.parse(model=model, messages=[{"role": "system", "content": instructions}, {"role": "user", "content": prompt}], response_format=GroundedOutput)
            parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("OpenAI returned no grounded RAG output")
        return parsed.model_dump() if isinstance(parsed, GroundedOutput) else GroundedOutput.model_validate(parsed).model_dump()

    @staticmethod
    def _fallback(hits: list[dict[str, Any]]) -> dict[str, Any]:
        evidence = hits[:2]
        return {"answer": "基于内部演示规则，建议先执行：" + "；".join(hit["excerpt"] for hit in evidence) + "。涉及后台写入前请人工审核，仅保存草稿。", "citation_ids": [hit["id"] for hit in evidence], "confidence": 0.78 if len(evidence) > 1 else 0.64, "requires_human_review": True, "recommended_action": "save_listing_draft"}
