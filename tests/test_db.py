import json

import pytest

from db import LocationDB


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = LocationDB(db_path)
    yield database
    database.close()


def test_add_and_get_locations(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1, address="Home")
    db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11, address="Work")
    db.add_location("Bob", "2026-06-01T12:00:00+00:00", 33.0, -117.0)

    locs = db.get_locations(person="Alice")
    assert len(locs) == 2
    assert locs[0]["address"] == "Home"
    assert locs[1]["latitude"] == 32.71


def test_get_people(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    db.add_location("Bob", "2026-06-01T12:00:00+00:00", 33.0, -117.0)

    people = db.get_people()
    assert sorted(people) == ["Alice", "Bob"]


def test_get_history_dict(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11)

    history = db.get_history_dict()
    assert "Alice" in history
    assert len(history["Alice"]) == 2
    assert "person" not in history["Alice"][0]
    assert "id" not in history["Alice"][0]


def test_get_history_dict_since_filter(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    db.add_location("Alice", "2026-06-07T12:00:00+00:00", 32.71, -117.11)

    history = db.get_history_dict(since="2026-06-05T00:00:00+00:00")
    assert len(history["Alice"]) == 1
    assert history["Alice"][0]["latitude"] == 32.71


def test_get_latest(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1, address="First")
    db.add_location("Alice", "2026-06-01T12:05:00+00:00", 32.71, -117.11, address="Last")

    latest = db.get_latest("Alice")
    assert latest["address"] == "Last"

    assert db.get_latest("Nobody") is None


def test_get_total_points(db):
    assert db.get_total_points() == 0
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1)
    db.add_location("Bob", "2026-06-01T12:00:00+00:00", 33.0, -117.0)
    assert db.get_total_points() == 2


def test_purge_older_than(db):
    db.add_location("Alice", "2020-01-01T00:00:00+00:00", 32.7, -117.1)
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.71, -117.11)

    count = db.purge_older_than(30)
    assert count == 1
    assert db.get_total_points() == 1


def test_charging_stored_as_bool(db):
    db.add_location("Alice", "2026-06-01T12:00:00+00:00", 32.7, -117.1, charging=True)
    db.add_location("Bob", "2026-06-01T12:00:00+00:00", 33.0, -117.0, charging=False)

    history = db.get_history_dict()
    assert history["Alice"][0]["charging"] is True
    assert history["Bob"][0]["charging"] is False


def test_import_from_json(db, tmp_path):
    json_data = {
        "Alice": [
            {"timestamp": "2026-06-01T12:00:00", "latitude": 32.7, "longitude": -117.1,
             "battery": 80, "charging": True, "address": "Home", "accuracy": 10}
        ]
    }
    json_path = tmp_path / "history.json"
    json_path.write_text(json.dumps(json_data))

    count = db.import_from_json(str(json_path))
    assert count == 1
    assert db.get_total_points() == 1

    history = db.get_history_dict()
    assert history["Alice"][0]["battery"] == 80


def test_import_nonexistent_json(db):
    count = db.import_from_json("/nonexistent/path.json")
    assert count == 0


def test_empty_db_operations(db):
    assert db.get_people() == []
    assert db.get_history_dict() == {}
    assert db.get_total_points() == 0
    assert db.get_latest("Nobody") is None
