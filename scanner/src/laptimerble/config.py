"""Race configuration and per-car defaults."""

from __future__ import annotations

from dataclasses import dataclass, field

NUM_CARS = 8
DEFAULT_NAMES: tuple[str, ...] = (
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
)
ADVERTISING_INTERVALS_MS: tuple[int, ...] = (20, 23, 29, 31, 37, 41, 43, 47)


def ble_local_name(car_index: int) -> str:
    """``LapTimer-1`` for car index 0, ``LapTimer-8`` for index 7."""
    return f"LapTimer-{car_index + 1}"


@dataclass
class RaceConfig:
    laps_target: int | None = 3  # None = unlimited (LD)
    rssi_threshold_dbm: int = -70
    lockout_seconds: float = 3.0
    drop_window_seconds: float = 0.3

    def with_laps_target(self, value: int | None) -> "RaceConfig":
        return RaceConfig(
            laps_target=value,
            rssi_threshold_dbm=self.rssi_threshold_dbm,
            lockout_seconds=self.lockout_seconds,
            drop_window_seconds=self.drop_window_seconds,
        )


@dataclass
class CarConfig:
    index: int  # 0..7
    display_name: str
    enabled: bool = False

    @property
    def number(self) -> int:
        return self.index + 1


def default_cars() -> list[CarConfig]:
    return [
        CarConfig(index=i, display_name=DEFAULT_NAMES[i], enabled=(i == 0))
        for i in range(NUM_CARS)
    ]
