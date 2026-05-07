# LapTimerBLE

BLE-driven lap timer for 1/10 scale RC cars. Side-mounted laptop scanner reads
BLE advertisements from XIAO ESP32-C3 transponders carried by each car and
records lap times when the RSSI peaks.

See `plan.md` for the full design.

## Run

On NixOS:

```sh
nix-shell           # creates .venv and installs deps on first entry
laptimerble
```

Elsewhere (Python 3.12+):

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e .
laptimerble
```

## Test

```sh
pip install -e '.[dev]'
pytest
```

## Firmware contract

Each ESP32-C3 must advertise with local name `LapTimer-N` (N = 1..8) and the
advertising interval listed in `plan.md`.
