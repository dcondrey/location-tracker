import pytest
from location_tracker.tracker import Tracker

@pytest.fixture
def tracker():
    tracker = Tracker()
    yield tracker

def test_tracker_stats_computation(tracker):
    tracker.locations = [(37.7749, -122.4194), (37.7859, -122.4364)]
    stats = tracker.compute_stats()
    assert stats["distance"] > 0
    assert stats["stops"] > 0
    assert stats["dwell_time"] > 0

def test_tracker_stop_detection(tracker):
    tracker.locations = [(37.7749, -122.4194), (37.7859, -122.4364)]
    stops = tracker.detect_stops()
    assert len(stops) > 0

def test_tracker_haversine_calculation(tracker):
    lat1, lon1 = 37.7749, -122.4194
    lat2, lon2 = 37.7859, -122.4364
    distance = tracker.haversine_distance(lat1, lon1, lat2, lon2)
    assert distance > 0