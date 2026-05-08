from datetime import datetime, timedelta
from pathlib import Path

import pytest

from laptimerble.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def test_bootstrap_creates_default_cars(storage: Storage) -> None:
    cars = storage.load_cars()
    assert len(cars) == 8
    indices = [idx for idx, _, _ in cars]
    assert indices == list(range(8))
    # Car 1 is enabled by default
    enabled_map = {idx: enabled for idx, _, enabled in cars}
    assert enabled_map[0] is True
    assert all(not enabled_map[i] for i in range(1, 8))


def test_set_car_name_persists(storage: Storage) -> None:
    storage.set_car_name(2, "Lightning")
    cars = dict((idx, name) for idx, name, _ in storage.load_cars())
    assert cars[2] == "Lightning"


def test_set_car_enabled_persists(storage: Storage) -> None:
    storage.set_car_enabled(3, True)
    cars = {idx: enabled for idx, _, enabled in storage.load_cars()}
    assert cars[3] is True


def test_top_today_includes_recorded_at(storage: Storage) -> None:
    started = datetime(2026, 5, 8, 14, 30, 0)
    race_id = storage.start_race(started, 3)
    storage.record_lap(race_id, 0, 1, 5.123, started)

    top = storage.top_today(0, today=started.date())
    assert len(top) == 1
    lap_seconds, recorded_at = top[0]
    assert lap_seconds == 5.123
    assert recorded_at.startswith("2026-05-08T14:30:00")


def test_record_lap_and_top_today(storage: Storage) -> None:
    started = datetime.now()
    race_id = storage.start_race(started, 3)
    times = [12.500, 11.800, 12.100, 13.000, 11.500, 11.700]
    for i, secs in enumerate(times, 1):
        storage.record_lap(
            race_id=race_id,
            car_index=0,
            lap_index=i,
            lap_seconds=secs,
            recorded_at=started + timedelta(seconds=secs * i),
        )

    top = storage.top_today(0, today=started.date())
    assert [s for s, _ in top] == sorted(times)[:5]


def test_top_today_filters_by_date(storage: Storage) -> None:
    started = datetime(2025, 1, 1, 12, 0, 0)
    race_id = storage.start_race(started, 3)
    storage.record_lap(race_id, 0, 1, 10.0, started)
    # Different day:
    storage.record_lap(race_id, 0, 2, 8.0, datetime(2025, 1, 2, 12, 0, 0))

    top = storage.top_today(0, today=started.date())
    assert [s for s, _ in top] == [10.0]


def test_clear_car_only_clears_one(storage: Storage) -> None:
    started = datetime.now()
    race_id = storage.start_race(started, 3)
    storage.record_lap(race_id, 0, 1, 10.0, started)
    storage.record_lap(race_id, 1, 1, 11.0, started)

    storage.clear_car(0)
    assert storage.top_today(0) == []
    assert [s for s, _ in storage.top_today(1)] == [11.0]


def test_top_overall_returns_fastest_across_cars(storage: Storage) -> None:
    started = datetime(2026, 5, 7, 12, 0, 0)
    race_id = storage.start_race(started, 5)
    # Mixed cars and times. Slowest first to confirm ordering.
    samples = [
        (0, 1, 15.000),
        (1, 1, 9.250),
        (2, 1, 12.700),
        (0, 2, 11.000),
        (1, 2, 9.100),
        (2, 2, 13.500),
        (0, 3, 10.200),
        (1, 3, 9.500),
    ]
    for car_index, lap_index, lap_seconds in samples:
        storage.record_lap(race_id, car_index, lap_index, lap_seconds, started)

    top = storage.top_overall(limit=5)
    assert [secs for _, secs, _ in top] == sorted(s for _, _, s in samples)[:5]
    # Fastest belongs to car 1 (index 1)
    assert top[0][0] == 1


def test_top_overall_empty(storage: Storage) -> None:
    assert storage.top_overall() == []


def test_clear_all_wipes_history(storage: Storage) -> None:
    started = datetime.now()
    race_id = storage.start_race(started, 3)
    storage.record_lap(race_id, 0, 1, 10.0, started)
    storage.record_lap(race_id, 1, 1, 11.0, started)

    storage.clear_all()
    assert storage.top_today(0) == []
    assert storage.top_today(1) == []
