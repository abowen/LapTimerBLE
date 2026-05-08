from laptimerble.scanner import CarDetectorRegistry, PeakDetector


def test_below_threshold_never_fires() -> None:
    det = PeakDetector(rssi_threshold=-70, lockout_seconds=0.0)
    for i in range(20):
        assert det.feed(-90, i * 0.02) is None


def test_single_pass_emits_at_peak() -> None:
    det = PeakDetector(rssi_threshold=-70, lockout_seconds=0.0, drop_window_seconds=0.1)
    samples = [
        (-90, 0.0),
        (-80, 0.05),
        (-70, 0.10),
        (-60, 0.15),  # peak
        (-65, 0.20),
        (-75, 0.25),
        (-90, 0.30),  # below threshold
        (-90, 0.45),  # > drop_window after last_above_t (0.20)
    ]
    emitted = None
    for rssi, t in samples:
        result = det.feed(rssi, t)
        if result is not None:
            emitted = result
    assert emitted == 0.15


def test_lockout_suppresses_immediate_second_pass() -> None:
    det = PeakDetector(rssi_threshold=-70, lockout_seconds=1.0, drop_window_seconds=0.1)

    # First pass — peak at t=0.15
    for rssi, t in [(-60, 0.10), (-50, 0.15), (-60, 0.20), (-90, 0.40)]:
        det.feed(rssi, t)

    # Second peak inside lockout — must NOT emit.
    emitted = None
    for rssi, t in [(-60, 0.50), (-50, 0.55), (-60, 0.60), (-90, 0.80)]:
        result = det.feed(rssi, t)
        if result is not None:
            emitted = result
    assert emitted is None


def test_lockout_releases_after_window() -> None:
    det = PeakDetector(rssi_threshold=-70, lockout_seconds=1.0, drop_window_seconds=0.1)

    for rssi, t in [(-60, 0.10), (-50, 0.15), (-60, 0.20), (-90, 0.40)]:
        det.feed(rssi, t)

    # Second pass well after lockout
    emitted = None
    for rssi, t in [
        (-60, 1.20),
        (-50, 1.25),  # peak
        (-60, 1.30),
        (-90, 1.50),
    ]:
        result = det.feed(rssi, t)
        if result is not None:
            emitted = result
    assert emitted == 1.25


def test_drop_window_must_elapse_before_emission() -> None:
    det = PeakDetector(rssi_threshold=-70, lockout_seconds=0.0, drop_window_seconds=0.5)
    # All samples above threshold — never closes window.
    for i in range(20):
        assert det.feed(-60, i * 0.02) is None


def test_registry_reconfigure_propagates() -> None:
    reg = CarDetectorRegistry(rssi_threshold=-70, lockout_seconds=3.0)
    reg.reconfigure(rssi_threshold=-60, lockout_seconds=1.5)
    for det in reg.detectors:
        assert det.rssi_threshold == -60
        assert det.lockout_seconds == 1.5


def test_registry_per_car_independent() -> None:
    reg = CarDetectorRegistry(rssi_threshold=-70, lockout_seconds=0.0, drop_window_seconds=0.1)
    # Car 0 emits, Car 1 should still be able to emit independently in same time window.
    for rssi, t in [(-60, 0.10), (-50, 0.15), (-60, 0.20), (-90, 0.40)]:
        reg.feed(0, rssi, t)
    emitted = None
    for rssi, t in [(-60, 0.50), (-50, 0.55), (-60, 0.60), (-90, 0.80)]:
        result = reg.feed(1, rssi, t)
        if result is not None:
            emitted = result
    assert emitted == 0.55
