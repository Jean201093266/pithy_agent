from __future__ import annotations


class LLMProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        provider: str,
        retryable: bool,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
        }

