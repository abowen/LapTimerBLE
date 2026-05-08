# LapTimerBLE

BLE-driven lap timer for 1/10 scale RC cars. Side-mounted laptop scanner reads
BLE advertisements from XIAO ESP32-C3 transponders carried by each car and
records lap times when the RSSI peaks.

See `plan.md` for the full design.

## Layout

```
.
├── scanner/    Python BLE scanner + Textual UI (runs on the laptop)
├── firmware/   ESP32-C3 transponder firmware (PlatformIO + Arduino + NimBLE)
└── plan.md     Spec shared by both halves
```

## Dev shell (NixOS)

```sh
nix-shell      # creates .venv, installs the scanner, puts pio on PATH
```

## Run the scanner

```sh
laptimerble                       # inside nix-shell
# or, elsewhere (Python 3.12+):
cd scanner && pip install -e '.[dev]' && laptimerble
```

## Build & flash a transponder

Inside `nix-shell`:

```sh
cd firmware
pio run -e car1 -t upload         # repeat for car2..car8
```

## Test the scanner

```sh
cd scanner && pytest
```
