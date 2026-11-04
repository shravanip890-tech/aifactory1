"""SQLite access layer."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import get_settings
from .models import STATUS_QUEUED, row_to_project, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL, category TEXT NOT NULL,
    template TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', features TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL, error TEXT, checks TEXT NOT NULL DEFAULT '[]', output_dir TEXT,
    file_count INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER, is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_files (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, kind TEXT NOT NULL, message TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect():
        pass


def add_event(conn, project_id: Optional[str], kind: str, message: str, is_demo: bool = False) -> None:
    conn.execute("INSERT INTO events (project_id, kind, message, is_demo, created_at) VALUES (?,?,?,?,?)",
                 (project_id, kind, message, int(is_demo), utcnow()))


def insert_project(conn, *, name, slug, category, template, description, features, is_demo=False) -> dict:
    pid, now = uuid.uuid4().hex[:12], utcnow()
    conn.execute(
        "INSERT INTO projects (id,name,slug,category,template,description,features,status,is_demo,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, slug, category, template, description, json.dumps(features), STATUS_QUEUED, int(is_demo), now, now))
    add_event(conn, pid, "created", f"Project '{name}' created", is_demo)
    return get_project(conn, pid)


def get_project(conn, pid: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return row_to_project(row) if row else None


def get_output_dir(conn, pid: str) -> Optional[str]:
    row = conn.execute("SELECT output_dir FROM projects WHERE id=?", (pid,)).fetchone()
    return row["output_dir"] if row else None


def list_projects(conn, *, category=None, status=None, search=None, include_demo=True) -> list[dict]:
    q, args = "SELECT * FROM projects WHERE 1=1", []
    if category:
        q += " AND category=?"; args.append(category)
    if status:
        q += " AND status=?"; args.append(status)
    if search:
        q += " AND (name LIKE ? OR description LIKE ?)"; args += [f"%{search}%"] * 2
    if not include_demo:
        q += " AND is_demo=0"
    q += " ORDER BY created_at DESC, rowid DESC"
    return [row_to_project(r) for r in conn.execute(q, args)]


def update_project(conn, pid: str, **fields) -> None:
    for key in ("features", "checks"):
        if key in fields:
            fields[key] = json.dumps(fields[key])
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE projects SET {cols} WHERE id=?", (*fields.values(), pid))


def replace_files(conn, pid: str, files: list[dict]) -> None:
    conn.execute("DELETE FROM project_files WHERE project_id=?", (pid,))
    conn.executemany("INSERT INTO project_files VALUES (?,?,?,?)",
                     [(pid, f["path"], f["size"], f["sha256"]) for f in files])


def get_files(conn, pid: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT path,size,sha256 FROM project_files WHERE project_id=? ORDER BY path", (pid,))]


def delete_project(conn, pid: str) -> None:
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.execute("DELETE FROM events WHERE project_id=?", (pid,))
