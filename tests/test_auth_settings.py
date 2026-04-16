from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config_store import ConfigStore
from app.core.db import AppDB
from app.core.llm import LLMClient
from app.skills.runtime import SkillRuntime
from app.tools.registry import ToolRegistry


@pytest.fixture()
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = AppDB(tmp_path / 'agent.db')
    config_store = ConfigStore(db, tmp_path / 'secret.key')
    tool_registry = ToolRegistry(db)
    llm_client = LLMClient()
    skill_runtime = SkillRuntime(db, config_store, llm_client, tool_registry)

    monkeypatch.setattr(main_module, 'db', db)
    monkeypatch.setattr(main_module, 'config_store', config_store)
    monkeypatch.setattr(main_module, 'tool_registry', tool_registry)
    monkeypatch.setattr(main_module, 'llm_client', llm_client)
    monkeypatch.setattr(main_module, 'skill_runtime', skill_runtime)
    monkeypatch.setattr(main_module, 'AUTH_STATE', {'locked': False, 'token': None, 'failed_attempts': 0})

    return TestClient(main_module.app)


def test_password_setup_lock_unlock_and_settings(isolated_client: TestClient) -> None:
    status = isolated_client.get('/api/security/status')
    assert status.status_code == 200
    assert status.json()['has_password'] is False

    setup = isolated_client.post('/api/security/setup', json={'password': 'pass1234'})
    assert setup.status_code == 200
    token = setup.json()['token']
    assert token
    headers = {'X-Session-Token': token}

    settings_save = isolated_client.put(
        '/api/settings',
        headers=headers,
        json={
            'theme': 'dark',
            'language': 'en-US',
            'log_lines': 60,
            'log_level': 'ERROR',
            'auto_refresh_logs': True,
            'send_shortcut': 'Ctrl+Enter',
        },
    )
    assert settings_save.status_code == 200
    assert settings_save.json()['theme'] == 'dark'

    lock_resp = isolated_client.post('/api/security/lock', headers=headers)
    assert lock_resp.status_code == 200
    assert lock_resp.json()['locked'] is True

    blocked = isolated_client.post('/api/chat', json={'message': 'hello'})
    assert blocked.status_code == 423

    wrong_unlock = isolated_client.post('/api/security/unlock', json={'password': 'wrong'})
    assert wrong_unlock.status_code == 401

    unlock_resp = isolated_client.post('/api/security/unlock', json={'password': 'pass1234'})
    assert unlock_resp.status_code == 200
    token2 = unlock_resp.json()['token']
    headers2 = {'X-Session-Token': token2}

    model_save = isolated_client.put(
        '/api/config/model',
        headers=headers2,
        json={
            'provider': 'mock',
            'model': 'mock-model',
            'api_key': '',
            'secret_key': '',
            'base_url': '',
            'temperature': 0.5,
            'max_tokens': 256,
            'timeout_seconds': 30,
        },
    )
    assert model_save.status_code == 200

    chat_ok = isolated_client.post('/api/chat', headers=headers2, json={'message': 'hello again'})
    assert chat_ok.status_code == 200

    logs_resp = isolated_client.get('/api/logs', headers=headers2, params={'limit': 50, 'level': 'INFO'})
    assert logs_resp.status_code == 200
    assert isinstance(logs_resp.json()['lines'], list)

