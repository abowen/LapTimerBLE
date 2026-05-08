"""BLE scanner + per-car RSSI peak detector.

The detector logic is independent of bleak and unit-tested via ``feed()``;
``BleScanner`` is a thin async wrapper that pumps advertisement callbacks into
the right per-car detector.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from .config import NUM_CARS, ble_local_name

# Number of most-recent (rssi, monotonic_t) samples kept per car for the Debug
# screen. Sized to comfortably exceed one screen of rows.
DEBUG_BUFFER_SIZE = 20

log = logging.getLogger(__name__)

# Timestamp of a detected pass, in monotonic seconds (matches ``time.monotonic``).
PassCallback = Callable[[int, float], None]  # (car_index, peak_t)


def _advertisement_monitor_offload_supported(adapter: str = "hci0") -> bool:
    """Does ``org.bluez.AdvertisementMonitorManager1.SupportedFeatures`` list anything?

    Empty list → the BT controller cannot offload OR-pattern matching to
    hardware. Without offload, BlueZ accepts an OrPattern monitor but never
    runs a scan to source matches from, so the monitor produces 0 callbacks.
    Use ``busctl`` rather than dragging in dbus-fast/dbus-next at module load
    — this is a one-shot startup probe.
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "busctl",
                "--system",
                "get-property",
                "org.bluez",
                f"/org/bluez/{adapter}",
                "org.bluez.AdvertisementMonitorManager1",
                "SupportedFeatures",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:  # noqa: BLE001
        return False
    if result.returncode != 0:
        return False
    # Output looks like  as 2 "controller-patterns" "..."  or  as 0
    parts = result.stdout.strip().split(maxsplit=2)
    if len(parts) < 2 or parts[0] != "as":
        return False
    try:
        return int(parts[1]) > 0
    except ValueError:
        return False


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

    rssi_threshold: int = -100
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

    rssi_threshold: int = -100
    lockout_seconds: float = 3.0
    drop_window_seconds: float = 0.3

    detectors: list[PeakDetector] = field(default_factory=list)
    # Latest (rssi_dbm, monotonic_t) per car — overwritten on every sample,
    # used by the UI to display live signal strength.
    latest_samples: list[Optional[tuple[int, float]]] = field(default_factory=list)
    # Ring buffer of the last DEBUG_BUFFER_SIZE samples per car for the Debug
    # screen. Populated by record_sample regardless of enabled state.
    recent_samples: list[Deque[tuple[int, float]]] = field(default_factory=list)

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
        if not self.recent_samples:
            self.recent_samples = [deque(maxlen=DEBUG_BUFFER_SIZE) for _ in range(NUM_CARS)]

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
        for buf in self.recent_samples:
            buf.clear()

    def record_sample(self, car_index: int, rssi: int, t: float) -> None:
        """Record a sample without running the peak detector.

        Called for every advertisement matching a known car name, even when the
        car is disabled — this keeps the Debug screen useful as a "is the
        transponder talking?" check before enabling.
        """
        self.latest_samples[car_index] = (rssi, t)
        self.recent_samples[car_index].append((rssi, t))

    def feed(self, car_index: int, rssi: int, t: float) -> Optional[float]:
        self.record_sample(car_index, rssi, t)
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
        # Set after start() to "passive" or "active" so the UI can surface
        # which path actually came up — silent fallback to active masks the
        # main perf regression (BlueZ D-Bus discovery cache coalescing ads
        # to multi-second updates), so make it visible.
        self.mode: Optional[str] = None
        # Reason passive scan was rejected, if it was. Useful for the UI.
        self.passive_failure_reason: Optional[str] = None
        # Rolling per-second callback rate across all LapTimer-* cars so the
        # header can show actual throughput. The MT7921's BT/Wi-Fi shared
        # antenna throttles BT during Wi-Fi activity, so this number is what
        # actually matters for whether peak detection has the resolution to
        # find a 30 kph pass — not the firmware's 50 Hz advertising rate.
        self._callback_times: Deque[float] = deque(maxlen=512)

    def set_enabled(self, indices: set[int]) -> None:
        self._enabled_cars = set(indices)

    def callback_rate_hz(self, window_seconds: float = 5.0) -> Optional[float]:
        """Callbacks per second over the trailing ``window_seconds``.

        Returns ``None`` until at least two samples land inside the window —
        a single callback gives no rate, and showing 0 Hz right after start
        looks like a failure rather than "no data yet".
        """
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return None
        cutoff = now - window_seconds
        # deque slicing is O(n); n is bounded by maxlen and we're called at
        # the header-refresh cadence, so this is fine.
        in_window = [t for t in self._callback_times if t >= cutoff]
        if len(in_window) < 2:
            return None
        span = in_window[-1] - in_window[0]
        if span <= 0:
            return None
        return (len(in_window) - 1) / span

    async def start(self) -> None:
        from bleak import BleakScanner  # noqa: WPS433

        loop = asyncio.get_running_loop()

        def _detection_callback(device, advertisement_data) -> None:  # type: ignore[no-untyped-def]
            name = advertisement_data.local_name or device.name
            if not name:
                return
            idx = self._name_to_index.get(name)
            if idx is None:
                return
            rssi = advertisement_data.rssi
            if rssi is None:
                return
            t = loop.time()
            self._callback_times.append(t)
            if idx not in self._enabled_cars:
                # Still record samples so the Debug screen / RSSI display work
                # even before the car has been enabled for racing.
                self.registry.record_sample(idx, int(rssi), t)
                return
            peak_t = self.registry.feed(idx, int(rssi), t)
            if peak_t is not None:
                self.on_pass(idx, peak_t)

        # BlueZ's classic *active* discovery path coalesces advertisements
        # through D-Bus PropertiesChanged signals and a discovery cache, so
        # even with DuplicateData=True the detection callback fires only every
        # few seconds. Passive scanning with kernel-level AdvertisementMonitor
        # patterns (BlueZ 5.56+, kernel >= 5.10) delivers every matching ad
        # straight from the controller — perfect for the 20 ms transponder.
        #
        # Fallback: if the adapter / kernel doesn't support monitors, drop to
        # active mode with DuplicateData=True (better than nothing).
        self._scanner = await self._start_passive(_detection_callback)
        if self._scanner is None:
            self._scanner = await self._start_active(_detection_callback)
            self.mode = "active"
        else:
            self.mode = "passive"

    async def _start_passive(self, detection_callback) -> object | None:  # type: ignore[no-untyped-def]
        from bleak import BleakScanner  # noqa: WPS433

        try:
            from bleak.args.bluez import OrPattern  # noqa: WPS433
            from bleak.assigned_numbers import AdvertisementDataType  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            log.warning("bleak BlueZ args unavailable (%s); using active scan", exc)
            self.passive_failure_reason = f"bleak args unavailable: {exc}"
            return None

        # BlueZ will happily register an OrPattern monitor even when the
        # controller has no MSFT offload — but in that case no LE scan is
        # auto-started and the monitor never fires (observed on MediaTek
        # MT7921: SupportedFeatures = []). Guard against this silent failure
        # so we fall back to active+DuplicateData rather than reporting
        # passive-mode while delivering 0 callbacks.
        if not _advertisement_monitor_offload_supported():
            self.passive_failure_reason = (
                "controller does not offload AdvertisementMonitor patterns"
            )
            log.warning(
                "Passive scan skipped: %s",
                self.passive_failure_reason,
            )
            return None

        # Match any local name starting with "LapTimer-" — covers all 8 cars.
        or_patterns = [
            OrPattern(0, AdvertisementDataType.COMPLETE_LOCAL_NAME, b"LapTimer-"),
            OrPattern(0, AdvertisementDataType.SHORTENED_LOCAL_NAME, b"LapTimer-"),
        ]
        scanner = BleakScanner(
            detection_callback=detection_callback,
            scanning_mode="passive",
            bluez={"or_patterns": or_patterns},
        )
        try:
            await scanner.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("Passive scan unsupported (%s); falling back to active", exc)
            self.passive_failure_reason = str(exc) or type(exc).__name__
            try:
                await scanner.stop()
            except Exception:  # noqa: BLE001
                pass
            return None
        log.info("BLE scanner started (passive + advertisement monitor)")
        return scanner

    async def _start_active(self, detection_callback) -> object:  # type: ignore[no-untyped-def]
        from bleak import BleakScanner  # noqa: WPS433

        scanner = BleakScanner(
            detection_callback=detection_callback,
            bluez={"filters": {"DuplicateData": True}},
        )
        await scanner.start()
        log.info("BLE scanner started (active + DuplicateData=True)")
        return scanner

    async def stop(self) -> None:
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            except Exception:  # noqa: BLE001
                log.exception("Error stopping BLE scanner")
            self._scanner = None
        self.mode = None
