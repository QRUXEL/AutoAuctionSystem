import sqlite3
import threading
import time

from .config import DATA

_lock = threading.RLock()
_conn = sqlite3.connect(DATA / "station.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,            -- capturing | processing | review | publishing | published | error
  source TEXT NOT NULL,            -- station | manual
  created_at REAL NOT NULL,
  captured_at REAL,
  last_file_at REAL,
  expected_photos INTEGER NOT NULL,
  expected_videos INTEGER NOT NULL,
  title TEXT DEFAULT '',
  description TEXT DEFAULT '',
  category_id INTEGER,
  category_path TEXT DEFAULT '',
  start_price INTEGER,
  buy_now_price INTEGER,
  duration_days INTEGER,
  ai_notes TEXT DEFAULT '',
  message TEXT DEFAULT '',
  publish_attempts INTEGER DEFAULT 0,
  tradera_request_id INTEGER,
  tradera_item_id INTEGER,
  published_at REAL
);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  kind TEXT NOT NULL,              -- photo | video
  seq INTEGER NOT NULL,
  raw TEXT NOT NULL,               -- paths relative to the items directory
  framed TEXT DEFAULT '',
  received_at REAL NOT NULL,
  crop TEXT DEFAULT '',            -- manual crop as JSON {x,y,w,h} fractions of the upright photo, or ''
  crop_auto TEXT DEFAULT ''        -- the automatic crop last used, same format
);
"""

# files.item_id = 0 means a loose shot (taken outside any capture window, e.g. a test shot)
MIGRATIONS = {"files": {"crop": "TEXT DEFAULT ''", "crop_auto": "TEXT DEFAULT ''",
                        "name": "TEXT DEFAULT ''",       # file name from the camera
                        "cam_time": "REAL",              # shot time on the camera's clock (see shottime.py)
                        "shot_at": "REAL",               # shot time converted to the server's clock
                        "adjust": "TEXT DEFAULT ''"}}    # per-photo brightness/contrast as JSON


def init():
    with _lock:
        _conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        _conn.commit()


def query(sql, *args):
    with _lock:
        rows = _conn.execute(sql, args).fetchall()
        _conn.commit()
        return [dict(r) for r in rows]


def one(sql, *args):
    rows = query(sql, *args)
    return rows[0] if rows else None


def run(sql, *args) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def update_item(item_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    run(f"UPDATE items SET {cols} WHERE id = ?", *fields.values(), item_id)


def lock():
    return _lock


def now() -> float:
    return time.time()
