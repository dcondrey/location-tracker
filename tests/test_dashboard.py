from dashboard import _adaptive_interval, _analyze_movement, _speed_info_for_points


def _make_points(coords_with_times):
    """Helper: list of (lat, lon, timestamp) -> location dicts."""
    return [{"latitude": lat, "longitude": lon, "timestamp": ts} for lat, lon, ts in coords_with_times]


def test_analyze_stationary():
    pts = _make_points(
        [
            (32.7, -117.1, "2026-06-01T12:00:00+00:00"),
            (32.7, -117.1, "2026-06-01T12:05:00+00:00"),
            (32.7, -117.1, "2026-06-01T12:10:00+00:00"),
        ]
    )
    m = _analyze_movement(pts)
    assert m["speed_kmh"] < 1
    assert m["stationary_seconds"] >= 600


def test_analyze_moving():
    pts = _make_points(
        [
            (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
            (32.714, -117.100, "2026-06-01T12:01:00+00:00"),
        ]
    )
    m = _analyze_movement(pts)
    assert m["speed_kmh"] > 50


def test_analyze_accelerating():
    pts = _make_points(
        [
            (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
            (32.700, -117.100, "2026-06-01T12:01:00+00:00"),
            (32.700, -117.100, "2026-06-01T12:02:00+00:00"),
            (32.701, -117.100, "2026-06-01T12:03:00+00:00"),
            (32.705, -117.100, "2026-06-01T12:04:00+00:00"),
            (32.714, -117.100, "2026-06-01T12:05:00+00:00"),
        ]
    )
    m = _analyze_movement(pts)
    assert m["trend"] == "accelerating"


def test_analyze_decelerating():
    pts = _make_points(
        [
            (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
            (32.714, -117.100, "2026-06-01T12:01:00+00:00"),
            (32.720, -117.100, "2026-06-01T12:02:00+00:00"),
            (32.721, -117.100, "2026-06-01T12:03:00+00:00"),
            (32.721, -117.100, "2026-06-01T12:04:00+00:00"),
        ]
    )
    m = _analyze_movement(pts)
    assert m["trend"] == "decelerating"


def test_analyze_empty():
    assert _analyze_movement([])["speed_kmh"] == 0
    assert _analyze_movement(None)["speed_kmh"] == 0
    single = [{"latitude": 32.7, "longitude": -117.1, "timestamp": "2026-06-01T12:00:00"}]
    assert _analyze_movement(single)["speed_kmh"] == 0


def test_speed_info_labels():
    stationary = _make_points(
        [(32.7, -117.1, "2026-06-01T12:00:00+00:00"), (32.7, -117.1, "2026-06-01T12:05:00+00:00")]
    )
    assert _speed_info_for_points(stationary)["label"] == "Stationary"

    fast = _make_points(
        [(32.700, -117.100, "2026-06-01T12:00:00+00:00"), (33.300, -117.100, "2026-06-01T12:01:00+00:00")]
    )
    assert _speed_info_for_points(fast)["label"] == "Highway"


def test_adaptive_interval_stationary():
    history = {
        "Alice": _make_points(
            [
                (32.7, -117.1, "2026-06-01T11:00:00+00:00"),
                (32.7, -117.1, "2026-06-01T11:10:00+00:00"),
                (32.7, -117.1, "2026-06-01T11:20:00+00:00"),
                (32.7, -117.1, "2026-06-01T11:30:00+00:00"),
                (32.7, -117.1, "2026-06-01T11:40:00+00:00"),
                (32.7, -117.1, "2026-06-01T12:00:00+00:00"),
            ]
        )
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval == 600
    assert category == "long stationary"


def test_adaptive_interval_highway():
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
    assert category == "highway"


def test_adaptive_interval_departing():
    # Stationary then rapid acceleration: 0, 0, 0, ~1km, ~3km, ~8km per minute
    history = {
        "Alice": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (32.700, -117.100, "2026-06-01T12:01:00+00:00"),
                (32.700, -117.100, "2026-06-01T12:02:00+00:00"),
                (32.710, -117.100, "2026-06-01T12:03:00+00:00"),
                (32.730, -117.100, "2026-06-01T12:04:00+00:00"),
                (32.780, -117.100, "2026-06-01T12:05:00+00:00"),
            ]
        )
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval <= 15
    assert category in ("departing", "driving", "highway")


def test_adaptive_interval_just_stopped():
    history = {
        "Alice": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (32.714, -117.100, "2026-06-01T12:01:00+00:00"),
                (32.714, -117.100, "2026-06-01T12:01:30+00:00"),
            ]
        )
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval == 30
    assert category == "just stopped"


def test_adaptive_ignores_me():
    history = {
        "Me": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (33.300, -117.100, "2026-06-01T12:01:00+00:00"),
            ]
        )
    }
    interval, _category = _adaptive_interval(history, 300)
    assert interval == 600  # "Me" is skipped; no tracked people → max interval


def test_adaptive_per_person_fastest_wins():
    history = {
        "Alice": _make_points(
            [
                (32.7, -117.1, "2026-06-01T12:00:00+00:00"),
                (32.7, -117.1, "2026-06-01T12:30:00+00:00"),
            ]
        ),
        "Bob": _make_points(
            [
                (32.700, -117.100, "2026-06-01T12:00:00+00:00"),
                (33.300, -117.100, "2026-06-01T12:01:00+00:00"),
            ]
        ),
    }
    interval, category = _adaptive_interval(history, 300)
    assert interval == 15
    assert category == "highway"
