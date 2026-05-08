from datetime import datetime
from pathlib import Path

from laptimerble.config import default_cars
from laptimerble.export import export_all_time, export_race
from laptimerble.models import Lap, RaceState
from laptimerble.storage import Storage


def test_export_writes_csv(tmp_path: Path) -> None:
    cars = default_cars()
    cars[0].enabled = True
    cars[0].display_name = "Lightning"

    state = RaceState(started_at=datetime(2026, 5, 7, 14, 30, 0))
    state.record(Lap(car_index=0, lap_index=1, lap_seconds=12.345, recorded_at=datetime.now()))
    state.record(Lap(car_index=0, lap_index=2, lap_seconds=11.800, recorded_at=datetime.now()))

    path = export_race(state, cars, dest_dir=tmp_path)
    assert path.exists()

    content = path.read_text(encoding="utf-8").splitlines()
    assert content[0] == "race_started_at,car_id,car_name,lap_index,lap_seconds"
    assert "Lightning" in content[1]
    assert "12.345" in content[1]
    assert "11.800" in content[2]


def test_export_skips_cars_with_no_laps(tmp_path: Path) -> None:
    cars = default_cars()
    cars[1].enabled = True

    state = RaceState(started_at=datetime.now())
    state.record(Lap(car_index=1, lap_index=1, lap_seconds=10.0, recorded_at=datetime.now()))

    path = export_race(state, cars, dest_dir=tmp_path)
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2  # header + 1 data row


def test_export_all_time_includes_all_races(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "test.db")
    try:
        cars = default_cars()
        cars[0].display_name = "Lightning"
        cars[1].display_name = "Thunder"

        race1 = storage.start_race(datetime(2026, 5, 1, 10, 0, 0), 3)
        storage.record_lap(race1, 0, 1, 12.345, datetime(2026, 5, 1, 10, 0, 12))
        storage.record_lap(race1, 1, 1, 13.500, datetime(2026, 5, 1, 10, 0, 13))

        race2 = storage.start_race(datetime(2026, 5, 7, 14, 30, 0), 5)
        storage.record_lap(race2, 0, 1, 11.200, datetime(2026, 5, 7, 14, 30, 11))

        path = export_all_time(storage, cars, dest_dir=tmp_path)
        rows = path.read_text(encoding="utf-8").splitlines()
        assert rows[0] == "race_started_at,car_id,car_name,lap_index,lap_seconds,recorded_at"
        assert len(rows) == 4  # header + 3 laps
        # Check ordering is chronological by recorded_at
        assert "12.345" in rows[1]
        assert "13.500" in rows[2]
        assert "11.200" in rows[3]
        # Car names resolved
        assert "Lightning" in rows[1]
        assert "Thunder" in rows[2]
    finally:
        storage.close()


def test_export_all_time_empty_db(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "test.db")
    try:
        path = export_all_time(storage, default_cars(), dest_dir=tmp_path)
        rows = path.read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1  # header only
    finally:
        storage.close()
