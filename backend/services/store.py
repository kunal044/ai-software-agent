import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

WORKSPACE = Path(
    os.getenv("ENGINEER_WORKSPACE") or Path(__file__).resolve().parents[1] / "workspace"
).resolve()


@contextmanager
def database():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(WORKSPACE / "review.sqlite3", timeout=30)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, repo TEXT NOT NULL, payload TEXT NOT NULL
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS records_lookup ON records(kind, repo)"
            )
            yield conn
    finally:
        conn.close()


def save(conn, kind, data):
    conn.execute(
        """INSERT INTO records VALUES (?, ?, ?, ?)
                 ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
        (data["id"], kind, data.get("repo_id", data["id"]), json.dumps(data)),
    )


def get(conn, kind, identifier):
    row = conn.execute(
        "SELECT payload FROM records WHERE kind=? AND id=?", (kind, identifier)
    ).fetchone()
    return json.loads(row[0]) if row else None


def latest(conn, kind, repo):
    row = conn.execute(
        "SELECT payload FROM records WHERE kind=? AND repo=? ORDER BY rowid DESC LIMIT 1",
        (kind, repo),
    ).fetchone()
    return json.loads(row[0]) if row else None


def records(conn, kind, repo=None, exclude=()):
    projection = "payload"
    if exclude:
        placeholders = ", ".join("?" for _ in exclude)
        projection = f"json_remove(payload, {placeholders})"
    rows = conn.execute(
        f"SELECT {projection} FROM records WHERE kind=? AND (? IS NULL OR repo=?) ORDER BY rowid DESC",
        (*("$." + key for key in exclude), kind, repo, repo),
    )
    return [json.loads(row[0]) for row in rows]
