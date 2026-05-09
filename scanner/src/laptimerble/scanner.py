"""BLE scanner + per-car RSSI peak detector.

The scanner uses ``aioblescan`` to talk directly to the controller via a raw
HCI socket, bypassing ``bluetoothd`` entirely. This avoids BlueZ's D-Bus
discovery cache (which coalesces ads to multi-second updates on controllers
without ``AdvertisementMonitor`` offload) and gives us one callback per
LE Advertising Report event.

Runtime requirements:

* ``bluetoothd`` must NOT be active on the same hci device — its scan
  commands fight ours. Stop with ``systemctl stop bluetooth`` (NixOS:
  also flip ``hardware.bluetooth.enable`` off and rebuild).
* The Python interpreter needs ``CAP_NET_RAW`` and ``CAP_NET_ADMIN`` to
  open ``AF_BLUETOOTH`` SOCK_RAW and bind to ``hci0``:
  ``sudo setcap cap_net_raw,cap_net_admin+eip $(realpath .venv/bin/python)``

The detector logic is backend-agnostic and unit-tested via ``feed()``.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import logging
import socket
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from .config import NUM_CARS, ble_local_name

# ioctl(hci_socket, HCIDEVUP, dev_id) brings an hci device up. Encoded as
# _IOW('H', 201, int): 0x40000000 (write) | (4 << 16) | ('H' << 8) | 201.
# Without bluetoothd around, no one auto-ups the device after boot, so the
# kernel ignores HCI commands sent on it (no command-complete events) and
# aioblescan hangs forever waiting for ``_initialized``.
HCIDEVUP = 0x400448C9


def _ensure_hci_up(hci_index: int) -> None:
    """Bring hci<index> up if it isn't already.

    Needs ``CAP_NET_ADMIN`` (granted by the same setcap that lets us bind
    AF_BLUETOOTH SOCK_RAW). EALREADY means it was already up, which is the
    common path when running consecutively.
    """
    sock = socket.socket(
        family=socket.AF_BLUETOOTH,
        type=socket.SOCK_RAW,
        proto=socket.BTPROTO_HCI,
    )
    try:
        fcntl.ioctl(sock.fileno(), HCIDEVUP, hci_index)
    except OSError as exc:
        if exc.errno != errno.EALREADY:
            raise
    finally:
        sock.close()

# Number of most-recent (rssi, monotonic_t) samples kept per car for the Debug
# screen. Sized to comfortably exceed one screen of rows.
DEBUG_BUFFER_SIZE = 20

log = logging.getLogger(__name__)

# Timestamp of a detected pass, in monotonic seconds (matches ``time.monotonic``).
PassCallback = Callable[[int, float], None]  # (car_index, peak_t)


@dataclass
class PeakDetector:
    """Threshold + lockout peak detector for one car.

    Feed it ``(rssi_dbm, monotonic_t)`` samples in time order. It returns the
    timestamp at which a complete pass was detected, otherwise ``None``.

    The detector is idle until RSSI clears ``rssi_threshold`` (a noise gate).
    Once in a pass window it tracks the strongest sample seen. The window
    closes (and a pass is emitted) when the running peak has not advanced for
    ``drop_window_seconds`` — i.e. RSSI is no longer rising. The detector does
    *not* require RSSI to fall back below the threshold, which would be
    unreliable when the threshold is set well below the noise floor. After
    emission, it ignores everything for ``lockout_seconds``.
    """

    rssi_threshold: int = -100
    lockout_seconds: float = 3.0
    drop_window_seconds: float = 0.3

    in_window: bool = False
    window_peak_rssi: int = -200
    window_peak_t: float = 0.0
    last_emit_t: float = -1e9  # so first call is always allowed

    def reset(self) -> None:
        self.in_window = False
        self.window_peak_rssi = -200
        self.window_peak_t = 0.0
        self.last_emit_t = -1e9

    def feed(self, rssi: int, t: float) -> Optional[float]:
        # Lockout: silently consume everything until lockout has elapsed
        if (t - self.last_emit_t) < self.lockout_seconds:
            return None

        if rssi < self.rssi_threshold:
            # Below the noise gate. If a window is open and the peak has been
            # stale long enough, this is a fine moment to close it.
            if self.in_window and (t - self.window_peak_t) >= self.drop_window_seconds:
                return self._emit()
            return None

        if not self.in_window:
            self.in_window = True
            self.window_peak_rssi = rssi
            self.window_peak_t = t
            return None

        if rssi > self.window_peak_rssi:
            self.window_peak_rssi = rssi
            self.window_peak_t = t
            return None

        # Above threshold, but not a new peak. Close the window once the peak
        # has been stale for the drop window.
        if (t - self.window_peak_t) >= self.drop_window_seconds:
            return self._emit()
        return None

    def _emit(self) -> float:
        peak_t = self.window_peak_t
        self.in_window = False
        self.window_peak_rssi = -200
        self.last_emit_t = peak_t
        return peak_t


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
    """Raw-HCI BLE scanner that fans LapTimer-* ads to per-car detectors.

    Uses ``aioblescan`` directly (no ``bluetoothd``) so every LE Advertising
    Report event from the controller reaches a detector with no D-Bus
    coalescing in between.
    """

    HCI_INTERFACE = 0  # hci0

    def __init__(
        self,
        registry: CarDetectorRegistry,
        on_pass: PassCallback,
    ) -> None:
        self.registry = registry
        self.on_pass = on_pass
        self._enabled_cars: set[int] = set()
        self._name_to_index: dict[str, int] = {
            ble_local_name(i): i for i in range(NUM_CARS)
        }
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._transport = None
        self._btctrl = None
        # ``mode`` is "active" while the HCI scan is live, None otherwise.
        # Raw HCI scanning is always controller-active (with SCAN_RSP); the
        # field is kept as a string so the header can render "scanning
        # (active)" alongside the live Hz rate.
        self.mode: Optional[str] = None
        # Rolling per-second callback rate across all LapTimer-* cars so the
        # header can show actual throughput. The MT7921's BT/Wi-Fi shared
        # antenna throttles BT during Wi-Fi activity, so this number is what
        # actually matters for whether peak detection has the resolution to
        # find a 30 kph pass.
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
        in_window = [t for t in self._callback_times if t >= cutoff]
        if len(in_window) < 2:
            return None
        span = in_window[-1] - in_window[0]
        if span <= 0:
            return None
        return (len(in_window) - 1) / span

    async def start(self) -> None:
        # Deferred import: aioblescan opens an AF_BLUETOOTH SOCK_RAW socket
        # at module-load time only when create_bt_socket() is called, so
        # plain ``import aioblescan`` is harmless on a machine without BT.
        import aioblescan as aiobs  # noqa: WPS433

        # Without bluetoothd, hci0 stays DOWN after boot and HCI commands
        # silently never get responses — we'd hang in send_scan_request.
        _ensure_hci_up(self.HCI_INTERFACE)

        self._loop = asyncio.get_running_loop()
        sock = aiobs.create_bt_socket(self.HCI_INTERFACE)
        # _create_connection_transport is a private asyncio API but it's the
        # canonical way to bind a Protocol to a pre-existing socket — see
        # the aioblescan README. There is no public equivalent that accepts
        # a non-stream/non-datagram socket.
        conn, btctrl = await self._loop._create_connection_transport(
            sock, aiobs.BLEScanRequester, None, None
        )
        btctrl.process = self._on_packet
        self._transport = conn
        self._btctrl = btctrl
        # isactivescan=True → controller asks for SCAN_RSP, so the local
        # name (which the firmware advertises in the ADV payload) shows up
        # reliably. Default scan params are 10 ms / 10 ms ≈ 100 % duty
        # cycle on this controller's BT time slice.
        # Wrap in a timeout: send_scan_request awaits an internal Event that
        # only sets after HCI command-complete responses come back. If
        # bluetoothd is still up, or the interpreter lacks CAP_NET_ADMIN,
        # those responses never arrive — fail loudly instead of hanging the
        # app's on_mount forever.
        try:
            await asyncio.wait_for(
                btctrl.send_scan_request(isactivescan=True), timeout=5.0
            )
        except asyncio.TimeoutError as exc:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                pass
            self._btctrl = None
            self._transport = None
            raise RuntimeError(
                "HCI scan start timed out — is bluetoothd still running, "
                "or does python lack CAP_NET_ADMIN?"
            ) from exc
        self.mode = "active"
        log.info("Raw HCI scanner started on hci%d", self.HCI_INTERFACE)

    async def stop(self) -> None:
        if self._btctrl is not None:
            try:
                await self._btctrl.stop_scan_request()
            except Exception:  # noqa: BLE001
                log.exception("stop_scan_request failed")
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                log.exception("transport close failed")
        self._btctrl = None
        self._transport = None
        self.mode = None

    def _on_packet(self, data: bytes) -> None:
        # Called from the BLEScanRequester protocol on the asyncio loop —
        # synchronous and serialised with the rest of the app.
        import aioblescan as aiobs  # noqa: WPS433

        ev = aiobs.HCI_Event()
        try:
            ev.decode(data)
        except Exception:  # noqa: BLE001
            return

        # Only LE Advertising Reports carry a name + RSSI for our cars.
        name_field = ev.retrieve("Complete Name") or ev.retrieve("Short Name")
        if not name_field:
            return
        rssi_field = ev.retrieve("rssi")
        if not rssi_field:
            return

        name = name_field[0].val
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        idx = self._name_to_index.get(name)
        if idx is None:
            return

        rssi = int(rssi_field[-1].val)
        # An HCI event can bundle several reports; pick the freshest "now"
        # for all of them rather than trying to back-date.
        assert self._loop is not None
        t = self._loop.time()
        self._callback_times.append(t)
        if idx not in self._enabled_cars:
            self.registry.record_sample(idx, rssi, t)
            return
        peak_t = self.registry.feed(idx, rssi, t)
        if peak_t is not None:
            self.on_pass(idx, peak_t)
