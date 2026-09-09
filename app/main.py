from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Callable, Literal
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from app.adapters.amazon_sp_api import AmazonSPAPIAdapter
from app.adapters.analyzer import Analyzer, create_analyzer, load_local_env
from app.adapters.knowledge import KnowledgeAdvisor
from app.adapters.rpa import LocalSellerCentralRPA
from app.adapters.http_integrations import LocalIntegrationGateway, RESOURCE_MODELS, SOURCES
from app.infrastructure.repository import Repository
from app.services.catalog import summary
from app.services.workflow import Workflow
from app.services import shadowbot
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"; DB_PATH = ROOT / "data" / "amazon_ops.db"
class DemoRequest(BaseModel):
    idempotency_key: str = Field(min_length=3)
    fail_once: bool = False
    platform: Literal["amazon", "shopee", "tiktok_shop"] = "amazon"
class ApprovalRequest(BaseModel):
    executor: Literal['playwright', 'shadowbot'] = 'playwright'
    approved_by: str = Field(default="demo-operator", min_length=2, max_length=80)
    anomaly_id: str | None = None
class AmazonPreviewRequest(BaseModel):
    lookback_days: int = Field(default=7, ge=1, le=30)
class SyncRequest(BaseModel):
    source: str
    resource: str
    idempotency_key: str = Field(min_length=3, max_length=100)
    failures_before_success: int = Field(default=0, ge=0, le=2)
class RAGSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    platform: Literal["amazon", "shopee", "tiktok_shop"] = "amazon"
    top_k: int = Field(default=5, ge=1, le=10)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    knowledge = getattr(getattr(app.state, "workflow", None), "knowledge", None)
    if hasattr(knowledge, "close"):
        knowledge.close()
def create_app(database: Path = DB_PATH, artifacts: Path = ARTIFACTS, base_url: str | None = None, analyzer: Analyzer | None = None, amazon: AmazonSPAPIAdapter | None = None, knowledge: KnowledgeAdvisor | None = None, integrations: LocalIntegrationGateway | None = None, shadowbot_launcher: Callable[[], dict] | None = None) -> FastAPI:
    load_local_env(ROOT / ".env.local")
    app = FastAPI(title="AmazonOps AI", version="0.4.0", description="Approval-gated Amazon operations inspection, grounded RAG and RPA draft assistant", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
    repo = Repository(database); runtime_base_url = base_url or os.getenv("AMAZONOPS_BASE_URL", "http://127.0.0.1:8000")
    analyzer = analyzer or create_analyzer(); amazon = amazon or AmazonSPAPIAdapter()
    integration_key = os.getenv("AMAZONOPS_INTEGRATION_KEY", "local-demo-only")
    source_endpoints = {source: os.getenv(f"{source.upper()}_API_BASE_URL", "") for source in SOURCES}
    source_tokens = {source: os.getenv(f"{source.upper()}_API_TOKEN", "") for source in SOURCES}
    integrations = integrations or LocalIntegrationGateway(runtime_base_url, integration_key, source_endpoints=source_endpoints, source_tokens=source_tokens, feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""))
    app.state.workflow = Workflow(repo, analyzer, LocalSellerCentralRPA(runtime_base_url, artifacts), artifacts, knowledge); app.state.repo = repo; app.state.amazon = amazon; app.state.integrations = integrations; app.state.integration_key = integration_key; app.state.mock_attempts = {}; app.state.shadowbot_launcher = shadowbot_launcher or shadowbot.launch
    artifacts.mkdir(parents=True, exist_ok=True); app.mount("/artifacts", StaticFiles(directory=str(artifacts)), name="artifacts")
    @app.get("/health")
    def health() -> dict: return {"status":"ok","mode":analyzer.mode,"provider":analyzer.provider,"external_credentials_required":analyzer.mode=="openai","amazon_integration":amazon.status(),"rag":app.state.workflow.knowledge.status()}
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request): return templates.TemplateResponse(request, "index.html", {"catalog":summary(),"tasks":repo.list_tasks()[:10],"metrics":repo.metrics(),"mode":analyzer.mode,"amazon":amazon.status(),"integrations":integrations.status(),"rag":app.state.workflow.knowledge.status()})
    @app.get("/simulator", response_class=HTMLResponse)
    def simulator(request: Request): return templates.TemplateResponse(request, "simulator.html", {})
    @app.get("/api/catalog")
    def catalog() -> dict: return summary()
    @app.get("/api/metrics")
    def metrics() -> dict: return repo.metrics()
    @app.get("/api/rag/status")
    def rag_status() -> dict: return app.state.workflow.knowledge.status()
    @app.post("/api/rag/search")
    def rag_search(payload: RAGSearchRequest) -> dict:
        try:
            hits = app.state.workflow.knowledge.search(payload.query, payload.platform, payload.top_k)
            return {"query": payload.query, "platform": payload.platform, "hits": hits, "pipeline": app.state.workflow.knowledge.status()}
        except Exception:
            raise HTTPException(503, "Local RAG is temporarily unavailable")
    @app.get("/api/integrations/contracts")
    def integration_contracts() -> dict:
        return {"mode":"external_or_fixture","sources":sorted(SOURCES),"resources":sorted(RESOURCE_MODELS),"auth":"environment-scoped token","scopes":["data:read","notify:write"],"retry_statuses":[429,"5xx"],"max_attempts":3}
    @app.get("/api/integrations/status")
    def integration_status() -> dict: return integrations.status()
    @app.post("/api/integrations/sync")
    def sync(payload: SyncRequest) -> dict:
        try: return integrations.sync(payload.source, payload.resource, payload.idempotency_key, payload.failures_before_success)
        except ValueError as error: raise HTTPException(422, str(error))
        except Exception as error: raise HTTPException(502, f"Data sync failed: {type(error).__name__}: {error}")
    @app.post("/simulator/data/{source}/{resource}")
    async def mock_sync(source: str, resource: str, request: Request):
        if request.headers.get("Authorization") != f"Bearer {integration_key}": return JSONResponse({"detail":"Invalid integration token"}, 401)
        if "data:read" not in request.headers.get("X-Scopes", "").split(): return JSONResponse({"detail":"Missing data:read scope"}, 403)
        if source not in SOURCES or resource not in RESOURCE_MODELS: return JSONResponse({"detail":"Unsupported source or resource"}, 404)
        body = await request.json(); key = (source, resource, body.get("idempotency_key")); count = app.state.mock_attempts.get(key, 0) + 1; app.state.mock_attempts[key] = count
        if count <= int(body.get("failures_before_success", 0)): return JSONResponse({"detail":"Demo rate limit"}, 429, headers={"Retry-After":"0"})
        return {"source":source,"resource":resource,"data":summary()[resource]}
    @app.post("/simulator/api/feishu/webhook")
    async def mock_feishu(request: Request):
        if request.headers.get("Authorization") != f"Bearer {integration_key}": return JSONResponse({"detail":"Invalid integration token"}, 401)
        if "notify:write" not in request.headers.get("X-Scopes", "").split(): return JSONResponse({"detail":"Missing notify:write scope"}, 403)
        payload = await request.json()
        if payload.get("msg_type") != "text" or not payload.get("content", {}).get("text"): return JSONResponse({"detail":"Invalid Feishu payload"}, 422)
        return {"code":0,"msg":"mock delivered"}
    @app.get("/api/integrations/amazon/status")
    def amazon_status() -> dict: return amazon.status()
    @app.post("/api/integrations/amazon/preview")
    def amazon_preview(payload: AmazonPreviewRequest) -> dict:
        try: return amazon.preview(payload.lookback_days)
        except RuntimeError as error: raise HTTPException(409, str(error))
        except Exception as error: raise HTTPException(502, f"Amazon SP-API preview failed: {type(error).__name__}")
    @app.post("/api/demo/run")
    def run_demo(payload: DemoRequest) -> dict: return app.state.workflow.create_and_inspect(payload.idempotency_key, payload.fail_once, payload.platform)
    @app.get("/api/tasks")
    def tasks() -> list[dict]: return repo.list_tasks()
    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict:
        try: return repo.get_task(task_id)
        except KeyError: raise HTTPException(404, "Task not found")
    @app.get("/api/tasks/{task_id}/anomalies")
    def anomalies(task_id: str) -> list[dict]:
        try:
            repo.get_task(task_id)
            return repo.list_anomalies(task_id)
        except KeyError:
            raise HTTPException(404, "Task not found")
    @app.post("/api/tasks/{task_id}/approve")
    def approve(task_id: str, payload: ApprovalRequest) -> dict:
        try:
            if payload.executor == 'shadowbot':
                repo.approve_and_select(task_id, payload.approved_by, payload.anomaly_id, enqueue=True)
                return start_shadowbot(task_id)
            result = app.state.workflow.approve_and_execute(task_id, payload.approved_by, payload.anomaly_id)
            if result["status"] == "succeeded": deliver_notification(task_id, strict=False)
            return repo.get_task(task_id)
        except KeyError: raise HTTPException(404, "Task not found")
        except (ValueError, PermissionError) as error: raise HTTPException(409, str(error))
    @app.post("/api/tasks/{task_id}/retry")
    def retry(task_id: str) -> dict:
        try:
            if shadowbot.requeue_if_shadowbot(repo, task_id):
                return start_shadowbot(task_id)
            result = app.state.workflow.retry(task_id)
            if result["status"] == "succeeded": deliver_notification(task_id, strict=False)
            return repo.get_task(task_id)
        except KeyError: raise HTTPException(404, "Task not found")
        except ValueError as error: raise HTTPException(409, str(error))

    def start_shadowbot(task_id: str) -> dict:
        try:
            app.state.shadowbot_launcher()
            return repo.get_task(task_id)
        except Exception as error:
            repo.update_task(task_id, 'failed', error=f'ShadowBot launch failed: {error}', completed=True)
            raise HTTPException(502, f'ShadowBot launch failed: {error}')
    def deliver_notification(task_id: str, strict: bool) -> dict:
        if repo.step_succeeded(task_id, "feishu_notification"):
            return {"mode": "already_delivered", "attempts": 0}
        task_value = repo.get_task(task_id)
        if task_value["status"] != "succeeded": raise HTTPException(409, "Only succeeded tasks can be notified")
        try:
            result = integrations.notify(task_value)
            repo.upsert_step(task_id, "feishu_notification", "succeeded", f"Feishu notification delivered by {result['mode']} in {result['attempts']} attempt(s)")
            return result
        except Exception as error:
            repo.upsert_step(task_id, "feishu_notification", "failed", f"Notification failed: {type(error).__name__}: {error}")
            if strict:
                if isinstance(error, HTTPException): raise
                raise HTTPException(502, f"Notification failed: {type(error).__name__}")
            return {"mode": "failed", "attempts": 0}
    @app.post("/api/tasks/{task_id}/notify")
    def notify(task_id: str) -> dict:
        try: return deliver_notification(task_id, strict=True)
        except KeyError: raise HTTPException(404, "Task not found")
    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str) -> dict:
        try:
            task_value = repo.get_task(task_id)
            if task_value["status"] != "succeeded" or task_value["report"] is None: raise HTTPException(409, "Report is not available until the task succeeds")
            return task_value["report"]
        except KeyError: raise HTTPException(404, "Task not found")
    @app.post('/api/rpa/claim')
    def claim_job(payload: shadowbot.ClaimRequest):
        try:
            return {'job': shadowbot.claim(repo, payload, runtime_base_url)}
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post('/api/rpa/tasks/{task_id}/result')
    def job_result(task_id: str, payload: shadowbot.ResultRequest):
        try:
            result = shadowbot.complete(repo, task_id, payload)
            if result["status"] == "succeeded": deliver_notification(task_id, strict=False)
            return repo.get_task(task_id)
        except KeyError:
            raise HTTPException(404, 'ShadowBot task not found')
        except PermissionError as error:
            raise HTTPException(403, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
    return app
app = create_app()
