"""Runs both offline demo paths against a locally running AmazonOps AI server."""
from __future__ import annotations

import argparse
import json
import time
from uuid import uuid4

import httpx


def wait_for_server(base_url: str) -> None:
    for _ in range(30):
        try:
            if httpx.get(f"{base_url}/health", timeout=1).is_success: return
        except httpx.HTTPError: pass
        time.sleep(0.5)
    raise RuntimeError("Local service did not become ready")


def run(base_url: str) -> None:
    wait_for_server(base_url)
    normal = httpx.post(f"{base_url}/api/demo/run", json={"idempotency_key":f"demo-success-{uuid4()}"}, timeout=90).json()
    assert normal["status"] == "succeeded", normal
    failed = httpx.post(f"{base_url}/api/demo/run", json={"idempotency_key":f"demo-retry-{uuid4()}", "fail_once":True}, timeout=90).json()
    assert failed["status"] == "failed", failed
    retried = httpx.post(f"{base_url}/api/tasks/{failed['id']}/retry", timeout=90).json()
    assert retried["status"] == "succeeded", retried
    print(json.dumps({"normal_task":normal["id"],"normal_status":normal["status"],"retry_task":retried["id"],"retry_status":retried["status"],"attempts":retried["attempt"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    run(parser.parse_args().base_url)
