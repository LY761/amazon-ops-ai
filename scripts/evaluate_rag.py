from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.rag import PlatformRuleRAG
from qdrant_client import QdrantClient


def main() -> None:
    dataset = json.loads((ROOT / "data" / "rag_evaluation.json").read_text(encoding="utf-8"))
    rag = PlatformRuleRAG(client=QdrantClient(":memory:"), vector_store_label="qdrant_in_memory_evaluation")
    recall_at_1 = recall_at_3 = recall_at_5 = mrr = grounded_citation_hit_rate = 0.0
    for row in dataset["retrieval_cases"]:
        hits = rag.search(row["query"], row["platform"], 5)
        ranked = [(hit["document_id"], hit["heading"]) for hit in hits]
        expected = (row["expected_document"], row["expected_heading"])
        if expected in ranked:
            rank = ranked.index(expected) + 1
            recall_at_1 += rank <= 1
            recall_at_3 += rank <= 3
            recall_at_5 += rank <= 5
            mrr += 1 / rank
        answer = rag.answer(row["query"], row["platform"], 3)
        cited = {(citation["source"], citation.get("heading")) for citation in answer["citations"]}
        grounded_citation_hit_rate += expected in cited
    refusals = sum(not rag.answer(row["query"], row["platform"], 3)["citations"] for row in dataset["no_evidence_cases"])
    count = len(dataset["retrieval_cases"])
    report = {
        "dataset_size": count,
        "recall_at_1": round(recall_at_1 / count, 4),
        "recall_at_3": round(recall_at_3 / count, 4),
        "recall_at_5": round(recall_at_5 / count, 4),
        "mrr": round(mrr / count, 4),
        "grounded_citation_hit_rate": round(grounded_citation_hit_rate / count, 4),
        "no_evidence_cases": len(dataset["no_evidence_cases"]),
        "no_evidence_refusal_rate": round(refusals / len(dataset["no_evidence_cases"]), 4),
        "rag": rag.status(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    rag.close()


if __name__ == "__main__":
    main()
