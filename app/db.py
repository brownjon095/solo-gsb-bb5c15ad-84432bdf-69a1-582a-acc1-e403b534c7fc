"""SQLite 数据访问层。数据目录由环境变量 DATA_DIR 控制（默认 ./data）。"""
from __future__ import annotations

import os
import sqlite3

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DATA_DIR = os.path.abspath(DATA_DIR)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calibrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id        INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT 'mm',
    pick_sigma_px   REAL NOT NULL DEFAULT 1.0,
    points_json     TEXT NOT NULL,
    homography_json TEXT NOT NULL,
    covariance_json TEXT NOT NULL,
    residuals_json  TEXT NOT NULL,
    rms             REAL,
    max_res         REAL,
    sigma2          REAL,
    dof             INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS measurements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id       INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    calibration_id INTEGER NOT NULL REFERENCES calibrations(id) ON DELETE CASCADE,
    label          TEXT NOT NULL DEFAULT '',
    mtype          TEXT NOT NULL CHECK (mtype IN ('line', 'polygon')),
    points_json    TEXT NOT NULL,
    value          REAL NOT NULL,
    uncertainty    REAL NOT NULL,
    unit           TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
