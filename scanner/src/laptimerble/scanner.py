"""BLE scanner + per-car RSSI peak detector.

The detector logic is independent of bleak and unit-tested via ``feed()``;
``BleScanner`` is a thin async wrapper that pumps advertisement callbacks into
the right per-car detector.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import NUM_CARS, ble_local_name

log = logging.getLogger(__name__)

# Timestamp of a detected pass, in monotonic seconds (matches ``time.monotonic``).
PassCallback = Callable[[int, float], None]  # (car_index, peak_t)


@dataclass
class PeakDetector:
    """Threshold + lockout peak detector for one car.

    Feed it ``(rssi_dbm, monotonic_t)`` samples in time order. It returns the
    timestamp at which a complete pass was detected, otherwise ``None``.

    The detector is idle until RSSI rises above ``rssi_threshold``. Once in a
    pass window it tracks the strongest sample seen. The window closes (and a
    pass is emitted) when RSSI has been below the threshold for at least
    ``drop_window_seconds``. After emission, it ignores everything for
    ``lockout_seconds``.
    """

    rssi_threshold: int = -70
    lockout_seconds: float = 3.0
    drop_window_seconds: float = 0.3

    in_window: bool = False
    window_peak_rssi: int = -200
    window_peak_t: float = 0.0
    last_above_t: float = 0.0
    last_emit_t: float = -1e9  # so first call is always allowed

    def reset(self) -> None:
        self.in_window = False
        self.window_peak_rssi = -200
        self.window_peak_t = 0.0
        self.last_above_t = 0.0
        self.last_emit_t = -1e9

    def feed(self, rssi: int, t: float) -> Optional[float]:
        # Lockout: silently consume everything until lockout has elapsed
        if (t - self.last_emit_t) < self.lockout_seconds:
            return None

        if rssi >= self.rssi_threshold:
            self.last_above_t = t
            if not self.in_window:
                self.in_window = True
                self.window_peak_rssi = rssi
                self.window_peak_t = t
            elif rssi > self.window_peak_rssi:
                self.window_peak_rssi = rssi
                self.window_peak_t = t
            return None

        # Below threshold: see if the open window has been quiet long enough
        if self.in_window and (t - self.last_above_t) >= self.drop_window_seconds:
            peak_t = self.window_peak_t
            self.in_window = False
            self.window_peak_rssi = -200
            self.last_emit_t = peak_t
            return peak_t
        return None


@dataclass
class CarDetectorRegistry:
    """One ``PeakDetector`` per car index, configured uniformly."""

    rssi_threshold: int = -70
    lockout_seconds: float = 3.0
    drop_window_seconds: float = 0.3

    detectors: list[PeakDetector] = field(default_factory=list)
    # Latest (rssi_dbm, monotonic_t) per car — overwritten on every feed,
    # used by the UI to display live signal strength.
    latest_samples: list[Optional[tuple[int, float]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.detectors:
            self.detectors = [
                PeakDetector(
                    rssi_threshold=self.rssi_threshold,
                    lockout_seconds=self.lockout_seconds,
                    drop_window_seconds=self.drop_window_seconds,
                )
                for _ in range(NUM_CARS)
            ]
        if not self.latest_samples:
            self.latest_samples = [None] * NUM_CARS

    def reconfigure(
        self,
        rssi_threshold: int | None = None,
        lockout_seconds: float | None = None,
    ) -> None:
        if rssi_threshold is not None:
            self.rssi_threshold = rssi_threshold
        if lockout_seconds is not None:
            self.lockout_seconds = lockout_seconds
        for d in self.detectors:
            if rssi_threshold is not None:
                d.rssi_threshold = rssi_threshold
            if lockout_seconds is not None:
                d.lockout_seconds = lockout_seconds

    def reset_all(self) -> None:
        for d in self.detectors:
            d.reset()
        self.latest_samples = [None] * NUM_CARS

    def feed(self, car_index: int, rssi: int, t: float) -> Optional[float]:
        self.latest_samples[car_index] = (rssi, t)
        return self.detectors[car_index].feed(rssi, t)


class BleScanner:
    """Async wrapper around ``bleak`` that fans advertisements out to detectors.

    Importing ``bleak`` is deferred so the rest of the app stays usable (and
    testable) on a machine without Bluetooth.
    """

    def __init__(
        self,
        registry: CarDetectorRegistry,
        on_pass: PassCallback,
    ) -> None:
        self.registry = registry
        self.on_pass = on_pass
        self._scanner = None
        self._enabled_cars: set[int] = set()
        self._name_to_index: dict[str, int] = {
            ble_local_name(i): i for i in range(NUM_CARS)
        }

    def set_enabled(self, indices: set[int]) -> None:
        self._enabled_cars = set(indices)

    async def start(self) -> None:
        from bleak import BleakScanner  # noqa: WPS433

        loop = asyncio.get_running_loop()

        def _detection_callback(device, advertisement_data) -> None:  # type: ignore[no-untyped-def]
            name = advertisement_data.local_name or device.name
            if not name:
                return
            idx = self._name_to_index.get(name)
            if idx is None or idx not in self._enabled_cars:
                return
            rssi = advertisement_data.rssi
            if rssi is None:
                return
            t = loop.time()
            peak_t = self.registry.feed(idx, int(rssi), t)
            if peak_t is not None:
                self.on_pass(idx, peak_t)

        self._scanner = BleakScanner(detection_callback=_detection_callback)
        await self._scanner.start()
        log.info("BLE scanner started")

    async def stop(self) -> None:
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            except Exception:  # noqa: BLE001
                log.exception("Error stopping BLE scanner")
            self._scanner = None
