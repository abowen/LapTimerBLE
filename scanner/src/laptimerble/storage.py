"""SQLite persistence for cars, races and laps."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Iterator

from .config import NUM_CARS, DEFAULT_NAMES

DEFAULT_DB_PATH = Path.home() / ".laptimerble" / "laps.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cars (
    car_index    INTEGER PRIMARY KEY CHECK(car_index BETWEEN 0 AND 7),
    display_name TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS races (
    race_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    laps_target  INTEGER
);

CREATE TABLE IF NOT EXISTS laps (
    lap_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id      INTEGER NOT NULL REFERENCES races(race_id) ON DELETE CASCADE,
    car_index    INTEGER NOT NULL,
    lap_index    INTEGER NOT NULL,
    lap_seconds  REAL NOT NULL,
    recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_laps_car_date
    ON laps(car_index, recorded_at);
"""


class Storage:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._bootstrap()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _bootstrap(self) -> None:
        with self._tx() as c:
            c.executescript(SCHEMA)
            existing = {row["car_index"] for row in c.execute("SELECT car_index FROM cars")}
            for i in range(NUM_CARS):
                if i not in existing:
                    c.execute(
                        "INSERT INTO cars(car_index, display_name, enabled) VALUES (?, ?, ?)",
                        (i, DEFAULT_NAMES[i], 1 if i == 0 else 0),
                    )

    # --- cars -----------------------------------------------------------

    def load_cars(self) -> list[tuple[int, str, bool]]:
        rows = self._conn.execute(
            "SELECT car_index, display_name, enabled FROM cars ORDER BY car_index"
        ).fetchall()
        return [(r["car_index"], r["display_name"], bool(r["enabled"])) for r in rows]

    def set_car_name(self, car_index: int, name: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE cars SET display_name = ? WHERE car_index = ?",
                (name, car_index),
            )

    def set_car_enabled(self, car_index: int, enabled: bool) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE cars SET enabled = ? WHERE car_index = ?",
                (1 if enabled else 0, car_index),
            )

    # --- races / laps ---------------------------------------------------

    def start_race(self, started_at: datetime, laps_target: int | None) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO races(started_at, laps_target) VALUES (?, ?)",
                (started_at.isoformat(), laps_target),
            )
            return int(cur.lastrowid)

    def finish_race(self, race_id: int, finished_at: datetime) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE races SET finished_at = ? WHERE race_id = ?",
                (finished_at.isoformat(), race_id),
            )

    def record_lap(
        self,
        race_id: int,
        car_index: int,
        lap_index: int,
        lap_seconds: float,
        recorded_at: datetime,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO laps(race_id, car_index, lap_index, lap_seconds, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (race_id, car_index, lap_index, lap_seconds, recorded_at.isoformat()),
            )

    def top_today(self, car_index: int, today: date | None = None, limit: int = 5) -> list[tuple[float, str]]:
        target = (today or date.today()).isoformat()
        rows = self._conn.execute(
            """
            SELECT lap_seconds, recorded_at FROM laps
             WHERE car_index = ? AND substr(recorded_at, 1, 10) = ?
             ORDER BY lap_seconds ASC
             LIMIT ?
            """,
            (car_index, target, limit),
        ).fetchall()
        return [(row["lap_seconds"], row["recorded_at"]) for row in rows]

    def top_overall(self, limit: int = 10) -> list[tuple[int, float, str]]:
        """Top ``limit`` fastest laps across all cars, all-time.

        Returns rows of ``(car_index, lap_seconds, recorded_at)`` ordered by
        ``lap_seconds`` ascending.
        """
        rows = self._conn.execute(
            """
            SELECT car_index, lap_seconds, recorded_at
              FROM laps
             ORDER BY lap_seconds ASC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            (row["car_index"], row["lap_seconds"], row["recorded_at"]) for row in rows
        ]

    def clear_car(self, car_index: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM laps WHERE car_index = ?", (car_index,))

    def clear_all(self) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM laps")
            c.execute("DELETE FROM races")

    def laps_for_race(self, race_id: int) -> list[tuple[int, int, float, str]]:
        rows = self._conn.execute(
            """
            SELECT car_index, lap_index, lap_seconds, recorded_at
              FROM laps
             WHERE race_id = ?
             ORDER BY recorded_at
            """,
            (race_id,),
        ).fetchall()
        return [
            (r["car_index"], r["lap_index"], r["lap_seconds"], r["recorded_at"])
            for r in rows
        ]

    def all_laps(self) -> list[tuple[str, int, int, float, str]]:
        """Every lap ever recorded, joined to its race start time.

        Returns rows of ``(race_started_at, car_index, lap_index, lap_seconds,
        recorded_at)`` ordered chronologically.
        """
        rows = self._conn.execute(
            """
            SELECT r.started_at AS race_started_at,
                   l.car_index, l.lap_index, l.lap_seconds, l.recorded_at
              FROM laps l
              JOIN races r ON r.race_id = l.race_id
          ORDER BY l.recorded_at
            """
        ).fetchall()
        return [
            (
                r["race_started_at"],
                r["car_index"],
                r["lap_index"],
                r["lap_seconds"],
                r["recorded_at"],
            )
            for r in rows
        ]
