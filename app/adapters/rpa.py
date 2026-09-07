from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from app.domain.models import ListingAdvice


class LocalSellerCentralRPA:
    def __init__(self, base_url: str, artifacts: Path) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Mock RPA base URL must use an HTTP loopback host")
        self.base_url, self.artifacts = base_url.rstrip("/"), artifacts

    def save_draft(self, task_id: str, advice: ListingAdvice) -> dict[str, str | dict[str, str]]:
        destination = self.artifacts / task_id
        destination.mkdir(parents=True, exist_ok=True)
        screenshot, trace = destination / "seller-central-draft.png", destination / "seller-central-draft.zip"
        expected = {"sku": advice.sku, "title": advice.suggested_title, "bullets": chr(10).join(advice.suggested_bullets)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            try:
                page.goto(f"{self.base_url}/simulator", wait_until="networkidle")
                page.fill("#sku", expected["sku"])
                page.fill("#title", expected["title"])
                page.fill("#bullets", expected["bullets"])
                page.fill("#note", "; ".join(advice.issues) or "Listing review passed")
                page.click("#save-draft")
                page.wait_for_selector("#saved:not(.hidden)")
                page.reload(wait_until="networkidle")
                page.wait_for_selector("#saved:not(.hidden)")
                actual = {field: page.locator(f"#{field}").input_value() for field in expected}
                if actual != expected:
                    raise RuntimeError(f"Persisted local draft verification failed: expected {expected!r}, got {actual!r}")
                page.screenshot(path=str(screenshot), full_page=True)
            finally:
                context.tracing.stop(path=str(trace))
                browser.close()
        prefix = self.artifacts.parent
        return {
            "screenshot": str(screenshot.relative_to(prefix)).replace(chr(92), "/"),
            "playwright_trace": str(trace.relative_to(prefix)).replace(chr(92), "/"),
            "saved_fields": expected,
        }
