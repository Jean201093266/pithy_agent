from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any


class AppDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tool_state (
                    name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS skill_versions (
                    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api_save',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(skill_id) REFERENCES skills(id)
                );
                CREATE INDEX IF NOT EXISTS idx_skill_versions_skill_id
                    ON skill_versions(skill_id, version_id DESC);
                CREATE TABLE IF NOT EXISTS custom_tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    manifest_json TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    server_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversation_state (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    memory_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    importance REAL NOT NULL DEFAULT 0.5,
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    access_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_memory_items_session
                    ON memory_items(session_id, created_at DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "session_id" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id, id DESC)"
            )

    def get_kv(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_kv(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO kv_store(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def add_message(self, role: str, content: str, session_id: str = "default") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversations(session_id, role, content) VALUES(?, ?, ?)",
                (session_id or "default", role, content),
            )

    def list_messages(self, limit: int = 30, session_id: str = "default") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id or "default", limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_conversation_summary(self, session_id: str = "default") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT summary FROM conversation_summaries WHERE session_id = ?",
                (session_id or "default",),
            ).fetchone()
        return "" if row is None else str(row["summary"])

    def save_conversation_summary(self, summary: str, session_id: str = "default") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversation_summaries(session_id, summary) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET summary = excluded.summary, updated_at = CURRENT_TIMESTAMP",
                (session_id or "default", summary),
            )

    def get_conversation_state(self, session_id: str = "default") -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM conversation_state WHERE session_id = ?",
                (session_id or "default",),
            ).fetchone()
        if row is None:
            return {}
        try:
            parsed = json.loads(row["state_json"])
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def save_conversation_state(self, state: dict[str, Any], session_id: str = "default") -> None:
        serialized = json.dumps(state, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversation_state(session_id, state_json) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, updated_at = CURRENT_TIMESTAMP",
                (session_id or "default", serialized),
            )

    def add_memory_item(
        self,
        memory_type: str,
        text: str,
        payload: dict[str, Any] | None = None,
        importance: float = 0.5,
        embedding: list[float] | None = None,
        session_id: str = "default",
    ) -> int:
        safe_importance = max(0.0, min(float(importance), 1.0))
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        embedding_json = json.dumps(embedding or [], ensure_ascii=False)
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO memory_items(session_id, memory_type, text, payload_json, importance, embedding_json) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (session_id or "default", memory_type, text, payload_json, safe_importance, embedding_json),
            )
            return int(cur.lastrowid)

    def list_memory_items(self, session_id: str = "default", limit: int = 300) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, memory_type, text, payload_json, importance, embedding_json, access_count, "
                "created_at, updated_at FROM memory_items WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id or "default", max(1, min(limit, 2000))),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "memory_type": row["memory_type"],
                "text": row["text"],
                "payload": json.loads(row["payload_json"] or "{}"),
                "importance": float(row["importance"]),
                "embedding": json.loads(row["embedding_json"] or "[]"),
                "access_count": int(row["access_count"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def touch_memory_items(self, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        with self.connect() as conn:
            conn.executemany(
                "UPDATE memory_items SET access_count = access_count + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [(mid,) for mid in memory_ids],
            )

    def delete_memory_item(self, memory_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM memory_items WHERE id = ?", (memory_id,))

    def find_similar_memories(
        self,
        query_embedding: list[float],
        session_id: str = "default",
        top_k: int = 6,
        min_importance: float = 0.2,
    ) -> list[dict[str, Any]]:
        items = self.list_memory_items(session_id=session_id, limit=800)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            if float(item.get("importance", 0.0)) < min_importance:
                continue
            emb = item.get("embedding")
            if not isinstance(emb, list) or not emb:
                continue
            sim = self._cosine_similarity(query_embedding, [float(x) for x in emb])
            recency_bonus = 1.0 / (1.0 + math.log1p(max(item.get("access_count", 0), 0)))
            score = sim * 0.85 + float(item["importance"]) * 0.1 + recency_bonus * 0.05
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[: max(1, min(top_k, 20))]]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        size = min(len(a), len(b))
        if size == 0:
            return 0.0
        dot = sum(float(a[i]) * float(b[i]) for i in range(size))
        norm_a = math.sqrt(sum(float(a[i]) * float(a[i]) for i in range(size)))
        norm_b = math.sqrt(sum(float(b[i]) * float(b[i]) for i in range(size)))
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def set_tool_enabled(self, name: str, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tool_state(name, enabled) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
                (name, int(enabled)),
            )

    def is_tool_enabled(self, name: str, default: bool = True) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT enabled FROM tool_state WHERE name = ?", (name,)).fetchone()
        return default if row is None else bool(row["enabled"])

    def upsert_skill(self, name: str, version: str, spec: dict[str, Any], source: str = "api_save") -> int:
        serialized = json.dumps(spec, ensure_ascii=False)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM skills WHERE name = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO skills(name, version, spec_json) VALUES(?, ?, ?)",
                    (name, version, serialized),
                )
                skill_id = int(cur.lastrowid)
                self._add_skill_version_conn(conn, skill_id, name, version, serialized, source)
                return skill_id
            conn.execute(
                "UPDATE skills SET version = ?, spec_json = ? WHERE id = ?",
                (version, serialized, row["id"]),
            )
            skill_id = int(row["id"])
            self._add_skill_version_conn(conn, skill_id, name, version, serialized, source)
            return skill_id

    def _add_skill_version_conn(
        self,
        conn: sqlite3.Connection,
        skill_id: int,
        name: str,
        version: str,
        spec_json: str,
        source: str,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO skill_versions(skill_id, name, version, spec_json, source) VALUES(?, ?, ?, ?, ?)",
            (skill_id, name, version, spec_json, source),
        )
        return int(cur.lastrowid)

    def list_skill_versions(self, skill_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT version_id, skill_id, name, version, spec_json, source, created_at "
                "FROM skill_versions WHERE skill_id = ? ORDER BY version_id DESC",
                (skill_id,),
            ).fetchall()
        return [
            {
                "version_id": row["version_id"],
                "skill_id": row["skill_id"],
                "name": row["name"],
                "version": row["version"],
                "spec": json.loads(row["spec_json"]),
                "source": row["source"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_skill_version(self, skill_id: int, version_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT version_id, skill_id, name, version, spec_json, source, created_at "
                "FROM skill_versions WHERE skill_id = ? AND version_id = ?",
                (skill_id, version_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "version_id": row["version_id"],
            "skill_id": row["skill_id"],
            "name": row["name"],
            "version": row["version"],
            "spec": json.loads(row["spec_json"]),
            "source": row["source"],
            "created_at": row["created_at"],
        }

    def rollback_skill(self, skill_id: int, target_version_id: int, reason: str = "") -> dict[str, Any]:
        with self.connect() as conn:
            skill_row = conn.execute(
                "SELECT id, name, version, spec_json FROM skills WHERE id = ?",
                (skill_id,),
            ).fetchone()
            if skill_row is None:
                raise KeyError(f"skill not found: {skill_id}")

            target_row = conn.execute(
                "SELECT version_id, name, version, spec_json FROM skill_versions WHERE skill_id = ? AND version_id = ?",
                (skill_id, target_version_id),
            ).fetchone()
            if target_row is None:
                raise KeyError(f"skill version not found: {target_version_id}")

            current_version = skill_row["version"]
            conn.execute(
                "UPDATE skills SET version = ?, spec_json = ? WHERE id = ?",
                (target_row["version"], target_row["spec_json"], skill_id),
            )
            source = "rollback" if not reason else f"rollback:{reason}"
            new_version_id = self._add_skill_version_conn(
                conn,
                skill_id,
                target_row["name"],
                target_row["version"],
                target_row["spec_json"],
                source,
            )

        return {
            "version_id": new_version_id,
            "rollback_from_version": current_version,
            "rollback_to_version": target_row["version"],
        }

    def list_skills(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, version, spec_json, created_at FROM skills ORDER BY id DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "version": row["version"],
                "spec": json.loads(row["spec_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_skill(self, skill_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name, version, spec_json FROM skills WHERE id = ?", (skill_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "spec": json.loads(row["spec_json"]),
        }

    def upsert_custom_tool(self, name: str, manifest: dict[str, Any]) -> int:
        serialized = json.dumps(manifest, ensure_ascii=False)
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM custom_tools WHERE name = ?", (name,)).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO custom_tools(name, manifest_json) VALUES(?, ?)",
                    (name, serialized),
                )
                return int(cur.lastrowid)
            conn.execute(
                "UPDATE custom_tools SET manifest_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (serialized, row["id"]),
            )
            return int(row["id"])

    def list_custom_tools(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, manifest_json, created_at, updated_at FROM custom_tools ORDER BY id DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "manifest": json.loads(row["manifest_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_custom_tool(self, name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name, manifest_json, created_at, updated_at FROM custom_tools WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "manifest": json.loads(row["manifest_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------
    # MCP server persistence
    # ------------------------------------------------------------------

    def upsert_mcp_server(self, server_id: str, config: dict[str, Any], enabled: bool = True) -> None:
        serialized = json.dumps(config, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO mcp_servers(server_id, config_json, enabled) VALUES(?, ?, ?) "
                "ON CONFLICT(server_id) DO UPDATE SET config_json = excluded.config_json, "
                "enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP",
                (server_id, serialized, int(enabled)),
            )

    def delete_mcp_server(self, server_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM mcp_servers WHERE server_id = ?", (server_id,))
        return cur.rowcount > 0

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT server_id, config_json, enabled, created_at, updated_at FROM mcp_servers ORDER BY created_at"
            ).fetchall()
        return [
            {
                "server_id": row["server_id"],
                "config": json.loads(row["config_json"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_mcp_server(self, server_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT server_id, config_json, enabled, created_at FROM mcp_servers WHERE server_id = ?",
                (server_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "server_id": row["server_id"],
            "config": json.loads(row["config_json"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }

