import json

import pytest

from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.adapters.analyzer import DeterministicAnalyzer
from app.adapters.rpa import LocalSellerCentralRPA
from app.services.workflow import Workflow


class StubRPA:
    def __init__(self, artifacts): self.artifacts = artifacts
    def save_draft(self, task_id, advice):
        folder = self.artifacts / task_id; folder.mkdir(parents=True, exist_ok=True)
        image = folder / "seller-central-draft.png"; image.write_bytes(b"not-a-real-image-test-stub")
        return f"artifacts/{task_id}/seller-central-draft.png"


def make_client(tmp_path):
    app = create_app(database=tmp_path / "db.sqlite", artifacts=tmp_path / "artifacts", analyzer=DeterministicAnalyzer())
    app.state.workflow.rpa = StubRPA(tmp_path / "artifacts")
    return TestClient(app)


class CountingAnalyzer(DeterministicAnalyzer):
    def __init__(self): self.calls = 0
    def analyze(self, products, inventory, orders):
        self.calls += 1
        return super().analyze(products, inventory, orders)


class CountingRPA(StubRPA):
    def __init__(self, artifacts): super().__init__(artifacts); self.calls = 0
    def save_draft(self, task_id, advice):
        self.calls += 1
        return super().save_draft(task_id, advice)


class FailingAnalyzer(DeterministicAnalyzer):
    provider = "failing-test"
    def analyze(self, products, inventory, orders): raise RuntimeError("analysis test failure")


def test_catalog_health_and_idempotent_success(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/health").json()["mode"] == "mock"
    catalog = client.get("/api/catalog").json()
    assert catalog["summary"]["product_count"] == 3
    first = client.post("/api/demo/run", json={"idempotency_key":"same-key"}).json()
    second = client.post("/api/demo/run", json={"idempotency_key":"same-key"}).json()
    assert first["id"] == second["id"]
    assert first["status"] == "succeeded"
    assert {step["name"] for step in first["steps"]} == {"data_validation","operations_analysis","browser_form","report_generation"}
    assert next(step for step in first["steps"] if step["name"] == "operations_analysis")["detail"] == "Generated operations advice with provider deterministic"
    report = client.get(f"/api/tasks/{first['id']}/report").json()
    assert report["listing_advice"] and report["evidence"]["json_report"]
    for path in report["evidence"].values():
        assert (tmp_path / path).is_file()
    saved_report = json.loads((tmp_path / report["evidence"]["json_report"]).read_text(encoding="utf-8"))
    assert saved_report["evidence"] == report["evidence"]
    assert client.post("/api/demo/run", json={"idempotency_key":"x"}).status_code == 422
    page = client.get("/")
    assert page.status_code == 200 and "查看详情" in page.text and "showTask" in page.text


def test_controlled_failure_retries_without_repeating_completed_steps(tmp_path):
    client = make_client(tmp_path)
    analyzer = CountingAnalyzer()
    client.app.state.workflow.analyzer = analyzer
    failed = client.post("/api/demo/run", json={"idempotency_key":"fail-key","fail_once":True}).json()
    assert failed["status"] == "failed"
    retried = client.post(f"/api/tasks/{failed['id']}/retry").json()
    assert retried["status"] == "succeeded"
    assert retried["attempt"] == 2
    assert all(step["status"] == "succeeded" for step in retried["steps"])
    assert analyzer.calls == 1
    assert client.app.state.repo.product_count() == 3
    assert failed["attempt_history"][0]["error"] == "Controlled first-attempt failure for retry demonstration"
    assert retried["attempt_history"][0]["error"] == "Controlled first-attempt failure for retry demonstration"
    assert retried["attempt_history"][1]["status"] == "succeeded"


def test_report_failure_retry_keeps_screenshot_and_does_not_repeat_rpa(tmp_path):
    client = make_client(tmp_path)
    rpa = CountingRPA(tmp_path / "artifacts")
    client.app.state.workflow.rpa = rpa
    original = client.app.state.workflow._write_reports
    failures = [True]
    def fail_once(*args):
        if failures: failures.pop(); raise RuntimeError("simulated report write failure")
        return original(*args)
    client.app.state.workflow._write_reports = fail_once
    failed = client.post("/api/demo/run", json={"idempotency_key":"report-failure"}).json()
    assert failed["status"] == "failed"
    assert failed["report"]["evidence"]["screenshot"]
    assert client.get(f"/api/tasks/{failed['id']}/report").status_code == 409
    retried = client.post(f"/api/tasks/{failed['id']}/retry").json()
    assert retried["status"] == "succeeded" and rpa.calls == 1
    assert retried["report"]["evidence"]["screenshot"] == failed["report"]["evidence"]["screenshot"]


def test_report_is_unavailable_while_task_is_running(tmp_path):
    client = make_client(tmp_path)
    task, _ = client.app.state.repo.create_task("running-task", "running-key", False)
    client.app.state.repo.update_task(task["id"], "running")
    response = client.get(f"/api/tasks/{task['id']}/report")
    assert response.status_code == 409
    assert response.json()["detail"] == "Report is not available until the task succeeds"


def test_rpa_rejects_external_base_url(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        LocalSellerCentralRPA("https://sellercentral.amazon.com", tmp_path)


def test_api_tests_stay_deterministic_when_environment_selects_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("AMAZONOPS_ANALYZER", "openai")
    monkeypatch.setattr(main, "create_analyzer", lambda: pytest.fail("injected analyzer must prevent provider construction"))
    app = create_app(database=tmp_path / "db.sqlite", artifacts=tmp_path / "artifacts", analyzer=DeterministicAnalyzer())
    assert app.state.workflow.analyzer.provider == "deterministic"


def test_analysis_failure_records_failed_step(tmp_path):
    app = create_app(database=tmp_path / "db.sqlite", artifacts=tmp_path / "artifacts", analyzer=FailingAnalyzer())
    app.state.workflow.rpa = StubRPA(tmp_path / "artifacts")
    task = TestClient(app).post("/api/demo/run", json={"idempotency_key":"analysis-failure"}).json()
    step = next(item for item in task["steps"] if item["name"] == "operations_analysis")
    assert task["status"] == "failed" and step["status"] == "failed"
    assert step["detail"] == "Analysis failed: analysis test failure"
