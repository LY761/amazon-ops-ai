import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.services import shadowbot
from test_api import make_client


def test_launcher_starts_shadowbot_after_offline_timeout(tmp_path, monkeypatch):
    cli, shell = tmp_path / 'shadowbot.shell-cli.exe', tmp_path / 'ShadowBot.Shell.exe'
    cli.touch(); shell.touch()
    monkeypatch.setenv('SHADOWBOT_CLI', str(cli))
    monkeypatch.setenv('SHADOWBOT_SHELL', str(shell))
    monkeypatch.setenv('SHADOWBOT_APP_ID', '509da804-71e6-43ee-9b6d-33d0f90fad42')
    calls, started = [], []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 1)
        if len(calls) == 2:
            return SimpleNamespace(returncode=0, stdout=None, stderr=None)
        if 'run' in command and sum('run' in call for call in calls) == 1:
            return SimpleNamespace(returncode=1, stdout=json.dumps({'ok': False, 'error': 'not ready'}), stderr='')
        data = {'taskId': 'rpa-1'} if 'run' in command else {'status': 'ready'}
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'data': data}), stderr='')

    monkeypatch.setattr(shadowbot.subprocess, 'run', run)
    monkeypatch.setattr(shadowbot.subprocess, 'Popen', lambda *args, **kwargs: started.append(args))
    monkeypatch.setattr(shadowbot.time, 'sleep', lambda _: None)
    assert shadowbot.launch()['taskId'] == 'rpa-1'
    assert started and calls[-1][-1] == '--async' and sum('run' in call for call in calls) == 2


def test_shadowbot_claim_completion_retry_and_concurrency(tmp_path):
    launches = []
    client = make_client(tmp_path, shadowbot_launcher=lambda: launches.append('run') or {})
    task = client.post('/api/demo/run', json={'idempotency_key': 'shadowbot-test'}).json()
    claim = lambda key: client.post('/api/rpa/claim', json={'worker_id': 'worker', 'claim_id': key})
    assert claim('not-approved').json() == {'job': None}
    approved = client.post(f"/api/tasks/{task['id']}/approve", json={'approved_by': 'operator', 'executor': 'shadowbot'}).json()
    assert approved['status'] == 'queued'
    assert launches == ['run']
    assert client.app.state.workflow.rpa.calls == 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda i: claim(f'parallel-{i}').json()['job'], range(4)))
    job = next(j for j in jobs if j)
    assert sum(j is not None for j in jobs) == 1
    path = f"/api/rpa/tasks/{task['id']}/result"
    failed = {'token': job['token'], 'status': 'failed', 'error': 'Element timeout'}
    assert client.post(path, json=failed).json()['status'] == 'failed'
    assert client.post(path, json=failed).status_code == 200
    assert client.post(f"/api/tasks/{task['id']}/retry").json()['status'] == 'queued'
    assert launches == ['run', 'run']
    assert client.post(path, json=failed).status_code == 403
    new_job = claim('retry-claim').json()['job']
    assert claim('retry-claim').json()['job'] == new_job
    result = {'token': new_job['token'], 'status': 'succeeded', 'saved_fields': {}}
    assert client.post(path, json=result).status_code == 409
    result['saved_fields'] = new_job['fields']
    done = client.post(path, json=result).json()
    assert done['status'] == 'succeeded' and done['retry_count'] == 1
    assert client.app.state.integrations.notifications == [task['id']]
    assert client.post(path, json=result).status_code == 200
    assert client.app.state.integrations.notifications == [task['id']]
    assert len(client.get(f"/api/tasks/{task['id']}").json()['attempt_history']) == 2
    result['status'], result['error'] = 'failed', 'conflict'
    assert client.post(path, json=result).status_code == 409
    assert client.app.state.workflow.rpa.calls == 0
