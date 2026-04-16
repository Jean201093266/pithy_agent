from __future__ import annotations

from typing import Any

from app.core.config_store import ModelConfig
from app.core.llm import LLMClient
from app.core.llm_errors import LLMProviderError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def test_openai_like_success(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr('requests.post', fake_post)
    client = LLMClient()
    cfg = ModelConfig(
        provider='openai-compatible',
        model='test-model',
        api_key='k',
        base_url='https://example.com/v1',
    )
    assert client.call('hello', cfg, []) == 'ok'


def test_openai_like_auth_error(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return FakeResponse(401, {"error": {"message": "invalid key"}})

    monkeypatch.setattr('requests.post', fake_post)
    client = LLMClient()
    cfg = ModelConfig(
        provider='openai-compatible',
        model='test-model',
        api_key='k',
        base_url='https://example.com/v1',
    )

    try:
        client.call('hello', cfg, [])
        assert False, 'expected LLMProviderError'
    except LLMProviderError as exc:
        assert exc.code == 'LLM_AUTH_ERROR'
        assert exc.status_code == 401


def test_wenxin_requires_secret_key() -> None:
    client = LLMClient()
    cfg = ModelConfig(provider='wenxin', model='ernie', api_key='ak', secret_key='')
    try:
        client.call('hello', cfg, [])
        assert False, 'expected LLMProviderError'
    except LLMProviderError as exc:
        assert exc.code == 'LLM_CONFIG_ERROR'

