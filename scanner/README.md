# laptimerble (scanner)

Python BLE scanner + Textual UI for LapTimerBLE. Reads `LapTimer-N`
advertisements emitted by the firmware in `../firmware/` and records
lap times when each car's RSSI peaks.

## Run

```sh
pip install -e '.[dev]'
laptimerble
```

## Test

```sh
pytest
```

See `../plan.md` for the full design.
