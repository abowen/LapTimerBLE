"""Textual UI."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, Static

from .config import (
    NUM_CARS,
    CarConfig,
    RaceConfig,
    ble_local_name,
    default_cars,
)
from .export import export_all_time, export_race
from .models import Lap, RaceState
from .scanner import BleScanner, CarDetectorRegistry, DEBUG_BUFFER_SIZE
from .storage import Storage
from .timeutil import format_lap, format_race

log = logging.getLogger(__name__)


# ----- styling -----------------------------------------------------------------

CSS = """
Screen {
    background: $background;
    color: $text;
}

#race-header {
    height: 5;
    border: heavy white;
    padding: 0 1;
    content-align: center middle;
}

#race-header.running {
    border: heavy green;
}

#race-header.countdown {
    border: heavy yellow;
}

#race-clock {
    text-style: bold;
    color: white;
}

#status-line {
    color: grey;
}

#cars {
    layout: grid;
    grid-size: 4 2;
    grid-gutter: 1 1;
    height: 1fr;
}

.car-card {
    border: round white;
    padding: 0 1;
    color: white;
    background: $surface;
}

.car-card.disabled {
    color: grey;
    border: round grey;
}

.car-card.selected {
    border: heavy yellow;
}

.car-card.finished {
    border: heavy green;
}

.hist-car-card {
    border: round white;
    padding: 0 1;
    color: white;
    background: $surface;
    margin: 0 0 1 0;
}

.car-name {
    text-style: bold;
}

.car-stats {
    color: grey;
}

.car-laps {
    color: white;
}

.car-disabled-laps {
    color: grey;
}

.car-top {
    color: cyan;
}

.modal {
    width: 70%;
    height: auto;
    max-height: 80%;
    border: heavy white;
    background: $surface;
    padding: 1 2;
}

.modal Input {
    margin: 0 0 1 0;
}

.modal-title {
    text-style: bold;
    color: yellow;
    margin-bottom: 1;
}

.hint {
    color: grey;
    margin-top: 1;
}

.hist-section {
    margin: 1 0 0 0;
    text-style: bold;
}
"""


# ----- car card widget ---------------------------------------------------------


class CarCard(Static):
    """One car's display: name, current laps, top-5-today."""

    DEFAULT_CSS = ""

    def __init__(self, car: CarConfig) -> None:
        super().__init__(id=f"car-{car.index}")
        self.car = car
        self.laps: list[Lap] = []
        self.finished: bool = False
        self.latest_rssi: Optional[int] = None
        self.add_class("car-card")
        if not car.enabled:
            self.add_class("disabled")

    def update_state(
        self,
        car: CarConfig,
        laps: list[Lap],
        finished: bool,
        selected: bool,
        latest_rssi: Optional[int] = None,
    ) -> None:
        self.car = car
        self.laps = laps
        self.finished = finished
        self.latest_rssi = latest_rssi

        self.set_class(not car.enabled, "disabled")
        self.set_class(selected, "selected")
        self.set_class(finished and car.enabled, "finished")
        self.refresh_text()

    def refresh_text(self) -> None:
        car = self.car
        header = f"[bold]{car.number}. {car.display_name}[/bold]"
        if not car.enabled:
            header += " (off)"
        elif self.finished:
            header += " ✓"

        if car.enabled:
            rssi_text = "—" if self.latest_rssi is None else str(self.latest_rssi)
            rssi_line = f"[grey]{rssi_text}[/grey]\n"
        else:
            rssi_line = ""

        if not car.enabled:
            laps_section = "[grey]disabled[/grey]"
        elif not self.laps:
            laps_section = "[grey]— no laps yet —[/grey]"
        else:
            lines = []
            for lap in self.laps:
                lines.append(f"L{lap.lap_index:>2}  {format_lap(lap.lap_seconds)}")
            laps_section = "\n".join(lines)

        self.update(f"{header}\n{rssi_line}{laps_section}")


# ----- modal screens -----------------------------------------------------------


class CarHistoryCard(Static):
    """Bordered card showing a car's top-5-today laps with timestamps."""

    def __init__(self, car: CarConfig, laps: list[tuple[float, str]]) -> None:
        super().__init__(self._build(car, laps), id=f"hist-car-{car.index}")
        self.car = car
        self.add_class("hist-car-card")

    @staticmethod
    def _build(car: CarConfig, laps: list[tuple[float, str]]) -> str:
        if not laps:
            return f"[bold]{car.number}. {car.display_name}[/bold]\n  [grey]no times today[/grey]"
        date_str = laps[0][1][:10]  # YYYY-MM-DD from ISO string
        title = f"[bold]{car.number}. {car.display_name}[/bold]  [grey]{date_str}[/grey]"
        lines = [f"  {format_lap(s)}   {ts[11:16]}" for s, ts in laps]
        return f"{title}\n" + "\n".join(lines)

    def update_laps(self, laps: list[tuple[float, str]]) -> None:
        self.update(self._build(self.car, laps))


class RenameScreen(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("Rename car", classes="modal-title")
            yield Input(value=self.current, id="rename-input")
            yield Static("Enter to apply, Esc to cancel.", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfigScreen(ModalScreen[Optional[RaceConfig]]):
    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("up", "focus_prev", "Prev field", show=False),
        Binding("down", "focus_next", "Next field", show=False),
    ]

    FIELD_IDS = ("laps-input", "rssi-input", "lockout-input")

    def __init__(self, config: RaceConfig) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("Configuration", classes="modal-title")
            yield Label("Laps  — number 1–99 or 'D' to disable")
            yield Input(
                value=("D" if self.config.laps_target is None else str(self.config.laps_target)),
                id="laps-input",
            )
            yield Label("Min RSSI dBm  — e.g. -70 (more negative = needs closer pass)")
            yield Input(value=str(self.config.rssi_threshold_dbm), id="rssi-input")
            yield Label("Lockout seconds  — race-start + per-car cooldown")
            yield Input(value=f"{self.config.lockout_seconds:g}", id="lockout-input")
            yield Static(
                "Tab / ↑↓ to switch fields.  Enter to Apply.  Esc to close.",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#laps-input", Input).focus()

    def _focused_field_index(self) -> int:
        focused = self.focused
        if focused is not None and focused.id in self.FIELD_IDS:
            return self.FIELD_IDS.index(focused.id)
        return 0

    def action_focus_prev(self) -> None:
        n = len(self.FIELD_IDS)
        idx = (self._focused_field_index() - 1) % n
        self.query_one(f"#{self.FIELD_IDS[idx]}", Input).focus()

    def action_focus_next(self) -> None:
        n = len(self.FIELD_IDS)
        idx = (self._focused_field_index() + 1) % n
        self.query_one(f"#{self.FIELD_IDS[idx]}", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        new = self._build()
        if new is not None:
            self.dismiss(new)

    def _build(self) -> RaceConfig | None:
        laps_raw = self.query_one("#laps-input", Input).value.strip()
        rssi_raw = self.query_one("#rssi-input", Input).value.strip()
        lockout_raw = self.query_one("#lockout-input", Input).value.strip()

        if laps_raw.upper() == "D":
            laps_target: int | None = None
        else:
            try:
                laps_target = int(laps_raw)
            except ValueError:
                self._flash("Laps must be a number 1-99 or 'D'.")
                return None
            if not (1 <= laps_target <= 99):
                self._flash("Laps must be between 1 and 99.")
                return None

        try:
            rssi = int(rssi_raw)
        except ValueError:
            self._flash("RSSI must be an integer like -70.")
            return None

        try:
            lockout = float(lockout_raw)
        except ValueError:
            self._flash("Lockout must be a number of seconds.")
            return None
        if lockout < 0:
            self._flash("Lockout must be >= 0.")
            return None

        return RaceConfig(
            laps_target=laps_target,
            rssi_threshold_dbm=rssi,
            lockout_seconds=lockout,
        )

    def _flash(self, message: str) -> None:
        self.app.bell()
        self.notify(message, severity="warning")


class DebugScreen(ModalScreen[None]):
    """Live view of the last DEBUG_BUFFER_SIZE BLE advertisements for one car."""

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, registry: CarDetectorRegistry, car: CarConfig) -> None:
        super().__init__()
        self.registry = registry
        self.car = car

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(
                f"Debug — {self.car.number}. {self.car.display_name}",
                classes="modal-title",
            )
            yield Static(
                f"[grey]Last {DEBUG_BUFFER_SIZE} advertisements (newest first). "
                f"rssi dBm   age ms[/grey]",
                classes="hint",
            )
            yield Static(self._body_text(), id="debug-body")
            yield Static("Esc to close.", classes="hint")

    def on_mount(self) -> None:
        self.set_interval(0.2, self._tick_refresh)

    def _tick_refresh(self) -> None:
        self.query_one("#debug-body", Static).update(self._body_text())

    def _body_text(self) -> str:
        samples = list(self.registry.recent_samples[self.car.index])
        if not samples:
            return "  [grey]no samples yet[/grey]"
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = time.monotonic()
        lines = []
        for rssi, t in reversed(samples):
            age_ms = max(0, int((now - t) * 1000))
            lines.append(f"  {rssi:>4}   {age_ms:>5}")
        return "\n".join(lines)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HistoryScreen(ModalScreen[Optional[str]]):
    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("a", "clear_all", "Clear all"),
    ]

    def __init__(self, storage: Storage, cars: list[CarConfig]) -> None:
        super().__init__()
        self.storage = storage
        self.cars = cars
        self._pending_clear = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("History", classes="modal-title")
            with VerticalScroll():
                yield Static("[cyan]Top 10 fastest laps (all-time)[/cyan]", classes="hist-section")
                yield Static(self._render_overall(), id="hist-overall")
                yield Static("[cyan]Top 5 today per car[/cyan]", classes="hist-section")
                for car in self.cars:
                    yield CarHistoryCard(car, self.storage.top_today(car.index))
            yield Static(
                "Press C then a digit 1-8 to clear that car, A to clear all, Esc to close.",
                classes="hint",
            )

    def _render_overall(self) -> str:
        rows = self.storage.top_overall(limit=10)
        if not rows:
            return "  [grey]no laps recorded yet[/grey]"
        name_by_index = {c.index: c.display_name for c in self.cars}
        lines: list[str] = []
        for rank, (car_index, lap_seconds, recorded_at) in enumerate(rows, 1):
            car_name = name_by_index.get(car_index, f"Car {car_index + 1}")
            day = recorded_at[:10] if recorded_at else ""
            lines.append(
                f"  {rank:>2}. {format_lap(lap_seconds):>7}  "
                f"[bold]{car_index + 1}.{car_name}[/bold]  [grey]{day}[/grey]"
            )
        return "\n".join(lines)

    def on_key(self, event: events.Key) -> None:
        if self._pending_clear:
            self._pending_clear = False
            if event.key.isdigit():
                n = int(event.key)
                if 1 <= n <= NUM_CARS:
                    self.storage.clear_car(n - 1)
                    self._refresh_rows()
                    self.notify(f"Cleared car {n}.")
                    event.stop()
                    return
            event.stop()
            return
        if event.key == "c":
            self._pending_clear = True
            self.notify("Press 1-8 to clear that car.")
            event.stop()

    def action_clear_all(self) -> None:
        self.storage.clear_all()
        self._refresh_rows()
        self.notify("Cleared all car history.")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _refresh_rows(self) -> None:
        self.query_one("#hist-overall", Static).update(self._render_overall())
        for car in self.cars:
            laps = self.storage.top_today(car.index)
            self.query_one(f"#hist-car-{car.index}", CarHistoryCard).update_laps(laps)


# ----- main app ---------------------------------------------------------------


class LapTimerApp(App):
    CSS = CSS
    TITLE = "LAP TIMER BLE"
    SUB_TITLE = "1/10 RC — BLE peak-RSSI lap timer"

    BINDINGS = [
        Binding("s", "toggle_start", "Start/Stop"),
        Binding("1", "select(0)", "1"),
        Binding("2", "select(1)", "2"),
        Binding("3", "select(2)", "3"),
        Binding("4", "select(3)", "4"),
        Binding("5", "select(4)", "5"),
        Binding("6", "select(5)", "6"),
        Binding("7", "select(6)", "7"),
        Binding("8", "select(7)", "8"),
        Binding("left", "select_prev", "◀"),
        Binding("right", "select_next", "▶"),
        Binding("e", "toggle_enabled", "Enable/Disable"),
        Binding("d", "open_debug", "Debug"),
        Binding("r", "rename", "Rename"),
        Binding("c", "open_config", "Config"),
        Binding("h", "open_history", "History"),
        Binding("x", "export", "Export CSV"),
        Binding("q", "quit", "Quit"),
    ]

    mode: reactive[str] = reactive("idle")  # idle | countdown | running | finished
    selected_car: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.cars: list[CarConfig] = default_cars()
        self.config: RaceConfig = RaceConfig()
        self.storage = Storage()
        self._sync_cars_from_db()

        self.race_state: RaceState = RaceState()
        self.race_id: int | None = None
        self.start_monotonic: float | None = None
        self.last_lap_monotonic: dict[int, float] = {}
        self.countdown_value: int = 0

        self.registry = CarDetectorRegistry(
            rssi_threshold=self.config.rssi_threshold_dbm,
            lockout_seconds=self.config.lockout_seconds,
            drop_window_seconds=self.config.drop_window_seconds,
        )
        self._pass_queue: asyncio.Queue[tuple[int, float]] = asyncio.Queue()
        self.scanner: BleScanner | None = None
        self.scanner_status: str = "off"

    # ----- initial state from DB -----

    def _sync_cars_from_db(self) -> None:
        rows = self.storage.load_cars()
        for idx, name, enabled in rows:
            self.cars[idx].display_name = name
            self.cars[idx].enabled = enabled

    # ----- compose -----

    def compose(self) -> ComposeResult:
        yield Static(id="race-header")
        yield Grid(*[CarCard(car) for car in self.cars], id="cars")
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(0.05, self._tick)
        self.set_interval(0.2, self._refresh_cards)
        self._refresh_header()
        self._refresh_cards()
        # Start the BLE scanner. Failure is non-fatal; the UI still works.
        try:
            self.scanner = BleScanner(self.registry, on_pass=self._on_ble_pass)
            self.scanner.set_enabled({c.index for c in self.cars if c.enabled})
            await self.scanner.start()
            self.scanner_status = "scanning"
        except Exception as exc:  # noqa: BLE001
            log.exception("BLE scanner failed to start")
            self.scanner_status = f"off ({type(exc).__name__})"
            self.scanner = None
        self._drain_passes_task = asyncio.create_task(self._drain_passes())

    async def on_unmount(self) -> None:
        if self.scanner is not None:
            await self.scanner.stop()
        self.storage.close()

    # ----- BLE → UI bridge -----

    def _on_ble_pass(self, car_index: int, peak_t: float) -> None:
        # Called from event loop thread by bleak; safe to enqueue.
        try:
            self._pass_queue.put_nowait((car_index, peak_t))
        except Exception:  # noqa: BLE001
            log.exception("Failed to enqueue pass event")

    async def _drain_passes(self) -> None:
        while True:
            car_index, peak_t = await self._pass_queue.get()
            self._handle_pass(car_index, peak_t)

    def _handle_pass(self, car_index: int, peak_t: float) -> None:
        if self.mode != "running":
            return
        car = self.cars[car_index]
        if not car.enabled:
            return
        prev_t = self.last_lap_monotonic.get(car_index, self.start_monotonic or peak_t)
        lap_seconds = max(0.0, peak_t - prev_t)
        self.last_lap_monotonic[car_index] = peak_t

        lap_index = self.race_state.lap_count(car_index) + 1
        lap = Lap(
            car_index=car_index,
            lap_index=lap_index,
            lap_seconds=lap_seconds,
            recorded_at=datetime.now(),
        )
        self.race_state.record(lap)
        if self.race_id is not None:
            self.storage.record_lap(
                race_id=self.race_id,
                car_index=car_index,
                lap_index=lap_index,
                lap_seconds=lap_seconds,
                recorded_at=lap.recorded_at,
            )
        self._refresh_card(car_index)
        self._maybe_finish_race()

    # ----- header / cards rendering -----

    def _tick(self) -> None:
        self._refresh_header()

    def _refresh_header(self) -> None:
        header = self.query_one("#race-header", Static)
        header.set_class(self.mode == "running", "running")
        header.set_class(self.mode == "countdown", "countdown")

        if self.mode == "idle":
            line1 = "[bold]READY[/bold]"
            elapsed = "00:00.000"
        elif self.mode == "countdown":
            line1 = f"[bold yellow]{self.countdown_value if self.countdown_value > 0 else 'GO'}[/bold yellow]"
            elapsed = "00:00.000"
        elif self.mode == "running":
            line1 = "[bold green]RACING[/bold green]"
            elapsed = format_race(self._race_elapsed())
        else:  # finished
            line1 = "[bold]FINISHED[/bold]"
            elapsed = format_race(self._race_elapsed())

        target = "∞" if self.config.laps_target is None else str(self.config.laps_target)
        config_line = (
            f"[grey]Laps {target}  RSSI ≥ {self.config.rssi_threshold_dbm} dBm  "
            f"Lockout {self.config.lockout_seconds:g}s  BLE: {self.scanner_status}[/grey]"
        )
        header.update(f"{line1}\n[bold]{elapsed}[/bold]\n{config_line}")

    def _race_elapsed(self) -> float:
        if self.start_monotonic is None:
            return 0.0
        if self.race_state.finished_at is not None and self.start_monotonic is not None:
            # Use the last lap timestamp or now; simple now() is fine for a stopped race
            return time.monotonic() - self.start_monotonic if self.mode == "running" else self._frozen_elapsed
        return time.monotonic() - self.start_monotonic

    @property
    def _frozen_elapsed(self) -> float:
        # Computed on stop() and reused.
        return getattr(self, "_frozen_elapsed_value", 0.0)

    def _refresh_cards(self) -> None:
        for car in self.cars:
            self._refresh_card(car.index)

    def _refresh_card(self, car_index: int) -> None:
        car = self.cars[car_index]
        card = self.query_one(f"#car-{car_index}", CarCard)
        laps = self.race_state.laps.get(car_index, [])
        finished = (
            self.config.laps_target is not None
            and len(laps) >= self.config.laps_target
        )
        card.update_state(
            car=car,
            laps=laps,
            finished=finished,
            selected=(car_index == self.selected_car),
            latest_rssi=self._latest_rssi(car_index),
        )

    def _latest_rssi(self, car_index: int) -> Optional[int]:
        sample = self.registry.latest_samples[car_index]
        if sample is None:
            return None
        rssi, sample_t = sample
        # Match the clock the scanner uses when feeding the registry.
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = time.monotonic()
        if now - sample_t > 2.0:
            return None
        return rssi

    # ----- actions -----

    def action_select(self, car_index: int) -> None:
        if 0 <= car_index < NUM_CARS:
            self.selected_car = car_index
            self._refresh_cards()

    def action_select_prev(self) -> None:
        self.selected_car = (self.selected_car - 1) % NUM_CARS
        self._refresh_cards()

    def action_select_next(self) -> None:
        self.selected_car = (self.selected_car + 1) % NUM_CARS
        self._refresh_cards()

    def action_toggle_enabled(self) -> None:
        if self.mode != "idle":
            self.notify("Stop the race before enabling/disabling cars.", severity="warning")
            return
        car = self.cars[self.selected_car]
        car.enabled = not car.enabled
        self.storage.set_car_enabled(car.index, car.enabled)
        if self.scanner is not None:
            self.scanner.set_enabled({c.index for c in self.cars if c.enabled})
        self._refresh_card(car.index)

    def action_rename(self) -> None:
        car = self.cars[self.selected_car]

        def _on_rename(value: str | None) -> None:
            if value:
                car.display_name = value
                self.storage.set_car_name(car.index, value)
                self._refresh_card(car.index)

        self.push_screen(RenameScreen(car.display_name), _on_rename)

    def action_open_config(self) -> None:
        if self.mode not in ("idle", "finished"):
            self.notify("Stop the race before changing config.", severity="warning")
            return

        def _on_apply(new: RaceConfig | None) -> None:
            if new is None:
                return
            self.config = new
            self.registry.reconfigure(
                rssi_threshold=new.rssi_threshold_dbm,
                lockout_seconds=new.lockout_seconds,
            )
            self._refresh_header()

        self.push_screen(ConfigScreen(self.config), _on_apply)

    def action_open_debug(self) -> None:
        car = self.cars[self.selected_car]
        self.push_screen(DebugScreen(self.registry, car))

    def action_open_history(self) -> None:
        def _on_close(_value: str | None) -> None:
            self._refresh_cards()

        self.push_screen(HistoryScreen(self.storage, self.cars), _on_close)

    def action_export(self) -> None:
        paths: list[str] = []
        if any(self.race_state.laps.values()):
            paths.append(str(export_race(self.race_state, self.cars)))
        all_time_path = export_all_time(self.storage, self.cars)
        paths.append(str(all_time_path))
        self.notify("Exported:\n" + "\n".join(paths), severity="information")

    def action_toggle_start(self) -> None:
        if self.mode == "idle" or self.mode == "finished":
            self._begin_countdown()
        elif self.mode in ("countdown", "running"):
            self._stop_race()

    # ----- race lifecycle -----

    def _begin_countdown(self) -> None:
        if not any(c.enabled for c in self.cars):
            self.notify("Enable at least one car first (1-8 then D).", severity="warning")
            return
        self._reset_race_state()
        self.mode = "countdown"
        self.countdown_value = 3
        self._refresh_header()
        self.set_timer(1.0, self._countdown_step)

    def _countdown_step(self) -> None:
        if self.mode != "countdown":
            return
        self.countdown_value -= 1
        if self.countdown_value <= 0:
            # Show "GO" briefly then start
            self.countdown_value = 0
            self._refresh_header()
            self.set_timer(0.5, self._start_race)
        else:
            self._refresh_header()
            self.set_timer(1.0, self._countdown_step)

    def _reset_race_state(self) -> None:
        self.race_state = RaceState()
        self.last_lap_monotonic.clear()
        self.race_id = None
        self.start_monotonic = None
        self.registry.reset_all()
        self._refresh_cards()

    def _start_race(self) -> None:
        self.mode = "running"
        now_dt = datetime.now()
        self.race_state.started_at = now_dt
        self.race_id = self.storage.start_race(now_dt, self.config.laps_target)
        loop_now = asyncio.get_event_loop().time()
        self.start_monotonic = loop_now
        # Seed each detector's lockout so the race-start lockout is enforced at
        # the same level as per-detection lockout (single lockout setting).
        for det in self.registry.detectors:
            det.last_emit_t = loop_now
        if self.scanner is not None:
            self.scanner.set_enabled({c.index for c in self.cars if c.enabled})
        self._refresh_header()

    def _maybe_finish_race(self) -> None:
        if self.config.laps_target is None:
            return
        target = self.config.laps_target
        for car in self.cars:
            if not car.enabled:
                continue
            if self.race_state.lap_count(car.index) < target:
                return
        self._finish_race()

    def _finish_race(self) -> None:
        if self.mode != "running":
            return
        self.mode = "finished"
        self.race_state.finished_at = datetime.now()
        self._frozen_elapsed_value = (
            time.monotonic() - self.start_monotonic if self.start_monotonic else 0.0
        )
        if self.race_id is not None:
            self.storage.finish_race(self.race_id, self.race_state.finished_at)
        self._refresh_header()
        self._refresh_cards()
        self.notify("Race finished.", severity="information")

    def _stop_race(self) -> None:
        if self.mode == "countdown":
            self.mode = "idle"
            self.countdown_value = 0
            self._refresh_header()
            return
        if self.mode == "running":
            self._finish_race()
