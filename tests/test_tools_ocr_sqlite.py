from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.tools.builtin as builtin
from app.tools.builtin import check_ocr_availability, tool_ocr_image, tool_sqlite_query


def test_sqlite_query_select_only(tmp_path: Path) -> None:
    db_path = tmp_path / 'sample.db'
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute('INSERT INTO items(name) VALUES(?)', ('alpha',))
        conn.commit()

    result = tool_sqlite_query({'db_path': str(db_path), 'query': 'SELECT id, name FROM items'})
    assert result['count'] == 1
    assert result['rows'][0]['name'] == 'alpha'


def test_sqlite_query_blocks_write(tmp_path: Path) -> None:
    db_path = tmp_path / 'sample.db'
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()

    with pytest.raises(PermissionError):
        tool_sqlite_query({'db_path': str(db_path), 'query': "DELETE FROM items"})


def test_ocr_tool_missing_image() -> None:
    with pytest.raises(FileNotFoundError):
        tool_ocr_image({'path': 'not_exists.png'})


def test_ocr_availability_has_required_keys() -> None:
    status = check_ocr_availability()
    assert 'available' in status
    assert 'reason' in status
    assert 'hint' in status


def test_ocr_tool_reports_unavailable_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / 'sample.png'
    image_path.write_bytes(b'not-a-real-image')

    monkeypatch.setattr(builtin, 'Image', object())
    monkeypatch.setattr(builtin, 'pytesseract', None)
    with pytest.raises(RuntimeError):
        tool_ocr_image({'path': str(image_path)})


