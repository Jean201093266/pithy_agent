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
        raise FileNotFoundError(f"file not found: {path}")
    return {"path": str(path), "content": path.read_text(encoding="utf-8")[:5000]}


def tool_echo(params: dict[str, Any]) -> dict[str, Any]:
    message = str(params.get("message", params.get("text", "")))
    return {"message": message, "params": params}


def tool_write_file(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(params.get("path", "")).expanduser()
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
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-5000:],
        "stderr": proc.stderr[-5000:],
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


