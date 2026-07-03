import pytest

from dashboard import create_app


@pytest.fixture
def client(tmp_path):
    data_file = str(tmp_path / "test.db")
    app = create_app(
        data_file=data_file,
        cookies_file=str(tmp_path / "none.enc"),
        email="test@test.com",
        poll_interval=300,
        start_poll=False,
    )
    app.config.update(TESTING=True, DATA_FILE=data_file)
    return app.test_client()


def test_geofence_missing_fields(client):
    r = client.post("/api/geofences", json={"person": "Alice"})
    assert r.status_code == 400


def test_geofence_invalid_coords(client):
    r = client.post(
        "/api/geofences",
        json={"person": "A", "label": "H", "latitude": "abc", "longitude": 0},
    )
    assert r.status_code == 400


def test_geofence_out_of_range(client):
    r = client.post(
        "/api/geofences",
        json={"person": "A", "label": "H", "latitude": 200, "longitude": 0},
    )
    assert r.status_code == 400


def test_geofence_nonpositive_radius(client):
    r = client.post(
        "/api/geofences",
        json={"person": "A", "label": "H", "latitude": 32.7, "longitude": -117.1, "radius_m": 0},
    )
    assert r.status_code == 400


def test_geofence_valid_roundtrip(client):
    r = client.post(
        "/api/geofences",
        json={"person": "A", "label": "Home", "latitude": 32.7, "longitude": -117.1, "radius_m": 150},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "id" in body

    listing = client.get("/api/geofences?person=A").get_json()
    assert len(listing) == 1
    assert listing[0]["label"] == "Home"


def test_self_location_out_of_range(client):
    r = client.post("/api/self-location", json={"latitude": 999, "longitude": 0})
    assert r.status_code == 400


def test_snap_requires_list(client):
    r = client.post("/api/snap", json={"coords": "notalist"})
    assert r.status_code == 400


def test_locations_envelope(client):
    r = client.get("/api/locations")
    assert r.status_code == 200
    body = r.get_json()
    assert "api_version" in body
    assert "locations" in body


def test_locations_includes_every_person(client):
    # No time window: a busy person must not crowd a sparse one out of the view.
    from db import LocationDB

    seed = LocationDB(client.application.config["DATA_FILE"])
    for i in range(30):
        seed.add_location("Busy", f"2026-06-01T12:{i:02d}:00+00:00", 32.0, -117.0)
    seed.add_location("Sparse", "2026-06-01T09:00:00+00:00", 33.0, -118.0)
    seed.close()

    body = client.get("/api/locations").get_json()
    assert "Busy" in body["locations"]
    assert "Sparse" in body["locations"]
    assert len(body["locations"]["Sparse"]) == 1
