from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.adapters.analyzer import Analyzer, DeterministicAnalyzer
from app.adapters.knowledge import KnowledgeAdvisor
from app.adapters.rpa import LocalSellerCentralRPA
from app.domain.models import ListingAdvice
from app.infrastructure.repository import Repository
from app.services.anomalies import evaluate_platform_rules, platform_rule_summary
from app.services.catalog import load_anomaly_labels, load_catalog, load_inventory_snapshots
from app.services.evaluation import evaluate_advice


class Workflow:
    """Inspection is safe offline; browser automation is reachable only after approval."""

    def __init__(self, repository: Repository, analyzer: Analyzer, rpa: LocalSellerCentralRPA, artifacts: Path, knowledge: KnowledgeAdvisor | None = None) -> None:
        self.repository, self.analyzer, self.rpa, self.artifacts = repository, analyzer, rpa, artifacts
        self.knowledge = knowledge or KnowledgeAdvisor()

    def create_and_inspect(self, idempotency_key: str, fail_once: bool = False, platform: str = "amazon") -> dict:
        task, created = self.repository.create_task(str(uuid4()), idempotency_key, fail_once, platform)
        return self.inspect(task["id"]) if created else task

    def inspect(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        if task["approval_status"] != "pending" or task["status"] not in {"pending", "inspecting", "failed"}:
            return task
        self.repository.update_task(task_id, "inspecting", error=None)
        try:
            products, inventory, orders = load_catalog()
            platform = task["platform"]
            snapshots = load_inventory_snapshots()
            if not self.repository.step_succeeded(task_id, "data_validation"):
                self.repository.save_products([product.model_dump() for product in products])
                self.repository.upsert_step(
                    task_id,
                    "data_validation",
                    "succeeded",
                    f"Validated {len(products)} products, {len(inventory)} inventory rows, {len(orders)} orders and {len(snapshots)} inventory snapshots",
                )
            advice = self.analyzer.analyze(products, inventory, orders)
            facts = DeterministicAnalyzer().analyze(products, inventory, orders)
            anomalies = evaluate_platform_rules(platform, products, inventory, orders, snapshots)
            target_sku = next((row["sku"] for row in anomalies if row["executable"]), None)
            if target_sku is None:
                raise RuntimeError("No executable Listing anomaly was detected")
            self.repository.save_anomalies(task_id, anomalies)
            self.repository.upsert_step(task_id, "operations_analysis", "succeeded", f"Generated {len(anomalies)} deterministic anomaly queue items; advice provider {self.analyzer.provider}")
            target = next(item for item in advice if item.sku == target_sku)
            rule_evidence = [row["detail"] for row in anomalies if row["sku"] == target_sku]
            recommendation = self.knowledge.recommend(target, platform, rule_evidence).model_dump()
            report = self._report(advice, facts, anomalies, len(snapshots), platform)
            report["recommendation"] = recommendation
            report["review"] = {"status": "pending_approval", "approval_required": recommendation["requires_human_review"], "approved_by": None}
            report["business_summary"] = self._business_summary(advice, "pending_approval")
            self.repository.save_analysis(task_id, report)
            self.repository.update_task(task_id, "pending_approval", report=report)
            self.repository.upsert_step(task_id, "knowledge_recommendation", "succeeded", f"Knowledge recommendation from {recommendation['source']} with {len(recommendation['citations'])} citations")
            self.repository.upsert_step(task_id, "human_review", "pending", "Awaiting explicit approval before local Listing draft automation")
        except Exception as error:
            self.repository.upsert_step(task_id, "operations_analysis", "failed", f"Inspection failed: {type(error).__name__}: {error}")
            self.repository.update_task(task_id, "failed", error=str(error), completed=True)
        return self.repository.get_task(task_id)

    def approve_and_execute(self, task_id: str, approved_by: str, anomaly_id: str | None = None) -> dict:
        task = self.repository.get_task(task_id)
        selected = self.repository.approve_and_select(task_id, approved_by, anomaly_id)
        report = task["report"] or self.repository.get_analysis(task_id)
        report["selected_anomaly"] = selected
        self.repository.update_task(task_id, "pending_approval", report=report)
        self.repository.upsert_step(task_id, "human_review", "succeeded", f"Approved by {approved_by}; selected {selected['sku']} {selected['anomaly_type']} for local draft automation")
        return self.execute(task_id)

    def retry(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        if task["status"] != "failed":
            raise ValueError("Only failed tasks can be retried")
        if task["approval_status"] == "pending":
            return self.inspect(task_id)
        self.repository.selected_anomaly(task_id)
        return self.execute(task_id)

    def execute(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        if task["approval_status"] != "approved":
            raise PermissionError("Browser automation is blocked until explicit approval")
        report = task["report"] or self.repository.get_analysis(task_id)
        selected = self.repository.selected_anomaly(task_id)
        self.repository.update_task(task_id, "running", error=None, increment=True, execution_started=True)
        try:
            current = self.repository.get_task(task_id)
            if current["fail_once"] and current["attempt"] == 1 and not self.repository.step_succeeded(task_id, "browser_form"):
                raise RuntimeError("Controlled first-attempt failure for retry demonstration")
            if not self.repository.step_succeeded(task_id, "browser_form"):
                target = next(ListingAdvice.model_validate(item) for item in report["listing_advice"] if item["sku"] == selected["sku"])
                evidence = self.rpa.save_draft(task_id, target)
                report["evidence"].update(evidence)
                self.repository.update_task(task_id, "running", report=report)
                self.repository.upsert_step(task_id, "browser_form", "succeeded", f"Saved and verified {target.sku} local simulator draft", evidence["screenshot"])
            if not self.repository.step_succeeded(task_id, "report_generation"):
                report["review"] = {"status": "approved_and_executed", "approval_required": True, "approved_by": self.repository.get_task(task_id)["approved_by"]}
                report["business_summary"] = self._business_summary([ListingAdvice.model_validate(item) for item in report["listing_advice"]], "executed")
                paths = self._write_reports(task_id, report)
                report["evidence"].update(paths)
                self.repository.upsert_step(task_id, "report_generation", "succeeded", "Wrote JSON and Markdown audit reports", paths["markdown_report"])
            self.repository.update_task(task_id, "succeeded", report=report, completed=True)
            self.repository.record_attempt(task_id, self.repository.get_task(task_id)["attempt"], "succeeded")
        except Exception as error:
            self._record_browser_failure(task_id, report, error)
            self.repository.update_task(task_id, "failed", error=str(error), report=report, completed=True)
            self.repository.record_attempt(task_id, self.repository.get_task(task_id)["attempt"], "failed", str(error))
        return self.repository.get_task(task_id)

    def _record_browser_failure(self, task_id: str, report: dict, error: Exception) -> None:
        evidence = report.setdefault("evidence", {})
        artifact = None
        for key, filename in (("screenshot", "seller-central-draft.png"), ("playwright_trace", "seller-central-draft.zip")):
            path = self.artifacts / task_id / filename
            if path.is_file():
                relative = str(path.relative_to(self.artifacts.parent)).replace(chr(92), "/")
                evidence[key] = relative
                artifact = relative
        self.repository.upsert_step(task_id, "browser_form", "failed", f"Browser automation failed: {type(error).__name__}: {error}", artifact)

    def _report(self, advice: list[ListingAdvice], facts: list[ListingAdvice], anomalies: list[dict], snapshot_count: int, platform: str) -> dict:
        by_type = {anomaly_type: sum(row["anomaly_type"] == anomaly_type for row in anomalies) for anomaly_type in ("listing", "inventory", "order")}
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.analyzer.mode,
            "provider": self.analyzer.provider,
            "data_source": "synthetic_batch_v2",
            "platform_rules": platform_rule_summary(platform),
            "listing_advice": [item.model_dump() for item in advice],
            "risk_summary": {
                "low_inventory": [item.sku for item in facts if item.inventory_risk],
                "order_attention": [item.sku for item in facts if item.order_risk],
            },
            "queue_summary": {
                "detected": len(anomalies),
                "by_type": by_type,
                "executable_listing": sum(bool(row["executable"]) for row in anomalies),
                "inventory_snapshots_used": snapshot_count,
            },
            "evaluation": evaluate_advice(facts, load_anomaly_labels()),
            "evidence": {},
        }

    @staticmethod
    def _business_summary(advice: list[ListingAdvice], state: str) -> dict:
        return {
            "inspection_state": state,
            "products_inspected": len(advice),
            "issues_found": sum(len(item.issues) for item in advice),
            "action": "save_listing_draft",
            "external_side_effect": "blocked_until_approval" if state == "pending_approval" else "local_simulator_draft_saved",
        }

    def _write_reports(self, task_id: str, report: dict) -> dict[str, str]:
        folder = self.artifacts / task_id
        folder.mkdir(parents=True, exist_ok=True)
        json_path, markdown_path = folder / "operations-report.json", folder / "operations-report.md"
        paths = {
            "json_report": str(json_path.relative_to(self.artifacts.parent)).replace(chr(92), "/"),
            "markdown_report": str(markdown_path.relative_to(self.artifacts.parent)).replace(chr(92), "/"),
        }
        report["evidence"].update(paths)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        recommendation = report["recommendation"]
        selected = report.get("selected_anomaly", {})
        lines = [
            "# AmazonOps AI 审计报告", "", "## 业务摘要",
            *(f"- {key}：{value}" for key, value in report["business_summary"].items()),
            "", "## 队列与评测",
            f"- 队列异常：{report['queue_summary']['detected']}",
            f"- 选中异常：{selected.get('sku', '未选择')}",
            f"- F1：{report['evaluation']['f1']}",
            "", "## 知识建议",
            f"- 回答：{recommendation['answer']}",
            f"- 置信度：{recommendation['confidence']}",
            f"- 推荐动作：{recommendation['recommended_action']}",
            "- 引用：" + "；".join(item["title"] for item in recommendation["citations"]),
            "", "## Listing 建议",
        ]
        for item in report["listing_advice"]:
            lines.extend([f"### {item['sku']}（评分 {item['score']}）", f"- 建议标题：{item['suggested_title']}", f"- 问题：{'；'.join(item['issues']) or '无'}", ""])
        markdown_path.write_text(chr(10).join(lines), encoding="utf-8")
        return paths
