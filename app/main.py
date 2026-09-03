from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.adapters.amazon_sp_api import AmazonSPAPIAdapter
from app.adapters.analyzer import Analyzer, create_analyzer, load_local_env
from app.adapters.rpa import LocalSellerCentralRPA
from app.infrastructure.repository import Repository
from app.services.catalog import summary
from app.services.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
DB_PATH = ROOT / "data" / "amazon_ops.db"

class DemoRequest(BaseModel):
    idempotency_key: str = Field(min_length=3)
    fail_once: bool = False


class AmazonPreviewRequest(BaseModel):
    lookback_days: int = Field(default=7, ge=1, le=30)


def create_app(
    database: Path = DB_PATH,
    artifacts: Path = ARTIFACTS,
    base_url: str | None = None,
    analyzer: Analyzer | None = None,
    amazon: AmazonSPAPIAdapter | None = None,
) -> FastAPI:
    load_local_env(ROOT / ".env.local")
    app = FastAPI(title="AmazonOps AI", version="0.2.0", description="Amazon operations inspection and RPA draft assistant")
    templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
    repo = Repository(database)
    runtime_base_url = base_url or os.getenv("AMAZONOPS_BASE_URL", "http://127.0.0.1:8000")
    analyzer = analyzer or create_analyzer()
    amazon = amazon or AmazonSPAPIAdapter()
    app.state.workflow = Workflow(repo, analyzer, LocalSellerCentralRPA(runtime_base_url, artifacts), artifacts)
    app.state.repo = repo
    app.state.amazon = amazon
    artifacts.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=str(artifacts)), name="artifacts")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "mode": analyzer.mode,
            "provider": analyzer.provider,
            "external_credentials_required": analyzer.mode == "openai",
            "amazon_integration": amazon.status(),
        }

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request, "index.html", {"catalog": summary(), "tasks": repo.list_tasks(), "mode": analyzer.mode, "amazon": amazon.status()})

    @app.get("/simulator", response_class=HTMLResponse)
    def simulator(request: Request): return templates.TemplateResponse(request, "simulator.html", {})

    @app.get("/api/catalog")
    def catalog() -> dict: return summary()

    @app.get("/api/integrations/amazon/status")
    def amazon_status() -> dict:
        return amazon.status()

    @app.post("/api/integrations/amazon/preview")
    def amazon_preview(payload: AmazonPreviewRequest) -> dict:
        try:
            return amazon.preview(payload.lookback_days)
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except Exception as error:
            raise HTTPException(502, f"Amazon SP-API preview failed: {type(error).__name__}")

    @app.post("/api/demo/run")
    def run_demo(payload: DemoRequest) -> dict: return app.state.workflow.create_and_run(payload.idempotency_key, payload.fail_once)

    @app.get("/api/tasks")
    def tasks() -> list[dict]: return repo.list_tasks()

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict:
        try: return repo.get_task(task_id)
        except KeyError: raise HTTPException(404, "Task not found")

    @app.post("/api/tasks/{task_id}/retry")
    def retry(task_id: str) -> dict:
        try: return app.state.workflow.retry(task_id)
        except KeyError: raise HTTPException(404, "Task not found")
        except ValueError as error: raise HTTPException(409, str(error))

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str) -> dict:
        try:
            task_value = repo.get_task(task_id)
            if task_value["status"] != "succeeded" or task_value["report"] is None:
                raise HTTPException(409, "Report is not available until the task succeeds")
            return task_value["report"]
        except KeyError: raise HTTPException(404, "Task not found")
    return app

app = create_app()
