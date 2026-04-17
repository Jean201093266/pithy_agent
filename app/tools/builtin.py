from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import requests

try:
    from PIL import Image, ImageGrab
except Exception:  # pragma: no cover
    Image = None
    ImageGrab = None

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

# ---------------------------------------------------------------------------
# Security: allowed base directories for file operations
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_WRITE_ROOTS: list[Path] = [_ROOT / "data"]

# Dangerous command patterns (case-insensitive substring match)
_CMD_BLACKLIST = [
    "rm -rf", "del /f", "del /s", "format ", "mkfs",
    "shutdown", "reboot", "halt", "poweroff",
    ":(){ :|:& };:", "dd if=",
    "reg delete", "reg add",
    "net user", "net localgroup",
    "chmod 777 /", "chown root",
    "> /dev/", "> /proc/",
]


def _is_safe_write_path(path: Path) -> bool:
    """Return True only if the path is under an allowed write root."""
    resolved = path.resolve()
    for root in _ALLOWED_WRITE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _check_command_safety(cmd: str) -> None:
    """Raise PermissionError if the command matches the blacklist."""
    lower_cmd = cmd.lower()
    for pattern in _CMD_BLACKLIST:
        if pattern in lower_cmd:
            raise PermissionError(
                f"Command blocked by security policy (matched pattern: '{pattern}'). "
                "Use a more specific command or adjust the allowed command list."
            )


def check_ocr_availability() -> dict[str, Any]:
    if Image is None:
        return {
            "available": False,
            "reason": "pillow_missing",
            "hint": "Install Pillow to enable image loading for OCR.",
        }
    if pytesseract is None:
        return {
            "available": False,
            "reason": "pytesseract_missing",
            "hint": "Install pytesseract and ensure tesseract executable is available.",
        }

    try:
        version = pytesseract.get_tesseract_version()
    except Exception:
        return {
            "available": False,
            "reason": "tesseract_unavailable",
            "hint": "Install Tesseract OCR and make sure it is in PATH.",
        }

    return {
        "available": True,
        "reason": "ok",
        "hint": "OCR is ready.",
        "version": str(version),
    }


def tool_read_file(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(params.get("path", "")).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    return {"path": str(path), "content": path.read_text(encoding="utf-8")[:5000]}


def tool_echo(params: dict[str, Any]) -> dict[str, Any]:
    message = str(params.get("message", params.get("text", "")))
    return {"message": message, "params": params}


def tool_write_file(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(params.get("path", "")).expanduser()
    if not _is_safe_write_path(path):
        allowed = ", ".join(str(r) for r in _ALLOWED_WRITE_ROOTS)
        raise PermissionError(
            f"写入路径不在允许范围内: {path}\n允许的目录: {allowed}"
        )
    content = str(params.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"path": str(path), "written": len(content)}


def tool_json_parse(params: dict[str, Any]) -> Any:
    text = str(params.get("text", "{}"))
    return json.loads(text)


def tool_web_search(params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    # Uses DuckDuckGo instant answer endpoint, no key needed.
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        timeout=10,
    )
    data = resp.json()
    return {
        "query": query,
        "answer": data.get("AbstractText") or data.get("Answer") or "",
        "heading": data.get("Heading") or "",
    }


def tool_run_command(params: dict[str, Any]) -> dict[str, Any]:
    cmd = str(params.get("command", "")).strip()
    if not cmd:
        raise ValueError("command is required")
    _check_command_safety(cmd)
    # Restrict working directory to data/ by default for safety
    cwd = str(params.get("cwd", _ROOT / "data"))
    timeout = min(int(params.get("timeout", 20)), 60)
    proc = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        timeout=timeout, cwd=cwd,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-5000:],
        "stderr": proc.stderr[-2000:],
    }


def tool_capture_screenshot(params: dict[str, Any]) -> dict[str, Any]:
    if ImageGrab is None:
        raise RuntimeError("Pillow is required for screenshot capture")
    output = Path(str(params.get("output", "data/screenshot.png"))).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab()
    image.save(output)
    return {"path": str(output), "size": image.size}


def tool_ocr_image(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(params.get("path", ""))).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"image not found: {path}")
    ocr_status = check_ocr_availability()
    if not ocr_status["available"]:
        raise RuntimeError(f"OCR unavailable: {ocr_status['reason']} ({ocr_status['hint']})")
    image = Image.open(path)
    language = str(params.get("lang", "eng"))
    text = pytesseract.image_to_string(image, lang=language)
    return {
        "path": str(path),
        "lang": language,
        "text": text.strip()[:20000],
        "ocr": ocr_status,
    }


def tool_sqlite_query(params: dict[str, Any]) -> dict[str, Any]:
    db_path = Path(str(params.get("db_path", "data/agent.db"))).expanduser()
    query = str(params.get("query", "")).strip()
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    if not query:
        raise ValueError("query is required")

    normalized = query.lstrip().upper()
    if not (normalized.startswith("SELECT") or normalized.startswith("PRAGMA")):
        raise PermissionError("only SELECT/PRAGMA queries are allowed")

    limit = int(params.get("limit", 100))
    limit = max(1, min(limit, 500))

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchmany(limit)
        columns = list(rows[0].keys()) if rows else []
        data = [dict(row) for row in rows]
    return {"db_path": str(db_path), "query": query, "count": len(data), "columns": columns, "rows": data}


def tool_http_request(params: dict[str, Any]) -> dict[str, Any]:
    """Make an HTTP request to any URL. Supports GET/POST/PUT/DELETE."""
    url = str(params.get("url", "")).strip()
    if not url:
        raise ValueError("url is required")
    method = str(params.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}:
        raise ValueError(f"unsupported HTTP method: {method}")
    headers = params.get("headers") or {}
    if isinstance(headers, str):
        headers = json.loads(headers)
    body = params.get("body") or params.get("data")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    timeout = min(int(params.get("timeout", 15)), 60)

    resp = requests.request(
        method, url,
        headers=headers,
        json=body if isinstance(body, (dict, list)) else None,
        data=body if isinstance(body, str) else None,
        timeout=timeout,
        allow_redirects=True,
    )
    try:
        response_body = resp.json()
    except Exception:
        response_body = resp.text[:10000]

    return {
        "url": url,
        "method": method,
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": response_body,
    }
