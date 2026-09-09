import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import main
from app.adapters.analyzer import DeterministicAnalyzer
from app.adapters.knowledge import KnowledgeAdvisor
from app.adapters.rpa import LocalSellerCentralRPA
from app.main import create_app


class StubRPA:
    def __init__(self, artifacts):
        self.artifacts, self.calls, self.skus = artifacts, 0, []

    def save_draft(self, task_id, advice):
        self.calls += 1
        self.skus.append(advice.sku)
        folder = self.artifacts / task_id
        folder.mkdir(parents=True, exist_ok=True)
        image, trace = folder / "seller-central-draft.png", folder / "seller-central-draft.zip"
        image.write_bytes(b"test-stub")
        trace.write_bytes(b"trace")
        return {
            "screenshot": f"artifacts/{task_id}/seller-central-draft.png",
            "playwright_trace": f"artifacts/{task_id}/seller-central-draft.zip",
            "saved_fields": {
                "sku": advice.sku,
                "title": advice.suggested_title,
                "bullets": chr(10).join(advice.suggested_bullets),
            },
        }


class FailingRPA:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def save_draft(self, task_id, advice):
        folder = self.artifacts / task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "seller-central-draft.zip").write_bytes(b"failure-trace")
        raise RuntimeError("selector disappeared")


class FlakyAnalyzer:
    mode, provider = "openai", "flaky-test"

    def __init__(self):
        self.calls = 0

    def analyze(self, products, inventory, orders):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return DeterministicAnalyzer().analyze(products, inventory, orders)


class RephrasingAnalyzer:
    mode, provider = "openai", "rephrasing-test"

    def analyze(self, products, inventory, orders):
        return [
            row.model_copy(update={"issues": ["AI used different wording"] if row.issues else []})
            for row in DeterministicAnalyzer().analyze(products, inventory, orders)
        ]


class StubIntegrations:
    def __init__(self): self.notifications = []
    def status(self): return {"sources": {}, "feishu": {"mode": "fixture_demo", "configured": False}}
    def notify(self, task):
        self.notifications.append(task["id"])
        return {"delivery": {"code": 0}, "payload": {}, "attempts": 1, "mode": "fixture_demo"}


class StubRuleRAG:
    def status(self):
        return {"vector_store": "qdrant_local_persistent", "embedding_model": "test", "reranker": "test", "documents": 4, "chunks": 9, "chunk_strategy": "test", "retrieval": "test", "generation_configured": "test", "last_generation": None, "initialized": True}
    def search(self, query, platform, top_k=5):
        return [{"id": "test:1", "document_id": f"{platform}_listing", "title": f"{platform} Listing运营规则SOP", "excerpt": "测试规则证据", "sparse_rank": 1, "dense_rank": 1, "rrf_score": 0.032787, "rerank_score": 0.99}][:top_k]
    def answer(self, query, platform="amazon", top_k=3):
        return {"answer": "应先生成草稿并人工审核。", "citations": [{"source": f"{platform}_listing", "title": f"{platform} Listing运营规则SOP", "excerpt": "规则命中后只保存草稿。"}], "confidence": 0.86, "requires_human_review": True, "recommended_action": "save_listing_draft", "source": "local_qdrant_rag"}
    def close(self):
        pass


def make_client(tmp_path, knowledge=None, analyzer=None, rpa=None, shadowbot_launcher=None, integrations=None):
    integrations = integrations or StubIntegrations()
    app = create_app(
        database=tmp_path / "db.sqlite",
        artifacts=tmp_path / "artifacts",
        analyzer=analyzer or DeterministicAnalyzer(),
        knowledge=knowledge or KnowledgeAdvisor(rag_url="", local_rag=StubRuleRAG()),
        integrations=integrations,
        shadowbot_launcher=shadowbot_launcher or (lambda: {"mode": "test"}),
    )
    app.state.workflow.rpa = rpa or StubRPA(tmp_path / "artifacts")
    return TestClient(app)


def test_inspection_creates_recommendation_and_blocks_rpa_until_approval(tmp_path):
    client = make_client(tmp_path)
    first = client.post("/api/demo/run", json={"idempotency_key": "review-key"}).json()
    second = client.post("/api/demo/run", json={"idempotency_key": "review-key"}).json()
    assert first["id"] == second["id"] and first["status"] == "pending_approval"
    assert first["approval_status"] == "pending" and client.app.state.workflow.rpa.calls == 0
    assert len(first["anomalies"]) == 24
    assert first["report"]["evaluation"]["f1"] == 1.0
    assert first["report"]["queue_summary"]["inventory_snapshots_used"] == 3000
    assert {step["name"] for step in first["steps"]} == {"data_validation", "operations_analysis", "knowledge_recommendation", "human_review"}
    recommendation = first["report"]["recommendation"]
    assert set(recommendation) == {"answer", "citations", "confidence", "requires_human_review", "recommended_action", "source"}
    assert recommendation["citations"] and recommendation["requires_human_review"] is True
    assert first["report"]["business_summary"]["external_side_effect"] == "blocked_until_approval"
    assert client.get(f"/api/tasks/{first['id']}/report").status_code == 409


def test_explicit_approval_executes_draft_and_writes_audit_artifacts(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "approve-key"}).json()
    done = client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "operator-a"}).json()
    assert done["status"] == "succeeded" and done["approval_status"] == "approved"
    assert done["report"]["review"] == {"status": "approved_and_executed", "approval_required": True, "approved_by": "operator-a"}
    assert done["duration_ms"] is not None and done["retry_count"] == 0
    assert client.app.state.workflow.rpa.calls == 1
    for key in ("screenshot", "playwright_trace", "json_report", "markdown_report"):
        assert (tmp_path / done["report"]["evidence"][key]).is_file()
    assert done["report"]["evidence"]["saved_fields"]["sku"] == "SKU-001"
    saved = json.loads((tmp_path / done["report"]["evidence"]["json_report"]).read_text(encoding="utf-8"))
    assert saved["recommendation"]["citations"][0]["title"] == "amazon Listing运营规则SOP"
    assert next(step for step in done["steps"] if step["name"] == "feishu_notification")["status"] == "succeeded"

    with client.app.state.repo.connect() as db:
        db.execute("UPDATE tasks SET created_at='2020-01-01 00:00:00' WHERE id=?", (task["id"],))
    assert client.get("/api/metrics").json()["avg_handling_ms"] == done["duration_ms"]


def test_retry_reuses_inspection_after_approved_failure(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "retry-key", "fail_once": True}).json()
    failed = client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "operator-a"}).json()
    assert failed["status"] == "failed" and client.app.state.workflow.rpa.calls == 0
    retried = client.post(f"/api/tasks/{task['id']}/retry").json()
    assert retried["status"] == "succeeded" and retried["attempt"] == 2 and retried["retry_count"] == 1
    assert client.app.state.workflow.rpa.calls == 1
    assert retried["attempt_history"][0]["status"] == "failed" and retried["attempt_history"][1]["status"] == "succeeded"


def test_unapproved_inspection_failure_can_retry(tmp_path):
    analyzer = FlakyAnalyzer()
    client = make_client(tmp_path, analyzer=analyzer)
    failed = client.post("/api/demo/run", json={"idempotency_key": "inspection-retry"}).json()
    assert failed["status"] == "failed" and failed["approval_status"] == "pending"
    retried = client.post(f"/api/tasks/{failed['id']}/retry").json()
    assert retried["status"] == "pending_approval" and analyzer.calls == 2


def test_approval_is_single_use_and_needs_pending_state(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "single-approval"}).json()
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    assert client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "a1"}).status_code == 200
    assert client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "a2"}).status_code == 409


def test_optional_external_rag_falls_back_to_local_without_network(tmp_path):
    client = make_client(tmp_path, KnowledgeAdvisor(rag_url="http://127.0.0.1:1", timeout=0.01, local_rag=StubRuleRAG()))
    task = client.post("/api/demo/run", json={"idempotency_key": "local-rag"}).json()
    assert task["report"]["recommendation"]["source"] == "local_qdrant_rag"


def test_catalog_health_and_rpa_loopback_guard(tmp_path):
    client = make_client(tmp_path)
    health = client.get("/health").json()
    assert health["mode"] == "mock" and health["rag"]["vector_store"] == "qdrant_local_persistent"
    assert client.get("/api/catalog").json()["summary"]["product_count"] == 100
    home = client.get("/")
    assert home.status_code == 200 and 'executor:"shadowbot"' in home.text
    with pytest.raises(ValueError, match="loopback"):
        LocalSellerCentralRPA("https://sellercentral.amazon.com", tmp_path)


def test_rag_status_and_search_expose_retrieval_trace(tmp_path):
    client = make_client(tmp_path)
    status = client.get("/api/rag/status").json()
    result = client.post("/api/rag/search", json={"query": "标题缺少规格怎么办", "platform": "amazon", "top_k": 3}).json()
    assert status["vector_store"] == "qdrant_local_persistent"
    assert result["hits"][0]["sparse_rank"] == 1 and result["hits"][0]["rerank_score"] == 0.99


def test_api_tests_stay_deterministic_when_environment_selects_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("AMAZONOPS_ANALYZER", "openai")
    monkeypatch.setattr(main, "create_analyzer", lambda: pytest.fail("injected analyzer must prevent provider construction"))
    app = create_app(database=tmp_path / "db.sqlite", artifacts=tmp_path / "artifacts", analyzer=DeterministicAnalyzer())
    assert app.state.workflow.analyzer.provider == "deterministic"


def test_queue_selects_requested_listing_and_rejects_non_executable_items(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "queue-key"}).json()
    queue = client.get(f"/api/tasks/{task['id']}/anomalies").json()
    assert len(queue) == 24
    assert next(row for row in queue if row["anomaly_type"] == "inventory")["detail"].endswith("30天库存变化：-29")
    chosen = next(row for row in queue if row["sku"] == "SKU-008" and row["anomaly_type"] == "listing")
    done = client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "operator-a", "anomaly_id": chosen["id"]}).json()
    assert done["status"] == "succeeded" and done["selected_anomaly_id"] == chosen["id"]
    assert client.app.state.workflow.rpa.skus == ["SKU-008"]
    assert done["report"]["selected_anomaly"]["sku"] == "SKU-008"

    task = client.post("/api/demo/run", json={"idempotency_key": "queue-reject"}).json()
    blocked = next(row for row in task["anomalies"] if row["anomaly_type"] == "inventory")
    assert client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "operator-a", "anomaly_id": blocked["id"]}).status_code == 409


def test_ai_wording_does_not_control_deterministic_anomaly_queue(tmp_path):
    client = make_client(tmp_path, analyzer=RephrasingAnalyzer())
    task = client.post("/api/demo/run", json={"idempotency_key": "rephrased-ai"}).json()
    assert task["status"] == "pending_approval"
    assert len(task["anomalies"]) == 24
    assert task["report"]["queue_summary"]["by_type"] == {"listing": 8, "inventory": 8, "order": 8}


def test_platform_rules_are_saved_and_rag_only_explains_the_rule_result(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "shopee-rules", "platform": "shopee"}).json()
    assert task["platform"] == "shopee"
    assert task["report"]["platform_rules"] == {
        "platform": "shopee",
        "version": "demo-2026-09",
        "policy": {"title_min": 25, "bullet_min": 3, "pending_order_days": 2},
        "decision_layer": "deterministic_rules",
        "rag_role": "retrieve_and_explain_sop",
    }
    assert task["report"]["recommendation"]["citations"][0]["title"] == "shopee Listing运营规则SOP"


def test_atomic_approval_keeps_one_selected_anomaly(tmp_path):
    client = make_client(tmp_path)
    task = client.post("/api/demo/run", json={"idempotency_key": "concurrent-approval"}).json()
    candidates = [row for row in task["anomalies"] if row["executable"]][:2]
    repository = client.app.state.repo

    def approve(index):
        try:
            return repository.approve_and_select(task["id"], f"operator-{index}", candidates[index]["id"])
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, (0, 1)))
    winner = next(row for row in results if row is not None)
    assert sum(row is not None for row in results) == 1
    saved = repository.get_task(task["id"])
    assert saved["selected_anomaly_id"] == winner["id"]
    assert next(row for row in saved["anomalies"] if row["selected"])["id"] == winner["id"]


def test_rpa_failure_records_failed_step_and_trace(tmp_path):
    rpa = FailingRPA(tmp_path / "artifacts")
    client = make_client(tmp_path, rpa=rpa)
    task = client.post("/api/demo/run", json={"idempotency_key": "rpa-failure"}).json()
    failed = client.post(f"/api/tasks/{task['id']}/approve", json={"approved_by": "operator-a"}).json()
    assert failed["status"] == "failed"
    browser_step = next(step for step in failed["steps"] if step["name"] == "browser_form")
    assert browser_step["status"] == "failed"
    assert browser_step["artifact_path"] == failed["report"]["evidence"]["playwright_trace"]
    assert (tmp_path / browser_step["artifact_path"]).is_file()
