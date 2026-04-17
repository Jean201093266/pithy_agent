from __future__ import annotations

import json
import logging
import time
from typing import Any, Generator

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config_store import ModelConfig
from app.core.llm_errors import LLMProviderError

LOGGER = logging.getLogger(__name__)


class TokenUsage:
    """Carries token usage stats from a single LLM call."""
    __slots__ = ("prompt_tokens", "completion_tokens", "total_tokens", "latency_ms")

    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0,
                 total_tokens: int = 0, latency_ms: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        self.latency_ms = latency_ms

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
        }


class LLMClient:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5), reraise=True)
    def call(self, prompt: str, cfg: ModelConfig, context: list[dict[str, Any]] | None = None) -> str:
        reply, _ = self.call_with_usage(prompt, cfg, context)
        return reply

    def call_with_usage(
        self, prompt: str, cfg: ModelConfig, context: list[dict[str, Any]] | None = None
    ) -> tuple[str, TokenUsage]:
        """Like call() but also returns TokenUsage."""
        provider = (cfg.provider or "mock").lower()
        t0 = time.monotonic()
        if provider == "mock":
            reply = self._mock_reply(prompt, context)
            usage = TokenUsage(latency_ms=int((time.monotonic() - t0) * 1000))
            return reply, usage
        if provider in {"openai", "openai-compatible"}:
            return self._openai_compatible_call_with_usage(prompt, cfg, context, provider)
        if provider == "tongyi":
            tongyi_cfg = ModelConfig(**{**cfg.__dict__})
            tongyi_cfg.base_url = tongyi_cfg.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            return self._openai_compatible_call_with_usage(prompt, tongyi_cfg, context, provider)
        if provider == "wenxin":
            reply = self._wenxin_call(prompt, cfg, context)
            usage = TokenUsage(latency_ms=int((time.monotonic() - t0) * 1000))
            return reply, usage
        raise LLMProviderError(
            code="LLM_PROVIDER_UNSUPPORTED",
            message=f"unsupported provider: {cfg.provider}",
            provider=provider,
            retryable=False,
            status_code=400,
        )

    def _mock_reply(self, prompt: str, context: list[dict[str, Any]] | None = None) -> str:
        prefix = "[MockAgent] "
        if context:
            prefix += f"(context={len(context)}) "
        return prefix + prompt

    def _openai_compatible_call(
        self, prompt: str, cfg: ModelConfig, context: list[dict[str, Any]] | None = None, provider: str = "openai"
    ) -> str:
        reply, _ = self._openai_compatible_call_with_usage(prompt, cfg, context, provider)
        return reply

    def _openai_compatible_call_with_usage(
        self, prompt: str, cfg: ModelConfig, context: list[dict[str, Any]] | None = None, provider: str = "openai"
    ) -> tuple[str, TokenUsage]:
        if not cfg.api_key:
            raise LLMProviderError(
                code="LLM_CONFIG_ERROR",
                message="api_key is required",
                provider=provider,
                retryable=False,
                status_code=400,
            )
        base_url = cfg.base_url.rstrip("/")
        if not base_url:
            if provider == "openai":
                base_url = "https://api.openai.com/v1"
            else:
                raise LLMProviderError(
                    code="LLM_CONFIG_ERROR",
                    message="base_url is required",
                    provider=provider,
                    retryable=False,
                    status_code=400,
                )

        messages = [{"role": "system", "content": "You are a helpful local agent."}]
        if context:
            for item in context[-8:]:
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": prompt})

        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        payload = {
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }

        try:
            t0 = time.monotonic()
            response = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout_seconds)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if response.status_code >= 400:
                self._raise_provider_http_error(provider, response)
            data = response.json()
            reply = data["choices"][0]["message"]["content"]
            usage_data = data.get("usage") or {}
            usage = TokenUsage(
                prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                completion_tokens=int(usage_data.get("completion_tokens", 0)),
                total_tokens=int(usage_data.get("total_tokens", 0)),
                latency_ms=latency_ms,
            )
            return reply, usage
        except LLMProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(
                code="LLM_RESPONSE_ERROR",
                message=f"invalid response payload: {exc}",
                provider=provider,
                retryable=False,
                status_code=502,
            ) from exc
        except requests.Timeout as exc:
            raise LLMProviderError(
                code="LLM_TIMEOUT",
                message="request timeout",
                provider=provider,
                retryable=True,
                status_code=504,
            ) from exc
        except requests.RequestException as exc:
            LOGGER.exception("LLM request failed")
            raise LLMProviderError(
                code="LLM_NETWORK_ERROR",
                message=f"network error: {exc}",
                provider=provider,
                retryable=True,
                status_code=502,
            ) from exc

    def _wenxin_call(self, prompt: str, cfg: ModelConfig, context: list[dict[str, Any]] | None = None) -> str:
        if not cfg.api_key or not cfg.secret_key:
            raise LLMProviderError(
                code="LLM_CONFIG_ERROR",
                message="wenxin requires api_key and secret_key",
                provider="wenxin",
                retryable=False,
                status_code=400,
            )

        token_url = "https://aip.baidubce.com/oauth/2.0/token"
        chat_url = cfg.base_url.rstrip("/") or "https://qianfan.baidubce.com/v2/chat/completions"
        model = cfg.model or "ernie-4.0-turbo-8k"
        messages = [{"role": "user", "content": prompt}]
        if context:
            for item in context[-8:]:
                messages.insert(0, {"role": item["role"], "content": item["content"]})

        try:
            token_resp = requests.post(
                token_url,
                params={
                    "grant_type": "client_credentials",
                    "client_id": cfg.api_key,
                    "client_secret": cfg.secret_key,
                },
                timeout=cfg.timeout_seconds,
            )
            if token_resp.status_code >= 400:
                self._raise_provider_http_error("wenxin", token_resp)
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise LLMProviderError(
                    code="LLM_AUTH_ERROR",
                    message="failed to fetch wenxin access token",
                    provider="wenxin",
                    retryable=False,
                    status_code=401,
                )

            chat_resp = requests.post(
                chat_url,
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": cfg.temperature,
                },
                timeout=cfg.timeout_seconds,
            )
            if chat_resp.status_code >= 400:
                self._raise_provider_http_error("wenxin", chat_resp)
            data = chat_resp.json()
            return data["choices"][0]["message"]["content"]
        except LLMProviderError:
            raise
        except requests.Timeout as exc:
            raise LLMProviderError(
                code="LLM_TIMEOUT",
                message="request timeout",
                provider="wenxin",
                retryable=True,
                status_code=504,
            ) from exc
        except requests.RequestException as exc:
            raise LLMProviderError(
                code="LLM_NETWORK_ERROR",
                message=f"network error: {exc}",
                provider="wenxin",
                retryable=True,
                status_code=502,
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(
                code="LLM_RESPONSE_ERROR",
                message=f"invalid response payload: {exc}",
                provider="wenxin",
                retryable=False,
                status_code=502,
            ) from exc

    def stream(
        self,
        prompt: str,
        cfg: ModelConfig,
        context: list[dict[str, Any]] | None = None,
    ) -> Generator[str, None, None]:
        """Stream tokens from LLM. Yields text chunks."""
        provider = (cfg.provider or "mock").lower()
        if provider == "mock":
            yield from self._mock_stream(prompt, context)
            return
        if provider in {"openai", "openai-compatible", "tongyi"}:
            if provider == "tongyi":
                cfg = ModelConfig(**{**cfg.__dict__})
                cfg.base_url = cfg.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            yield from self._openai_stream(prompt, cfg, context, provider)
            return
        # fallback: non-streaming call
        result = self.call(prompt, cfg, context)
        yield result

    def _mock_stream(
        self,
        prompt: str,
        context: list[dict[str, Any]] | None = None,
    ) -> Generator[str, None, None]:
        """Simulate streaming for mock provider."""
        import time
        reply = f"[MockAgent] {'(context=' + str(len(context)) + ') ' if context else ''}{prompt}"
        words = reply.split()
        for i, word in enumerate(words):
            yield ('' if i == 0 else ' ') + word
            time.sleep(0.04)

    def _openai_stream(
        self,
        prompt: str,
        cfg: ModelConfig,
        context: list[dict[str, Any]] | None = None,
        provider: str = "openai",
    ) -> Generator[str, None, None]:
        """Stream tokens from OpenAI-compatible API."""
        if not cfg.api_key:
            raise LLMProviderError(
                code="LLM_CONFIG_ERROR",
                message="api_key is required",
                provider=provider,
                retryable=False,
                status_code=400,
            )
        base_url = (cfg.base_url or "").rstrip("/")
        if not base_url:
            if provider == "openai":
                base_url = "https://api.openai.com/v1"
            else:
                raise LLMProviderError(
                    code="LLM_CONFIG_ERROR",
                    message="base_url is required",
                    provider=provider,
                    retryable=False,
                    status_code=400,
                )

        messages = [{"role": "system", "content": "You are a helpful local agent."}]
        if context:
            for item in context[-8:]:
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": prompt})

        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        payload = {
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "stream": True,
        }

        try:
            with requests.post(
                url, headers=headers, json=payload,
                timeout=cfg.timeout_seconds, stream=True,
            ) as resp:
                if resp.status_code >= 400:
                    self._raise_provider_http_error(provider, resp)
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0]["delta"]
                        text = delta.get("content") or ""
                        if text:
                            yield text
                    except (KeyError, IndexError, json.JSONDecodeError):
                        continue
        except LLMProviderError:
            raise
        except requests.Timeout as exc:
            raise LLMProviderError(
                code="LLM_TIMEOUT", message="request timeout",
                provider=provider, retryable=True, status_code=504,
            ) from exc
        except requests.RequestException as exc:
            raise LLMProviderError(
                code="LLM_NETWORK_ERROR", message=f"network error: {exc}",
                provider=provider, retryable=True, status_code=502,
            ) from exc

    def _raise_provider_http_error(self, provider: str, response: requests.Response) -> None:
        status = response.status_code
        message = "http request failed"
        try:
            data = response.json()
            if isinstance(data, dict):
                message = (
                    data.get("error", {}).get("message")
                    or data.get("error_msg")
                    or data.get("message")
                    or str(data)
                )
        except ValueError:
            message = response.text[:500]

        if status in {401, 403}:
            raise LLMProviderError("LLM_AUTH_ERROR", message, provider, False, 401)
        if status == 429:
            raise LLMProviderError("LLM_RATE_LIMIT", message, provider, True, 429)
        if 500 <= status <= 599:
            raise LLMProviderError("LLM_UPSTREAM_ERROR", message, provider, True, 502)
        raise LLMProviderError("LLM_REQUEST_ERROR", message, provider, False, 400)

    def embed(self, text: str, cfg: ModelConfig) -> list[float]:
        """Generate a text embedding vector.

        Priority:
        1. sentence-transformers (local, no API cost)
        2. OpenAI-compatible /embeddings API
        3. Fallback: hash-based pseudo-embedding (original behaviour, dims=64)
        """
        # --- Try sentence-transformers (local) ---
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _model = getattr(self, "_st_model", None)
            if _model is None:
                self._st_model = SentenceTransformer("all-MiniLM-L6-v2")
                _model = self._st_model
            vec = _model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        except ImportError:
            pass
        except Exception as exc:
            LOGGER.warning("sentence-transformers embed failed: %s", exc)

        # --- Try OpenAI-compatible embeddings API ---
        provider = (cfg.provider or "mock").lower()
        if provider in {"openai", "openai-compatible", "tongyi"} and cfg.api_key:
            try:
                base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
                if provider == "tongyi":
                    base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
                embed_model = "text-embedding-3-small" if provider == "openai" else cfg.model
                resp = requests.post(
                    f"{base_url}/embeddings",
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                    json={"model": embed_model, "input": text[:8000]},
                    timeout=15,
                )
                if resp.status_code == 200:
                    return resp.json()["data"][0]["embedding"]
            except Exception as exc:
                LOGGER.warning("API embed failed: %s", exc)

        # --- Fallback: hash-based pseudo-embedding ---
        import math as _math
        import re as _re
        dims = 64
        tokens = _re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        vec = [0.0] * dims
        if tokens:
            for token in tokens:
                h = hash(token)
                idx = h % dims
                sign = 1.0 if (h >> 1) & 1 else -1.0
                vec[idx] += sign
            norm = _math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
        return vec
