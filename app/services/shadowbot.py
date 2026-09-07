"""Local single-machine ShadowBot queue; never reassign a running browser job automatically."""
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _cli(cli: Path, *args: str, timeout: int = 15) -> dict:
    try:
        result = subprocess.run(
            [str(cli), *args],
            cwd=cli.parent,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('ShadowBot CLI timed out') from error
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if result.returncode or not payload.get('ok'):
        raise RuntimeError(payload.get('error') or (result.stderr or '').strip() or 'ShadowBot CLI failed')
    return payload.get('data') or {}


def launch() -> dict:
    cli = Path(os.environ['SHADOWBOT_CLI'])
    app_id = os.environ['SHADOWBOT_APP_ID']
    if not cli.is_file():
        raise RuntimeError('ShadowBot CLI was not found')
    try:
        _cli(cli, 'system', 'health', timeout=1)
    except RuntimeError:
        shell = Path(os.getenv('SHADOWBOT_SHELL', str(cli.with_name('ShadowBot.Shell.exe'))))
        if not shell.is_file():
            raise RuntimeError('ShadowBot client was not found')
        subprocess.Popen(
            [str(shell)],
            cwd=shell.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            time.sleep(0.5)
            try:
                _cli(cli, 'system', 'health', timeout=1)
                break
            except RuntimeError:
                pass
        else:
            raise RuntimeError('ShadowBot client did not become ready')
    for attempt in range(3):
        try:
            return _cli(cli, 'console', 'task', 'run', '--app-id', app_id, '--async')
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(1)


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=80)
    claim_id: str = Field(min_length=8, max_length=120)


class ResultRequest(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    status: Literal['succeeded', 'failed']
    saved_fields: dict[str, str] = Field(default_factory=dict)
    error: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def validate_result(self):
        if self.status == 'failed' and not self.error.strip():
            raise ValueError('A failed execution requires an error reason')
        return self


def expected_fields(report):
    sku = report['selected_anomaly']['sku']
    advice = next(row for row in report['listing_advice'] if row['sku'] == sku)
    return {'sku': sku, 'title': advice['suggested_title'], 'bullets': '\n'.join(advice['suggested_bullets'])}


def claim(repo, request, base_url):
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM rpa_jobs WHERE claim_id=?', (request.claim_id,)).fetchone()
        if existing:
            if existing['worker_id'] != request.worker_id:
                raise ValueError('Claim id belongs to another worker')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (existing['task_id'],)).fetchone()
            if task['status'] != 'running':
                raise ValueError('Claim is already completed; use a new claim id')
            token = existing['token']
        else:
            task = db.execute("SELECT tasks.* FROM tasks JOIN rpa_jobs ON tasks.id=rpa_jobs.task_id WHERE status='queued' AND approval_status='approved' ORDER BY created_at,id LIMIT 1").fetchone()
            if task is None:
                return None
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE rpa_jobs SET worker_id=?,claim_id=?,token=?,result_json=NULL WHERE task_id=?', (request.worker_id, request.claim_id, token, task['id']))
            db.execute("UPDATE tasks SET status='running',attempt=attempt+1,error=NULL,execution_started_at=?,completed_at=NULL,updated_at=? WHERE id=?", (now, now, task['id']))
        report = json.loads(task['report_json'])
        return {'task_id': task['id'], 'token': token, 'url': base_url.rstrip('/') + '/simulator', 'fields': expected_fields(report), 'note': 'Approved local draft only'}


def complete(repo, task_id, request):
    result = request.model_dump(exclude={'token'})
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT * FROM rpa_jobs WHERE task_id=?', (task_id,)).fetchone()
        if not job:
            raise KeyError(task_id)
        if not job['token'] or not secrets.compare_digest(job['token'], request.token):
            raise PermissionError('Invalid or stale execution token')
        if job['result_json']:
            if json.loads(job['result_json']) != result:
                raise ValueError('Conflicting duplicate result')
        else:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task['status'] != 'running':
                raise ValueError('Task is not running')
            report = json.loads(task['report_json'])
            if request.status == 'succeeded' and request.saved_fields != expected_fields(report):
                raise ValueError('Read-back fields do not match approved content')
            report['evidence']['shadowbot'] = {'worker_id': job['worker_id'], **result}
            report['review']['status'] = 'approved_and_executed' if request.status == 'succeeded' else 'execution_failed'
            report['business_summary']['inspection_state'] = request.status
            report['business_summary']['external_side_effect'] = 'local_simulator_draft_reported' if request.status == 'succeeded' else 'execution_failed'
            now = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE tasks SET status=?,error=?,report_json=?,completed_at=?,updated_at=? WHERE id=?', (request.status, request.error or None, json.dumps(report), now, now, task_id))
            db.execute('UPDATE rpa_jobs SET result_json=? WHERE task_id=?', (json.dumps(result), task_id))
            db.execute('INSERT INTO attempt_history(task_id,attempt,status,error) VALUES(?,?,?,?)', (task_id, task['attempt'], request.status, request.error or None))
            db.execute("INSERT INTO steps(task_id,name,status,detail) VALUES(?,'shadowbot_browser',?,?) ON CONFLICT(task_id,name) DO UPDATE SET status=excluded.status,detail=excluded.detail", (task_id, request.status, request.error or 'Worker returned matching read-back fields'))
    return repo.get_task(task_id)


def requeue_if_shadowbot(repo, task_id):
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM rpa_jobs WHERE task_id=?', (task_id,)).fetchone():
            return False
        updated = db.execute("UPDATE tasks SET status='queued',error=NULL,completed_at=NULL,execution_started_at=NULL WHERE id=? AND status='failed' AND approval_status='approved'", (task_id,))
        if updated.rowcount != 1:
            raise ValueError('Only failed ShadowBot tasks may be requeued')
        db.execute('UPDATE rpa_jobs SET token=NULL,claim_id=NULL,worker_id=NULL,result_json=NULL WHERE task_id=?', (task_id,))
    return True
