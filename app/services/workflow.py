from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.adapters.analyzer import Analyzer
from app.adapters.rpa import LocalSellerCentralRPA
from app.domain.models import ListingAdvice
from app.infrastructure.repository import Repository
from app.services.catalog import load_catalog


class Workflow:
    CORE_STEPS = ("data_validation", "operations_analysis", "browser_form", "report_generation")

    def __init__(self, repository: Repository, analyzer: Analyzer, rpa: LocalSellerCentralRPA, artifacts: Path) -> None:
        self.repository, self.analyzer, self.rpa, self.artifacts = repository, analyzer, rpa, artifacts

    def create_and_run(self, idempotency_key: str, fail_once: bool = False) -> dict:
        task, created = self.repository.create_task(str(uuid4()), idempotency_key, fail_once)
        return self.run(task["id"]) if created and task["status"] == "pending" else task

    def retry(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        if task["status"] != "failed":
            raise ValueError("Only failed tasks can be retried")
        return self.run(task_id)

    def run(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        self.repository.update_task(task_id, "running", error=None, increment=True)
        try:
            products, inventory, orders = load_catalog()
            if not self.repository.step_succeeded(task_id, "data_validation"):
                self.repository.save_products([product.model_dump() for product in products])
                self.repository.upsert_step(task_id, "data_validation", "succeeded", f"Validated {len(products)} products, {len(inventory)} inventory rows and {len(orders)} orders")
            report: dict
            if not self.repository.step_succeeded(task_id, "operations_analysis"):
                try:
                    advice = self.analyzer.analyze(products, inventory, orders)
                except Exception as error:
                    self.repository.upsert_step(task_id, "operations_analysis", "failed", f"Analysis failed: {error}")
                    raise
                report = self._report(advice)
                self.repository.save_analysis(task_id, report)
                self.repository.upsert_step(task_id, "operations_analysis", "succeeded", f"Generated operations advice with provider {self.analyzer.provider}")
            else:
                report = self.repository.get_task(task_id)["report"] or self.repository.get_analysis(task_id)
            current = self.repository.get_task(task_id)
            if current["fail_once"] and current["attempt"] == 1 and not self.repository.step_succeeded(task_id, "browser_form"):
                self.repository.upsert_step(task_id, "browser_form", "failed", "Controlled first-attempt failure for retry demonstration")
                raise RuntimeError("Controlled first-attempt failure for retry demonstration")
            if not self.repository.step_succeeded(task_id, "browser_form"):
                target = next(ListingAdvice.model_validate(item) for item in report["listing_advice"] if item["sku"] == "MAT-002")
                screenshot = self.rpa.save_draft(task_id, target)
                report["evidence"]["screenshot"] = screenshot
                self.repository.update_task(task_id, "running", report=report)
                self.repository.upsert_step(task_id, "browser_form", "succeeded", "Saved optimized listing as local simulator draft", screenshot)
            if not self.repository.step_succeeded(task_id, "report_generation"):
                paths = self._write_reports(task_id, report)
                report["evidence"].update(paths)
                self.repository.upsert_step(task_id, "report_generation", "succeeded", "Wrote JSON and Markdown reports", paths["markdown_report"])
            self.repository.update_task(task_id, "succeeded", report=report)
            self.repository.record_attempt(task_id, self.repository.get_task(task_id)["attempt"], "succeeded")
        except Exception as error:
            self.repository.update_task(task_id, "failed", error=str(error))
            self.repository.record_attempt(task_id, self.repository.get_task(task_id)["attempt"], "failed", str(error))
        return self.repository.get_task(task_id)

    def _report(self, advice: list) -> dict:
        return {"generated_at":datetime.now(timezone.utc).isoformat(),"mode":self.analyzer.mode,"provider":self.analyzer.provider,"data_source":"fixture_demo","listing_advice":[item.model_dump() for item in advice],"risk_summary":{"low_inventory":[item.sku for item in advice if item.inventory_risk],"order_attention":[item.sku for item in advice if item.order_risk]},"evidence":{}}

    def _write_reports(self, task_id: str, report: dict) -> dict[str, str]:
        folder = self.artifacts / task_id
        folder.mkdir(parents=True, exist_ok=True)
        json_path, markdown_path = folder / "operations-report.json", folder / "operations-report.md"
        paths = {"json_report":str(json_path.relative_to(self.artifacts.parent)).replace("\\", "/"),"markdown_report":str(markdown_path.relative_to(self.artifacts.parent)).replace("\\", "/")}
        report["evidence"].update(paths)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# AmazonOps AI 运营报告", "", "## Listing 建议", ""]
        for item in report["listing_advice"]:
            lines.extend([f"### {item['sku']}（评分 {item['score']}）", f"- 建议标题：{item['suggested_title']}", f"- 问题：{'；'.join(item['issues']) or '无'}", ""])
        lines.extend(["## 风险", f"- 低库存：{', '.join(report['risk_summary']['low_inventory']) or '无'}", f"- 订单关注：{', '.join(report['risk_summary']['order_attention']) or '无'}"])
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
        return paths
