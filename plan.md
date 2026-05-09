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

The scanner uses `aioblescan` to talk to the BT controller through a raw
HCI socket. This bypasses `bluetoothd` and BlueZ's D-Bus discovery cache
entirely — every LE Advertising Report event from the controller becomes
one callback into the peak detector with no coalescing in between.

Two host requirements before launching the app:

1. **`bluetoothd` must be off** on the same `hci0`. Its scan commands
   conflict with ours. On NixOS:

   ```nix
   hardware.bluetooth.enable = false;
   ```

   `nixos-rebuild switch` and confirm `systemctl status bluetooth` is
   `inactive (dead)`. (Existing audio/keyboard pairings via BlueZ will
   stop working while the scanner is in use — this host is dedicated to
   timing.)

2. **Capabilities on the Python interpreter.** Opening
   `AF_BLUETOOTH SOCK_RAW`, binding to `hci0`, and issuing the `HCIDEVUP`
   ioctl need `CAP_NET_RAW` and `CAP_NET_ADMIN`. On a non-Nix distro this
   would be `setcap cap_net_raw,cap_net_admin+eip "$(realpath .venv/bin/python)"`,
   but `/nix/store` is read-only so xattrs there silently fail to persist —
   `getcap` returns empty. Work around by copying the binary into the venv
   (writable filesystem) and setting caps on the copy:

   ```sh
   cp "$(realpath .venv/bin/python)" .venv/bin/python.real
   sudo setcap cap_net_raw,cap_net_admin+eip .venv/bin/python.real
   getcap .venv/bin/python.real    # confirm: ... = cap_net_admin,cap_net_raw+eip
   ```

   Then launch the app via the cap'd copy directly. `pyvenv.cfg` lives one
   level up from the binary, so the venv's `site-packages` are still picked
   up automatically:

   ```sh
   .venv/bin/python.real -m laptimerble
   ```

   (The `.venv/bin/laptimerble` shebang script can't be used as-is because
   it points to the original `/nix/store` python that lacks caps.) Permanent
   setup would use NixOS `security.wrappers` or a systemd unit with
   `AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN`.

The header shows the live rolling rate (e.g. `BLE: scanning (active) 28 Hz`)
so the operator can confirm the controller is keeping up before each session.
On the Framework 13 / MT7921, with Wi-Fi enabled and a transponder ~3 m
away, expect roughly 20–40 Hz per car at the controller's 10 ms / 10 ms
default scan window — Wi-Fi activity on the shared antenna can briefly
halve that. `nmcli radio wifi off` is the quickest way to confirm Wi-Fi
coex is or isn't the limiter.

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

- Scanner uses `aioblescan` to read every LE Advertising Report event off
  a raw HCI socket (no `bluetoothd`), identifies each car by local name,
  and records `(rssi, monotonic_timestamp)` per advertisement.

## Detection algorithm

Per-car threshold-plus-lockout peak detector:

1. Samples below the configured RSSI threshold are ignored — it acts as a
   noise gate.
2. When a sample first clears the threshold a "pass window" opens.
3. While in the window, track the strongest RSSI sample seen and its timestamp.
4. When the running peak has not advanced for `drop_window_seconds` (~300 ms)
   — i.e. RSSI is no longer rising — the window closes and the peak's
   timestamp is the lap-completion time. The window does *not* require RSSI
   to fall back below the threshold, because with the threshold defaulted to
   -100 dBm (well under the noise floor) real-world RSSI never gets that low,
   and "wait for sub-threshold" would leave the window open forever.
5. After emitting a lap, the per-car detector is locked out for `lockout_seconds`
   (same value used as the race-start lockout — see below).

## Race flow

1. **Idle** — Car 1 enabled by default.
2. **Start pressed** — 3-second visible countdown (3, 2, 1, GO). Each
   countdown tick plays a 300 ms / 800 Hz beep; "GO" plays a higher
   500 ms / 1600 Hz beep.
3. **Running** — "GO" the race timer starts; lap detection is suppressed for
   the configured `lockout_seconds` (default 3 s) to prevent recording the start
   line as a lap.
4. **Lap detected** — Append to the car's lap list, recompute its top-5-today,
   and play a 100 ms / 1200 Hz beep so the operator hears each lap.
5. **Finished** — when every enabled car has reached the configured lap count
   (or never, if lap counting is disabled). Also stoppable manually.

### Audio cues

Sine-wave tones are generated in pure Python by `laptimerble.audio` and piped
as raw PCM to whichever system player is on PATH, in this order:

1. `paplay` — PulseAudio / PipeWire-pulse (`pkgs.pulseaudio` client tools)
2. `pw-cat` — PipeWire native (`pkgs.pipewire`, already present on this host)
3. `aplay`  — ALSA fallback (`pkgs.alsa-utils`)

If none are on PATH the calls silently no-op, so audio is purely a UX
enhancement and does not block the app from running. PipeWire on the
Framework 13 ships `pw-cat` by default, which is what the app will use.

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
| Min RSSI (dBm) | Integer like `-100` (more negative = needs closer pass)   |
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
