# LapTimerBLE firmware

ESP32-C3 transponder firmware. Each car runs a binary built from this same
source tree; `CAR_NUMBER` is a compile-time flag that selects the BLE local
name (`LapTimer-N`) and the advertising interval from the table in
`../plan.md`.

## Hardware

- Seeed XIAO ESP32-C3
- Powered from the ESC's BEC (5 V → onboard LDO)
- USB-C used only for flashing / debugging

## Build & flash

PlatformIO is on `PATH` inside the repo's `nix-shell`.

```sh
cd firmware
pio run -e car1 -t upload          # build + flash car 1
pio device monitor -e car1         # serial console (115200)
```

Repeat with `-e car2` … `-e car8` for the other transponders. Each XIAO needs
to be flashed with the matching environment for its assigned car number.

## What it does

On boot:

1. Initialises NimBLE with device name `LapTimer-<CAR_NUMBER>`.
2. Sets TX power to +9 dBm (max common to ESP32-C3 / -S3 / classic).
3. Pins both min and max advertising intervals to the per-car value, so the
   stack does not jitter the rate.
4. Starts non-connectable, undirected advertising and never stops.

The onboard user LED (GPIO 8, active-low) blinks ~80 ms every 2 s so you can
tell the firmware is alive without a serial connection.

## Mapping car number → interval

Defined in `src/main.cpp` (`kAdvIntervalMs`), kept identical to the table in
`../plan.md`:

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
