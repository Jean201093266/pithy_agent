from __future__ import annotations

import base64
import hmac
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from app.core.db import AppDB


@dataclass
class ModelConfig:
    provider: str = "mock"
    model: str = "mock-model"
    api_key: str = ""
    secret_key: str = ""
    base_url: str = ""
    temperature: float = 0.5
    max_tokens: int = 512
    timeout_seconds: int = 30


@dataclass
class AppSettings:
    theme: str = "system"
    language: str = "zh-CN"
    log_lines: int = 120
    log_level: str = "INFO"
    auto_refresh_logs: bool = False
    send_shortcut: str = "Ctrl+Enter"


class ConfigStore:
    def __init__(self, db: AppDB, secret_path: Path) -> None:
        self.db = db
        self.secret_path = secret_path
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        if self.secret_path.exists():
            raw = self.secret_path.read_bytes().strip()
            return hashlib.sha256(raw).digest()
        seed = get_random_bytes(32)
        self.secret_path.write_bytes(base64.b64encode(seed))
        return hashlib.sha256(base64.b64encode(seed)).digest()

    def _encrypt(self, text: str) -> str:
        nonce = get_random_bytes(12)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(text.encode("utf-8"))
        payload = base64.b64encode(nonce + tag + ciphertext).decode("ascii")
        return payload

    def _decrypt(self, payload: str) -> str:
        raw = base64.b64decode(payload.encode("ascii"))
        nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        plain = cipher.decrypt_and_verify(ciphertext, tag)
        return plain.decode("utf-8")

    def _hash_password(self, password: str, salt: bytes | None = None) -> dict[str, Any]:
        if salt is None:
            salt = get_random_bytes(16)
        iterations = 200_000
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return {
            "salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(digest).decode("ascii"),
            "iterations": iterations,
        }

    def get_model_config(self) -> ModelConfig:
        raw = self.db.get_kv("model_config")
        if not raw:
            return ModelConfig()
        data = json.loads(raw)
        api_key = ""
        secret_key = ""
        if data.get("api_key_encrypted"):
            api_key = self._decrypt(data["api_key_encrypted"])
        if data.get("secret_key_encrypted"):
            secret_key = self._decrypt(data["secret_key_encrypted"])
        provider = data.get("provider", "mock")
        if provider not in {"mock", "openai", "openai-compatible", "tongyi", "wenxin"}:
            provider = "mock"
        return ModelConfig(
            provider=provider,
            model=data.get("model", "mock-model"),
            api_key=api_key,
            secret_key=secret_key,
            base_url=data.get("base_url", ""),
            temperature=float(data.get("temperature", 0.5)),
            max_tokens=int(data.get("max_tokens", 512)),
            timeout_seconds=int(data.get("timeout_seconds", 30)),
        )

    def save_model_config(self, model_cfg: ModelConfig) -> None:
        payload = asdict(model_cfg)
        payload["api_key_encrypted"] = self._encrypt(model_cfg.api_key) if model_cfg.api_key else ""
        payload["secret_key_encrypted"] = self._encrypt(model_cfg.secret_key) if model_cfg.secret_key else ""
        payload.pop("api_key")
        payload.pop("secret_key")
        self.db.set_kv("model_config", json.dumps(payload, ensure_ascii=False))

    def get_app_settings(self) -> AppSettings:
        raw = self.db.get_kv("app_settings")
        if not raw:
            return AppSettings()
        data = json.loads(raw)
        theme = data.get("theme", "system")
        if theme not in {"system", "light", "dark"}:
            theme = "system"
        language = data.get("language", "zh-CN")
        if language not in {"zh-CN", "en-US"}:
            language = "zh-CN"
        return AppSettings(
            theme=theme,
            language=language,
            log_lines=max(20, min(int(data.get("log_lines", 120)), 500)),
            log_level=str(data.get("log_level", "INFO")).upper(),
            auto_refresh_logs=bool(data.get("auto_refresh_logs", False)),
            send_shortcut=str(data.get("send_shortcut", "Ctrl+Enter")),
        )

    def save_app_settings(self, settings: AppSettings) -> None:
        self.db.set_kv("app_settings", json.dumps(asdict(settings), ensure_ascii=False))

    def has_unlock_password(self) -> bool:
        return bool(self.db.get_kv("auth_password_hash"))

    def set_unlock_password(self, password: str) -> None:
        payload = self._hash_password(password)
        self.db.set_kv("auth_password_hash", json.dumps(payload, ensure_ascii=False))

    def verify_unlock_password(self, password: str) -> bool:
        raw = self.db.get_kv("auth_password_hash")
        if not raw:
            return False
        data = json.loads(raw)
        salt = base64.b64decode(data["salt"].encode("ascii"))
        expected = data["hash"]
        iterations = int(data.get("iterations", 200_000))
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        actual = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(actual, expected)

