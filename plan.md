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

## Scanner host requirements

The 20–47 ms advertising cadence is only deliverable end-to-end when BlueZ
exposes `org.bluez.AdvertisementMonitorManager1`. Bleak's passive scan
registers an OR-pattern monitor on that interface and receives one callback
per matching advertisement. Without it, the scanner falls back to active
discovery; BlueZ then routes ads through its D-Bus discovery cache and
`PropertiesChanged` is coalesced to multi-second updates, defeating peak
detection.

`AdvertisementMonitorManager1` is registered only when bluetoothd runs with
**Experimental features enabled**. On NixOS:

```nix
hardware.bluetooth.settings.General.Experimental = true;
```

After `nixos-rebuild switch` and `systemctl restart bluetooth`, verify:

```sh
busctl call org.bluez /org/bluez/hci0 \
  org.bluez.AdvertisementMonitorManager1 SupportedMonitorTypes
```

should return a non-empty array (e.g. `as 1 "or_patterns"`). The header in
the running app shows `BLE: scanning (passive)` in green when this works,
and `BLE: scanning (active) — passive unavailable` in red when it doesn't.

**Controller offload requirement.** Even with `Experimental = true`, the
passive path only delivers events if the BT controller offloads pattern
matching to hardware. Check:

```sh
busctl get-property org.bluez /org/bluez/hci0 \
  org.bluez.AdvertisementMonitorManager1 SupportedFeatures
```

A non-empty list (e.g. `controller-patterns`) means offload works — passive
will deliver every matching ad. An empty list (`as 0`) means BlueZ accepts
the monitor registration but never runs a scan to source matches from, so
0 callbacks are delivered. The MediaTek MT7921 in the Framework 13 AMD
returns empty here; the scanner detects this case at startup and forces the
active+`DuplicateData=True` path.

**Active-mode throughput on MT7921.** With Wi-Fi enabled and the transponder
~3 m away, the AMD/MT7921 combo card produces about 5–8 callbacks/sec for
a 50 Hz advertiser — far below the firmware's ad rate but still enough to
catch every pass for the side-mounted 30 kph scenario. The rate is gated by
two things:

1. **BT/Wi-Fi antenna sharing.** The MT7921 multiplexes one antenna between
   BT and Wi-Fi — heavy Wi-Fi traffic halves (or worse) BT scan duty. If
   throughput collapses to "every few seconds", check whether Wi-Fi is busy.
   Quickest test: `nmcli radio wifi off`, then watch the rate climb in the
   header.
2. **Kernel LE scan parameters.** `/sys/kernel/debug/bluetooth/hci0/le_scan_int`
   and `le_scan_window` (units of 0.625 ms) control the scan duty cycle.
   Default is around 60/30 ms (50 % duty); setting both to `16` (10 ms / 10 ms,
   100 % duty on its time slot) is the upper bound for this controller. It
   needs root and survives until reboot:
   ```sh
   echo 16 | sudo tee /sys/kernel/debug/bluetooth/hci0/le_scan_int
   echo 16 | sudo tee /sys/kernel/debug/bluetooth/hci0/le_scan_window
   ```
   Make permanent via a NixOS systemd one-shot service if it helps.

The header shows the live rolling rate (e.g. `BLE: scanning (active) — passive
unavailable 7.3 Hz`) so the operator can confirm the controller is keeping
up before each session.

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
| `E`       | Toggle the selected car's enabled state                  |
| `D`       | Open Debug screen for the selected car                   |
| `R`       | Rename the selected car (modal text input)               |
| `C`       | Open Configuration screen                                |
| `H`       | Open History screen                                      |
| `X`       | Export current session to CSV                            |
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

Debug screen shows:

- Live view of the last 20 BLE advertisements received for the **selected car**,
  newest first. Each row shows `rssi_dbm` and `age_ms` (time since the
  advertisement was received). Refreshes every ~200 ms.
- Samples are recorded for every advertisement matching a known car name —
  including disabled cars — so the screen can be used to verify a transponder
  is talking before it is enabled for racing.
- `Esc` closes the screen.

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
| RSSI threshold (dBm)  | -100                   |
| Drop-window (s)       | 0.3 (internal)         |

## Tests

- `pytest` covering pure-logic modules:
  - `timeutil` — format/parse round-trips
  - `scanner` — peak detector against synthesised RSSI traces
  - `storage` — schema bootstrap, top-5 query, history clearing
- BLE and Textual layers exercised manually.
