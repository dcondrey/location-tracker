import pytest

from tracker import STOP_DISTANCE_METERS, LocationTracker


@pytest.fixture
def tracker(tmp_path):
    db_path = str(tmp_path / "test.db")
    t = LocationTracker(
        cookies_file="nonexistent.enc",
        email="test@test.com",
        data_file=db_path,
    )
    yield t
    t.db.close()


def test_haversine_zero_distance(tracker):
    dist = tracker.haversine(-117.1, 32.7, -117.1, 32.7)
    assert dist == 0.0


def test_haversine_known_distance(tracker):
    # San Diego to Los Angeles ~ 179 km
    dist = tracker.haversine(-117.16, 32.72, -118.24, 34.05)
    assert 170_000 < dist < 190_000


def test_haversine_symmetric(tracker):
    d1 = tracker.haversine(-117.1, 32.7, -118.2, 34.0)
    d2 = tracker.haversine(-118.2, 34.0, -117.1, 32.7)
    assert abs(d1 - d2) < 0.01


def test_stop_distance_constant():
    assert STOP_DISTANCE_METERS == 25


def test_get_stats_empty(tracker):
    stats = tracker.get_stats()
    assert stats == {}


def test_get_stats_with_data(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    tracker.db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11)

    stats = tracker.get_stats()
    assert "Alice" in stats
    assert stats["Alice"]["total_points"] == 2
    assert stats["Alice"]["total_distance_km"] > 0


def test_get_stats_caching(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)

    stats1 = tracker.get_stats()
    stats2 = tracker.get_stats()
    assert stats1 is stats2  # same cached object


def test_get_stats_cache_invalidation(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    tracker.get_stats()  # populate cache

    tracker.db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11)
    stats2 = tracker.get_stats()
    assert stats2["Alice"]["total_points"] == 2


def test_get_people(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    tracker.db.add_location("Bob", "2026-06-01T12:00:00+00:00", 33.0, -117.0)

    people = tracker.get_people()
    assert sorted(people) == ["Alice", "Bob"]


def test_history_property(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)

    history = tracker.history
    assert "Alice" in history
    assert len(history["Alice"]) == 1
