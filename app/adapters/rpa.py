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

    def save_draft(self, task_id: str, advice: ListingAdvice) -> str:
        destination = self.artifacts / task_id
        destination.mkdir(parents=True, exist_ok=True)
        screenshot = destination / "seller-central-draft.png"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"{self.base_url}/simulator", wait_until="networkidle")
            page.fill("#sku", advice.sku)
            page.fill("#title", advice.suggested_title)
            page.fill("#bullets", "\n".join(advice.suggested_bullets))
            page.fill("#note", "; ".join(advice.issues) or "Listing review passed")
            page.click("#save-draft")
            page.wait_for_selector("#saved:not(.hidden)")
            page.screenshot(path=str(screenshot), full_page=True)
            browser.close()
        return str(screenshot.relative_to(self.artifacts.parent)).replace("\\", "/")
