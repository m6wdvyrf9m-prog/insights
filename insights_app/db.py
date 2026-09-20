from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .cards import seed_cards


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "migrations" / "001_schema.sql"


def connect(database_path: Path | str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(database_path: Path | str) -> None:
    with connect(database_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        seed_reference_data(conn)


@contextmanager
def transaction(database_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = connect(database_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def seed_reference_data(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO activities(slug, name, description)
        VALUES ('colour-card-game', 'Colour Card Game', 'Participants keep or gift colour-preference behaviour cards.')
        ON CONFLICT(slug) DO UPDATE SET
          name = excluded.name,
          description = excluded.description
        """
    )
    for colour, ordinal, content in seed_cards():
        conn.execute(
            """
            INSERT INTO card_bank(colour, ordinal, content, replaceable_seed, active)
            VALUES (?, ?, ?, 1, 1)
            ON CONFLICT(colour, ordinal) DO UPDATE SET
              content = excluded.content,
              replaceable_seed = 1,
              active = 1
            """,
            (colour, ordinal, content),
        )


def ensure_admin_user(conn: sqlite3.Connection, admin_password_hash: str) -> None:
    if not admin_password_hash:
        return
    conn.execute(
        """
        INSERT INTO users(username, password_hash, role, active)
        VALUES ('admin', ?, 'admin', 1)
        ON CONFLICT(username) DO UPDATE SET
          password_hash = excluded.password_hash,
          role = 'admin',
          active = 1,
          updated_at = CURRENT_TIMESTAMP
        """,
        (admin_password_hash,),
    )


def get_activity_id(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM activities WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise LookupError(f"Activity not found: {slug}")
    return int(row["id"])
