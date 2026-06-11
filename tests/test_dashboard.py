from dashboard import _adaptive_interval, _compute_speed_kmh


def _make_points(coords_with_times):
    """Helper: list of (lat, lon, timestamp) -> location dicts."""
    return [{"latitude": lat, "longitude": lon, "timestamp": ts} for lat, lon, ts in coords_with_times]


def test_compute_speed_stationary():
    history = {
        "Alice": _make_points(
            [
                (32.7, -117.1, "2026-06-01T12:00:00+00:00"),
                (32.7, -117.1, "2026-06-01T12:05:00+00:00"),
            ]
        )
    }
    speed = _compute_speed_kmh(history)
    assert speed < 1


def test_compute_speed_moving():
    # ~1.5 km in 60 seconds = 90 km/h
    history = {
        "Alice": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (32.714, -117.100, "2026-06-01T12:01:00+00:00"),
            ]
        )
    }
    speed = _compute_speed_kmh(history)
    assert speed > 50


def test_compute_speed_ignores_me():
    history = {
        "Me": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (33.000, -117.100, "2026-06-01T12:01:00+00:00"),
            ]
        )
    }
    speed = _compute_speed_kmh(history)
    assert speed == 0.0


def test_compute_speed_empty():
    assert _compute_speed_kmh({}) == 0.0
    assert _compute_speed_kmh({"Alice": []}) == 0.0
    single = {"Alice": [{"latitude": 32.7, "longitude": -117.1, "timestamp": "2026-06-01T12:00:00"}]}
    assert _compute_speed_kmh(single) == 0.0


def test_adaptive_interval_stationary():
    history = {
        "Alice": _make_points(
            [
                (32.7, -117.1, "2026-06-01T12:00:00+00:00"),
                (32.7, -117.1, "2026-06-01T12:10:00+00:00"),
            ]
        )
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval == 600
    assert category == "stationary"


def test_adaptive_interval_fast():
    history = {
        "Alice": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (33.300, -117.100, "2026-06-01T12:01:00+00:00"),
            ]
        )
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval == 15
    assert category == "fast"
