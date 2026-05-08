from __future__ import annotations

import logging
from pathlib import Path

from .app import LapTimerApp


def _configure_logging() -> Path:
    """Send logs to a file rather than stderr.

    Textual takes over the TTY in alternate-screen mode, so anything written
    to stderr (the default ``logging.lastResort`` target) is invisible.
    Tail this file when the header shows ``BLE: off (...)`` to see the
    underlying traceback.
    """
    log_dir = Path.home() / ".laptimerble"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "scanner.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=log_path,
        filemode="a",
    )
    return log_path


def main() -> None:
    log_path = _configure_logging()
    logging.getLogger(__name__).info("--- laptimerble starting (log → %s) ---", log_path)
    LapTimerApp().run()


if __name__ == "__main__":
    main()
