from laptimerble.timeutil import format_lap, format_race


def test_format_lap_sub_second() -> None:
    assert format_lap(0.123) == "0.123"


def test_format_lap_typical() -> None:
    assert format_lap(12.345) == "12.345"


def test_format_lap_zero() -> None:
    assert format_lap(0.0) == "0.000"


def test_format_lap_negative_clamps() -> None:
    assert format_lap(-1.0) == "0.000"


def test_format_lap_long_lap() -> None:
    # Even slow laps stay in seconds with millisecond precision.
    assert format_lap(75.5) == "75.500"


def test_format_race_short() -> None:
    assert format_race(3.5) == "00:03.500"


def test_format_race_minutes() -> None:
    assert format_race(222.187) == "03:42.187"


def test_format_race_zero() -> None:
    assert format_race(0.0) == "00:00.000"


def test_format_race_negative_clamps() -> None:
    assert format_race(-5.0) == "00:00.000"
