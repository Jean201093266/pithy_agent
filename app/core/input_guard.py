"""
Input guard: basic Prompt Injection detection + input sanitisation.

Usage::

    from app.core.input_guard import InputGuard

    result = InputGuard.check(user_message)
    if result.blocked:
        # return result.reason to the user
        ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

# ---------------------------------------------------------------------------
# Known injection patterns (case-insensitive)
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # Classic override attempts
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"forget\s+(everything|all|your\s+instructions?)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?(?:evil|malicious|jailbreak)", re.I),
    re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:evil|malicious|DAN|jailbreak)", re.I),
    re.compile(r"\bDAN\b"),
    # Token boundary injection
    re.compile(r"<\|im_start\|>|<\|im_end\|>|<\|system\|>|<\|user\|>|<\|assistant\|>"),
    re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>"),
    # Role switching
    re.compile(r"system\s*:\s*you\s+are\s+now", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"override\s+(?:your\s+)?(?:safety|system|instructions?)", re.I),
    # Jailbreak keywords
    re.compile(r"jailbreak\s+mode", re.I),
    re.compile(r"developer\s+mode\s+enabled", re.I),
    re.compile(r"unrestricted\s+mode", re.I),
    # Prompt leakage attempts
    re.compile(r"(print|repeat|output|show|reveal|display)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"what\s+(are\s+)?your\s+(system\s+)?instructions?", re.I),
    # Additional jailbreak patterns
    re.compile(r"do\s+anything\s+now", re.I),
    re.compile(r"simulate\s+(?:a\s+)?(?:developer|admin)\s+mode", re.I),
    re.compile(r"pretend\s+(?:you\s+)?(?:have\s+)?no\s+(?:restrictions?|rules?|limitations?)", re.I),
    re.compile(r"bypass\s+(?:your\s+)?(?:safety|content|ethical)\s+(?:filter|guard|policy)", re.I),
    re.compile(r"respond\s+without\s+(?:any\s+)?(?:moral|ethical|safety)", re.I),
    # Base64/encoding bypass attempts
    re.compile(r"decode\s+(?:this\s+)?base64\s+and\s+(?:execute|run|follow)", re.I),
    re.compile(r"execute\s+(?:the\s+)?(?:decoded|following\s+encoded)", re.I),
]

_MAX_INPUT_LENGTH = 8000  # characters


@dataclass
class GuardResult:
    blocked: bool
    reason: str = ""
    sanitised: str = ""   # cleaned input (stripped of dangerous tokens)


class InputGuard:
    """Static utility for input validation."""

    _STRIP_TOKENS: ClassVar[list[re.Pattern[str]]] = [
        # Strip control tokens that could leak into prompts
        re.compile(r"<\|[^|>]{1,30}\|>"),
        re.compile(r"\[/?INST\]|<</?SYS>>"),
    ]

    # Dangerous HTML tags/attributes for XSS prevention in output
    _XSS_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.DOTALL),
        re.compile(r"\bon\w+\s*=\s*[\"'][^\"']*[\"']", re.I),
        re.compile(r"javascript\s*:", re.I),
        re.compile(r"<iframe\b[^>]*>", re.I),
        re.compile(r"<object\b[^>]*>", re.I),
        re.compile(r"<embed\b[^>]*>", re.I),
    ]

    @classmethod
    def check(cls, text: str) -> GuardResult:
        """Run all checks.  Returns a GuardResult."""
        if not text or not text.strip():
            return GuardResult(blocked=False, sanitised=text)

        # 1. Length check
        if len(text) > _MAX_INPUT_LENGTH:
            return GuardResult(
                blocked=True,
                reason=f"输入内容过长（{len(text)} 字符），最多允许 {_MAX_INPUT_LENGTH} 字符，请缩短后重试。",
            )

        # 2. Injection pattern check
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return GuardResult(
                    blocked=True,
                    reason="检测到潜在的提示词注入攻击，已拒绝处理。如果这是误判，请换一种表达方式。",
                )

        # 3. Strip dangerous control tokens (non-blocking, just sanitise)
        sanitised = text
        for pat in cls._STRIP_TOKENS:
            sanitised = pat.sub("", sanitised)

        return GuardResult(blocked=False, sanitised=sanitised)

    @classmethod
    def sanitize_output(cls, text: str) -> str:
        """Strip dangerous HTML/JS from LLM output to prevent XSS."""
        if not text:
            return text
        sanitised = text
        for pat in cls._XSS_PATTERNS:
            sanitised = pat.sub("", sanitised)
        return sanitised

