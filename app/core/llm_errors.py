from __future__ import annotations


class LLMProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        provider: str,
        retryable: bool,
        status_code: int = 400,
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.model = model

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
        }
        if self.model:
            d["model"] = self.model
        return d

