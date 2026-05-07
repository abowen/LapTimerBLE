"""CSV export."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .config import CarConfig
from .models import RaceState
from .storage import Storage

EXPORT_DIR = Path("./exports")


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _ensure_dir(dest_dir: Path | None) -> Path:
    target = dest_dir or EXPORT_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def export_race(state: RaceState, cars: list[CarConfig], dest_dir: Path | None = None) -> Path:
    """Write the current race's laps to ``./exports/laps_<timestamp>.csv``."""
    target_dir = _ensure_dir(dest_dir)
    path = target_dir / f"laps_{_timestamp()}.csv"

    started = state.started_at.isoformat() if state.started_at else ""

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["race_started_at", "car_id", "car_name", "lap_index", "lap_seconds"])
        for car in cars:
            for lap in state.laps.get(car.index, []):
                writer.writerow(
                    [started, car.number, car.display_name, lap.lap_index, f"{lap.lap_seconds:.3f}"]
                )

    return path


def export_all_time(
    storage: Storage,
    cars: list[CarConfig],
    dest_dir: Path | None = None,
) -> Path:
    """Write every recorded lap to ``./exports/laps_alltime_<timestamp>.csv``."""
    target_dir = _ensure_dir(dest_dir)
    path = target_dir / f"laps_alltime_{_timestamp()}.csv"

    name_by_index = {c.index: c.display_name for c in cars}

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "race_started_at",
                "car_id",
                "car_name",
                "lap_index",
                "lap_seconds",
                "recorded_at",
            ]
        )
        for race_started, car_index, lap_index, lap_seconds, recorded_at in storage.all_laps():
            writer.writerow(
                [
                    race_started,
                    car_index + 1,
                    name_by_index.get(car_index, ""),
                    lap_index,
                    f"{lap_seconds:.3f}",
                    recorded_at,
                ]
            )

    return path
