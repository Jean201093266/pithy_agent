from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _set_mock_config() -> None:
    resp = client.put(
        '/api/config/model',
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
    assert resp.status_code == 200


def test_health_ok() -> None:
    resp = client.get('/api/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'ok'


def test_chat_ok() -> None:
    _set_mock_config()
    resp = client.post('/api/chat', json={'message': 'hello'})
    assert resp.status_code == 200
    body = resp.json()
    assert 'reply' in body
    assert isinstance(body['plan'], list)
    assert 'brain' in body
    assert body['brain']['strategy'] in ('react-primary+plan-exec-lite', 'mock-react', 'llm-react')
    assert isinstance(body['brain'].get('react_trace', []), list)


def test_tools_list_ok() -> None:
    resp = client.get('/api/tools')
    assert resp.status_code == 200
    tools = resp.json()
    assert any(t['name'] == 'read_file' for t in tools)


def test_skill_save_and_run() -> None:
    _set_mock_config()
    skill_payload = {
        'name': 'smoke_skill',
        'version': '1.0.0',
        'description': 'basic llm skill',
        'steps': [{'kind': 'llm', 'name': 'mock_step', 'params': {'prompt': 'hello skill'}}],
    }
    save_resp = client.post('/api/skills', json=skill_payload)
    assert save_resp.status_code == 200
    skill_id = save_resp.json()['id']

    run_resp = client.post(f'/api/skills/{skill_id}/run', json={'input_text': 'run', 'context': {}})
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body['skill']['id'] == skill_id
    assert 'output' in body


def test_model_test_returns_structured_error_code() -> None:
    save_resp = client.put(
        '/api/config/model',
        json={
            'provider': 'openai',
            'model': 'gpt-4o-mini',
            'api_key': '',
            'secret_key': '',
            'base_url': 'https://api.openai.com/v1',
            'temperature': 0.5,
            'max_tokens': 128,
            'timeout_seconds': 30,
        },
    )
    assert save_resp.status_code == 200

    resp = client.post('/api/config/model/test')
    assert resp.status_code == 400
    detail = resp.json()['detail']
    assert detail['code'] == 'LLM_CONFIG_ERROR'


def test_skill_import_export_and_rollback() -> None:
    _set_mock_config()
    initial = {
        'name': 'versioned_skill',
        'version': '1.0.0',
        'description': 'v1',
        'steps': [{'kind': 'llm', 'name': 'step1', 'params': {'prompt': 'first'}}],
    }
    save_resp = client.post('/api/skills', json=initial)
    assert save_resp.status_code == 200
    skill_id = save_resp.json()['id']

    import_payload = {
        'format': 'yaml',
        'content': (
            'name: versioned_skill\n'
            'version: 2.0.0\n'
            'description: v2\n'
            'steps:\n'
            '  - kind: llm\n'
            '    name: step2\n'
            '    params:\n'
            '      prompt: second\n'
        ),
    }
    import_resp = client.post('/api/skills/import', json=import_payload)
    assert import_resp.status_code == 200
    assert import_resp.json()['version'] == '2.0.0'

    versions_resp = client.get(f'/api/skills/{skill_id}/versions')
    assert versions_resp.status_code == 200
    versions = versions_resp.json()['versions']
    assert len(versions) >= 2

    export_resp = client.get(f'/api/skills/{skill_id}/export', params={'format': 'json'})
    assert export_resp.status_code == 200
    assert '"version": "2.0.0"' in export_resp.json()['content']

    first_version_id = versions[-1]['version_id']
    rollback_resp = client.post(
        f'/api/skills/{skill_id}/rollback',
        json={'target_version_id': first_version_id, 'reason': 'test'},
    )
    assert rollback_resp.status_code == 200
    assert rollback_resp.json()['active_version'] == '1.0.0'


def test_custom_tool_import_and_execute() -> None:
    manifest = {
        'name': 'api_custom_echo',
        'description': 'Echo wrapper for API smoke test',
        'risk_level': 'normal',
        'target_tool': 'echo',
        'default_params': {'message': 'default'},
        'param_mapping': {'text': 'message'},
        'version': '1.0.0',
    }
    import_resp = client.post('/api/tools/import', json=manifest)
    assert import_resp.status_code == 200
    assert import_resp.json()['tool']['name'] == 'api_custom_echo'

    execute_resp = client.post(
        '/api/tools/api_custom_echo/execute',
        json={'params': {'text': 'hello api custom'}, 'authorized': True},
    )
    assert execute_resp.status_code == 200
    mcp_result = execute_resp.json()['result']
    # execute returns an MCP envelope: {"content": [{"type": "text", "text": "..."}], "isError": false}
    assert not mcp_result['isError']
    assert 'hello api custom' in mcp_result['content'][0]['text']


