"""Domain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Lap:
    car_index: int
    lap_index: int  # 1-based
    lap_seconds: float
    recorded_at: datetime


@dataclass
class RaceState:
    started_at: datetime | None = None
    finished_at: datetime | None = None
    laps: dict[int, list[Lap]] = field(default_factory=dict)  # by car_index

    def record(self, lap: Lap) -> None:
        self.laps.setdefault(lap.car_index, []).append(lap)

    def lap_count(self, car_index: int) -> int:
        return len(self.laps.get(car_index, []))

    def total_seconds_for(self, car_index: int) -> float:
        return sum(lap.lap_seconds for lap in self.laps.get(car_index, []))
