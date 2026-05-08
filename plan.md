# Plan

## Goal

Lap timer for 1/10 scale RC cars, using BLE-advertising transponders detected by a
side-mounted laptop scanner.

## Scope

The repository contains both halves of the system, developed independently
against the BLE protocol below as the contract:

- `scanner/` — Python scanner + Textual UI on the laptop.
- `firmware/` — ESP32-C3 transponder firmware (PlatformIO + Arduino + NimBLE).

## Hardware

- Car (transponder): Seeed XIAO ESP32-C3, powered from the ESC's BEC
- Scanner: Framework 13 (Ryzen 7840U) with Bluetooth 5.2, running NixOS

## Race requirements

| Parameter            | Value                              |
| -------------------- | ---------------------------------- |
| Track width          | 3 m                                |
| Car speed            | ~30 kph (≈ 8.3 m/s)                |
| Reader location      | Side-mounted, up to 3 m lateral    |
| Cars supported       | 8, each uniquely identified        |
| Timing precision     | within 0.1 s                       |

## BLE protocol (firmware contract)

- Each car advertises a BLE local name `LapTimer-N` where `N` is 1..8.
- Advertising is non-connectable, undirected, with both min and max interval
  pinned to the per-car value below (no jitter).
- TX power is set to +9 dBm so all transponders are equivalent at the reader.
- The car number is selected at firmware build time (`CAR_NUMBER` build flag);
  one PlatformIO environment per car (`car1` … `car8`).
- Advertising intervals (one per car, primes near 20 ms baseline to minimise
  collisions):

  | Car | Interval |
  | --- | -------- |
  | 1   | 20 ms    |
  | 2   | 23 ms    |
  | 3   | 29 ms    |
  | 4   | 31 ms    |
  | 5   | 37 ms    |
  | 6   | 41 ms    |
  | 7   | 43 ms    |
  | 8   | 47 ms    |

- Scanner uses `bleak` with a detection callback; identifies each car by local name
  and records `(rssi, monotonic_timestamp)` per advertisement.

## Detection algorithm

Per-car threshold-plus-lockout peak detector:

1. Samples below the configured RSSI threshold are ignored.
2. When a sample first crosses the threshold a "pass window" opens.
3. While in the window, track the max RSSI sample and its timestamp.
4. When samples have been below the threshold for a short drop period (~300 ms)
   the window closes and the peak's timestamp is the lap-completion time.
5. After emitting a lap, the per-car detector is locked out for `lockout_seconds`
   (same value used as the race-start lockout — see below).

## Race flow

1. **Idle** — Car 1 enabled by default.
2. **Start pressed** — 3-second visible countdown (3, 2, 1, GO).
3. **Running** — "GO" the race timer starts; lap detection is suppressed for
   the configured `lockout_seconds` (default 3 s) to prevent recording the start
   line as a lap.
4. **Lap detected** — Append to the car's lap list, recompute its top-5-today.
5. **Finished** — when every enabled car has reached the configured lap count
   (or never, if lap counting is disabled). Also stoppable manually.

## UI / UX (Textual)

- Dark theme; retro mono font (`Courier New`-style, configured via Textual CSS).
- Layout: header (race state + clock), grid of 8 car cards (4 × 2), footer
  (key hints).
- Active cars rendered white, disabled cars grey.
- Each car card shows:
  - Car number + display name
  - Latest detected RSSI (raw dBm integer) below the name; `—` when no recent
    sample (older than 2 s, scanner off, or car disabled)
  - Current race laps (one per row), with elapsed time per lap
- Default car display names: `One`, `Two`, …, `Eight`.
- Selected car visually highlighted.

### Time format

- Lap time: `ss.ms` — seconds with millisecond precision (`12.345`).
- Race time: `mm:ss.ms` — minutes:seconds.milliseconds (`03:42.187`).

### Keyboard shortcuts

Top-level:

| Key       | Action                                                   |
| --------- | -------------------------------------------------------- |
| `S`       | Start / Stop the race                                    |
| `1-8`     | Select car N                                             |
| `← / →`   | Move selection to previous / next car (wraps 1↔8)        |
| `D`       | Toggle the selected car's enabled state                  |
| `R`       | Rename the selected car (modal text input)               |
| `C`       | Open Configuration screen                                |
| `H`       | Open History screen                                      |
| `E`       | Export current session to CSV                            |
| `Q`       | Quit                                                     |

Configuration screen:

| Key         | Action                                                    |
| ----------- | --------------------------------------------------------- |
| `Tab` / `↑↓`| Move focus between fields                                 |
| `Enter`     | Apply current values and close                            |
| `Esc`       | Close without applying                                    |

Fields:

| Field          | Accepted input                                            |
| -------------- | --------------------------------------------------------- |
| Laps target    | Number 1–99, or `D` to disable (race runs until Stop)     |
| Min RSSI (dBm) | Integer like `-70` (more negative = needs closer pass)    |
| Lockout (s)    | Number of seconds — race-start gate + per-car cooldown    |

History screen shows:

- **Top 10 fastest laps overall** — best 10 lap times across all cars (all-time),
  each row tagged with the car that set it and the date it was set.
- **Per-car top 5 today** — the best 5 laps each car has recorded today, displayed
  as a bordered card. The card title shows the car name and the date of the fastest
  lap. Each lap row shows the lap time and the time of day (`HH:MM`) it was recorded.

| Key       | Action                                            |
| --------- | ------------------------------------------------- |
| `C<n>`    | Clear lap history for car N                       |
| `A`       | Clear all cars' lap history                       |
| `Esc`     | Close                                             |

## Persistence

- SQLite at `~/.laptimerble/laps.db`.
- Tables: `cars` (id, display_name, enabled), `laps` (id, car_id, race_id,
  lap_index, lap_seconds, recorded_at), `races` (id, started_at, finished_at,
  laps_target).
- "Top 5 today" = the 5 smallest `lap_seconds` for the car where
  `date(recorded_at) = today`.

## Export

`E` writes two files into `./exports/`:

- `laps_<YYYY-MM-DD_HHMMSS>.csv` — the **current race only** (omitted if no laps
  recorded yet). Columns: `race_started_at, car_id, car_name, lap_index,
  lap_seconds`.
- `laps_alltime_<YYYY-MM-DD_HHMMSS>.csv` — **every lap ever recorded** across
  all races. Columns: `race_started_at, car_id, car_name, lap_index,
  lap_seconds, recorded_at`.

## Defaults

| Setting               | Default                |
| --------------------- | ---------------------- |
| Enabled cars          | Car 1 only             |
| Laps target           | 3                      |
| Lockout (s)           | 3                      |
| RSSI threshold (dBm)  | -70                    |
| Drop-window (s)       | 0.3 (internal)         |

## Tests

- `pytest` covering pure-logic modules:
  - `timeutil` — format/parse round-trips
  - `scanner` — peak detector against synthesised RSSI traces
  - `storage` — schema bootstrap, top-5 query, history clearing
- BLE and Textual layers exercised manually.
