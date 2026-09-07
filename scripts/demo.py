"""Run the complete offline AmazonOps AI business loop against a local server."""

from __future__ import annotations

import argparse
import json
import time
from uuid import uuid4

import httpx


def wait_for_server(base_url: str) -> None:
    for _ in range(30):
        try:
            if httpx.get(f"{base_url}/health", timeout=1).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("Local service did not become ready")


def inspect_then_approve(base_url: str, key: str, target_sku: str, fail_once: bool = False) -> dict:
    inspection = httpx.post(
        f"{base_url}/api/demo/run",
        json={"idempotency_key": key, "fail_once": fail_once},
        timeout=90,
    ).json()
    assert inspection["status"] == "pending_approval", inspection
    selected = next(
        row for row in inspection["anomalies"]
        if row["sku"] == target_sku and row["anomaly_type"] == "listing"
    )
    return httpx.post(
        f"{base_url}/api/tasks/{inspection['id']}/approve",
        json={"approved_by": "demo-script", "anomaly_id": selected["id"]},
        timeout=90,
    ).json()


def run(base_url: str) -> None:
    wait_for_server(base_url)
    completed = inspect_then_approve(base_url, f"demo-success-{uuid4()}", "SKU-008")
    assert completed["status"] == "succeeded", completed
    failed = inspect_then_approve(base_url, f"demo-retry-{uuid4()}", "SKU-007", True)
    assert failed["status"] == "failed", failed
    retried = httpx.post(f"{base_url}/api/tasks/{failed['id']}/retry", timeout=90).json()
    assert retried["status"] == "succeeded", retried
    print(json.dumps({
        "completed_task": completed["id"],
        "selected_sku": completed["report"]["selected_anomaly"]["sku"],
        "queue_items": completed["report"]["queue_summary"]["detected"],
        "evaluation": completed["report"]["evaluation"],
        "knowledge_citations": len(completed["report"]["recommendation"]["citations"]),
        "artifacts": completed["report"]["evidence"],
        "retry_task": retried["id"],
        "retry_count": retried["retry_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    run(parser.parse_args().base_url)
