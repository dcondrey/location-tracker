import threading
from pathlib import Path

import pytest

from providers import ProviderAuthError
from tracker import STOP_DISTANCE_METERS, LocationTracker


class _AlwaysAuthFails:
    """Provider stub whose auth always fails and whose refresh claims success."""

    def __init__(self):
        self.refresh_calls = 0

    def get_locations(self):
        raise ProviderAuthError("expired")

    def try_refresh(self):
        self.refresh_calls += 1
        return True

    def auth_instructions(self):
        return "re-auth"


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


def test_get_stats_concurrent_access(tracker):
    tracker.db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    tracker.db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11)

    results = []
    errors = []

    def worker():
        try:
            for _ in range(10):
                results.append(tracker.get_stats()["Alice"]["total_points"])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert results and all(p == 2 for p in results)


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


def test_recent_history(tracker):
    for i in range(3):
        tracker.db.add_location("Alice", f"2026-06-01T12:0{i}:00+00:00", 32.0 + i, -117.0)
    recent = tracker.recent_history(limit_per_person=2)
    assert len(recent["Alice"]) == 2
    assert recent["Alice"][-1]["latitude"] == 34.0


def test_poll_location_refresh_is_bounded(tmp_path):
    # Auth fails, refresh "succeeds" but auth still fails on retry. Must not
    # recurse forever: exactly one refresh attempt, then give up.
    provider = _AlwaysAuthFails()
    t = LocationTracker(
        cookies_file="nonexistent.enc",
        email="test@test.com",
        data_file=str(tmp_path / "test.db"),
        provider=provider,
    )
    assert t.poll_location() is False
    assert provider.refresh_calls == 1
    t.db.close()


def test_generate_map_escapes_html(tracker, tmp_path):
    evil_name = "<script>alert(1)</script>"
    evil_addr = '"><img src=x onerror=alert(1)>'
    tracker.db.add_location(evil_name, "2026-06-01T12:00:00+00:00", 32.7, -117.1, address=evil_addr)
    tracker.db.add_location(evil_name, "2026-06-01T12:05:00+00:00", 32.7, -117.1, address=evil_addr)

    out = str(tmp_path / "map.html")
    assert tracker.generate_map(output_file=out) == out

    content = Path(out).read_text()
    assert evil_name not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "onerror=alert(1)>" not in content
