import pytest

from intelligence import (
    Intelligence,
    _battery_multiplier,
    _progressive_backoff,
    _target_spacing,
    compute_poll_interval,
)


@pytest.fixture
def db(tmp_path):

    from db import LocationDB

    db_path = str(tmp_path / "test.db")
    database = LocationDB(db_path)
    yield database
    database.close()


@pytest.fixture
def intel(db):
    return Intelligence(db.conn)


def test_target_spacing():
    assert _target_spacing(0) == 0
    assert _target_spacing(5) == 20
    assert _target_spacing(25) == 50
    assert _target_spacing(50) == 100
    assert _target_spacing(100) == 150


def test_battery_multiplier():
    assert _battery_multiplier(None, None) == 1.0
    assert _battery_multiplier(50, False) == 1.0
    assert _battery_multiplier(20, False) == 1.5
    assert _battery_multiplier(10, False) == 2.5
    assert _battery_multiplier(3, False) == 5.0
    assert _battery_multiplier(3, True) == 1.0


def test_progressive_backoff():
    assert _progressive_backoff(60) == 20
    assert _progressive_backoff(300) == 90
    assert _progressive_backoff(1000) == 240
    assert _progressive_backoff(2000) == 600


def test_cluster_stop_creates_place(intel):
    pid = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    assert pid > 0
    place = intel.find_nearest_place("Alice", 32.7, -117.1)
    assert place is not None
    assert place["visit_count"] == 1


def test_cluster_stop_merges_nearby(intel):
    pid1 = intel.cluster_stop("Alice", 32.7000, -117.1000, "2026-06-01T12:00:00+00:00")
    pid2 = intel.cluster_stop("Alice", 32.7001, -117.1001, "2026-06-02T12:00:00+00:00")
    assert pid1 == pid2
    place = intel.find_nearest_place("Alice", 32.7, -117.1)
    assert place["visit_count"] == 2


def test_cluster_stop_different_locations(intel):
    pid1 = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    pid2 = intel.cluster_stop("Alice", 33.0, -117.5, "2026-06-01T12:00:00+00:00")
    assert pid1 != pid2


def test_record_arrival_departure(intel):
    pid = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    intel.record_arrival("Alice", pid, "2026-06-01T12:00:00+00:00")
    intel.record_departure("Alice", pid, "2026-06-01T14:00:00+00:00")

    row = intel.conn.execute(
        "SELECT duration_seconds FROM dwell_observations WHERE place_id=? AND departed_at IS NOT NULL", (pid,)
    ).fetchone()
    assert row is not None
    assert row["duration_seconds"] == 7200.0


def test_predict_dwell_remaining_insufficient_data(intel):
    pid = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    result = intel.predict_dwell_remaining("Alice", pid, 300, 0, 12)
    assert result is None


def test_predict_dwell_remaining_with_data(intel):
    pid = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    for day in range(1, 5):
        intel.record_arrival("Alice", pid, f"2026-06-0{day}T09:00:00+00:00")
        intel.record_departure("Alice", pid, f"2026-06-0{day}T17:00:00+00:00")

    remaining = intel.predict_dwell_remaining("Alice", pid, 3600, 0, 10)
    assert remaining is not None
    assert remaining > 0


def test_departure_probability_no_data(intel):
    from datetime import UTC, datetime

    p = intel.departure_probability("Alice", 32.7, -117.1, datetime.now(UTC))
    assert p == 0.0


def test_departure_probability_with_data(intel):
    from datetime import UTC, datetime

    pid = intel.cluster_stop("Alice", 32.7, -117.1, "2026-06-01T12:00:00+00:00")
    now = datetime.now(UTC)
    dep_hour = now.hour
    dep_minute = now.minute + 5
    if dep_minute >= 60:
        dep_hour += 1
        dep_minute -= 60

    for day in range(1, 6):
        intel.record_arrival("Alice", pid, f"2026-06-{day:02d}T09:00:00+00:00")
        intel.conn.execute(
            "UPDATE dwell_observations SET departed_at=?, duration_seconds=3600, day_of_week=? "
            "WHERE person='Alice' AND place_id=? AND departed_at IS NULL",
            (f"2026-06-{day:02d}T{dep_hour:02d}:{dep_minute:02d}:00+00:00", now.weekday(), pid),
        )
    intel.conn.commit()

    p = intel.departure_probability("Alice", 32.7, -117.1, now)
    assert p >= 0.0


def test_speed_zone_learning(intel):
    points = [
        {"latitude": 32.700, "longitude": -117.100, "timestamp": "2026-06-01T12:00:00+00:00"},
        {"latitude": 32.710, "longitude": -117.100, "timestamp": "2026-06-01T12:01:00+00:00"},
        {"latitude": 32.711, "longitude": -117.100, "timestamp": "2026-06-01T12:02:00+00:00"},
    ]
    intel.learn_speed_zones_from_trip(points)
    zones = intel.conn.execute("SELECT COUNT(*) as c FROM speed_zones").fetchone()["c"]
    assert zones >= 1


def test_near_speed_zone_requires_min_observations(intel):
    intel.conn.execute(
        "INSERT INTO speed_zones (lat, lon, avg_speed_kmh, observation_count, last_updated) "
        "VALUES (32.71, -117.10, 10, 1, '2026-06-01')"
    )
    intel.conn.commit()
    points = [
        {"latitude": 32.700, "longitude": -117.100},
        {"latitude": 32.705, "longitude": -117.100},
    ]
    assert not intel.near_speed_zone(points)


def test_backfill(intel):
    locations = [
        {"latitude": 32.7, "longitude": -117.1, "timestamp": "2026-06-01T08:00:00+00:00"},
        {"latitude": 32.7, "longitude": -117.1, "timestamp": "2026-06-01T09:00:00+00:00"},
        {"latitude": 32.7, "longitude": -117.1, "timestamp": "2026-06-01T10:00:00+00:00"},
        {"latitude": 33.0, "longitude": -117.5, "timestamp": "2026-06-01T11:00:00+00:00"},
        {"latitude": 33.0, "longitude": -117.5, "timestamp": "2026-06-01T12:00:00+00:00"},
        {"latitude": 33.0, "longitude": -117.5, "timestamp": "2026-06-01T13:00:00+00:00"},
    ]
    intel.backfill_from_locations("Alice", locations)
    places = intel.conn.execute("SELECT COUNT(*) as c FROM known_places WHERE person='Alice'").fetchone()["c"]
    assert places >= 2


def test_compute_poll_interval_moving(intel):
    interval, reason = compute_poll_interval(
        intel,
        "Alice",
        32.7,
        -117.1,
        speed_kmh=40,
        trend="steady",
        stationary_secs=0,
        battery=80,
        charging=False,
    )
    assert 4 <= interval <= 25
    assert "km/h" in reason or "spacing" in reason


def test_compute_poll_interval_stationary(intel):
    interval, reason = compute_poll_interval(
        intel,
        "Alice",
        32.7,
        -117.1,
        speed_kmh=0,
        trend="steady",
        stationary_secs=3600,
        battery=80,
        charging=False,
    )
    assert interval >= 200


def test_compute_poll_interval_low_battery(intel):
    interval_normal, _ = compute_poll_interval(
        intel,
        "Alice",
        32.7,
        -117.1,
        speed_kmh=40,
        trend="steady",
        stationary_secs=0,
        battery=80,
        charging=False,
    )
    interval_low, _ = compute_poll_interval(
        intel,
        "Alice",
        32.7,
        -117.1,
        speed_kmh=40,
        trend="steady",
        stationary_secs=0,
        battery=5,
        charging=False,
    )
    assert interval_low > interval_normal


def test_compute_poll_interval_departing(intel):
    interval, reason = compute_poll_interval(
        intel,
        "Alice",
        32.7,
        -117.1,
        speed_kmh=8,
        trend="accelerating",
        stationary_secs=0,
        battery=80,
        charging=False,
    )
    assert interval <= 6
    assert "departing" in reason
